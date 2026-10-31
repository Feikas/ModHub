"""Ajustes: claves de las webs, protocolos, instalación, contenido y fuentes."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ..settings import ALL_SOURCES
from ..sources import AdultFilter, SearchFilterOptions
from .protocol import ProtocolRegistrar
from .theme import COLORS, SOURCE_NAMES
from .widgets import button, open_url, page_header, spawn

KEY_FIELDS = [
    ("nexus", "api_key", "Nexus Mods — API key", "https://www.nexusmods.com/users/myaccount?tab=api",
     "Buscar funciona sin ella; hace falta para ver archivos e instalar. Con cuenta gratuita las descargas pasan por el botón de la web."),
    ("curseforge", "api_key", "CurseForge — API key", "https://console.curseforge.com/",
     "Gratuita: crea una cuenta de desarrollador en la consola de CurseForge."),
    ("modio", "api_key", "mod.io — API key", "https://mod.io/me/access", "Gratuita, en tu perfil de mod.io."),
]


class SettingsView(QWidget):
    def __init__(self, ctx, notify, registrar: ProtocolRegistrar | None = None):
        super().__init__()
        self.setObjectName("Page")
        self.ctx, self.notify = ctx, notify
        self.registrar = registrar or ProtocolRegistrar()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        save = button("💾  Guardar ajustes", self.save, kind="Primary")
        outer.addWidget(page_header("Ajustes", f"Datos de ModHub en: {ctx.data_dir}", save))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("Page")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 8, 0)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # ── Claves ──
        keys = QGroupBox("Cuentas y claves")
        kf = QFormLayout(keys)
        self.key_edits: dict[tuple[str, str], QLineEdit] = {}
        for source, name, label, url, hint in KEY_FIELDS:
            row = QHBoxLayout()
            edit = QLineEdit(ctx.store.get_secret(source, name))
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            edit.setPlaceholderText("Pega aquí tu clave")
            edit.setToolTip(hint)
            self.key_edits[(source, name)] = edit
            row.addWidget(edit, 1)
            row.addWidget(button("Conseguir…", lambda _=False, u=url: open_url(u)))
            if source == "nexus":
                row.addWidget(button("Comprobar", self.check_nexus))
            kf.addRow(label, row)
        self.nexus_status = QLabel("")
        self.nexus_status.setObjectName("Muted")
        kf.addRow("", self.nexus_status)
        if not ctx.store.keyring_available:
            warn = QLabel("⚠ El Administrador de credenciales no está disponible: las claves se guardarán en settings.json.")
            warn.setStyleSheet(f"color: {COLORS['warn']};")
            kf.addRow(warn)
        lay.addWidget(keys)

        # ── Protocolos ──
        proto = QGroupBox("Enlaces desde el navegador")
        pl = QGridLayout(proto)
        self.proto_labels: dict[str, QLabel] = {}
        rows = [("modhub", "modhub://", "Enlaces propios de ModHub (GameBanana 1-Click, etc.)."),
                ("nxm", "nxm://", "Botón «Mod Manager Download» de Nexus. Si usas Vortex/MO2, ModHub guarda su "
                                  "configuración y se la devuelve al desactivarlo.")]
        for r, (scheme, title, hint) in enumerate(rows):
            pl.addWidget(QLabel(f"<b>{title}</b>"), r, 0)
            status = QLabel("")
            self.proto_labels[scheme] = status
            pl.addWidget(status, r, 1)
            pl.addWidget(button("Usar ModHub", lambda _=False, s=scheme: self.set_protocol(s, True)), r, 2)
            pl.addWidget(button("Dejar de usar", lambda _=False, s=scheme: self.set_protocol(s, False)), r, 3)
            h = QLabel(hint)
            h.setObjectName("Muted")
            h.setWordWrap(True)
            pl.addWidget(h, r, 4)
        pl.setColumnStretch(4, 1)
        if not self.registrar.available:
            n = QLabel("Solo disponible en Windows.")
            n.setObjectName("Muted")
            pl.addWidget(n, 2, 0, 1, 5)
        lay.addWidget(proto)

        # ── Instalación ──
        inst = QGroupBox("Instalación")
        f = QFormLayout(inst)
        self.deploy_method = QComboBox()
        for value, text in (("hardlink", "Hardlink (recomendado: 0 bytes extra)"),
                            ("symlink", "Symlink (requiere Modo de desarrollador)"),
                            ("copy", "Copia (ocupa el doble)")):
            self.deploy_method.addItem(text, value)
        self.deploy_method.setCurrentIndex(max(0, self.deploy_method.findData(ctx.settings.deploy_method)))
        f.addRow("Método para juegos nuevos:", self.deploy_method)
        self.seven_zip = self._path_row(f, "7-Zip (7z.exe):", ctx.settings.seven_zip,
                                        "Vacío = detectarlo solo", file=True)
        self.downloads_folder = self._path_row(f, "Carpeta Descargas:", ctx.settings.downloads_folder,
                                               "Vacío = tu carpeta Descargas")
        self.watch = QCheckBox("Vigilar la carpeta Descargas para recoger mods bajados desde la web")
        self.watch.setChecked(ctx.settings.watch_downloads)
        f.addRow("", self.watch)
        self.concurrent = QSpinBox()
        self.concurrent.setRange(1, 8)
        self.concurrent.setValue(ctx.settings.max_concurrent_downloads)
        f.addRow("Descargas simultáneas:", self.concurrent)
        engine = ctx.extractor.seven_zip
        e = QLabel(f"Motor de extracción: {'7-Zip (' + str(engine) + ')' if engine else 'Python (sin .rar)'}")
        e.setObjectName("Muted")
        f.addRow("", e)
        lay.addWidget(inst)

        # ── Contenido ──
        content = QGroupBox("Contenido")
        cf = QFormLayout(content)
        self.adult_default = QComboBox()
        for value, text in ((AdultFilter.HIDE, "Ocultar contenido adulto"), (AdultFilter.ONLY, "Solo contenido adulto"),
                            (AdultFilter.ALL, "Mostrar todo")):
            self.adult_default.addItem(text, value.value)
        self.adult_default.setCurrentIndex(max(0, self.adult_default.findData(ctx.default_filters.adult.value)))
        cf.addRow("Filtro por defecto:", self.adult_default)
        lay.addWidget(content)

        # ── Fuentes ──
        src = QGroupBox("Webs activas en el buscador")
        sg = QGridLayout(src)
        self.source_boxes: dict[str, QCheckBox] = {}
        for i, s in enumerate(ALL_SOURCES):
            cb = QCheckBox(SOURCE_NAMES.get(s, s))
            cb.setChecked(s in ctx.settings.enabled_sources)
            self.source_boxes[s] = cb
            sg.addWidget(cb, i // 5, i % 5)
        lay.addWidget(src)
        lay.addStretch(1)
        self.refresh_protocols()

    def _path_row(self, form: QFormLayout, label: str, value: str, placeholder: str, *, file: bool = False) -> QLineEdit:
        row = QHBoxLayout()
        edit = QLineEdit(value)
        edit.setPlaceholderText(placeholder)
        row.addWidget(edit, 1)

        def pick():
            if file:
                path, _ = QFileDialog.getOpenFileName(self, label, "", "7z.exe (7z.exe);;Todos (*.*)")
            else:
                path = QFileDialog.getExistingDirectory(self, label)
            if path:
                edit.setText(path)
        row.addWidget(button("…", pick))
        form.addRow(label, row)
        return edit

    # ── acciones ───────────────────────────────────────────────────────
    def refresh_protocols(self) -> None:
        for scheme, label in self.proto_labels.items():
            if not self.registrar.available:
                label.setText("—")
            elif self.registrar.is_ours(scheme):
                label.setText(f"<span style='color:{COLORS['ok']}'>✔ ModHub</span>")
            elif self.registrar.current_handler(scheme):
                label.setText(f"<span style='color:{COLORS['warn']}'>Otro programa</span>")
            else:
                label.setText("Sin asignar")

    def set_protocol(self, scheme: str, enable: bool) -> None:
        try:
            if enable:
                self.registrar.register(scheme)
                self.notify(f"ModHub abrirá ahora los enlaces {scheme}://", "ok")
            else:
                self.notify(self.registrar.unregister(scheme), "info")
        except OSError as e:
            self.notify(f"No se pudo cambiar {scheme}://: {e}", "error")
        self.refresh_protocols()

    def check_nexus(self) -> None:
        self.save(quiet=True)
        if "nexus" not in self.ctx.registry:
            self.nexus_status.setText("Nexus está desactivado en «Webs activas».")
            return
        self.nexus_status.setText("Comprobando…")
        nexus = self.ctx.registry.get("nexus")
        spawn(nexus.validate(force=True),
              on_ok=lambda u: self.nexus_status.setText(
                  f"✔ Conectado como {u.name} · {'Premium (descargas directas)' if u.is_premium else 'cuenta gratuita'}"),
              on_error=lambda e: self.nexus_status.setText(f"✖ {e}"))

    def save(self, quiet: bool = False) -> None:
        s = self.ctx.settings
        for (source, name), edit in self.key_edits.items():
            self.ctx.store.set_secret(source, name, edit.text())
        s.deploy_method = self.deploy_method.currentData()
        s.seven_zip = self.seven_zip.text().strip()
        s.downloads_folder = self.downloads_folder.text().strip()
        s.watch_downloads = self.watch.isChecked()
        s.max_concurrent_downloads = self.concurrent.value()
        filters = SearchFilterOptions.from_dict(s.search_filters)
        filters.adult = AdultFilter(self.adult_default.currentData())
        s.search_filters = filters.to_dict()
        s.enabled_sources = [k for k, cb in self.source_boxes.items() if cb.isChecked()]
        self.ctx.save_settings()
        if not quiet:
            self.notify("Ajustes guardados.", "ok")
