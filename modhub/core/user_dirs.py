"""Carpetas del usuario donde algunos mods deben instalarse (fuera de la carpeta del juego).

Muchos mods no van en el juego sino en:
  Documentos\\My Games\\<juego>          (Bethesda, muchos juegos de PC)
  %APPDATA%  (AppData\\Roaming)
  %LOCALAPPDATA%\\<juego>\\Saved\\Config  (Unreal: configuración .ini)
  AppData\\LocalLow\\<estudio>\\<juego>   (Unity)
  Saved Games\\<juego>

En las rutas de un plan se escriben con un prefijo: «@documents/My Games/Skyrim Special Edition/SKSE/x.ini».
Solo se admiten estas carpetas (lista blanca) y nunca se puede salir de ellas.
"""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

BASE_NAMES = {
    "documents": "Documentos",
    "appdata": "AppData\\Roaming",
    "localappdata": "AppData\\Local",
    "locallow": "AppData\\LocalLow",
    "savedgames": "Partidas guardadas (Saved Games)",
}
PREFIX = "@"


def _known_folder(guid: str) -> Path | None:
    """Carpeta real de Windows (respeta Documentos movido a OneDrive o a otro disco)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        import uuid

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]
        u = uuid.UUID(guid)
        g = GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) != 0:
            return None
        path = Path(out.value)
        ctypes.windll.ole32.CoTaskMemFree(out)
        return path
    except Exception:  # noqa: BLE001
        return None


def detect_user_dirs(env: dict[str, str] | None = None) -> dict[str, Path]:
    env = dict(os.environ if env is None else env)
    home = Path(env.get("USERPROFILE") or env.get("HOME") or Path.home())
    out = {
        "documents": _known_folder("FDD39AD0-238F-46AF-ADB4-6C85480369C7") or home / "Documents",
        "appdata": Path(env["APPDATA"]) if env.get("APPDATA") else home / "AppData" / "Roaming",
        "localappdata": Path(env["LOCALAPPDATA"]) if env.get("LOCALAPPDATA") else home / "AppData" / "Local",
        "locallow": _known_folder("A520A1A4-1780-4FF6-BD18-167343C5AF16") or home / "AppData" / "LocalLow",
        "savedgames": _known_folder("4C5C32FF-BB9D-43B0-B5B4-2D72E54EAAA4") or home / "Saved Games",
    }
    return out


def split_target(rel_path: str) -> tuple[str | None, str]:
    """'@documents/My Games/x.ini' -> ('documents', 'My Games/x.ini');  'Data/x.esp' -> (None, 'Data/x.esp')"""
    if rel_path.startswith(PREFIX):
        base, _, rest = rel_path[1:].partition("/")
        return base.lower(), rest
    return None, rel_path


def describe_target(rel_path: str, game_name: str | None = None) -> str:
    base, rest = split_target(rel_path)
    game = f"la carpeta de {game_name}" if game_name else "la carpeta del juego"
    where = BASE_NAMES.get(base, game) if base else game
    return f"{where}\\{PurePosixPath(rest)}".replace("/", "\\") if rest else where
