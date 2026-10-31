"""Escenario de demostración de la UI con webs simuladas (sin red). Sirve para pruebas y capturas.

    python tests/ui_demo.py           -> abre la ventana con datos de ejemplo
    (en las pruebas se usa build_demo() con QT_QPA_PLATFORM=offscreen)
"""
from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modhub.core.game_detect import GameDetector  # noqa: E402


def _zip(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for k, v in entries.items():
            zf.writestr(k, v)
    return buf.getvalue()


NOW = int(time.time())
MOD_ZIP = _zip({"manifest.json": "{}", "README.md": "léeme", "plugins/MoreCompany.dll": "DLL"})
TS_INDEX = [
    {"full_name": f"{owner}-{name}", "name": name, "owner": owner, "package_url": f"https://thunderstore.io/c/lethal-company/p/{owner}/{name}/",
     "is_deprecated": False, "has_nsfw_content": nsfw, "categories": cats, "date_updated": "2026-10-20T10:00:00Z", "date_created": f"2025-{1 + len(name) % 12:02d}-15T10:00:00Z",
     "versions": [{"full_name": f"{owner}-{name}-1.0.0", "name": name, "version_number": "1.0.0",
                   "download_url": f"https://thunderstore.io/package/download/{owner}/{name}/1.0.0/",
                   "downloads": dl, "description": desc, "icon": "", "file_size": len(MOD_ZIP),
                   "dependencies": ["BepInEx-BepInExPack-5.4.2100"]}]}
    for owner, name, dl, desc, cats, nsfw in [
        ("notnotnotswipez", "MoreCompany", 27_067_074, "Hasta 32 jugadores por partida", ["Misc", "Client-side"], False),
        ("Evaisa", "LethalLib", 24_088_726, "Librería para crear mods", ["Libraries"], False),
        ("x753", "More_Suits", 9_812_331, "Más trajes para la tripulación", ["Suits", "Cosmetics"], False),
        ("Sligili", "MoreCompanyEmotes", 3_120_000, "Bailes y emotes", ["Emotes", "Cosmetics"], False),
        ("Adulto", "SpicySuits", 120_000, "Trajes para adultos", ["Suits"], True),
        ("Coderz", "CompanyBugFixes", 640_000, "Corrige fallos del juego base", ["Bug Fixes"], False),
    ]
]
MODRINTH_HITS = {"hits": [
    {"project_id": "AANobbMI", "slug": "sodium", "title": "Sodium", "author": "jellysquid3", "description":
     "Motor de renderizado moderno: muchos más FPS", "project_type": "mod", "downloads": 61_000_000,
     "date_modified": "2026-10-01T00:00:00Z", "categories": ["optimization", "fabric"]},
    {"project_id": "gvQqBUqZ", "slug": "lithium", "title": "Lithium", "author": "jellysquid3",
     "description": "Optimiza la lógica del servidor", "project_type": "mod", "downloads": 30_000_000,
     "date_modified": "2025-01-01T00:00:00Z", "categories": ["optimization"]},
]}


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "/c/lethal-company/api/v1/package/" in url:
        return httpx.Response(200, json=TS_INDEX)
    if "thunderstore.io/package/download/" in url:
        return httpx.Response(200, content=MOD_ZIP, headers={"Content-Length": str(len(MOD_ZIP))})
    if "api.modrinth.com/v2/search" in url:
        return httpx.Response(200, json=MODRINTH_HITS)
    return httpx.Response(404, json={"error": "simulado"})


class NoRegistry:
    def values(self, *a):
        return None

    def subkeys(self, *a):
        return []


def build_demo(base: Path, *, with_fomod: bool = True):
    """Crea juegos falsos + AppContext con webs simuladas. Devuelve (ctx, ids)."""
    from modhub.ui.context import AppContext

    lethal = base / "Juegos" / "Lethal Company"
    (lethal / "BepInEx" / "core").mkdir(parents=True, exist_ok=True)
    (lethal / "Lethal Company.exe").write_text("exe")
    (lethal / "BepInEx" / "core" / "BepInEx.dll").write_text("dll")
    skyrim = base / "Juegos" / "Skyrim Special Edition"
    (skyrim / "Data").mkdir(parents=True, exist_ok=True)
    (skyrim / "SkyrimSE.exe").write_text("exe")
    stardew = base / "Juegos" / "Stardew Valley"
    stardew.mkdir(parents=True, exist_ok=True)
    (stardew / "Stardew Valley.exe").write_text("exe")

    detector = GameDetector(registry=NoRegistry(), env={}, drives=[], steam_paths=[])
    ctx = AppContext(base / "appdata", http_transport=httpx.MockTransport(handler), use_keyring=False,
                     detector=detector)
    ctx.settings.enabled_sources = ["thunderstore", "modrinth", "nexus", "curseforge", "moddb", "ayakamods"]
    ctx.reload_sources()
    ids = {name: ctx.add_game_folder(str(p), name) for name, p in
           [("Lethal Company", lethal), ("Skyrim Special Edition", skyrim), ("Stardew Valley", stardew)]}
    return ctx, ids


if __name__ == "__main__":
    import asyncio
    import tempfile

    import qasync
    from PySide6.QtWidgets import QApplication

    from modhub.ui.main_window import MainWindow
    from modhub.ui.theme import STYLESHEET

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    ctx, _ = build_demo(Path(tempfile.mkdtemp(prefix="modhub-demo-")))
    win = MainWindow(ctx, start_watcher=False)
    win.show()
    closing = asyncio.Event()
    app.aboutToQuit.connect(closing.set)
    with loop:
        loop.run_until_complete(closing.wait())
