"""Qué hacer con el resultado de una instalación (común a buscador, mods instalados y enlaces nxm://)."""
from __future__ import annotations

from ..sources import DownloadPolicy
from ..sources.platforms import PLATFORM_NAMES
from .intercept import PendingIntercept
from .web_queue import WebItem
from .widgets import open_url, run_command, spawn


class InstallFlow:
    def __init__(self, ctx, notify, review_cb, watcher=None, web_queue=None):
        self.ctx, self.notify, self.review_cb, self.watcher = ctx, notify, review_cb, watcher
        self.web_queue = web_queue

    def handle_result(self, result, game_id: int, name: str | None = None) -> None:
        name = name or "El mod"
        if result.status == "installed":
            actions = []
            msg = f"«{name}» instalado y activado."
            deps = self.ctx.pending_dependencies(game_id, result)
            if deps:
                actions.append((f"Instalar dependencias ({len(deps)})", lambda: self.install_dependencies(game_id, result)))
            if result.plan and result.plan.missing_loaders:
                loader = result.plan.missing_loaders[0]
                platform = self.ctx.game_platform(game_id)
                if platform in loader.unsupported:
                    msg += (f" Atención: este mod necesita {loader.name}, que no existe para la versión "
                            f"{PLATFORM_NAMES[platform]} del juego; probablemente no funcionará.")
                else:
                    msg += f" Atención: falta {loader.name}; sin él el mod no funcionará."
                if loader.sources and platform not in loader.unsupported:
                    actions.append((f"Instalar {loader.name}", lambda: self.install_loader(game_id, loader)))
            level = "warn" if (result.plan and result.plan.missing_loaders) or result.message else "ok"
            self.notify(msg + (f" {result.message}" if result.message else ""), level, actions or None)
        elif result.status == "needs_review":
            self.notify(f"«{name}» necesita que revises cómo instalarlo.", "warn",
                        [("Revisar ahora", lambda: self.review_cb(result.mod_id))])
        elif result.status == "needs_user":
            self._manual(result, game_id, name)
        else:
            self.notify(f"Falló la instalación de «{name}»: {result.message}", "error")

    def install_dependencies(self, game_id: int, result) -> None:
        def done(rs):
            ok = [r for r in rs if r.status == "installed"]
            for r in rs:
                if r.status != "installed":              # p. ej. Nexus gratis: a la cola de la web
                    self.handle_result(r, game_id, r.name or "Dependencia")
            if ok or not rs:
                self.notify(f"{len(ok)} dependencias instaladas." if ok else
                            "Las dependencias ya estaban instaladas.", "ok")
        spawn(self.ctx.install_dependencies(game_id, result), on_ok=done,
              on_error=lambda e: self.notify(f"Dependencias: {e}", "error"))

    def install_loader(self, game_id: int, loader) -> None:
        spawn(self.ctx.install_loader(game_id, loader),
              on_ok=lambda r: self.handle_result(r, game_id, loader.name) if r else
              self.notify(f"No hay descarga automática de {loader.name}: instálalo desde {loader.homepage}", "warn",
                          [("Abrir web", lambda: open_url(loader.homepage))] if loader.homepage else None),
              on_error=lambda e: self.notify(f"{loader.name}: {e}", "error"))

    def _manual(self, result, game_id: int, name: str) -> None:
        t = result.ticket
        if t.policy is DownloadPolicy.BROWSER_INTERCEPT:
            method = t.intercept.value if t.intercept else ""
            if method == "downloads_folder" and self.watcher is not None:
                self.watcher.expect(PendingIntercept(game_id, name, t.source, t.mod_id, t.file_id, t.filename))
                self.ctx.events.intercept_event.emit(f"Esperando «{name}» en la carpeta Descargas…")
            elif method == "protocol":
                self.ctx.events.intercept_event.emit(f"Esperando el enlace de descarga de «{name}» desde la web…")
                if self.web_queue is not None and t.page_url:
                    # Un clic en la web por mod; lo demás va solo y las páginas se abren de una en una
                    self.web_queue.add(WebItem(game_id, t.source, t.mod_id, t.file_id, name, t.page_url))
                    return
            self.notify(t.note or "Hay que descargarlo desde la web.", "warn",
                        [("Abrir la web", lambda: open_url(t.page_url))] if t.page_url else None)
            if t.page_url:
                open_url(t.page_url)
        elif t.policy is DownloadPolicy.MANUAL_STREAM:
            actions = []
            if t.page_url:
                actions.append(("Abrir en Steam", lambda: open_url(t.page_url)))
            if t.command:
                actions.append(("Usar steamcmd", lambda: run_command(t.command)))
            self.notify(t.note or "Este mod se descarga con Steam.", "warn", actions or None)
