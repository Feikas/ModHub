"""Versión del juego (Steam / Xbox · PC Game Pass / Epic / GOG): filtros, archivo preferido y rutas WinGDK."""
from __future__ import annotations

from pathlib import Path

import pytest

from modhub.core.platform_paths import PathAdapter, loader_paths
from modhub.sources.base import ModFile, ModInfo, SearchFilterOptions
from modhub.sources.platforms import is_compatible, pick_file, platforms_in
from test_fase3 import Env, write


@pytest.mark.parametrize("text, yes, no", [
    ("Game Pass version", {"xbox"}, set()),
    ("MS Store/Gamepass", {"xbox"}, set()),
    ("SFSE - Steam only", {"steam"}, set()),
    ("Works on Steam and Xbox", {"steam", "xbox"}, set()),
    ("Not compatible with Game Pass", set(), {"xbox"}),
    ("No Xbox support", set(), {"xbox"}),
    ("Epic Games Store version", {"epic"}, set()),
    ("GOG version", {"gog"}, set()),
    # falsos positivos que NO deben marcar nada
    ("Steam Workshop collection", set(), set()),
    ("Steampunk outfit", set(), set()),
    ("Steam Deck controls", set(), set()),
    ("Xbox Series controller icons", set(), set()),
    ("Epic armor set", set(), set()),
])
def test_lee_la_version_del_texto(text, yes, no):
    got_yes, got_no = platforms_in(text)
    assert set(got_yes) == yes and set(got_no) == no


def test_compatibilidad():
    assert is_compatible(frozenset(), frozenset(), "xbox")                       # universal
    assert not is_compatible(frozenset({"steam"}), frozenset(), "xbox")          # solo Steam
    assert is_compatible(frozenset({"steam", "xbox"}), frozenset(), "xbox")
    assert not is_compatible(frozenset(), frozenset({"xbox"}), "xbox")           # «no vale para Game Pass»
    assert is_compatible(frozenset({"steam"}), frozenset(), None)                # versión desconocida: no se oculta


def test_filtro_oculta_lo_de_otra_version():
    f = SearchFilterOptions(platform="xbox")
    mods = [ModInfo("nexus", "1", "Universal"),
            ModInfo("nexus", "2", "Solo Steam", platforms=frozenset({"steam"})),
            ModInfo("nexus", "3", "Para Game Pass", platforms=frozenset({"xbox"}))]
    assert [m.name for m in f.apply(mods)] == ["Universal", "Para Game Pass"]
    assert len(SearchFilterOptions().apply(mods)) == 3


def test_elige_el_archivo_de_tu_version():
    files = [ModFile("nexus", "1", "a", "Main - Steam", is_primary=True, platforms=frozenset({"steam"})),
             ModFile("nexus", "1", "b", "Main - Game Pass", platforms=frozenset({"xbox"})),
             ModFile("nexus", "1", "c", "Optional textures")]
    assert pick_file(files, "xbox").file_id == "b"
    assert pick_file(files, "steam").file_id == "a"
    assert pick_file(files, "gog").file_id == "c"           # ni Steam ni Xbox: el universal
    assert pick_file(files, None) is None                     # sin saber la versión: lo de siempre


def test_rutas_wingdk(tmp_path):
    gdk = tmp_path / "xbox"
    write(gdk / "Pal" / "Binaries" / "WinGDK" / "Palworld-WinGDK-Shipping.exe")
    steam = tmp_path / "steam"
    write(steam / "Pal" / "Binaries" / "Win64" / "Palworld-Win64-Shipping.exe")
    assert PathAdapter(gdk).adapt("Pal/Binaries/Win64/ue4ss/Mods/x/main.lua") == "Pal/Binaries/WinGDK/ue4ss/Mods/x/main.lua"
    assert PathAdapter(gdk).adapt("Pal/Content/Paks/~mods/a.pak") == "Pal/Content/Paks/~mods/a.pak"
    assert PathAdapter(steam).adapt("Pal/Binaries/Win64/dwmapi.dll") == "Pal/Binaries/Win64/dwmapi.dll"
    assert PathAdapter(gdk).is_gdk and not PathAdapter(steam).is_gdk
    # un gestor antiguo creó Win64 vacío en la versión Xbox: manda la carpeta que tiene el .exe
    (gdk / "Pal" / "Binaries" / "Win64").mkdir()
    assert PathAdapter(gdk).adapt("Pal/Binaries/Win64/dwmapi.dll") == "Pal/Binaries/WinGDK/dwmapi.dll"
    assert "Pal/Binaries/WinGDK/dwmapi.dll" in loader_paths(gdk, ("Pal/Binaries/Win64/dwmapi.dll",))


def test_despliegue_en_version_xbox_va_a_wingdk(tmp_path):
    env = Env(tmp_path)
    write(env.root / "Pal" / "Binaries" / "WinGDK" / "Palworld-WinGDK-Shipping.exe")
    env.mod("UE4SS", {"Pal/Binaries/Win64/dwmapi.dll": "x", "Pal/Binaries/Win64/ue4ss/UE4SS.dll": "y"})
    rep = env.dep.deploy_game(env.game_id)
    assert rep.ok and rep.added == 2
    assert (env.root / "Pal/Binaries/WinGDK/dwmapi.dll").exists()
    assert not (env.root / "Pal/Binaries/Win64").exists()
    assert env.dep.purge_game(env.game_id).ok and not (env.root / "Pal/Binaries/WinGDK/dwmapi.dll").exists()
    assert (env.root / "Pal/Binaries/WinGDK/Palworld-WinGDK-Shipping.exe").exists()


def test_script_extenders_no_existen_en_game_pass():
    from modhub.games.loaders import F4SE, SKSE64
    assert "xbox" in SKSE64.unsupported and "xbox" in F4SE.unsupported


def test_juego_manual_en_xboxgames_se_reconoce_como_game_pass(tmp_path):
    import httpx
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from modhub.ui.context import AppContext
    ctx = AppContext(tmp_path / "data", use_keyring=False,
                     http_transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    gdk = tmp_path / "D" / "XboxGames" / "Palworld" / "Content"
    write(gdk / "Pal" / "Binaries" / "WinGDK" / "Palworld-WinGDK-Shipping.exe")
    steam = tmp_path / "Juegos" / "Hades"
    write(steam / "Hades.exe")
    assert ctx.game_platform(ctx.add_game_folder(str(gdk), "Palworld")) == "xbox"
    assert ctx.game_platform(ctx.add_game_folder(str(steam), "Hades")) is None
