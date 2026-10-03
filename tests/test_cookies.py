import shutil
import tempfile
import unittest
from pathlib import Path

from ytdl.cookies import CookieStore


class CookieStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = self.tmp / "cookies.txt"
        self.store = CookieStore(self.path)
        self.copy = self.tmp / "copies" / "job.txt"

    def test_absent(self):
        self.assertEqual(self.store.state, "absent")
        self.assertIsNone(self.store.checkout(self.copy))
        self.assertFalse(self.copy.exists())

    def test_checkout_makes_a_private_copy(self):
        self.path.write_text("v1")
        self.assertIsNotNone(self.store.checkout(self.copy))
        self.assertEqual(self.copy.read_text(), "v1")
        self.assertEqual(self.copy.stat().st_mode & 0o777, 0o600)

    def test_checkin_writes_back_refreshed_cookies(self):
        self.path.write_text("v1")
        version = self.store.checkout(self.copy)
        self.copy.write_text("v1 rafraîchi par yt-dlp")
        self.store.checkin(self.copy, version)
        self.assertEqual(self.path.read_text(), "v1 rafraîchi par yt-dlp")
        self.assertFalse(self.copy.exists())

    def test_checkin_never_overwrites_a_renewed_file(self):
        self.path.write_text("ancien")
        version = self.store.checkout(self.copy)
        self.copy.write_text("ancien rafraîchi")
        self.path.write_text("nouveaux cookies déposés par scp")
        self.store.checkin(self.copy, version)
        self.assertEqual(self.path.read_text(), "nouveaux cookies déposés par scp")
        self.assertFalse(self.copy.exists())

    def test_checkin_without_version_only_removes_the_copy(self):
        self.copy.parent.mkdir()
        self.copy.write_text("x")
        self.store.checkin(self.copy, None)
        self.assertFalse(self.copy.exists())
        self.assertFalse(self.path.exists())

    def test_verdicts(self):
        self.path.write_text("v1")
        self.assertEqual(self.store.state, "unknown")
        self.store.mark("expired")
        self.assertEqual(self.store.state, "expired")
        self.store.mark("ok")
        self.assertEqual(self.store.state, "ok")

    def test_renewed_file_clears_the_expired_verdict(self):
        self.path.write_text("v1")
        self.store.mark("expired")
        self.path.write_text("v2 renouvelé")
        self.assertEqual(self.store.state, "unknown")

    def test_own_checkin_keeps_the_verdict(self):
        self.path.write_text("v1")
        version = self.store.checkout(self.copy)
        self.store.mark("ok")
        self.copy.write_text("v1 rafraîchi")
        self.store.checkin(self.copy, version)
        self.assertEqual(self.store.state, "ok")


if __name__ == "__main__":
    unittest.main()
