"""Assemblages (spec §8) : ZIP, chapitres, et ffmpeg / ffprobe en sous-processus."""
import re
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .errors import Cancelled


@dataclass(frozen=True)
class Media:
    duration: float
    video: tuple | None  # (codec, largeur, hauteur, images/s) ; None pour un fichier audio
    audio: tuple | None  # (codec, fréquence, canaux) ; None sans piste audio

    @property
    def height(self) -> int | None:
        return self.video[2] if self.video else None


def compatible(medias: list[Media]) -> bool:
    """Assemblage par copie possible : mêmes paramètres vidéo et audio partout."""
    first = medias[0]
    return all(m.video == first.video and m.audio == first.audio for m in medias)


def target_height(medias: list[Media], cap: int | None) -> int:
    """Hauteur du ré-encodage : min(plafond choisi, plus grande hauteur, 1080)."""
    tallest = max((m.height or 0) for m in medias) or 1080
    return min(cap or 1080, tallest, 1080)


def frame_width(height: int) -> int:
    """Largeur paire d'un cadre 16:9 (libx264 exige des dimensions paires)."""
    return (height * 16 // 9 + 1) // 2 * 2


def reencode_estimate(medias: list[Media], height: int, factor: float) -> int:
    """Secondes de calcul : durée totale × facteur (donné pour 1080p), au prorata des pixels."""
    total = sum(m.duration for m in medias)
    return round(total * factor * (height / 1080) ** 2)


def _escape(text: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", text)


def ffmetadata(titles: list[str], durations: list[float]) -> str:
    """Fichier de chapitres ffmpeg : un chapitre par morceau, bout à bout."""
    lines = [";FFMETADATA1"]
    end = 0.0
    for title, duration in zip(titles, durations):
        start, end = end, end + duration
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={round(start * 1000)}",
                  f"END={round(end * 1000)}", f"title={_escape(title)}"]
    return "\n".join(lines) + "\n"


def zip_files(entries: list[tuple[Path, str]], out: Path, cancel: threading.Event | None = None) -> None:
    """ZIP sans compression (médias déjà compressés) ; chaque fichier est effacé une fois archivé."""
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for path, arcname in entries:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            archive.write(path, arcname)
            path.unlink()
