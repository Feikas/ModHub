"""Rutas que cambian según la versión del juego (Steam / Epic / GOG frente a Xbox · PC Game Pass).

Los juegos Unreal de la Xbox App / Game Pass (compilados con el GDK de Microsoft) guardan los binarios en
`<Proyecto>/Binaries/WinGDK` en vez de `<Proyecto>/Binaries/Win64`. Casi todos los mods (UE4SS, .dll, .asi…)
vienen preparados para Win64, así que al desplegarlos se lleva a WinGDK lo que iba a Win64.

La decisión se toma mirando la carpeta REAL del juego (no la tienda): si existe WinGDK y no Win64, se reasigna.
Así funciona también con copias portables y con juegos que la Xbox App instaló en otra unidad.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path, PurePosixPath

STEAM_BIN, GDK_BIN = "win64", "wingdk"


def _uses_gdk(root: Path, project_parts: tuple[str, ...]) -> str | None:
    """Nombre real (con mayúsculas) de la carpeta WinGDK si ese proyecto usa GDK; None si usa Win64 o no se sabe."""
    binaries = root.joinpath(*project_parts, "Binaries")
    if not binaries.is_dir():
        return None
    names = {p.name.lower(): p.name for p in binaries.iterdir() if p.is_dir()}
    if GDK_BIN not in names:
        return None
    if STEAM_BIN not in names:
        return names[GDK_BIN]

    def has_exe(name: str) -> bool:
        return any(f.suffix.lower() == ".exe" for f in (binaries / names[name]).iterdir() if f.is_file())
    # Hay las dos (p. ej. un gestor antiguo creó Win64 a mano): manda la que tiene el ejecutable del juego
    return names[GDK_BIN] if has_exe(GDK_BIN) and not has_exe(STEAM_BIN) else None


class PathAdapter:
    """Traduce rutas de mod (pensadas para Steam) a la estructura real de ESTE juego."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._cache: dict[tuple[str, ...], str | None] = {}

    def _gdk_dir(self, project_parts: tuple[str, ...]) -> str | None:
        key = tuple(p.lower() for p in project_parts)
        if key not in self._cache:
            self._cache[key] = _uses_gdk(self.root, project_parts)
        return self._cache[key]

    def adapt(self, rel_path: str) -> str:
        parts = list(PurePosixPath(rel_path).parts)
        for i in range(len(parts) - 1):
            if parts[i].lower() == "binaries" and parts[i + 1].lower() == STEAM_BIN:
                gdk = self._gdk_dir(tuple(parts[:i]))
                if gdk:
                    parts[i + 1] = gdk
                break
        return str(PurePosixPath(*parts)) if parts else rel_path

    @property
    def is_gdk(self) -> bool:
        """¿Algún proyecto Unreal de primer nivel usa WinGDK? (para mostrarlo en la interfaz)"""
        try:
            return any(_uses_gdk(self.root, (d.name,)) for d in self.root.iterdir() if d.is_dir())
        except OSError:
            return False


def adapt_paths(root: Path, rel_paths: list[str]) -> list[str]:
    adapter = PathAdapter(root)
    return [adapter.adapt(p) for p in rel_paths]


def loader_paths(root: Path, detect: tuple[str, ...]) -> tuple[str, ...]:
    """Rutas de detección de un modloader, más su variante WinGDK si el juego la usa."""
    adapter = PathAdapter(root)
    return tuple(dict.fromkeys([*detect, *(adapter.adapt(p) for p in detect)]))
