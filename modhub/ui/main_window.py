"""Ventana principal: barra lateral + páginas + avisos + enlaces del navegador."""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
                               QStackedWidget, QStatusBar, QVBoxLayout, QWidget)

from ..core.downloader import DownloadState
from ..games import get_profile
from ..settings import default_downloads_dir
from .dialogs import ChooseGameDialog, FomodDialog, PlanReviewDialog
from .downloads_view import DownloadsView
from .flow import InstallFlow
from .games_view import GamesView, report_text
from .installed_view import InstalledView
from .intercept import DownloadsWatcher
from .search_view import SearchView
from .settings_view import SettingsView
from .protocol import ProtocolRegistrar
from .theme import SOURCE_NAMES
from .web_queue import WebQueue
from .widgets import NotificationArea, spawn

ICON = Path(__file__).resolve().parent / "assets" / "modhub.png"
PAGES = [("games", "🎮   Juegos"), ("search", "🔎   Buscador global"), ("installed", "🧩   Mods instalados"),
         ("downloads", "⬇   Descargas"), ("settings", "⚙   Ajustes")]


def redact(url: str) -> str:
    """Oculta la clave de descarga de los enlaces nxm:// en lo que se muestra."""
    return re.sub(r"(key|expires|user_id)=[^&]+", r"\1=…", url)


class MainWindow(QMainWindow):
    def __init__(self, ctx, *, registrar=None, start_watcher: bool = True):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("ModHub — Gestor universal de mods")
        if ICON.exists():
            self.setWindowIcon(QIcon(str(ICON)))
        self.resize(1320, 820)
        self.setMinimumSize(QSize(1000, 640))

        root = QWidget()
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.setCentralWidget(root)

        # ── barra lateral ──
        side = QWidget()
        side.setObjectName("Sidebar")
        side.setFixedWidth(232)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 12)
        logo = QLabel("◆ ModHub")
        logo.setObjectName("Logo")
        sub = QLabel("Gestor universal de mods")
        sub.setObjectName("LogoSub")
        sv.addWidget(logo)
        sv.addWidget(sub)
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        for key, text in PAGES:
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(QSize(200, 42))
            self.nav.addItem(item)
        sv.addWidget(self.nav, 1)
        self.side_status = QLabel("")
        self.side_status.setObjectName("LogoSub")
        self.side_status.setWordWrap(True)
        sv.addWidget(self.side_status)
        h.addWidget(side)

        # ── contenido ──
        content = QWidget()
        content.setObjectName("Page")
        cv = QVBoxLayout(content)
        cv.setContentsMargins(0, 0, 0, 0)
        self.notifications = NotificationArea()
        nwrap = QWidget()
        nl = QVBoxLayout(nwrap)
        nl.setContentsMargins(24, 12, 24, 0)
        nl.addWidget(self.notifications)
        cv.addWidget(nwrap)
        self.stack = QStackedWidget()
        cv.addWidget(self.stack, 1)
        h.addWidget(content, 1)

        # ── servicios de la UI ──
        folder = Path(ctx.settings.downloads_folder) if ctx.settings.downloads_folder else default_downloads_dir()
        self.watcher = DownloadsWatcher(folder, self)
        self.watcher.file_ready.connect(self._on_download_detected)
        if start_watcher and ctx.settings.watch_downloads and not self.watcher.start():
            self.notify(f"No se puede vigilar la carpeta Descargas ({folder}). Cámbiala en Ajustes.", "warn")
        self.registrar = registrar or ProtocolRegistrar()
        self.web_queue = WebQueue(self.notify, protocol_ok=self._protocol_ok)
        self.flow = InstallFlow(ctx, self.notify, self.review_mod, self.watcher, self.web_queue)

        # ── páginas ──
        self.pages: dict[str, QWidget] = {
            "games": GamesView(ctx, self.notify),
            "search": SearchView(ctx, self.notify, self.flow),
            "installed": InstalledView(ctx, self.notify, self.flow),
            "downloads": DownloadsView(ctx, self.notify),
            "settings": SettingsView(ctx, self.notify, self.registrar),
        }
        for key, _ in PAGES:
            self.stack.addWidget(self.pages[key])
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.pages["games"].game_selected.connect(self._sync_game)
        ctx.events.navigate.connect(self.go)
        ctx.events.notify.connect(lambda text, level, actions: self.notify(text, level, actions))

        self.setStatusBar(QStatusBar())
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start(1000)
        self.go("games" if ctx.games() else "games")
        QTimer.singleShot(1200, self._offer_resume)

    # ── utilidades ─────────────────────────────────────────────────────
    def notify(self, text: str, level: str = "info", actions=None) -> None:
        self.notifications.show_message(text, level, actions)

    def go(self, page: str) -> None:
        keys = [k for k, _ in PAGES]
        if page in keys:
            self.nav.setCurrentRow(keys.index(page))

    def _sync_game(self, game_id: int) -> None:
        for key in ("search", "installed"):
            self.pages[key].game.select_game(game_id)

    def _update_status(self) -> None:
        tasks = self.ctx.downloads.tasks
        running = [t for t in tasks if t.state is DownloadState.RUNNING]
        speed = sum(t.speed for t in running)
        if running:
            text = f"⬇ {len(running)} descargando · {speed / 1048576:.1f} MB/s"
        else:
            text = "Sin descargas activas"
        if self.web_queue.current is not None:
            text += f"   ·   🌐 Esperando tu clic en la web: «{self.web_queue.current.name}»"
            if self.web_queue.pending:
                text += f" (+{len(self.web_queue.pending)} en cola)"
        configured = [SOURCE_NAMES.get(k, k) for k, a in self.ctx.registry.adapters.items() if a.is_configured]
        self.statusBar().showMessage(f"{text}   ·   Webs listas: {', '.join(configured)}")
        self.side_status.setText(f"{len(self.ctx.games())} juegos")

    # ── activación desde otra copia / enlaces del navegador ──────────────
    def handle_message(self, message: str) -> None:
        self.bring_to_front()
        if message.strip() and message.strip() != "activate":
            self.handle_url(message.strip())

    def bring_to_front(self) -> None:
        self.setWindowState((self.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()

    def handle_url(self, url: str) -> None:
        self.ctx.events.intercept_event.emit(f"Enlace recibido: {redact(url)}")
        try:
            match = self.ctx.resolve_protocol(url)
        except Exception as e:  # noqa: BLE001
            self.notify(str(e), "error")
            return
        req = match.request
        source = SOURCE_NAMES.get(req.source, req.source)
        expected = self.web_queue.take(req.source, req.mod_id, req.file_id)
        if expected is not None and self.ctx.game(expected.game_id) is not None:
            # Lo habías pedido desde ModHub: ya se sabe el juego y el nombre, sin preguntar nada
            self.notify(f"«{expected.name}»: enlace recibido de {source}. Descargando…", "info")
            spawn(self.ctx.install_from_protocol(match, expected.game_id),
                  on_ok=lambda r: self.flow.handle_result(r, expected.game_id, expected.name),
                  on_error=lambda e: self.notify(f"No se pudo instalar «{expected.name}»: {e}", "error"))
            return
        if not match.games:
            what = f"«{req.game_id}»" if req.game_id else f"ningún juego compatible con {source}"
            self.notify(f"El enlace es para {what}, pero ese juego no está en ModHub. Añádelo en «Juegos» y "
                        "vuelve a pulsar el botón en la web.", "warn")
            self.go("games")
            return
        game_id = match.games[0]["id"]
        if len(match.games) > 1:
            dlg = ChooseGameDialog(match.games, f"Este mod de {source} podría ser de varios juegos. ¿En cuál lo instalo?",
                                   self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            game_id = dlg.game_id()
        self.notify(f"Descarga recibida desde {source}: mod {req.mod_id}. Empezando…", "info")
        self.go("downloads")
        spawn(self.ctx.install_from_protocol(match, game_id),
              on_ok=lambda r: self.flow.handle_result(r, game_id, f"Mod {req.mod_id}"),
              on_error=lambda e: self.notify(f"No se pudo instalar desde el enlace: {e}", "error"))

    def _on_download_detected(self, path: Path, pending) -> None:
        if pending is None:
            self.ctx.events.intercept_event.emit(f"Archivo nuevo en Descargas (no esperado): {path.name}")
            games = self.ctx.games()
            if games:
                self.notify(f"Se ha descargado «{path.name}». ¿Es un mod?", "info",
                            [("Instalar en un juego…", lambda: self._install_unexpected(path))])
            return
        game = self.ctx.game(pending.game_id)
        self.ctx.events.intercept_event.emit(f"Recogido de Descargas: {path.name} → {pending.mod_name}")
        self.notify(f"«{path.name}» descargado. ¿Instalarlo en {game['name']}?", "ok",
                    [("Instalar", lambda: self._install_archive(pending.game_id, path, pending.mod_name,
                                                                 pending.source, pending.mod_id))])

    def _install_unexpected(self, path: Path) -> None:
        dlg = ChooseGameDialog(self.ctx.games(), f"¿En qué juego quieres instalar «{path.name}»?", self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.game_id() is not None:
            self._install_archive(dlg.game_id(), path, path.stem)

    def _install_archive(self, game_id: int, path: Path, name: str, source: str | None = None,
                         mod_id: str | None = None) -> None:
        spawn(self.ctx.install_archive(game_id, path, name, source=source, mod_id=mod_id),
              on_ok=lambda r: self.flow.handle_result(r, game_id, name),
              on_error=lambda e: self.notify(f"No se pudo instalar «{name}»: {e}", "error"))

    # ── revisión de mods dudosos / FOMOD ────────────────────────────────
    def review_mod(self, mod_id: int) -> None:
        mod = self.ctx.db.one("SELECT * FROM mods WHERE id=?", (mod_id,))
        if mod is None or not mod["staging_dir"]:
            self.notify("Este mod no tiene archivos que revisar.", "warn")
            return
        game = self.ctx.game(mod["game_id"])
        profile = get_profile(game["profile"])
        try:
            kind, obj = self.ctx.review_session(mod_id)
        except Exception as e:  # noqa: BLE001
            self.notify(f"No se pudo abrir el instalador: {e}", "error")
            return
        if kind == "fomod":
            dlg = FomodDialog(obj, profile, game["install_path"], self)
            if dlg.exec() != QDialog.DialogCode.Accepted or dlg.plan is None:
                return
            plan = dlg.plan
        else:
            dlg = PlanReviewDialog(obj, self, title=f"Revisar «{mod['name']}»")
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            plan = dlg.plan
        spawn(self.ctx.run_deploy(mod["game_id"], self.ctx.installer.confirm_review, mod_id, plan),
              on_ok=lambda r: self.notify(report_text(f"«{mod['name']}» instalado", r), "ok" if r.ok else "warn"),
              on_error=lambda e: self.notify(str(e), "error"))

    def _protocol_ok(self, source: str) -> bool:
        """¿El botón de la web mandará el enlace a ModHub? (nxm:// registrado a nuestro nombre)"""
        if source != "nexus" or not self.registrar.available:
            return True
        try:
            return self.registrar.is_ours("nxm")
        except OSError:
            return True

    def _offer_resume(self) -> None:
        """Si al cerrar ModHub quedaron descargas a medias, ofrece seguir donde se quedaron."""
        rows = self.ctx.interrupted_installs()
        if not rows:
            return
        names = ", ".join(f"«{r['name']}»" for r in rows[:3]) + (f" y {len(rows) - 3} más" if len(rows) > 3 else "")
        self.notify(f"Quedaron descargas a medias al cerrar ModHub: {names}.", "warn",
                    [("Continuar", lambda: self._resume_all(rows)), ("Descartar", lambda: self._discard_all(rows))])

    def _resume_all(self, rows) -> None:
        for r in rows:
            spawn(self.ctx.resume_install(r),
                  on_ok=lambda res, r=r: self.flow.handle_result(res, r["game_id"], r["name"]),
                  on_error=lambda e, r=r: self.notify(f"No se pudo continuar «{r['name']}»: {e}", "error"))

    def _discard_all(self, rows) -> None:
        for r in rows:
            self.ctx.discard_interrupted(r["id"])
            self.ctx.events.mods_changed.emit(r["game_id"])

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self.ctx.settings.search_filters = self.pages["search"].filters.options().to_dict()
            self.ctx.store.save()
        finally:
            self.watcher.stop()
            super().closeEvent(event)
