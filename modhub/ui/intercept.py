"""Vigila la carpeta Descargas para recoger los mods que se bajan desde el navegador.

Cuando un mod es BROWSER_INTERCEPT (CurseForge restringido, Mod DB, AyakaMods…), ModHub abre la
web y apunta una "espera" (PendingIntercept). Si en los minutos siguientes aparece en Descargas un
archivo comprimido que encaja, se avisa al usuario con un botón para instalarlo.

Los navegadores escriben primero en .crdownload / .part / .tmp y renombran al acabar; además se
espera a que el tamaño deje de cambiar antes de dar el archivo por terminado.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

ARCHIVE_EXTS = {".zip", ".7z", ".rar", ".tar", ".gz", ".jar", ".pak", ".dll", ".esp", ".esm", ".esl", ".archive"}
PARTIAL_EXTS = {".crdownload", ".part", ".tmp", ".download", ".partial", ".opdownload"}
PENDING_TTL = 30 * 60          # 30 minutos


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


@dataclass
class PendingIntercept:
    game_id: int
    mod_name: str
    source: str
    mod_id: str
    file_id: str
    expected_filename: str | None = None
    opened_at: float = field(default_factory=time.time)

    def matches(self, path: Path) -> int:
        """Puntuación de coincidencia: 100 nombre exacto, 60 contiene el nombre del mod, 0 nada."""
        if self.expected_filename and path.name.lower() == self.expected_filename.lower():
            return 100
        name = _norm(self.mod_name)
        if name and len(name) >= 4 and name in _norm(path.stem):
            return 60
        return 0


class DownloadsWatcher(QObject):
    file_ready = Signal(object, object)          # (Path, PendingIntercept | None)

    def __init__(self, folder: Path | str, parent=None, *, stable_checks: int = 2, poll_ms: int = 1000):
        super().__init__(parent)
        self.folder = Path(folder)
        self.pending: list[PendingIntercept] = []
        self._known: set[str] = set()
        self._candidates: dict[str, tuple[int, int]] = {}      # ruta -> (último tamaño, comprobaciones estables)
        self.stable_checks = stable_checks
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._scan)
        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self._tick)

    def start(self) -> bool:
        if not self.folder.is_dir():
            return False
        self._known = {str(p) for p in self.folder.iterdir()}
        self._watcher.addPath(str(self.folder))
        self._timer.start()
        return True

    def stop(self) -> None:
        self._timer.stop()
        if self._watcher.directories():
            self._watcher.removePaths(self._watcher.directories())

    def expect(self, pending: PendingIntercept) -> None:
        self.pending.append(pending)

    def _scan(self, *_):
        try:
            entries = list(self.folder.iterdir())
        except OSError:
            return
        for p in entries:
            key = str(p)
            if key in self._known or not p.is_file():
                continue
            if p.suffix.lower() in PARTIAL_EXTS:
                continue                                 # aún descargando; cuando se renombre lo veremos
            if p.suffix.lower() not in ARCHIVE_EXTS:
                self._known.add(key)
                continue
            self._candidates.setdefault(key, (-1, 0))

    def _tick(self):
        self._scan()
        now = time.time()
        self.pending = [p for p in self.pending if now - p.opened_at < PENDING_TTL]
        for key, (last, stable) in list(self._candidates.items()):
            p = Path(key)
            try:
                size = p.stat().st_size
            except OSError:
                self._candidates.pop(key, None)
                continue
            stable = stable + 1 if size == last and size > 0 else 0
            if stable >= self.stable_checks:
                self._candidates.pop(key)
                self._known.add(key)
                self.file_ready.emit(p, self._match(p))
            else:
                self._candidates[key] = (size, stable)

    def _match(self, path: Path) -> PendingIntercept | None:
        scored = sorted(((p.matches(path), p) for p in self.pending), key=lambda t: -t[0])
        if scored and scored[0][0] > 0:
            best = scored[0][1]
        elif len(self.pending) == 1:
            best = self.pending[0]                       # una sola espera reciente: muy probablemente es esta
        else:
            return None
        self.pending.remove(best)
        return best
