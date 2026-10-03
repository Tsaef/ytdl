import unittest
from pathlib import Path

from ytdl import formats

VIDEO_FORMATS = [
    {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129, "filesize": 10_000_000},
    {"format_id": "251", "vcodec": "none", "acodec": "opus", "abr": 135, "filesize": 11_000_000},
    {"format_id": "137", "vcodec": "avc1.640028", "acodec": "none", "height": 1080, "tbr": 4000, "filesize": 200_000_000},
    {"format_id": "248", "vcodec": "vp9", "acodec": "none", "height": 1080, "tbr": 2600, "filesize": 150_000_000},
    {"format_id": "136", "vcodec": "avc1.4d401f", "acodec": "none", "height": 720, "tbr": 2000, "filesize_approx": 90_000_000},
    {"format_id": "18", "vcodec": "avc1.42001E", "acodec": "mp4a.40.2", "height": 360, "tbr": 500, "filesize": None},
]


class OptionsTest(unittest.TestCase):
    def test_base_options_lock_down_extractors_and_playlists(self):
        o = formats.base_options(None)
        self.assertEqual(o["allowed_extractors"], ["default", "-generic"])
        self.assertTrue(o["noplaylist"])
        self.assertEqual(o["extract_flat"], "in_playlist")
        self.assertEqual(o["playlistend"], 200)
        self.assertNotIn("cookiefile", o)

    def test_base_options_pass_the_cookie_copy(self):
        self.assertEqual(formats.base_options(Path("/data/c/1.txt"))["cookiefile"], "/data/c/1.txt")

    def test_mp4_prefers_h264_aac_under_the_cap(self):
        o = formats.format_options("mp4", 1080)
        self.assertEqual(o["format"], "bv*[height<=1080][vcodec^=avc1]+ba[acodec^=mp4a]/bv*[height<=1080]+ba/b[height<=1080]")
        self.assertEqual(o["merge_output_format"], "mp4")
        self.assertEqual(o["postprocessors"], [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}])

    def test_mp4_without_cap(self):
        self.assertEqual(formats.format_options("mp4", None)["format"], "bv*[vcodec^=avc1]+ba[acodec^=mp4a]/bv*+ba/b")

    def test_mp3_downloads_raw_audio(self):
        self.assertEqual(formats.format_options("mp3", None), {"format": "ba/b"})

    def test_unknown_format(self):
        with self.assertRaises(ValueError):
            formats.format_options("avi", None)


class InfoTest(unittest.TestCase):
    def test_estimate_sums_requested_formats(self):
        info = {"requested_formats": [{"filesize": 600}, {"filesize_approx": 400}]}
        self.assertEqual(formats.estimate_size(info), 1000)

    def test_estimate_unknown_when_a_part_is_unknown(self):
        self.assertIsNone(formats.estimate_size({"requested_formats": [{"filesize": 600}, {}]}))

    def test_estimate_single_format(self):
        self.assertEqual(formats.estimate_size({"filesize_approx": 42}), 42)
        self.assertIsNone(formats.estimate_size({}))

    def test_live_and_upcoming(self):
        self.assertTrue(formats.is_live({"is_live": True}))
        self.assertTrue(formats.is_live({"live_status": "is_upcoming"}))
        self.assertFalse(formats.is_live({"live_status": "was_live"}))

    def test_playlist_entries(self):
        info = {"_type": "playlist", "entries": [
            None,
            {"url": "https://y/1", "title": "Un", "duration": 60},
            {"webpage_url": "https://y/2"},
            {"title": "sans lien"},
        ]}
        self.assertTrue(formats.is_playlist(info))
        self.assertEqual(formats.playlist_entries(info), [
            {"url": "https://y/1", "title": "Un", "duration": 60},
            {"url": "https://y/2", "title": "https://y/2", "duration": None},
        ])

    def test_playlist_entries_capped_at_200(self):
        info = {"_type": "playlist", "entries": [{"url": f"https://y/{i}"} for i in range(250)]}
        self.assertEqual(len(formats.playlist_entries(info)), 200)

    def test_best_audio_prefers_aac(self):
        self.assertEqual(formats.best_audio(VIDEO_FORMATS)["format_id"], "140")

    def test_height_options_prefer_h264_and_add_audio(self):
        self.assertEqual(formats.height_options(VIDEO_FORMATS), [
            {"height": 1080, "size": 210_000_000},
            {"height": 720, "size": 100_000_000},
            {"height": 360, "size": None},
        ])

    def test_summarize_video(self):
        info = {"title": "T", "duration": 600, "thumbnail": "https://i/t.jpg", "formats": VIDEO_FORMATS}
        s = formats.summarize(info)
        self.assertEqual(s["kind"], "video")
        self.assertEqual((s["title"], s["duration"], s["thumbnail"]), ("T", 600, "https://i/t.jpg"))
        self.assertEqual(s["heights"][0], {"height": 1080, "size": 210_000_000})
        self.assertEqual(s["mp3_size"], 14_400_000)

    def test_summarize_playlist(self):
        info = {"_type": "playlist", "title": "PL", "entries": [{"url": "https://y/1", "title": "Un", "duration": 5}]}
        self.assertEqual(formats.summarize(info), {
            "kind": "playlist", "title": "PL", "count": 1, "entries": [{"title": "Un", "duration": 5}],
        })


class NamesTest(unittest.TestCase):
    def test_forbidden_characters_are_replaced(self):
        self.assertEqual(formats.safe_name('Live: "Best" of <AC/DC> | 2024?'), "Live_ _Best_ of _AC_DC_ _ 2024_")

    def test_control_characters_and_spaces(self):
        self.assertEqual(formats.safe_name("a\nb\tc   d"), "a_b_c d")

    def test_never_empty(self):
        for title in (None, "", "   ", "..."):
            with self.subTest(title=title):
                self.assertEqual(formats.safe_name(title), "sans titre")

    def test_trailing_dots_removed(self):
        self.assertEqual(formats.safe_name("titre."), "titre")

    def test_limit_is_in_bytes(self):
        name = formats.safe_name("🎵" * 100)
        self.assertLessEqual(len(name.encode()), 180)
        self.assertEqual(name, "🎵" * 45)

    def test_numbered_pads_to_the_count(self):
        self.assertEqual(formats.numbered(3, 12, "Intro", "mp4"), "03 - Intro.mp4")
        self.assertEqual(formats.numbered(7, 150, "a/b", "mp3"), "007 - a_b.mp3")


if __name__ == "__main__":
    unittest.main()
