"""GameBanana — API v11 pública ("semi-oficial", sin clave).

ID de juego (GameRef.ids["gamebanana"]) = _idRow del juego (p. ej. "19567" Zenless Zone Zero).

1-Click Install: GameBanana muestra ese botón solo para gestores registrados en su web.
El enlace tiene la forma   <esquema>:https://gamebanana.com/mmdl/<file_id>,<Modelo>,<item_id>
Endpoints marcados (verificar) se comprobarán contra la API real.
"""
from __future__ import annotations

import re

from .base import (AdultSupport, DownloadPolicy, SearchFilterOptions, SortOrder, best_name_match, InterceptMethod, ModFile, ModInfo, ProtocolRequest,
                   SourceAdapter)

API = "https://gamebanana.com/apiv11"
# Orden al EXPLORAR un juego (sin texto). (verificar nombres exactos contra la API real)
BROWSE_SORT = {SortOrder.TRENDING: "Generic_MostViewed", SortOrder.DOWNLOADS: "Generic_MostDownloaded",
               SortOrder.NEWEST: "Generic_Newest", SortOrder.UPDATED: "Generic_LatestUpdated",
               SortOrder.RELEVANCE: "Generic_MostViewed"}
_MMDL = re.compile(r"gamebanana\.com/mmdl/(\d+),(\w+),(\d+)", re.I)


def _thumb(media: dict | None) -> str | None:
    img = ((media or {}).get("_aImages") or [{}])[0]
    return f"{img['_sBaseUrl']}/{img.get('_sFile220') or img['_sFile']}" if img.get("_sBaseUrl") else None


class GameBananaAdapter(SourceAdapter):
    key = "gamebanana"
    display_name = "GameBanana"
    needs_game = False
    text_fallback = True
    adult_support = AdultSupport.UNKNOWN
    default_install = DownloadPolicy.AUTOMATIC
    protocol_schemes = ("modhub:https://gamebanana.com/mmdl/",)

    async def search_games(self, text):
        data = await self.http.get_json(f"{API}/Util/Game/NameMatch", params={"_sName": text})  # (verificar)
        records = data.get("_aRecords", data if isinstance(data, list) else [])
        return [(r.get("_sName", ""), str(r.get("_idRow"))) for r in records if r.get("_idRow")]

    async def find_game_id(self, game_name):
        from .base import strip_edition
        candidates: list[tuple[str, str]] = []
        for text in dict.fromkeys((strip_edition(game_name) or game_name, game_name)):
            candidates += await self.search_games(text)
            if hit := best_name_match(game_name, candidates):
                return hit
        return None

    async def search(self, query, game_ref=None, *, page=1, page_size=20, filters=None):
        order = (filters or SearchFilterOptions()).effective_sort(query)
        gid = self.game_id(game_ref)
        if not query.strip():
            if not gid:
                return []
            data = await self.http.get_json(f"{API}/Mod/Index", params={       # (verificar)
                "_aFilters[Generic_Game]": gid, "_sSort": BROWSE_SORT[order],
                "_nPage": page, "_nPerpage": min(page_size, 50)})
            return [self._record(r) for r in data.get("_aRecords", [])]
        params = {"_sModelName": "Mod", "_sOrder": "best_match", "_sSearchString": query,
                  "_nPage": page, "_nPerpage": min(page_size, 50)}
        if gid := self.game_id(game_ref):
            params["_idGameRow"] = gid
        data = await self.http.get_json(f"{API}/Util/Search/Results", params=params)   # (verificar)
        return [self._record(r) for r in data.get("_aRecords", [])]

    def _record(self, r: dict) -> ModInfo:
        return ModInfo(
            source=self.key, mod_id=str(r["_idRow"]), name=r.get("_sName", ""),
            author=(r.get("_aSubmitter") or {}).get("_sName", ""), page_url=r.get("_sProfileUrl", ""),
            thumbnail=_thumb(r.get("_aPreviewMedia")), downloads=r.get("_nDownloadCount"),
            game_id=str((r.get("_aGame") or {}).get("_idRow", "")) or None,
            raw_categories=[(r.get("_aRootCategory") or {}).get("_sName", "")],
            updated_at=r.get("_tsDateUpdated") or r.get("_tsDateModified"), created_at=r.get("_tsDateAdded"))

    async def _fetch_mod_details(self, mod_id, game_ref):
        d = await self.http.get_json(f"{API}/Mod/{mod_id}/ProfilePage")
        info = ModInfo(
            source=self.key, mod_id=str(mod_id), name=d.get("_sName", ""),
            author=(d.get("_aSubmitter") or {}).get("_sName", ""), description=d.get("_sText", ""),
            page_url=d.get("_sProfileUrl", f"https://gamebanana.com/mods/{mod_id}"),
            thumbnail=_thumb(d.get("_aPreviewMedia")), downloads=d.get("_nDownloadCount"),
            images=[f"{i['_sBaseUrl']}/{i['_sFile']}" for i in (d.get("_aPreviewMedia") or {}).get("_aImages") or []
                    if i.get("_sBaseUrl") and i.get("_sFile")],
            game_id=str((d.get("_aGame") or {}).get("_idRow", "")) or None)
        for i, f in enumerate(d.get("_aFiles", [])):
            info.files.append(ModFile(
                source=self.key, mod_id=str(mod_id), file_id=str(f["_idRow"]), name=f.get("_sFile", ""),
                version=f.get("_sVersion") or "", filename=f.get("_sFile", ""), size=f.get("_nFilesize"),
                is_primary=(i == 0), hashes={"md5": f["_sMd5Checksum"]} if f.get("_sMd5Checksum") else {},
                extra={"url": f.get("_sDownloadUrl")}))
        return info

    async def get_download_ticket(self, file_id, mod_id, game_ref=None, *, context=None):
        info, f = await self._find_file(file_id, mod_id, game_ref)
        if f.extra.get("url"):
            return self._ticket(DownloadPolicy.AUTOMATIC, f, url=f.extra["url"])
        return self._ticket(DownloadPolicy.BROWSER_INTERCEPT, f, intercept=InterceptMethod.DOWNLOADS_FOLDER,
                            page_url=info.page_url, note="Archivo alojado fuera de GameBanana.")

    def parse_protocol_url(self, url):
        m = _MMDL.search(url)
        if not m:
            return None
        file_id, model, item_id = m.groups()
        return ProtocolRequest(source=self.key, mod_id=item_id, file_id=file_id, context={"model": model})
