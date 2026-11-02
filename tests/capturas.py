"""Recorre la interfaz con el escenario de demo y guarda una captura de cada pantalla.

    QT_QPA_PLATFORM=offscreen python tests/capturas.py <carpeta_salida>
"""
import asyncio
import sys
import tempfile
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import qasync  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from modhub.ui.main_window import MainWindow  # noqa: E402
from modhub.ui.theme import STYLESHEET  # noqa: E402
from ui_demo import build_demo  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "capturas")
out.mkdir(parents=True, exist_ok=True)
app = QApplication([])
# El backend offscreen puede no cargar las fuentes de Windows automáticamente.
if sys.platform == "win32":
    from PySide6.QtGui import QFontDatabase
    for filename in ("segoeui.ttf", "segoeuib.ttf", "seguiemj.ttf"):
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
app.setStyle("Fusion")
app.setStyleSheet(STYLESHEET)
loop = qasync.QEventLoop(app)
asyncio.set_event_loop(loop)
ctx, ids = build_demo(Path(tempfile.mkdtemp()))
win = MainWindow(ctx, start_watcher=False)
win.resize(1320, 820)
win.show()


async def until(cond, timeout=6.0):
    for _ in range(int(timeout / 0.05)):
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


async def scenario():
    await asyncio.sleep(0.3)
    win.grab().save(str(out / "1_juegos.png"))
    win.go("search")
    sv = win.pages["search"]
    sv.game.select_game(ids["Lethal Company"])
    sv.query.setText("company")
    sv.filter_btn.setChecked(True)
    sv.search()
    await until(lambda: sv.table.rowCount() > 0)
    sv.table.selectRow(0)
    await asyncio.sleep(0.4)
    win.grab().save(str(out / "2_buscador.png"))
    print("BUSCADOR:", sv.table.rowCount(), "filas ·", sv.status.text())
    sv.install_selected()
    gid = ids["Lethal Company"]
    await until(lambda: any(m["state"] in ("enabled", "error") for m in ctx.mods(gid)))
    await asyncio.sleep(0.5)
    print("MODS:", [(m["name"], m["state"], m["error"]) for m in ctx.mods(gid)])
    win.go("downloads")
    await asyncio.sleep(0.4)
    win.grab().save(str(out / "4_descargas.png"))
    win.go("installed")
    win.pages["installed"].game.select_game(gid)
    await asyncio.sleep(0.3)
    win.grab().save(str(out / "3_instalados.png"))
    win.go("settings")
    await asyncio.sleep(0.3)
    win.grab().save(str(out / "5_ajustes.png"))
    win.go("games")
    await asyncio.sleep(0.3)
    win.grab().save(str(out / "1_juegos.png"))
    app.quit()


loop.create_task(scenario())
try:
    with loop:
        loop.run_until_complete(asyncio.sleep(30))
except RuntimeError:
    pass            # app.quit() detiene el bucle antes de tiempo: es lo esperado
