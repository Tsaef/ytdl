import shutil
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

from ytdl import assemble
from ytdl.assemble import Media
from ytdl.errors import Cancelled

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


if __name__ == "__main__":
    unittest.main()
