"""Punto de entrada de ModHub.

    python -m modhub.main                 abre la aplicación
    python -m modhub.main "nxm://…"       lo que ejecuta Windows al pulsar un enlace en el navegador

Si ModHub ya está abierto, esta segunda copia solo le pasa el enlace y se cierra.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler

URL_PREFIXES = ("nxm://", "modhub://", "modhub:", "ror2mm://")


def _setup_logging(data_dir) -> None:
    handler = RotatingFileHandler(data_dir / "modhub.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handlers: list[logging.Handler] = [handler]
    if sys.stderr is not None:                       # en ModHub.exe (sin consola) no hay stderr
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx registra cada URL completa (con claves de API en la consulta): solo avisos y errores
    for noisy in ("httpx", "httpcore", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def find_url(argv: list[str]) -> str | None:
    return next((a for a in argv[1:] if a.lower().startswith(URL_PREFIXES)), None)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    url = find_url(argv)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from .settings import default_data_dir
    from .ui.single_instance import SingleInstanceGuard

    app = QApplication(argv)
    app.setApplicationName("ModHub")
    app.setOrganizationName("ModHub")

    guard = SingleInstanceGuard()
    if not guard.acquire():
        # Ya hay un ModHub abierto: le pasamos el enlace (o que se ponga delante) y salimos.
        guard.send(url or "activate")
        return 0

    import qasync

    from .ui.context import AppContext
    from .ui.main_window import MainWindow
    from .ui.theme import STYLESHEET

    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    _setup_logging(data_dir)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    ctx = AppContext(data_dir)
    window = MainWindow(ctx)
    guard.message_received.connect(window.handle_message)
    window.show()
    if url:
        QTimer.singleShot(400, lambda: window.handle_url(url))

    closing = asyncio.Event()
    app.aboutToQuit.connect(ctx.shutdown)          # pausa las descargas: se reanudan desde el .part
    app.aboutToQuit.connect(closing.set)
    with loop:
        loop.run_until_complete(closing.wait())
    guard.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
