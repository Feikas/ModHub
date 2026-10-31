# ModHub — Gestor universal de mods para Windows

Busca, descarga e instala mods de Nexus Mods, Thunderstore, Modrinth, CurseForge, mod.io,
GameBanana, Steam Workshop, Mod DB y AyakaMods desde una sola aplicación.

- **Empezar:** lee `GUIA_WINDOWS.md` (instalar.bat → demo.bat → ejecutar.bat).
- **Arquitectura:** `ARQUITECTURA.md`.
- **Pruebas:** `probar.bat` (o `python -m pytest`).

Estructura: `modhub/sources` (webs), `modhub/core` (descargas, extracción, despliegue, FOMOD),
`modhub/games` (perfiles de juegos), `modhub/ui` (interfaz PySide6), `modhub/main.py` (arranque).
