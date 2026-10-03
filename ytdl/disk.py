"""Marge d'espace disque toujours laissée au reste du VPS (spec §9)."""
import shutil
import threading
import time
from pathlib import Path

from .errors import disk_error


class DiskGuard:
    def __init__(self, path: Path, floor: int, usage=shutil.disk_usage, clock=time.monotonic):
        self._path = path
        self._floor = floor
        self._usage = usage
        self._clock = clock
        self._lock = threading.Lock()
        self._reserved: dict[str, int] = {}
        self._last_check = float("-inf")

    def free(self) -> int:
        return self._usage(self._path).free

    def reserve(self, key: str, need: int) -> None:
        """Réserve need octets pour key (remplace sa réservation) ou lève JobError('disk')."""
        with self._lock:
            others = sum(v for k, v in self._reserved.items() if k != key)
            if self.free() - others - need < self._floor:
                raise disk_error(need)
            self._reserved[key] = need

    def release(self, key: str) -> None:
        with self._lock:
            self._reserved.pop(key, None)

    def check(self) -> None:
        """Surveillance pendant un téléchargement : au plus une mesure par seconde."""
        now = self._clock()
        if now - self._last_check < 1:
            return
        self._last_check = now
        if self.free() < self._floor:
            raise disk_error()
