"""Orquestador: une Fase 2 y Fase 3 en una sola llamada.

    ticket -> descarga -> extracción -> análisis -> registro -> despliegue

    installer = ModInstaller(db, registry, downloads, extractor, deployer, staging)
    result = await installer.install(game_id, "thunderstore", "Owner-Mod", "Owner-Mod-1.0.0")
    if result.status == "needs_user":       # hay que pasar por la web (BROWSER_INTERCEPT / MANUAL_STREAM)
        abrir(result.ticket.page_url)
    elif result.status == "needs_review":   # estructura dudosa o FOMOD: la UI pregunta al usuario
        mostrar(result.plan.summary())
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..games import get_profile
from ..sources import DownloadPolicy, DownloadTicket, GameRef, ModInfo, SourceRegistry
from .deployer import Deployer, DeployReport
from .downloader import DownloadManager, DownloadState
from .extractor import Extractor, InstallPlan, ModAnalyzer
from .staging import StagingManager

log = logging.getLogger("modhub.installer")


@dataclass
class InstallResult:
    status: str                     # installed | needs_user | needs_review | failed
    mod_id: int | None = None       # id en la tabla mods
    ticket: DownloadTicket | None = None
    plan: InstallPlan | None = None
    deploy: DeployReport | None = None
    message: str = ""
    dependencies: list[Any] = field(default_factory=list)
    name: str = ""                  # nombre legible del mod (para los avisos)


class ModInstaller:
    def __init__(self, db, registry: SourceRegistry, downloads: DownloadManager, extractor: Extractor,
                 deployer: Deployer, staging: StagingManager):
        self.db, self.registry, self.downloads = db, registry, downloads
        self.extractor, self.deployer, self.staging = extractor, deployer, staging

    # --- datos del juego ----------------------------------------------------
    def game(self, game_id: int):
        row = self.db.one("SELECT * FROM games WHERE id=?", (game_id,))
        if row is None:
            raise ValueError(f"El juego {game_id} no existe.")
        return row

    def game_ref(self, game_id: int) -> GameRef:
        row = self.game(game_id)
        ids = {r["source"]: r["external_id"] for r in
               self.db.query("SELECT source, external_id FROM game_source_ids WHERE game_id=?", (game_id,))}
        return GameRef(name=row["name"], ids=ids, slug=row["slug"])

    # --- instalación desde una fuente ----------------------------------------
    async def install(self, game_id: int, source: str, mod_id: str, file_id: str, *,
                      context: dict[str, Any] | None = None, enable: bool = True) -> InstallResult:
        adapter = self.registry.get(source)
        ref = self.game_ref(game_id)
        ticket = await adapter.get_download_ticket(file_id, mod_id, ref, context=context)
        if ticket.policy is not DownloadPolicy.AUTOMATIC:
            # No se registra nada todavía: cuando el archivo llegue (navegador/Steam) se usa install_archive().
            return InstallResult("needs_user", ticket=ticket, message=ticket.note or "Requiere acción en la web.")

        info = await adapter.get_mod_details(mod_id, ref)
        file = next((f for f in info.files if f.file_id == file_id), None)
        db_id = self._upsert_mod(game_id, info, file_id, file.version if file else "", state="downloading")

        game = self.game(game_id)
        task = self.downloads.add_ticket(ticket, self.staging.downloads_dir(game["slug"], game["install_path"]),
                                         label=info.name)
        task = await self.downloads.wait(task.id)
        if task.state is not DownloadState.DONE:
            self._set_state(db_id, "error", task.error or "Descarga cancelada")
            return InstallResult("failed", db_id, ticket=ticket, message=task.error or "Descarga cancelada")
        self.db.execute("UPDATE mods SET archive_path=? WHERE id=?", (str(task.dest), db_id))

        # En Thunderstore el nombre único es "Autor-Paquete" (igual que r2modman): evita choques entre mods homónimos.
        folder_name = info.mod_id if source == "thunderstore" else info.name
        result = await self._extract_and_register(game_id, db_id, task.dest, folder_name,
                                                  file.version if file else "", enable=enable,
                                                  description="\n".join(filter(None, (
                                                      info.description, info.summary,
                                                      str(file.extra.get("description") or "") if file else ""))))
        result.ticket = ticket
        result.dependencies = list(file.dependencies) if file else []
        return result

    async def install_archive(self, game_id: int, archive: Path | str, name: str | None = None, *,
                              enable: bool = True, description: str = "") -> InstallResult:
        """Para archivos que llegan por el navegador (BROWSER_INTERCEPT) o que el usuario arrastra."""
        archive = Path(archive)
        name = name or archive.stem
        info = ModInfo(source="local", mod_id=name, name=name)
        db_id = self._upsert_mod(game_id, info, archive.name, "", state="downloaded")
        game = self.game(game_id)
        dest = self.staging.downloads_dir(game["slug"], game["install_path"]) / archive.name
        if archive.resolve() != dest.resolve():
            shutil.copy2(archive, dest)
        self.db.execute("UPDATE mods SET archive_path=? WHERE id=?", (str(dest), db_id))
        return await self._extract_and_register(game_id, db_id, dest, name, "", enable=enable, description=description)

    async def _extract_and_register(self, game_id, db_id, archive: Path, name, version, *, enable,
                                    description: str = "") -> InstallResult:
        game = self.game(game_id)
        mod_dir = self.staging.mod_dir(game["slug"], game["install_path"], name, version)
        if mod_dir.exists():
            shutil.rmtree(mod_dir)                    # reinstalación / actualización
        try:
            await self.extractor.extract(archive, mod_dir)
        except Exception as e:  # noqa: BLE001
            self._set_state(db_id, "error", str(e))
            return InstallResult("failed", db_id, message=str(e))

        plan = ModAnalyzer(get_profile(game["profile"])).analyze(mod_dir, name, game["install_path"],
                                                                 description=description)
        self.deployer.register_plan(db_id, plan)
        if plan.needs_review:
            self._set_state(db_id, "needs_review")
            return InstallResult("needs_review", db_id, plan=plan, message="; ".join(plan.warnings))

        self._set_state(db_id, "enabled" if enable else "installed")
        report = self.deployer.deploy_game(game_id) if enable else None
        return InstallResult("installed", db_id, plan=plan, deploy=report,
                             message="; ".join((report.errors if report else []) + plan.warnings))

    # --- gestión --------------------------------------------------------------
    def confirm_review(self, mod_db_id: int, plan: InstallPlan | None = None) -> DeployReport:
        """El usuario aceptó (o corrigió) el plan de un mod en revisión."""
        if plan is not None:
            self.deployer.register_plan(mod_db_id, plan)
        return self.set_enabled(mod_db_id, True)

    def set_enabled(self, mod_db_id: int, enabled: bool) -> DeployReport:
        row = self.db.one("SELECT game_id FROM mods WHERE id=?", (mod_db_id,))
        self._set_state(mod_db_id, "enabled" if enabled else "disabled")
        return self.deployer.deploy_game(row["game_id"])

    def set_priority(self, mod_db_id: int, priority: int) -> DeployReport:
        row = self.db.one("SELECT game_id FROM mods WHERE id=?", (mod_db_id,))
        self.db.execute("UPDATE mods SET priority=? WHERE id=?", (priority, mod_db_id))
        return self.deployer.deploy_game(row["game_id"])

    def uninstall(self, mod_db_id: int, *, delete_files: bool = True) -> DeployReport:
        row = self.db.one("SELECT * FROM mods WHERE id=?", (mod_db_id,))
        self._set_state(mod_db_id, "disabled")
        report = self.deployer.deploy_game(row["game_id"])      # quita sus enlaces del juego
        if report.ok:
            if delete_files:
                for p in (row["staging_dir"], row["archive_path"]):
                    if p and Path(p).is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                    elif p and Path(p).is_file():
                        Path(p).unlink(missing_ok=True)
            self.db.execute("DELETE FROM mods WHERE id=?", (mod_db_id,))
        return report

    # --- base de datos --------------------------------------------------------
    def _upsert_mod(self, game_id: int, info: ModInfo, file_id: str, version: str, *, state: str) -> int:
        self.db.execute(
            """INSERT INTO mods (game_id, source, source_mod_id, source_file_id, name, version, author, page_url, state,
                                 installed_at, priority)
               VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),
                       (SELECT COALESCE(MAX(priority), 0) + 1 FROM mods WHERE game_id=?))
               ON CONFLICT(game_id, source, source_mod_id, source_file_id) DO UPDATE SET
                   name=excluded.name, version=excluded.version, state=excluded.state, error=NULL""",
            (game_id, info.source, info.mod_id, file_id, info.name, version, info.author, info.page_url, state, game_id))
        return self.db.one("SELECT id FROM mods WHERE game_id=? AND source=? AND source_mod_id=? AND source_file_id=?",
                           (game_id, info.source, info.mod_id, file_id))["id"]

    def _set_state(self, mod_db_id: int, state: str, error: str | None = None) -> None:
        self.db.execute("UPDATE mods SET state=?, error=? WHERE id=?", (state, error, mod_db_id))
