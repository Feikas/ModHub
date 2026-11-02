"""Modloaders y script extenders más comunes."""
from __future__ import annotations

from dataclasses import replace

from .base import ModLoader

BEPINEX = ModLoader(
    key="bepinex", name="BepInEx",
    detect=("BepInEx/core/BepInEx.dll", "BepInEx/core/BepInEx.Core.dll", "BepInEx/core/BepInEx.Preloader.dll"),
    sources=(("thunderstore", "BepInEx-BepInExPack"),),
    homepage="https://github.com/BepInEx/BepInEx",
    note="Cargador de plugins para juegos Unity.")


def bepinex_pack(thunderstore_id: str) -> ModLoader:
    """Cada comunidad de Thunderstore publica su propio BepInExPack."""
    return replace(BEPINEX, sources=(("thunderstore", thunderstore_id),))


def ue4ss(project: str) -> ModLoader:
    base = f"{project}/Binaries/Win64"
    return ModLoader(
        key="ue4ss", name="UE4SS",
        detect=(f"{base}/dwmapi.dll", f"{base}/UE4SS.dll", f"{base}/ue4ss/UE4SS.dll"),
        homepage="https://github.com/UE4SS-RE/RE-UE4SS",
        note="Cargador de scripts Lua/C++ para juegos Unreal Engine 4/5.")


SKSE64 = ModLoader(key="skse", name="SKSE64", detect=("skse64_loader.exe",), launcher="skse64_loader.exe",
                   homepage="https://skse.silverlock.org", note="Script Extender de Skyrim SE/AE.",
                   unsupported=("xbox", "epic"))      # solo Steam y GOG (según su autor)

F4SE = ModLoader(key="f4se", name="F4SE", detect=("f4se_loader.exe",), launcher="f4se_loader.exe",
                 homepage="https://f4se.silverlock.org", note="Script Extender de Fallout 4.",
                 unsupported=("xbox",))               # no existe para la versión de Microsoft Store / Game Pass

SMAPI = ModLoader(key="smapi", name="SMAPI", detect=("StardewModdingAPI.exe", "StardewModdingAPI.dll"),
                  launcher="StardewModdingAPI.exe",
                  sources=(("nexus", "2400"),), homepage="https://smapi.io",
                  note="Imprescindible para casi todos los mods de Stardew Valley.")

REDMOD = ModLoader(key="redmod", name="REDmod", detect=("tools/redmod/bin/redMod.exe",),
                   homepage="https://www.cyberpunk.net/en/modding-support",
                   note="Herramienta oficial de CD Projekt (DLC gratuito). Necesaria para la carpeta mods/.")

CYBER_ENGINE_TWEAKS = ModLoader(key="cet", name="Cyber Engine Tweaks",
                                detect=("bin/x64/plugins/cyber_engine_tweaks.asi",),
                                sources=(("nexus", "107"),), homepage="https://github.com/maximegmd/CyberEngineTweaks")

SCRIPT_HOOK_V = ModLoader(key="scripthookv", name="Script Hook V", detect=("ScriptHookV.dll",),
                          homepage="http://www.dev-c.com/gtav/scripthookv/",
                          note="Se actualiza tras cada parche de GTA V; no funciona en GTA Online.")

ASI_LOADER = ModLoader(key="asiloader", name="ASI Loader", detect=("dinput8.dll",),
                       homepage="https://github.com/ThirteenAG/Ultimate-ASI-Loader")
