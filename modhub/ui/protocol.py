"""Registro de los protocolos URL en Windows (sin permisos de administrador).

Se escribe en HKEY_CURRENT_USER\\Software\\Classes\\<protocolo>, que solo afecta al usuario actual:

    HKCU\\Software\\Classes\\modhub
        (Predeterminado) = "URL:ModHub Protocol"
        URL Protocol     = ""
        DefaultIcon\\(Predeterminado)      = "C:\\…\\ModHub.exe",0
        shell\\open\\command\\(Predeterminado) = "C:\\…\\ModHub.exe" "%1"

nxm:// pertenece a Nexus y suelen tenerlo Vortex o Mod Organizer 2. Antes de quedárnoslo
guardamos el gestor anterior en HKCU\\Software\\ModHub\\Protocols para devolvérselo al desactivarlo.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Protocol

CLASSES = r"Software\Classes"
BACKUP_KEY = r"Software\ModHub\Protocols"
SCHEMES = {
    "modhub": "URL:ModHub Protocol",
    "nxm": "URL:Nexus Mods (ModHub)",
}


class RegistryBackend(Protocol):
    def get(self, path: str, name: str = "") -> str | None: ...
    def set(self, path: str, name: str, value: str) -> None: ...
    def delete_tree(self, path: str) -> None: ...


class WinRegBackend:
    """HKEY_CURRENT_USER real (solo Windows)."""

    def __init__(self):
        import winreg
        self.w = winreg

    def get(self, path, name=""):
        try:
            with self.w.OpenKey(self.w.HKEY_CURRENT_USER, path) as k:
                return str(self.w.QueryValueEx(k, name)[0])
        except OSError:
            return None

    def set(self, path, name, value):
        with self.w.CreateKeyEx(self.w.HKEY_CURRENT_USER, path, 0, self.w.KEY_WRITE) as k:
            self.w.SetValueEx(k, name, 0, self.w.REG_SZ, value)

    def delete_tree(self, path):
        try:
            with self.w.OpenKey(self.w.HKEY_CURRENT_USER, path, 0, self.w.KEY_ALL_ACCESS) as k:
                while True:
                    try:
                        child = self.w.EnumKey(k, 0)
                    except OSError:
                        break
                    self.delete_tree(f"{path}\\{child}")
            self.w.DeleteKey(self.w.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            pass


def launch_command() -> str:
    """Orden que Windows ejecutará al pulsar un enlace. Funciona con el .exe empaquetado o con Python."""
    if getattr(sys, "frozen", False):                       # ModHub.exe (PyInstaller / Nuitka)
        return f'"{sys.executable}" "%1"'
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")               # sin ventana negra de consola
    exe = pythonw if pythonw.exists() else python
    script = Path(__file__).resolve().parents[2] / "run_modhub.pyw"
    return f'"{exe}" "{script}" "%1"'


def icon_path() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}",0'
    return str(Path(__file__).resolve().parent / "assets" / "modhub.ico")


class ProtocolRegistrar:
    def __init__(self, backend: RegistryBackend | None = None, command: str | None = None):
        if backend is None and os.name == "nt":
            backend = WinRegBackend()
        self.backend = backend
        self.command = command or launch_command()

    @property
    def available(self) -> bool:
        return self.backend is not None

    def current_handler(self, scheme: str) -> str | None:
        if not self.backend:
            return None
        return self.backend.get(rf"{CLASSES}\{scheme}\shell\open\command")

    def is_ours(self, scheme: str) -> bool:
        return (self.current_handler(scheme) or "").strip().lower() == self.command.strip().lower()

    def register(self, scheme: str) -> None:
        if not self.backend:
            raise OSError("El registro de protocolos solo está disponible en Windows.")
        previous = self.current_handler(scheme)
        if previous and not self.is_ours(scheme):
            self.backend.set(BACKUP_KEY, f"{scheme}_previous", previous)   # p. ej. el de Vortex
        base = rf"{CLASSES}\{scheme}"
        self.backend.set(base, "", SCHEMES.get(scheme, f"URL:{scheme}"))
        self.backend.set(base, "URL Protocol", "")
        self.backend.set(rf"{base}\DefaultIcon", "", icon_path())
        self.backend.set(rf"{base}\shell\open\command", "", self.command)

    def unregister(self, scheme: str) -> str:
        """Quita ModHub como gestor. Si había otro (Vortex/MO2), se le devuelve. Devuelve un texto para la UI."""
        if not self.backend or not self.is_ours(scheme):
            return "ModHub no era el gestor de este protocolo."
        previous = self.backend.get(BACKUP_KEY, f"{scheme}_previous")
        if previous:
            self.backend.set(rf"{CLASSES}\{scheme}\shell\open\command", "", previous)
            return "Se ha devuelto el protocolo al programa que lo tenía antes."
        self.backend.delete_tree(rf"{CLASSES}\{scheme}")
        return "Protocolo eliminado."
