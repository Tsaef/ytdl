"""cookies.txt partagé entre les tâches (spec §11).

Chaque tâche travaille sur sa propre copie ; yt-dlp la met à jour (la session se
prolonge) et elle est recopiée à la fin, sauf si l'utilisateur a déposé un nouveau
fichier entre-temps : celui-ci gagne toujours.
"""
import os
import shutil
import threading
from pathlib import Path


class CookieStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._state = "unknown"
        self._state_version = None  # version du fichier au moment du dernier verdict

    def _version(self):
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return None
        return (st.st_mtime_ns, st.st_size)

    @property
    def state(self) -> str:
        with self._lock:
            version = self._version()
            if version is None:
                return "absent"
            if version != self._state_version:
                return "unknown"  # fichier remplacé depuis le dernier verdict
            return self._state

    def mark(self, state: str) -> None:
        """'ok' après un succès YouTube, 'expired' après une erreur de cookies."""
        with self._lock:
            self._state = state
            self._state_version = self._version()

    def checkout(self, dest: Path):
        """Copie cookies.txt vers dest ; renvoie sa version, ou None s'il est absent."""
        with self._lock:
            version = self._version()
            if version is None:
                return None
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.path, dest)
            os.chmod(dest, 0o600)
            return version

    def checkin(self, copy: Path, version) -> None:
        """Recopie la copie d'une tâche si l'original n'a pas changé ; efface la copie."""
        with self._lock:
            try:
                if version is not None and copy.exists() and self._version() == version:
                    tmp = self.path.with_name(self.path.name + ".tmp")
                    shutil.copyfile(copy, tmp)
                    os.chmod(tmp, 0o600)
                    os.replace(tmp, self.path)
                    if self._state_version == version:
                        self._state_version = self._version()
            finally:
                copy.unlink(missing_ok=True)
