"""Elegir juego escribiendo: autocompletado con los juegos instalados y el catálogo de todas las webs.

- GamePicker: selector del buscador. Se puede elegir un juego instalado o CUALQUIER juego del
  catálogo (aunque no esté instalado) para ver y buscar sus mods.
- GameNameDialog: al añadir una carpeta (portables), confirmar qué juego es con sugerencias reales.
"""
from __future__ import annotations

from PySide6.QtCore import QStringListModel, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCompleter, QDialog, QDialogButtonBox, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QVBoxLayout)

from ..sources import GameRef
from .theme import SOURCE_NAMES
from .widgets import GameCombo, spawn

VIRTUAL = -1          # data del elemento «juego del catálogo (no instalado)»


def describe(ref: GameRef, installed: bool) -> str:
    webs = ", ".join(SOURCE_NAMES.get(s, s) for s in sorted(ref.ids) if s != "steam")
    tag = "✔ instalado" if installed else (webs or "catálogo")
    return f"{ref.name}   ·   {tag}"


class _CatalogSearch:
    """Pide el catálogo con un pequeño retardo mientras se escribe y entrega las sugerencias."""

    def __init__(self, ctx, on_results, delay_ms: int = 350):
        self.ctx, self.on_results = ctx, on_results
        self._gen = 0
        self._text = ""
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.setInterval(delay_ms)
        self.timer.timeout.connect(self._run)

    def request(self, text: str) -> None:
        self._text = text.strip()
        self.timer.start()

    def _run(self) -> None:
        if len(self._text) < 2:
            return
        self._gen += 1
        gen, text = self._gen, self._text
        spawn(self.ctx.search_game_catalog(text),
              on_ok=lambda refs: gen == self._gen and self.on_results(text, refs))


class GamePicker(GameCombo):
    """Combo editable: lista de juegos instalados + autocompletado de cualquier juego."""

    def __init__(self, ctx, **kw):
        self._virtual: GameRef | None = None
        super().__init__(ctx, **kw)
        self.setEditable(True)
        self.setInsertPolicy(GameCombo.InsertPolicy.NoInsert)
        self.lineEdit().setPlaceholderText("Escribe un juego…")
        self._refs: dict[str, GameRef] = {}
        self._model = QStringListModel()
        self._completer = QCompleter(self._model, self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self._completer.setMaxVisibleItems(12)
        self.setCompleter(self._completer)
        self._completer.activated[str].connect(self._chosen)
        self._search = _CatalogSearch(ctx, self._show)
        self.lineEdit().textEdited.connect(self._search.request)

    # --- datos ------------------------------------------------------------
    def reload(self) -> None:
        super().reload()
        if self._virtual is not None:
            self._add_virtual(self._virtual, select=self.currentData() == VIRTUAL)

    def current_game_id(self) -> int | None:
        data = self.currentData()
        return None if data in (None, VIRTUAL) else data

    def current_ref(self) -> GameRef | None:
        """El juego elegido, esté instalado o no."""
        if self.currentData() == VIRTUAL:
            return self._virtual
        gid = self.current_game_id()
        return self.ctx.game_ref(gid) if gid is not None else None

    @property
    def is_virtual(self) -> bool:
        return self.currentData() == VIRTUAL and self._virtual is not None

    # --- autocompletado ---------------------------------------------------
    def _show(self, text: str, refs: list[GameRef]) -> None:
        if self.lineEdit().text().strip() != text:
            return                                       # el usuario siguió escribiendo
        installed = {g["name"].lower(): g["id"] for g in self.ctx.games()}
        self._refs = {}
        labels = []
        for r in refs:
            label = describe(r, r.name.lower() in installed or bool(r.slug))
            self._refs[label] = r
            labels.append(label)
        self._model.setStringList(labels)
        self._completer.setCompletionPrefix("")
        if labels:
            self._completer.complete()

    def _chosen(self, label: str) -> None:
        ref = self._refs.get(label)
        if ref is None:
            return
        for i in range(self.count()):                    # ¿es uno de los instalados?
            data = self.itemData(i)
            if data not in (None, VIRTUAL) and self.itemText(i).lower() == ref.name.lower():
                QTimer.singleShot(0, lambda i=i: self.setCurrentIndex(i))
                return
        self.choose_catalog_game(ref)

    def choose_catalog_game(self, ref: GameRef) -> None:
        """Elige un juego que no está instalado (solo para ver y buscar sus mods)."""
        self._virtual = ref
        QTimer.singleShot(0, lambda: self._add_virtual(ref, select=True))

    def _add_virtual(self, ref: GameRef, *, select: bool) -> None:
        idx = self.findData(VIRTUAL)
        text = f"🌐 {ref.name}"
        if idx < 0:
            self.addItem(text, VIRTUAL)
            idx = self.count() - 1
        else:
            self.setItemText(idx, text)
        if select:
            if self.currentIndex() == idx:
                self.currentIndexChanged.emit(idx)
            else:
                self.setCurrentIndex(idx)
            self.lineEdit().setText(text)


class GameNameDialog(QDialog):
    """«¿Qué juego es?» al añadir una carpeta: sugerencias reales para enlazarlo bien con las webs."""

    picked = Signal(object)

    def __init__(self, ctx, initial: str, folder: str, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.ref: GameRef | None = None
        self.setWindowTitle("¿Qué juego es?")
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        info = QLabel(f"Carpeta: {folder}\n\nEscribe el nombre del juego y elige el correcto de la lista. "
                      "Así ModHub sabrá dónde buscar sus mods.")
        info.setWordWrap(True)
        lay.addWidget(info)
        self.name = QLineEdit(initial)
        self.name.setPlaceholderText("Nombre del juego")
        lay.addWidget(self.name)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        self.hint = QLabel("Buscando…")
        self.hint.setObjectName("Muted")
        lay.addWidget(self.hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Añadir")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._search = _CatalogSearch(ctx, self._show, delay_ms=300)
        self.name.textEdited.connect(self._search.request)
        self.list.currentItemChanged.connect(self._on_item)
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        from ..sources.base import strip_edition
        self._search.request(strip_edition(initial) or initial)

    def _show(self, text: str, refs: list[GameRef]) -> None:
        self.list.clear()
        for r in refs:
            if r.slug:                                  # ya instalado: no se ofrece otra vez
                continue
            it = QListWidgetItem(describe(r, False))
            it.setData(Qt.ItemDataRole.UserRole, r)
            self.list.addItem(it)
        self.hint.setText("Elige el juego de la lista (o deja el nombre tal cual)." if self.list.count()
                          else "No se ha encontrado en ninguna web: se añadirá con el nombre escrito.")
        if self.list.count():
            self.list.setCurrentRow(0)

    def _on_item(self, item, _prev) -> None:
        self.ref = item.data(Qt.ItemDataRole.UserRole) if item else None

    def result_name_and_ids(self) -> tuple[str, dict[str, str]]:
        if self.ref is not None:
            return self.ref.name, dict(self.ref.ids)
        return self.name.text().strip(), {}
