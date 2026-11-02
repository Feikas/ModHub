"""La descripción del mod dice dónde va (Documentos, AppData, una carpeta del juego…)."""
from __future__ import annotations

from pathlib import Path, PurePosixPath as P

import pytest

from modhub.core.deployer import Deployer
from modhub.core.extractor import ModAnalyzer
from modhub.core.path_hints import extract_hints, interpret_path, plan_from_hints
from modhub.games import get_profile
from test_fase3 import Env, write


@pytest.mark.parametrize("text, target", [
    ("Extract the contents to Documents\\My Games\\Skyrim Special Edition\\SKSE",
     "@documents/My Games/Skyrim Special Edition/SKSE"),
    ("Copy Engine.ini to %LOCALAPPDATA%\\Pal\\Saved\\Config\\Windows", "@localappdata/Pal/Saved/Config/Windows"),
    ("Put the files in C:\\Users\\<username>\\AppData\\LocalLow\\Team Cherry\\Hollow Knight",
     "@locallow/Team Cherry/Hollow Knight"),
    ("Copia los archivos en Saved Games\\CD Projekt Red\\Cyberpunk 2077", "@savedgames/CD Projekt Red/Cyberpunk 2077"),
    ("Go to %USERPROFILE%\\Documents\\My Games\\Starfield and paste the ini", "@documents/My Games/Starfield"),
    ("<p>Extract to <b>%APPDATA%\\Balatro\\Mods</b></p>", "@appdata/Balatro/Mods"),
    ("Drop the .pak file into Content\\Paks\\~mods", "Content/Paks/~mods"),
    ("Install to steamapps\\common\\Stardew Valley\\Mods", "Mods"),
    ("Unzip into C:\\Program Files (x86)\\Steam\\steamapps\\common\\Hades\\Content", "Content"),
    ("Merge with <game folder>\\BepInEx\\plugins", "BepInEx/plugins"),
    ("Copy to [Game Folder]\\Data\\SKSE\\Plugins then run the game", "Data/SKSE/Plugins"),
    ("Copy to the Data folder", "Data"),
    ("Extract to your game folder.", ""),
    ("Extrae el contenido en la carpeta del juego", ""),
])
def test_lee_destinos(text, target):
    assert [h.target for h in extract_hints(text)][:1] == [target]


@pytest.mark.parametrize("text", [
    "If you had the old version, delete Documents\\My Games\\X\\old.ini",
    "Download from https://www.nexusmods.com/skyrim/mods/1",
    "Your saves are in C:\\Program Files (x86)\\Steam\\userdata\\123\\456",
    "Borra la carpeta Documents\\My Games\\X antes de actualizar",
    "Thanks to everyone who tested it.",
])
def test_ignora_lo_que_no_es_un_destino(text):
    assert extract_hints(text) == []


def test_nunca_sale_de_las_carpetas_permitidas():
    assert interpret_path("C:\\Windows\\System32") is None
    assert interpret_path("%TEMP%\\x") is None


def test_reparte_por_tipo_de_archivo_y_solapamiento():
    hints = extract_hints("Copy Engine.ini to %LOCALAPPDATA%\\Pal\\Saved\\Config\\Windows. "
                          "Drop the .pak into Content\\Paks\\~mods")
    plan = plan_from_hints([P("MiMod/Engine.ini"), P("MiMod/x.pak")], hints)
    assert {str(a): str(b) for a, b in plan.mappings} == {
        "MiMod/Engine.ini": "@localappdata/Pal/Saved/Config/Windows/Engine.ini",
        "MiMod/x.pak": "Content/Paks/~mods/x.pak"}
    # el zip ya trae «Config/Windows»: se encaja sin duplicar carpetas
    plan = plan_from_hints([P("Config/Windows/Engine.ini")], extract_hints(
        "Extract to %LOCALAPPDATA%\\Pal\\Saved\\Config\\Windows"))
    assert str(plan.mappings[0][1]) == "@localappdata/Pal/Saved/Config/Windows/Engine.ini"
    # el zip trae «Documents/My Games/…»: se respeta aunque la descripción diga otra cosa
    plan = plan_from_hints([P("MiMod/Documents/My Games/Fallout4/Fallout4Custom.ini")],
                           extract_hints("Extract to game folder"))
    assert str(plan.mappings[0][1]) == "@documents/My Games/Fallout4/Fallout4Custom.ini"


def test_analizador_usa_la_descripcion_si_no_esta_seguro(tmp_path):
    mod = tmp_path / "mod"
    write(mod / "MiMod-1.0" / "cool.pak")
    write(mod / "MiMod-1.0" / "readme.txt")
    plan = ModAnalyzer(get_profile(None)).analyze(mod, "MiMod", description="Drop the .pak into Content\\Paks\\~mods")
    assert plan.rule == "descripción del mod" and not plan.needs_review
    assert [str(m.target) for m in plan.mappings] == ["Content/Paks/~mods/cool.pak"]
    assert any("Según la descripción" in w for w in plan.warnings)


def test_fuera_del_juego_siempre_se_confirma(tmp_path):
    mod = tmp_path / "mod"
    write(mod / "Engine.ini")
    plan = ModAnalyzer(get_profile(None)).analyze(
        mod, "Cfg", description="Copy Engine.ini to %LOCALAPPDATA%\\Pal\\Saved\\Config\\Windows")
    assert plan.needs_review and "FUERA de la carpeta del juego" in plan.warnings[0]
    assert str(plan.mappings[0].target) == "@localappdata/Pal/Saved/Config/Windows/Engine.ini"


def test_sin_descripcion_util_no_cambia_nada(tmp_path):
    mod = tmp_path / "mod"
    write(mod / "cool.pak")
    a = ModAnalyzer(get_profile(None)).analyze(mod, "M")
    b = ModAnalyzer(get_profile(None)).analyze(mod, "M", description="Great mod, enjoy!")
    assert [str(m.target) for m in a.mappings] == [str(m.target) for m in b.mappings] and a.rule == b.rule


@pytest.fixture
def env_user(tmp_path):
    env = Env(tmp_path)
    docs = tmp_path / "Usuario" / "Documents"
    write(docs / "My Games" / "Juego" / "Juego.ini", "ORIGINAL DEL USUARIO")
    env.dep = Deployer(env.db, user_dirs={"documents": docs, "appdata": tmp_path / "Usuario" / "Roaming",
                                          "localappdata": tmp_path / "Usuario" / "Local",
                                          "locallow": tmp_path / "Usuario" / "LocalLow",
                                          "savedgames": tmp_path / "Usuario" / "Saved Games"})
    return env, docs


def test_despliega_en_documentos_con_copia_y_lo_deshace(env_user):
    env, docs = env_user
    env.mod("Cfg", {"@documents/My Games/Juego/Juego.ini": "DEL MOD",
                    "@documents/My Games/Juego/Nuevo/extra.ini": "EXTRA",
                    "Data/plugin.esp": "ESP"})
    rep = env.dep.deploy_game(env.game_id)
    assert rep.ok, rep.errors
    ini = docs / "My Games" / "Juego" / "Juego.ini"
    assert ini.read_text() == "DEL MOD" and rep.backed_up == 1
    assert rep.methods["copy"] == 2                         # fuera del juego: copia, nunca enlace
    assert (env.root / "Data" / "plugin.esp").exists()
    assert env.dep.verify_game(env.game_id) == []
    rep = env.dep.purge_game(env.game_id)
    assert rep.ok and ini.read_text() == "ORIGINAL DEL USUARIO"
    assert not (docs / "My Games" / "Juego" / "Nuevo").exists()     # la carpeta que creó ModHub se quita


@pytest.mark.parametrize("bad", ["@documents/suelto.ini", "@documents/../../evil.dll", "@system32/x/y.dll",
                                 "@localappdata/Steam/userdata/1/cfg.vdf"])
def test_destinos_peligrosos_se_rechazan(env_user, bad):
    env, docs = env_user
    env.mod("Mal", {bad: "x"})
    rep = env.dep.deploy_game(env.game_id)
    assert rep.errors and rep.added == 0
