"""Carpetas de staging: dónde viven las descargas y los mods descomprimidos.

Regla de oro: el staging de un juego está en el MISMO disco que el juego.
Si no, los hardlinks son imposibles y habría que copiar (doble espacio).

    Juego en C:  ->  %APPDATA%\\ModHub\\staging\\<juego>\\…   (carpeta del usuario, siempre escribible)
    Juego en D:  ->  D:\\ModHubStaging\\<juego>\\…
    Se puede forzar otra carpeta por disco con `overrides`.

Estructura de cada juego:
    <staging>/<juego>/downloads/   archivos descargados (.zip, .7z…)
    <staging>/<juego>/mods/<mod>/  mods descomprimidos (origen de los hardlinks)
    <staging>/<juego>/backups/     archivos originales del juego que un mod sustituyó
"""
from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_FOLDER = "ModHubStaging"


def _existing(path: Path) -> Path:
    """La carpeta existente más cercana (para comparar discos antes de crear nada)."""
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def same_volume(a: Path | str, b: Path | str) -> bool:
    """¿Están en el mismo disco/volumen? (requisito de los hardlinks)"""
    try:
        return os.stat(_existing(Path(a))).st_dev == os.stat(_existing(Path(b))).st_dev
    except OSError:
        return False


def volume_root(path: Path | str) -> Path:
    """Raíz del volumen que contiene `path` (C:\\ en Windows; el punto de montaje en Linux)."""
    p = _existing(Path(path)).resolve()
    if os.name == "nt":
        return Path(p.anchor)
    dev = os.stat(p).st_dev
    while p != p.parent and os.stat(p.parent).st_dev == dev:
        p = p.parent
    return p


def safe_folder_name(name: str, max_len: int = 80) -> str:
    """Nombre válido como carpeta en Windows."""
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    clean = re.sub(r"\s+", " ", clean)
    if clean.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)),
                                        *(f"LPT{i}" for i in range(10))}:
        clean = f"_{clean}"
    return (clean or "mod")[:max_len].rstrip(" .")


class StagingManager:
    def __init__(self, user_data_dir: Path | str, *, overrides: dict[str, Path | str] | None = None,
                 folder_name: str = DEFAULT_FOLDER):
        """
        user_data_dir: %APPDATA%\\ModHub (se usa para los juegos que están en ese mismo disco).
        overrides:     {"D:\\\\": "D:\\\\Mods\\\\Staging"} carpeta elegida por el usuario para un disco.
        """
        self.user_data_dir = Path(user_data_dir)
        self.overrides = {self._vol_key(k): Path(v) for k, v in (overrides or {}).items()}
        self.folder_name = folder_name

    @staticmethod
    def _vol_key(p: Path | str) -> str:
        return str(p).lower().rstrip("\\/") or "/"

    def root_for(self, game_path: Path | str) -> Path:
        vol = volume_root(game_path)
        if (o := self.overrides.get(self._vol_key(vol))) is not None:
            return o
        if same_volume(self.user_data_dir, game_path):
            return self.user_data_dir / "staging"
        return vol / self.folder_name

    def game_dir(self, game_slug: str, game_path: Path | str) -> Path:
        return self.root_for(game_path) / safe_folder_name(game_slug)

    def downloads_dir(self, game_slug: str, game_path: Path | str) -> Path:
        return self._mk(self.game_dir(game_slug, game_path) / "downloads")

    def mods_dir(self, game_slug: str, game_path: Path | str) -> Path:
        return self._mk(self.game_dir(game_slug, game_path) / "mods")

    def backups_dir(self, game_slug: str, game_path: Path | str) -> Path:
        return self._mk(self.game_dir(game_slug, game_path) / "backups")

    def mod_dir(self, game_slug: str, game_path: Path | str, mod_name: str, version: str = "") -> Path:
        folder = safe_folder_name(f"{mod_name} {version}".strip())
        return self.mods_dir(game_slug, game_path) / folder

    @staticmethod
    def _mk(p: Path) -> Path:
        p.mkdir(parents=True, exist_ok=True)
        return p
