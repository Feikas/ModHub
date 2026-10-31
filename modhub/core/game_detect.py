"""Detección automática de juegos instalados (Steam, Epic, Xbox/Game Pass, GOG y carpetas propias).

Todo lo que toca el sistema (registro de Windows, variables de entorno, letras de
unidad) se inyecta, así que el módulo se puede probar en cualquier PC con datos falsos.

    detector = GameDetector()
    for game in detector.detect_all():
        print(game.store, game.name, game.install_path, game.profile_slug)
"""
from __future__ import annotations

import json
import logging
import os
import re
import string
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from ..games import find_profile, normalize_name

log = logging.getLogger("modhub.detect")

# Apps de Steam que no son juegos
STEAM_IGNORED_APPIDS = {"228980", "1070560", "1391110", "1628350", "1493710", "2180100", "250820", "1826330"}


@dataclass
class DetectedGame:
    store: str                      # steam | epic | xbox | gog | custom
    store_id: str
    name: str
    install_path: Path
    exe_path: Path | None = None
    profile_slug: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.store}:{self.store_id}"


# ════════════════════════════════════════════════════════════════════════
#  Utilidades para carpetas de juegos
# ════════════════════════════════════════════════════════════════════════
# .exe que NO son el juego
_NOT_GAME_EXE = re.compile(
    r"(unins|uninstall|setup|install|redist|vcredist|vc_redist|dxsetup|dotnet|crash|report|launcherhelper|"
    r"unitycrashhandler|ue4prereq|ueprereq|easyanticheat|eac|battleye|be_service|cefsharp|helper|updater|"
    r"7z|winrar|notification_helper|quicksfv|dxwebsetup|oalinst|physx)", re.I)
# Restos típicos en nombres de carpeta de juegos descargados/portables
_NAME_JUNK = re.compile(
    r"(\b(v|build)\s*\d[\w.]*|\b(repack|portable|multi\d*|gog|steamrip|elamigos|fitgirl|dodi|codex|plaza|"
    r"skidrow|razor1911|flt|tenoke|rune|empress|insaneramzes|0xdeadcode)\b.*$|\[.*?\]|\(.*?\))", re.I)


def has_game_files(path: Path) -> bool:
    """La carpeta existe y no está vacía (un juego desinstalado suele dejar la carpeta vacía o sin ella)."""
    try:
        return path.is_dir() and any(path.iterdir())
    except OSError:
        return False


def _exe_candidates(folder: Path, depth: int = 3) -> list[Path]:
    out: list[Path] = []
    try:
        for p in folder.iterdir():
            if p.is_file() and p.suffix.lower() == ".exe" and not _NOT_GAME_EXE.search(p.stem):
                out.append(p)
            elif p.is_dir() and depth > 1 and not p.name.lower().startswith(("_commonredist", "redist", "directx",
                                                                           "support", "__installer", "engine")):
                out += _exe_candidates(p, depth - 1)
    except OSError:
        pass
    return out


def find_main_exe(folder: Path) -> Path | None:
    """El .exe principal: el más cercano a la raíz y, a igualdad, el más grande."""
    cands = _exe_candidates(folder)
    if not cands:
        return None
    return min(cands, key=lambda p: (len(p.relative_to(folder).parts), -p.stat().st_size))


def is_game_folder(folder: Path) -> bool:
    """¿Hay un .exe de juego directamente aquí (o en bin/Binaries)?"""
    return bool(_exe_candidates(folder, depth=1)) or any(
        (folder / d).is_dir() and _exe_candidates(folder / d, depth=3) for d in ("bin", "Binaries", "x64", "Game"))


def find_game_root(folder: Path) -> Path:
    """Si eligieron una subcarpeta del juego («Mods», «Data», «BepInEx»…), devuelve la carpeta del juego."""
    p = folder
    for _ in range(3):
        if _exe_candidates(p, depth=1):
            return p
        if p.parent == p:
            break
        p = p.parent
    return folder


def clean_folder_name(name: str) -> str:
    """'Solasta.II.Supporter.Edition-InsaneRamZes' -> 'Solasta II Supporter Edition'"""
    text = re.sub(r"\[.*?\]|\(.*?\)", " ", name)                       # [GOG] (v1.12)
    text = re.sub(r"-[A-Za-z0-9]+$", "", text) if " " not in text else text   # -GRUPO al final
    text = re.sub(r"(?i)(?<![a-z])v?\d+(?:[._]\d+){1,}[a-z]?\b", " ", text)    # v0.107.1, 1.5.78
    text = re.sub(r"(?i)(?<![a-z])(v|build)\s?\d+\b", " ", text)               # v12, build 5
    text = re.sub(r"[._]+", " ", text)
    text = _NAME_JUNK.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" -")
    return text or name


# ════════════════════════════════════════════════════════════════════════
#  Registro de Windows (inyectable)
# ════════════════════════════════════════════════════════════════════════
class RegistryReader(Protocol):
    def values(self, hive: str, path: str) -> dict[str, Any] | None: ...
    def subkeys(self, hive: str, path: str) -> list[str]: ...


class WindowsRegistry:
    """Lectura del registro real. Solo funciona en Windows."""

    def __init__(self):
        import winreg
        self.winreg = winreg
        self.hives = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}

    def values(self, hive, path):
        wr = self.winreg
        try:
            with wr.OpenKey(self.hives[hive], path, 0, wr.KEY_READ) as key:
                out, i = {}, 0
                while True:
                    try:
                        name, value, _ = wr.EnumValue(key, i)
                    except OSError:
                        return out
                    out[name] = value
                    i += 1
        except OSError:
            return None

    def subkeys(self, hive, path):
        wr = self.winreg
        try:
            with wr.OpenKey(self.hives[hive], path, 0, wr.KEY_READ) as key:
                out, i = [], 0
                while True:
                    try:
                        out.append(wr.EnumKey(key, i))
                    except OSError:
                        return out
                    i += 1
        except OSError:
            return []


class NullRegistry:
    def values(self, hive, path):
        return None

    def subkeys(self, hive, path):
        return []


# ════════════════════════════════════════════════════════════════════════
#  Parser VDF/ACF de Steam (formato de texto de Valve)
# ════════════════════════════════════════════════════════════════════════
_VDF_TOKEN = re.compile(r'"((?:\\.|[^"\\])*)"|([{}])|//[^\n]*')


def parse_vdf(text: str) -> dict[str, Any]:
    """Convierte el texto de libraryfolders.vdf / appmanifest_*.acf en diccionarios anidados.
    Las claves se guardan tal cual y también se puede buscar sin distinguir mayúsculas con vdf_get()."""
    root: dict[str, Any] = {}
    stack = [root]
    pending_key: str | None = None
    for m in _VDF_TOKEN.finditer(text):
        string_, brace = m.group(1), m.group(2)
        if string_ is not None:
            value = string_.replace('\\\\', '\\').replace('\\"', '"')
            if pending_key is None:
                pending_key = value
            else:
                stack[-1][pending_key] = value
                pending_key = None
        elif brace == "{":
            child: dict[str, Any] = {}
            stack[-1][pending_key or ""] = child
            stack.append(child)
            pending_key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
    return root


def vdf_get(d: dict[str, Any], key: str, default: Any = None) -> Any:
    lk = key.lower()
    return next((v for k, v in d.items() if k.lower() == lk), default)


# ════════════════════════════════════════════════════════════════════════
#  Detector
# ════════════════════════════════════════════════════════════════════════
class GameDetector:
    def __init__(self, *, registry: RegistryReader | None = None, env: dict[str, str] | None = None,
                 drives: Iterable[Path] | None = None, steam_paths: Iterable[Path] | None = None,
                 profile_matcher: Callable[..., Any] = find_profile):
        if registry is None:
            try:
                registry = WindowsRegistry()
            except ImportError:            # no estamos en Windows
                registry = NullRegistry()
        self.registry = registry
        self.env = dict(os.environ if env is None else env)
        self._drives = list(drives) if drives is not None else None
        self._steam_paths = list(steam_paths) if steam_paths is not None else None
        self.match_profile = profile_matcher

    # --- utilidades -------------------------------------------------------
    def drives(self) -> list[Path]:
        if self._drives is not None:
            return self._drives
        if os.name != "nt":
            return [Path("/")]
        return [Path(f"{d}:\\") for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]

    def _finish(self, g: DetectedGame) -> DetectedGame:
        profile = self.match_profile(g.store, g.store_id, g.name, g.install_path)
        g.profile_slug = profile.slug if profile else None
        if g.exe_path is None and profile:
            g.exe_path = next((g.install_path / e for e in profile.exe_names if (g.install_path / e).is_file()), None)
        return g

    def detect_all(self) -> list[DetectedGame]:
        """Lanza todos los detectores. Si uno falla, los demás siguen."""
        found: dict[str, DetectedGame] = {}
        for name, fn in (("Steam", self.detect_steam), ("Epic", self.detect_epic),
                         ("GOG", self.detect_gog), ("Xbox", self.detect_xbox)):
            try:
                for g in fn():
                    found.setdefault(g.key, g)
            except Exception:  # noqa: BLE001
                log.exception("La detección de %s falló", name)
        return sorted(found.values(), key=lambda g: g.name.lower())

    # --- Steam ------------------------------------------------------------
    def steam_roots(self) -> list[Path]:
        if self._steam_paths is not None:
            return [p for p in self._steam_paths if p.exists()]
        candidates = []
        for hive, key, value in (("HKCU", r"Software\Valve\Steam", "SteamPath"),
                                 ("HKLM", r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                                 ("HKLM", r"SOFTWARE\Valve\Steam", "InstallPath")):
            v = (self.registry.values(hive, key) or {}).get(value)
            if v:
                candidates.append(Path(v))
        pf86 = self.env.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        candidates.append(Path(pf86) / "Steam")
        seen, out = set(), []
        for c in candidates:
            k = str(c).lower().replace("/", "\\")
            if k not in seen and c.exists():
                seen.add(k)
                out.append(c)
        return out

    def steam_libraries(self) -> list[Path]:
        libs: list[Path] = []
        for root in self.steam_roots():
            libs.append(root)
            for vdf_path in (root / "steamapps" / "libraryfolders.vdf", root / "config" / "libraryfolders.vdf"):
                if not vdf_path.is_file():
                    continue
                data = parse_vdf(vdf_path.read_text(encoding="utf-8", errors="replace"))
                folders = vdf_get(data, "libraryfolders") or {}
                for entry in folders.values():
                    path = vdf_get(entry, "path") if isinstance(entry, dict) else entry   # formato nuevo / antiguo
                    if isinstance(path, str) and path:
                        libs.append(Path(path))
                break
        uniq: dict[str, Path] = {}
        for lib in libs:
            uniq.setdefault(str(lib).lower().rstrip("\\/"), lib)
        return [l for l in uniq.values() if (l / "steamapps").is_dir()]

    def detect_steam(self) -> list[DetectedGame]:
        games = []
        for lib in self.steam_libraries():
            for acf in sorted((lib / "steamapps").glob("appmanifest_*.acf")):
                try:
                    state = vdf_get(parse_vdf(acf.read_text(encoding="utf-8", errors="replace")), "AppState") or {}
                except OSError:
                    continue
                appid, installdir = vdf_get(state, "appid"), vdf_get(state, "installdir")
                if not appid or not installdir or appid in STEAM_IGNORED_APPIDS:
                    continue
                path = lib / "steamapps" / "common" / installdir
                try:
                    flags = int(vdf_get(state, "StateFlags") or 4)
                except ValueError:
                    flags = 4
                if not flags & 4 or not has_game_files(path):      # 4 = «completamente instalado»
                    continue
                games.append(self._finish(DetectedGame(
                    store="steam", store_id=appid, name=vdf_get(state, "name") or installdir, install_path=path,
                    extra={"library": str(lib), "workshop": str(lib / "steamapps" / "workshop" / "content" / appid)})))
        return games

    # --- Epic Games -------------------------------------------------------
    def epic_manifest_dir(self) -> Path:
        program_data = self.env.get("ProgramData", r"C:\ProgramData")
        return Path(program_data) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"

    def detect_epic(self) -> list[DetectedGame]:
        folder = self.epic_manifest_dir()
        if not folder.is_dir():
            return []
        games = []
        for item in sorted(folder.glob("*.item")):
            try:
                m = json.loads(item.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                log.warning("Manifiesto de Epic ilegible: %s", item)
                continue
            if m.get("bIsIncompleteInstall") or not m.get("InstallLocation"):
                continue
            if "addons" in [c.lower() for c in m.get("AppCategories", [])] and not m.get("bIsApplication", True):
                continue                                                    # DLC
            main = m.get("MainGameAppName")
            if main and m.get("AppName") and main != m["AppName"]:
                continue                       # «Contenido de …»: complemento de otro juego, no un juego aparte
            path = Path(m["InstallLocation"])
            if not has_game_files(path):
                continue
            exe = path / m["LaunchExecutable"] if m.get("LaunchExecutable") else None
            games.append(self._finish(DetectedGame(
                store="epic", store_id=m.get("AppName") or item.stem, name=m.get("DisplayName") or path.name,
                install_path=path, exe_path=exe if exe and exe.is_file() else None,
                extra={"catalog_namespace": m.get("CatalogNamespace")})))
        return games

    # --- GOG Galaxy -------------------------------------------------------
    def detect_gog(self) -> list[DetectedGame]:
        games = []
        for base in (r"SOFTWARE\WOW6432Node\GOG.com\Games", r"SOFTWARE\GOG.com\Games"):
            for gid in self.registry.subkeys("HKLM", base):
                v = self.registry.values("HKLM", f"{base}\\{gid}") or {}
                path = v.get("path") or v.get("PATH")
                if not path or not Path(path).is_dir():
                    continue
                exe = v.get("exe") or v.get("EXE")
                exe_path = Path(exe) if exe and Path(exe).is_absolute() else (Path(path) / exe if exe else None)
                games.append(self._finish(DetectedGame(
                    store="gog", store_id=str(v.get("gameID") or gid), name=v.get("gameName") or Path(path).name,
                    install_path=Path(path), exe_path=exe_path if exe_path and exe_path.is_file() else None)))
            if games:
                break
        return games

    # --- Xbox App / PC Game Pass -------------------------------------------
    @staticmethod
    def read_gaming_root(drive: Path) -> Path | None:
        """Lee <unidad>:\\.GamingRoot: cabecera 'RGBX' + ruta en UTF-16 de la carpeta de juegos."""
        f = drive / ".GamingRoot"
        try:
            raw = f.read_bytes()
        except OSError:
            return None
        if not raw.startswith(b"RGBX") or len(raw) < 10:
            return None
        text = raw[8:].decode("utf-16-le", errors="ignore").split("\x00")[0].strip()
        if not text:
            return None
        rel = text.lstrip("\\/")
        return drive / rel

    def detect_xbox(self) -> list[DetectedGame]:
        games = []
        for drive in self.drives():
            roots = {p for p in (self.read_gaming_root(drive), drive / "XboxGames") if p and p.is_dir()}
            for root in roots:
                for folder in sorted(p for p in root.iterdir() if p.is_dir()):
                    content = folder / "Content"
                    if not content.is_dir():
                        continue
                    name, identity = folder.name, folder.name
                    config = content / "MicrosoftGame.config"
                    if config.is_file():
                        try:
                            xml = ET.parse(config).getroot()
                            ident = next((e for e in xml.iter() if e.tag.endswith("Identity")), None)
                            shell = next((e for e in xml.iter() if e.tag.endswith("ShellVisuals")), None)
                            identity = (ident.get("Name") if ident is not None else None) or identity
                            name = (shell.get("DefaultDisplayName") if shell is not None else None) or name
                            if name.startswith("ms-resource:"):
                                name = folder.name
                        except ET.ParseError:
                            pass
                    games.append(self._finish(DetectedGame(
                        store="xbox", store_id=identity, name=name, install_path=content,
                        extra={"package_folder": str(folder)})))
        return games

    # --- Carpetas añadidas a mano -------------------------------------------
    def from_folder(self, path: Path | str, name: str | None = None, exe: Path | str | None = None) -> DetectedGame:
        """Juego en una carpeta elegida a mano (portables, juegos sin tienda…).
        Si se eligió una subcarpeta del juego (p. ej. «Mods»), se sube hasta la carpeta con el .exe."""
        path = Path(path)
        if not path.is_dir():
            raise FileNotFoundError(f"La carpeta no existe: {path}")
        root = find_game_root(path)
        exe_path = Path(exe) if exe else find_main_exe(root)
        g = DetectedGame(store="custom", store_id=normalize_name(str(root)) or "custom",
                         name=name or clean_folder_name(root.name), install_path=root, exe_path=exe_path)
        return self._finish(g)

    def scan_library(self, folder: Path | str) -> list[DetectedGame]:
        """Una carpeta con VARIOS juegos portables dentro (p. ej. «D:\\Juegos»): un juego por subcarpeta."""
        folder = Path(folder)
        out = []
        try:
            subdirs = sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith((".", "$")))
        except OSError:
            return out
        for sub in subdirs:
            if find_main_exe(sub) is not None:
                out.append(self.from_folder(sub))
        return out

    def detect_portable(self, folders: Iterable[Path | str]) -> list[DetectedGame]:
        games: list[DetectedGame] = []
        for f in folders:
            f = Path(f)
            if not f.is_dir():
                continue
            games += [self.from_folder(f)] if is_game_folder(f) else self.scan_library(f)
        return games


# ════════════════════════════════════════════════════════════════════════
#  Guardar en la base de datos
# ════════════════════════════════════════════════════════════════════════
def save_detected(db, games: Iterable[DetectedGame], staging, *, deploy_method: str = "hardlink") -> dict[str, int]:
    """Inserta o actualiza los juegos en la tabla `games` y sus IDs por fuente. Devuelve {clave: game_id}."""
    from ..games import get_profile

    ids: dict[str, int] = {}
    with db.transaction():
        for g in games:
            profile = get_profile(g.profile_slug)
            slug = g.profile_slug or f"{g.store}-{normalize_name(g.name)[:40] or g.store_id}"
            staging_path = str(staging.game_dir(slug, g.install_path))
            db.execute(
                """INSERT INTO games (slug, name, install_path, exe_path, store, store_app_id, profile, staging_path,
                                      deploy_method)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(slug) DO UPDATE SET install_path=excluded.install_path, exe_path=excluded.exe_path,
                       store=excluded.store, store_app_id=excluded.store_app_id, profile=excluded.profile""",
                (slug, profile.name if g.profile_slug else g.name, str(g.install_path),
                 str(g.exe_path) if g.exe_path else None, g.store, g.store_id, profile.slug, staging_path,
                 deploy_method))
            game_id = db.query("SELECT id FROM games WHERE slug=?", (slug,))[0]["id"]
            for source, ext in profile.source_ids.items():
                db.execute("INSERT OR REPLACE INTO game_source_ids VALUES (?,?,?)", (game_id, source, ext))
            if g.store == "steam":
                db.execute("INSERT OR IGNORE INTO game_source_ids VALUES (?,?,?)", (game_id, "steam", g.store_id))
            ids[g.key] = game_id
    return ids
