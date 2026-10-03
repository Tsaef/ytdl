import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.fakes import FakeYDL
from tests.test_formats import VIDEO_FORMATS
from ytdl import formats
from ytdl.disk import DiskGuard
from ytdl.downloader import Downloader, Playlist
from ytdl.errors import Cancelled, JobError

URL = "https://www.youtube.com/watch?v=abc"


class DownloaderTest(unittest.TestCase):
    def setUp(self):
        FakeYDL.reset()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.frees = None
        self.guard = DiskGuard(self.tmp, 0, usage=self.usage)
        self.dl = Downloader(self.guard, ydl_class=FakeYDL)
        self.cancel = threading.Event()
        self.reports = []

    def usage(self, path):
        return SimpleNamespace(free=next(self.frees) if self.frees else 10**15)

    def fetch(self, target, fmt="mp4"):
        return self.dl.fetch(target, fmt, 1080, self.tmp / "dl" / "001", None, "job",
                             lambda *r: self.reports.append(r), self.cancel)

    def test_analyze_uses_locked_down_options(self):
        FakeYDL.infos[URL] = {"title": "T", "duration": 60, "formats": VIDEO_FORMATS}
        result = self.dl.analyze(URL, Path("/c.txt"))
        self.assertEqual(result["kind"], "video")
        opts = FakeYDL.instances[0].opts
        self.assertEqual(opts["cookiefile"], "/c.txt")
        self.assertEqual(opts["allowed_extractors"], ["default", "-generic"])

    def test_live_is_refused(self):
        FakeYDL.infos[URL] = {"title": "T", "live_status": "is_live"}
        with self.assertRaises(JobError) as ctx:
            self.dl.analyze(URL, None)
        self.assertEqual(ctx.exception.kind, "live")

    def test_extraction_errors_are_classified(self):
        FakeYDL.infos[URL] = RuntimeError("ERROR: [youtube] abc: Sign in to confirm you’re not a bot.")
        with self.assertRaises(JobError) as ctx:
            self.dl.analyze(URL, None)
        self.assertEqual(ctx.exception.kind, "cookies")

    def test_resolve_playlist(self):
        FakeYDL.infos[URL] = {"_type": "playlist", "title": "PL", "entries": [{"url": "https://y/1", "title": "Un"}]}
        result = self.dl.resolve(URL, "mp4", 720, None)
        self.assertIsInstance(result, Playlist)
        self.assertEqual((result.title, result.entries[0]["url"]), ("PL", "https://y/1"))
        self.assertEqual(FakeYDL.instances[0].opts["format"], formats.video_selector(720))

    def test_resolve_video_returns_its_infos(self):
        FakeYDL.infos[URL] = {"title": "T", "filesize": 10}
        self.assertEqual(self.dl.resolve(URL, "mp3", None, None)["title"], "T")
        self.assertEqual(FakeYDL.instances[0].opts["format"], "ba/b")

    def test_fetch_reports_progress_across_video_and_audio(self):
        info = {"title": "T", "duration": 60, "requested_formats": [{"filesize": 600}, {"filesize": 400}]}
        FakeYDL.progress = [
            {"status": "downloading", "filename": "v", "downloaded_bytes": 300, "total_bytes": 600, "speed": 1.0, "eta": 5},
            {"status": "finished", "filename": "v", "total_bytes": 600},
            {"status": "downloading", "filename": "a", "downloaded_bytes": 200, "total_bytes": 400, "speed": 2.0, "eta": 1},
        ]
        item = self.fetch(info)
        self.assertEqual(self.reports, [(30.0, 1.0, 5), (80.0, 2.0, 1)])
        self.assertEqual(item.path, self.tmp / "dl" / "001.mp4")
        self.assertEqual((item.title, item.duration), ("T", 60))
        self.assertEqual(FakeYDL.instances[-1].opts["outtmpl"], str(self.tmp / "dl" / "001") + ".%(ext)s")

    def test_fetch_from_an_url_extracts_first(self):
        FakeYDL.infos["https://y/1"] = {"title": "Un", "duration": 5, "filesize": 10}
        self.assertEqual(self.fetch("https://y/1").title, "Un")

    def test_refused_before_downloading_when_disk_is_short(self):
        self.frees = iter([1000])
        with self.assertRaises(JobError) as ctx:
            self.fetch({"title": "T", "filesize": 600})  # réserve 2 × 600 > 1000
        self.assertEqual(ctx.exception.kind, "disk")
        self.assertEqual(FakeYDL.processed, 0)

    def test_cancel_wins_over_yt_dlp_wrapping(self):
        self.cancel.set()
        FakeYDL.progress = [{"status": "downloading", "downloaded_bytes": 1}]
        with self.assertRaises(Cancelled):
            self.fetch({"title": "T", "filesize": 10})

    def test_disk_watch_stops_the_download(self):
        self.frees = iter([1000, -1])  # réservation acceptée, puis le disque se remplit
        FakeYDL.progress = [{"status": "downloading", "downloaded_bytes": 1}]
        with self.assertRaises(JobError) as ctx:
            self.fetch({"title": "T", "filesize": 10})
        self.assertEqual(ctx.exception.kind, "disk")

    def test_reservation_is_always_released(self):
        self.fetch({"title": "T", "filesize": 10})
        self.guard.reserve("autre", 10**15)  # échouerait si « job » restait réservé
        self.guard.release("autre")
        self.cancel.set()
        FakeYDL.progress = [{"status": "downloading", "downloaded_bytes": 1}]
        with self.assertRaises(Cancelled):
            self.fetch({"title": "T", "filesize": 10})
        self.guard.reserve("autre", 10**15)


if __name__ == "__main__":
    unittest.main()
