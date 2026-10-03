"""Assemblages (spec §8) : ZIP, chapitres, et ffmpeg / ffprobe en sous-processus."""
import json
import re
import subprocess
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .errors import Cancelled, JobError, last_line


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


FFMPEG = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
THREADS = ["-threads", "2"]  # avec cpus: 2 dans compose, laisse des cœurs à Minecraft


def run(args: list[str], cancel: threading.Event | None = None) -> None:
    """Lance ffmpeg ; Cancelled si cancel est levé pendant l'exécution, JobError s'il échoue."""
    with tempfile.TemporaryFile() as stderr:
        proc = subprocess.Popen(FFMPEG + args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr)
        while True:
            try:
                code = proc.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel.is_set():
                    proc.kill()
                    proc.wait()
                    raise Cancelled() from None
        if code != 0:
            stderr.seek(0)
            raise JobError("other", "Échec de la conversion", last_line(stderr.read().decode(errors="replace")))


def probe(path: Path) -> Media:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise JobError("other", "Échec de la conversion", last_line(proc.stderr))
    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return Media(
        duration=float(data["format"]["duration"]),
        video=(v["codec_name"], v["width"], v["height"], v["r_frame_rate"]) if v else None,
        audio=(a["codec_name"], a["sample_rate"], a["channels"]) if a else None,
    )


def to_mp3(src: Path, out: Path, cancel: threading.Event | None = None) -> None:
    """MP3 CBR 192 kb/s, 44,1 kHz, stéréo : identique pour toutes les pistes (spec §7)."""
    run(["-i", str(src), "-vn", "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", "-ac", "2",
         *THREADS, str(out)], cancel)


def _concat_list(files: list[Path], path: Path) -> None:
    # Démultiplexeur concat : chemins absolus entre apostrophes, une apostrophe s'écrit '\''.
    path.write_text("".join("file '" + str(f.resolve()).replace("'", "'\\''") + "'\n" for f in files))


def concat_copy(files: list[Path], titles: list[str], durations: list[float], out: Path,
                cancel: threading.Event | None = None) -> None:
    """Met les fichiers bout à bout sans ré-encoder, avec un chapitre par fichier."""
    listing = out.with_name(out.name + ".concat.txt")
    chapters = out.with_name(out.name + ".chapters.txt")
    _concat_list(files, listing)
    chapters.write_text(ffmetadata(titles, durations))
    try:
        run(["-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(chapters),
             "-map", "0", "-map_metadata", "1", "-map_chapters", "1", "-c", "copy", str(out)], cancel)
    finally:
        listing.unlink(missing_ok=True)
        chapters.unlink(missing_ok=True)


def normalize(src: Path, media: Media, height: int, out: Path, cancel: threading.Event | None = None) -> None:
    """Ré-encode vers le format commun (spec §8) : H.264 + AAC, cadre 16:9, 30 i/s."""
    width = frame_width(height)
    video_filter = (f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p")
    args = ["-i", str(src)]
    if media.audio is None:
        # Sans piste audio, l'assemblage par copie échouerait : on ajoute un silence.
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    else:
        args += ["-map", "0:v:0", "-map", "0:a:0"]
    args += ["-vf", video_filter, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", *THREADS, str(out)]
    run(args, cancel)
