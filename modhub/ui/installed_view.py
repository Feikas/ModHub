"""Mods instalados de un juego: activar/desactivar en vivo, prioridad, revisar, desinstalar limpio."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QTableWidget, QVBoxLayout, QWidget)

from .games_view import report_text
from .theme import COLORS
from .widgets import GameCombo, badge_item, button, page_header, play_game, source_badge, spawn, text_item

STATE_LABELS = {
    "enabled": ("Activo", COLORS["ok"]), "disabled": ("Desactivado", COLORS["muted"]),
    "installed": ("Instalado", COLORS["muted"]), "needs_review": ("Revisar", COLORS["warn"]),
    "downloading": ("Descargando", COLORS["info"]), "downloaded": ("Descargado", COLORS["muted"]),
    "error": ("Error", COLORS["error"]),
}


class InstalledView(QWidget):
    COLS = ["Activo", "Prioridad", "Nombre", "Versión", "Fuente", "Estado", "Instalación", "Conflictos"]

    def __init__(self, ctx, notify, flow):
        super().__init__()
        self.setObjectName("Page")
        self.ctx, self.notify, self.flow = ctx, notify, flow
        self._busy = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)

        self.game = GameCombo(ctx)
        self.game.currentIndexChanged.connect(lambda _i: self.refresh())
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(self.game)
        hl.addWidget(button("▶  Jugar", lambda: play_game(self.ctx, self.game.current_game_id(), self.notify, self),
                            kind="Primary", tooltip="Abre el juego con los mods activados"))
        lay.addWidget(page_header("Mods instalados",
                                  "Activa o desactiva mods al instante. Más prioridad = gana si dos mods tocan "
                                  "el mismo archivo.", head))

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        for c in range(len(self.COLS)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.cellDoubleClicked.connect(lambda *_: self.review())
        lay.addWidget(self.table, 1)

        self.empty = QLabel("Este juego aún no tiene mods. Búscalos en el Buscador global o instala un archivo.")
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.empty)

        bar = QHBoxLayout()
        self.up_btn = button("▲ Subir", lambda: self.move(+1), tooltip="Más prioridad")
        self.down_btn = button("▼ Bajar", lambda: self.move(-1), tooltip="Menos prioridad")
        self.review_btn = button("🔎  Revisar…", self.review, tooltip="Ver o cambiar dónde se instala (y opciones FOMOD)")
        self.uninstall_btn = button("🗑  Desinstalar limpio", self.uninstall, kind="Danger")
        for b in (self.up_btn, self.down_btn, self.review_btn, self.uninstall_btn):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(button("📦  Instalar archivo…", self.install_file,
                             tooltip="Instala un .zip/.7z/.rar que ya tengas descargado"))
        bar.addWidget(button("✔  Comprobar", self.verify, tooltip="Comprueba que los enlaces siguen en su sitio"))
        bar.addWidget(button("🔗  Desplegar", self.deploy))
        lay.addLayout(bar)

        ctx.events.mods_changed.connect(lambda gid: gid == self.game.current_game_id() and self.refresh())
        self.refresh()

    # ── datos ──────────────────────────────────────────────────────────
    def selected_mod_id(self) -> int | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return self.table.item(rows[0].row(), 2).data(Qt.ItemDataRole.UserRole) if rows else None

    def refresh(self) -> None:
        gid = self.game.current_game_id()
        keep = self.selected_mod_id()
        mods = self.ctx.mods(gid) if gid is not None else []
        conflicts = self.ctx.conflicts(gid) if gid is not None else {}
        self.table.setRowCount(len(mods))
        for r, m in enumerate(mods):
            box = QCheckBox()
            box.setChecked(m["state"] == "enabled")
            box.setEnabled(m["state"] in ("enabled", "disabled", "installed"))
            box.setToolTip("Activar / desactivar (crea o quita los enlaces en el juego)")
            box.toggled.connect(lambda on, mid=m["id"]: self.toggle(mid, on))
            cell = QWidget()
            cl = QHBoxLayout(cell)
            cl.setContentsMargins(12, 0, 0, 0)
            cl.addWidget(box)
            self.table.setCellWidget(r, 0, cell)
            self.table.setItem(r, 1, text_item(m["priority"], align_right=True))
            self.table.setItem(r, 2, text_item(m["name"], data=m["id"], tooltip=m["error"] or m["page_url"] or ""))
            self.table.setItem(r, 3, text_item(m["version"] or "—"))
            self.table.setItem(r, 4, source_badge(m["source"]))
            label, color = STATE_LABELS.get(m["state"], (m["state"], COLORS["muted"]))
            self.table.setItem(r, 5, badge_item(label, color, m["error"] or ""))
            conf = m["confidence"]
            rule = m["install_rule"] or "—"
            self.table.setItem(r, 6, text_item(f"{rule}" + (f" ({conf:.0%})" if conf is not None else ""),
                                               tooltip="Regla usada para decidir las carpetas y su confianza"))
            n = conflicts.get(m["id"], 0)
            self.table.setItem(r, 7, badge_item(f"{n} archivos" if n else "—", COLORS["warn"] if n else COLORS["muted"],
                                                "Archivos que también trae otro mod; gana el de más prioridad"))
        self.empty.setVisible(not mods)
        self.table.setVisible(bool(mods))
        if keep is not None:
            for r in range(self.table.rowCount()):
                if self.table.item(r, 2).data(Qt.ItemDataRole.UserRole) == keep:
                    self.table.selectRow(r)
        self._update_buttons()

    def _update_buttons(self) -> None:
        has = self.selected_mod_id() is not None and not self._busy
        for b in (self.up_btn, self.down_btn, self.review_btn, self.uninstall_btn):
            b.setEnabled(has)

    def _run(self, coro, what: str) -> None:
        self._busy = True
        self._update_buttons()

        def done():
            self._busy = False
            self.refresh()
        spawn(coro, on_ok=lambda r: r is not None and self.notify(report_text(what, r), "ok" if r.ok else "warn"),
              on_error=lambda e: self.notify(f"{what}: {e}", "error"), on_finally=done)

    # ── acciones ───────────────────────────────────────────────────────
    def toggle(self, mod_id: int, enabled: bool) -> None:
        gid = self.game.current_game_id()
        self._run(self.ctx.run_deploy(gid, self.ctx.installer.set_enabled, mod_id, enabled),
                  "Mod activado" if enabled else "Mod desactivado")

    def move(self, direction: int) -> None:
        """Intercambia la prioridad con el vecino de arriba (+1) o de abajo (-1)."""
        mid, gid = self.selected_mod_id(), self.game.current_game_id()
        mods = self.ctx.mods(gid)
        idx = next((i for i, m in enumerate(mods) if m["id"] == mid), None)
        other = idx - direction if idx is not None else None       # la lista va de mayor a menor prioridad
        if idx is None or other is None or not 0 <= other < len(mods):
            return
        a, b = mods[idx], mods[other]
        pa, pb = a["priority"], b["priority"]
        if pa == pb:
            pa, pb = (pb + 1, pb) if direction > 0 else (pb - 1, pb)
        self.ctx.db.execute("UPDATE mods SET priority=? WHERE id=?", (pa, b["id"]))
        self._run(self.ctx.run_deploy(gid, self.ctx.installer.set_priority, a["id"], pb), "Prioridad cambiada")

    def review(self) -> None:
        if (mid := self.selected_mod_id()) is not None:
            self.flow.review_cb(mid)

    def uninstall(self) -> None:
        mid, gid = self.selected_mod_id(), self.game.current_game_id()
        if mid is None:
            return
        name = self.ctx.db.one("SELECT name FROM mods WHERE id=?", (mid,))["name"]
        if QMessageBox.question(self, "Desinstalar", f"¿Desinstalar «{name}»?\n\nSe quitarán sus enlaces del juego, "
                                "se restaurarán los archivos originales que tapaba y se borrará de ModHub."
                                ) != QMessageBox.StandardButton.Yes:
            return
        self._run(self.ctx.run_deploy(gid, self.ctx.installer.uninstall, mid), f"«{name}» desinstalado")

    def deploy(self) -> None:
        gid = self.game.current_game_id()
        if gid is not None:
            self._run(self.ctx.run_deploy(gid, self.ctx.deployer.deploy_game, gid), "Despliegue")

    def verify(self) -> None:
        gid = self.game.current_game_id()
        if gid is None:
            return
        issues = self.ctx.deployer.verify_game(gid)
        if not issues:
            self.notify("Todo correcto: cada archivo desplegado sigue en su sitio.", "ok")
        else:
            self.notify(f"{len(issues)} problemas: " + "; ".join(issues[:3]) + (" …" if len(issues) > 3 else "")
                        + " Pulsa «Desplegar» para repararlo.", "warn")

    def install_file(self) -> None:
        gid = self.game.current_game_id()
        if gid is None:
            self.notify("Elige primero un juego.", "warn")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Archivo del mod", "",
                                              "Mods (*.zip *.7z *.rar *.tar *.gz *.dll *.jar *.pak *.esp *.esm);;"
                                              "Todos (*.*)")
        if not path:
            return
        spawn(self.ctx.install_archive(gid, path),
              on_ok=lambda r: self.flow.handle_result(r, gid, Path(path).stem),
              on_error=lambda e: self.notify(f"No se pudo instalar: {e}", "error"))
