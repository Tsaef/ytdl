import unittest
from pathlib import Path
from types import SimpleNamespace

from ytdl.config import GB
from ytdl.disk import DiskGuard
from ytdl.errors import JobError


class DiskGuardTest(unittest.TestCase):
    def setUp(self):
        self.free = 50 * GB
        self.now = 0.0
        self.guard = DiskGuard(Path("/data"), 10 * GB,
                               usage=lambda p: SimpleNamespace(free=self.free), clock=lambda: self.now)

    def test_reserve_within_the_margin(self):
        self.guard.reserve("a", 30 * GB)  # 50 - 30 = 20 ≥ 10

    def test_refusal_states_the_needed_size(self):
        with self.assertRaises(JobError) as ctx:
            self.guard.reserve("a", 45 * GB)
        self.assertEqual(ctx.exception.kind, "disk")
        self.assertIn("45,0 Go", ctx.exception.message)

    def test_other_reservations_count(self):
        self.guard.reserve("a", 20 * GB)
        with self.assertRaises(JobError):
            self.guard.reserve("b", 25 * GB)  # 50 - 20 - 25 = 5 < 10

    def test_same_key_replaces_its_reservation(self):
        self.guard.reserve("a", 20 * GB)
        self.guard.reserve("a", 25 * GB)  # 50 - 25 = 25, pas 50 - 20 - 25

    def test_release(self):
        self.guard.reserve("a", 20 * GB)
        self.guard.release("a")
        self.guard.reserve("b", 30 * GB)

    def test_unknown_size_still_respects_the_floor(self):
        self.free = 9 * GB
        with self.assertRaises(JobError):
            self.guard.reserve("a", 0)

    def test_check_raises_under_the_floor(self):
        self.free = 9 * GB
        with self.assertRaises(JobError):
            self.guard.check()

    def test_check_runs_at_most_once_per_second(self):
        self.guard.check()
        self.free = 1 * GB
        self.now = 0.5
        self.guard.check()  # ignorée : moins d'une seconde après la précédente
        self.now = 1.5
        with self.assertRaises(JobError):
            self.guard.check()


if __name__ == "__main__":
    unittest.main()
