"""Cola de descargas en tiempo real + registro de interceptaciones (enlaces nxm:// y carpeta Descargas)."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QListWidget, QProgressBar, QSplitter, QStyle,
                               QTableWidget, QVBoxLayout, QWidget)

from ..core.downloader import DownloadState
from .theme import COLORS
from .widgets import (badge_item, button, human_eta, human_size, icon_button, open_folder, page_header, spawn,
                      text_item)

SP = QStyle.StandardPixmap

STATE_TEXT = {
    DownloadState.QUEUED: ("En cola", COLORS["muted"]), DownloadState.RUNNING: ("Descargando", COLORS["info"]),
    DownloadState.PAUSED: ("En pausa", COLORS["warn"]), DownloadState.DONE: ("Completada", COLORS["ok"]),
    DownloadState.FAILED: ("Error", COLORS["error"]), DownloadState.CANCELLED: ("Cancelada", COLORS["muted"]),
}


class DownloadsView(QWidget):
    COLS = ["Archivo", "Progreso", "Velocidad", "Restante", "Tamaño", "Estado", ""]

    def __init__(self, ctx, notify):
        super().__init__()
        self.setObjectName("Page")
        self.ctx, self.notify = ctx, notify
        self.rows: dict[int, int] = {}          # id de tarea -> fila
        self._dirty: set[int] = set()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(page_header("Descargas", "Progreso en tiempo real. Las descargas pausadas o cortadas "
                                               "continúan desde donde se quedaron."))

        split = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 220)
        for c in range(2, len(self.COLS)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        split.addWidget(self.table)

        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 8, 0, 0)
        t = QLabel("Interceptaciones")
        t.setObjectName("SectionTitle")
        bl.addWidget(t)
        hint = QLabel("Enlaces recibidos desde el navegador (nxm://, modhub://) y archivos detectados en Descargas.")
        hint.setObjectName("Muted")
        bl.addWidget(hint)
        self.log = QListWidget()
        bl.addWidget(self.log, 1)
        split.addWidget(box)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)

        bar = QHBoxLayout()
        bar.addStretch(1)
        bar.addWidget(button("Limpiar terminadas", self.clear_finished))
        lay.addLayout(bar)

        ctx.events.download_progress.connect(self._on_progress)
        ctx.events.intercept_event.connect(self.add_log)
        self._timer = QTimer(self)                 # repinta como mucho 4 veces por segundo
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._flush)
        self._timer.start()

    def add_log(self, text: str) -> None:
        self.log.insertItem(0, f"{time.strftime('%H:%M:%S')}   {text}")
        while self.log.count() > 200:
            self.log.takeItem(self.log.count() - 1)

    def active_count(self) -> int:
        return sum(1 for t in self.ctx.downloads.tasks if t.state in (DownloadState.RUNNING, DownloadState.QUEUED))

    def _on_progress(self, task) -> None:
        if task.id not in self.rows:
            self._add_row(task)
        self._dirty.add(task.id)

    def _add_row(self, task) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.rows[task.id] = r
        self.table.setItem(r, 0, text_item(task.label, data=task.id, tooltip=str(task.dest)))
        bar = QProgressBar()
        bar.setRange(0, 1000)
        self.table.setCellWidget(r, 1, bar)
        actions = QWidget()
        al = QHBoxLayout(actions)
        al.setContentsMargins(4, 0, 4, 0)
        pause = icon_button(SP.SP_MediaPause, lambda _=False, i=task.id: self._toggle_pause(i), "Pausar / reanudar")
        cancel = icon_button(SP.SP_DialogCancelButton, lambda _=False, i=task.id: spawn(self.ctx.downloads.cancel(i)),
                             "Cancelar")
        folder = icon_button(SP.SP_DirOpenIcon, lambda _=False, t=task: open_folder(str(t.dest.parent)), "Abrir carpeta")
        for b in (pause, cancel, folder):
            al.addWidget(b)
        actions.setProperty("pause", pause)
        actions.setProperty("cancel", cancel)
        self.table.setCellWidget(r, 6, actions)

    def _toggle_pause(self, task_id: int) -> None:
        t = self.ctx.downloads.get(task_id)
        if t.state in (DownloadState.RUNNING, DownloadState.QUEUED):
            spawn(self.ctx.downloads.pause(task_id))
        elif t.state in (DownloadState.PAUSED, DownloadState.FAILED):
            self.ctx.downloads.resume(task_id)

    def _flush(self) -> None:
        for tid in list(self._dirty):
            self._dirty.discard(tid)
            r = self.rows.get(tid)
            if r is None:
                continue
            t = self.ctx.downloads.get(tid)
            bar: QProgressBar = self.table.cellWidget(r, 1)
            if t.progress is None:
                bar.setRange(0, 0)               # indeterminada
            else:
                bar.setRange(0, 1000)
                bar.setValue(int(t.progress * 1000))
                bar.setFormat(f"{t.progress:.0%}")
            running = t.state is DownloadState.RUNNING
            self.table.setItem(r, 2, text_item(f"{human_size(t.speed)}/s" if running and t.speed else "—",
                                               align_right=True))
            self.table.setItem(r, 3, text_item(human_eta(t.eta_seconds) if running else "—", align_right=True))
            self.table.setItem(r, 4, text_item(human_size(t.total_bytes or t.expected_size), align_right=True))
            label, color = STATE_TEXT[t.state]
            self.table.setItem(r, 5, badge_item(label, color, t.error or ""))
            actions = self.table.cellWidget(r, 6)
            if actions is not None:
                pause = actions.property("pause")
                paused = t.state in (DownloadState.PAUSED, DownloadState.FAILED)
                pause.setIcon(pause.style().standardIcon(SP.SP_MediaPlay if paused else SP.SP_MediaPause))
                pause.setEnabled(t.state not in (DownloadState.DONE, DownloadState.CANCELLED))
                actions.property("cancel").setEnabled(t.state not in (DownloadState.DONE, DownloadState.CANCELLED))
            if t.state is DownloadState.FAILED and t.error:
                self.notify(f"Descarga fallida: {t.label} — {t.error}", "error")

    def clear_finished(self) -> None:
        for tid, r in sorted(self.rows.items(), key=lambda kv: -kv[1]):
            if self.ctx.downloads.get(tid).state in (DownloadState.DONE, DownloadState.CANCELLED):
                self.table.removeRow(r)
                del self.rows[tid]
        self.rows = {self.table.item(r, 0).data(Qt.ItemDataRole.UserRole): r for r in range(self.table.rowCount())}
