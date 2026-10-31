"""Prueba en vivo de la Fase 2 contra las webs reales.

Uso (desde la carpeta ModHub):
    python probar_fuentes.py                      # Modrinth + Thunderstore (no necesitan clave)
    python probar_fuentes.py "more company"       # otra búsqueda
    set NEXUS_API_KEY=tu_clave && python probar_fuentes.py   # añade Nexus (Windows)
"""
from __future__ import annotations

import asyncio
import os
import sys

from modhub.sources import DownloadPolicy, GameRef, HttpClient, SourceRegistry

QUERY = sys.argv[1] if len(sys.argv) > 1 else "company"

# Un "juego" de ejemplo con su ID en cada web.
GAMES = [
    GameRef("Lethal Company", ids={"thunderstore": "lethal-company", "nexus": "lethalcompany"}),
    GameRef("Minecraft (Fabric)", ids={"modrinth": "fabric"}),
]


def linea(c="─"):
    print(c * 72)


async def main() -> None:
    settings = {"nexus": {"api_key": os.environ.get("NEXUS_API_KEY"), "use_keyring": False}}
    async with HttpClient() as http:
        reg = SourceRegistry(http, settings, only=["modrinth", "thunderstore", "nexus"])

        for game in GAMES:
            linea("═")
            print(f"Buscando «{QUERY}» en {game.name}")
            linea("═")
            r = await reg.search_all(QUERY, game, page_size=5)
            for src, mods in r.by_source.items():
                print(f"\n[{src}] {len(mods)} resultados en {r.timings.get(src)} s")
                for m in mods:
                    print(f"   · {m.name:<35} {m.author:<20} ⬇ {m.downloads or 0:,}")
            for src, err in r.errors.items():
                print(f"\n[{src}] ❌ {err}")
            for src, why in r.skipped.items():
                print(f"[{src}] (omitido: {why})")

            # Detalle + ticket del primer resultado de cada fuente
            for src, mods in r.by_source.items():
                if not mods:
                    continue
                ad = reg.get(src)
                info = await ad.get_mod_details(mods[0].mod_id, game)
                f = info.latest_file()
                linea()
                print(f"Detalle [{src}] {info.name} — {len(info.files)} archivos")
                if not f:
                    continue
                print(f"   Último: {f.name} v{f.version} · {f.filename} · {f.size or 0:,} bytes")
                if f.dependencies:
                    print("   Depende de:", ", ".join(d.name or d.mod_id for d in f.dependencies[:5]))
                t = await ad.get_download_ticket(f.file_id, info.mod_id, game)
                print(f"   Ticket: {t.policy.value}", f"→ {t.url}" if t.policy is DownloadPolicy.AUTOMATIC
                      else f"→ {t.page_url}  ({t.note})")


if __name__ == "__main__":
    asyncio.run(main())
