"""Vraie page et vraie API, faux téléchargeur, sans réseau : python3 -m tests.devserver

Liens de démo : n'importe quelle URL http(s) (vidéo), https://example.com/playlist
(3 vidéos de formats différents : la fusion demande une décision) et
https://example.com/erreur (cookies expirés).
"""
import tempfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from tests.fakes import FakeAssembler, FakeDownloader, demo_playlist
from ytdl.config import Settings
from ytdl.cookies import CookieStore
from ytdl.disk import DiskGuard
from ytdl.errors import JobError
from ytdl.jobs import JobManager
from ytdl.server import make_handler


def main() -> None:
    data = Path(tempfile.mkdtemp(prefix="ytdl-dev-"))
    settings = Settings(data_dir=data, disk_floor=0)
    cookies = CookieStore(settings.cookies_file)
    guard = DiskGuard(data, 0)
    downloader = FakeDownloader(
        playlists={"https://example.com/playlist": demo_playlist(3, "Playlist de démo")},
        errors={"https://example.com/erreur": JobError("cookies", details="ERROR: Sign in to confirm you're not a bot")},
        delay=1.0,
    )
    manager = JobManager(settings, downloader, cookies, guard, FakeAssembler(compatible=False))
    manager.start()
    base = make_handler(manager, cookies, guard)
    jobs_dir = settings.jobs_dir.resolve()

    class DevHandler(base):
        def do_GET(self):
            # En prod, Caddy sert /files ; ici, un équivalent minimal.
            path = unquote(urlparse(self.path).path)
            if path.startswith("/files/"):
                target = (jobs_dir / path.removeprefix("/files/")).resolve()
                if target.is_file() and target.is_relative_to(jobs_dir):
                    data = target.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Content-Disposition", "attachment")
                    self.end_headers()
                    self.wfile.write(data)
                    return
            super().do_GET()

    print("http://localhost:8000  (données : " + str(data) + ")", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8000), DevHandler).serve_forever()


if __name__ == "__main__":
    main()
