"""Diálogos: asistente FOMOD, revisión de un plan de instalación y elegir juego para un enlace."""
from __future__ import annotations

from pathlib import Path, PurePosixPath

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QRadioButton, QScrollArea,
                               QSplitter, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core.extractor import FileMapping, InstallPlan
from ..core.fomod import FomodSession, Selections
from ..games.base import GameProfile
from .theme import COLORS
from .widgets import button


# ════════════════════════════════════════════════════════════════════════
#  Asistente FOMOD
# ════════════════════════════════════════════════════════════════════════
class FomodDialog(QDialog):
    """Paso a paso con las opciones del instalador. Al aceptar, `self.plan` tiene el InstallPlan."""

    TYPE_HINTS = {"Required": "Obligatorio", "Recommended": "Recomendado", "NotUsable": "No disponible",
                  "CouldBeUsable": "Podría funcionar"}

    def __init__(self, session: FomodSession, profile: GameProfile, game_root: str, parent=None):
        super().__init__(parent)
        self.session, self.profile, self.game_root = session, profile, game_root
        self.selections: Selections = {}
        self.plan: InstallPlan | None = None
        self.history: list[int] = []
        cfg = session.config
        self.setWindowTitle(f"Instalador: {cfg.module_name}")
        self.resize(980, 640)

        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        self.title = QLabel(cfg.module_name)
        self.title.setObjectName("PageTitle")
        head.addWidget(self.title, 1)
        self.step_label = QLabel("")
        self.step_label.setObjectName("Muted")
        head.addWidget(self.step_label)
        lay.addLayout(head)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        split.addWidget(self.scroll)
        side = QFrame()
        side.setObjectName("DetailPanel")
        sl = QVBoxLayout(side)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumHeight(220)
        self.image.setStyleSheet(f"background: {COLORS['panel2']}; border-radius: 8px;")
        sl.addWidget(self.image)
        self.desc = QTextBrowser()
        sl.addWidget(self.desc, 1)
        split.addWidget(side)
        split.setSizes([560, 420])
        lay.addWidget(split, 1)

        self.errors = QLabel("")
        self.errors.setStyleSheet(f"color: {COLORS['error']};")
        self.errors.setWordWrap(True)
        lay.addWidget(self.errors)
        bar = QHBoxLayout()
        bar.addStretch(1)
        self.back_btn = button("◀ Atrás", self.back)
        self.next_btn = button("Siguiente ▶", self.next, kind="Primary")
        bar.addWidget(self.back_btn)
        bar.addWidget(self.next_btn)
        bar.addWidget(button("Cancelar", self.reject))
        lay.addLayout(bar)

        self._show_image(cfg.image)
        steps = session.visible_steps(self.selections)
        if not steps:                       # FOMOD sin pasos: solo archivos obligatorios
            self._finish()
        else:
            self.current = steps[0]
            self._render()

    # ── navegación ─────────────────────────────────────────────────────
    def _remaining(self) -> list[int]:
        return [s for s in self.session.visible_steps(self.selections) if s > self.current]

    def _render(self) -> None:
        if (self.current, 0) not in self.selections:
            self.selections.update(self.session.default_for_step(self.current, self.selections))
        step = self.session.config.steps[self.current]
        visible = self.session.visible_steps(self.selections)
        self.step_label.setText(f"Paso {visible.index(self.current) + 1} de {len(visible)} · {step.name}")
        page = QWidget()
        page.setObjectName("ScrollBody")
        pl = QVBoxLayout(page)
        for g, group in enumerate(step.groups):
            pl.addWidget(self._group_box(g, group))
        pl.addStretch(1)
        self.scroll.setWidget(page)
        self.back_btn.setEnabled(bool(self.history))
        self.next_btn.setText("Instalar" if not self._remaining() else "Siguiente ▶")
        self.errors.clear()

    def _group_box(self, g: int, group) -> QGroupBox:
        hints = {"SelectExactlyOne": "elige una", "SelectAtMostOne": "elige una o ninguna",
                 "SelectAtLeastOne": "elige al menos una", "SelectAll": "todas incluidas", "SelectAny": "elige las que quieras"}
        box = QGroupBox(f"{group.name}  ({hints[group.type]})")
        bl = QVBoxLayout(box)
        exclusive = group.type in ("SelectExactlyOne", "SelectAtMostOne")
        btn_group = QButtonGroup(box)
        btn_group.setExclusive(False)
        chosen = self.selections.get((self.current, g), set())
        for i, plugin in enumerate(group.plugins):
            ptype = self.session.plugin_type(self.current, g, i, self.selections)
            text = plugin.name + (f"   [{self.TYPE_HINTS[ptype]}]" if ptype in self.TYPE_HINTS else "")
            w: QAbstractButton = QRadioButton(text) if exclusive else QCheckBox(text)
            w.setChecked(i in chosen)
            w.setEnabled(ptype not in ("Required", "NotUsable") and group.type != "SelectAll")
            w.setProperty("idx", i)
            w.toggled.connect(lambda on, gg=g, ii=i, ex=exclusive, bg=btn_group: self._toggle(gg, ii, on, ex, bg))
            w.installEventFilter(self)
            w.setProperty("plugin", (g, i))
            btn_group.addButton(w)
            bl.addWidget(w)
        return box

    def eventFilter(self, obj, event):           # al pasar el ratón: imagen y descripción de la opción
        if event.type() == event.Type.Enter and obj.property("plugin") is not None:
            g, i = obj.property("plugin")
            self._describe(self.session.config.steps[self.current].groups[g].plugins[i])
        return super().eventFilter(obj, event)

    def _toggle(self, g: int, i: int, on: bool, exclusive: bool, btn_group: QButtonGroup) -> None:
        key = (self.current, g)
        chosen = set(self.selections.get(key, set()))
        if on:
            if exclusive:
                chosen = {i}
                for b in btn_group.buttons():
                    if b.property("idx") != i and b.isChecked():
                        b.blockSignals(True)
                        b.setChecked(False)
                        b.blockSignals(False)
            else:
                chosen.add(i)
        else:
            chosen.discard(i)
        self.selections[key] = chosen
        self._describe(self.session.config.steps[self.current].groups[g].plugins[i])
        self.next_btn.setText("Instalar" if not self._remaining() else "Siguiente ▶")

    def _describe(self, plugin) -> None:
        self.desc.setPlainText(plugin.description or plugin.name)
        self._show_image(plugin.image)

    def _show_image(self, rel: str | None) -> None:
        if rel:
            path = self.session._resolve(rel)
            if path and path.is_file():
                pix = QPixmap(str(path))
                if not pix.isNull():
                    self.image.setPixmap(pix.scaled(400, 260, Qt.AspectRatioMode.KeepAspectRatio,
                                                    Qt.TransformationMode.SmoothTransformation))
                    return
        self.image.setPixmap(QPixmap())
        self.image.setText("Sin imagen")

    def next(self) -> None:
        errors = self.session.validate_step(self.current, self.selections)
        if errors:
            self.errors.setText("  ".join(errors))
            return
        remaining = self._remaining()
        if not remaining:
            self._finish()
            return
        self.history.append(self.current)
        self.current = remaining[0]
        self._render()

    def back(self) -> None:
        if self.history:
            self.current = self.history.pop()
            self._render()

    def _finish(self) -> None:
        # Las selecciones de pasos que acabaron ocultos no cuentan
        visible = set(self.session.visible_steps(self.selections))
        sel = {k: v for k, v in self.selections.items() if k[0] in visible}
        self.plan = self.session.build_plan(sel, self.profile, self.game_root)
        review = PlanReviewDialog(self.plan, self, title="Resumen de la instalación")
        if review.exec() == QDialog.DialogCode.Accepted:
            self.plan = review.plan
            self.accept()


# ════════════════════════════════════════════════════════════════════════
#  Revisión de un plan
# ════════════════════════════════════════════════════════════════════════
class PlanReviewDialog(QDialog):
    """Muestra qué archivo va a dónde. Permite anteponer una carpeta si el analizador no acertó."""

    def __init__(self, plan: InstallPlan, parent=None, *, title: str = "Revisar instalación"):
        super().__init__(parent)
        self.original = plan
        self.plan = plan
        self.setWindowTitle(title)
        self.resize(820, 600)
        lay = QVBoxLayout(self)
        conf = QLabel(f"Regla: <b>{plan.rule}</b> · confianza {plan.confidence:.0%} · "
                      f"{len(plan.mappings)} archivos se instalarán · {len(plan.ignored)} se ignoran")
        lay.addWidget(conf)
        for w in plan.warnings:
            l = QLabel(f"⚠ {w}")
            l.setWordWrap(True)
            l.setStyleSheet(f"color: {COLORS['warn']};")
            lay.addWidget(l)
        if plan.missing_loaders:
            l = QLabel("Falta: " + ", ".join(x.name for x in plan.missing_loaders) +
                       ". Puedes instalar el mod, pero no funcionará hasta instalarlo.")
            l.setStyleSheet(f"color: {COLORS['warn']};")
            lay.addWidget(l)

        row = QHBoxLayout()
        row.addWidget(QLabel("Instalar dentro de la subcarpeta:"))
        self.prefix = QLineEdit()
        self.prefix.setPlaceholderText("(vacío = tal cual)  p. ej.  Data   o   BepInEx/plugins")
        self.prefix.textChanged.connect(self._rebuild)
        row.addWidget(self.prefix, 1)
        lay.addLayout(row)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Destino en el juego", "Archivo del mod"])
        self.tree.setColumnWidth(0, 420)
        lay.addWidget(self.tree, 1)
        buttons = QDialogButtonBox()
        ok = buttons.addButton("Instalar así", QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setObjectName("Primary")
        buttons.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._rebuild()

    def _rebuild(self) -> None:
        prefix = self.prefix.text().strip().replace("\\", "/").strip("/")
        if prefix and ".." in PurePosixPath(prefix).parts:
            prefix = ""
        maps = [FileMapping(m.source, PurePosixPath(prefix) / m.target if prefix else m.target)
                for m in self.original.mappings]
        p = self.original
        self.plan = InstallPlan(p.mod_dir, p.rule if not prefix else f"{p.rule}+manual", 1.0 if prefix else p.confidence,
                                maps, p.ignored, [], p.missing_loaders, p.provides_loaders, False)
        self.tree.clear()
        root_items: dict[str, QTreeWidgetItem] = {}
        for m in sorted(maps, key=lambda x: str(x.target).lower()):
            top = m.target.parts[0] if len(m.target.parts) > 1 else "(raíz del juego)"
            parent = root_items.get(top)
            if parent is None:
                parent = QTreeWidgetItem([top + "/" if top != "(raíz del juego)" else top, ""])
                root_items[top] = parent
                self.tree.addTopLevelItem(parent)
            QTreeWidgetItem(parent, [str(m.target), _short(m.source, p.mod_dir)])
        for item in root_items.values():
            item.setExpanded(len(root_items) <= 4)


def _short(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return str(path)


# ════════════════════════════════════════════════════════════════════════
#  Elegir juego (enlaces que encajan con varios)
# ════════════════════════════════════════════════════════════════════════
class ChooseGameDialog(QDialog):
    def __init__(self, games, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("¿Para qué juego?")
        lay = QVBoxLayout(self)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lay.addWidget(lbl)
        self.combo = QComboBox()
        for g in games:
            self.combo.addItem(g["name"], g["id"])
        lay.addWidget(self.combo)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def game_id(self) -> int | None:
        return self.combo.currentData()
