"""Perfil de juego: todo lo que ModHub necesita saber de un juego concreto.

Un perfil responde a tres preguntas:
  1. ¿Es este el juego? (IDs de Steam/Epic/GOG/Xbox, ejecutable, nombre)
  2. ¿Qué modloader necesita? (BepInEx, SKSE, UE4SS, SMAPI…)
  3. ¿Dónde va cada archivo de un mod? (lista ordenada de reglas, ver rules.py)
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from ..sources.base import GameRef

if TYPE_CHECKING:
    from .rules import Rule

# Archivos que nunca se despliegan en el juego (se comparan con el nombre del archivo)
DEFAULT_IGNORE = ("readme*", "léeme*", "leeme*", "*.url", "license*", "licence*", "changelog*",
                  "*.md", "thumbs.db", ".ds_store", "desktop.ini", "*.lnk")
# Carpetas que se descartan enteras
DEFAULT_IGNORE_DIRS = ("__macosx", ".git")


def normalize_name(name: str) -> str:
    """'The Elder Scrolls V: Skyrim Special Edition™' -> 'theelderscrollsvskyrimspecialedition'"""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.lower())


@dataclass(frozen=True)
class ModLoader:
    """Un modloader o "script extender" que algunos juegos necesitan."""

    key: str
    name: str
    detect: tuple[str, ...]                 # rutas relativas a la raíz del juego; si existe una -> instalado
    sources: tuple[tuple[str, str], ...] = ()   # (fuente, mod_id) donde descargarlo automáticamente
    homepage: str = ""
    note: str = ""
    launcher: str = ""                      # .exe que hay que abrir EN VEZ del juego para que cargue los mods
    unsupported: tuple[str, ...] = ()       # versiones del juego donde NO existe (p. ej. SKSE en Game Pass)

    def is_installed(self, game_root: Path) -> bool:
        from ..core.platform_paths import loader_paths      # Xbox / Game Pass: Binaries/WinGDK
        return any((game_root / p).exists() for p in loader_paths(game_root, self.detect))

    def provided_by(self, targets: Iterable[str]) -> bool:
        """¿Este mod ES el loader? (instala alguno de sus archivos de detección)"""
        wanted = {p.lower().replace("\\", "/") for p in self.detect}
        return any(t.lower() in wanted for t in targets)

    def source_for(self, source: str) -> str | None:
        return dict(self.sources).get(source)


@dataclass
class GameProfile:
    slug: str
    name: str
    # --- Identificación ---------------------------------------------------
    steam_ids: tuple[str, ...] = ()
    epic_ids: tuple[str, ...] = ()           # AppName de los manifiestos de Epic
    gog_ids: tuple[str, ...] = ()
    xbox_ids: tuple[str, ...] = ()           # nombre de carpeta en XboxGames / Identity Name
    exe_names: tuple[str, ...] = ()          # relativos a la raíz del juego
    aliases: tuple[str, ...] = ()            # otros nombres con los que aparece en las tiendas
    # --- Fuentes de mods --------------------------------------------------
    source_ids: dict[str, str] = field(default_factory=dict)   # -> GameRef.ids
    # --- Modloaders -------------------------------------------------------
    required_loaders: tuple[ModLoader, ...] = ()
    optional_loaders: tuple[ModLoader, ...] = ()
    # --- Estructura -------------------------------------------------------
    rules: tuple["Rule", ...] = ()           # se prueban en orden; gana la primera que encaja
    fallback_target: str = ""                # destino si ninguna regla encaja (confianza baja)
    known_dirs: dict[str, str] = field(default_factory=dict)   # carpeta -> descripción (para la UI)
    ignore: tuple[str, ...] = DEFAULT_IGNORE
    ignore_dirs: tuple[str, ...] = DEFAULT_IGNORE_DIRS
    notes: str = ""

    def game_ref(self) -> GameRef:
        return GameRef(name=self.name, ids=dict(self.source_ids), slug=self.slug)

    def missing_loaders(self, game_root: Path) -> list[ModLoader]:
        return [l for l in self.required_loaders if not l.is_installed(game_root)]

    def match_score(self, store: str, store_id: str, name: str, install_path: Path | None) -> int:
        """0 = no es este juego; ≥100 coincidencia exacta de tienda; 60 ejecutable; 50 nombre."""
        ids = {"steam": self.steam_ids, "epic": self.epic_ids, "gog": self.gog_ids, "xbox": self.xbox_ids}
        if store_id and str(store_id) in ids.get(store, ()):
            return 100
        if install_path and self.exe_names and any((install_path / e).is_file() for e in self.exe_names):
            return 60
        names = {normalize_name(n) for n in (self.name, *self.aliases)}
        if name and normalize_name(name) in names:
            return 50
        return 0
