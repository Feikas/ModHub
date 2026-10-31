"""Registro de fuentes, búsqueda global en paralelo y enrutado de URLs de protocolo.

Uso:
    async with HttpClient() as http:
        registry = SourceRegistry(http, settings={"nexus": {"api_key": "…"}})
        game = GameRef("Lethal Company", ids={"thunderstore": "lethal-company", "nexus": "lethalcompany"})
        results = await registry.search_all("more company", game)
        for mod in results.merged():
            print(mod.source, mod.name)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from .base import (CATEGORIES, AdultFilter, AdultSupport, AuthRequired, Dependency, DownloadPolicy, DownloadTicket, GameNotSupported, GameRef,
                   InterceptMethod, ModFile, ModInfo, ModNotFound, ProtocolRequest, SourceAdapter,
                   SORT_LABELS, SearchFilterOptions, SortOrder, SourceError, classify_categories, sort_key)
from .curseforge import CurseForgeAdapter
from .gamebanana import GameBananaAdapter
from .http import HttpClient, HttpError, RateLimit
from .modio import ModIoAdapter
from .modrinth import ModrinthAdapter
from .nexus import NexusAdapter, NexusAuth
from .scraping.ayakamods import AyakaModsAdapter
from .scraping.moddb import ModDBAdapter
from .thunderstore import ThunderstoreAdapter

log = logging.getLogger("modhub.sources")

# Orden = prioridad al mezclar resultados con el mismo número de descargas
ADAPTER_CLASSES: tuple[type[SourceAdapter], ...] = (
    NexusAdapter, ModrinthAdapter, ThunderstoreAdapter, CurseForgeAdapter, ModIoAdapter,
    GameBananaAdapter, ModDBAdapter, AyakaModsAdapter,
)


@dataclass(slots=True)
class SearchResults:
    """Resultado de una búsqueda global: lo que devolvió cada fuente, sus errores y las que se omitieron."""

    query: str
    by_source: dict[str, list[ModInfo]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)      # fuente -> mensaje para la UI
    skipped: dict[str, str] = field(default_factory=dict)     # fuente -> motivo
    timings: dict[str, float] = field(default_factory=dict)   # fuente -> segundos
    filtered_out: dict[str, int] = field(default_factory=dict)  # fuente -> resultados ocultados por filtros
    by_name: set[str] = field(default_factory=set)            # fuentes buscadas por el NOMBRE del juego (sin ID)
    filters: SearchFilterOptions = field(default_factory=SearchFilterOptions)

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.by_source.values())

    @property
    def sort(self) -> SortOrder:
        return self.filters.effective_sort(self.query)

    def merged(self, limit: int | None = None) -> list[ModInfo]:
        """Une todas las fuentes en una lista.

        - Descargas / Más nuevos / Actualizados: orden global por ese dato (comparable entre webs).
        - Relevancia / Populares ahora: cada web lo calcula a su manera, así que se intercalan sus
          listas por posición (el 1.º de cada web, luego el 2.º…)."""
        lists = list(self.by_source.values())
        if sort_key(ModInfo("", "", ""), self.sort) is not None:
            out = sorted((m for l in lists for m in l), key=lambda m: sort_key(m, self.sort), reverse=True)
            return out[:limit] if limit else out
        out: list[ModInfo] = []
        for i in range(max((len(l) for l in lists), default=0)):
            for l in lists:
                if i < len(l):
                    out.append(l[i])
        return out[:limit] if limit else out


class SourceRegistry:
    def __init__(self, http: HttpClient, settings: dict[str, dict[str, Any]] | None = None, *,
                 cache: Any = None, only: Iterable[str] | None = None):
        """
        settings: {"nexus": {"api_key": "…"}, "curseforge": {"api_key": "…", "enabled": True}, …}
        cache:    objeto con cache_get/cache_set (la Database de ModHub) para el índice de Thunderstore.
        only:     limitar a estas fuentes (útil en pruebas).
        """
        settings = settings or {}
        allowed = set(only) if only else None
        self.adapters: dict[str, SourceAdapter] = {}
        for cls in ADAPTER_CLASSES:
            conf = settings.get(cls.key, {})
            if conf.get("enabled") is False or (allowed is not None and cls.key not in allowed):
                continue
            self.adapters[cls.key] = (ThunderstoreAdapter(http, conf, cache=cache)
                                      if cls is ThunderstoreAdapter else cls(http, conf))

    def __contains__(self, key: str) -> bool:
        return key in self.adapters

    def get(self, key: str) -> SourceAdapter:
        try:
            return self.adapters[key]
        except KeyError:
            raise SourceError(f"La fuente '{key}' no existe o está desactivada.") from None

    def status(self) -> dict[str, dict[str, Any]]:
        """Para la pantalla de Ajustes: qué fuentes están listas y cuáles necesitan clave."""
        return {k: {"name": a.display_name, "configured": a.is_configured, "needs_game": a.needs_game}
                for k, a in self.adapters.items()}

    def _skip_reason(self, adapter: SourceAdapter, game_ref: GameRef | None,
                     filters: SearchFilterOptions | None = None) -> str | None:
        if filters is not None:
            if filters.adult is AdultFilter.ONLY and adapter.adult_support is AdultSupport.NEVER:
                return "Esta web no admite contenido adulto."
            if filters.auto_install_only and not adapter.can_auto_install:
                return "Esta web no permite instalación automática."
        if not adapter.is_configured and adapter.search_needs_auth:
            return "Falta configurar la API key."
        if game_ref is not None and not game_ref.id_for(adapter.key) and not (adapter.text_fallback and game_ref.name):
            return f"Este juego no está en {adapter.display_name}."
        if game_ref is None and adapter.needs_game:
            return "Esta fuente necesita elegir un juego."
        return None

    async def search_all(self, query: str, game_ref: GameRef | None = None, *, page: int = 1,
                         page_size: int = 20, timeout: float = 20.0,
                         sources: Iterable[str] | None = None,
                         filters: SearchFilterOptions | None = None) -> SearchResults:
        """Busca en todas las fuentes habilitadas a la vez.

        Una fuente lenta o caída no bloquea a las demás: cada una tiene su `timeout`
        y su error queda en `results.errors`.
        """
        filters = filters or SearchFilterOptions()
        results = SearchResults(query=query, filters=filters)
        wanted = set(sources) if sources else None
        tasks: dict[str, SourceAdapter] = {}
        for key, adapter in self.adapters.items():
            if wanted is not None and key not in wanted:
                continue
            if reason := self._skip_reason(adapter, game_ref, filters):
                results.skipped[key] = reason
            else:
                tasks[key] = adapter

        async def run(key: str, adapter: SourceAdapter):
            start = time.perf_counter()
            q, ref = query, game_ref
            if game_ref is not None and not game_ref.id_for(key):
                # Web multijuego que no tiene el juego en su catálogo: se busca por el nombre del juego
                from .base import strip_edition
                q, ref = f"{strip_edition(game_ref.name) or game_ref.name} {query}".strip(), None
                results.by_name.add(key)
            try:
                return await asyncio.wait_for(
                    adapter.search(q, ref, page=page, page_size=page_size, filters=filters), timeout)
            finally:
                results.timings[key] = round(time.perf_counter() - start, 2)

        outcomes = await asyncio.gather(*(run(k, a) for k, a in tasks.items()), return_exceptions=True)
        for key, outcome in zip(tasks, outcomes):
            if isinstance(outcome, BaseException):
                if isinstance(outcome, asyncio.CancelledError):
                    raise outcome
                msg = "Tiempo de espera agotado." if isinstance(outcome, asyncio.TimeoutError) else str(outcome)
                log.warning("Búsqueda en %s falló: %r", key, outcome)
                results.errors[key] = msg or type(outcome).__name__
            else:
                enriched = [tasks[key].enrich(m) for m in outcome]
                kept = filters.apply(enriched)
                if sort_key(ModInfo("", "", ""), results.sort) is not None:       # por si la web no ordenó
                    kept.sort(key=lambda m: sort_key(m, results.sort), reverse=True)
                results.by_source[key] = kept
                if len(kept) != len(enriched):
                    results.filtered_out[key] = len(enriched) - len(kept)
        return results

    async def link_game(self, game_name: str, known: dict[str, str] | None = None, *,
                        timeout: float = 25.0) -> dict[str, str]:
        """Busca el juego por su nombre en cada web y devuelve {fuente: id} de las que lo tienen.

        Así «Buscar todo lo de este juego» funciona aunque el juego no tenga perfil en ModHub."""
        known = known or {}

        async def one(key: str, adapter: SourceAdapter):
            try:
                return key, await asyncio.wait_for(adapter.find_game_id(game_name), timeout)
            except Exception as e:  # noqa: BLE001 - una web caída no impide enlazar las demás
                log.info("No se pudo buscar «%s» en %s: %s", game_name, key, e)
                return key, None

        pending = [(k, a) for k, a in self.adapters.items()
                   if k not in known and (a.is_configured or not a.search_needs_auth)]
        found = await asyncio.gather(*(one(k, a) for k, a in pending))
        return {k: v for k, v in found if v}

    async def search_games(self, text: str, *, limit: int = 25, timeout: float = 12.0) -> list[GameRef]:
        """Catálogo de juegos de todas las webs para el autocompletado: «cyber» -> [Cyberpunk 2077 (Nexus, GameBanana…)].
        Une el mismo juego de varias webs en un solo GameRef con todos sus IDs."""
        from .base import normalize_game_name, strip_edition
        text = text.strip()
        if len(text) < 2:
            return []

        async def one(key: str, adapter: SourceAdapter):
            try:
                return key, await asyncio.wait_for(adapter.search_games(text), timeout)
            except Exception as e:  # noqa: BLE001
                log.info("Catálogo de juegos de %s no disponible: %s", key, e)
                return key, []

        usable = [(k, a) for k, a in self.adapters.items() if a.is_configured or not a.search_needs_auth]
        found = await asyncio.gather(*(one(k, a) for k, a in usable))
        merged: dict[str, GameRef] = {}
        for key, games in found:
            for name, gid in games:
                if not name or not gid:
                    continue
                norm = normalize_game_name(strip_edition(name)) or normalize_game_name(name)
                ref = merged.setdefault(norm, GameRef(name=name, ids={}))
                ref.ids.setdefault(key, gid)
                if key == "nexus":                 # el nombre de Nexus suele ser el más limpio
                    ref.name = name
        t = normalize_game_name(text)

        def rank(r: GameRef):
            n = normalize_game_name(r.name)
            return (not n.startswith(t), -len(r.ids), len(n))
        return sorted(merged.values(), key=rank)[:limit]

    def route_protocol(self, url: str) -> ProtocolRequest | None:
        """Recibe la URL que Windows pasa a ModHub (nxm://, ror2mm://, modhub:…) y la interpreta."""
        for adapter in self.adapters.values():
            if req := adapter.parse_protocol_url(url.strip()):
                return req
        return None

    async def close(self) -> None:
        await asyncio.gather(*(a.close() for a in self.adapters.values()))


__all__ = [
    "SourceRegistry", "SearchResults", "SearchFilterOptions", "SortOrder", "SORT_LABELS", "AdultFilter", "AdultSupport", "CATEGORIES",
    "classify_categories", "HttpClient", "HttpError", "RateLimit",
    "SourceAdapter", "GameRef", "ModInfo", "ModFile", "Dependency", "DownloadTicket", "DownloadPolicy",
    "InterceptMethod", "ProtocolRequest", "SourceError", "AuthRequired", "GameNotSupported", "ModNotFound",
    "NexusAuth",
]
