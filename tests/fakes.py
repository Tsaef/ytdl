"""Doublures de test : aucune ne touche au réseau."""
from pathlib import Path


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
