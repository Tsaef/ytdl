import os
import shutil
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from tests.fakes import FakeAssembler, FakeDownloader, demo_playlist, wait_for
from ytdl.config import Settings
from ytdl.cookies import CookieStore
from ytdl.disk import DiskGuard
from ytdl.errors import JobError
from ytdl.jobs import NO_SPACE_NOTE, Conflict, JobManager

VIDEO = "https://www.youtube.com/watch?v=abc"
PL = "https://www.youtube.com/playlist?list=PL1"


class JobManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.now = 1000.0
        self.free = 10**15
        self.settings = Settings(data_dir=self.tmp, workers=2, disk_floor=0, ttl=3600)
        self.cookies = CookieStore(self.settings.cookies_file)
        self.guard = DiskGuard(self.tmp, 0, usage=lambda p: SimpleNamespace(free=self.free))

    def start(self, downloader, assembler=None):
        self.asm = assembler or FakeAssembler()
        manager = JobManager(self.settings, downloader, self.cookies, self.guard, self.asm, clock=lambda: self.now)
        manager.start()
        return manager

    def until(self, manager, job_id, state):
        wait_for(lambda: (manager.get(job_id) or {}).get("state") == state)
        return manager.get(job_id)

    def job_dir(self, job):
        return self.settings.jobs_dir / job["id"]

    # ---- vidéo seule ---------------------------------------------------
    def test_single_mp4_is_delivered_under_a_clean_name(self):
        m = self.start(FakeDownloader())
        job = m.submit(VIDEO, "mp4", 1080, None)
        done = self.until(m, job["id"], "ready")
        name = "Vidéo watch_v=abc.mp4"
        self.assertEqual(done["file"], {"name": name, "size": len(f"contenu de {VIDEO}"),
                                        "url": f"/files/{job['id']}/{quote(name)}"})
        self.assertEqual(os.listdir(self.job_dir(job)), [name])  # dl/ effacé
        self.assertEqual(done["expires_at"], done["finished_at"] + 3600)
        self.assertEqual(done["title"], "Vidéo watch?v=abc")

    def test_single_mp3_is_converted(self):
        m = self.start(FakeDownloader())
        done = self.until(m, m.submit(VIDEO, "mp3", None, None)["id"], "ready")
        self.assertEqual(done["file"]["name"], "Vidéo watch_v=abc.mp3")
        self.assertEqual(self.asm.calls, ["to_mp3"])

    def test_cookie_copy_never_lands_in_the_served_dir(self):
        self.settings.cookies_file.write_text("cookies")
        gate = threading.Event()
        m = self.start(FakeDownloader(gate=gate))
        job = m.submit(VIDEO, "mp4", None, None)
        self.until(m, job["id"], "downloading")
        wait_for(lambda: any(self.settings.cookie_copies_dir.iterdir()))
        self.assertEqual([p for p in self.job_dir(job).rglob("*.txt")], [])
        gate.set()
        done = self.until(m, job["id"], "ready")
        self.assertEqual(os.listdir(self.job_dir(job)), [done["file"]["name"]])
        # La copie est rendue juste après le passage en « ready » (bloc finally du worker).
        wait_for(lambda: not any(self.settings.cookie_copies_dir.iterdir()))

    def test_youtube_success_marks_cookies_ok(self):
        self.settings.cookies_file.write_text("cookies")
        m = self.start(FakeDownloader())
        self.until(m, m.submit(VIDEO, "mp4", None, None)["id"], "ready")
        wait_for(lambda: self.cookies.state == "ok")  # verdict posé juste après « ready »

    def test_cookie_error_marks_cookies_expired(self):
        self.settings.cookies_file.write_text("cookies")
        m = self.start(FakeDownloader(errors={VIDEO: JobError("cookies", details="Sign in to confirm")}))
        job = m.submit(VIDEO, "mp4", None, None)
        failed = self.until(m, job["id"], "error")
        self.assertEqual(failed["error"]["kind"], "cookies")
        self.assertEqual(self.cookies.state, "expired")
        self.assertFalse(self.job_dir(job).exists())

    # ---- file, annulation, effacement ----------------------------------
    def test_two_downloads_at_a_time_the_third_waits(self):
        gate = threading.Event()
        m = self.start(FakeDownloader(gate=gate))
        jobs = [m.submit(f"https://www.youtube.com/watch?v={i}", "mp4", None, None) for i in range(3)]
        wait_for(lambda: [m.get(j["id"])["state"] for j in jobs].count("downloading") == 2)
        self.assertEqual([m.get(j["id"])["state"] for j in jobs].count("queued"), 1)
        gate.set()
        for j in jobs:
            self.until(m, j["id"], "ready")

    def test_cancel_a_running_job(self):
        m = self.start(FakeDownloader(gate=threading.Event()))
        job = m.submit(VIDEO, "mp4", None, None)
        self.until(m, job["id"], "downloading")
        self.assertTrue(m.delete(job["id"]))
        self.until(m, job["id"], "cancelled")
        self.assertFalse(self.job_dir(job).exists())

    def test_cancel_a_queued_job(self):
        gate = threading.Event()
        m = self.start(FakeDownloader(gate=gate))
        jobs = [m.submit(f"https://www.youtube.com/watch?v={i}", "mp4", None, None) for i in range(3)]
        wait_for(lambda: [m.get(j["id"])["state"] for j in jobs].count("downloading") == 2)
        queued = next(j for j in jobs if m.get(j["id"])["state"] == "queued")
        m.delete(queued["id"])
        self.assertEqual(m.get(queued["id"])["state"], "cancelled")
        gate.set()
        for j in jobs:
            if j is not queued:
                self.until(m, j["id"], "ready")
        self.assertEqual(m.get(queued["id"])["state"], "cancelled")

    def test_delete_a_finished_job(self):
        m = self.start(FakeDownloader())
        job = m.submit(VIDEO, "mp4", None, None)
        self.until(m, job["id"], "ready")
        self.assertTrue(m.delete(job["id"]))
        self.assertIsNone(m.get(job["id"]))
        self.assertFalse(self.job_dir(job).exists())
        self.assertFalse(m.delete(job["id"]))

    def test_sweep_removes_expired_jobs(self):
        m = self.start(FakeDownloader())
        job = m.submit(VIDEO, "mp4", None, None)
        self.until(m, job["id"], "ready")
        m.sweep()
        self.assertIsNotNone(m.get(job["id"]))
        self.now += 3601
        m.sweep()
        self.assertIsNone(m.get(job["id"]))
        self.assertFalse(self.job_dir(job).exists())

    def test_start_empties_jobs_dir_but_keeps_the_mounted_directory(self):
        jobs_dir = self.settings.jobs_dir
        (jobs_dir / "ancienne").mkdir(parents=True)
        (jobs_dir / "ancienne" / "x.mp4").write_bytes(b"x")
        (jobs_dir / "orphelin.txt").write_text("x")
        inode = jobs_dir.stat().st_ino
        self.start(FakeDownloader())
        self.assertEqual(jobs_dir.stat().st_ino, inode)  # même dossier : le montage de Caddy le voit
        self.assertEqual(list(jobs_dir.iterdir()), [])

    def test_unexpected_crash_keeps_the_worker_alive(self):
        m = self.start(FakeDownloader(crash={VIDEO}))
        failed = self.until(m, m.submit(VIDEO, "mp4", None, None)["id"], "error")
        self.assertEqual(failed["error"]["kind"], "other")
        self.until(m, m.submit("https://www.youtube.com/watch?v=ok", "mp4", None, None)["id"], "ready")

    # ---- playlists -----------------------------------------------------
    def test_playlist_zip_skips_unavailable_videos(self):
        broken = "https://www.youtube.com/watch?v=v2"
        m = self.start(FakeDownloader(playlists={PL: demo_playlist(3)}, errors={broken: JobError("unavailable")}))
        job = m.submit(PL, "mp4", None, "zip")
        done = self.until(m, job["id"], "ready")
        self.assertEqual(done["file"]["name"], "Ma playlist.zip")
        self.assertEqual(done["skipped"], ["Titre 2"])
        with zipfile.ZipFile(self.job_dir(job) / "Ma playlist.zip") as z:
            self.assertEqual(z.namelist(), ["01 - Titre 1.mp4", "02 - Titre 3.mp4"])

    def test_playlist_skips_any_failing_video_except_cookies_and_disk(self):
        broken = "https://www.youtube.com/watch?v=v2"
        m = self.start(FakeDownloader(playlists={PL: demo_playlist(3)},
                                      errors={broken: JobError("other", details="HTTP Error 403: Forbidden")}))
        done = self.until(m, m.submit(PL, "mp4", None, "zip")["id"], "ready")
        self.assertEqual(done["skipped"], ["Titre 2"])

    def test_playlist_stops_on_a_cookie_error(self):
        broken = "https://www.youtube.com/watch?v=v2"
        m = self.start(FakeDownloader(playlists={PL: demo_playlist(3)},
                                      errors={broken: JobError("cookies", details="Sign in to confirm you're not a bot")}))
        job = m.submit(PL, "mp4", None, "zip")
        failed = self.until(m, job["id"], "error")
        self.assertEqual(failed["error"]["kind"], "cookies")
        self.assertFalse(self.job_dir(job).exists())

    def test_playlist_mode_defaults_to_zip(self):
        m = self.start(FakeDownloader(playlists={PL: demo_playlist(2)}))
        done = self.until(m, m.submit(PL, "mp4", None, None)["id"], "ready")
        self.assertEqual(done["file"]["name"], "Ma playlist.zip")

    def test_playlist_with_nothing_available(self):
        pl = demo_playlist(2)
        m = self.start(FakeDownloader(playlists={PL: pl}, errors={e["url"]: JobError("unavailable") for e in pl.entries}))
        failed = self.until(m, m.submit(PL, "mp4", None, "zip")["id"], "error")
        self.assertEqual(failed["error"]["message"], "Aucune vidéo de la playlist n'est disponible")

    def test_playlist_concat_compatible_is_copied(self):
        m = self.start(FakeDownloader(playlists={PL: demo_playlist(3)}), FakeAssembler(compatible=True))
        done = self.until(m, m.submit(PL, "mp4", None, "concat")["id"], "ready")
        self.assertEqual(done["file"]["name"], "Ma playlist.mp4")
        self.assertEqual(self.asm.calls, ["concat_copy"])

    def test_playlist_concat_mp3(self):
        m = self.start(FakeDownloader(playlists={PL: demo_playlist(2)}), FakeAssembler(compatible=False))
        done = self.until(m, m.submit(PL, "mp3", None, "concat")["id"], "ready")
        self.assertEqual(done["file"]["name"], "Ma playlist.mp3")
        self.assertEqual(self.asm.calls, ["to_mp3", "to_mp3", "concat_copy"])

    def test_incompatible_videos_wait_for_a_decision_then_reencode(self):
        m = self.start(FakeDownloader(playlists={PL: demo_playlist(3)}), FakeAssembler(compatible=False))
        job = m.submit(PL, "mp4", None, "concat")
        waiting = self.until(m, job["id"], "awaiting_decision")
        self.assertEqual(waiting["decision"], {"reencode_estimate_s": 180})  # 3 × 60 s en 1080p
        self.assertEqual(waiting["expires_at"], self.now + 3600)
        m.decide(job["id"], "reencode")
        done = self.until(m, job["id"], "ready")
        self.assertEqual(done["file"]["name"], "Ma playlist.mp4")
        self.assertEqual(self.asm.calls, [("normalize", 1080)] * 3 + ["concat_copy"])
        self.assertIsNone(done["decision"])

    def test_decision_zip_instead(self):
        m = self.start(FakeDownloader(playlists={PL: demo_playlist(3)}), FakeAssembler(compatible=False))
        job = m.submit(PL, "mp4", None, "concat")
        self.until(m, job["id"], "awaiting_decision")
        m.decide(job["id"], "zip")
        done = self.until(m, job["id"], "ready")
        with zipfile.ZipFile(self.job_dir(job) / done["file"]["name"]) as z:
            self.assertEqual(len(z.namelist()), 3)

    def test_decide_without_a_pending_decision(self):
        m = self.start(FakeDownloader())
        job = m.submit(VIDEO, "mp4", None, None)
        self.until(m, job["id"], "ready")
        with self.assertRaises(Conflict):
            m.decide(job["id"], "zip")
        self.assertIsNone(m.decide("0" * 32, "zip"))

    def test_concat_without_room_falls_back_to_zip(self):
        self.free = 10  # la réservation pour le fichier unique échoue
        m = self.start(FakeDownloader(playlists={PL: demo_playlist(2)}), FakeAssembler(compatible=True))
        done = self.until(m, m.submit(PL, "mp4", None, "concat")["id"], "ready")
        self.assertEqual(done["file"]["name"], "Ma playlist.zip")
        self.assertEqual(done["note"], NO_SPACE_NOTE)

    def test_analyze_uses_a_cookie_copy_and_cleans_it(self):
        self.settings.cookies_file.write_text("cookies")
        m = self.start(FakeDownloader(playlists={PL: demo_playlist(2)}))
        self.assertEqual(m.analyze(PL)["count"], 2)
        self.assertEqual(list(self.settings.cookie_copies_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
