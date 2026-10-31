"""Pruebas de la v0.4: lista de juegos que se mantiene al día, portables, catálogo de juegos y ajustes."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from modhub.core.game_detect import GameDetector, clean_folder_name, find_game_root  # noqa: E402
from modhub.settings import SettingsStore  # noqa: E402
from modhub.sources import GameRef, HttpClient, RateLimit, SourceRegistry  # noqa: E402
from modhub.sources.base import best_name_match  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def write(p: Path, data: str = "x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data)
    return p


class NoRegistry:
    def values(self, *a):
        return None

    def subkeys(self, *a):
        return []


def acf(appid, name, installdir, flags=4):
    return (f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"name"\t\t"{name}"\n\t"StateFlags"\t\t"{flags}"\n'
            f'\t"installdir"\t\t"{installdir}"\n}}\n')


# ════════════════════════════════════════════════════════════════════════
#  Nombres y carpetas de portables
# ════════════════════════════════════════════════════════════════════════
def test_nombres_de_carpetas_de_portables_se_limpian():
    assert clean_folder_name("Slay.the.Spire.2.v0.107.1-0xdeadcode") == "Slay the Spire 2"
    assert clean_folder_name("Solasta.II.Supporter.Edition-InsaneRamZes") == "Solasta II Supporter Edition"


def test_nombres_parecidos_se_emparejan_sin_confundir_secuelas():
    c = [("Slay the Spire 2", "sts2"), ("Slay the Spire", "sts1"), ("Solasta II", "sol2"), ("Fallout 4", "f4")]
    assert best_name_match("Slay spire 2", c) == "sts2"          # como lo escribiste
    assert best_name_match("Slay the Spire II", c) == "sts2"     # romanos = arábigos
    assert best_name_match("Solasta II Supporter Edition", c) == "sol2"
    assert best_name_match("Fallout", c) is None


def test_subcarpeta_mods_sube_a_la_del_exe(tmp_path):
    game = tmp_path / "Slay.the.Spire.2.v0.107.1-0xdeadcode"
    write(game / "SlayTheSpire2.exe")
    write(game / "UnityCrashHandler64.exe")
    (game / "Mods").mkdir()
    assert find_game_root(game / "Mods") == game
    g = GameDetector(registry=NoRegistry(), env={}, drives=[], steam_paths=[]).from_folder(game / "Mods")
    assert g.install_path == game and g.exe_path.name == "SlayTheSpire2.exe" and g.name == "Slay the Spire 2"


def test_carpeta_con_varios_portables(tmp_path):
    lib = tmp_path / "hydras"
    write(lib / "Solasta.II.Supporter.Edition-InsaneRamZes" / "Solasta2.exe")
    write(lib / "Otro.Juego.v1.2-GRUPO" / "bin" / "Juego.exe")
    write(lib / "SoloTexto" / "leeme.txt")
    det = GameDetector(registry=NoRegistry(), env={}, drives=[], steam_paths=[])
    names = sorted(g.name for g in det.scan_library(lib))
    assert names == ["Otro Juego", "Solasta II Supporter Edition"]


# ════════════════════════════════════════════════════════════════════════
#  Detección que mantiene la lista al día (tu caso: Fortnite desinstalado)
# ════════════════════════════════════════════════════════════════════════
def make_ctx(tmp_path, steam, epic_env):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from modhub.ui.context import AppContext
    det = GameDetector(registry=NoRegistry(), env=epic_env, drives=[], steam_paths=[steam])
    return AppContext(tmp_path / "data", use_keyring=False, detector=det,
                      http_transport=httpx.MockTransport(lambda r: httpx.Response(404)))


def test_detectar_quita_lo_desinstalado_y_respeta_lo_que_quitas(tmp_path):
    steam = tmp_path / "Steam"
    write(steam / "steamapps" / "appmanifest_2357570.acf", acf("2357570", "Overwatch", "Overwatch"))
    write(steam / "steamapps" / "common" / "Overwatch" / "Overwatch.exe")
    write(steam / "steamapps" / "appmanifest_1.acf", acf("1", "Bajando", "Bajando", flags=1026))
    write(steam / "steamapps" / "common" / "Bajando" / "a.bin")                   # aún descargando: no cuenta
    (steam / "steamapps" / "common" / "Cyberpunk 2077").mkdir(parents=True)          # carpeta vacía: desinstalado
    write(steam / "steamapps" / "appmanifest_1091500.acf", acf("1091500", "Cyberpunk 2077", "Cyberpunk 2077"))
    pd = tmp_path / "ProgramData"
    manifests = pd / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    fortnite = tmp_path / "Fortnite"
    write(fortnite / "FortniteGame" / "Binaries" / "Win64" / "FortniteLauncher.exe")
    write(manifests / "a.item", json.dumps({"DisplayName": "Fortnite", "AppName": "Fortnite",
                                            "MainGameAppName": "Fortnite", "InstallLocation": str(fortnite)}))
    write(manifests / "b.item", json.dumps({"DisplayName": "Contenido de LEGO® Fortnite", "AppName": "94bc",
                                            "MainGameAppName": "Fortnite", "InstallLocation": str(fortnite)}))
    ctx = make_ctx(tmp_path, steam, {"ProgramData": str(pd)})

    res = run(ctx.detect_games())
    assert sorted(g["name"] for g in ctx.games()) == ["Fortnite", "Overwatch"] and res["found"] == 2

    # Desinstalas Fortnite (Epic borra su manifiesto y su carpeta): al detectar, desaparece
    for f in manifests.iterdir():
        f.unlink()
    import shutil
    shutil.rmtree(fortnite)
    res = run(ctx.detect_games())
    assert [g["name"] for g in ctx.games()] == ["Overwatch"] and res["removed"] == 1

    # Quitas Overwatch a mano: no vuelve a aparecer al detectar
    ow = ctx.games()[0]["id"]
    assert ctx.remove_game(ow).ok
    assert run(ctx.detect_games())["ignored"] == 1 and ctx.games() == []


def test_quitar_un_juego_cuya_carpeta_ya_no_existe(tmp_path):
    steam = tmp_path / "Steam"
    (steam / "steamapps").mkdir(parents=True)
    ctx = make_ctx(tmp_path, steam, {})
    game = write(tmp_path / "E" / "Fortnite" / "Fortnite.exe").parent
    gid = ctx.add_game_folder(str(game), "Fortnite")
    import shutil
    shutil.rmtree(game)                                    # antes: «No se encuentra la carpeta del juego»
    report = ctx.remove_game(gid)
    assert report.ok and ctx.games() == []


def test_portable_guardado_en_mods_se_corrige_al_detectar(tmp_path):
    steam = tmp_path / "Steam"
    (steam / "steamapps").mkdir(parents=True)
    ctx = make_ctx(tmp_path, steam, {})
    game = tmp_path / "D" / "Slay.the.Spire.2.v0.107.1-0xdeadcode"
    write(game / "SlayTheSpire2.exe")
    (game / "Mods").mkdir()
    # Así quedó en tu base de datos (versión anterior): ruta de «Mods», sin .exe
    ctx.db.execute("INSERT INTO games (slug,name,install_path,store,store_app_id,staging_path) VALUES (?,?,?,?,?,?)",
                   ("custom-slayspire2", "Slay spire 2", str(game / "Mods"), "custom", "x", str(tmp_path / "st")))
    run(ctx.detect_games())
    g = ctx.games()[0]
    assert Path(g["install_path"]) == game and g["exe_path"].endswith("SlayTheSpire2.exe")


# ════════════════════════════════════════════════════════════════════════
#  Ajustes: Steam fuera, Mod DB desactivado, claves fuera del registro
# ════════════════════════════════════════════════════════════════════════
def test_migracion_quita_steam_y_su_clave(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({
        "enabled_sources": ["nexus", "steam", "moddb", "thunderstore"],
        "secrets_fallback": {"steam.web_api_key": "CBCF0000", "nexus.api_key": "N"}}))
    store = SettingsStore(tmp_path, use_keyring=False)
    assert store.settings.enabled_sources == ["nexus", "thunderstore"]
    assert store.get_secret("steam", "web_api_key") == "" and store.get_secret("nexus", "api_key") == "N"
    saved = json.loads((tmp_path / "settings.json").read_text())
    assert "CBCF0000" not in json.dumps(saved) and saved["config_version"] == 2
    store2 = SettingsStore(tmp_path, use_keyring=False)          # la migración solo se hace una vez
    store2.settings.enabled_sources.append("moddb")
    store2.save()
    assert "moddb" in SettingsStore(tmp_path, use_keyring=False).settings.enabled_sources


def test_errores_no_muestran_claves():
    from modhub.sources.http import HttpError

    async def go():
        async with HttpClient(transport=httpx.MockTransport(lambda r: httpx.Response(404, text="no")),
                              limits={"x.test": RateLimit(1000, 1000)}) as http:
            await http.get_json("https://x.test/a?key=SECRETO&api_key=OTRO")
    with pytest.raises(HttpError) as e:
        run(go())
    assert "SECRETO" not in str(e.value) and "OTRO" not in str(e.value)


def test_cloudflare_da_un_mensaje_claro():
    from modhub.sources.http import HttpError

    def cf(_r):
        return httpx.Response(403, headers={"server": "cloudflare"}, text="<title>Just a moment...</title>")

    async def go():
        async with HttpClient(transport=httpx.MockTransport(cf), limits={"www.moddb.com": RateLimit(1000, 1000)}) as http:
            await http.get_text("https://www.moddb.com/mods")
    with pytest.raises(HttpError) as e:
        run(go())
    assert "bloquea las búsquedas automáticas" in str(e.value)


# ════════════════════════════════════════════════════════════════════════
#  Catálogo de juegos (autocompletado)
# ════════════════════════════════════════════════════════════════════════
def catalog_handler(req: httpx.Request):
    url = str(req.url)
    if "graphql" in url:
        body = json.loads(req.content)
        if "games(" in body["query"]:
            return httpx.Response(200, json={"data": {"games": {"nodes": [
                {"name": "Slay the Spire", "domainName": "slaythespire"},
                {"name": "Slay The Spire II", "domainName": "slaythespire2"}]}}})
        return httpx.Response(200, json={"data": {"mods": {"nodes": [
            {"modId": 7, "name": "Mod de prueba", "downloads": 10, "adultContent": False,
             "game": {"domainName": body["variables"]["filter"]["gameDomainName"][0]["value"]}}]}}})
    if "community" in url:
        return httpx.Response(200, json={"results": [{"identifier": "slay-the-spire-2", "name": "Slay the Spire 2"}],
                                         "pagination": {"next_link": None}})
    return httpx.Response(404)


def test_catalogo_une_el_mismo_juego_de_varias_webs():
    async def go():
        async with HttpClient(transport=httpx.MockTransport(catalog_handler),
                              limits={h: RateLimit(1000, 1000) for h in ("api.nexusmods.com", "thunderstore.io")}) as http:
            reg = SourceRegistry(http, only=["nexus", "thunderstore"])
            return await reg.search_games("slay")
    refs = run(go())
    sts2 = next(r for r in refs if "2" in r.name or "II" in r.name)
    assert sts2.ids == {"nexus": "slaythespire2", "thunderstore": "slay-the-spire-2"}


def test_ui_buscar_mods_de_un_juego_no_instalado(tmp_path):
    from PySide6.QtWidgets import QApplication
    import qasync
    app = QApplication.instance() or QApplication([])
    loop = qasync.QEventLoop(app, already_running=False)
    asyncio.set_event_loop(loop)
    from modhub.ui.context import AppContext
    from modhub.ui.main_window import MainWindow
    ctx = AppContext(tmp_path / "data", use_keyring=False,
                     detector=GameDetector(registry=NoRegistry(), env={}, drives=[], steam_paths=[]),
                     http_transport=httpx.MockTransport(catalog_handler))
    ctx.settings.enabled_sources = ["nexus", "thunderstore"]
    ctx.reload_sources()
    win = MainWindow(ctx, start_watcher=False)
    sv = win.pages["search"]

    async def scenario():
        refs = await ctx.search_game_catalog("slay")
        sts2 = next(r for r in refs if r.ids.get("nexus") == "slaythespire2")
        sv.game.choose_catalog_game(sts2)
        for _ in range(100):
            await asyncio.sleep(0.05)
            if "resultados" in sv.status.text():
                break
        assert sv.game.is_virtual and sv.game.currentText().endswith("Slay The Spire II")
        assert [m.name for m in sv.view] == ["Mod de prueba"]
        sv.table.selectRow(0)
        await asyncio.sleep(0.3)
        assert not sv.install_btn.isEnabled() and "añádelo" in sv.install_btn.toolTip()

    with loop:
        loop.run_until_complete(scenario())
    win.close()
    loop.close()
