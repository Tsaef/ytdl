"""Serveur HTTP : la page et l'API JSON (spec §5). Lancement : python -m ytdl.server"""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .config import from_env
from .cookies import CookieStore
from .disk import DiskGuard
from .downloader import Downloader
from .errors import JobError
from .jobs import Conflict, JobManager

WEB = Path(__file__).resolve().parent.parent / "web"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
MAX_BODY = 64 * 1024
JOB_PATH = re.compile(r"^/api/jobs/([0-9a-f]{32})(/decision)?$")


class BadRequest(Exception):
    pass


def read_json(rfile, length_header: str | None) -> dict:
    try:
        length = int(length_header or 0)
    except ValueError:
        raise BadRequest("Content-Length invalide") from None
    if not 0 <= length <= MAX_BODY:
        raise BadRequest("Requête trop volumineuse")
    try:
        data = json.loads(rfile.read(length) or b"{}")
    except ValueError:  # comprend UnicodeDecodeError
        raise BadRequest("JSON invalide") from None
    if not isinstance(data, dict):
        raise BadRequest("Objet JSON attendu")
    return data


def parse_url(value) -> str:
    if not isinstance(value, str) or len(value) > 2000:
        raise BadRequest("Lien invalide")
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise BadRequest("Lien invalide : http(s) attendu")
    return value


def parse_job_request(body: dict) -> tuple:
    url = parse_url(body.get("url"))
    fmt = body.get("format")
    if fmt not in ("mp4", "mp3"):
        raise BadRequest("Format invalide : mp4 ou mp3")
    height = body.get("height")
    if height is not None and (type(height) is not int or not 144 <= height <= 4320):
        raise BadRequest("Qualité invalide")
    mode = body.get("playlist_mode")
    if mode not in (None, "zip", "concat"):
        raise BadRequest("Mode de playlist invalide : zip ou concat")
    return url, fmt, height, mode


def make_handler(manager: JobManager, cookies: CookieStore, guard: DiskGuard):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ytdl"

        def log_message(self, format, *args):  # Caddy journalise déjà chaque requête
            pass

        def _send(self, status, payload=None, body=None, content_type="application/json; charset=utf-8"):
            data = body if body is not None else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _error(self, status, kind, message, details=""):
            self._send(status, {"error": {"kind": kind, "message": message, "details": details}})

        def do_GET(self):
            path = urlparse(self.path).path
            if path in STATIC:
                name, content_type = STATIC[path]
                return self._send(200, body=(WEB / name).read_bytes(), content_type=content_type)
            if path == "/api/status":
                return self._send(200, {"cookies": cookies.state, "disk_free_bytes": guard.free()})
            if path == "/api/jobs":
                return self._send(200, manager.list_jobs())
            match = JOB_PATH.match(path)
            job = manager.get(match.group(1)) if match and not match.group(2) else None
            if job is None:
                return self._error(404, "not_found", "Introuvable")
            self._send(200, job)

        def do_POST(self):
            path = urlparse(self.path).path
            match = JOB_PATH.match(path)
            try:
                body = read_json(self.rfile, self.headers.get("Content-Length"))
                # Anti-CSRF : un formulaire d'un autre site ne peut pas envoyer
                # application/json, et fetch() déclencherait un preflight jamais accepté.
                content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if content_type != "application/json":
                    return self._error(415, "bad_request", "Content-Type application/json attendu")
                if path == "/api/analyze":
                    return self._send(200, manager.analyze(parse_url(body.get("url"))))
                if path == "/api/jobs":
                    return self._send(201, manager.submit(*parse_job_request(body)))
                if match and match.group(2):
                    if body.get("choice") not in ("reencode", "zip"):
                        raise BadRequest("Choix invalide : reencode ou zip")
                    job = manager.decide(match.group(1), body["choice"])
                    if job is None:
                        return self._error(404, "not_found", "Tâche introuvable")
                    return self._send(200, job)
                self._error(404, "not_found", "Introuvable")
            except BadRequest as e:
                self._error(400, "bad_request", str(e))
            except Conflict as e:
                self._error(409, "conflict", str(e))
            except JobError as e:
                self._send(422, {"error": e.to_dict()})
            except Exception as e:  # jamais de connexion coupée sans réponse
                self._error(500, "other", "Erreur interne", repr(e))

        def do_DELETE(self):
            match = JOB_PATH.match(urlparse(self.path).path)
            if match and not match.group(2) and manager.delete(match.group(1)):
                return self._send(200, {"deleted": match.group(1)})
            self._error(404, "not_found", "Tâche introuvable")

    return Handler


def main() -> None:
    settings = from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    cookies = CookieStore(settings.cookies_file)
    guard = DiskGuard(settings.data_dir, settings.disk_floor)
    manager = JobManager(settings, Downloader(guard), cookies, guard)
    manager.start()
    server = ThreadingHTTPServer(("0.0.0.0", settings.port), make_handler(manager, cookies, guard))
    print(f"ytdl : écoute sur le port {settings.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
