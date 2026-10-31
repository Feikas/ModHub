"""Mod DB (sin API pública).

  1. Lista de archivos: RSS oficial por mod (lo más estable).
  2. Búsqueda: HTML de /mods?kw=… con parser tolerante.
  3. Descarga: se abre la página en el navegador y ModHub recoge el archivo de Descargas.
     Ritmo: 1 petición cada 2 s. Sin saltarse anuncios ni protecciones.

Los selectores CSS pueden romperse si cambian la web: están todos juntos aquí arriba.
"""
from __future__ import annotations

import re

import feedparser
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..base import AdultSupport, DownloadPolicy, InterceptMethod, ModFile, ModInfo, ModNotFound, SourceAdapter

BASE = "https://www.moddb.com"
RSS = "https://rss.moddb.com/mods/{slug}/downloads/feed/rss.xml"
SEL_RESULT_ROW = "div.row.rowcontent"
SEL_RESULT_TITLE = "h4 a"
SEL_RESULT_SUMMARY = "p"
_DL_ID = re.compile(r"/downloads/([\w-]+)")


class ModDBAdapter(SourceAdapter):
    key = "moddb"
    display_name = "Mod DB"
    needs_game = False
    text_fallback = True
    adult_support = AdultSupport.UNKNOWN
    default_install = DownloadPolicy.BROWSER_INTERCEPT
    can_auto_install = False

    async def search(self, query, game_ref=None, *, page=1, page_size=20, filters=None):
        html = await self.http.get_text(f"{BASE}/mods/page/{page}", params={"filter": "t", "kw": query})
        out = []
        for row in HTMLParser(html).css(SEL_RESULT_ROW):
            a = row.css_first(SEL_RESULT_TITLE)
            href = (a.attributes.get("href") or "") if a else ""
            if "/mods/" not in href:
                continue
            p, img = row.css_first(SEL_RESULT_SUMMARY), row.css_first("img")
            out.append(ModInfo(source=self.key, mod_id=href.rstrip("/").rsplit("/", 1)[-1], name=a.text(strip=True),
                               summary=p.text(strip=True) if p else "", page_url=BASE + href,
                               thumbnail=img.attributes.get("src") if img else None))
        return out[:page_size]

    async def _fetch_mod_details(self, mod_id, game_ref):
        feed = feedparser.parse(await self.http.get_text(RSS.format(slug=mod_id)))
        if feed.bozo and not feed.entries:
            raise ModNotFound(f"Mod DB: no se pudo leer el mod '{mod_id}'.")
        info = ModInfo(source=self.key, mod_id=mod_id, name=feed.feed.get("title", mod_id),
                       page_url=f"{BASE}/mods/{mod_id}")
        for i, e in enumerate(feed.entries):
            m = _DL_ID.search(e.link)
            info.files.append(ModFile(source=self.key, mod_id=mod_id, file_id=m.group(1) if m else e.link,
                                      name=e.title, is_primary=(i == 0), uploaded_at=e.get("published"),
                                      extra={"page_url": e.link}))
        return info

    async def get_download_ticket(self, file_id, mod_id, game_ref=None, *, context=None):
        _, f = await self._find_file(file_id, mod_id, game_ref)
        return self._ticket(DownloadPolicy.BROWSER_INTERCEPT, f, intercept=InterceptMethod.DOWNLOADS_FOLDER,
                            page_url=f.extra["page_url"],
                            note="Mod DB no permite descarga automática: se abrirá la web y ModHub recogerá el archivo.")
