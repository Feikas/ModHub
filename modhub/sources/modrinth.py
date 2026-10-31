"""Modrinth — API pública v2 (https://docs.modrinth.com). Sin clave.

ID de juego (GameRef.ids["modrinth"]): Modrinth es casi todo Minecraft, así que
el "juego" es en realidad el cargador y/o la versión:
    "fabric"          -> solo mods para Fabric
    "fabric@1.21.1"   -> Fabric y Minecraft 1.21.1
    "@1.21.1"         -> cualquier cargador, Minecraft 1.21.1
    "minecraft"       -> todo Minecraft (lo que se asigna al enlazar el juego automáticamente)
Sin GameRef se busca en todo Modrinth.
"""
from __future__ import annotations

import asyncio
import json

from .base import (AdultSupport, Dependency, SearchFilterOptions, SortOrder, normalize_game_name, DownloadPolicy, DownloadTicket, GameRef, ModFile, ModInfo,
                   ModNotFound, SourceAdapter)
from .http import HttpError

API = "https://api.modrinth.com/v2"
# «Populares ahora» no existe en Modrinth: lo más cercano es por seguidores.
SORT_INDEX = {SortOrder.RELEVANCE: "relevance", SortOrder.TRENDING: "follows", SortOrder.DOWNLOADS: "downloads",
              SortOrder.NEWEST: "newest", SortOrder.UPDATED: "updated"}
SITE = "https://modrinth.com"


def _parse_game_id(game_id: str | None) -> tuple[str | None, str | None]:
    """'fabric@1.21.1' -> ('fabric', '1.21.1')"""
    if not game_id or game_id == "minecraft":
        return None, None
    loader, _, version = game_id.partition("@")
    return (loader or None), (version or None)


class ModrinthAdapter(SourceAdapter):
    key = "modrinth"
    display_name = "Modrinth"
    needs_game = False
    adult_support = AdultSupport.NEVER          # sus normas prohíben el contenido adulto
    default_install = DownloadPolicy.AUTOMATIC

    @property
    def project_types(self) -> list[str]:
        return self.settings.get("project_types", ["mod"])

    # ── Búsqueda ────────────────────────────────────────────────────────
    async def search_games(self, text):
        return [("Minecraft", "minecraft")] if text.strip().lower() in "minecraft" or "minecraft" in text.lower() else []

    async def find_game_id(self, game_name):
        return "minecraft" if "minecraft" in normalize_game_name(game_name) else None

    async def search(self, query, game_ref=None, *, page=1, page_size=20, filters=None):
        loader, version = _parse_game_id(self.game_id(game_ref))
        facets = [[f"project_type:{t}" for t in self.project_types]]
        if loader:
            facets.append([f"categories:{loader}"])
        version = version or (filters.game_version if filters else None)   # filtro nativo de versión
        if version:
            facets.append([f"versions:{version}"])
        order = (filters or SearchFilterOptions()).effective_sort(query)
        params = {"facets": json.dumps(facets), "index": SORT_INDEX[order]}
        if query.strip():
            params["query"] = query
        data = await self.http.get_json(f"{API}/search", params={**params,
            "limit": min(page_size, 100), "offset": (page - 1) * page_size})
        return [self._from_hit(h) for h in data.get("hits", [])]

    def _from_hit(self, h: dict) -> ModInfo:
        return ModInfo(
            source=self.key, mod_id=h["project_id"], name=h["title"], author=h.get("author", ""),
            summary=h.get("description", ""), page_url=f"{SITE}/{h.get('project_type', 'mod')}/{h['slug']}",
            thumbnail=h.get("icon_url") or None, downloads=h.get("downloads"),
            updated_at=h.get("date_modified"), created_at=h.get("date_created"), raw_categories=list(h.get("categories", [])), extra={"slug": h["slug"], "categories": h.get("categories", [])})

    # ── Ficha completa ──────────────────────────────────────────────────
    async def _fetch_mod_details(self, mod_id, game_ref):
        loader, version = _parse_game_id(self.game_id(game_ref))
        params = {}
        if loader:
            params["loaders"] = json.dumps([loader])
        if version:
            params["game_versions"] = json.dumps([version])
        try:
            project, versions, members = await asyncio.gather(
                self.http.get_json(f"{API}/project/{mod_id}"),
                self.http.get_json(f"{API}/project/{mod_id}/version", params=params),
                self.http.get_json(f"{API}/project/{mod_id}/members"),
            )
        except HttpError as e:
            if e.status == 404:
                raise ModNotFound(f"Modrinth: no existe el proyecto '{mod_id}'.") from e
            raise

        owner = next((m["user"]["username"] for m in members if m.get("role") == "Owner"),
                     members[0]["user"]["username"] if members else "")
        info = ModInfo(
            source=self.key, mod_id=project["id"], name=project["title"], author=owner,
            summary=project.get("description", ""), description=project.get("body", ""),
            page_url=f"{SITE}/{project.get('project_type', 'mod')}/{project['slug']}",
            thumbnail=project.get("icon_url") or None, downloads=project.get("downloads"),
            images=[g["url"] for g in sorted(project.get("gallery") or [], key=lambda g: not g.get("featured"))
                    if g.get("url")],
            updated_at=project.get("updated"), game_id=self.game_id(game_ref),
            extra={"slug": project["slug"], "loaders": project.get("loaders", []),
                   "game_versions": project.get("game_versions", [])})

        # Cada "versión" de Modrinth = un ModFile (su archivo principal). Vienen de más nueva a más vieja.
        for i, v in enumerate(versions):
            files = v.get("files", [])
            main = next((f for f in files if f.get("primary")), files[0] if files else None)
            if not main:
                continue
            info.files.append(ModFile(
                source=self.key, mod_id=info.mod_id, file_id=v["id"], name=v.get("name") or v["version_number"],
                version=v.get("version_number", ""), filename=main["filename"], size=main.get("size"),
                is_primary=(i == 0),
                uploaded_at=v.get("date_published"), hashes=main.get("hashes", {}),
                game_versions=v.get("game_versions", []), loaders=v.get("loaders", []),
                dependencies=[
                    Dependency(source=self.key, mod_id=d.get("project_id") or "", file_id=d.get("version_id"),
                               required=d.get("dependency_type") == "required")
                    for d in v.get("dependencies", []) if d.get("dependency_type") in ("required", "optional")
                ],
                extra={"url": main["url"], "version_type": v.get("version_type"),
                       "extra_files": [f["url"] for f in files if f is not main]},
            ))
        return info

    async def get_project_id_for_version(self, version_id: str) -> str:
        """Algunas dependencias solo traen version_id: esto devuelve el proyecto al que pertenece."""
        return (await self.http.get_json(f"{API}/version/{version_id}"))["project_id"]

    # ── Descarga ────────────────────────────────────────────────────────
    async def get_download_ticket(self, file_id, mod_id, game_ref=None, *, context=None):
        _, f = await self._find_file(file_id, mod_id, game_ref)
        return self._ticket(DownloadPolicy.AUTOMATIC, f, url=f.extra["url"])
