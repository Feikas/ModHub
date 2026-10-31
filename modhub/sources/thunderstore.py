"""Thunderstore — API pública (https://thunderstore.io/api/docs/). Sin clave.

Mods con BepInEx/MelonLoader para Lethal Company, Valheim, Risk of Rain 2, R.E.P.O.…

ID de juego (GameRef.ids["thunderstore"]) = comunidad: "lethal-company", "valheim",
"riskofrain2"… (lista completa con `list_communities()`).

Thunderstore no tiene buscador por API: se descarga el índice de la comunidad
(puede pesar decenas de MB en juegos grandes), se guarda en memoria/disco una hora
y se busca en local, igual que hace r2modman.

Dependencias: "Autor-Paquete-1.2.3" -> mod_id "Autor-Paquete", file_id "Autor-Paquete-1.2.3".
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from typing import Any

from .base import (AdultFilter, AdultSupport, Dependency, DownloadPolicy, SearchFilterOptions, SortOrder,
                   best_name_match, parse_date, GameRef, ModFile, ModInfo, ModNotFound,
                   ProtocolRequest, SourceAdapter)

BASE = "https://thunderstore.io"
INDEX_TTL = 3600.0
_DEP = re.compile(r"^(?P<ns>[^-]+)-(?P<name>.+)-(?P<ver>\d+\.\d+\.\d+)$")


def split_full_name(full: str) -> tuple[str, str, str | None]:
    """'BepInEx-BepInExPack-5.4.2100' -> ('BepInEx', 'BepInExPack', '5.4.2100')"""
    m = _DEP.match(full)
    if m:
        return m["ns"], m["name"], m["ver"]
    ns, _, name = full.partition("-")
    return ns, name, None


class ThunderstoreAdapter(SourceAdapter):
    key = "thunderstore"
    display_name = "Thunderstore"
    protocol_schemes = ("ror2mm://",)     # botón "Install with Mod Manager"
    adult_support = AdultSupport.FLAGGED   # campo has_nsfw_content
    default_install = DownloadPolicy.AUTOMATIC

    def __init__(self, http, settings=None, *, cache=None):
        """`cache`: objeto opcional con cache_get(key)/cache_set(key, value, ttl) (la Database de ModHub)."""
        super().__init__(http, settings)
        self.cache = cache
        self._index: dict[str, tuple[float, dict[str, dict]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._communities: list[dict] | None = None

    # ── Índice por comunidad ─────────────────────────────────────────────
    async def _packages(self, community: str) -> dict[str, dict]:
        """{'Autor-Paquete': paquete} de una comunidad, cacheado."""
        hit = self._index.get(community)
        if hit and time.monotonic() - hit[0] < INDEX_TTL:
            return hit[1]
        lock = self._locks.setdefault(community, asyncio.Lock())
        async with lock:   # si 5 búsquedas piden el índice a la vez, se descarga una sola vez
            hit = self._index.get(community)
            if hit and time.monotonic() - hit[0] < INDEX_TTL:
                return hit[1]
            raw = self.cache.cache_get(f"ts_index:{community}") if self.cache else None
            if raw is None:
                raw = await self.http.get_json(f"{BASE}/c/{community}/api/v1/package/", timeout=180)
                if self.cache:
                    self.cache.cache_set(f"ts_index:{community}", raw, INDEX_TTL)
            packages = {p["full_name"]: p for p in raw}
            self._index[community] = (time.monotonic(), packages)
            return packages

    async def list_communities(self) -> list[dict[str, Any]]:
        """[{'identifier': 'lethal-company', 'name': 'Lethal Company'}, …]"""
        out, url = [], f"{BASE}/api/experimental/community/"
        while url:
            data = await self.http.get_json(url)
            out += [{"identifier": c["identifier"], "name": c["name"]} for c in data.get("results", [])]
            url = (data.get("pagination") or {}).get("next_link")
        return out

    # ── Búsqueda ────────────────────────────────────────────────────────
    async def _all_communities(self) -> list[dict]:
        if self._communities is None:
            self._communities = await self.list_communities()
        return self._communities

    async def search_games(self, text):
        t = text.lower().strip()
        return [(c["name"], c["identifier"]) for c in await self._all_communities() if t in c["name"].lower()]

    async def find_game_id(self, game_name):
        if self._communities is None:
            try:
                self._communities = await self.list_communities()
            except Exception:  # noqa: BLE001
                return None
        return best_name_match(game_name, [(c["name"], c["identifier"]) for c in self._communities])

    @staticmethod
    def _trend(p: dict, now: datetime) -> float:
        """«Popular ahora»: descargas por día de las versiones recientes (velocidad, no total)."""
        score = 0.0
        for v in p["versions"][:3]:
            created = parse_date(v.get("date_created")) or now
            days = max(1.0, (now - created).total_seconds() / 86400)
            score += v.get("downloads", 0) / days
        return score

    async def search(self, query, game_ref=None, *, page=1, page_size=20, filters=None):
        community = (game_ref.require(self.key) if game_ref
                     else self.settings.get("default_community") or "riskofrain2")
        # Filtro nativo: con «Ocultar adulto» ni siquiera se puntúan los paquetes NSFW
        include_nsfw = filters is not None and filters.adult is not AdultFilter.HIDE
        order = (filters or SearchFilterOptions()).effective_sort(query)
        words = query.lower().split()
        now = datetime.now(timezone.utc)

        def matches(p: dict) -> bool:
            hay = f"{p['name']} {p['owner']} {p['versions'][0].get('description', '')}".lower()
            return all(w in hay for w in words)

        def key(p: dict) -> float:
            total = sum(v.get("downloads", 0) for v in p["versions"])
            if order is SortOrder.RELEVANCE:
                return (1e15 if query.lower() in p["name"].lower() else 0) + total
            if order is SortOrder.TRENDING:
                return self._trend(p, now)
            if order is SortOrder.NEWEST:
                return (parse_date(p.get("date_created")) or now).timestamp()
            if order is SortOrder.UPDATED:
                return (parse_date(p.get("date_updated")) or now).timestamp()
            return total

        hits = [p for p in (await self._packages(community)).values()
                if p["versions"] and not p.get("is_deprecated")
                and not (p.get("has_nsfw_content") and not include_nsfw) and matches(p)]
        hits.sort(key=key, reverse=True)
        start = (page - 1) * page_size
        return [self._to_info(p, community, with_files=False) for p in hits[start:start + page_size]]

    def _to_info(self, p: dict, community: str, *, with_files: bool) -> ModInfo:
        latest = p["versions"][0]
        info = ModInfo(
            source=self.key, mod_id=p["full_name"], name=p["name"], author=p["owner"],
            summary=latest.get("description", ""), page_url=p.get("package_url", ""),
            thumbnail=latest.get("icon"), images=[latest["icon"]] if latest.get("icon") else [], downloads=sum(v.get("downloads", 0) for v in p["versions"]),
            updated_at=p.get("date_updated"), created_at=p.get("date_created"), game_id=community,
            is_adult=bool(p.get("has_nsfw_content", False)), raw_categories=list(p.get("categories", [])),
            extra={"categories": p.get("categories", []), "rating": p.get("rating_score"),
                   "pinned": p.get("is_pinned", False)})
        if with_files:
            for i, v in enumerate(p["versions"]):
                info.files.append(ModFile(
                    source=self.key, mod_id=p["full_name"], file_id=v["full_name"], name=v["name"],
                    version=v["version_number"], filename=f"{v['full_name']}.zip", size=v.get("file_size"),
                    is_primary=(i == 0), uploaded_at=v.get("date_created"),
                    dependencies=[self._dep(d) for d in v.get("dependencies", [])],
                    extra={"url": v["download_url"], "community": community}))
        return info

    def _dep(self, full: str) -> Dependency:
        ns, name, _ = split_full_name(full)
        return Dependency(source=self.key, mod_id=f"{ns}-{name}", file_id=full, name=name)

    # ── Ficha completa ──────────────────────────────────────────────────
    async def _fetch_mod_details(self, mod_id, game_ref):
        community = game_ref.require(self.key) if game_ref else self.settings.get("default_community")
        if not community:
            raise ModNotFound("Thunderstore necesita saber la comunidad (juego).")
        p = (await self._packages(community)).get(mod_id)
        if p is None:
            raise ModNotFound(f"Thunderstore: '{mod_id}' no existe en {community}.")
        return self._to_info(p, community, with_files=True)

    # ── Descarga ────────────────────────────────────────────────────────
    async def get_download_ticket(self, file_id, mod_id, game_ref=None, *, context=None):
        _, f = await self._find_file(file_id, mod_id, game_ref)
        return self._ticket(DownloadPolicy.AUTOMATIC, f, url=f.extra["url"])

    # ── Protocolo ───────────────────────────────────────────────────────
    def parse_protocol_url(self, url):
        # ror2mm://v1/install/thunderstore.io/<Autor>/<Paquete>/<versión>/
        if not url.lower().startswith("ror2mm://"):
            return None
        segs = [s for s in url.split("://", 1)[1].split("/") if s]
        if len(segs) >= 6 and segs[0] == "v1" and segs[1] == "install":
            ns, name, ver = segs[3], segs[4], segs[5]
            return ProtocolRequest(source=self.key, mod_id=f"{ns}-{name}", file_id=f"{ns}-{name}-{ver}")
        return None
