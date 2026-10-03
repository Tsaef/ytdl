"""Options yt-dlp et lecture des infos qu'il renvoie (spec §6, §7). N'importe pas yt-dlp."""
import re
from pathlib import Path

MAX_ITEMS = 200
MP3_BITRATE = 192_000

_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def base_options(cookiefile: Path | None) -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        # watch?v=…&list=… donne la vidéo ; playlist?list=… donne la playlist.
        "noplaylist": True,
        # Une playlist est lue en surface (titres, liens) : chaque vidéo est extraite à part.
        "extract_flat": "in_playlist",
        "playlistend": MAX_ITEMS,
        # Sans l'extracteur générique, yt-dlp refuse les URL arbitraires (spec §13).
        "allowed_extractors": ["default", "-generic"],
    }
    if cookiefile is not None:
        options["cookiefile"] = str(cookiefile)
    return options


def video_selector(height: int | None) -> str:
    cap = f"[height<={height}]" if height else ""
    return f"bv*{cap}[vcodec^=avc1]+ba[acodec^=mp4a]/bv*{cap}+ba/b{cap}"


def format_options(fmt: str, height: int | None) -> dict:
    if fmt == "mp4":
        return {
            "format": video_selector(height),
            "merge_output_format": "mp4",
            # Sans effet sur un MP4 ; garantit le conteneur si yt-dlp retombe sur « b ».
            "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
        }
    if fmt == "mp3":
        # Piste audio brute : assemble.to_mp3 convertit avec des réglages identiques
        # pour toutes les pistes, ce qui permet l'assemblage par copie.
        return {"format": "ba/b"}
    raise ValueError(f"format inconnu : {fmt}")


def _size(fmt: dict) -> int | None:
    return fmt.get("filesize") or fmt.get("filesize_approx")


def estimate_size(info: dict) -> int | None:
    """Taille du format retenu par yt-dlp ; None si une partie est inconnue."""
    parts = info.get("requested_formats")
    if parts:
        sizes = [_size(p) for p in parts]
        return sum(sizes) if all(sizes) else None
    return _size(info)


def is_live(info: dict) -> bool:
    return bool(info.get("is_live")) or info.get("live_status") in ("is_live", "is_upcoming")


def is_playlist(info: dict) -> bool:
    return info.get("_type") == "playlist"


def playlist_entries(info: dict) -> list[dict]:
    entries = []
    for entry in info.get("entries") or []:
        if not entry:
            continue
        url = entry.get("url") or entry.get("webpage_url")
        if url:
            entries.append({"url": url, "title": entry.get("title") or url, "duration": entry.get("duration")})
    return entries[:MAX_ITEMS]


def _is_video(fmt: dict) -> bool:
    return fmt.get("vcodec") not in (None, "none") and bool(fmt.get("height"))


def best_audio(formats: list[dict]) -> dict | None:
    audios = [f for f in formats if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    if not audios:
        return None
    return max(audios, key=lambda f: (str(f.get("acodec", "")).startswith("mp4a"), f.get("abr") or 0))


def height_options(formats: list[dict]) -> list[dict]:
    """Hauteurs disponibles, de la plus haute à la plus basse, avec leur taille estimée."""
    videos = [f for f in formats if _is_video(f)]
    audio = best_audio(formats)
    options = []
    for height in sorted({f["height"] for f in videos}, reverse=True):
        at_height = [f for f in videos if f["height"] == height]
        h264 = [f for f in at_height if str(f.get("vcodec", "")).startswith("avc1")]
        pick = max(h264 or at_height, key=lambda f: f.get("tbr") or 0)
        size = _size(pick)
        if size and pick.get("acodec") in (None, "none") and audio:
            audio_size = _size(audio)
            size = size + audio_size if audio_size else None
        options.append({"height": height, "size": size})
    return options


def mp3_size(duration: float | None) -> int | None:
    return int(duration * MP3_BITRATE / 8) if duration else None


def summarize(info: dict) -> dict:
    """Réponse de /api/analyze (spec §6)."""
    if is_playlist(info):
        entries = playlist_entries(info)
        return {
            "kind": "playlist",
            "title": info.get("title") or "Playlist",
            "count": len(entries),
            "entries": [{"title": e["title"], "duration": e["duration"]} for e in entries],
        }
    return {
        "kind": "video",
        "title": info.get("title") or "Vidéo",
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "heights": height_options(info.get("formats") or []),
        "mp3_size": mp3_size(info.get("duration")),
    }


def safe_name(title: str | None, limit: int = 180) -> str:
    """Nom de fichier valide partout (Windows compris), limité en octets (ext4 : 255)."""
    name = re.sub(r"\s+", " ", _FORBIDDEN.sub("_", title or "")).strip().strip(".")
    while len(name.encode()) > limit:
        name = name[:-1]
    return name.rstrip(" .") or "sans titre"


def numbered(index: int, count: int, title: str, ext: str) -> str:
    width = max(2, len(str(count)))
    return f"{index:0{width}d} - {safe_name(title)}.{ext}"
