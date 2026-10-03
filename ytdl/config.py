"""Réglages lus dans l'environnement (spec §9, §12)."""
import os
from dataclasses import dataclass
from pathlib import Path

GB = 1024**3


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    workers: int = 2
    disk_floor: int = 10 * GB
    ttl: float = 6 * 3600.0
    reencode_factor: float = 1.0
    port: int = 8000

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def cookie_copies_dir(self) -> Path:
        # Hors de jobs/ : Caddy sert jobs/, une copie des cookies ne doit jamais y passer.
        return self.data_dir / "cookie-copies"

    @property
    def cookies_file(self) -> Path:
        return self.data_dir / "cookies.txt"


def from_env(env=os.environ) -> Settings:
    return Settings(
        data_dir=Path(env.get("YTDL_DATA", "/data")),
        workers=int(env.get("YTDL_WORKERS", "2")),
        disk_floor=int(float(env.get("YTDL_DISK_FLOOR", "10")) * GB),
        ttl=float(env.get("YTDL_TTL", "6")) * 3600,
        reencode_factor=float(env.get("YTDL_REENCODE_FACTOR", "1.0")),
        port=int(env.get("YTDL_PORT", "8000")),
    )
