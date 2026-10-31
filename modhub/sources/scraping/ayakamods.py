"""AyakaMods (web XenForo multijuego, muy usada para gachas y juegos asiáticos).

Comprobado contra la web real (octubre 2026):
- Catálogo de juegos:   GET /games/?search=<texto>         -> tarjetas  a.gameCard-link  /games/<slug>.<id>/
- Explorar un juego:    GET /?game_id=<id>&order=<orden>&direction=desc&page=<n>   (18 mods por página)
                        órdenes: last_update · download_count · rating_weighted · mod_date · title
- Buscar por texto:     POST /mods/search  (q, game_id, _xfToken, _xfResponseType=json)
                        -> {"html": {"content": <tarjetas>}, "total": n}. Es lo que usa su buscador.
- Tarjeta de mod:       div.cardModMainWrapperLink (título, resumen, juego, autor, descargas, fechas, icono)
- Ficha:                /mods/<slug>.<id>/ ; descargar exige iniciar sesión en la web -> se abre en el navegador.

ID de juego (GameRef.ids["ayakamods"]) = número del juego, p. ej. "417" (Neverness to Everness).
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..base import (AdultSupport, DownloadPolicy, InterceptMethod, ModFile, ModInfo, SearchFilterOptions, SortOrder,
                    SourceAdapter, best_name_match, sort_key, strip_edition)

BASE = "https://ayakamods.com"
PER_PAGE = 18
ORDER = {SortOrder.TRENDING: "download_count", SortOrder.DOWNLOADS: "download_count",
         SortOrder.NEWEST: "mod_date", SortOrder.UPDATED: "last_update", SortOrder.RELEVANCE: "download_count"}
ADULT_WORDS = re.compile(r"\b(nsfw|18\+|r18|r-18|nude|nudity|naked|topless|bottomless|hentai|lewd|explicit|"
                         r"porn|sex|sexy|uncensor\w*|decensor\w*|futa\w*|lingerie)\b", re.I)
_MOD_URL = re.compile(r"/mods/([\w%.-]+?\.\d+)/?(?:$|[?#\"])")
_GAME_URL = re.compile(r"/games/[\w-]+\.(\d+)/?")
_GAME_ID = re.compile(r"game_id=(\d+)")
TOKEN_TTL = 1800.0


def _abs(url: str | None) -> str | None:
    if not url:
        return None
    return url if url.startswith("http") else f"{BASE}/{url.lstrip('/')}"


def _int(text: str | None) -> int | None:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def _ts(node) -> str | None:
    """<time data-timestamp="…"> -> ISO 8601."""
    t = node.css_first("time[data-timestamp]") if node is not None else None
    if t is None:
        return None
    try:
        return datetime.fromtimestamp(int(t.attributes["data-timestamp"]), timezone.utc).isoformat()
    except (KeyError, TypeError, ValueError):
        return None


def looks_adult(*texts: str) -> bool:
    return any(ADULT_WORDS.search(t or "") for t in texts)


class AyakaModsAdapter(SourceAdapter):
    key = "ayakamods"
    display_name = "AyakaMods"
    needs_game = False
    search_needs_auth = False
    text_fallback = True                      # si no tiene el juego, se busca su nombre como texto
    adult_support = AdultSupport.FLAGGED      # etiqueta NSFW o palabras claras en título/resumen
    default_install = DownloadPolicy.BROWSER_INTERCEPT
    can_auto_install = False

    def __init__(self, http, settings=None):
        super().__init__(http, settings)
        self._token: tuple[float, str] | None = None
        self._games_cache: dict[str, list[tuple[str, str]]] = {}

    # ── juegos ─────────────────────────────────────────────────────────
    async def search_games(self, text):
        text = text.strip()
        if text.lower() in self._games_cache:
            return self._games_cache[text.lower()]
        html = await self.http.get_text(f"{BASE}/games/", params={"search": text})
        out, seen = [], set()
        for a in HTMLParser(html).css("a.gameCard-link, div.gameCard a"):
            m = _GAME_URL.search(a.attributes.get("href") or "")
            if not m or m.group(1) in seen:
                continue
            title = a.css_first(".gameCard-title")
            img = a.css_first("img")
            name = (title.text(strip=True) if title else "") or (img.attributes.get("alt") if img else "") or ""
            if name:
                seen.add(m.group(1))
                out.append((name, m.group(1)))
        self._games_cache[text.lower()] = out
        return out

    async def find_game_id(self, game_name):
        candidates: list[tuple[str, str]] = []
        for text in dict.fromkeys((strip_edition(game_name) or game_name, game_name)):
            candidates += await self.search_games(text)
            if hit := best_name_match(game_name, candidates):
                return hit
        # La web busca por «contiene»: probar con la primera palabra larga ayuda con nombres raros
        words = [w for w in re.split(r"\W+", strip_edition(game_name) or game_name) if len(w) >= 4]
        if words and words[0].lower() != (strip_edition(game_name) or game_name).lower():
            candidates += await self.search_games(words[0])
            return best_name_match(game_name, candidates)
        return None

    # ── búsqueda ───────────────────────────────────────────────────────
    async def search(self, query, game_ref=None, *, page=1, page_size=20, filters=None):
        filters = filters or SearchFilterOptions()
        order = filters.effective_sort(query)
        gid = self.game_id(game_ref)
        if query.strip():
            mods = await self._text_search(query.strip(), gid)
            by = SortOrder.DOWNLOADS if order is SortOrder.TRENDING else order
            if sort_key(ModInfo("", "", ""), by) is not None:          # «Recomendado» = orden de la web
                mods.sort(key=lambda m: sort_key(m, by) or 0, reverse=True)
            start = (page - 1) * page_size
            return mods[start:start + page_size]
        # Explorar: la web da 18 por página; se piden las necesarias para llenar page_size
        out: list[ModInfo] = []
        first = (page - 1) * page_size // PER_PAGE + 1
        skip = (page - 1) * page_size - (first - 1) * PER_PAGE
        web_page = first
        while len(out) < skip + page_size:
            params = {"order": ORDER[order], "direction": "desc", "page": web_page}
            if gid:
                params["game_id"] = gid
            html = await self.http.get_text(f"{BASE}/", params=params)
            batch = [m for m in self._parse_cards(html, gid) if m.mod_id not in {o.mod_id for o in out}]
            out += batch
            if not batch or f"page={web_page + 1}" not in html or web_page - first >= 3:
                break
            web_page += 1
        return out[skip:skip + page_size]

    async def _csrf(self, *, fresh: bool = False) -> str:
        if self._token and not fresh and time.monotonic() - self._token[0] < TOKEN_TTL:
            return self._token[1]
        html = await self.http.get_text(f"{BASE}/games/")
        m = re.search(r'data-csrf="([^"]+)"', html) or re.search(r"csrf:\s*'([^']+)'", html)
        if not m:
            raise RuntimeError("AyakaMods ha cambiado su web: no se encuentra el token de búsqueda.")
        self._token = (time.monotonic(), m.group(1))
        return m.group(1)

    async def _text_search(self, query: str, gid: str | None) -> list[ModInfo]:
        for attempt in (0, 1):
            data = {"q": query, "_xfToken": await self._csrf(fresh=attempt == 1), "_xfResponseType": "json"}
            if gid:
                data["game_id"] = gid
            resp = await self.http.request("POST", f"{BASE}/mods/search", data=data,
                                           headers={"X-Requested-With": "XMLHttpRequest",
                                                    "Accept": "application/json, text/javascript, */*; q=0.01"})
            try:
                payload = resp.json()
            except ValueError:
                payload = {}
            html = (payload.get("html") or {}).get("content") if isinstance(payload, dict) else None
            if html is not None:
                return self._parse_cards(html, gid)
            if payload.get("errors") and attempt == 0:      # token caducado: se pide otro y se repite
                continue
            break
        raise RuntimeError("AyakaMods no ha devuelto resultados (¿ha cambiado su web?).")

    def _parse_cards(self, html: str, gid: str | None) -> list[ModInfo]:
        out, seen = [], set()
        for card in HTMLParser(html).css("div.cardModMainWrapperLink"):
            link = card.css_first("a.modCardLink") or card.css_first("a[href*='/mods/']")
            m = _MOD_URL.search(link.attributes.get("href") or "") if link else None
            if not m or m.group(1) in seen:
                continue
            mod_id = m.group(1)
            seen.add(mod_id)
            title = card.css_first(".cMMBodyTitle")
            name = title.text(strip=True) if title else mod_id
            summary_n = card.css_first(".cMMBodyText")
            summary = summary_n.text(strip=True) if summary_n else ""
            tags = " ".join(t.text(strip=True) for t in card.css(".content-label .content-tag, .content-label .label"))
            game_link = card.css_first("a.cMMGameLink")
            game_id = gid
            if game_link is not None and (gm := _GAME_ID.search(game_link.attributes.get("href") or "")):
                game_id = gm.group(1)
            cat = card.css_first(".cMMCategoryText")
            author = card.css_first(".cMMFootReactionsUsername a.username") or card.css_first("a.username")
            img = card.css_first(".cMMPictureWrapper img")
            dl = card.css_first('[data-stat="downloads"]')
            updated = created = None
            for el in card.css(".cMMFootDateElement"):
                label = (el.css_first(".cMMFootDateLabel").text(strip=True).lower()
                         if el.css_first(".cMMFootDateLabel") else "")
                if "upload" in label:
                    created = _ts(el)
                else:
                    updated = _ts(el)
            if updated is None:
                updated = _ts(card.css_first(".cMMFoot"))
            adult = "nsfw" in tags.lower() or "18+" in tags or looks_adult(name, summary)
            out.append(ModInfo(
                source=self.key, mod_id=mod_id, name=name, summary=summary,
                author=author.text(strip=True) if author else card.attributes.get("data-author", "") or "",
                page_url=f"{BASE}/mods/{mod_id}/", thumbnail=_abs(img.attributes.get("src")) if img else None,
                downloads=_int(dl.text(strip=True)) if dl else None, updated_at=updated, created_at=created or updated,
                game_id=game_id, is_adult=adult,
                raw_categories=[cat.text(strip=True)] if cat else [],
                extra={"game": game_link.text(strip=True) if game_link else "", "repost": "repost" in tags.lower()}))
        return out

    # ── ficha y descarga ───────────────────────────────────────────────
    async def _fetch_mod_details(self, mod_id, game_ref):
        page_url = f"{BASE}/mods/{mod_id}/"
        tree = HTMLParser(await self.http.get_text(page_url))
        h1 = tree.css_first("h1")
        version_n = h1.css_first(".u-muted") if h1 else None
        version = version_n.text(strip=True) if version_n else ""
        name = h1.text(strip=True) if h1 else mod_id
        if version and name.endswith(version):
            name = name[: -len(version)].strip()
        pairs: dict[str, object] = {}
        for dl in tree.css("dl.pairs"):
            dt, dd = dl.css_first("dt"), dl.css_first("dd")
            if dt and dd:
                pairs[dt.text(strip=True).lower()] = dd
        def pair_text(k):  # noqa: E306
            n = pairs.get(k)
            return n.text(strip=True) if n is not None else ""
        body = tree.css_first("div.bbWrapper")
        og = tree.css_first('meta[property="og:image"]')
        images = []
        for img in tree.css("a.file-preview img, .bbWrapper img.bbImage"):
            src = _abs(img.attributes.get("src"))
            if src and src not in images and "/smilies/" not in src:
                images.append(src)
        author = pair_text("uploaded by") or (tree.css_first(".mod-subtitle a.username").text(strip=True)
                                              if tree.css_first(".mod-subtitle a.username") else "")
        game_n = tree.css_first(".akl-breadcrumbs a[href*='/games/']")
        game_id = None
        if game_n is not None and (gm := _GAME_URL.search(game_n.attributes.get("href") or "")):
            game_id = gm.group(1)
        button = tree.css_first(".modSidebarGroup--buttons .button, .modSidebarGroup--buttons a.button")
        btn_text = button.text(strip=True) if button else ""
        page_text = tree.body.text() if tree.body else ""
        description = body.text(separator="\n", strip=True) if body else ""
        info = ModInfo(
            source=self.key, mod_id=mod_id, name=name, author=author, description=description,
            summary=description[:300], page_url=page_url, thumbnail=_abs(og.attributes.get("content")) if og else None,
            images=images[:12], downloads=_int(pair_text("downloads")),
            created_at=_ts(pairs.get("first release")), updated_at=_ts(pairs.get("last update")),
            game_id=game_id, is_adult=looks_adult(name, description[:600]))
        info.files.append(ModFile(
            source=self.key, mod_id=mod_id, file_id=mod_id, name=name, version=version or pair_text("version"),
            is_primary=True, extra={"external": "external file" in page_text.lower(),
                                    "login": "log in" in btn_text.lower() or "sign up" in btn_text.lower(),
                                    "size": pair_text("total size")}))
        return info

    async def get_download_ticket(self, file_id, mod_id, game_ref=None, *, context=None):
        info, f = await self._find_file(file_id, mod_id, game_ref)
        if f.extra.get("external"):
            note = "Archivo alojado fuera de AyakaMods: descárgalo desde el enlace de la ficha."
        elif f.extra.get("login"):
            note = ("AyakaMods pide iniciar sesión (gratis) para descargar. Entra con tu cuenta en la web que se "
                    "abre y pulsa Download: ModHub recogerá el archivo de tu carpeta de Descargas.")
        else:
            note = "Se abrirá la ficha del mod: pulsa Download y ModHub recogerá el archivo."
        return self._ticket(DownloadPolicy.BROWSER_INTERCEPT, f, intercept=InterceptMethod.DOWNLOADS_FOLDER,
                            page_url=info.page_url, note=note)
