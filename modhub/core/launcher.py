"""Lanzar un juego desde ModHub.

Orden de preferencia:
  1. Script extender / cargador instalado (SKSE, F4SE, SMAPI): sin él, sus mods no cargan.
  2. Lanzador de la tienda: Steam y Epic (necesario para el DRM, logros y el overlay).
  3. El .exe del juego (GOG, Xbox, carpetas propias o si lo anterior no está).
Los mods BepInEx, UE4SS, REDmod… se cargan solos aunque el juego se abra desde Steam.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..games import get_profile


@dataclass
class LaunchPlan:
    kind: str            # "loader" | "uri" | "exe" | "missing"
    target: str          # ruta del .exe o URI de la tienda
    label: str           # texto para el usuario
    cwd: str | None = None


def plan_launch(game) -> LaunchPlan:
    """`game` = fila de la tabla games."""
    root = Path(game["install_path"])
    profile = get_profile(game["profile"])
    for loader in (*profile.required_loaders, *profile.optional_loaders):
        if loader.launcher and (exe := root / loader.launcher).is_file():
            return LaunchPlan("loader", str(exe), f"{loader.name} (carga los mods)", str(root))
    store, app_id = game["store"], game["store_app_id"]
    if store == "steam" and app_id:
        return LaunchPlan("uri", f"steam://rungameid/{app_id}", "Steam")
    if store == "epic" and app_id:
        return LaunchPlan("uri", f"com.epicgames.launcher://apps/{app_id}?action=launch&silent=true", "Epic Games")
    exe = game["exe_path"]
    if exe and Path(exe).is_file():
        return LaunchPlan("exe", exe, Path(exe).name, str(Path(exe).parent))
    candidates = [root / e for e in profile.exe_names if (root / e).is_file()]
    if candidates:
        return LaunchPlan("exe", str(candidates[0]), candidates[0].name, str(candidates[0].parent))
    return LaunchPlan("missing", "", "No se sabe qué .exe abrir")


def launch(plan: LaunchPlan) -> None:
    if plan.kind == "uri":
        if sys.platform == "win32":
            os.startfile(plan.target)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", plan.target])
    elif plan.kind in ("exe", "loader"):
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([plan.target], cwd=plan.cwd, creationflags=flags, close_fds=True)
    else:
        raise FileNotFoundError("No se sabe qué ejecutable abrir: elige el .exe del juego.")
