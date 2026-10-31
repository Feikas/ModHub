"""Perfiles de juegos concretos.

Añadir un juego nuevo = añadir un GameProfile a PROFILES. Para juegos Unity con
BepInEx basta con una línea usando `bepinex_profile(...)`.
"""
from __future__ import annotations

from .base import GameProfile
from .loaders import (ASI_LOADER, CYBER_ENGINE_TWEAKS, F4SE, REDMOD, SCRIPT_HOOK_V, SKSE64, SMAPI,
                      bepinex_pack, ue4ss)
from .rules import FilesByExtension, FolderPerMarker, RootMarkers, ThunderstorePackage

# ─── Unity + BepInEx ───────────────────────────────────────────────────
BEPINEX_ROOT_MARKERS = ("bepinex", "winhttp.dll", "doorstop_config.ini", ".doorstop_version")


def bepinex_profile(slug: str, name: str, *, steam: str, exe: str, thunderstore: str | None = None,
                    nexus: str | None = None, pack_id: str = "BepInEx-BepInExPack", **extra) -> GameProfile:
    sources = {k: v for k, v in {"thunderstore": thunderstore, "nexus": nexus, "steam": steam}.items() if v}
    return GameProfile(
        slug=slug, name=name, steam_ids=(steam,), exe_names=(exe,), source_ids=sources,
        required_loaders=(bepinex_pack(pack_id),),
        rules=(
            RootMarkers(BEPINEX_ROOT_MARKERS, "", name="bepinex-raíz"),     # BepInExPack o mods con BepInEx/…
            ThunderstorePackage(),                                           # plugins/, patchers/, config/…
            FilesByExtension({".dll"}, "BepInEx/plugins/{mod}", name="plugin-dll"),
        ),
        fallback_target="BepInEx/plugins/{mod}",
        known_dirs={"BepInEx/plugins": "Plugins", "BepInEx/patchers": "Patchers", "BepInEx/config": "Configuración"},
        **extra)


LETHAL_COMPANY = bepinex_profile("lethalcompany", "Lethal Company", steam="1966720", exe="Lethal Company.exe",
                                 thunderstore="lethal-company", nexus="lethalcompany")
VALHEIM = bepinex_profile("valheim", "Valheim", steam="892970", exe="valheim.exe", thunderstore="valheim",
                          nexus="valheim", pack_id="denikson-BepInExPack_Valheim")
RISK_OF_RAIN_2 = bepinex_profile("riskofrain2", "Risk of Rain 2", steam="632360", exe="Risk of Rain 2.exe",
                                 thunderstore="riskofrain2", nexus="riskofrain2", pack_id="bbepis-BepInExPack")

# ─── Bethesda (Creation Engine) ────────────────────────────────────────
BETHESDA_DATA_MARKERS = (
    "*.esp", "*.esm", "*.esl", "*.bsa", "*.ba2", "meshes", "textures", "scripts", "sound", "music",
    "interface", "strings", "seq", "shadersfx", "lodsettings", "grass", "video", "materials", "vis",
    "skse", "f4se", "nemesis_engine", "tools", "calientetools", "dyndolod",
)


def _bethesda_rules(extender_exe: str):
    return (
        RootMarkers((extender_exe,), "", name="script-extender"),        # el propio SKSE/F4SE
        RootMarkers(("data",), "", name="carpeta-data"),                  # el mod ya trae Data/
        RootMarkers(BETHESDA_DATA_MARKERS, "Data", name="contenido-data"),
    )


SKYRIM_SE = GameProfile(
    slug="skyrimse", name="The Elder Scrolls V: Skyrim Special Edition",
    aliases=("Skyrim Special Edition", "Skyrim Anniversary Edition"),
    steam_ids=("489830",), exe_names=("SkyrimSE.exe",),
    source_ids={"nexus": "skyrimspecialedition", "steam": "489830"},
    optional_loaders=(SKSE64,), rules=_bethesda_rules("skse64_loader.exe"), fallback_target="Data",
    ignore_dirs=("__macosx", "fomod"),
    known_dirs={"Data": "Plugins, texturas, mallas, scripts", "Data/SKSE/Plugins": "Plugins de SKSE"},
    notes="El orden de carga de plugins (.esp/.esm/.esl) se gestiona en plugins.txt (pendiente).")

FALLOUT_4 = GameProfile(
    slug="fallout4", name="Fallout 4", steam_ids=("377160",), exe_names=("Fallout4.exe",),
    source_ids={"nexus": "fallout4", "steam": "377160"},
    optional_loaders=(F4SE,), rules=_bethesda_rules("f4se_loader.exe"), fallback_target="Data",
    ignore_dirs=("__macosx", "fomod"), known_dirs={"Data": "Plugins, texturas, mallas"})

# ─── Cyberpunk 2077 (REDengine) ────────────────────────────────────────
CYBERPUNK_2077 = GameProfile(
    slug="cyberpunk2077", name="Cyberpunk 2077", steam_ids=("1091500",), gog_ids=("1423049311",),
    exe_names=("bin/x64/Cyberpunk2077.exe",),
    source_ids={"nexus": "cyberpunk2077"},
    optional_loaders=(REDMOD, CYBER_ENGINE_TWEAKS),
    rules=(
        RootMarkers(("archive", "bin", "r6", "red4ext", "mods", "engine", "tools"), "", name="raíz-redengine"),
        FilesByExtension({".archive", ".xl"}, "archive/pc/mod", name="archive-suelto", only_matching=True),
        FilesByExtension({".reds"}, "r6/scripts/{mod}", name="redscript"),
    ),
    fallback_target="",
    known_dirs={"archive/pc/mod": "Mods .archive", "mods": "Mods REDmod", "r6/scripts": "Redscript",
                "bin/x64/plugins/cyber_engine_tweaks/mods": "Mods de CET", "red4ext/plugins": "Plugins RED4ext"})

# ─── Unreal Engine + UE4SS ─────────────────────────────────────────────
def ue_profile(slug: str, name: str, *, project: str, steam: str, exe: str, nexus: str | None = None,
               ue4ss_mods_dir: str | None = None, **extra) -> GameProfile:
    """`project` = carpeta del proyecto UE (Palworld -> "Pal")."""
    win64 = f"{project}/Binaries/Win64"
    mods_dir = ue4ss_mods_dir or f"{win64}/ue4ss/Mods"     # UE4SS ≥ 3.0; las versiones viejas usaban {win64}/Mods
    return GameProfile(
        slug=slug, name=name, steam_ids=(steam,), exe_names=(exe,),
        source_ids={k: v for k, v in {"nexus": nexus, "steam": steam}.items() if v},
        optional_loaders=(ue4ss(project),),
        rules=(
            RootMarkers((project.lower(),), "", name="carpeta-proyecto"),
            RootMarkers(("dwmapi.dll", "ue4ss"), win64, name="ue4ss"),
            RootMarkers(("binaries", "content"), project, name="contenido-proyecto"),
            FolderPerMarker(("scripts/main.lua", "dlls/main.dll"), mods_dir + "/{dir}", name="mod-ue4ss"),
            FilesByExtension({".pak", ".ucas", ".utoc"}, f"{project}/Content/Paks/~mods", name="pak",
                             only_matching=True),
        ),
        fallback_target=f"{project}/Content/Paks/~mods",
        known_dirs={f"{project}/Content/Paks/~mods": "Mods .pak", mods_dir: "Mods de UE4SS"},
        **extra)


PALWORLD = ue_profile("palworld", "Palworld", project="Pal", steam="1623730", exe="Palworld.exe", nexus="palworld")

# ─── Stardew Valley (SMAPI) ────────────────────────────────────────────
STARDEW_VALLEY = GameProfile(
    slug="stardewvalley", name="Stardew Valley", steam_ids=("413150",), gog_ids=("1453375253",),
    exe_names=("Stardew Valley.exe",), source_ids={"nexus": "stardewvalley"},
    required_loaders=(SMAPI,),
    rules=(
        RootMarkers(("stardewmoddingapi.exe", "smapi-internal"), "", name="smapi"),
        FolderPerMarker(("manifest.json",), "Mods/{dir}", name="mod-smapi"),
    ),
    fallback_target="Mods/{mod}", known_dirs={"Mods": "Mods de SMAPI (una carpeta por mod)"})

# ─── GTA V ─────────────────────────────────────────────────────────────
GTA_V = GameProfile(
    slug="gtav", name="Grand Theft Auto V", aliases=("Grand Theft Auto V Legacy", "GTA V"),
    steam_ids=("271590",), exe_names=("GTA5.exe",),
    source_ids={"nexus": "gta5"}, optional_loaders=(SCRIPT_HOOK_V, ASI_LOADER),
    rules=(RootMarkers(("scripthookv.dll", "dinput8.dll", "*.asi", "scripts"), "", name="scripts-raíz"),),
    fallback_target="",
    notes="Los mods para la carpeta mods/ de OpenIV (.oiv) requieren OpenIV y no se instalan automáticamente.")

# ─── Juego desconocido ─────────────────────────────────────────────────
GENERIC = GameProfile(
    slug="generic", name="Juego genérico",
    rules=(RootMarkers(BEPINEX_ROOT_MARKERS, "", name="bepinex-raíz"),),
    fallback_target="", notes="Sin perfil específico: todas las instalaciones necesitarán revisión.")

PROFILES: tuple[GameProfile, ...] = (
    LETHAL_COMPANY, VALHEIM, RISK_OF_RAIN_2, SKYRIM_SE, FALLOUT_4, CYBERPUNK_2077, PALWORLD,
    STARDEW_VALLEY, GTA_V,
)
