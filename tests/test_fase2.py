"""Pruebas sin red de la Fase 2.

Se usa httpx.MockTransport: las peticiones pasan por el HttpClient real (límites,
reintentos, cabeceras) pero las respuestas las fabrica la prueba.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from modhub.db.database import Database
from modhub.sources import (AuthRequired, DownloadPolicy, GameRef, HttpClient, HttpError,
                            InterceptMethod, RateLimit, SourceRegistry)
from modhub.sources.nexus import NexusAuth


# ─── Utilidades ─────────────────────────────────────────────────────────
class Router:
    """Mapa 'fragmento de URL' -> respuesta (dict/list = JSON, str = texto, int = código de error)."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        url = str(request.url)
        for frag, payload in self.routes.items():
            if frag in url:
                if callable(payload):
                    payload = payload(request)
                if isinstance(payload, httpx.Response):
                    return payload
                if isinstance(payload, int):
                    return httpx.Response(payload, text="error")
                if isinstance(payload, str):
                    return httpx.Response(200, text=payload)
                return httpx.Response(200, json=payload)
        return httpx.Response(404, text=f"sin ruta simulada: {url}")


def make_http(router: Router, **kw) -> HttpClient:
    kw.setdefault("limits", {h: RateLimit(1000, burst=1000) for h in
                             ("api.modrinth.com", "thunderstore.io", "api.nexusmods.com", "api.curseforge.com",
                              "api.steampowered.com", "rss.moddb.com", "example.com")})
    return HttpClient(transport=httpx.MockTransport(router), backoff_base=0.01, **kw)


def run(coro):
    return asyncio.run(coro)


LETHAL = GameRef("Lethal Company", ids={"thunderstore": "lethal-company", "nexus": "lethalcompany"})


# ─── HttpClient ─────────────────────────────────────────────────────────
def test_http_reintenta_429_y_respeta_retry_after():
    state = {"n": 0}

    def flaky(_req):
        state["n"] += 1
        if state["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    async def go():
        async with make_http(Router({"example.com": flaky})) as http:
            return await http.get_json("https://example.com/x")
    assert run(go()) == {"ok": True} and state["n"] == 3


def test_http_error_definitivo_lanza_httperror():
    async def go():
        async with make_http(Router({"example.com": 404})) as http:
            await http.get_json("https://example.com/x")
    with pytest.raises(HttpError) as e:
        run(go())
    assert e.value.status == 404


def test_http_rate_limit_espacia_peticiones():
    router = Router({"lento.test": {"ok": 1}})

    async def go():
        async with make_http(router, limits={"lento.test": RateLimit(20, burst=1)}) as http:
            t = time.perf_counter()
            await asyncio.gather(*(http.get_json("https://lento.test/") for _ in range(5)))
            return time.perf_counter() - t
    assert run(go()) >= 0.18          # 5 peticiones a 20/s con ráfaga 1 -> ≥ 0.2 s


def test_http_envia_user_agent():
    router = Router({"example.com": {"ok": 1}})

    async def go():
        async with make_http(router) as http:
            await http.get_json("https://example.com/")
    run(go())
    assert router.calls[0].headers["user-agent"].startswith("ModHub/")


# ─── Modrinth ───────────────────────────────────────────────────────────
MODRINTH = {
    "/v2/search": lambda r: {"hits": [{"project_id": "AANobbMI", "slug": "sodium", "title": "Sodium",
                                       "author": "jellysquid3", "description": "rápido", "project_type": "mod",
                                       "downloads": 100, "icon_url": "i", "date_modified": "d",
                                       "facets_seen": json.loads(r.url.params["facets"])}]},
    "/v2/project/AANobbMI/version": [
        {"id": "v2", "name": "Sodium 0.6.1", "version_number": "0.6.1", "version_type": "release",
         "loaders": ["fabric"], "game_versions": ["1.21.1"], "date_published": "2026-01-01",
         "dependencies": [{"project_id": "P7dR8mSH", "dependency_type": "required"},
                          {"project_id": "x", "dependency_type": "incompatible"}],
         "files": [{"url": "https://cdn.modrinth.com/s.jar", "filename": "sodium.jar", "size": 10,
                    "primary": True, "hashes": {"sha1": "ab", "sha512": "cd"}}]}],
    "/v2/project/AANobbMI/members": [{"role": "Owner", "user": {"username": "jellysquid3"}}],
    "/v2/project/AANobbMI": {"id": "AANobbMI", "slug": "sodium", "title": "Sodium", "description": "rápido",
                             "body": "# Sodium", "project_type": "mod", "downloads": 100, "updated": "u"},
}


def test_modrinth_busqueda_usa_filtros_de_cargador_y_version():
    router = Router(MODRINTH)

    async def go():
        async with make_http(router) as http:
            reg = SourceRegistry(http, only=["modrinth"])
            return await reg.get("modrinth").search("sodium", GameRef("MC", ids={"modrinth": "fabric@1.21.1"}))
    res = run(go())
    facets = json.loads(router.calls[0].url.params["facets"])
    assert res[0].name == "Sodium" and ["categories:fabric"] in facets and ["versions:1.21.1"] in facets


def test_modrinth_detalles_y_ticket_automatico():
    async def go():
        async with make_http(Router(MODRINTH)) as http:
            ad = SourceRegistry(http, only=["modrinth"]).get("modrinth")
            info = await ad.get_mod_details("AANobbMI")
            ticket = await ad.get_download_ticket("v2", "AANobbMI")
            return info, ticket
    info, t = run(go())
    f = info.latest_file()
    assert info.author == "jellysquid3" and f.file_id == "v2" and f.loaders == ["fabric"]
    assert [d.mod_id for d in f.dependencies] == ["P7dR8mSH"]          # el "incompatible" se ignora
    assert t.policy is DownloadPolicy.AUTOMATIC and t.url.endswith("s.jar") and t.hashes["sha512"] == "cd"


# ─── Thunderstore ──────────────────────────────────────────────────────
TS_INDEX = [
    {"full_name": "Owner-MoreCompany", "name": "MoreCompany", "owner": "Owner", "package_url": "u",
     "is_deprecated": False, "has_nsfw_content": False, "date_updated": "d", "categories": [],
     "versions": [{"full_name": "Owner-MoreCompany-1.9.0", "name": "MoreCompany", "version_number": "1.9.0",
                   "download_url": "https://thunderstore.io/package/download/Owner/MoreCompany/1.9.0/",
                   "downloads": 5000, "description": "más jugadores", "icon": "i", "file_size": 123,
                   "dependencies": ["BepInEx-BepInExPack-5.4.2100"]}]},
    {"full_name": "Old-Dead", "name": "DeadMod", "owner": "Old", "package_url": "u", "is_deprecated": True,
     "versions": [{"full_name": "Old-Dead-1.0.0", "name": "DeadMod", "version_number": "1.0.0",
                   "download_url": "x", "downloads": 9}]},
]


def test_thunderstore_busca_en_local_y_descarga_el_indice_una_vez(tmp_path: Path):
    router = Router({"/c/lethal-company/api/v1/package/": TS_INDEX})

    async def go():
        async with make_http(router) as http:
            ad = SourceRegistry(http, cache=Database(tmp_path / "t.db"), only=["thunderstore"]).get("thunderstore")
            found = await asyncio.gather(ad.search("company", LETHAL), ad.search("dead", LETHAL))
            info = await ad.get_mod_details("Owner-MoreCompany", LETHAL)
            ticket = await ad.get_download_ticket("Owner-MoreCompany-1.9.0", "Owner-MoreCompany", LETHAL)
            return found, info, ticket
    (hits, deprecated), info, t = run(go())
    assert [m.mod_id for m in hits] == ["Owner-MoreCompany"] and deprecated == []
    dep = info.files[0].dependencies[0]
    assert (dep.mod_id, dep.file_id) == ("BepInEx-BepInExPack", "BepInEx-BepInExPack-5.4.2100")
    assert t.policy is DownloadPolicy.AUTOMATIC and t.filename == "Owner-MoreCompany-1.9.0.zip"
    assert len(router.calls) == 1


# ─── Nexus ──────────────────────────────────────────────────────────────
NEXUS_MOD = {"mod_id": 266, "name": "SkyUI", "author": "SkyUI Team", "summary": "UI", "available": True}
NEXUS_FILES = {"files": [
    {"file_id": 2, "name": "SkyUI", "version": "5.2", "file_name": "SkyUI_5_2.7z", "size_in_bytes": 100,
     "category_name": "MAIN", "is_primary": True, "md5": "aa", "uploaded_timestamp": 2},
    {"file_id": 1, "name": "Viejo", "category_name": "ARCHIVED", "uploaded_timestamp": 1}]}


def nexus_routes(premium: bool):
    return {"users/validate.json": {"user_id": 1, "name": "didac", "is_premium": premium},
            "mods/266/files/2/download_link.json": [{"URI": "https://cdn.nexus/SkyUI.7z", "short_name": "Paris"}],
            "mods/266/files.json": NEXUS_FILES, "mods/266.json": NEXUS_MOD}


def nexus_registry(http):
    return SourceRegistry(http, {"nexus": {"api_key": "k", "use_keyring": False}}, only=["nexus"])


SKYRIM = GameRef("Skyrim SE", ids={"nexus": "skyrimspecialedition"})


def test_nexus_gratis_pide_pasar_por_la_web():
    async def go():
        async with make_http(Router(nexus_routes(premium=False))) as http:
            return await nexus_registry(http).get("nexus").get_download_ticket("2", "266", SKYRIM)
    t = run(go())
    assert t.policy is DownloadPolicy.BROWSER_INTERCEPT and t.intercept is InterceptMethod.PROTOCOL
    assert "file_id=2" in t.page_url


def test_nexus_flujo_nxm_completo_con_cuenta_gratis():
    router = Router(nexus_routes(premium=False))

    async def go():
        async with make_http(router) as http:
            reg = nexus_registry(http)
            req = reg.route_protocol("nxm://skyrimspecialedition/mods/266/files/2?key=abc&expires=99&user_id=1")
            ad = reg.get(req.source)
            return await ad.get_download_ticket(req.file_id, req.mod_id, context=req.context)
    t = run(go())
    assert t.policy is DownloadPolicy.AUTOMATIC and t.url == "https://cdn.nexus/SkyUI.7z"
    dl = next(c for c in router.calls if "download_link" in str(c.url))
    assert dl.url.params["key"] == "abc" and dl.headers["apikey"] == "k"


def test_nexus_premium_descarga_directa_y_oculta_archivados():
    async def go():
        async with make_http(Router(nexus_routes(premium=True))) as http:
            ad = nexus_registry(http).get("nexus")
            return await ad.get_mod_details("266", SKYRIM), await ad.get_download_ticket("2", "266", SKYRIM)
    info, t = run(go())
    assert [f.file_id for f in info.files] == ["2"] and t.policy is DownloadPolicy.AUTOMATIC


def test_nexus_key_invalida_da_authrequired():
    async def go():
        async with make_http(Router({"users/validate.json": 401})) as http:
            await nexus_registry(http).get("nexus").validate()
    with pytest.raises(AuthRequired):
        run(go())


def test_nexus_sin_key_no_esta_configurado():
    async def go():
        async with make_http(Router({})) as http:
            reg = SourceRegistry(http, {"nexus": {"use_keyring": False}}, only=["nexus"])
            return reg.status()["nexus"]["configured"]
    assert run(go()) is False


def test_nexus_auth_sso_recibe_la_key():
    """Simula el servidor SSO de Nexus sin red."""
    class FakeWS:
        def __init__(self):
            self.sent = []
            self.msgs = [{"success": True, "data": {"connection_token": "t"}},
                         {"success": True, "data": {"api_key": "CLAVE"}}]
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def send(self, m): self.sent.append(json.loads(m))
        def __aiter__(self): return self
        async def __anext__(self):
            if not self.msgs:
                raise StopAsyncIteration
            return json.dumps(self.msgs.pop(0))

    import sys, types
    ws = FakeWS()
    sys.modules["websockets"] = types.SimpleNamespace(connect=lambda url: ws)
    opened = []
    try:
        auth = NexusAuth(use_keyring=False)
        key = run(auth.login_sso("modhub", open_browser=opened.append, timeout=5))
    finally:
        del sys.modules["websockets"]
    assert key == "CLAVE" and auth.api_key == "CLAVE"
    assert "application=modhub" in opened[0] and ws.sent[0]["protocol"] == 2


# ─── Otras fuentes ─────────────────────────────────────────────────────
def test_curseforge_respeta_la_decision_del_autor():
    routes = {"/v1/mods/1/files": {"data": [{"id": 2, "displayName": "M 1.0", "fileName": "m.jar",
                                             "downloadUrl": None, "hashes": [], "dependencies": []}]},
              "/v1/mods/1": {"data": {"id": 1, "name": "M", "gameId": 432, "allowModDistribution": False,
                                      "links": {"websiteUrl": "https://www.curseforge.com/minecraft/mc-mods/m"}}}}

    async def go():
        async with make_http(Router(routes)) as http:
            reg = SourceRegistry(http, {"curseforge": {"api_key": "x"}}, only=["curseforge"])
            return await reg.get("curseforge").get_download_ticket("2", "1")
    t = run(go())
    assert t.policy is DownloadPolicy.BROWSER_INTERCEPT and t.intercept is InterceptMethod.DOWNLOADS_FOLDER


def test_moddb_lee_archivos_por_rss():
    rss = ('<?xml version="1.0"?><rss version="2.0"><channel><title>Foo</title><item><title>Patch 1.2</title>'
           '<link>https://www.moddb.com/mods/foo/downloads/patch-12</link></item></channel></rss>')

    async def go():
        async with make_http(Router({"rss.moddb.com": rss})) as http:
            ad = SourceRegistry(http, only=["moddb"]).get("moddb")
            return await ad.get_download_ticket("patch-12", "foo")
    assert run(go()).policy is DownloadPolicy.BROWSER_INTERCEPT


# ─── Registro / búsqueda global ────────────────────────────────────────
def test_search_all_en_paralelo_aisla_errores_y_omite_lo_que_no_aplica(tmp_path):
    routes = {"/c/lethal-company/api/v1/package/": TS_INDEX,
              "api.modrinth.com": 503,           # Modrinth "caído"
              "graphql": {"data": {"mods": {"nodes": []}}}}

    async def go():
        async with make_http(Router(routes), max_retries=1) as http:
            reg = SourceRegistry(http, {"nexus": {"api_key": "k", "use_keyring": False}},
                                 only=["thunderstore", "modrinth", "nexus", "curseforge", "gamebanana"])
            game = GameRef("Prueba", ids={**LETHAL.ids, "modrinth": "fabric", "curseforge": "1"})
            return await reg.search_all("company", game)
    r = run(go())
    assert "gamebanana" in r.by_name                 # web multijuego sin ID -> se busca por el nombre del juego
    assert [m.name for m in r.by_source["thunderstore"]] == ["MoreCompany"]
    assert "503" in r.errors["modrinth"]
    assert r.by_source["nexus"] == []
    assert "API key" in r.skipped["curseforge"]
    assert r.merged()[0].name == "MoreCompany" and set(r.timings) == {"thunderstore", "modrinth", "nexus", "gamebanana"}


def test_search_all_timeout_por_fuente():
    class SlowTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            await asyncio.sleep(5)
            return httpx.Response(200, json={})

    async def go():
        async with HttpClient(transport=SlowTransport()) as http:
            reg = SourceRegistry(http, only=["modrinth"])
            return await reg.search_all("x", timeout=0.2)
    r = run(go())
    assert r.errors["modrinth"] == "Tiempo de espera agotado."


def test_protocolos():
    async def go():
        async with make_http(Router({})) as http:
            return SourceRegistry(http)
    reg = run(go())
    ts = reg.route_protocol("ror2mm://v1/install/thunderstore.io/BepInEx/BepInExPack/5.4.2100/")
    gb = reg.route_protocol("modhub:https://gamebanana.com/mmdl/987654,Mod,445566")
    assert (ts.source, ts.mod_id, ts.file_id) == ("thunderstore", "BepInEx-BepInExPack", "BepInEx-BepInExPack-5.4.2100")
    assert (gb.source, gb.mod_id, gb.file_id) == ("gamebanana", "445566", "987654")
    assert reg.route_protocol("https://example.com") is None


def test_database_y_cache(tmp_path: Path):
    db = Database(tmp_path / "modhub.db")
    tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"games", "mods", "deployed_files", "downloads", "game_source_ids"} <= tables
    db.cache_set("k", {"a": 1}, 60)
    assert db.cache_get("k") == {"a": 1}
