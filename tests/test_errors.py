import unittest

from ytdl.config import GB
from ytdl.errors import JobError, classify, disk_error, last_line

CASES = [
    ("ERROR: [youtube] jNQXAC9IVRw: Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies for the authentication.", "cookies"),
    ("ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate for some users.", "cookies"),
    ("ERROR: [youtube] abc: The provided YouTube account cookies are no longer valid.", "cookies"),
    ("ERROR: [youtube] abc: Video unavailable", "unavailable"),
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video. Use --cookies-from-browser or --cookies for the authentication.", "unavailable"),
    ("ERROR: [youtube] abc: The uploader has not made this video available in your country", "unavailable"),
    ("ERROR: [youtube] abc: Join this channel to get access to members-only content like this video", "unavailable"),
    ("ERROR: Unsupported URL: https://example.com/page", "unsupported"),
    ("ERROR: No suitable extractor found for URL http://172.18.0.1:8765/", "unsupported"),
    ("ERROR: [youtube] abc: This live event will begin in 3 hours.", "live"),
    ("ERROR: [youtube] abc: Premieres in 2 hours", "live"),
    ("ERROR: Unable to download webpage: <urlopen error [Errno 111] Connection refused>", "network"),
    ("ERROR: unable to download video data: HTTP Error 503: Service Unavailable", "network"),
    ("ERROR: [youtube] abc: Read timed out.", "network"),
    ("ERROR: [youtube] abc: Requested format is not available. Use --list-formats for a list of available formats", "other"),
]


class ClassifyTest(unittest.TestCase):
    def test_known_messages(self):
        for raw, kind in CASES:
            with self.subTest(raw=raw):
                self.assertEqual(classify(raw).kind, kind)

    def test_french_message_and_original_last_line(self):
        e = classify("WARNING: x\nERROR: [youtube] abc: Video unavailable\n")
        self.assertEqual(e.message, "Vidéo indisponible")
        self.assertEqual(e.details, "ERROR: [youtube] abc: Video unavailable")

    def test_last_line_of_empty_text(self):
        self.assertEqual(last_line("  \n "), "")

    def test_disk_error_states_needed_size_in_french(self):
        self.assertEqual(disk_error(3 * GB).message, "Espace disque insuffisant (3,0 Go nécessaires)")
        self.assertEqual(disk_error().message, "Espace disque insuffisant")

    def test_to_dict(self):
        self.assertEqual(
            JobError("network", details="d").to_dict(),
            {"kind": "network", "message": "Erreur réseau, réessaie", "details": "d"},
        )


if __name__ == "__main__":
    unittest.main()
