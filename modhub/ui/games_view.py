"""Panel de juegos: detección automática, añadir carpetas, estado de modloaders, desplegar y purgar."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMessageBox,
                               QTableWidget, QVBoxLayout, QWidget)

from ..games import get_profile
from ..sources.platforms import PLATFORM_NAMES
from .theme import COLORS
from .widgets import button, open_folder, page_header, play_game, spawn, text_item

STORE_NAMES = {"steam": "Steam", "epic": "Epic Games", "xbox": "Xbox / Game Pass", "gog": "GOG", "custom": "Manual"}


class GamesView(QWidget):
    game_selected = Signal(int)

    def __init__(self, ctx, notify):
        super().__init__()
        self.setObjectName("Page")
        self.ctx, self.notify = ctx, notify
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)

        actions = QWidget()
        al = QHBoxLayout(actions)
        al.setContentsMargins(0, 0, 0, 0)
        self.detect_btn = button("🔍  Detectar juegos", self.detect, kind="Primary",
                                 tooltip="Busca juegos de Steam, Epic, Xbox/Game Pass y GOG")
        al.addWidget(self.detect_btn)
        al.addWidget(button("📁  Añadir juego…", self.add_folder,
                            tooltip="Un juego portable o sin tienda: elige su carpeta (la del .exe)"))
        al.addWidget(button("🗂  Carpeta de portables…", self.add_library,
                            tooltip="Una carpeta con VARIOS juegos portables dentro: se añaden todos y se "
                                    "vuelve a mirar cada vez que pulses «Detectar juegos»"))
        lay.addWidget(page_header("Juegos", "Tus juegos instalados y el estado de sus mods.", actions))

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Juego", "Tienda", "Perfil", "Modloaders", "Mods activos", "Ruta"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        for c in range(5):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.table.cellDoubleClicked.connect(lambda *_: self._open_mods())
        lay.addWidget(self.table, 1)

        self.empty = QLabel("Aún no hay juegos. Pulsa «Detectar juegos» o añade la carpeta de un juego a mano.")
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.empty)

        bar = QHBoxLayout()
        self.play_btn = button("▶  Jugar", lambda: play_game(self.ctx, self.selected_game_id(), self.notify, self),
                               kind="Primary", tooltip="Abre el juego (con SKSE/F4SE/SMAPI si están instalados)")
        self.mods_btn = button("🧩  Ver mods", self._open_mods)
        self.deploy_btn = button("🔗  Desplegar", self.deploy, tooltip="Vuelve a crear los enlaces de los mods activos")
        self.purge_btn = button("🧹  Purgar todo", self.purge, kind="Danger",
                                tooltip="Quita todos los mods del juego y restaura los archivos originales")
        self.folder_btn = button("📂  Abrir carpeta", self._open_game_folder)
        self.remove_btn = button("Quitar de la lista", self.remove)
        for b in (self.play_btn, self.mods_btn, self.deploy_btn, self.purge_btn, self.folder_btn):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.remove_btn)
        lay.addLayout(bar)

        ctx.events.games_changed.connect(self.refresh)
        ctx.events.mods_changed.connect(lambda _gid: self.refresh())
        self.refresh()

    # ── datos ──────────────────────────────────────────────────────────
    def selected_game_id(self) -> int | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        return self.table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)

    def refresh(self) -> None:
        keep = self.selected_game_id()
        games = self.ctx.games()
        self.table.setRowCount(len(games))
        for r, g in enumerate(games):
            profile = get_profile(g["profile"])
            loaders = self.ctx.loader_status(g["id"])
            parts, missing = [], False
            platform = self.ctx.game_platform(g["id"])
            for loader, required, installed in loaders:
                if platform in loader.unsupported and not installed:
                    parts.append(f"⛔ {loader.name} (no existe para {PLATFORM_NAMES[platform]})")
                    missing = True
                    continue
                mark = "✔" if installed else ("✖" if required else "–")
                parts.append(f"{mark} {loader.name}")
                missing |= required and not installed
            enabled = self.ctx.db.one("SELECT COUNT(*) AS n FROM mods WHERE game_id=? AND state='enabled'",
                                      (g["id"],))["n"]
            self.table.setItem(r, 0, text_item(g["name"], data=g["id"]))
            store = STORE_NAMES.get(g["store"], g["store"] or "—")
            if g["store"] == "custom" and platform:
                store += f" ({PLATFORM_NAMES[platform]})"
            self.table.setItem(r, 1, text_item(store))
            self.table.setItem(r, 2, text_item(profile.name if profile.slug != "generic" else "Genérico",
                                               tooltip=profile.notes))
            li = text_item("   ".join(parts) or "No necesita",
                           tooltip="✔ instalado · ✖ obligatorio y falta · – opcional sin instalar · "
                                   "⛔ no existe para tu versión del juego")
            if missing:
                li.setForeground(Qt.GlobalColor.yellow)
            self.table.setItem(r, 3, li)
            self.table.setItem(r, 4, text_item(enabled, align_right=True))
            self.table.setItem(r, 5, text_item(g["install_path"], tooltip=f"Staging: {g['staging_path']}"))
        self.empty.setVisible(not games)
        self.table.setVisible(bool(games))
        if keep is not None:
            self._select(keep)
        elif games:
            self.table.selectRow(0)
        self._update_buttons()

    def _select(self, game_id: int) -> None:
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) == game_id:
                self.table.selectRow(r)
                return

    def _on_select(self) -> None:
        self._update_buttons()
        if (gid := self.selected_game_id()) is not None:
            self.ctx.settings.last_game_id = gid
            self.game_selected.emit(gid)

    def _update_buttons(self) -> None:
        has = self.selected_game_id() is not None
        for b in (self.play_btn, self.mods_btn, self.deploy_btn, self.purge_btn, self.folder_btn, self.remove_btn):
            b.setEnabled(has)

    # ── acciones ───────────────────────────────────────────────────────
    def detect(self) -> None:
        self.detect_btn.setEnabled(False)
        self.detect_btn.setText("Buscando…")

        def ok(res):
            text = f"Detección terminada: {res['found']} juegos instalados."
            if res["removed"]:
                text += f" Se han quitado {res['removed']} que ya no están instalados."
            if res["ignored"]:
                text += f" {res['ignored']} que quitaste tú siguen ocultos."
            self.notify(text, "ok")

        def err(e):
            self.notify(f"La detección falló: {e}", "error")

        def end():
            self.detect_btn.setEnabled(True)
            self.detect_btn.setText("🔍  Detectar juegos")
        spawn(self.ctx.detect_games(), on_ok=ok, on_error=err, on_finally=end)

    def add_folder(self) -> None:
        from ..core.game_detect import clean_folder_name, find_game_root
        from .game_picker import GameNameDialog
        folder = QFileDialog.getExistingDirectory(self, "Carpeta del juego (donde está el .exe)")
        if not folder:
            return
        root = find_game_root(Path(folder))               # si eligieron «…\\Mods», sube a la del .exe
        dlg = GameNameDialog(self.ctx, clean_folder_name(root.name), str(root), self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, ids = dlg.result_name_and_ids()
        try:
            gid = self.ctx.add_game_folder(str(root), name or None, source_ids=ids)
        except Exception as e:  # noqa: BLE001
            self.notify(f"No se pudo añadir: {e}", "error")
            return
        prof = get_profile(self.ctx.game(gid)["profile"])
        if prof.slug == "generic":
            self.notify(f"«{name}» añadido sin perfil específico: cada mod pedirá revisión antes de instalarse.",
                        "warn")
        else:
            self.notify(f"«{name}» reconocido como {prof.name}.", "ok")
        self._select(gid)

    def add_library(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Carpeta que contiene tus juegos portables")
        if not folder:
            return
        ids = self.ctx.add_portable_library(folder)
        if ids:
            names = ", ".join(self.ctx.game(i)["name"] for i in ids[:6]) + (" …" if len(ids) > 6 else "")
            self.notify(f"{len(ids)} juegos portables añadidos: {names}. Si alguno tiene mal el nombre, "
                        "quítalo y añádelo con «Añadir juego…».", "ok")
        else:
            self.notify("No se ha encontrado ningún juego (subcarpetas con un .exe) en esa carpeta.", "warn")

    def _open_mods(self) -> None:
        if (gid := self.selected_game_id()) is not None:
            self.game_selected.emit(gid)
            self.ctx.events.navigate.emit("installed")

    def _open_game_folder(self) -> None:
        if (gid := self.selected_game_id()) is not None:
            open_folder(self.ctx.game(gid)["install_path"])

    def deploy(self) -> None:
        gid = self.selected_game_id()
        if gid is None:
            return
        spawn(self.ctx.run_deploy(gid, self.ctx.deployer.deploy_game, gid),
              on_ok=lambda r: self.notify(report_text("Despliegue", r), "ok" if r.ok else "warn"),
              on_error=lambda e: self.notify(str(e), "error"))

    def purge(self) -> None:
        gid = self.selected_game_id()
        if gid is None:
            return
        name = self.ctx.game(gid)["name"]
        if QMessageBox.question(self, "Purgar mods",
                                f"Se quitarán TODOS los mods de «{name}» y se restaurarán los archivos originales.\n"
                                "Los mods siguen en ModHub y puedes volver a desplegarlos cuando quieras.\n\n¿Continuar?"
                                ) != QMessageBox.StandardButton.Yes:
            return
        spawn(self.ctx.run_deploy(gid, self.ctx.deployer.purge_game, gid),
              on_ok=lambda r: self.notify(report_text("Purga", r), "ok" if r.ok else "warn"),
              on_error=lambda e: self.notify(str(e), "error"))

    def remove(self) -> None:
        gid = self.selected_game_id()
        if gid is None:
            return
        name = self.ctx.game(gid)["name"]
        if QMessageBox.question(self, "Quitar juego",
                                f"Se purgarán los mods de «{name}» y se quitará de la lista.\n"
                                "No se borra el juego ni los mods descargados. ¿Continuar?"
                                ) != QMessageBox.StandardButton.Yes:
            return
        r = self.ctx.remove_game(gid)
        self.notify(report_text("Juego quitado", r), "ok" if r.ok else "warn")


def report_text(what: str, r) -> str:
    bits = []
    if r.added:
        bits.append(f"{r.added} archivos enlazados")
    if r.removed:
        bits.append(f"{r.removed} quitados")
    if r.backed_up:
        bits.append(f"{r.backed_up} originales guardados")
    if r.restored:
        bits.append(f"{r.restored} originales restaurados")
    if r.conflicts:
        bits.append(f"{len(r.conflicts)} conflictos resueltos por prioridad")
    if not bits:
        bits.append("todo estaba al día")
    text = f"{what}: " + ", ".join(bits) + "."
    if r.warnings:
        text += f" Avisos: {r.warnings[0]}" + (f" (+{len(r.warnings) - 1})" if len(r.warnings) > 1 else "")
    if r.errors:
        text += f" Errores: {r.errors[0]}" + (f" (+{len(r.errors) - 1})" if len(r.errors) > 1 else "")
    return text
