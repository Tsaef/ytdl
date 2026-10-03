"""Pont vers yt-dlp : analyse, résolution vidéo / playlist, téléchargement (spec §7)."""
import threading
from dataclasses import dataclass
from pathlib import Path

from . import formats
from .disk import DiskGuard
from .errors import Cancelled, JobError, classify


@dataclass
class Item:
    """Un fichier téléchargé."""
    path: Path
    title: str
    duration: float | None


@dataclass
class Playlist:
    title: str
    entries: list[dict]  # [{"url", "title", "duration"}], 200 au plus


def _yt_dlp_class():
    import yt_dlp  # import tardif : les tests n'en ont pas besoin

    return yt_dlp.YoutubeDL


class Downloader:
    def __init__(self, guard: DiskGuard, ydl_class=None):
        self._guard = guard
        self._ydl_class = ydl_class

    def _ydl(self, cookiefile: Path | None, extra: dict | None = None):
        return (self._ydl_class or _yt_dlp_class())(formats.base_options(cookiefile) | (extra or {}))

    def _extract(self, url: str, cookiefile: Path | None, extra: dict | None = None) -> dict:
        try:
            with self._ydl(cookiefile, extra) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            raise classify(str(e)) from e
        if formats.is_live(info):
            raise JobError("live")
        return info

    def analyze(self, url: str, cookiefile: Path | None) -> dict:
        return formats.summarize(self._extract(url, cookiefile))

    def resolve(self, url: str, fmt: str, height: int | None, cookiefile: Path | None) -> Playlist | dict:
        info = self._extract(url, cookiefile, formats.format_options(fmt, height))
        if formats.is_playlist(info):
            return Playlist(info.get("title") or "Playlist", formats.playlist_entries(info))
        return info

    def fetch(self, target, fmt: str, height: int | None, dest_stem: Path, cookiefile: Path | None,
              key: str, on_progress, cancel: threading.Event) -> Item:
        """Télécharge une vidéo seule (URL, ou infos venant de resolve) vers dest_stem.<ext>."""
        if isinstance(target, dict):
            info = target
        else:
            info = self._extract(target, cookiefile, formats.format_options(fmt, height))
        estimate = formats.estimate_size(info)
        self._guard.reserve(key, 2 * estimate if estimate else 0)
        aborted: list[Exception] = []
        finished: dict = {}  # octets des parties terminées (vidéo, puis audio)

        def hook(d: dict) -> None:
            try:
                if cancel.is_set():
                    raise Cancelled()
                self._guard.check()
            except (Cancelled, JobError) as e:
                aborted.append(e)  # yt-dlp emballe l'exception : on garde l'originale
                raise
            if d.get("status") == "finished":
                finished[d.get("filename")] = d.get("total_bytes") or d.get("downloaded_bytes") or 0
            elif d.get("status") == "downloading":
                if estimate:
                    done, total = sum(finished.values()) + (d.get("downloaded_bytes") or 0), estimate
                else:
                    done, total = d.get("downloaded_bytes") or 0, d.get("total_bytes") or d.get("total_bytes_estimate")
                on_progress(min(100.0, 100.0 * done / total) if total else None, d.get("speed"), d.get("eta"))

        options = formats.format_options(fmt, height) | {
            "outtmpl": str(dest_stem) + ".%(ext)s",
            "progress_hooks": [hook],
        }
        dest_stem.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._ydl(cookiefile, options) as ydl:
                result = ydl.process_ie_result(info, download=True)
        except Exception as e:
            if aborted:
                raise aborted[0] from e
            raise classify(str(e)) from e
        finally:
            self._guard.release(key)
        return Item(_output(result, dest_stem), info.get("title") or dest_stem.name, info.get("duration"))


def _output(result: dict, dest_stem: Path) -> Path:
    """Fichier final : celui que yt-dlp déclare, sinon le plus gros dest_stem.*."""
    for download in result.get("requested_downloads") or []:
        path = download.get("filepath")
        if path and Path(path).exists():
            return Path(path)
    found = [p for p in dest_stem.parent.glob(dest_stem.name + ".*") if p.suffix not in (".part", ".ytdl")]
    if not found:
        raise JobError("other", details="fichier téléchargé introuvable")
    return max(found, key=lambda p: p.stat().st_size)
