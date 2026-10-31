"""Piezas reutilizables de la interfaz."""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import traceback
from typing import Any, Awaitable, Callable

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QStyle, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .theme import COLORS, POLICY_COLORS, SOURCE_COLORS, SOURCE_NAMES

log = logging.getLogger("modhub.ui")

_RUNNING: set[asyncio.Task] = set()       # evita que el recolector de basura cancele tareas


def spawn(coro: Awaitable[Any], *, on_ok: Callable[[Any], None] | None = None,
          on_error: Callable[[BaseException], None] | None = None,
          on_finally: Callable[[], None] | None = None) -> asyncio.Task:
    """Lanza una corrutina desde la UI (botones, señales) y llama a los callbacks al acabar."""
    task = asyncio.ensure_future(coro)
    _RUNNING.add(task)

    def done(t: asyncio.Task) -> None:
        _RUNNING.discard(t)
        try:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                log.error("Tarea fallida: %s", "".join(traceback.format_exception(exc)))
                if on_error:
                    on_error(exc)
            elif on_ok:
                on_ok(t.result())
        finally:
            if on_finally:
                on_finally()
    task.add_done_callback(done)
    return task


def human_size(n: float | None) -> str:
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_count(n: int | None) -> str:
    if n is None:
        return "—"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} M"
    if n >= 1_000:
        return f"{n / 1_000:.1f} k"
    return str(n)


def human_eta(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = int(seconds)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {(s % 3600) // 60:02d} min"


def open_url(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


def open_folder(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def run_command(cmd: list[str]) -> None:
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen(cmd, creationflags=flags)


# ─── Insignias en tablas ───────────────────────────────────────────────
def badge_item(text: str, color: str, tooltip: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(f" {text} ")
    item.setForeground(QColor(color))
    item.setToolTip(tooltip)
    f = item.font()
    f.setBold(True)
    item.setFont(f)
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


def source_badge(source: str) -> QTableWidgetItem:
    return badge_item(SOURCE_NAMES.get(source, source), SOURCE_COLORS.get(source, COLORS["muted"]))


def policy_badge(policy, labels: dict) -> QTableWidgetItem:
    text, tip = labels.get(policy, ("—", ""))
    color = POLICY_COLORS.get(getattr(policy, "value", ""), COLORS["muted"])
    return badge_item(text, color, tip)


def text_item(text: Any, *, align_right: bool = False, tooltip: str = "", data: Any = None) -> QTableWidgetItem:
    item = QTableWidgetItem("" if text is None else str(text))
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if align_right:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if tooltip:
        item.setToolTip(tooltip)
    if data is not None:
        item.setData(Qt.ItemDataRole.UserRole, data)
    return item


class SortKeyItem(QTableWidgetItem):
    """Celda que se ordena por un valor numérico distinto del texto que muestra."""

    def __init__(self, text: str, key: float):
        super().__init__(text)
        self.key = key
        self.setFlags(self.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def __lt__(self, other):  # noqa: D401
        return self.key < getattr(other, "key", 0)


# ─── Cabecera de página ────────────────────────────────────────────────
def page_header(title: str, subtitle: str = "", right: QWidget | None = None) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 8)
    col = QVBoxLayout()
    t = QLabel(title)
    t.setObjectName("PageTitle")
    col.addWidget(t)
    if subtitle:
        s = QLabel(subtitle)
        s.setObjectName("PageSubtitle")
        s.setWordWrap(True)
        col.addWidget(s)
    lay.addLayout(col, 1)
    if right is not None:
        lay.addWidget(right, 0, Qt.AlignmentFlag.AlignBottom)
    return w


def button(text: str, slot=None, *, kind: str = "", tooltip: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if tooltip:
        b.setToolTip(tooltip)
    if slot:
        b.clicked.connect(slot)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def icon_button(icon: QStyle.StandardPixmap, slot=None, tooltip: str = "") -> QPushButton:
    """Botón pequeño con un icono estándar del sistema (se ve igual en cualquier PC)."""
    b = button("", slot, kind="Icon", tooltip=tooltip)
    b.setIcon(b.style().standardIcon(icon))
    return b


def play_game(ctx, game_id: int | None, notify, parent=None) -> None:
    """Botón «▶ Jugar»: abre el juego; si no se sabe qué .exe abrir, lo pregunta y lo recuerda."""
    from PySide6.QtWidgets import QFileDialog
    if game_id is None:
        return
    plan = ctx.launch_plan(game_id)
    game = ctx.game(game_id)
    if plan.kind == "missing":
        exe, _ = QFileDialog.getOpenFileName(parent, f"¿Qué ejecutable abre «{game['name']}»?",
                                             game["install_path"], "Ejecutables (*.exe);;Todos (*.*)")
        if not exe:
            return
        ctx.set_game_exe(game_id, exe)
    try:
        used = ctx.launch_game(game_id)
        notify(f"Abriendo «{game['name']}» con {used.label}…", "ok")
    except Exception as e:  # noqa: BLE001
        notify(f"No se pudo abrir «{game['name']}»: {e}", "error")


# ─── Selector de juego compartido ──────────────────────────────────────
class GameCombo(QComboBox):
    """Combo de juegos que se recarga solo cuando cambia la lista."""

    def __init__(self, ctx, *, allow_none: bool = False, none_text: str = "Sin juego (búsqueda general)"):
        super().__init__()
        self.ctx = ctx
        self.allow_none = allow_none
        self.none_text = none_text
        self.setMinimumWidth(200)
        ctx.events.games_changed.connect(self.reload)
        self.reload()

    def reload(self) -> None:
        current = self.current_game_id()
        self.blockSignals(True)
        self.clear()
        if self.allow_none:
            self.addItem(self.none_text, None)
        for g in self.ctx.games():
            self.addItem(g["name"], g["id"])
        target = current if current is not None else self.ctx.settings.last_game_id
        idx = self.findData(target)
        if idx >= 0:
            self.setCurrentIndex(idx)
        self.blockSignals(False)
        self.currentIndexChanged.emit(self.currentIndex())

    def current_game_id(self) -> int | None:
        return self.currentData()

    def select_game(self, game_id: int) -> None:
        idx = self.findData(game_id)
        if idx >= 0:
            self.setCurrentIndex(idx)


# ─── Avisos (banners) ──────────────────────────────────────────────────
class Banner(QFrame):
    ICONS = {"ok": "✔", "warn": "⚠", "error": "✖", "info": "ℹ"}

    def __init__(self, text: str, level: str = "info", actions=None, timeout_ms: int = 9000, parent=None):
        super().__init__(parent)
        self.setObjectName("Banner")
        self.setProperty("level", level)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 8, 8)
        icon = QLabel(self.ICONS.get(level, "ℹ"))
        icon.setStyleSheet(f"color: {COLORS.get(level if level != 'info' else 'info', COLORS['info'])};"
                           "font-size: 13pt; font-weight: 700;")
        lay.addWidget(icon)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(label, 1)
        for caption, callback in actions or []:
            b = button(caption, kind="Primary")
            b.clicked.connect(lambda _=False, cb=callback: (cb(), self.close_banner()))
            lay.addWidget(b)
        close = icon_button(QStyle.StandardPixmap.SP_TitleBarCloseButton, None, "Cerrar")
        close.clicked.connect(self.close_banner)
        lay.addWidget(close)
        if timeout_ms and not actions:
            QTimer.singleShot(timeout_ms, self.close_banner)

    def close_banner(self) -> None:
        self.hide()
        self.deleteLater()


class NotificationArea(QWidget):
    MAX = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)

    def show_message(self, text: str, level: str = "info", actions=None) -> None:
        while self.lay.count() >= self.MAX:
            w = self.lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.lay.addWidget(Banner(text, level, actions))
