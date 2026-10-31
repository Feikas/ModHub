"""mod.io — API v1 (https://docs.mod.io). API key gratuita; algunos juegos exigen OAuth.

ID de juego (GameRef.ids["modio"]) = game_id numérico.
Los IDs de mod solo son únicos dentro de un juego: mod_id = "<game_id>:<mod_id>".
"""
from __future__ import annotations

import asyncio

from .base import AdultSupport, Dependency, SearchFilterOptions, SortOrder, best_name_match, DownloadPolicy, InterceptMethod, ModFile, ModInfo, SourceAdapter

API = "https://api.mod.io/v1"
SORT = {SortOrder.RELEVANCE: "-popular", SortOrder.TRENDING: "-popular", SortOrder.DOWNLOADS: "-downloads_total",
        SortOrder.NEWEST: "-date_live", SortOrder.UPDATED: "-date_updated"}


def _split(composite: str) -> tuple[str, str]:
    game, _, mod = composite.partition(":")
    return game, mod


class ModIoAdapter(SourceAdapter):
    key = "modio"
    display_name = "mod.io"
    requires_auth = True
    required_settings = ("api_key",)
    adult_support = AdultSupport.FLAGGED      # maturity_option (8 = explícito)
    default_install = DownloadPolicy.AUTOMATIC

    def _req(self, **params):
        headers = {}
        if token := self.settings.get("oauth_token"):
            headers["Authorization"] = f"Bearer {token}"
        return {"params": {"api_key": self._setting("api_key"), **params}, "headers": headers}

    def _to_info(self, m: dict, game: str) -> ModInfo:
        return ModInfo(
            source=self.key, mod_id=f"{game}:{m['id']}", name=m["name"],
            author=(m.get("submitted_by") or {}).get("username", ""), summary=m.get("summary", ""),
            description=m.get("description_plaintext") or "", page_url=m.get("profile_url", ""),
            thumbnail=(m.get("logo") or {}).get("thumb_320x180"),
            downloads=(m.get("stats") or {}).get("downloads_total"), updated_at=str(m.get("date_updated", "")),
            created_at=str(m.get("date_live") or m.get("date_added") or ""),
            game_id=game, is_adult=bool((m.get("maturity_option") or 0) & 8),
            raw_categories=[t.get("name", "") for t in m.get("tags", [])])

    async def search_games(self, text):
        data = await self.http.get_json(f"{API}/games", **self._req(_q=text, _limit=30))
        return [(g["name"], str(g["id"])) for g in data.get("data", [])]

    async def find_game_id(self, game_name):
        data = await self.http.get_json(f"{API}/games", **self._req(_q=game_name, _limit=20))
        return best_name_match(game_name, [(g["name"], str(g["id"])) for g in data.get("data", [])])

    async def search(self, query, game_ref=None, *, page=1, page_size=20, filters=None):
        game = game_ref.require(self.key) if game_ref else self._setting("default_game_id")
        order = (filters or SearchFilterOptions()).effective_sort(query)
        extra = {"_q": query} if query.strip() else {}
        data = await self.http.get_json(f"{API}/games/{game}/mods", **self._req(
            **extra, _limit=min(page_size, 100), _offset=(page - 1) * page_size, _sort=SORT[order]))
        return [self._to_info(m, game) for m in data.get("data", [])]

    async def _fetch_mod_details(self, mod_id, game_ref):
        game, mod = _split(mod_id)
        m, files, deps = await asyncio.gather(
            self.http.get_json(f"{API}/games/{game}/mods/{mod}", **self._req()),
            self.http.get_json(f"{API}/games/{game}/mods/{mod}/files", **self._req(_sort="-date_added")),
            self.http.get_json(f"{API}/games/{game}/mods/{mod}/dependencies", **self._req()),
        )
        info = self._to_info(m, game)
        info.images = [i["original"] for i in (m.get("media") or {}).get("images") or [] if i.get("original")]
        if (m.get("logo") or {}).get("original"):
            info.images.insert(0, m["logo"]["original"])
        dep_list = [Dependency(source=self.key, mod_id=f"{game}:{d['mod_id']}", name=d.get("name", ""))
                    for d in deps.get("data", [])]
        for i, f in enumerate(files.get("data", [])):
            info.files.append(ModFile(
                source=self.key, mod_id=mod_id, file_id=str(f["id"]), name=f.get("filename", ""),
                version=f.get("version") or "", filename=f.get("filename", ""), size=f.get("filesize"),
                is_primary=(i == 0), hashes={"md5": (f.get("filehash") or {}).get("md5", "")},
                dependencies=list(dep_list), extra={"url": (f.get("download") or {}).get("binary_url")}))
        return info

    async def get_download_ticket(self, file_id, mod_id, game_ref=None, *, context=None):
        info, f = await self._find_file(file_id, mod_id, game_ref)
        if not f.extra.get("url"):
            return self._ticket(DownloadPolicy.BROWSER_INTERCEPT, f, intercept=InterceptMethod.DOWNLOADS_FOLDER,
                                page_url=info.page_url,
                                note="mod.io no dio enlace: puede que este juego exija iniciar sesión.")
        return self._ticket(DownloadPolicy.AUTOMATIC, f, url=f.extra["url"])
