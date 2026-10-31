"""CurseForge — API oficial "CFCore" (https://docs.curseforge.com/rest-api/).

- Necesita API key propia y gratuita (console.curseforge.com).
- Si el autor desactiva `allowModDistribution`, la API no da enlace: se abre la web y
  ModHub recoge el archivo de Descargas. Nunca se salta esa decisión del autor.

ID de juego (GameRef.ids["curseforge"]) = gameId numérico: "432" Minecraft, "78022" Minecraft Bedrock…
"""
from __future__ import annotations

from .base import (AdultSupport, Dependency, SearchFilterOptions, SortOrder, best_name_match, DownloadPolicy, InterceptMethod, ModFile, ModInfo, ModNotFound,
                   SourceAdapter)
from .http import HttpError

API = "https://api.curseforge.com/v1"
REL_REQUIRED, REL_OPTIONAL = 3, 2
# ModsSearchSortField: 1 Featured · 2 Popularity · 3 LastUpdated · 6 TotalDownloads · 11 ReleasedDate
SORT_FIELD = {SortOrder.RELEVANCE: 1, SortOrder.TRENDING: 2, SortOrder.DOWNLOADS: 6, SortOrder.NEWEST: 11,
              SortOrder.UPDATED: 3}


class CurseForgeAdapter(SourceAdapter):
    key = "curseforge"
    display_name = "CurseForge"
    requires_auth = True
    required_settings = ("api_key",)
    adult_support = AdultSupport.NEVER
    default_install = DownloadPolicy.AUTOMATIC

    def _headers(self):
        return {"x-api-key": self._setting("api_key")}

    async def _games(self) -> list[tuple[str, str]]:
        if getattr(self, "_game_list", None) is None:
            self._game_list = await self._fetch_games()
        return self._game_list

    async def search_games(self, text):
        t = text.lower().strip()
        return [g for g in await self._games() if t in g[0].lower()]

    async def find_game_id(self, game_name):
        return best_name_match(game_name, await self._games())

    async def _fetch_games(self):
        games, index = [], 0
        while True:                                   # la lista de juegos es corta (unas pocas páginas)
            data = await self.http.get_json(f"{API}/games", headers=self._headers(),
                                            params={"index": index, "pageSize": 50})
            games += [(g["name"], str(g["id"])) for g in data.get("data", [])]
            pag = data.get("pagination") or {}
            index += pag.get("resultCount", 0) or 50
            if not data.get("data") or index >= pag.get("totalCount", 0):
                break
        return games

    async def search(self, query, game_ref=None, *, page=1, page_size=20, filters=None):
        game = game_ref.require(self.key) if game_ref else self._setting("default_game_id")
        order = (filters or SearchFilterOptions()).effective_sort(query)
        data = await self.http.get_json(f"{API}/mods/search", headers=self._headers(), params={
            "gameId": game, "pageSize": min(page_size, 50),
            **({"searchFilter": query} if query.strip() else {}),
            "index": (page - 1) * page_size, "sortField": SORT_FIELD[order], "sortOrder": "desc",
            **({"gameVersion": filters.game_version} if filters and filters.game_version else {})})
        return [self._to_info(m) for m in data.get("data", [])]

    def _to_info(self, m: dict) -> ModInfo:
        return ModInfo(
            source=self.key, mod_id=str(m["id"]), name=m["name"],
            author=", ".join(a["name"] for a in m.get("authors", [])), summary=m.get("summary", ""),
            page_url=(m.get("links") or {}).get("websiteUrl", ""),
            thumbnail=(m.get("logo") or {}).get("thumbnailUrl"), downloads=int(m.get("downloadCount") or 0),
            updated_at=m.get("dateModified"), created_at=m.get("dateCreated"), game_id=str(m.get("gameId")),
            raw_categories=[c.get("name", "") for c in m.get("categories", [])],
            install_support=(DownloadPolicy.AUTOMATIC if m.get("allowModDistribution") is not False
                             else DownloadPolicy.BROWSER_INTERCEPT),
            extra={"allow_distribution": m.get("allowModDistribution") is not False})

    async def _fetch_mod_details(self, mod_id, game_ref):
        try:
            mod = (await self.http.get_json(f"{API}/mods/{mod_id}", headers=self._headers()))["data"]
        except HttpError as e:
            if e.status == 404:
                raise ModNotFound(f"CurseForge: no existe el mod {mod_id}.") from e
            raise
        files = await self.http.get_json(f"{API}/mods/{mod_id}/files", headers=self._headers(),
                                         params={"pageSize": 50})
        info = self._to_info(mod)
        info.images = [s["url"] for s in mod.get("screenshots") or [] if s.get("url")]
        if (mod.get("logo") or {}).get("url"):
            info.images.insert(0, mod["logo"]["url"])
        for i, f in enumerate(files.get("data", [])):
            info.files.append(ModFile(
                source=self.key, mod_id=info.mod_id, file_id=str(f["id"]), name=f["displayName"],
                version=f["displayName"], filename=f["fileName"], size=f.get("fileLength"),
                is_primary=(i == 0), uploaded_at=f.get("fileDate"),
                hashes={("sha1" if h["algo"] == 1 else "md5"): h["value"] for h in f.get("hashes", [])},
                game_versions=f.get("gameVersions", []),
                dependencies=[Dependency(source=self.key, mod_id=str(d["modId"]),
                                         required=d["relationType"] == REL_REQUIRED)
                              for d in f.get("dependencies", []) if d.get("relationType") in (REL_REQUIRED, REL_OPTIONAL)],
                extra={"url": f.get("downloadUrl")}))
        return info

    async def get_download_ticket(self, file_id, mod_id, game_ref=None, *, context=None):
        info, f = await self._find_file(file_id, mod_id, game_ref)
        if not info.extra["allow_distribution"] or not f.extra.get("url"):
            return self._ticket(
                DownloadPolicy.BROWSER_INTERCEPT, f, intercept=InterceptMethod.DOWNLOADS_FOLDER,
                page_url=f"{info.page_url}/download/{file_id}",
                note="El autor no permite descargas desde apps de terceros: bájalo en la web y ModHub lo recogerá.")
        return self._ticket(DownloadPolicy.AUTOMATIC, f, url=f.extra["url"])
