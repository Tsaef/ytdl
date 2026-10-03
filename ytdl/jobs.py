"""File de tâches : états, workers, annulation, décision, expiration (spec §5, §8, §9, §12)."""
import copy
import queue
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlparse

from . import assemble, formats
from .downloader import Item, Playlist
from .errors import Cancelled, JobError

ACTIVE = ("queued", "downloading", "processing")
PUBLIC = ("id", "url", "title", "format", "height", "playlist_mode", "state", "progress", "error",
          "decision", "file", "skipped", "note", "created_at", "finished_at", "expires_at")
NO_SPACE_NOTE = "Pas assez d'espace disque pour un fichier unique : livré en ZIP."


class Conflict(Exception):
    """Action impossible dans l'état actuel de la tâche."""


def is_youtube(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ("youtu.be", "youtube.com") or host.endswith(".youtube.com")


@dataclass
class Job:
    id: str
    url: str
    format: str
    height: int | None
    playlist_mode: str | None
    created_at: float
    title: str | None = None
    state: str = "queued"
    progress: dict = field(default_factory=dict)
    error: dict | None = None
    decision: dict | None = None
    file: dict | None = None
    skipped: list = field(default_factory=list)
    note: str | None = None
    finished_at: float | None = None
    expires_at: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    pending: list = field(default_factory=list)  # [(Item, Media)] en attente de décision

    def to_dict(self) -> dict:
        return {key: copy.deepcopy(getattr(self, key)) for key in PUBLIC}


def _empty(directory: Path) -> None:
    """Vide un dossier sans le supprimer : jobs/ est monté tel quel dans Caddy."""
    directory.mkdir(parents=True, exist_ok=True)
    for child in directory.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


class JobManager:
    def __init__(self, settings, downloader, cookies, guard, assembler=assemble, clock=time.time):
        self.settings = settings
        self._dl = downloader
        self._cookies = cookies
        self._guard = guard
        self._asm = assembler
        self._clock = clock
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._queue: queue.Queue = queue.Queue()

    # ---- cycle de vie ---------------------------------------------------
    def start(self) -> None:
        # Les tâches en mémoire sont perdues au redémarrage : leurs fichiers aussi.
        _empty(self.settings.jobs_dir)
        _empty(self.settings.cookie_copies_dir)
        for _ in range(self.settings.workers):
            threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._janitor, daemon=True).start()

    def _janitor(self) -> None:
        while True:
            time.sleep(300)
            self.sweep()

    def sweep(self) -> None:
        now = self._clock()
        with self._lock:
            expired = [j for j in self._jobs.values() if j.expires_at is not None and j.expires_at <= now]
            for job in expired:
                del self._jobs[job.id]
        for job in expired:
            shutil.rmtree(self._dir(job), ignore_errors=True)

    # ---- API ------------------------------------------------------------
    def analyze(self, url: str) -> dict:
        copy_path = self.settings.cookie_copies_dir / f"analyze-{uuid.uuid4().hex}.txt"
        version = self._cookies.checkout(copy_path)
        try:
            return self._dl.analyze(url, copy_path if version else None)
        except JobError as e:
            if e.kind == "cookies":
                self._cookies.mark("expired")
            raise
        finally:
            self._cookies.checkin(copy_path, version)

    def submit(self, url: str, fmt: str, height: int | None, playlist_mode: str | None) -> dict:
        job = Job(uuid.uuid4().hex, url, fmt, height, playlist_mode, created_at=self._clock())
        with self._lock:
            self._jobs[job.id] = job
        self._queue.put((job, "run"))
        return self.get(job.id)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.to_dict() if job else None

    def list_jobs(self) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return [j.to_dict() for j in jobs]

    def delete(self, job_id: str) -> bool:
        """Annule une tâche en attente ou en cours ; efface une tâche terminée."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job.state in ACTIVE:
                job.cancel.set()
                if job.state != "queued":
                    return True  # le worker voit l'annulation, efface et passe en « cancelled »
                self._finish(job, "cancelled")
            else:
                del self._jobs[job_id]
        shutil.rmtree(self._dir(job), ignore_errors=True)
        return True

    def decide(self, job_id: str, choice: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.state != "awaiting_decision":
                raise Conflict("Aucune décision en attente pour cette tâche")
            job.state, job.decision, job.finished_at, job.expires_at = "queued", None, None, None
        self._queue.put((job, choice))
        return self.get(job_id)

    # ---- exécution ------------------------------------------------------
    def _dir(self, job: Job) -> Path:
        return self.settings.jobs_dir / job.id

    def _update(self, job: Job, **fields) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(job, key, value)

    def _finish(self, job: Job, state: str, **fields) -> None:
        now = self._clock()
        self._update(job, state=state, finished_at=now, expires_at=now + self.settings.ttl, progress={}, **fields)

    def _worker(self) -> None:
        while True:
            job, action = self._queue.get()
            self._execute(job, action)

    def _execute(self, job: Job, action: str) -> None:
        with self._lock:
            if job.cancel.is_set() or self._jobs.get(job.id) is not job:
                return
            job.state = "downloading" if action == "run" else "processing"
        # Copie des cookies HORS de jobs/ : Caddy sert jobs/.
        copy_path = self.settings.cookie_copies_dir / f"{job.id}.txt"
        version = self._cookies.checkout(copy_path) if action == "run" else None
        try:
            if action == "run":
                self._run(job, copy_path if version else None)
                if is_youtube(job.url):
                    self._cookies.mark("ok")
            elif action == "reencode":
                self._reencode(job)
            else:
                self._deliver_zip(job, [item for item, _ in job.pending])
        except Cancelled:
            shutil.rmtree(self._dir(job), ignore_errors=True)
            self._finish(job, "cancelled", pending=[])
        except JobError as e:
            if e.kind == "cookies":
                self._cookies.mark("expired")
            shutil.rmtree(self._dir(job), ignore_errors=True)
            self._finish(job, "error", error=e.to_dict(), pending=[])
        except Exception as e:  # filet : une tâche ne doit jamais tuer son worker
            shutil.rmtree(self._dir(job), ignore_errors=True)
            self._finish(job, "error", error=JobError("other", details=repr(e)).to_dict(), pending=[])
        finally:
            self._cookies.checkin(copy_path, version)
            self._guard.release(job.id)

    def _progress(self, job: Job, item: int | None = None, items: int | None = None):
        def report(percent, speed, eta):
            self._update(job, progress={"percent": percent, "speed_bps": speed, "eta_s": eta,
                                        "item": item, "items": items})
        return report

    def _run(self, job: Job, cookiefile: Path | None) -> None:
        dl_dir = self._dir(job) / "dl"
        target = self._dl.resolve(job.url, job.format, job.height, cookiefile)
        if isinstance(target, Playlist):
            self._update(job, title=target.title)
            items = self._fetch_playlist(job, target, dl_dir, cookiefile)
        else:
            self._update(job, title=target.get("title"))
            items = [self._dl.fetch(target, job.format, job.height, dl_dir / "001", cookiefile,
                                    job.id, self._progress(job), job.cancel)]
        self._update(job, state="processing", progress={})
        if job.format == "mp3":
            items = [self._to_mp3(job, item) for item in items]
        if not isinstance(target, Playlist):
            return self._ready(job, self._move_single(job, items[0]))
        if (job.playlist_mode or "zip") == "zip":
            return self._deliver_zip(job, items)
        medias = [self._asm.probe(item.path) for item in items]
        if job.format == "mp3" or self._asm.compatible(medias):
            return self._deliver_concat(job, items, medias)
        height = self._asm.target_height(medias, job.height)
        estimate = self._asm.reencode_estimate(medias, height, self.settings.reencode_factor)
        now = self._clock()
        self._update(job, state="awaiting_decision", pending=list(zip(items, medias)),
                     decision={"reencode_estimate_s": estimate}, finished_at=now,
                     expires_at=now + self.settings.ttl, progress={})

    def _fetch_playlist(self, job: Job, playlist: Playlist, dl_dir: Path, cookiefile: Path | None) -> list[Item]:
        items, skipped = [], []
        count = len(playlist.entries)
        for index, entry in enumerate(playlist.entries, 1):
            if job.cancel.is_set():
                raise Cancelled()
            self._update(job, progress={"percent": 0.0, "item": index, "items": count})
            try:
                items.append(self._dl.fetch(entry["url"], job.format, job.height, dl_dir / f"{index:03d}",
                                            cookiefile, job.id, self._progress(job, index, count), job.cancel))
            except JobError as e:
                # Cookies ou disque : les vidéos suivantes échoueraient aussi, on arrête.
                # Tout le reste (403, format absent, DRM…) ne touche que cette vidéo.
                if e.kind in ("cookies", "disk"):
                    raise
                skipped.append(entry["title"])
                self._update(job, skipped=list(skipped))
        if not items:
            raise JobError("unavailable", "Aucune vidéo de la playlist n'est disponible")
        return items

    def _to_mp3(self, job: Job, item: Item) -> Item:
        out = item.path.with_name(item.path.stem + ".conv.mp3")
        self._asm.to_mp3(item.path, out, job.cancel)
        item.path.unlink()
        return Item(out, item.title, item.duration)

    def _move_single(self, job: Job, item: Item) -> Path:
        out = self._dir(job) / f"{formats.safe_name(item.title)}.{job.format}"
        item.path.replace(out)
        return out

    def _deliver_zip(self, job: Job, items: list[Item], note: str | None = None) -> None:
        out = self._dir(job) / f"{formats.safe_name(job.title)}.zip"
        entries = [(item.path, formats.numbered(i, len(items), item.title, job.format))
                   for i, item in enumerate(items, 1)]
        self._asm.zip_files(entries, out, job.cancel)
        self._ready(job, out, note)

    def _deliver_concat(self, job: Job, items: list[Item], medias: list) -> None:
        try:
            self._guard.reserve(job.id, sum(item.path.stat().st_size for item in items))
        except JobError:
            # Pas la place pour un fichier unique en plus des morceaux : le ZIP efface
            # chaque morceau dès qu'il est archivé, il tient toujours.
            return self._deliver_zip(job, items, NO_SPACE_NOTE)
        out = self._dir(job) / f"{formats.safe_name(job.title)}.{job.format}"
        self._asm.concat_copy([i.path for i in items], [i.title for i in items],
                              [m.duration for m in medias], out, job.cancel)
        for item in items:
            item.path.unlink()
        self._ready(job, out)

    def _reencode(self, job: Job) -> None:
        pending = job.pending
        height = self._asm.target_height([media for _, media in pending], job.height)
        items, medias = [], []
        for index, (item, media) in enumerate(pending, 1):
            if job.cancel.is_set():
                raise Cancelled()
            self._update(job, progress={"percent": None, "item": index, "items": len(pending)})
            out = item.path.with_name(item.path.stem + ".norm.mp4")
            self._asm.normalize(item.path, media, height, out, job.cancel)
            item.path.unlink()
            items.append(Item(out, item.title, item.duration))
            medias.append(self._asm.probe(out))
        self._update(job, progress={})
        self._deliver_concat(job, items, medias)

    def _ready(self, job: Job, out: Path, note: str | None = None) -> None:
        shutil.rmtree(self._dir(job) / "dl", ignore_errors=True)
        file = {"name": out.name, "size": out.stat().st_size, "url": f"/files/{job.id}/{quote(out.name)}"}
        self._finish(job, "ready", file=file, note=note, pending=[])
