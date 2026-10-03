import shutil
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

from ytdl import assemble
from ytdl.assemble import Media
from ytdl.errors import Cancelled

import json
import os
import subprocess
import time

from ytdl.errors import JobError

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
if not HAVE_FFMPEG and os.environ.get("YTDL_REQUIRE_FFMPEG") == "1":
    raise RuntimeError("YTDL_REQUIRE_FFMPEG=1 mais ffmpeg ou ffprobe est introuvable")


def make_clip(path, width=320, height=180, fps=30, seconds=1, audio=True, vcodec="libx264"):
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate={fps}:duration={seconds}"]
    if audio:
        # sine est mono : -ac 2 pour ressembler à une vraie vidéo (stéréo).
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={seconds}",
                 "-c:a", "aac", "-ac", "2"]
    args += ["-c:v", vcodec, "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(args, check=True)


def make_audio(path, seconds=1):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
                    "-c:a", "aac", "-ac", "2", str(path)],
                   check=True)


def chapter_titles(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    return [c["tags"]["title"] for c in json.loads(out.stdout)["chapters"]]

H264 = ("h264", 1920, 1080, "30/1")
AAC = ("aac", "44100", 2)


class PureTest(unittest.TestCase):
    def test_compatible_needs_identical_video_and_audio(self):
        self.assertTrue(assemble.compatible([Media(1, H264, AAC), Media(2, H264, AAC)]))
        self.assertFalse(assemble.compatible([Media(1, H264, AAC), Media(2, ("h264", 1280, 720, "30/1"), AAC)]))
        self.assertFalse(assemble.compatible([Media(1, H264, AAC), Media(2, H264, ("aac", "48000", 2))]))
        self.assertFalse(assemble.compatible([Media(1, H264, AAC), Media(2, H264, None)]))

    def test_target_height(self):
        medias = [Media(1, H264, AAC), Media(1, ("vp9", 1280, 720, "25/1"), AAC)]
        self.assertEqual(assemble.target_height(medias, None), 1080)
        self.assertEqual(assemble.target_height(medias, 720), 720)
        tall = [Media(1, ("vp9", 3840, 2160, "30/1"), AAC)]
        self.assertEqual(assemble.target_height(tall, None), 1080)  # plafond du ré-encodage
        small = [Media(1, ("h264", 640, 360, "30/1"), AAC)]
        self.assertEqual(assemble.target_height(small, 1080), 360)  # jamais d'agrandissement

    def test_frame_width_is_even_16_9(self):
        self.assertEqual([assemble.frame_width(h) for h in (1080, 720, 480, 360, 180)], [1920, 1280, 854, 640, 320])

    def test_reencode_estimate_scales_with_pixels(self):
        medias = [Media(3600, H264, AAC), Media(1800, H264, AAC)]
        self.assertEqual(assemble.reencode_estimate(medias, 1080, 1.0), 5400)
        self.assertEqual(assemble.reencode_estimate(medias, 720, 1.0), 2400)

    def test_ffmetadata_chapters_follow_each_other(self):
        text = assemble.ffmetadata(["Un", "Deux"], [1.5, 2.25])
        self.assertEqual(text, ";FFMETADATA1\n"
                               "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1500\ntitle=Un\n"
                               "[CHAPTER]\nTIMEBASE=1/1000\nSTART=1500\nEND=3750\ntitle=Deux\n")

    def test_ffmetadata_escapes_special_characters(self):
        self.assertIn("title=a\\=b\\;c\\#d\\\\e\n", assemble.ffmetadata(["a=b;c#d\\e"], [1]))


class ZipTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.a, self.b = self.tmp / "001.mp4", self.tmp / "002.mp4"
        self.a.write_bytes(b"aaa")
        self.b.write_bytes(b"bbbb")

    def test_stored_in_order_and_sources_removed(self):
        out = self.tmp / "pl.zip"
        assemble.zip_files([(self.a, "01 - A.mp4"), (self.b, "02 - B.mp4")], out)
        with zipfile.ZipFile(out) as z:
            self.assertEqual(z.namelist(), ["01 - A.mp4", "02 - B.mp4"])
            self.assertTrue(all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist()))
            self.assertEqual(z.read("02 - B.mp4"), b"bbbb")
        self.assertFalse(self.a.exists() or self.b.exists())

    def test_cancel(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            assemble.zip_files([(self.a, "01 - A.mp4")], self.tmp / "pl.zip", cancel)
        self.assertTrue(self.a.exists())


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg absent")
class FfmpegTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_probe(self):
        make_clip(self.tmp / "a.mp4")
        media = assemble.probe(self.tmp / "a.mp4")
        self.assertAlmostEqual(media.duration, 1.0, delta=0.1)
        self.assertEqual(media.video, ("h264", 320, 180, "30/1"))
        self.assertEqual(media.audio, ("aac", "44100", 2))

    def test_to_mp3_uses_fixed_settings(self):
        make_audio(self.tmp / "a.m4a")
        assemble.to_mp3(self.tmp / "a.m4a", self.tmp / "a.mp3")
        media = assemble.probe(self.tmp / "a.mp3")
        self.assertIsNone(media.video)
        self.assertEqual(media.audio, ("mp3", "44100", 2))

    def test_concat_copy_mp4_with_chapters(self):
        files = [self.tmp / "001.mp4", self.tmp / "002.mp4"]
        for f in files:
            make_clip(f)
        out = self.tmp / "pl.mp4"
        assemble.concat_copy(files, ["Un", "Deux"], [1.0, 1.0], out)
        self.assertAlmostEqual(assemble.probe(out).duration, 2.0, delta=0.15)
        self.assertEqual(chapter_titles(out), ["Un", "Deux"])
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["001.mp4", "002.mp4", "pl.mp4"])

    def test_concat_copy_mp3_with_chapters(self):
        mp3s = []
        for name in ("001", "002"):
            make_audio(self.tmp / f"{name}.m4a")
            assemble.to_mp3(self.tmp / f"{name}.m4a", self.tmp / f"{name}.mp3")
            mp3s.append(self.tmp / f"{name}.mp3")
        out = self.tmp / "pl.mp3"
        assemble.concat_copy(mp3s, ["A", "B"], [1.0, 1.0], out)
        self.assertAlmostEqual(assemble.probe(out).duration, 2.0, delta=0.15)
        self.assertEqual(chapter_titles(out), ["A", "B"])

    def test_normalize_makes_different_videos_concatenable(self):
        make_clip(self.tmp / "a.mp4")
        make_clip(self.tmp / "b.mp4", width=160, height=120, fps=25, vcodec="mpeg4")
        outs = []
        for name in ("a", "b"):
            src = self.tmp / f"{name}.mp4"
            out = self.tmp / f"{name}.norm.mp4"
            assemble.normalize(src, assemble.probe(src), 180, out)
            outs.append(assemble.probe(out))
        for media in outs:
            self.assertEqual(media.video, ("h264", 320, 180, "30/1"))
            self.assertEqual(media.audio, ("aac", "48000", 2))
        self.assertTrue(assemble.compatible(outs))

    def test_normalize_adds_silence_when_audio_is_missing(self):
        make_clip(self.tmp / "muet.mp4", audio=False)
        src, out = self.tmp / "muet.mp4", self.tmp / "muet.norm.mp4"
        assemble.normalize(src, assemble.probe(src), 180, out)
        media = assemble.probe(out)
        self.assertEqual(media.audio, ("aac", "48000", 2))
        self.assertAlmostEqual(media.duration, 1.0, delta=0.15)

    def test_run_failure_keeps_ffmpeg_message(self):
        with self.assertRaises(JobError) as ctx:
            assemble.run(["-i", str(self.tmp / "absent.mp4"), str(self.tmp / "x.mp4")])
        self.assertEqual(ctx.exception.message, "Échec de la conversion")
        # Spec §10 : la dernière ligne de ffmpeg (ffmpeg 8 y met la cause, pas le nom du fichier).
        self.assertIn("No such file or directory", ctx.exception.details)

    def test_run_cancel_kills_ffmpeg(self):
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        started = time.monotonic()
        with self.assertRaises(Cancelled):
            assemble.run(["-re", "-f", "lavfi", "-i", "testsrc2=duration=60", "-f", "null", "-"], cancel)
        self.assertLess(time.monotonic() - started, 3)


if __name__ == "__main__":
    unittest.main()
