"""Ajustes de ModHub.

- Lo normal se guarda en %APPDATA%\\ModHub\\settings.json.
- Las claves (API keys) van al Administrador de credenciales de Windows mediante `keyring`.
  Si keyring no está disponible, se guardan en settings.json y se avisa en Ajustes.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("modhub.settings")

KEYRING_SERVICE = "ModHub"
# (fuente, nombre del ajuste) que son secretos
SECRETS: tuple[tuple[str, str], ...] = (
    ("nexus", "api_key"), ("curseforge", "api_key"), ("modio", "api_key"))
ALL_SOURCES = ("nexus", "modrinth", "thunderstore", "curseforge", "modio", "gamebanana", "moddb", "ayakamods")
# Mod DB está detrás de la protección anti-bots de Cloudflare: casi siempre responde 403 a las apps.
OFF_BY_DEFAULT = ("moddb",)
CONFIG_VERSION = 2


def app_dir() -> Path:
    """Carpeta donde está ModHub.exe (o el código, si se ejecuta con Python)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def is_portable() -> bool:
    """Versión portable: hay un archivo «portable.txt» junto a ModHub.exe."""
    return (app_dir() / "portable.txt").is_file()


def default_data_dir() -> Path:
    if env := os.environ.get("MODHUB_DATA_DIR"):
        return Path(env)
    if is_portable():
        return app_dir() / "Datos"           # todo viaja con la carpeta (USB, otro PC…)
    try:
        from platformdirs import user_data_dir
        return Path(user_data_dir("ModHub", appauthor=False, roaming=True))
    except ImportError:
        base = os.environ.get("APPDATA") or Path.home() / ".local" / "share"
        return Path(base) / "ModHub"


def default_downloads_dir() -> Path:
    return Path.home() / "Downloads"


@dataclass
class Settings:
    enabled_sources: list[str] = field(default_factory=lambda: [s for s in ALL_SOURCES if s not in OFF_BY_DEFAULT])
    source_options: dict[str, dict[str, Any]] = field(default_factory=dict)   # ajustes no secretos por fuente
    search_filters: dict[str, Any] = field(default_factory=lambda: {"adult": "hide"})
    deploy_method: str = "hardlink"
    staging_overrides: dict[str, str] = field(default_factory=dict)
    seven_zip: str = ""
    max_concurrent_downloads: int = 3
    downloads_folder: str = ""                 # vacío = carpeta Descargas del usuario
    watch_downloads: bool = True
    nexus_app_slug: str = ""                   # solo si Nexus aprueba la app (SSO)
    last_game_id: int | None = None
    ignored_games: list[str] = field(default_factory=list)    # «tienda:id» que el usuario quitó: no volver a añadir
    portable_dirs: list[str] = field(default_factory=list)    # carpetas con juegos portables (se miran al detectar)
    secrets_fallback: dict[str, str] = field(default_factory=dict)   # solo si no hay keyring
    config_version: int = 0


class SettingsStore:
    def __init__(self, data_dir: Path | str | None = None, *, use_keyring: bool = True):
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "settings.json"
        self.use_keyring = use_keyring
        self.settings = self._load()
        self._migrate()

    def _migrate(self) -> None:
        """Ajustes de versiones anteriores de ModHub."""
        s = self.settings
        if s.config_version < 2:
            # v0.4: Steam Workshop retirado (no da descargas fiables) y Mod DB desactivado (Cloudflare)
            s.enabled_sources = [x for x in s.enabled_sources if x not in ("steam", *OFF_BY_DEFAULT)]
            s.source_options.pop("steam", None)
            try:
                self.set_secret("steam", "web_api_key", "")          # borra la clave de Steam guardada
            except Exception:  # noqa: BLE001
                pass
            s.secrets_fallback.pop("steam.web_api_key", None)
        if s.config_version < CONFIG_VERSION:
            s.config_version = CONFIG_VERSION
            try:
                self.save()
            except OSError:
                pass

    # --- archivo ----------------------------------------------------------
    def _load(self) -> Settings:
        if not self.path.is_file():
            return Settings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("settings.json dañado; se usan los valores por defecto.")
            return Settings()
        known = {k: v for k, v in raw.items() if k in Settings.__dataclass_fields__}
        return Settings(**known)

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self.settings), indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    # --- secretos -----------------------------------------------------------
    def _keyring(self):
        if not self.use_keyring:
            return None
        try:
            import keyring
            keyring.get_keyring()
            return keyring
        except Exception:  # noqa: BLE001
            return None

    @property
    def keyring_available(self) -> bool:
        return self._keyring() is not None

    def get_secret(self, source: str, name: str) -> str:
        key = f"{source}.{name}"
        if kr := self._keyring():
            try:
                return kr.get_password(KEYRING_SERVICE, key) or ""
            except Exception:  # noqa: BLE001
                pass
        return self.settings.secrets_fallback.get(key, "")

    def set_secret(self, source: str, name: str, value: str) -> None:
        key = f"{source}.{name}"
        value = value.strip()
        if kr := self._keyring():
            try:
                if value:
                    kr.set_password(KEYRING_SERVICE, key, value)
                else:
                    try:
                        kr.delete_password(KEYRING_SERVICE, key)
                    except Exception:  # noqa: BLE001
                        pass
                self.settings.secrets_fallback.pop(key, None)
                return
            except Exception:  # noqa: BLE001
                log.warning("No se pudo usar el Administrador de credenciales; la clave se guarda en el archivo.")
        if value:
            self.settings.secrets_fallback[key] = value
        else:
            self.settings.secrets_fallback.pop(key, None)

    # --- para el registro de fuentes ---------------------------------------
    def source_settings(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for src in ALL_SOURCES:
            conf = dict(self.settings.source_options.get(src, {}))
            conf["enabled"] = src in self.settings.enabled_sources
            out[src] = conf
        for src, name in SECRETS:
            if value := self.get_secret(src, name):
                out[src][name] = value
        out["nexus"]["use_keyring"] = False          # la clave ya la gestiona este módulo
        return out
