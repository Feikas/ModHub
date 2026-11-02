"""Contexto de la aplicación: crea y conecta todos los servicios (Fases 2 y 3) y avisa a la UI con señales Qt."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from PySide6.QtCore import QObject, Signal

from ..core import (Deployer, DeployReport, DownloadManager, Extractor, GameDetector, InstallResult,
                    ModAnalyzer, ModInstaller, StagingManager, save_detected)
from ..core.fomod import FomodSession, data_file_state, load_fomod
from ..db.database import Database
from ..games import get_profile
from ..games.base import ModLoader
from ..settings import SettingsStore
from ..sources import (DownloadPolicy, GameRef, HttpClient, ProtocolRequest, SearchFilterOptions,
                       SourceRegistry)

log = logging.getLogger("modhub.app")


class AppEvents(QObject):
    """Señales globales. La UI se suscribe; el núcleo nunca importa Qt."""

    download_progress = Signal(object)          # DownloadTask
    mods_changed = Signal(int)                  # game_id
    games_changed = Signal()
    notify = Signal(str, str, object)           # texto, nivel (info|ok|warn|error), acciones [(texto, callable)]
    intercept_event = Signal(str)               # texto para el registro de interceptaciones
    navigate = Signal(str)                      # nombre de vista


@dataclass
class ProtocolMatch:
    request: ProtocolRequest
    games: list[Any]                            # filas de games donde podría instalarse


class AppContext:
    def __init__(self, data_dir: Path | str | None = None, *, http_transport=None, use_keyring: bool = True,
                 detector: GameDetector | None = None):
        self.store = SettingsStore(data_dir, use_keyring=use_keyring)
        self.settings = self.store.settings
        self.data_dir = self.store.data_dir
        self.events = AppEvents()
        self.db = Database(self.data_dir / "modhub.db")
        self.staging = StagingManager(self.data_dir, overrides=self.settings.staging_overrides)
        self.http = HttpClient(transport=http_transport)
        self.downloads = DownloadManager(self.http, max_concurrent=self.settings.max_concurrent_downloads,
                                         on_progress=self.events.download_progress.emit)
        self.extractor = Extractor(self.settings.seven_zip or None)
        self.deployer = Deployer(self.db)
        self.detector = detector or GameDetector()
        self.registry: SourceRegistry
        self.installer: ModInstaller
        from .images import ImageLoader
        self.images = ImageLoader(self.http, self.data_dir / "cache" / "images")
        self.reload_sources()

    # ── servicios ──────────────────────────────────────────────────────
    def reload_sources(self) -> None:
        """Rehace el registro de fuentes (tras cambiar claves o fuentes activas en Ajustes)."""
        self.registry = SourceRegistry(self.http, self.store.source_settings(), cache=self.db)
        self._linked: set[int] = set()          # con claves nuevas hay que volver a enlazar los juegos
        self.installer = ModInstaller(self.db, self.registry, self.downloads, self.extractor, self.deployer,
                                      self.staging)

    def save_settings(self) -> None:
        self.store.save()
        self.staging.overrides = {self.staging._vol_key(k): Path(v)
                                  for k, v in self.settings.staging_overrides.items()}
        self.extractor = Extractor(self.settings.seven_zip or None)
        self.reload_sources()

    @property
    def default_filters(self) -> SearchFilterOptions:
        return SearchFilterOptions.from_dict(self.settings.search_filters)

    def shutdown(self) -> None:
        """Cierre síncrono (al salir de la app): las descargas en curso quedan en pausa con su .part."""
        for t in self.downloads.tasks:
            if t._runner and not t._runner.done():
                t._intent = "pause"
                t._runner.cancel()
        try:
            self.store.save()
        finally:
            self.db.close()

    async def aclose(self) -> None:
        await self.downloads.close()
        await self.http.aclose()
        self.db.close()

    # ── juegos ─────────────────────────────────────────────────────────
    def games(self) -> list[Any]:
        return self.db.query("SELECT * FROM games ORDER BY name COLLATE NOCASE")

    def game(self, game_id: int):
        return self.db.one("SELECT * FROM games WHERE id=?", (game_id,))

    def game_platform(self, game_id: int | None) -> str | None:
        """Versión del juego instalado: steam | xbox | epic | gog, o None si no se sabe (carpeta propia)."""
        from ..core.platform_paths import PathAdapter
        from ..sources.platforms import STORE_TO_PLATFORM
        game = self.game(game_id) if game_id is not None else None
        if game is None:
            return None
        if game["store"] in STORE_TO_PLATFORM:
            return STORE_TO_PLATFORM[game["store"]]
        path = (game["install_path"] or "").replace("\\", "/").lower()
        if "/xboxgames/" in path or "/windowsapps/" in path or PathAdapter(game["install_path"]).is_gdk:
            return "xbox"                                  # portable/manual, pero es la versión de Game Pass
        return None

    def game_ref(self, game_id: int) -> GameRef:
        return self.installer.game_ref(game_id)

    async def ensure_linked(self, game_id: int, *, force: bool = False) -> dict[str, str]:
        """Busca el juego por su nombre en las webs que aún no tiene enlazadas y guarda los IDs.
        Se hace una vez por sesión y juego (o de nuevo al cambiar claves en Ajustes)."""
        if game_id in self._linked and not force:
            return {}
        game = self.game(game_id)
        if game is None:
            return {}
        known = {r["source"]: r["external_id"] for r in
                 self.db.query("SELECT source, external_id FROM game_source_ids WHERE game_id=?", (game_id,))}
        names = [game["name"]]
        if game["profile"] != "generic":                    # el nombre del perfil y sus alias también valen
            from ..games import get_profile
            prof = get_profile(game["profile"])
            names += [prof.name, *prof.aliases]
        found: dict[str, str] = {}
        for name in dict.fromkeys(names):
            new = await self.registry.link_game(name, {**known, **found})
            found.update(new)
        for source, ext in found.items():
            self.db.execute("INSERT OR IGNORE INTO game_source_ids VALUES (?,?,?)", (game_id, source, ext))
        self._linked.add(game_id)
        if found:
            log.info("Juego %s enlazado con: %s", game["name"], found)
        return found

    def linked_sources(self, game_id: int) -> list[str]:
        return [r["source"] for r in self.db.query("SELECT source FROM game_source_ids WHERE game_id=?", (game_id,))]

    def game_by_source(self, source: str, external_id: str) -> list[Any]:
        return self.db.query("""SELECT g.* FROM games g JOIN game_source_ids s ON s.game_id=g.id
                                WHERE s.source=? AND lower(s.external_id)=lower(?)""", (source, external_id))

    async def detect_games(self) -> dict[str, int]:
        """Detecta juegos de las tiendas y de las carpetas portables guardadas.

        - No vuelve a añadir los que el usuario quitó (lista de ignorados).
        - Quita de la lista los juegos de tienda que ya no están instalados (si no tienen mods activos)."""
        def work():
            found = self.detector.detect_all()
            found += self.detector.detect_portable(self.settings.portable_dirs)
            return found
        found = await asyncio.to_thread(work)
        await asyncio.to_thread(self._repair_custom_roots)
        ignored = set(self.settings.ignored_games)
        keep = [g for g in found if g.key not in ignored]
        ids = save_detected(self.db, keep, self.staging, deploy_method=self.settings.deploy_method)
        removed = 0
        present = set(ids.values())
        for g in self.db.query("SELECT * FROM games"):
            if g["id"] in present:
                continue
            stale_store = g["store"] != "custom"
            missing_folder = not Path(g["install_path"]).is_dir()
            active = self.db.one("SELECT COUNT(*) n FROM mods WHERE game_id=? AND state='enabled'", (g["id"],))["n"]
            if (stale_store or missing_folder) and (not active or missing_folder):
                self.db.execute("DELETE FROM games WHERE id=?", (g["id"],))
                removed += 1
        self.events.games_changed.emit()
        return {"found": len(keep), "removed": removed, "ignored": len(found) - len(keep)}

    def _repair_custom_roots(self) -> None:
        """Juegos añadidos a mano eligiendo una subcarpeta («…\\Mods»): se corrigen a la carpeta del .exe."""
        from ..core.game_detect import find_game_root, find_main_exe
        for g in self.db.query("SELECT * FROM games WHERE store='custom'"):
            path = Path(g["install_path"])
            if not path.is_dir():
                continue
            root = find_game_root(path)
            deployed = self.db.one("SELECT COUNT(*) n FROM deployed_files WHERE game_id=?", (g["id"],))["n"]
            if root != path and not deployed:
                exe = g["exe_path"] or (str(e) if (e := find_main_exe(root)) else None)
                self.db.execute("UPDATE games SET install_path=?, exe_path=? WHERE id=?", (str(root), exe, g["id"]))
            elif not g["exe_path"] and (e := find_main_exe(path)):
                self.db.execute("UPDATE games SET exe_path=? WHERE id=?", (str(e), g["id"]))

    def add_game_folder(self, folder: str, name: str | None = None, *, exe: str | None = None,
                        source_ids: dict[str, str] | None = None) -> int:
        g = self.detector.from_folder(folder, name, exe)
        self.unignore(g.key)
        game_id = save_detected(self.db, [g], self.staging, deploy_method=self.settings.deploy_method)[g.key]
        for source, ext in (source_ids or {}).items():          # IDs elegidos con el autocompletado
            self.db.execute("INSERT OR REPLACE INTO game_source_ids VALUES (?,?,?)", (game_id, source, ext))
        self._linked.discard(game_id)
        self.events.games_changed.emit()
        return game_id

    def add_portable_library(self, folder: str) -> list[int]:
        """Carpeta con varios juegos portables dentro: los añade todos y la recuerda para futuras detecciones."""
        games = self.detector.scan_library(folder)
        if folder not in self.settings.portable_dirs:
            self.settings.portable_dirs.append(folder)
            self.store.save()
        ids = save_detected(self.db, games, self.staging, deploy_method=self.settings.deploy_method)
        self.events.games_changed.emit()
        return list(ids.values())

    def game_key(self, game_id: int) -> str | None:
        g = self.game(game_id)
        if g is None:
            return None
        return f"{g['store']}:{g['store_app_id']}" if g["store"] != "custom" else f"custom:{g['store_app_id']}"

    def unignore(self, key: str) -> None:
        if key in self.settings.ignored_games:
            self.settings.ignored_games.remove(key)
            self.store.save()

    def remove_game(self, game_id: int) -> DeployReport:
        """Quita el juego de ModHub. Si su carpeta ya no existe (desinstalado), no hay nada que limpiar en ella."""
        game = self.game(game_id)
        if game is None:
            return DeployReport()
        if Path(game["install_path"]).is_dir():
            report = self.deployer.purge_game(game_id)
            if not report.ok:
                return report
        else:
            report = DeployReport()
            report.warnings.append("La carpeta del juego ya no existe; se quita de la lista.")
        key = self.game_key(game_id)
        self.db.execute("DELETE FROM games WHERE id=?", (game_id,))
        if key and key not in self.settings.ignored_games:      # que «Detectar juegos» no lo vuelva a meter
            self.settings.ignored_games.append(key)
            self.store.save()
        self.events.games_changed.emit()
        return report

    async def search_game_catalog(self, text: str) -> list[GameRef]:
        """Autocompletado de juegos: primero los instalados, luego el catálogo de todas las webs."""
        from ..sources.base import normalize_game_name
        t = normalize_game_name(text)
        installed = [self.game_ref(g["id"]) for g in self.games() if t and t in normalize_game_name(g["name"])]
        remote = await self.registry.search_games(text)
        names = {normalize_game_name(r.name) for r in installed}
        return installed + [r for r in remote if normalize_game_name(r.name) not in names]

    def launch_plan(self, game_id: int):
        from ..core.launcher import plan_launch
        return plan_launch(self.game(game_id))

    def launch_game(self, game_id: int):
        """Abre el juego (con su cargador de mods si lo tiene). Devuelve el LaunchPlan usado."""
        from ..core.launcher import launch
        plan = self.launch_plan(game_id)
        launch(plan)
        return plan

    def set_game_exe(self, game_id: int, exe: str) -> None:
        self.db.execute("UPDATE games SET exe_path=? WHERE id=?", (exe, game_id))
        self.events.games_changed.emit()

    def loader_status(self, game_id: int) -> list[tuple[ModLoader, bool, bool]]:
        """[(loader, obligatorio, instalado)]"""
        g = self.game(game_id)
        prof, root = get_profile(g["profile"]), Path(g["install_path"])
        return ([(l, True, l.is_installed(root)) for l in prof.required_loaders] +
                [(l, False, l.is_installed(root)) for l in prof.optional_loaders])

    # ── mods ───────────────────────────────────────────────────────────
    def mods(self, game_id: int) -> list[Any]:
        return self.db.query("SELECT * FROM mods WHERE game_id=? ORDER BY priority DESC, id DESC", (game_id,))

    def conflicts(self, game_id: int) -> dict[int, int]:
        """{mod_id: nº de archivos en conflicto}"""
        _, conflicts = self.deployer.desired_state(game_id)
        out: dict[int, int] = {}
        for ids in conflicts.values():
            for i in ids:
                out[i] = out.get(i, 0) + 1
        return out

    async def install(self, game_id: int, source: str, mod_id: str, file_id: str,
                      context: dict | None = None) -> InstallResult:
        result = await self.installer.install(game_id, source, mod_id, file_id, context=context)
        self.events.mods_changed.emit(game_id)
        return result

    async def install_archive(self, game_id: int, path: Path | str, name: str | None = None, *,
                              source: str | None = None, mod_id: str | None = None) -> InstallResult:
        description = ""
        if source and mod_id and source in self.registry:     # llegó por el navegador: se lee su descripción
            try:
                info = await self.registry.get(source).get_mod_details(mod_id, self.game_ref(game_id))
                description = "\n".join(filter(None, (info.description, info.summary)))
            except Exception as e:  # noqa: BLE001 - sin descripción se instala igual
                log.info("Sin descripción de %s:%s: %s", source, mod_id, e)
        result = await self.installer.install_archive(game_id, path, name, description=description)
        self.events.mods_changed.emit(game_id)
        return result

    async def run_deploy(self, game_id: int, fn, *args) -> DeployReport:
        """Ejecuta una operación de despliegue en un hilo para no congelar la ventana."""
        report = await asyncio.to_thread(fn, *args)
        self.events.mods_changed.emit(game_id)
        return report

    async def install_loader(self, game_id: int, loader: ModLoader) -> InstallResult | None:
        """Instala el modloader si alguna fuente lo ofrece (p. ej. BepInExPack en Thunderstore)."""
        ref = self.game_ref(game_id)
        for source, mod_id in loader.sources:
            if source in self.registry and ref.id_for(source) and self.registry.get(source).is_configured:
                info = await self.registry.get(source).get_mod_details(mod_id, ref)
                if f := info.latest_file():
                    return await self.install(game_id, source, info.mod_id, f.file_id)
        return None

    # ── instalaciones que quedaron a medias al cerrar ModHub ──────────────
    def interrupted_installs(self) -> list[Any]:
        """Mods que se estaban descargando cuando se cerró ModHub (su .part sigue en staging)."""
        busy = {t.label for t in self.downloads.tasks if t.state.value not in ("done", "failed", "cancelled")}
        rows = self.db.query("""SELECT m.*, g.name AS game_name FROM mods m JOIN games g ON g.id=m.game_id
                                WHERE m.state='downloading' AND m.source!='local' ORDER BY m.id""")
        return [r for r in rows if r["name"] not in busy]

    async def resume_install(self, mod_row) -> InstallResult:
        """Vuelve a pedir el archivo y sigue desde donde se quedó la descarga (mismo destino -> reanuda el .part)."""
        return await self.install(mod_row["game_id"], mod_row["source"], mod_row["source_mod_id"],
                                  mod_row["source_file_id"])

    def discard_interrupted(self, mod_db_id: int) -> None:
        self.db.execute("UPDATE mods SET state='error', error=? WHERE id=? AND state='downloading'",
                        ("Descarga interrumpida al cerrar ModHub", mod_db_id))

    def pending_dependencies(self, game_id: int, result: InstallResult) -> list:
        """Dependencias obligatorias que faltan (ni instaladas como mod ni cubiertas por un modloader ya puesto)."""
        installed = {(r["source"], r["source_mod_id"]) for r in self.mods(game_id)}
        loaders = self.loader_status(game_id)
        return [d for d in result.dependencies
                if d.required and (d.source, d.mod_id) not in installed and d.source in self.registry
                and not any((d.source, d.mod_id) in l.sources and ok for l, _, ok in loaders)]

    async def install_dependencies(self, game_id: int, result: InstallResult) -> list[InstallResult]:
        """Instala las dependencias obligatorias que aún no estén instaladas."""
        out = []
        installed: set = set()
        ref = self.game_ref(game_id)
        for dep in self.pending_dependencies(game_id, result):
            if (dep.source, dep.mod_id) in installed:
                continue
            adapter = self.registry.get(dep.source)
            info = await adapter.get_mod_details(dep.mod_id, ref)
            f = next((x for x in info.files if x.file_id == dep.file_id), None) or info.latest_file()
            if f:
                res = await self.install(game_id, dep.source, info.mod_id, f.file_id)
                res.name = info.name                      # para los avisos («Instala X»)
                out.append(res)
                installed.add((dep.source, dep.mod_id))
        return out

    # ── revisión / FOMOD ────────────────────────────────────────────────
    def review_session(self, mod_db_id: int):
        """('fomod', FomodSession) o ('plan', InstallPlan) para un mod pendiente de revisión."""
        mod = self.db.one("SELECT * FROM mods WHERE id=?", (mod_db_id,))
        game = self.game(mod["game_id"])
        profile = get_profile(game["profile"])
        staging_dir = Path(mod["staging_dir"])
        cfg = load_fomod(staging_dir)
        if cfg is not None:
            data_dir = Path(game["install_path"]) / (profile.fallback_target or "")
            return "fomod", FomodSession(cfg, data_file_state(data_dir))
        return "plan", ModAnalyzer(profile).analyze(staging_dir, mod["name"], game["install_path"])

    # ── protocolos ─────────────────────────────────────────────────────
    def resolve_protocol(self, url: str) -> ProtocolMatch:
        """Interpreta nxm://, ror2mm:// o modhub:// y busca en qué juegos podría instalarse."""
        req = self.registry.route_protocol(url) or self._parse_modhub_url(url)
        if req is None:
            raise ValueError(f"Enlace no reconocido: {url[:80]}")
        if req.game_id:
            games = self.game_by_source(req.source, req.game_id)
        else:
            games = self.db.query("""SELECT g.* FROM games g JOIN game_source_ids s ON s.game_id=g.id
                                     WHERE s.source=?""", (req.source,))
        return ProtocolMatch(req, list(games))

    @staticmethod
    def _parse_modhub_url(url: str) -> ProtocolRequest | None:
        """modhub://install?source=modrinth&mod=AANobbMI&file=xyz&game=fabric"""
        if not url.lower().startswith("modhub://"):
            return None
        parts = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        if parts.netloc != "install" or not q.get("source") or not q.get("mod"):
            return None
        return ProtocolRequest(source=q["source"], mod_id=q["mod"], file_id=q.get("file"), game_id=q.get("game"))

    async def install_from_protocol(self, match: ProtocolMatch, game_id: int) -> InstallResult:
        req = match.request
        file_id = req.file_id
        if not file_id:
            info = await self.registry.get(req.source).get_mod_details(req.mod_id, self.game_ref(game_id))
            file_id = info.latest_file().file_id
        return await self.install(game_id, req.source, req.mod_id, file_id, context=req.context)

    def policy_label(self, policy: DownloadPolicy | None) -> str:
        return POLICY_LABELS.get(policy, ("Desconocida", ""))[0]


POLICY_LABELS: dict[DownloadPolicy | None, tuple[str, str]] = {
    DownloadPolicy.AUTOMATIC: ("Automática", "ModHub lo descarga e instala solo"),
    DownloadPolicy.BROWSER_INTERCEPT: ("Vía web", "Se abre la web; ModHub recoge la descarga"),
    DownloadPolicy.MANUAL_STREAM: ("Vía Steam", "Lo descarga Steam (suscripción) o steamcmd"),
    None: ("—", "No se sabe todavía"),
}
