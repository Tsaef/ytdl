"""Doublures de test : aucune ne touche au réseau."""
import time
from pathlib import Path

from ytdl import assemble
from ytdl.downloader import Item, Playlist
from ytdl.errors import Cancelled


class FakeYDL:
    """Imite la partie de yt_dlp.YoutubeDL utilisée par Downloader."""

    infos: dict = {}       # url -> dict d'infos, ou exception à lever
    progress: list = []    # dicts passés aux progress_hooks pendant le téléchargement
    instances: list = []
    processed = 0

    def __init__(self, opts):
        self.opts = opts
        FakeYDL.instances.append(self)

    @classmethod
    def reset(cls):
        cls.infos, cls.progress, cls.instances, cls.processed = {}, [], [], 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        assert download is False
        result = FakeYDL.infos[url]
        if isinstance(result, Exception):
            raise result
        return dict(result)

    def process_ie_result(self, info, download=True):
        FakeYDL.processed += 1
        try:
            for d in FakeYDL.progress:
                for hook in self.opts.get("progress_hooks", []):
                    hook(d)
        except Exception as e:
            # yt-dlp emballe les exceptions des hooks dans une DownloadError.
            raise RuntimeError(f"ERROR: interrupted: {e!r}") from e
        path = Path(self.opts["outtmpl"].replace("%(ext)s", "mp4"))
        path.write_bytes(b"video")
        return {**info, "requested_downloads": [{"filepath": str(path)}]}


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition non atteinte à temps")


def demo_playlist(n=3, title="Ma playlist"):
    return Playlist(title, [{"url": f"https://www.youtube.com/watch?v=v{i}", "title": f"Titre {i}", "duration": 60}
                            for i in range(1, n + 1)])


class FakeDownloader:
    """Remplace Downloader : réponses scriptées, fichiers factices, aucun réseau."""

    def __init__(self, playlists=None, errors=None, delay=0.0, gate=None, crash=()):
        self.playlists = playlists or {}  # url -> Playlist
        self.errors = errors or {}        # url -> JobError levée à l'analyse ou au téléchargement
        self.delay = delay                # secondes entre deux étapes de progression
        self.gate = gate                  # threading.Event : fetch attend qu'il soit levé
        self.crash = set(crash)           # url qui lèvent une exception imprévue
        self.titles = {e["url"]: e["title"] for pl in self.playlists.values() for e in pl.entries}

    def analyze(self, url, cookiefile):
        if url in self.errors:
            raise self.errors[url]
        if url in self.playlists:
            pl = self.playlists[url]
            return {"kind": "playlist", "title": pl.title, "count": len(pl.entries),
                    "entries": [{"title": e["title"], "duration": 60} for e in pl.entries]}
        return {"kind": "video", "title": "Vidéo de démo", "duration": 754, "thumbnail": None,
                "heights": [{"height": 1080, "size": 300 * 2**20}, {"height": 720, "size": 150 * 2**20}],
                "mp3_size": 18 * 2**20}

    def resolve(self, url, fmt, height, cookiefile):
        if url in self.crash:
            raise RuntimeError("panne imprévue")
        if url in self.playlists:
            return self.playlists[url]
        if url in self.errors:
            raise self.errors[url]
        return {"title": self.titles.get(url, f"Vidéo {url.rsplit('/', 1)[-1]}"), "webpage_url": url, "duration": 60.0}

    def fetch(self, target, fmt, height, dest_stem, cookiefile, key, on_progress, cancel):
        url = target if isinstance(target, str) else target["webpage_url"]
        if url in self.errors:
            raise self.errors[url]
        if self.gate is not None:
            while not self.gate.wait(0.02):
                if cancel.is_set():
                    raise Cancelled()
        for percent in (25.0, 50.0, 75.0, 100.0):
            if cancel.is_set():
                raise Cancelled()
            on_progress(percent, 2_000_000.0, 3)
            time.sleep(self.delay)
        dest_stem.parent.mkdir(parents=True, exist_ok=True)
        path = dest_stem.with_suffix(".mp4" if fmt == "mp4" else ".m4a")
        path.write_bytes(f"contenu de {url}".encode())
        title = target["title"] if isinstance(target, dict) else self.titles.get(url, url)
        return Item(path, title, 60.0)


class FakeAssembler:
    """Remplace le module assemble sans ffmpeg ; les parties pures restent les vraies."""

    zip_files = staticmethod(assemble.zip_files)
    compatible = staticmethod(assemble.compatible)
    target_height = staticmethod(assemble.target_height)
    reencode_estimate = staticmethod(assemble.reencode_estimate)

    def __init__(self, compatible=True):
        self.same = compatible  # False : seules les vidéos « 001… » sont en 1080p
        self.calls = []

    def probe(self, path):
        first = self.same or path.name.startswith("001")
        video = ("h264", 1920, 1080, "30/1") if first else ("vp9", 1280, 720, "25/1")
        return assemble.Media(60.0, video, ("aac", "44100", 2))

    def to_mp3(self, src, out, cancel=None):
        self.calls.append("to_mp3")
        out.write_bytes(src.read_bytes())

    def concat_copy(self, files, titles, durations, out, cancel=None):
        self.calls.append("concat_copy")
        out.write_bytes(b"".join(f.read_bytes() for f in files))

    def normalize(self, src, media, height, out, cancel=None):
        self.calls.append(("normalize", height))
        out.write_bytes(src.read_bytes())
