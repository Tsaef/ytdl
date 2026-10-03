import io
import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from tests.fakes import FakeAssembler, FakeDownloader, demo_playlist, wait_for
from ytdl.config import Settings
from ytdl.cookies import CookieStore
from ytdl.disk import DiskGuard
from ytdl.errors import JobError
from ytdl.jobs import JobManager
from ytdl.server import MAX_BODY, BadRequest, make_handler, read_json

VIDEO = "https://www.youtube.com/watch?v=abc"
PL = "https://www.youtube.com/playlist?list=PL1"
COOKIE_FAIL = "https://www.youtube.com/watch?v=bot"


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        settings = Settings(data_dir=self.tmp, disk_floor=0)
        cookies = CookieStore(settings.cookies_file)
        guard = DiskGuard(self.tmp, 0)
        downloader = FakeDownloader(playlists={PL: demo_playlist(2)},
                                    errors={COOKIE_FAIL: JobError("cookies", details="Sign in to confirm")})
        self.manager = JobManager(settings, downloader, cookies, guard, FakeAssembler())
        self.manager.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.manager, cookies, guard))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def raw(self, method, path, data=None, content_type="application/json"):
        headers = {"Content-Type": content_type} if data is not None else {}
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def call(self, method, path, body=None, data=None):
        if body is not None:
            data = json.dumps(body).encode()
        status, _, payload = self.raw(method, path, data)
        return status, json.loads(payload)

    def ready_job(self):
        _, job = self.call("POST", "/api/jobs", {"url": VIDEO, "format": "mp4"})
        wait_for(lambda: self.call("GET", f"/api/jobs/{job['id']}")[1]["state"] == "ready")
        return job

    def test_page_and_assets(self):
        status, headers, body = self.raw("GET", "/")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertIn(b"<title>ytdl</title>", body)
        self.assertTrue(self.raw("GET", "/app.js")[1]["Content-Type"].startswith("text/javascript"))
        self.assertTrue(self.raw("GET", "/style.css")[1]["Content-Type"].startswith("text/css"))

    def test_status(self):
        status, body = self.call("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["cookies"], "absent")
        self.assertIsInstance(body["disk_free_bytes"], int)

    def test_analyze(self):
        self.assertEqual(self.call("POST", "/api/analyze", {"url": VIDEO})[1]["kind"], "video")
        status, body = self.call("POST", "/api/analyze", {"url": PL})
        self.assertEqual((status, body["kind"], body["count"]), (200, "playlist", 2))

    def test_analyze_reports_job_errors(self):
        status, body = self.call("POST", "/api/analyze", {"url": COOKIE_FAIL})
        self.assertEqual(status, 422)
        self.assertEqual(body["error"]["kind"], "cookies")

    def test_job_lifecycle(self):
        status, job = self.call("POST", "/api/jobs", {"url": VIDEO, "format": "mp4", "height": 720})
        self.assertEqual(status, 201)
        self.assertEqual((job["format"], job["height"], job["playlist_mode"]), ("mp4", 720, None))
        wait_for(lambda: self.call("GET", f"/api/jobs/{job['id']}")[1]["state"] == "ready")
        self.assertEqual([j["id"] for j in self.call("GET", "/api/jobs")[1]], [job["id"]])
        self.assertEqual(self.call("DELETE", f"/api/jobs/{job['id']}"), (200, {"deleted": job["id"]}))
        self.assertEqual(self.call("GET", f"/api/jobs/{job['id']}")[0], 404)

    def test_invalid_requests_get_400_not_500(self):
        bodies = [
            {"format": "mp4"},
            {"url": "file:///etc/passwd", "format": "mp4"},
            {"url": "javascript:alert(1)", "format": "mp4"},
            {"url": "ftp://example.com/v", "format": "mp4"},
            {"url": VIDEO, "format": "avi"},
            {"url": VIDEO, "format": "mp4", "height": "1080"},
            {"url": VIDEO, "format": "mp4", "height": True},
            {"url": VIDEO, "format": "mp4", "height": 99999},
            {"url": VIDEO, "format": "mp4", "playlist_mode": "tout"},
            {"url": 42, "format": "mp4"},
        ]
        for body in bodies:
            with self.subTest(body=body):
                status, payload = self.call("POST", "/api/jobs", body)
                self.assertEqual(status, 400)
                self.assertEqual(payload["error"]["kind"], "bad_request")
        for data in (b"pas du json", b"[1, 2]", b"\xff\xfe"):
            with self.subTest(data=data):
                self.assertEqual(self.call("POST", "/api/jobs", data=data)[0], 400)
        self.assertEqual(self.call("POST", "/api/analyze", {"url": "file:///etc/passwd"})[0], 400)

    def test_cross_site_form_posts_are_refused(self):
        # Un formulaire d'un autre site peut poster du JSON en text/plain, avec les
        # identifiants basic_auth du navigateur : seul application/json est accepté.
        body = b'{"url":"https://www.youtube.com/watch?v=X","format":"mp4","p":"="}\r\n'
        for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"):
            with self.subTest(content_type=content_type):
                status, _, payload = self.raw("POST", "/api/jobs", body, content_type)
                self.assertEqual(status, 415)
                self.assertEqual(json.loads(payload)["error"]["kind"], "bad_request")
        self.assertEqual(self.call("GET", "/api/jobs")[1], [])
        self.assertEqual(self.raw("POST", "/api/jobs", body, "application/json; charset=utf-8")[0], 201)

    def test_decisions(self):
        job = self.ready_job()
        self.assertEqual(self.call("POST", f"/api/jobs/{job['id']}/decision", {"choice": "zip"})[0], 409)
        self.assertEqual(self.call("POST", f"/api/jobs/{job['id']}/decision", {"choice": "tout"})[0], 400)
        self.assertEqual(self.call("POST", f"/api/jobs/{'0' * 32}/decision", {"choice": "zip"})[0], 404)

    def test_unknown_routes(self):
        self.assertEqual(self.call("GET", "/api/nope")[0], 404)
        self.assertEqual(self.call("GET", "/api/jobs/zzz")[0], 404)
        self.assertEqual(self.call("DELETE", "/api/jobs/zzz")[0], 404)
        self.assertEqual(self.call("POST", "/api/nope", {})[0], 404)


class ReadJsonTest(unittest.TestCase):
    def test_empty_body_is_an_empty_object(self):
        self.assertEqual(read_json(io.BytesIO(b""), None), {})

    def test_too_large(self):
        with self.assertRaises(BadRequest):
            read_json(io.BytesIO(b"{}"), str(MAX_BODY + 1))

    def test_bad_length(self):
        for length in ("abc", "-1"):
            with self.subTest(length=length), self.assertRaises(BadRequest):
                read_json(io.BytesIO(b"{}"), length)


if __name__ == "__main__":
    unittest.main()
