"""Buscador global: una búsqueda, todas las webs, con panel de filtros de contenido."""
from __future__ import annotations

import html
from datetime import datetime

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QRadioButton,
                               QScrollArea, QSizePolicy, QSplitter, QTabBar, QTableWidget, QTextBrowser,
                               QVBoxLayout, QWidget)

from dataclasses import replace

from ..sources import CATEGORIES, SORT_LABELS, AdultFilter, ModInfo, SearchFilterOptions, SortOrder
from ..sources.base import parse_date
from ..sources.platforms import PLATFORM_NAMES, PLATFORMS, is_compatible, pick_file
from ..sources.platforms import label as platform_label
from .context import POLICY_LABELS
from .game_picker import GamePicker
from ..sources import GameRef
from .theme import COLORS, SOURCE_NAMES
from .widgets import (GameCombo, SortKeyItem, button, human_count, human_size, open_url, page_header,
                      policy_badge, source_badge, spawn, text_item)

THUMB = QSize(64, 36)
DETAIL_IMG_H = 190

CATEGORY_SHORT = {"ui": "Interfaz", "cosmetic": "Skins", "gameplay": "Jugabilidad", "overhaul": "Overhauls",
                  "fixes": "Correcciones", "graphics": "Gráficos", "audio": "Audio", "qol": "Calidad de vida",
                  "content": "Contenido nuevo", "characters": "Personajes", "library": "Librerías",
                  "tools": "Herramientas", "performance": "Rendimiento"}

UPDATED_OPTIONS = [("Cualquier fecha", None), ("Último mes", 30), ("Últimos 3 meses", 90),
                   ("Últimos 6 meses", 180), ("Último año", 365)]


PLATFORM_CHOICES = [("Automática (la de tu juego)", "auto"), ("Todas las versiones", None),
                    *[(PLATFORM_NAMES[p], p) for p in PLATFORMS]]


class FilterPanel(QFrame):
    """Equivalente al panel «Content options» de Nexus, pero para todas las webs."""

    changed = Signal()            # cualquier cambio del usuario (la vista vuelve a buscar sola)

    def __init__(self, initial: SearchFilterOptions):
        super().__init__()
        self.setObjectName("FilterPanel")
        grid = QGridLayout(self)
        grid.setContentsMargins(16, 12, 16, 12)
        grid.setHorizontalSpacing(28)

        title = QLabel("Contenido adulto")
        title.setObjectName("SectionTitle")
        grid.addWidget(title, 0, 0)
        self.adult_group = QButtonGroup(self)
        for i, (text, value) in enumerate([("Ocultar contenido adulto", AdultFilter.HIDE),
                                           ("Mostrar solo contenido adulto", AdultFilter.ONLY),
                                           ("Mostrar todo", AdultFilter.ALL)]):
            rb = QRadioButton(text)
            rb.setProperty("value", value.value)
            self.adult_group.addButton(rb, i)
            grid.addWidget(rb, i + 1, 0)
        note = QLabel("Se aplica en la propia web cuando lo permite (Nexus, Thunderstore) y además "
                      "se filtra en ModHub. En webs sin marca de adulto puede escaparse algo.")
        note.setWordWrap(True)
        note.setMaximumWidth(330)
        note.setObjectName("Muted")
        note.setStyleSheet("font-size: 8.5pt;")
        grid.addWidget(note, 4, 0)

        t2 = QLabel("Estado y compatibilidad")
        t2.setObjectName("SectionTitle")
        grid.addWidget(t2, 0, 1)
        self.auto_only = QCheckBox("Solo instalación automática")
        self.auto_only.setToolTip("Solo mods que ModHub puede descargar e instalar sin pasar por la web")
        grid.addWidget(self.auto_only, 1, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("Actualizados:"))
        self.updated = QComboBox()
        for text, days in UPDATED_OPTIONS:
            self.updated.addItem(text, days)
        row.addWidget(self.updated, 1)
        grid.addLayout(row, 2, 1)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Versión del juego:"))
        self.game_version = QLineEdit()
        self.game_version.setPlaceholderText("p. ej. 1.21.1 (Modrinth/CurseForge)")
        row2.addWidget(self.game_version, 1)
        grid.addLayout(row2, 3, 1)
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Mi versión:"))
        self.platform = QComboBox()
        self.platform.setToolTip("Oculta los mods que el autor marca solo para otra versión del juego "
                                 "(p. ej. «Steam only» si tienes la de Xbox / Game Pass).")
        for text, value in PLATFORM_CHOICES:
            self.platform.addItem(text, value)
        row3.addWidget(self.platform, 1)
        grid.addLayout(row3, 4, 1)

        t3 = QLabel("Categorías")
        t3.setObjectName("SectionTitle")
        grid.addWidget(t3, 0, 2)
        cats = QWidget()
        cl = QGridLayout(cats)
        cl.setContentsMargins(0, 0, 0, 0)
        self.cat_boxes: dict[str, QCheckBox] = {}
        for i, (key, label) in enumerate(CATEGORIES.items()):
            cb = QCheckBox(CATEGORY_SHORT.get(key, label))
            cb.setToolTip(label)
            self.cat_boxes[key] = cb
            cl.addWidget(cb, i // 3, i % 3)
        grid.addWidget(cats, 1, 2, 4, 1)
        grid.setColumnStretch(2, 1)

        self.reset_btn = button("Restablecer", self.reset)
        grid.addWidget(self.reset_btn, 5, 2, Qt.AlignmentFlag.AlignRight)
        self._silent = False
        self.set_options(initial)
        self.adult_group.buttonToggled.connect(lambda _b, on: on and self._emit())
        self.auto_only.toggled.connect(self._emit)
        self.updated.currentIndexChanged.connect(self._emit)
        self.game_version.editingFinished.connect(self._emit)
        self.platform.currentIndexChanged.connect(self._emit)
        for cb in self.cat_boxes.values():
            cb.toggled.connect(self._emit)

    def _emit(self, *_):
        if not self._silent:
            self.changed.emit()

    def options(self) -> SearchFilterOptions:
        checked = self.adult_group.checkedButton()
        adult = AdultFilter(checked.property("value")) if checked else AdultFilter.HIDE
        return SearchFilterOptions(
            adult=adult, auto_install_only=self.auto_only.isChecked(), updated_within_days=self.updated.currentData(),
            game_version=self.game_version.text().strip() or None,
            categories=frozenset(k for k, cb in self.cat_boxes.items() if cb.isChecked()))

    def set_options(self, o: SearchFilterOptions) -> None:
        self._silent = True
        for b in self.adult_group.buttons():
            b.setChecked(b.property("value") == o.adult.value)
        self.auto_only.setChecked(o.auto_install_only)
        self.updated.setCurrentIndex(max(0, self.updated.findData(o.updated_within_days)))
        self.game_version.setText(o.game_version or "")
        for k, cb in self.cat_boxes.items():
            cb.setChecked(k in o.categories)
        self._silent = False

    def reset(self) -> None:
        self.set_options(SearchFilterOptions())
        self.platform.setCurrentIndex(0)
        self.changed.emit()

    def platform_choice(self) -> str | None:
        """'auto' (según el juego elegido), None (todas) o una plataforma concreta."""
        return self.platform.currentData()

    def summary(self) -> str:
        o = self.options()
        bits = []
        if o.adult is AdultFilter.ONLY:
            bits.append("solo adulto")
        elif o.adult is AdultFilter.ALL:
            bits.append("incluye adulto")
        if o.auto_install_only:
            bits.append("solo automáticos")
        if o.updated_within_days:
            bits.append(f"≤{o.updated_within_days} días")
        if o.game_version:
            bits.append(f"v{o.game_version}")
        if o.categories:
            bits.append(f"{len(o.categories)} categorías")
        if self.platform_choice() not in ("auto", None):
            bits.append(f"solo {PLATFORM_NAMES[self.platform_choice()]}")
        return ", ".join(bits)


class SearchView(QWidget):
    COLS = ["Nombre", "Fuente", "Instalación", "Autor", "Descargas", "Publicado", "Actualizado"]

    def __init__(self, ctx, notify, flow):
        super().__init__()
        self.setObjectName("Page")
        self.ctx, self.notify, self.flow = ctx, notify, flow
        self.by_source: dict[str, list[ModInfo]] = {}     # resultados acumulados (todas las páginas)
        self.view: list[ModInfo] = []                     # lo que enseña la tabla (según la pestaña)
        self.page = 1
        self.current: ModInfo | None = None
        self._search_task = None
        self._gen = 0                                     # nº de búsqueda: descarta respuestas viejas
        self._last = None                                 # último SearchResults (para el texto de estado)
        self._refilter = QTimer(self)
        self._refilter.setSingleShot(True)
        self._refilter.setInterval(450)
        self._refilter.timeout.connect(self._on_filters_changed)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(page_header("Buscador global",
                                  "Busca en Nexus, Thunderstore, Modrinth, CurseForge, mod.io, GameBanana y AyakaMods a la vez. "
                                  "Elige uno de tus juegos o escribe cualquier otro."))

        bar = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setObjectName("SearchBox")
        self.query.setPlaceholderText("Buscar…  (vacío = todos los mods del juego)")
        self.query.returnPressed.connect(self.search)
        bar.addWidget(self.query, 1)
        self.game = GamePicker(ctx, allow_none=True)
        self.game.setMinimumWidth(260)
        self.game.setToolTip("Elige uno de tus juegos o ESCRIBE cualquier otro para ver sus mods")
        self.game.currentIndexChanged.connect(lambda _i: self._auto_browse())
        self._catalog_links: dict[str, dict[str, str]] = {}   # juegos no instalados ya enlazados por nombre
        bar.addWidget(self.game)
        self.sort = QComboBox()
        self.sort.setToolTip("Recomendado = lo que mejor encaja con lo que escribes; sin texto, lo más popular ahora")
        for order, label in SORT_LABELS.items():
            self.sort.addItem(label, order.value)
        self.sort.setCurrentIndex(max(0, self.sort.findData(ctx.default_filters.sort.value)))
        self.sort.currentIndexChanged.connect(lambda _i: self._resort())
        bar.addWidget(QLabel("Ordenar:"))
        bar.addWidget(self.sort)
        self.filter_btn = button("⚙  Filtros", tooltip="Opciones de contenido")
        self.filter_btn.setCheckable(True)
        self.filter_btn.toggled.connect(lambda on: self.filters.setVisible(on))
        bar.addWidget(self.filter_btn)
        self.search_btn = button("Buscar", self.search, kind="Primary")
        bar.addWidget(self.search_btn)
        lay.addLayout(bar)

        self.filters = FilterPanel(ctx.default_filters)
        self.filters.setVisible(False)
        self.filters.changed.connect(self._refilter.start)
        lay.addWidget(self.filters)

        self.status = QLabel("Elige un juego para ver todos sus mods, o escribe algo y pulsa Buscar.")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        # Pestañas por web: «Todas» reparte los puestos entre webs para que ninguna tape a las demás
        self.tabs = QTabBar()
        self.tabs.setObjectName("SourceTabs")
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.addTab("Todas")
        self.tabs.setTabData(0, None)
        self.tabs.currentChanged.connect(lambda _i: self._render())
        lay.addWidget(self.tabs)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(THUMB.height() + 8)
        self.table.setIconSize(THUMB)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c, width in zip(range(1, len(self.COLS)), (100, 92, 100, 86, 90, 90)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(c, width)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setSortingEnabled(True)
        hh.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)      # orden inicial = el de «Ordenar»
        self.table.itemSelectionChanged.connect(self._on_select)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.table, 1)
        self.more_btn = button("Cargar más resultados", self.load_more)
        self.more_btn.setVisible(False)
        ll.addWidget(self.more_btn)
        split.addWidget(left)
        split.addWidget(self._build_detail())
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([800, 350])
        lay.addWidget(split, 1)

    # ── panel de detalle ───────────────────────────────────────────────
    def _build_detail(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("DetailPanel")
        panel.setMinimumWidth(320)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(16, 14, 16, 14)
        scroll = QScrollArea()                         # si no cabe todo, el detalle se desplaza
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("DetailInner")
        scroll.setWidget(inner)
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 6, 0)
        outer.addWidget(scroll, 1)
        self.d_title = QLabel("Selecciona un mod")
        self.d_title.setObjectName("PageTitle")
        self.d_title.setWordWrap(True)
        v.addWidget(self.d_title)
        self.d_meta = QLabel("")
        self.d_meta.setObjectName("Muted")
        self.d_meta.setWordWrap(True)
        v.addWidget(self.d_meta)
        self.d_image = QLabel()
        self.d_image.setObjectName("DetailImage")
        self.d_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.d_image.setFixedHeight(DETAIL_IMG_H)
        self.d_image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.d_image.setStyleSheet(f"background: {COLORS['panel2']}; border-radius: 8px; color: {COLORS['muted']};")
        self.d_image.setText("Sin imagen")
        v.addWidget(self.d_image)
        self.d_gallery = QListWidget()
        self.d_gallery.setObjectName("Gallery")
        self.d_gallery.setViewMode(QListWidget.ViewMode.IconMode)
        self.d_gallery.setFlow(QListWidget.Flow.LeftToRight)
        self.d_gallery.setWrapping(False)
        self.d_gallery.setIconSize(QSize(72, 40))
        self.d_gallery.setFixedHeight(58)
        self.d_gallery.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.d_gallery.setToolTip("Capturas publicadas en la web. Pulsa una para verla en grande.")
        self.d_gallery.currentItemChanged.connect(lambda it, _p: it and self._show_big(it.data(Qt.ItemDataRole.UserRole)))
        self.d_gallery.setVisible(False)
        v.addWidget(self.d_gallery)
        self.d_body = QTextBrowser()
        self.d_body.setOpenExternalLinks(True)
        self.d_body.setMinimumHeight(140)
        v.addWidget(self.d_body, 1)
        v = outer                                       # archivo y botones siempre visibles abajo
        v.addWidget(QLabel("Archivo / versión:"))
        self.d_files = QComboBox()
        v.addWidget(self.d_files)
        row = QHBoxLayout()
        self.install_btn = button("⬇  Instalar", self.install_selected, kind="Primary")
        self.web_btn = button("🌐  Ver en la web", lambda: self.current and open_url(self.current.page_url))
        row.addWidget(self.install_btn, 1)
        row.addWidget(self.web_btn)
        v.addLayout(row)
        self.install_btn.setEnabled(False)
        self.web_btn.setEnabled(False)
        return panel

    # ── búsqueda ───────────────────────────────────────────────────────
    @property
    def results(self) -> list[ModInfo]:
        return [m for lst in self.by_source.values() for m in lst]

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self.by_source and not (self._search_task and not self._search_task.done()):
            self._auto_browse()

    def _has_game(self) -> bool:
        return self.game.current_ref() is not None

    def _auto_browse(self) -> None:
        """Al elegir un juego (sin texto escrito) se muestran directamente sus mods."""
        if self._has_game() and not self.query.text().strip():
            self.search()

    def _resort(self) -> None:
        if self.by_source or (self._has_game() and not self.query.text().strip()):
            self.search()

    def _on_filters_changed(self) -> None:
        if self.by_source or self.query.text().strip() or self._has_game():
            self.search()

    async def _catalog_ref(self, ref: GameRef) -> GameRef:
        """Juego no instalado: completa en qué webs está (por nombre) y lo recuerda."""
        if ref.name not in self._catalog_links:
            found = await self.ctx.registry.link_game(ref.name, dict(ref.ids))
            self._catalog_links[ref.name] = {**found, **ref.ids}
        return GameRef(name=ref.name, ids=dict(self._catalog_links[ref.name]))

    def _current_ref_sync(self) -> GameRef | None:
        ref = self.game.current_ref()
        if ref is not None and self.game.is_virtual and ref.name in self._catalog_links:
            return GameRef(name=ref.name, ids=dict(self._catalog_links[ref.name]))
        return ref

    def _platform(self, gid: int | None) -> str | None:
        choice = self.filters.platform_choice()
        return self.ctx.game_platform(gid) if choice == "auto" else choice

    def search(self) -> None:
        if not self.query.text().strip() and not self._has_game():
            self.status.setText("Escribe qué buscas, o elige un juego para ver todos sus mods.")
            self.query.setFocus()
            return
        self.page = 1
        self._run_search()

    def load_more(self) -> None:
        self.page += 1
        self._run_search()

    def _run_search(self) -> None:
        if self._search_task and not self._search_task.done():
            self._search_task.cancel()
        self._gen += 1
        gen, page = self._gen, self.page
        query = self.query.text().strip()
        gid = self.game.current_game_id()
        virtual = self.game.current_ref() if self.game.is_virtual else None
        filters = replace(self.filters.options(), sort=SortOrder(self.sort.currentData()),
                          platform=self._platform(gid))
        self.ctx.settings.search_filters = filters.to_dict()
        self.search_btn.setEnabled(False)
        order = SORT_LABELS[filters.effective_sort(query)].lower()
        if query:
            self.status.setText(f"Buscando «{query}» · {order}…")
        else:
            self.status.setText(f"Cargando todos los mods de {self.game.currentText()} · {order}…")

        async def run():
            if virtual is not None:                         # juego del catálogo (no instalado)
                ref = await self._catalog_ref(virtual)
                return await self.ctx.registry.search_all(query, ref, page=page, page_size=25, filters=filters)
            if gid is not None and page == 1:
                await self.ctx.ensure_linked(gid)          # encuentra el juego en las webs (1.ª vez)
            ref = self.ctx.game_ref(gid) if gid is not None else None
            return await self.ctx.registry.search_all(query, ref, page=page, page_size=25, filters=filters)

        def done():
            if gen == self._gen:
                self.search_btn.setEnabled(True)

        self._search_task = spawn(
            run(), on_ok=lambda res: gen == self._gen and self._show_results(res, page),
            on_error=lambda e: gen == self._gen and self.status.setText(f"Error en la búsqueda: {e}"),
            on_finally=done)

    def _show_results(self, res, page: int) -> None:
        if page == 1:
            self.by_source = {}
        for src, mods in res.by_source.items():
            have = {m.mod_id for m in self.by_source.get(src, [])}
            self.by_source.setdefault(src, []).extend(m for m in mods if m.mod_id not in have)
        self._last = res
        self.more_btn.setVisible(any(res.by_source.values()))
        self._rebuild_tabs()
        self._render()

    def _rebuild_tabs(self) -> None:
        keep = self.tabs.tabData(self.tabs.currentIndex())
        self.tabs.blockSignals(True)
        while self.tabs.count() > 1:
            self.tabs.removeTab(1)
        self.tabs.setTabText(0, f"Todas ({len(self.results)})")
        for src, mods in sorted(self.by_source.items(), key=lambda kv: -len(kv[1])):
            if mods:
                i = self.tabs.addTab(f"{SOURCE_NAMES.get(src, src)} ({len(mods)})")
                self.tabs.setTabData(i, src)
        idx = next((i for i in range(self.tabs.count()) if self.tabs.tabData(i) == keep), 0)
        self.tabs.setCurrentIndex(idx)
        self.tabs.blockSignals(False)

    def _balanced(self) -> list[ModInfo]:
        """Reparte los puestos por turnos entre webs: el 1.º de cada una, luego el 2.º…"""
        lists = [l for l in self.by_source.values() if l]
        out: list[ModInfo] = []
        for i in range(max((len(l) for l in lists), default=0)):
            out += [l[i] for l in lists if i < len(l)]
        return out

    def _render(self) -> None:
        src = self.tabs.tabData(self.tabs.currentIndex())
        self.view = self._balanced() if src is None else list(self.by_source.get(src, []))
        self.table.setSortingEnabled(False)
        self.table.clearContents()
        self.table.setRowCount(len(self.view))
        for r, mod in enumerate(self.view):
            name = text_item(mod.name, data=r, tooltip=mod.summary)
            if mod.is_adult:
                name.setText(f"🔞 {mod.name}")
                name.setForeground(Qt.GlobalColor.magenta)
            if mod.platforms or mod.excluded_platforms:
                tag = platform_label(mod.platforms) or ("no " + platform_label(mod.excluded_platforms))
                name.setText(f"{name.text()}   [{tag}]")
                name.setToolTip(f"{mod.summary}\n\nVersión del juego: {tag}".strip())
            if (pix := self.ctx.images.cached(mod.thumbnail)) is not None:
                name.setIcon(QIcon(pix))
            self.table.setItem(r, 0, name)
            self.table.setItem(r, 1, source_badge(mod.source))
            self.table.setItem(r, 2, policy_badge(mod.install_support, POLICY_LABELS))
            self.table.setItem(r, 3, text_item(mod.author))
            self.table.setItem(r, 4, SortKeyItem(human_count(mod.downloads), mod.downloads or 0))
            for col, value in ((5, mod.created_at), (6, mod.updated_at)):
                when = parse_date(value)
                self.table.setItem(r, col, SortKeyItem(when.strftime("%d/%m/%Y") if when else "—",
                                                       when.timestamp() if when else 0))
        self.table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.table.setSortingEnabled(True)
        self._load_thumbnails(self._gen)
        self._update_status()

    def _load_thumbnails(self, gen: int) -> None:
        for mod in self.view:
            if mod.thumbnail and self.ctx.images.cached(mod.thumbnail) is None:
                spawn(self.ctx.images.load(mod.thumbnail),
                      on_ok=lambda pix, m=mod: pix is not None and gen == self._gen and self._set_thumb(m, pix))

    def _set_thumb(self, mod: ModInfo, pix) -> None:
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            idx = item.data(Qt.ItemDataRole.UserRole) if item else None
            if idx is not None and idx < len(self.view) and self.view[idx] is mod:
                item.setIcon(QIcon(pix))
                return

    def _update_status(self) -> None:
        res = self._last
        if res is None:
            return
        what = f"«{res.query}»" if res.query else "todos los mods"
        bits = [f"{len(self.results)} resultados de {what} · {SORT_LABELS[res.sort]}"]
        if res.filtered_out:
            bits.append(f"{sum(res.filtered_out.values())} ocultos por filtros")
        for src, err in res.errors.items():
            bits.append(f"⚠ {SOURCE_NAMES.get(src, src)}: {err[:90]}")
        empty = [SOURCE_NAMES.get(s, s) for s, v in res.by_source.items() if not v and s not in self.by_source]
        if empty:
            bits.append("Sin resultados en: " + ", ".join(empty))
        by_name = [SOURCE_NAMES.get(s, s) for s in sorted(getattr(res, "by_name", ())) if res.by_source.get(s)]
        if by_name:
            bits.append("Buscado por el nombre del juego en: " + ", ".join(by_name))
        skipped = [f"{SOURCE_NAMES.get(s, s)}: {why.rstrip('.')}" for s, why in res.skipped.items()]
        if skipped:
            bits.append("Sin consultar — " + "; ".join(skipped))
        if fs := self.filters.summary():
            bits.append(f"Filtros: {fs}")
        self.status.setText("  ·  ".join(bits))

    # ── detalle ────────────────────────────────────────────────────────
    def _selected_mod(self) -> ModInfo | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        idx = item.data(Qt.ItemDataRole.UserRole) if item else None
        return self.view[idx] if idx is not None and idx < len(self.view) else None

    def _on_select(self) -> None:
        mod = self._selected_mod()
        if mod is None:
            return
        self.current = mod
        self.d_title.setText(mod.name)
        cats = ", ".join(CATEGORY_SHORT.get(c, c) for c in mod.categories) or "sin categoría"
        self.d_meta.setText(f"{SOURCE_NAMES.get(mod.source, mod.source)} · por {mod.author or 'desconocido'} · "
                            f"{human_count(mod.downloads)} descargas · {cats}")
        self.d_body.setHtml(f"<p>{html.escape(mod.summary or '')}</p><p style='color:{COLORS['muted']}'>"
                            "Cargando detalles…</p>")
        self.d_files.clear()
        self.install_btn.setEnabled(False)
        self._install_hint(has_files=None)
        self.web_btn.setEnabled(bool(mod.page_url))
        self._show_gallery(mod, mod.images or ([mod.thumbnail] if mod.thumbnail else []))
        ref = self._current_ref_sync()
        if mod.game_id and (ref is None or not ref.id_for(mod.source)):   # p. ej. búsqueda sin juego elegido
            ref = GameRef(name=ref.name if ref else mod.game_id,
                          ids={**(ref.ids if ref else {}), mod.source: mod.game_id})
        spawn(self.ctx.registry.get(mod.source).get_mod_details(mod.mod_id, ref),
              on_ok=lambda info, m=mod: self._show_details(m, info),
              on_error=lambda e, m=mod: self.current is m and self.d_body.setHtml(
                  f"<p>{html.escape(m.summary or '')}</p><p style='color:{COLORS['warn']}'>"
                  f"{html.escape(str(e))}</p>"))

    def _install_hint(self, *, has_files: bool | None) -> None:
        """Explica por qué no se puede instalar (si es el caso)."""
        if self.game.is_virtual:
            tip = "Este juego no está en tu lista: añádelo en «Juegos» para instalar sus mods."
        elif self.game.current_game_id() is None:
            tip = "Elige primero uno de tus juegos en el selector de arriba."
        elif has_files is None:
            tip = "Cargando los archivos del mod…"
        elif not has_files:
            tip = "No se han podido cargar los archivos de este mod (¿falta la clave de la web en Ajustes?)."
        else:
            tip = ""
        self.install_btn.setToolTip(tip)

    # ── imágenes del detalle ────────────────────────────────────────────
    def _show_gallery(self, mod: ModInfo, urls: list[str]) -> None:
        urls = list(dict.fromkeys(u for u in urls if u))[:12]
        self._gallery_urls = urls
        self.d_gallery.clear()
        self.d_gallery.setVisible(len(urls) > 1)
        if not urls:
            self.d_image.setPixmap(QIcon().pixmap(1, 1))
            self.d_image.setText("Sin imagen")
            return
        self._show_big(urls[0])
        if len(urls) > 1:
            for u in urls:
                it = QListWidgetItem()
                it.setData(Qt.ItemDataRole.UserRole, u)
                it.setSizeHint(QSize(78, 46))
                self.d_gallery.addItem(it)
                spawn(self.ctx.images.load(u),
                      on_ok=lambda pix, item=it, m=mod: pix is not None and self.current is m and item.setIcon(QIcon(pix)))

    def _show_big(self, url: str | None) -> None:
        mod = self.current

        def put(pix):
            if pix is None or self.current is not mod:
                return
            w = max(200, self.d_image.width() - 8)
            self.d_image.setPixmap(pix.scaled(w, DETAIL_IMG_H - 8, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
        if (pix := self.ctx.images.cached(url)) is not None:
            put(pix)
            return
        self.d_image.setPixmap(QIcon().pixmap(1, 1))
        self.d_image.setText("Cargando imagen…")
        spawn(self.ctx.images.load(url), on_ok=lambda p: put(p) if p is not None else (
            self.current is mod and self.d_image.setText("Sin imagen")))

    def _show_details(self, mod: ModInfo, info: ModInfo) -> None:
        if self.current is not mod:
            return
        body = info.description or info.summary or mod.summary or ""
        if "<" not in body:
            body = "<p>" + html.escape(body).replace("\n", "<br>") + "</p>"
        policy = mod.install_support
        label, tip = POLICY_LABELS.get(policy, ("—", ""))
        self.d_body.setHtml(f"<p><b>Instalación:</b> {label} — {tip}</p>{body[:20000]}")
        gallery = list(dict.fromkeys([*info.images, *mod.images, *([mod.thumbnail] if mod.thumbnail else [])]))
        if gallery != getattr(self, "_gallery_urls", None):
            self._show_gallery(mod, gallery)
        plat = self._platform(self.game.current_game_id())
        best = pick_file(info.files, plat)
        for f in info.files:
            date = parse_date(f.uploaded_at)
            text = f"{f.name or f.filename}  {('v' + f.version) if f.version and f.version not in f.name else ''}"
            text += f"  ·  {human_size(f.size)}" + (f"  ·  {date:%d/%m/%Y}" if date else "")
            if f.platforms:
                text += f"  ·  [{platform_label(f.platforms)}]"
            if plat and not is_compatible(f.platforms, f.excluded_platforms, plat):
                text = "⚠ " + text + "  (otra versión del juego)"
            self.d_files.addItem(text.strip(), f.file_id)
        if best is not None:
            self.d_files.setCurrentIndex(max(0, self.d_files.findData(best.file_id)))
        installed = self.game.current_game_id() is not None
        self.install_btn.setEnabled(bool(info.files) and installed)
        self._install_hint(has_files=bool(info.files))

    # ── instalar ───────────────────────────────────────────────────────
    def install_selected(self) -> None:
        mod, gid, file_id = self.current, self.game.current_game_id(), self.d_files.currentData()
        if not mod or gid is None or not file_id:
            return
        self.install_btn.setEnabled(False)
        self.notify(f"Instalando «{mod.name}»…", "info")
        self.ctx.events.intercept_event.emit(f"Instalación pedida: {mod.name} ({SOURCE_NAMES.get(mod.source)})")
        spawn(self.ctx.install(gid, mod.source, mod.mod_id, file_id),
              on_ok=lambda r: self.flow.handle_result(r, gid, mod.name),
              on_error=lambda e: self.notify(f"No se pudo instalar «{mod.name}»: {e}", "error"),
              on_finally=lambda: self.install_btn.setEnabled(True))
