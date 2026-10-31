"""Pruebas de la Fase 3 (sin red ni Windows: registro, discos y servidores simulados)."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

import httpx
import pytest

from modhub.core import (DeployError, Deployer, DownloadManager, DownloadState, Extractor, FileMapping,
                         GameDetector, InstallPlan, ModAnalyzer, ModInstaller, StagingManager, UnsafeArchive,
                         save_detected)
from modhub.core.game_detect import parse_vdf, vdf_get
from modhub.db.database import Database
from modhub.games import get_profile
from modhub.sources import HttpClient, RateLimit, SourceRegistry


def run(coro):
    return asyncio.run(coro)


def write(path: Path, data: str | bytes = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.write_bytes if isinstance(data, bytes) else path.write_text)(data)
    return path


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# ════════════════════════════════════════════════════════════════════════
#  Detección de juegos
# ════════════════════════════════════════════════════════════════════════
class FakeRegistry:
    def __init__(self, data: dict[tuple[str, str], dict]):
        self.data = data

    def values(self, hive, path):
        return self.data.get((hive, path))

    def subkeys(self, hive, path):
        prefix = path + "\\"
        return sorted({p[len(prefix):].split("\\")[0] for h, p in self.data if h == hive and p.startswith(prefix)})


def acf(appid, name, installdir):
    return f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"name"\t\t"{name}"\n\t"installdir"\t\t"{installdir}"\n}}\n'


def test_steam_varias_librerias_y_perfil(tmp_path):
    steam, lib2 = tmp_path / "Steam", tmp_path / "D" / "SteamLibrary"
    write(steam / "steamapps" / "libraryfolders.vdf", f'''"libraryfolders"
{{
    "0" {{ "path" "{str(steam).replace(chr(92), chr(92) * 2)}" "apps" {{ "228980" "1" }} }}
    "1" {{ "path" "{str(lib2).replace(chr(92), chr(92) * 2)}" "apps" {{ "1966720" "1" }} }}
}}''')
    write(steam / "steamapps" / "appmanifest_228980.acf", acf("228980", "Steamworks Common Redistributables", "Steamworks Shared"))
    (steam / "steamapps" / "common" / "Steamworks Shared").mkdir(parents=True)
    write(lib2 / "steamapps" / "appmanifest_1966720.acf", acf("1966720", "Lethal Company", "Lethal Company"))
    write(lib2 / "steamapps" / "common" / "Lethal Company" / "Lethal Company.exe")
    write(lib2 / "steamapps" / "appmanifest_999.acf", acf("999", "Desinstalado", "NoExiste"))

    games = GameDetector(registry=FakeRegistry({}), env={}, drives=[], steam_paths=[steam]).detect_steam()
    assert [(g.store_id, g.profile_slug) for g in games] == [("1966720", "lethalcompany")]
    assert games[0].exe_path.name == "Lethal Company.exe"


def test_parse_vdf_formato_antiguo_y_escapes():
    d = parse_vdf('"LibraryFolders"\n{\n "TimeNextStatsReport" "1"\n "1" "D:\\\\Games\\\\Steam"\n}')
    assert vdf_get(vdf_get(d, "libraryfolders"), "1") == "D:\\Games\\Steam"


def test_epic_manifiestos(tmp_path):
    game = tmp_path / "Epic" / "Palworld"
    write(game / "Palworld.exe")
    manifests = tmp_path / "ProgramData" / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    write(manifests / "A.item", json.dumps({"DisplayName": "Palworld", "InstallLocation": str(game),
                                            "AppName": "abc123", "LaunchExecutable": "Palworld.exe"}))
    write(manifests / "B.item", json.dumps({"DisplayName": "Roto", "InstallLocation": str(tmp_path / "x"),
                                            "bIsIncompleteInstall": True}))
    write(manifests / "C.item", "{no es json")
    games = GameDetector(registry=FakeRegistry({}), env={"ProgramData": str(tmp_path / "ProgramData")},
                         drives=[], steam_paths=[]).detect_epic()
    assert len(games) == 1 and games[0].profile_slug == "palworld" and games[0].exe_path.name == "Palworld.exe"


def test_gog_registro(tmp_path):
    game = write(tmp_path / "GOG" / "Stardew Valley" / "Stardew Valley.exe").parent
    reg = FakeRegistry({("HKLM", r"SOFTWARE\WOW6432Node\GOG.com\Games\1453375253"):
                        {"gameName": "Stardew Valley", "path": str(game), "exe": str(game / "Stardew Valley.exe"),
                         "gameID": "1453375253"}})
    games = GameDetector(registry=reg, env={}, drives=[], steam_paths=[]).detect_gog()
    assert games[0].profile_slug == "stardewvalley" and games[0].store_id == "1453375253"


def test_xbox_gamingroot_y_config(tmp_path):
    drive = tmp_path / "E"
    write(drive / ".GamingRoot", b"RGBX\x01\x00\x00\x00" + "\\Juegos Xbox".encode("utf-16-le") + b"\x00\x00")
    content = drive / "Juegos Xbox" / "Palworld" / "Content"
    write(content / "MicrosoftGame.config",
          '<Game><Identity Name="PocketpairInc.Palworld"/><ShellVisuals DefaultDisplayName="Palworld"/></Game>')
    write(drive / "XboxGames" / "SinContent" / "otra.txt")
    games = GameDetector(registry=FakeRegistry({}), env={}, drives=[drive], steam_paths=[]).detect_xbox()
    assert [(g.store_id, g.name, g.profile_slug) for g in games] == [("PocketpairInc.Palworld", "Palworld", "palworld")]
    assert games[0].install_path == content


def test_guardar_juegos_en_bd(tmp_path):
    game = write(tmp_path / "Lethal Company" / "Lethal Company.exe").parent
    det = GameDetector(registry=FakeRegistry({}), env={}, drives=[], steam_paths=[])
    g = det.from_folder(game)
    db = Database(tmp_path / "m.db")
    ids = save_detected(db, [g], StagingManager(tmp_path / "appdata"))
    row = db.one("SELECT * FROM games WHERE id=?", (ids[g.key],))
    assert row["profile"] == "lethalcompany" and row["slug"] == "lethalcompany"
    assert db.external_game_id(row["id"], "thunderstore") == "lethal-company"


# ════════════════════════════════════════════════════════════════════════
#  Análisis de estructura
# ════════════════════════════════════════════════════════════════════════
def plan_for(tmp_path, profile_slug, files: dict[str, str], name="MiMod", game_root=None):
    mod = tmp_path / "mod"
    for rel in files:
        write(mod / rel, files[rel])
    return ModAnalyzer(get_profile(profile_slug)).analyze(mod, name, game_root)


def targets(plan):
    return sorted(plan.targets())


def test_bepinexpack_de_thunderstore_va_a_la_raiz(tmp_path):
    p = plan_for(tmp_path, "lethalcompany", {
        "manifest.json": "{}", "icon.png": "", "README.md": "",
        "BepInExPack/BepInEx/core/BepInEx.dll": "", "BepInExPack/winhttp.dll": "",
        "BepInExPack/doorstop_config.ini": ""})
    assert targets(p) == ["BepInEx/core/BepInEx.dll", "doorstop_config.ini", "winhttp.dll"]
    assert [l.key for l in p.provides_loaders] == ["bepinex"] and not p.needs_review


def test_paquete_thunderstore_tipico(tmp_path):
    p = plan_for(tmp_path, "lethalcompany", {
        "manifest.json": "{}", "README.md": "", "icon.png": "",
        "plugins/MoreCompany.dll": "", "plugins/assets/skin.bundle": "", "config/more.cfg": ""},
        name="notnotnotswipez-MoreCompany")
    assert targets(p) == ["BepInEx/config/more.cfg",
                          "BepInEx/plugins/notnotnotswipez-MoreCompany/MoreCompany.dll",
                          "BepInEx/plugins/notnotnotswipez-MoreCompany/assets/skin.bundle",
                          "BepInEx/plugins/notnotnotswipez-MoreCompany/icon.png",
                          "BepInEx/plugins/notnotnotswipez-MoreCompany/manifest.json"]
    assert PurePosixPath("README.md") in p.ignored


def test_dll_suelta_en_carpeta_envoltorio(tmp_path):
    p = plan_for(tmp_path, "valheim", {"MiMod-1.2/MiMod.dll": "", "MiMod-1.2/leeme.txt": ""})
    assert targets(p) == ["BepInEx/plugins/MiMod/MiMod.dll"]


def test_bepinex_faltante_se_detecta(tmp_path):
    game = tmp_path / "juego"
    game.mkdir()
    p = plan_for(tmp_path, "lethalcompany", {"x.dll": ""}, game_root=game)
    assert [l.key for l in p.missing_loaders] == ["bepinex"]


def test_skyrim_contenido_suelto_va_a_data_y_fomod_pide_revision(tmp_path):
    p = plan_for(tmp_path, "skyrimse", {
        "MiMod v1/MiMod.esp": "", "MiMod v1/textures/a.dds": "", "MiMod v1/fomod/ModuleConfig.xml": ""})
    assert targets(p) == ["Data/MiMod.esp", "Data/textures/a.dds"]
    assert p.has_fomod and p.needs_review


def test_skyrim_mod_que_ya_trae_data(tmp_path):
    p = plan_for(tmp_path, "skyrimse", {"Data/SKSE/Plugins/x.dll": "", "Data/x.esp": ""})
    assert targets(p) == ["Data/SKSE/Plugins/x.dll", "Data/x.esp"]


def test_skyrim_variantes_avisa(tmp_path):
    p = plan_for(tmp_path, "skyrimse", {"1K/textures/a.dds": "", "2K/textures/a.dds": ""})
    assert targets(p) == ["Data/textures/a.dds"] and any("variantes" in w for w in p.warnings)


def test_stardew_una_carpeta_por_mod(tmp_path):
    p = plan_for(tmp_path, "stardewvalley", {
        "Pack/ModA/manifest.json": "", "Pack/ModA/ModA.dll": "", "Pack/ModB/manifest.json": "",
        "Pack/ModB/assets/x.png": ""})
    assert targets(p) == ["Mods/ModA/ModA.dll", "Mods/ModA/manifest.json",
                          "Mods/ModB/assets/x.png", "Mods/ModB/manifest.json"]


def test_palworld_pak_y_ue4ss(tmp_path):
    assert targets(plan_for(tmp_path, "palworld", {"Mod_P.pak": ""})) == ["Pal/Content/Paks/~mods/Mod_P.pak"]
    p = plan_for(tmp_path / "b", "palworld", {"Mods/Volar/Scripts/main.lua": "", "Mods/Volar/enabled.txt": ""})
    assert targets(p) == ["Pal/Binaries/Win64/ue4ss/Mods/Volar/Scripts/main.lua",
                          "Pal/Binaries/Win64/ue4ss/Mods/Volar/enabled.txt"]


def test_juego_desconocido_pide_revision(tmp_path):
    p = plan_for(tmp_path, "generic", {"Envoltorio/cosa.bin": ""})
    assert targets(p) == ["cosa.bin"] and p.needs_review and p.rule == "sin-reconocer"


# ════════════════════════════════════════════════════════════════════════
#  Extracción
# ════════════════════════════════════════════════════════════════════════
def make_zip(path: Path, entries: dict[str, bytes], symlink: str | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (0o120777 << 16)
            zf.writestr(info, "/etc/passwd")
    return path


@pytest.fixture(params=["python", "7zip"])
def extractor(request):
    ex = Extractor()
    if request.param == "python":
        ex.seven_zip = None
    elif ex.seven_zip is None:
        pytest.skip("7-Zip no está instalado aquí")
    return ex


def test_extraer_zip(tmp_path, extractor):
    z = make_zip(tmp_path / "m.zip", {"a/b.txt": b"hola", "c.dll": b"x"})
    r = extractor.extract_sync(z, tmp_path / "out")
    assert r.files == 2 and (tmp_path / "out" / "a" / "b.txt").read_text() == "hola"


def test_zip_slip_se_rechaza_sin_dejar_restos(tmp_path, extractor):
    z = make_zip(tmp_path / "malo.zip", {"ok.txt": b"1", "../../fuera.txt": b"2"})
    with pytest.raises(UnsafeArchive):
        extractor.extract_sync(z, tmp_path / "out")
    assert not (tmp_path / "out").exists() and not (tmp_path / "fuera.txt").exists()
    assert not list(tmp_path.glob("*.partial"))


def test_enlaces_simbolicos_del_zip_se_ignoran(tmp_path):
    ex = Extractor()
    ex.seven_zip = None
    z = make_zip(tmp_path / "s.zip", {"real.txt": b"1"}, symlink="trampa")
    ex.extract_sync(z, tmp_path / "out")
    assert sorted(os.listdir(tmp_path / "out")) == ["real.txt"]


def test_archivo_suelto_y_tar(tmp_path):
    ex = Extractor()
    ex.seven_zip = None
    dll = write(tmp_path / "Plugin.dll", b"MZ...")
    ex.extract_sync(dll, tmp_path / "o1")
    assert (tmp_path / "o1" / "Plugin.dll").exists()
    tgz = tmp_path / "m.tar.gz"
    with tarfile.open(tgz, "w:gz") as tf:
        info = tarfile.TarInfo("dir/f.txt")
        info.size = 3
        tf.addfile(info, io.BytesIO(b"abc"))
    ex.extract_sync(tgz, tmp_path / "o2")
    assert (tmp_path / "o2" / "dir" / "f.txt").read_bytes() == b"abc"


# ════════════════════════════════════════════════════════════════════════
#  Descargas
# ════════════════════════════════════════════════════════════════════════
PAYLOAD = os.urandom(300_000)
SHA256 = hashlib.sha256(PAYLOAD).hexdigest()


def range_server(payload=PAYLOAD, *, honor_range=True, delay=0.0, log=None, fail_first=0):
    state = {"fails": fail_first}

    async def handler(request: httpx.Request):
        if log is not None:
            log.append(request.headers.get("Range"))
        if state["fails"] > 0:
            state["fails"] -= 1
            raise httpx.ConnectError("cable desenchufado")
        start = 0
        rng = request.headers.get("Range")
        if rng and honor_range:
            start = int(rng.split("=")[1].split("-")[0])
        body = payload[start:]

        async def chunks():
            for i in range(0, len(body), 16_384):
                if delay:
                    await asyncio.sleep(delay)
                yield body[i:i + 16_384]
        headers = {"Content-Length": str(len(body))}
        if start:
            headers["Content-Range"] = f"bytes {start}-{len(payload) - 1}/{len(payload)}"
        return httpx.Response(206 if start else 200, headers=headers, content=chunks())
    return handler


def dm_for(handler, **kw):
    http = HttpClient(transport=httpx.MockTransport(handler), limits={"dl.test": RateLimit(1000, burst=1000)})
    return http, DownloadManager(http, chunk_size=16_384, retry_delay=0.01, **kw)


def test_descarga_con_progreso_y_sha256(tmp_path):
    seen = []

    async def go():
        http, dm = dm_for(range_server(), on_progress=lambda t: seen.append(t.done_bytes), progress_interval=0)
        t = dm.add("https://dl.test/mod.zip", tmp_path / "mod.zip", size=len(PAYLOAD), hashes={"sha256": SHA256})
        t = await dm.wait(t.id)
        await http.aclose()
        return t
    t = run(go())
    assert t.state is DownloadState.DONE and (tmp_path / "mod.zip").read_bytes() == PAYLOAD
    assert t.progress == 1.0 and len(seen) > 3 and not t.part_path.exists()


def test_hash_incorrecto_falla_y_borra(tmp_path):
    async def go():
        http, dm = dm_for(range_server())
        t = await dm.wait(dm.add("https://dl.test/m", tmp_path / "m", hashes={"md5": "0" * 32}).id)
        await http.aclose()
        return t
    t = run(go())
    assert t.state is DownloadState.FAILED and "dañado" in t.error
    assert not (tmp_path / "m").exists() and not t.part_path.exists()


@pytest.mark.parametrize("honor", [True, False])
def test_reanuda_desde_el_part(tmp_path, honor):
    write(tmp_path / "m.zip.part", PAYLOAD[:100_000])
    log = []

    async def go():
        http, dm = dm_for(range_server(honor_range=honor, log=log))
        t = await dm.wait(dm.add("https://dl.test/m.zip", tmp_path / "m.zip", hashes={"sha256": SHA256}).id)
        await http.aclose()
        return t
    t = run(go())
    assert log[0] == "bytes=100000-"
    assert t.state is DownloadState.DONE and (tmp_path / "m.zip").read_bytes() == PAYLOAD


def test_reintenta_tras_corte_de_red(tmp_path):
    async def go():
        http, dm = dm_for(range_server(fail_first=2))
        t = await dm.wait(dm.add("https://dl.test/m", tmp_path / "m", hashes={"sha256": SHA256}).id)
        await http.aclose()
        return t
    assert run(go()).state is DownloadState.DONE


def test_pausa_y_reanudacion(tmp_path):
    log = []

    async def go():
        http, dm = dm_for(range_server(delay=0.005, log=log))
        t = dm.add("https://dl.test/m", tmp_path / "m", hashes={"sha256": SHA256})
        while t.done_bytes < 50_000:
            await asyncio.sleep(0.005)
        await dm.pause(t.id)
        paused_state, part_size = t.state, t.part_path.stat().st_size
        dm.resume(t.id)
        await dm.wait(t.id)
        await http.aclose()
        return t, paused_state, part_size
    t, paused_state, part_size = run(go())
    assert paused_state is DownloadState.PAUSED and part_size >= 50_000
    assert log[-1] == f"bytes={part_size}-"
    assert t.state is DownloadState.DONE and (tmp_path / "m").read_bytes() == PAYLOAD


def test_cancelar_borra_el_part(tmp_path):
    async def go():
        http, dm = dm_for(range_server(delay=0.01))
        t = dm.add("https://dl.test/m", tmp_path / "m")
        await asyncio.sleep(0.05)
        await dm.cancel(t.id)
        await dm.wait(t.id)
        await http.aclose()
        return t
    t = run(go())
    assert t.state is DownloadState.CANCELLED and not t.part_path.exists() and not (tmp_path / "m").exists()


# ════════════════════════════════════════════════════════════════════════
#  Despliegue
# ════════════════════════════════════════════════════════════════════════
class Env:
    """Un juego falso con su BD, staging y helpers para crear mods."""

    def __init__(self, tmp: Path, deploy_method="hardlink"):
        self.root = tmp / "Juego"
        write(self.root / "Juego.exe", "exe")
        write(self.root / "Data" / "original.txt", "ORIGINAL")
        self.snapshot = tree(self.root)
        self.staging = tmp / "staging"
        self.db = Database(tmp / "m.db")
        self.db.execute("INSERT INTO games (slug,name,install_path,staging_path,deploy_method) VALUES (?,?,?,?,?)",
                        ("juego", "Juego", str(self.root), str(self.staging), deploy_method))
        self.game_id = self.db.one("SELECT id FROM games")["id"]
        self.dep = Deployer(self.db)

    def mod(self, name: str, files: dict[str, str], priority=0, enabled=True) -> int:
        cur = self.db.execute("INSERT INTO mods (game_id,source,source_mod_id,name,state,priority) VALUES (?,?,?,?,?,?)",
                              (self.game_id, "local", name, name, "enabled" if enabled else "disabled", priority))
        mod_id = cur.lastrowid
        mod_dir = self.staging / "mods" / name
        maps = [FileMapping(write(mod_dir / rel, data), PurePosixPath(rel)) for rel, data in files.items()]
        self.dep.register_plan(mod_id, InstallPlan(mod_dir, "test", 1.0, maps))
        return mod_id

    def state(self, mod_id, state):
        self.db.execute("UPDATE mods SET state=? WHERE id=?", (state, mod_id))


def test_despliegue_con_hardlinks_y_purga_limpia(tmp_path):
    env = Env(tmp_path)
    env.mod("A", {"BepInEx/plugins/A/a.dll": "A", "Data/nuevo.txt": "N"})
    r = env.dep.deploy_game(env.game_id)
    assert r.ok and r.added == 2 and r.methods["hardlink"] == 2
    assert os.path.samefile(env.root / "BepInEx/plugins/A/a.dll", env.staging / "mods/A/BepInEx/plugins/A/a.dll")
    assert env.dep.deploy_game(env.game_id).unchanged == 2          # segunda vez: nada que hacer
    r = env.dep.purge_game(env.game_id)
    assert r.removed == 2 and tree(env.root) == env.snapshot
    assert not (env.root / "BepInEx").exists()                       # carpetas creadas por ModHub, borradas
    assert env.db.query("SELECT * FROM deployed_files") == [] and env.db.query("SELECT * FROM deployed_dirs") == []


def test_original_del_juego_se_respalda_y_se_restaura(tmp_path):
    env = Env(tmp_path)
    m = env.mod("Retexturas", {"Data/original.txt": "MODIFICADO"})
    r = env.dep.deploy_game(env.game_id)
    assert r.backed_up == 1 and (env.root / "Data/original.txt").read_text() == "MODIFICADO"
    env.state(m, "disabled")
    r = env.dep.deploy_game(env.game_id)
    assert r.restored == 1 and tree(env.root) == env.snapshot


def test_conflictos_gana_la_prioridad_mas_alta(tmp_path):
    env = Env(tmp_path)
    low = env.mod("Bajo", {"x.txt": "bajo"}, priority=1)
    high = env.mod("Alto", {"x.txt": "alto"}, priority=5)
    r = env.dep.deploy_game(env.game_id)
    assert (env.root / "x.txt").read_text() == "alto" and r.conflicts == {"x.txt": [low, high]}
    env.state(high, "disabled")
    env.dep.deploy_game(env.game_id)
    assert (env.root / "x.txt").read_text() == "bajo"


def test_archivo_cambiado_por_el_usuario_no_se_borra(tmp_path):
    env = Env(tmp_path)
    env.mod("A", {"cfg.ini": "mod"})
    env.dep.deploy_game(env.game_id)
    (env.root / "cfg.ini").unlink()
    write(env.root / "cfg.ini", "editado por el juego")
    r = env.dep.purge_game(env.game_id)
    assert (env.root / "cfg.ini").read_text() == "editado por el juego" and r.warnings
    assert env.dep.verify_game(env.game_id) == []


def test_mod_actualizado_se_reenlaza(tmp_path):
    env = Env(tmp_path)
    env.mod("A", {"a.txt": "v1"})
    env.dep.deploy_game(env.game_id)
    src = env.staging / "mods/A/a.txt"
    src.unlink()
    write(src, "v2")                                     # re-extracción: archivo nuevo (otro inodo)
    r = env.dep.deploy_game(env.game_id)
    assert r.removed == 1 and r.added == 1 and (env.root / "a.txt").read_text() == "v2"


def test_sin_hardlink_usa_symlink_y_luego_copia(tmp_path, monkeypatch):
    def no_hardlink(src, dest):
        raise OSError(18, "Invalid cross-device link")
    monkeypatch.setattr(Deployer, "_hardlink", staticmethod(no_hardlink))
    env = Env(tmp_path)
    env.mod("A", {"a.txt": "A"})
    # En Windows sin «Modo de desarrollador» no se pueden crear symlinks: entonces debe copiar.
    method = next(iter(env.dep.deploy_game(env.game_id).methods))
    assert method in ("symlink", "copy")
    assert (env.root / "a.txt").is_symlink() == (method == "symlink")
    env.dep.purge_game(env.game_id)
    env.dep.allow_symlink = False
    assert env.dep.deploy_game(env.game_id).methods == {"copy": 1}
    env.dep.purge_game(env.game_id)
    assert tree(env.root) == env.snapshot


def test_rutas_que_salen_del_juego_se_rechazan(tmp_path):
    env = Env(tmp_path)
    cur = env.db.execute("INSERT INTO mods (game_id,source,source_mod_id,name,state) VALUES (?,?,?,?,?)",
                         (env.game_id, "local", "malo", "malo", "enabled"))
    write(tmp_path / "malo.txt", "x")
    env.db.execute("INSERT INTO mod_files VALUES (?,?,?)", (cur.lastrowid, "../fuera.txt", str(tmp_path / "malo.txt")))
    r = env.dep.deploy_game(env.game_id)
    assert r.errors and not (tmp_path / "fuera.txt").exists()


# ════════════════════════════════════════════════════════════════════════
#  De extremo a extremo: Thunderstore -> juego
# ════════════════════════════════════════════════════════════════════════
def test_instalacion_completa_y_desinstalacion(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr("README.md", "léeme")
        zf.writestr("plugins/Linterna.dll", "DLL")
    zip_bytes = buf.getvalue()
    index = [{"full_name": "Autor-Linterna", "name": "Linterna", "owner": "Autor", "package_url": "u",
              "is_deprecated": False, "versions": [{
                  "full_name": "Autor-Linterna-1.0.0", "name": "Linterna", "version_number": "1.0.0",
                  "download_url": "https://thunderstore.io/package/download/Autor/Linterna/1.0.0/",
                  "file_size": len(zip_bytes), "dependencies": ["BepInEx-BepInExPack-5.4.2100"]}]}]

    def handler(req):
        if "/api/v1/package/" in str(req.url):
            return httpx.Response(200, json=index)
        if "/package/download/" in str(req.url):
            return httpx.Response(200, content=zip_bytes)
        return httpx.Response(404)

    game_root = write(tmp_path / "Lethal Company" / "Lethal Company.exe").parent
    write(game_root / "BepInEx/core/BepInEx.dll")
    before = tree(game_root)
    db = Database(tmp_path / "m.db")
    staging = StagingManager(tmp_path / "appdata")
    det = GameDetector(registry=FakeRegistry({}), env={}, drives=[], steam_paths=[])
    game_id = save_detected(db, [det.from_folder(game_root)], staging)["custom:" + det.from_folder(game_root).store_id]

    async def go():
        async with HttpClient(transport=httpx.MockTransport(handler),
                              limits={"thunderstore.io": RateLimit(1000, burst=1000)}) as http:
            inst = ModInstaller(db, SourceRegistry(http, cache=db, only=["thunderstore"]), DownloadManager(http),
                                Extractor(), Deployer(db), staging)
            res = await inst.install(game_id, "thunderstore", "Autor-Linterna", "Autor-Linterna-1.0.0")
            installed = tree(game_root)
            report = inst.uninstall(res.mod_id)
            return res, installed, report
    res, installed, report = run(go())
    assert res.status == "installed", res.message
    assert installed["BepInEx/plugins/Autor-Linterna/Linterna.dll"] == b"DLL"
    assert "BepInEx/plugins/Autor-Linterna/README.md" not in installed
    assert [d.mod_id for d in res.dependencies] == ["BepInEx-BepInExPack"]
    assert report.ok and tree(game_root) == before                    # desinstalación 100 % limpia
    assert db.query("SELECT * FROM mods") == []
