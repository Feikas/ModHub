"""Reglas de mapeo: deciden a qué carpeta del juego va cada archivo de un mod.

Cada regla recibe la lista de archivos del mod (rutas relativas, con '/') y devuelve
un RuleMatch con las parejas (origen -> destino) o None si no reconoce el mod.
Los destinos admiten plantillas:
    {mod} -> nombre seguro del mod      {dir} -> nombre de la carpeta que encajó
"""
from __future__ import annotations

import abc
from collections import defaultdict
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import PurePosixPath
from typing import Iterable

ROOT = PurePosixPath()

Mapping = tuple[PurePosixPath, PurePosixPath]     # (ruta en el mod, ruta en el juego)


@dataclass
class RuleContext:
    mod_name: str


@dataclass
class RuleMatch:
    rule: str
    mappings: list[Mapping]
    confidence: float
    notes: list[str] = field(default_factory=list)


# ─── utilidades ────────────────────────────────────────────────────────
def _target(template: str, ctx: RuleContext, dirname: str = "") -> PurePosixPath:
    text = template.format_map({"mod": ctx.mod_name, "dir": dirname or ctx.mod_name})
    return PurePosixPath(text) if text else ROOT


def _under(path: PurePosixPath, base: PurePosixPath) -> PurePosixPath | None:
    if base == ROOT:
        return path
    try:
        return path.relative_to(base)
    except ValueError:
        return None


def _children(files: Iterable[PurePosixPath]) -> dict[PurePosixPath, set[str]]:
    """{carpeta: {nombres en minúscula de lo que contiene}} para cada carpeta del mod (incluida la raíz)."""
    out: dict[PurePosixPath, set[str]] = defaultdict(set)
    for f in files:
        parts = f.parts
        for i, part in enumerate(parts):
            out[PurePosixPath(*parts[:i]) if i else ROOT].add(part.lower())
    return out


def _common_dir(paths: list[PurePosixPath]) -> PurePosixPath:
    parents = [p.parent.parts for p in paths]
    common: list[str] = []
    for level in zip(*parents):
        if all(x == level[0] for x in level):
            common.append(level[0])
        else:
            break
    return PurePosixPath(*common) if common else ROOT


def _map_dir(files, base, target: PurePosixPath) -> list[Mapping]:
    out = []
    for f in files:
        rel = _under(f, base)
        if rel is not None:
            out.append((f, target / rel if target != ROOT else rel))
    return out


# ─── reglas ────────────────────────────────────────────────────────────
class Rule(abc.ABC):
    name = "regla"

    @abc.abstractmethod
    def match(self, files: list[PurePosixPath], ctx: RuleContext) -> RuleMatch | None: ...


class RootMarkers(Rule):
    """Busca la carpeta MENOS profunda que contiene alguno de los `markers` y la copia en `target`.

    Ej.: markers={"bepinex", "winhttp.dll"}, target=""  -> "MiMod-1.0/BepInEx/plugins/x.dll"
         se instala como "BepInEx/plugins/x.dll" en la raíz del juego.
    Los marcadores admiten comodines ("*.esp").
    """

    def __init__(self, markers: Iterable[str], target: str = "", *, confidence: float = 0.95, name: str = ""):
        self.markers = tuple(m.lower() for m in markers)
        self.target = target
        self.confidence = confidence
        self.name = name or f"marcadores→{target or 'raíz'}"

    def match(self, files, ctx):
        children = _children(files)
        best_depth, candidates = None, []
        for d in sorted(children, key=lambda p: (len(p.parts), str(p))):
            if best_depth is not None and len(d.parts) > best_depth:
                break
            if any(fnmatch(c, m) for c in children[d] for m in self.markers):
                best_depth = len(d.parts)
                candidates.append(d)
        if not candidates:
            return None
        base = candidates[0]
        notes = []
        if len(candidates) > 1:
            notes.append("El archivo trae varias variantes (" + ", ".join(map(str, candidates)) +
                         f"); se ha elegido «{base}». Revísalo si no es la que querías.")
        return RuleMatch(self.name, _map_dir(files, base, _target(self.target, ctx)), self.confidence, notes)


class ThunderstorePackage(Rule):
    """Paquete con formato Thunderstore (manifest.json en la raíz), igual que lo instala r2modman."""

    name = "paquete-thunderstore"
    LAYOUT = {"plugins": "BepInEx/plugins/{mod}", "patchers": "BepInEx/patchers/{mod}",
              "monomod": "BepInEx/monomod/{mod}", "core": "BepInEx/core", "config": "BepInEx/config"}

    def match(self, files, ctx):
        top = {f.parts[0].lower() for f in files if len(f.parts) > 1}
        has_manifest = any(str(f).lower() == "manifest.json" for f in files)
        has_dll = any(len(f.parts) == 1 and f.suffix.lower() == ".dll" for f in files)
        if not has_manifest or not (top & set(self.LAYOUT) or has_dll):
            return None
        plugin_dir = _target(self.LAYOUT["plugins"], ctx)
        maps = []
        for f in files:
            first = f.parts[0].lower()
            if len(f.parts) > 1 and first in self.LAYOUT:
                maps.append((f, _target(self.LAYOUT[first], ctx) / PurePosixPath(*f.parts[1:])))
            else:   # dll sueltas, manifest, icono y carpetas desconocidas -> carpeta del plugin
                maps.append((f, plugin_dir / f))
        return RuleMatch(self.name, maps, 0.9)


class FilesByExtension(Rule):
    """Si hay archivos con estas extensiones, su carpeta común se instala en `target`."""

    def __init__(self, extensions: Iterable[str], target: str, *, confidence: float = 0.8,
                 name: str = "", only_matching: bool = False):
        self.exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
        self.target = target
        self.confidence = confidence
        self.only_matching = only_matching
        self.name = name or f"extensión→{target}"

    def match(self, files, ctx):
        hits = [f for f in files if f.suffix.lower() in self.exts]
        if not hits:
            return None
        base = _common_dir(hits)
        pool = hits if self.only_matching else files
        return RuleMatch(self.name, _map_dir(pool, base, _target(self.target, ctx)), self.confidence)


class FolderPerMarker(Rule):
    """Cada carpeta que contiene un archivo marcador es un mod independiente.

    UE4SS:  marker "scripts/main.lua"  -> target ".../ue4ss/Mods/{dir}"
    SMAPI:  marker "manifest.json"     -> target "Mods/{dir}"
    """

    def __init__(self, markers: Iterable[str], target: str, *, confidence: float = 0.9, name: str = ""):
        self.markers = tuple(PurePosixPath(m.lower()) for m in markers)
        self.target = target
        self.confidence = confidence
        self.name = name or f"carpeta-por-mod→{target}"

    def match(self, files, ctx):
        lower = {PurePosixPath(str(f).lower()): f for f in files}
        roots: list[PurePosixPath] = []
        for lf, f in lower.items():
            for m in self.markers:
                n = len(m.parts)
                if len(lf.parts) >= n and PurePosixPath(*lf.parts[-n:]) == m:
                    roots.append(PurePosixPath(*f.parts[:-n]) if len(f.parts) > n else ROOT)
        if not roots:
            return None
        # Solo las carpetas de más arriba (una carpeta dentro de otra ya elegida no cuenta)
        roots = sorted(set(roots), key=lambda p: len(p.parts))
        chosen: list[PurePosixPath] = []
        for r in roots:
            if not any(_under(r, c) is not None for c in chosen):
                chosen.append(r)
        maps: list[Mapping] = []
        for base in chosen:
            name = base.name if base != ROOT else ctx.mod_name
            maps += _map_dir(files, base, _target(self.target, ctx, name))
        return RuleMatch(self.name, maps, self.confidence)


class Fallback(Rule):
    """Último recurso: quita carpetas envoltorio ("MiMod-v1/MiMod/…") y lo copia todo en `target`."""

    name = "sin-reconocer"

    def __init__(self, target: str = "", confidence: float = 0.3):
        self.target = target
        self.confidence = confidence

    def match(self, files, ctx):
        if not files:
            return None
        base = ROOT
        while True:
            rels = [_under(f, base) for f in files]
            firsts = {r.parts[0] for r in rels if r is not None}
            if len(firsts) == 1 and all(len(r.parts) > 1 for r in rels if r is not None):
                base = base / firsts.pop()
            else:
                break
        return RuleMatch(self.name, _map_dir(files, base, _target(self.target, ctx)), self.confidence,
                         ["No se ha reconocido la estructura del mod: revisa dónde se instalará."])
