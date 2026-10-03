import unittest
from pathlib import Path

from ytdl.config import GB, Settings, from_env


class FromEnvTest(unittest.TestCase):
    def test_defaults_match_spec(self):
        s = from_env({})
        self.assertEqual(s.data_dir, Path("/data"))
        self.assertEqual(s.workers, 2)
        self.assertEqual(s.disk_floor, 10 * GB)
        self.assertEqual(s.ttl, 6 * 3600)
        self.assertEqual(s.reencode_factor, 1.0)
        self.assertEqual(s.port, 8000)
        self.assertEqual(s.jobs_dir, Path("/data/jobs"))
        self.assertEqual(s.cookies_file, Path("/data/cookies.txt"))

    def test_overrides_use_gigabytes_and_hours(self):
        s = from_env({"YTDL_DATA": "/tmp/x", "YTDL_WORKERS": "3", "YTDL_DISK_FLOOR": "2.5", "YTDL_TTL": "1"})
        self.assertEqual(s.data_dir, Path("/tmp/x"))
        self.assertEqual((s.workers, s.disk_floor, s.ttl), (3, int(2.5 * GB), 3600.0))

    def test_cookie_copies_live_outside_the_served_jobs_dir(self):
        s = Settings(data_dir=Path("/data"))
        self.assertFalse(s.cookie_copies_dir.is_relative_to(s.jobs_dir))


if __name__ == "__main__":
    unittest.main()
