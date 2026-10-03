"""Erreurs de tâche et classement des messages yt-dlp / ffmpeg (spec §10)."""
from .config import GB

MESSAGES = {
    "cookies": "Cookies expirés : à renouveler",
    "unavailable": "Vidéo indisponible",
    "unsupported": "Lien non pris en charge",
    "disk": "Espace disque insuffisant",
    "network": "Erreur réseau, réessaie",
    "live": "Les directs ne sont pas pris en charge",
    "other": "Échec du téléchargement",
}

# L'ordre compte : « Private video. Sign in if you've been granted access… » doit
# rester une vidéo indisponible, pas un problème de cookies.
_PATTERNS = (
    # Âge : le compte secondaire n'est pas vérifié, renouveler les cookies n'y changerait rien.
    ("unavailable", ("video unavailable", "private video", "in your country", "members-only", "has been removed",
                     "confirm your age", "age-restricted", "inappropriate for some users")),
    # « not a bot » plutôt que « sign in to confirm » (qui attrape aussi l'âge) ; sans
    # l'apostrophe, qui peut être typographique.
    ("cookies", ("not a bot", "cookies are no longer valid")),
    ("unsupported", ("unsupported url", "no suitable extractor")),
    ("live", ("live event will begin", "premieres in")),
    ("network", ("timed out", "connection refused", "connection reset", "name resolution", "http error 5", "network is unreachable")),
)


class Cancelled(Exception):
    """Tâche annulée par l'utilisateur."""


class JobError(Exception):
    def __init__(self, kind: str, message: str | None = None, details: str = ""):
        self.kind = kind
        self.message = message or MESSAGES[kind]
        self.details = details
        super().__init__(self.message)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "message": self.message, "details": self.details}


def last_line(text: str) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def classify(raw: str) -> JobError:
    low = raw.lower()
    kind = next((k for k, needles in _PATTERNS if any(n in low for n in needles)), "other")
    return JobError(kind, details=last_line(raw))


def disk_error(need_bytes: int = 0) -> JobError:
    if need_bytes:
        size = f"{need_bytes / GB:.1f}".replace(".", ",")
        return JobError("disk", f"Espace disque insuffisant ({size} Go nécessaires)")
    return JobError("disk")
