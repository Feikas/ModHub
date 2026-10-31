"""Pruebas de la Fase 4: filtros, FOMOD, protocolo, instancia única, vigilante de Descargas y la UI (sin pantalla)."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

import httpx
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from modhub.core.fomod import FomodSession, data_file_state, load_fomod  # noqa: E402
from modhub.games import get_profile  # noqa: E402
from modhub.sources import (AdultFilter, DownloadPolicy, HttpClient, ModInfo, RateLimit,  # noqa: E402
                            SearchFilterOptions, SourceRegistry, classify_categories)


def run(coro):
    return asyncio.run(coro)


# ════════════════════════════════════════════════════════════════════════
#  Filtros
# ════════════════════════════════════════════════════════════════════════
def mod(**kw) -> ModInfo:
    base = dict(source="x", mod_id="1", name="m", is_adult=False, install_support=DownloadPolicy.AUTOMATIC,
                updated_at=datetime.now(timezone.utc).isoformat(), categories=["ui"])
    base.update(kw)
    return ModInfo(**base)


def test_filtro_adulto_tres_modos():
    mods = [mod(mod_id="sfw"), mod(mod_id="nsfw", is_adult=True), mod(mod_id="?", is_adult=None)]
    ids = lambda f: [m.mod_id for m in f.apply(mods)]                       # noqa: E731
    assert ids(SearchFilterOptions(adult=AdultFilter.HIDE)) == ["sfw", "?"]
    assert ids(SearchFilterOptions(adult=AdultFilter.ONLY)) == ["nsfw"]
    assert ids(SearchFilterOptions(adult=AdultFilter.ALL)) == ["sfw", "nsfw", "?"]


def test_filtros_estado_fecha_y_categorias():
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    mods = [mod(mod_id="nuevo"), mod(mod_id="viejo", updated_at=old), mod(mod_id="web", install_support=DownloadPolicy.BROWSER_INTERCEPT),
            mod(mod_id="unix", updated_at=str(int(time.time()))), mod(mod_id="skin", categories=["cosmetic"])]
    f = SearchFilterOptions(adult=AdultFilter.ALL, auto_install_only=True, updated_within_days=180,
                            categories=frozenset({"ui"}))
    assert [m.mod_id for m in f.apply(mods)] == ["nuevo", "unix"]
    assert SearchFilterOptions.from_dict(f.to_dict()) == f


def test_categorias_de_cada_web_se_normalizan():
    assert classify_categories(["Bug Fixes", "Cosmetics", "Suits"]) == ["cosmetic", "fixes"]
    assert classify_categories(["optimization"]) == ["performance"]
    assert classify_categories(["User Interface"]) == ["ui"]


def _ts_handler(index):
    def h(req):
        return httpx.Response(200, json=index) if "/package/" in str(req.url) else httpx.Response(404)
    return h


def test_registro_aplica_filtros_y_omite_webs_incompatibles():
    from ui_demo import TS_INDEX
    from modhub.sources import GameRef

    async def go(filters):
        async with HttpClient(transport=httpx.MockTransport(_ts_handler(TS_INDEX)),
                              limits={"thunderstore.io": RateLimit(1000, burst=1000)}) as http:
            reg = SourceRegistry(http, only=["thunderstore", "modrinth", "moddb"])
            game = GameRef("LC", ids={"thunderstore": "lethal-company", "modrinth": "x", "moddb": "x"})
            return await reg.search_all("suits", game, filters=filters)
    r = run(go(SearchFilterOptions(adult=AdultFilter.HIDE)))
    assert [m.name for m in r.by_source["thunderstore"]] == ["More_Suits"]          # el NSFW ni se puntúa
    r = run(go(SearchFilterOptions(adult=AdultFilter.ONLY)))
    assert [m.name for m in r.by_source["thunderstore"]] == ["SpicySuits"]
    assert "adulto" in r.skipped["modrinth"]                                         # Modrinth lo prohíbe
    r = run(go(SearchFilterOptions(adult=AdultFilter.ALL, auto_install_only=True, categories=frozenset({"cosmetic"}))))
    assert {m.name for m in r.by_source["thunderstore"]} == {"More_Suits", "SpicySuits"}
    assert "automática" in r.skipped["moddb"]


def test_nexus_envia_el_filtro_adulto_a_su_api():
    sent = {}

    def h(req):
        import json
        sent.update(json.loads(req.content))
        return httpx.Response(200, json={"data": {"mods": {"nodes": [
            {"modId": 1, "name": "A", "game": {"domainName": "skyrimspecialedition"}, "adultContent": False,
             "modCategory": {"name": "User Interface"}}]}}})

    async def go():
        async with HttpClient(transport=httpx.MockTransport(h), limits={"api.nexusmods.com": RateLimit(1000, 1000)}) as http:
            reg = SourceRegistry(http, {"nexus": {"api_key": "k", "use_keyring": False}}, only=["nexus"])
            from modhub.sources import GameRef
            return await reg.search_all("a", GameRef("S", ids={"nexus": "skyrimspecialedition"}),
                                        filters=SearchFilterOptions(adult=AdultFilter.HIDE))
    r = run(go())
    assert sent["variables"]["filter"]["adultContent"] == [{"value": False, "op": "EQUALS"}]
    m = r.by_source["nexus"][0]
    assert m.categories == ["ui"] and m.install_support is DownloadPolicy.BROWSER_INTERCEPT


# ════════════════════════════════════════════════════════════════════════
#  FOMOD
# ════════════════════════════════════════════════════════════════════════
FOMOD_XML = """<?xml version="1.0" encoding="utf-8"?>
<config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="http://qconsulting.ca/fo3/ModConfig5.0.xsd">
  <moduleName>Cielos Mejorados</moduleName>
  <requiredInstallFiles><file source="core\\Cielos.esp" destination="Cielos.esp"/></requiredInstallFiles>
  <installSteps order="Explicit">
    <installStep name="Resolución">
      <optionalFileGroups order="Explicit">
        <group name="Texturas" type="SelectExactlyOne">
          <plugins order="Explicit">
            <plugin name="2K"><description>Para la mayoría</description><image path="fomod\\img.png"/>
              <files><folder source="2k" destination="textures"/></files>
              <conditionFlags><flag name="res">2k</flag></conditionFlags>
              <typeDescriptor><type name="Optional"/></typeDescriptor></plugin>
            <plugin name="4K"><description>Para PCs potentes</description>
              <files><folder source="4k" destination="textures"/></files>
              <conditionFlags><flag name="res">4k</flag></conditionFlags>
              <typeDescriptor><type name="Recommended"/></typeDescriptor></plugin>
          </plugins>
        </group>
        <group name="Extras" type="SelectAny">
          <plugins order="Explicit">
            <plugin name="Parche USSEP">
              <files><file source="patches/ussep.esp" destination="Cielos - USSEP.esp" priority="1"/></files>
              <typeDescriptor><dependencyType><defaultType name="NotUsable"/>
                <patterns><pattern><dependencies operator="And"><fileDependency file="unofficial skyrim special edition patch.esp" state="Active"/></dependencies>
                <type name="Recommended"/></pattern></patterns></dependencyType></typeDescriptor></plugin>
          </plugins>
        </group>
      </optionalFileGroups>
    </installStep>
    <installStep name="Solo 4K">
      <visible><flagDependency flag="res" value="4k"/></visible>
      <optionalFileGroups><group name="Nubes" type="SelectAtMostOne"><plugins>
        <plugin name="Nubes 4K"><description>Nubes extra</description>
          <files><file source="4k_extra/clouds.dds" destination="textures/sky/clouds.dds" priority="5"/></files>
          <typeDescriptor><type name="Optional"/></typeDescriptor></plugin>
      </plugins></group></optionalFileGroups>
    </installStep>
  </installSteps>
  <conditionalFileInstalls><patterns><pattern>
    <dependencies operator="And"><flagDependency flag="res" value="4k"/></dependencies>
    <files><file source="readme4k.txt" destination="Docs/4k.txt"/></files>
  </pattern></patterns></conditionalFileInstalls>
</config>
"""


def make_fomod_mod(root: Path) -> Path:
    files = {"Cielos v2/fomod/ModuleConfig.xml": FOMOD_XML, "Cielos v2/fomod/img.png": "",
             "Cielos v2/core/Cielos.esp": "esp", "Cielos v2/2K/sky.dds": "2k", "Cielos v2/4k/sky.dds": "4k",
             "Cielos v2/4k_extra/clouds.dds": "c", "Cielos v2/patches/ussep.esp": "p", "Cielos v2/readme4k.txt": "r"}
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data)
    return root


def targets(plan):
    return sorted(plan.targets())


def test_fomod_por_defecto_recomendado_y_pasos_condicionales(tmp_path):
    cfg = load_fomod(make_fomod_mod(tmp_path / "mod"))
    assert cfg.module_name == "Cielos Mejorados" and cfg.root.name == "Cielos v2"
    s = FomodSession(cfg)
    sel = s.default_selections()
    assert sel[(0, 0)] == {1}                                   # 4K es «Recommended»
    assert s.visible_steps(sel) == [0, 1]                       # el paso «Solo 4K» aparece
    plan = s.build_plan(sel, get_profile("skyrimse"))
    assert targets(plan) == ["Data/Cielos.esp", "Data/Docs/4k.txt", "Data/textures/sky.dds"]
    sel[(1, 0)] = {0}
    assert "Data/textures/sky/clouds.dds" in targets(s.build_plan(sel, get_profile("skyrimse")))


def test_fomod_elegir_2k_oculta_el_paso_4k(tmp_path):
    s = FomodSession(load_fomod(make_fomod_mod(tmp_path / "mod")))
    sel = {(0, 0): {0}, (0, 1): set(), (1, 0): {0}}             # el usuario marcó algo en el paso 4K y luego cambió
    assert s.visible_steps(sel) == [0]
    plan = s.build_plan(sel, get_profile("skyrimse"))
    assert targets(plan) == ["Data/Cielos.esp", "Data/textures/sky.dds"]
    assert plan.mappings[1].source.read_text() == "2k"          # "2k" en el XML, carpeta "2K" en disco


def test_fomod_dependencia_de_archivo_y_validacion(tmp_path):
    s = FomodSession(load_fomod(make_fomod_mod(tmp_path / "mod")))
    assert s.plugin_type(0, 1, 0, {}) == "NotUsable"
    assert "no se puede usar" in " ".join(s.validate_step(0, {(0, 0): {1}, (0, 1): {0}}))
    assert "exactamente una" in " ".join(s.validate_step(0, {(0, 0): set()}))
    data = tmp_path / "Data"
    data.mkdir()
    (data / "Unofficial Skyrim Special Edition Patch.esp").write_text("x")
    s2 = FomodSession(load_fomod(tmp_path / "mod"), data_file_state(data))
    if sys.platform != "win32":                       # en Linux el nombre distingue mayúsculas: lo igualamos
        (data / "unofficial skyrim special edition patch.esp").write_text("x")
    assert s2.plugin_type(0, 1, 0, {}) == "Recommended" and s2.default_selections()[(0, 1)] == {0}


def test_fomod_xml_utf16_y_mal_declarado(tmp_path):
    root = make_fomod_mod(tmp_path / "a")
    (root / "Cielos v2/fomod/ModuleConfig.xml").write_bytes(FOMOD_XML.replace("utf-8", "utf-16").encode("utf-16"))
    assert load_fomod(root).module_name == "Cielos Mejorados"
    root = make_fomod_mod(tmp_path / "b")
    (root / "Cielos v2/fomod/ModuleConfig.xml").write_bytes(FOMOD_XML.replace("utf-8", "utf-16").encode("utf-8"))
    assert load_fomod(root).module_name == "Cielos Mejorados"


# ════════════════════════════════════════════════════════════════════════
#  Protocolo URL (registro simulado)
# ════════════════════════════════════════════════════════════════════════
class FakeReg:
    def __init__(self):
        self.data: dict[tuple[str, str], str] = {}

    def get(self, path, name=""):
        return self.data.get((path.lower(), name))

    def set(self, path, name, value):
        self.data[(path.lower(), name)] = value

    def delete_tree(self, path):
        self.data = {k: v for k, v in self.data.items() if not k[0].startswith(path.lower())}


def test_protocolo_registra_y_devuelve_el_de_vortex():
    from modhub.ui.protocol import ProtocolRegistrar
    reg = FakeReg()
    vortex = '"C:\\Vortex\\Vortex.exe" -d "%1"'
    reg.set(r"Software\Classes\nxm\shell\open\command", "", vortex)
    pr = ProtocolRegistrar(reg, command='"C:\\ModHub\\ModHub.exe" "%1"')
    pr.register("nxm")
    assert pr.is_ours("nxm") and reg.get(r"Software\Classes\nxm", "URL Protocol") == ""
    assert "Vortex" in pr.unregister("nxm") or pr.current_handler("nxm") == vortex
    assert pr.current_handler("nxm") == vortex
    pr.register("modhub")
    pr.unregister("modhub")
    assert pr.current_handler("modhub") is None


def test_comando_de_lanzamiento_apunta_al_lanzador():
    from modhub.ui.protocol import launch_command
    cmd = launch_command()
    assert cmd.endswith('"%1"') and "run_modhub.pyw" in cmd


# ════════════════════════════════════════════════════════════════════════
#  Qt: instancia única, vigilante de Descargas e interfaz
# ════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    from modhub.ui.theme import STYLESHEET
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLESHEET)
    return app


def pump(app, seconds: float, until=None):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if until and until():
            return True
        time.sleep(0.01)
    return bool(until and until())


def test_instancia_unica_pasa_el_enlace(qapp, tmp_path):
    from modhub.ui.single_instance import SingleInstanceGuard
    first = SingleInstanceGuard("ModHubTest", tmp_path)
    second = SingleInstanceGuard("ModHubTest", tmp_path)
    got = []
    first.message_received.connect(got.append)
    assert first.acquire() is True
    assert second.acquire() is False
    # La segunda copia de ModHub es OTRO proceso, como cuando Windows abre un enlace nxm://
    import subprocess
    code = ("import sys; from PySide6.QtCore import QCoreApplication; app = QCoreApplication([]); "
            "from modhub.ui.single_instance import SingleInstanceGuard as G; "
            f"g = G('ModHubTest', r'{tmp_path}'); "
            "sys.exit(0 if (not g.acquire() and g.send(sys.argv[1])) else 3)")
    proc = subprocess.Popen([sys.executable, "-c", code, "nxm://skyrimspecialedition/mods/1/files/2?key=a&expires=1"],
                            cwd=str(Path(__file__).resolve().parents[1]))
    pump(qapp, 10, lambda: bool(got))
    assert proc.wait(10) == 0
    assert got == ["nxm://skyrimspecialedition/mods/1/files/2?key=a&expires=1"]
    first.release()
    third = SingleInstanceGuard("ModHubTest", tmp_path)
    assert third.acquire() is True
    third.release()
    pump(qapp, 0.2)


def test_vigilante_recoge_la_descarga_esperada(qapp, tmp_path):
    from modhub.ui.intercept import DownloadsWatcher, PendingIntercept
    (tmp_path / "viejo.zip").write_text("ya estaba")
    w = DownloadsWatcher(tmp_path, poll_ms=50)
    got = []
    w.file_ready.connect(lambda p, pend: got.append((p.name, pend and pend.mod_name)))
    assert w.start()
    w.expect(PendingIntercept(1, "Cielos Mejorados", "curseforge", "1", "2", None))
    (tmp_path / "Cielos_Mejorados-v2.zip.crdownload").write_text("a medias")
    pump(qapp, 0.4)
    assert got == []                                            # descargando: aún no
    (tmp_path / "Cielos_Mejorados-v2.zip.crdownload").rename(tmp_path / "Cielos_Mejorados-v2.zip")
    assert pump(qapp, 3, lambda: bool(got))
    assert got == [("Cielos_Mejorados-v2.zip", "Cielos Mejorados")] and w.pending == []
    w.stop()


def _ui(tmp_path):
    import qasync
    from ui_demo import build_demo
    from modhub.ui.main_window import MainWindow
    loop = qasync.QEventLoop(already_running=False)
    asyncio.set_event_loop(loop)
    ctx, ids = build_demo(tmp_path)
    win = MainWindow(ctx, start_watcher=False)
    return loop, ctx, ids, win


async def _until(cond, timeout=8.0):
    for _ in range(int(timeout / 0.05)):
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


def test_ui_buscar_instalar_desactivar_y_desinstalar(qapp, tmp_path):
    loop, ctx, ids, win = _ui(tmp_path)
    gid = ids["Lethal Company"]
    plugin = Path(ctx.game(gid)["install_path"]) / "BepInEx/plugins/notnotnotswipez-MoreCompany/MoreCompany.dll"

    async def scenario():
        sv = win.pages["search"]
        sv.game.select_game(gid)
        sv.query.setText("company")
        sv.search()
        assert await _until(lambda: sv.table.rowCount() == 3)
        names = [sv.table.item(r, 0).text() for r in range(3)]
        assert names[0] == "MoreCompany"                           # orden por relevancia
        sv.table.selectRow(0)
        assert await _until(lambda: sv.install_btn.isEnabled())
        sv.install_selected()
        assert await _until(lambda: plugin.exists())
        iv = win.pages["installed"]
        iv.game.select_game(gid)
        await asyncio.sleep(0.2)
        box = iv.table.cellWidget(0, 0).findChild(type(iv.table.cellWidget(0, 0).children()[1]))
        box.setChecked(False)                                     # desactivar en vivo
        assert await _until(lambda: not plugin.exists())
        iv.table.selectRow(0)
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
        iv.uninstall()
        assert await _until(lambda: ctx.mods(gid) == [])

    with loop:
        loop.run_until_complete(scenario())
    assert not (Path(ctx.game(gid)["install_path"]) / "BepInEx/plugins").exists() or \
        not any((Path(ctx.game(gid)["install_path"]) / "BepInEx/plugins").iterdir())


def test_ui_enlace_nxm_sin_juego_avisa(qapp, tmp_path):
    loop, ctx, ids, win = _ui(tmp_path)
    win.handle_url("nxm://cyberpunk2077/mods/107/files/99?key=SECRETO&expires=1")
    banners = [w.text() for w in win.notifications.findChildren(type(win.side_status))]
    assert any("no está en ModHub" in t for t in banners)
    log = win.pages["downloads"].log.item(0).text()
    assert "SECRETO" not in log and "key=…" in log                 # la clave no se muestra
    win.close()
    loop.close()


def test_ui_fomod_dialogo_produce_plan(qapp, tmp_path):
    loop, ctx, ids, win = _ui(tmp_path)
    from modhub.ui.dialogs import FomodDialog
    gid = ids["Skyrim Special Edition"]
    staging = make_fomod_mod(tmp_path / "staging_mod")
    ctx.db.execute("INSERT INTO mods (game_id, source, source_mod_id, name, state, staging_dir) VALUES (?,?,?,?,?,?)",
                   (gid, "local", "cielos", "Cielos", "needs_review", str(staging)))
    mid = ctx.db.one("SELECT id FROM mods WHERE source_mod_id='cielos'")["id"]
    kind, session = ctx.review_session(mid)
    assert kind == "fomod"
    dlg = FomodDialog(session, get_profile("skyrimse"), ctx.game(gid)["install_path"], win)
    radios = dlg.scroll.widget().findChildren(type(dlg.back_btn).__mro__[1])     # QAbstractButton
    two_k = next(b for b in radios if b.text().startswith("2K"))
    two_k.setChecked(True)
    assert dlg.next_btn.text() == "Instalar"                        # al elegir 2K desaparece el paso 4K
    from modhub.ui import dialogs
    dialogs.PlanReviewDialog.exec = lambda self: dialogs.QDialog.DialogCode.Accepted
    dlg.next()
    assert dlg.result() == dialogs.QDialog.DialogCode.Accepted
    assert sorted(dlg.plan.targets()) == ["Data/Cielos.esp", "Data/textures/sky.dds"]
    report = ctx.installer.confirm_review(mid, dlg.plan)
    assert report.ok and (Path(ctx.game(gid)["install_path"]) / "Data/textures/sky.dds").read_text() == "2k"
    win.close()
    loop.close()


# ════════════════════════════════════════════════════════════════════════
#  Explorar sin texto, ordenar y enlazar juegos por nombre
# ════════════════════════════════════════════════════════════════════════
from modhub.sources import SortOrder, GameRef  # noqa: E402
from modhub.sources.base import best_name_match  # noqa: E402


def test_nombres_de_juego_se_emparejan_sin_falsos_positivos():
    cands = [("Fallout 4", "f4"), ("Fallout 4 VR", "f4vr"), ("Dragon's Dogma 2", "dd2"), ("R.E.P.O.", "repo")]
    assert best_name_match("Fallout 4", cands) == "f4"
    assert best_name_match("Dragons Dogma 2", cands) == "dd2"
    assert best_name_match("REPO", cands) == "repo"
    assert best_name_match("Fallout", cands) is None            # demasiado distinto: mejor no enlazar


def _ts_registry_search(query, order):
    from ui_demo import TS_INDEX

    async def go():
        async with HttpClient(transport=httpx.MockTransport(_ts_handler(TS_INDEX)),
                              limits={"thunderstore.io": RateLimit(1000, burst=1000)}) as http:
            reg = SourceRegistry(http, only=["thunderstore"])
            return await reg.search_all(query, GameRef("LC", ids={"thunderstore": "lethal-company"}),
                                        filters=SearchFilterOptions(sort=order))
    return run(go())


def test_explorar_juego_sin_texto_devuelve_todo():
    r = _ts_registry_search("", SortOrder.RELEVANCE)
    assert r.sort is SortOrder.TRENDING                         # sin texto, «Recomendado» = populares
    assert len(r.by_source["thunderstore"]) == 5                # todos menos el NSFW (oculto por defecto)


def test_ordenar_por_descargas_y_mezclar_webs():
    r = _ts_registry_search("", SortOrder.DOWNLOADS)
    downloads = [m.downloads for m in r.merged()]
    assert downloads == sorted(downloads, reverse=True) and r.merged()[0].name == "MoreCompany"


def test_link_game_encuentra_la_comunidad_por_nombre():
    communities = {"results": [{"identifier": "lethal-company", "name": "Lethal Company"},
                               {"identifier": "valheim", "name": "Valheim"}], "pagination": {"next_link": None}}

    def h(req):
        return httpx.Response(200, json=communities) if "community" in str(req.url) else httpx.Response(404)

    async def go():
        async with HttpClient(transport=httpx.MockTransport(h), limits={"thunderstore.io": RateLimit(1000, 1000)}) as http:
            reg = SourceRegistry(http, only=["thunderstore", "modrinth", "nexus"])
            return await reg.link_game("Lethal Company"), await reg.link_game("Minecraft Java Edition")
    lc, mc = run(go())
    assert lc == {"thunderstore": "lethal-company"}            # Nexus sin clave: ni se consulta
    assert mc == {"modrinth": "minecraft"}


def test_ui_elegir_juego_muestra_sus_mods_sin_escribir(qapp, tmp_path):
    loop, ctx, ids, win = _ui(tmp_path)
    sv = win.pages["search"]

    async def scenario():
        sv.game.select_game(ids["Stardew Valley"])
        sv.game.select_game(ids["Lethal Company"])               # sin texto: explora solo
        assert await _until(lambda: sv.table.rowCount() == 5)
        sv.sort.setCurrentIndex(sv.sort.findData("downloads"))
        assert await _until(lambda: "resultados" in sv.status.text() and "Más descargados" in sv.status.text())
        names = [sv.table.item(r, 0).text() for r in range(sv.table.rowCount())]
        assert names[:2] == ["MoreCompany", "LethalLib"]

    with loop:
        loop.run_until_complete(scenario())
    win.close()
    loop.close()


# ════════════════════════════════════════════════════════════════════════
#  Botón «Jugar»
# ════════════════════════════════════════════════════════════════════════
def _game_row(root, profile="generic", store="custom", app_id=None, exe=None):
    return {"install_path": str(root), "profile": profile, "store": store, "store_app_id": app_id, "exe_path": exe}


def test_jugar_prefiere_script_extender_luego_tienda_luego_exe(tmp_path):
    from modhub.core.launcher import plan_launch
    (tmp_path / "SkyrimSE.exe").write_text("x")
    assert plan_launch(_game_row(tmp_path, "skyrimse", "steam", "489830")).target == "steam://rungameid/489830"
    (tmp_path / "skse64_loader.exe").write_text("x")
    p = plan_launch(_game_row(tmp_path, "skyrimse", "steam", "489830"))
    assert p.kind == "loader" and p.target.endswith("skse64_loader.exe")
    epic = plan_launch(_game_row(tmp_path / "x", "generic", "epic", "Ginger"))
    assert epic.target.startswith("com.epicgames.launcher://apps/Ginger")
    gog = tmp_path / "gog"
    gog.mkdir()
    (gog / "Stardew Valley.exe").write_text("x")
    assert plan_launch(_game_row(gog, "stardewvalley", "gog")).target.endswith("Stardew Valley.exe")
    assert plan_launch(_game_row(tmp_path / "vacio")).kind == "missing"
