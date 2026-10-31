"""AyakaMods (estructura real de la web) y búsqueda por nombre en webs multijuego."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modhub.sources import GameRef, HttpClient, RateLimit, SourceRegistry  # noqa: E402
from modhub.sources.base import AdultFilter, SearchFilterOptions, SortOrder  # noqa: E402

CARD = """<div class="cardModMainWrapperLink js-inlineModContainer" data-author="{author}">
<a href="/mods/{slug}/" class="modCardLink"><div class="cardModMain">
<div class="content-label d-flex">{tag}</div>
<div class="cMMPictureWrapper"><img src="/data/mod_icons/5/{n}.webp?1" alt="{name}"></div>
<div class="cMMBody"><div class="cMMBodyTitle">{name}</div><div class="cMMBodyText">{summary}</div>
<div class="cMMBodyGameCategory"><a href="/?game_id=417" class="cMMGameLink"> Neverness to Everness </a>
<span class="cMMCategoryText">Outfits</span></div></div>
<div class="cMMFoot"><div class="cMMFootReactionsUsername"><div class="cMMFootReactionsElement">
<a href="/members/x.1/" class="username"><span>{author}</span></a></div></div>
<div class="cMMFootReactions"><div class="cMMFootReactionsElement" data-stat="downloads"><span>{dl}</span></div></div>
<div class="cMMFootDates"><div class="cMMFootDateElement"><span class="cMMFootDateLabel">Updated:</span>
<time class="u-dt" data-timestamp="1793366584">x</time></div>
<div class="cMMFootDateElement"><span class="cMMFootDateLabel">Uploaded:</span>
<time class="u-dt" data-timestamp="1787268254">x</time></div></div></div></div></a></div>"""


def card(n, name, dl, summary="", tag="", author="Nisha"):
    return CARD.format(slug=f"{name.lower().replace(' ', '-')}.{n}", n=n, name=name, dl=f"{dl:,}",
                       summary=summary, tag=tag, author=author)


GAMES = """<html data-csrf="111,abc"><div class="gameGrid"><div class="gameCard">
<a href="/games/neverness-to-everness.417/" class="gameCard-link"><img alt="Neverness to Everness">
<div class="gameCard-title">Neverness to Everness</div></a></div></div></html>"""
LIST = "<html><main>" + card(5661, "Akane Outfit", 1200) + card(2430, "Ayaka Decensored", 6846) + "</main></html>"
SEARCH = {"html": {"content": card(1, "Low Outfit", 10) +
                   card(2, "Big Outfit", 900, tag='<span class="content-tag">NSFW</span>')}, "total": 2}
MOD = """<html><head><meta property="og:image" content="https://ayakamods.com/data/mod_icons/2/2430.webp"></head><body>
<h1> Ayaka NTE Decensored <span class="u-muted">1.0.0</span> </h1>
<ul class="akl-breadcrumbs"><li><a href="/games/neverness-to-everness.417/">NTE</a></li></ul>
<div class="bbWrapper">Remove transparency fade</div>
<a class="file-preview js-lbImage" href="/attachments/2-png.1/"><img src="/data/attachments/22/22340.jpg"></a>
<div class="modSidebarGroup modSidebarGroup--buttons"><span class="button is-disabled">Log in or sign up to download</span></div>
<dl class="pairs"><dt>Uploaded by</dt><dd><a class="username">Senku Aoki</a></dd></dl>
<dl class="pairs"><dt>Downloads</dt><dd>6,846</dd></dl>
<dl class="pairs"><dt>Last update</dt><dd><time data-timestamp="1779811719">x</time></dd></dl>
</body></html>"""


def run(coro):
    return asyncio.run(coro)


def make(log):
    def handler(req: httpx.Request):
        log.append(req)
        p = req.url.path
        if p == "/games/":
            return httpx.Response(200, text=GAMES)
        if p == "/mods/search":
            return httpx.Response(200, json=SEARCH)
        if p.startswith("/mods/"):
            return httpx.Response(200, text=MOD)
        if p == "/":
            return httpx.Response(200, text=LIST)
        return httpx.Response(404)
    return HttpClient(transport=httpx.MockTransport(handler), limits={"ayakamods.com": RateLimit(1000, 1000)})


def test_ayakamods_encuentra_el_juego_por_nombre():
    log = []

    async def go():
        async with make(log) as http:
            ad = SourceRegistry(http, only=["ayakamods"]).get("ayakamods")
            return await ad.find_game_id("Neverness to Everness"), await ad.search_games("never")
    gid, games = run(go())
    assert gid == "417" and games == [("Neverness to Everness", "417")]
    assert log[0].url.params["search"]


def test_ayakamods_explora_un_juego_por_descargas():
    log = []

    async def go():
        async with make(log) as http:
            reg = SourceRegistry(http, only=["ayakamods"])
            ref = GameRef("Neverness to Everness", ids={"ayakamods": "417"})
            return await reg.search_all("", ref, filters=SearchFilterOptions(adult=AdultFilter.ALL,
                                                                              sort=SortOrder.DOWNLOADS))
    r = run(go())
    mods = r.by_source["ayakamods"]
    assert [m.name for m in mods] == ["Ayaka Decensored", "Akane Outfit"]       # ordenado por descargas
    first = mods[1]
    assert first.downloads == 1200 and first.author == "Nisha" and first.game_id == "417"
    assert first.thumbnail.startswith("https://ayakamods.com/data/mod_icons/")
    assert first.updated_at.startswith("2026-10-30") and first.created_at.startswith("2026-08")
    assert mods[0].is_adult is True and first.is_adult is False
    q = log[0].url.params
    assert q["game_id"] == "417" and q["order"] == "download_count"


def test_ayakamods_busca_texto_con_token_y_oculta_nsfw():
    log = []

    async def go():
        async with make(log) as http:
            reg = SourceRegistry(http, only=["ayakamods"])
            ref = GameRef("Neverness to Everness", ids={"ayakamods": "417"})
            return await reg.search_all("outfit", ref)
    r = run(go())
    assert [m.name for m in r.by_source["ayakamods"]] == ["Low Outfit"]
    assert r.filtered_out["ayakamods"] == 1
    post = next(x for x in log if x.method == "POST")
    body = parse_qs(post.content.decode())
    assert body["q"] == ["outfit"] and body["game_id"] == ["417"] and body["_xfToken"] == ["111,abc"]


def test_ayakamods_ficha_y_descarga_por_navegador():
    async def go():
        async with make([]) as http:
            ad = SourceRegistry(http, only=["ayakamods"]).get("ayakamods")
            info = await ad.get_mod_details("ayaka-nte-decensored.2430")
            return info, await ad.get_download_ticket(info.files[0].file_id, info.mod_id)
    info, t = run(go())
    assert info.name == "Ayaka NTE Decensored" and info.files[0].version == "1.0.0"
    assert info.downloads == 6846 and info.author == "Senku Aoki" and info.game_id == "417"
    assert info.images == ["https://ayakamods.com/data/attachments/22/22340.jpg"] and info.is_adult
    assert t.page_url.endswith("/mods/ayaka-nte-decensored.2430/") and "iniciar sesión" in t.note


def test_web_multijuego_sin_el_juego_busca_por_su_nombre():
    log = []

    async def go():
        async with make(log) as http:
            reg = SourceRegistry(http, only=["ayakamods"])
            return await reg.search_all("outfit", GameRef("Neverness to Everness: Deluxe Edition", ids={}))
    r = run(go())
    assert "ayakamods" in r.by_name and "ayakamods" not in r.skipped
    body = parse_qs(next(x for x in log if x.method == "POST").content.decode())
    assert body["q"] == ["Neverness to Everness outfit"] and "game_id" not in body


def test_slugs_con_caracteres_chinos_y_siglas_delante():
    from modhub.sources.base import best_name_match, strip_edition
    from modhub.sources.scraping.ayakamods import _MOD_URL
    assert _MOD_URL.search("/mods/%E5%AE%89%E9%AD%82_%E8%A1%A3.2750/").group(1) == "%E5%AE%89%E9%AD%82_%E8%A1%A3.2750"
    assert _MOD_URL.search("/mods/latest-reviews") is None
    assert strip_edition("NTE: Neverness to Everness") == "Neverness to Everness"
    assert strip_edition("Zenless Zone Zero (ZZZ)") == "Zenless Zone Zero"
    assert strip_edition("Wanted: Dead") == "Wanted: Dead"                  # no son siglas: se queda igual
    assert best_name_match("NTE: Neverness to Everness", [("Neverness To Everness", "417")]) == "417"
