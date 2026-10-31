# ModHub — Arquitectura (Fases 1 y 2)

## 1. Stack elegido: Python 3.12 + PySide6 (Qt 6)

| Opción | Pros | Contras | Veredicto |
|---|---|---|---|
| **Python + PySide6** | Ecosistema enorme para APIs/scraping/archivos, Qt es nativo y rápido, `asyncio` + `qasync` | El .exe pesa ~60-90 MB | ✅ **Elegida** |
| Electron + Node | UI web muy flexible (Vortex lo usa) | 150-250 MB de RAM en reposo, no es "ligera" | ❌ |
| C# .NET 8 + WPF/Avalonia | La más nativa en Windows | Más lenta de iterar, scraping menos cómodo | Plan B |
| CustomTkinter | Fácil | Se queda corta para listas grandes, arrastrar/soltar, temas | ❌ |

Librerías clave: `httpx` (HTTP asíncrono y HTTP/2), `selectolax` (HTML), `feedparser` (RSS),
`keyring` (API keys guardadas en el Administrador de credenciales de Windows, nunca en texto plano),
`7z.exe` empaquetado (zip/rar/7z con una sola herramienta), `pywin32` (registro y enlaces).
Empaquetado final con **Nuitka** o **PyInstaller** + instalador **Inno Setup**.

## 2. Estructura de carpetas

```
ModHub/
├─ modhub/
│  ├─ main.py                 (Fase 4) arranque, instancia única, recibe URLs de protocolo
│  ├─ settings.py             (Fase 3) ajustes + keyring
│  ├─ db/
│  │  ├─ schema.sql           ✅ tablas
│  │  └─ database.py          ✅ acceso + caché de APIs
│  ├─ sources/                ✅ FASE 2
│  │  ├─ base.py              contrato común (SourceAdapter, ModFile, DownloadTicket…)
│  │  ├─ http.py              cliente compartido: límites por web, reintentos, User-Agent
│  │  ├─ nexus.py  curseforge.py  modrinth.py  thunderstore.py
│  │  ├─ modio.py  gamebanana.py  steam_workshop.py
│  │  ├─ scraping/moddb.py  scraping/ayakamods.py
│  │  └─ __init__.py          registro + búsqueda global en paralelo + enrutado de protocolos
│  ├─ core/                   (Fase 3) el núcleo
│  │  ├─ game_detect.py       Steam (libraryfolders.vdf), Epic (manifests), Xbox, GOG
│  │  ├─ downloader.py        cola concurrente, reanudar, verificación de hash
│  │  ├─ extractor.py         7z.exe + detector de estructura (Data/, BepInEx/plugins/…)
│  │  ├─ deployer.py          hardlinks / symlinks / copia + registro para deshacer
│  │  ├─ dependencies.py      resolución de dependencias y orden
│  │  └─ watch_downloads.py   vigila Descargas para los casos "abrir en navegador"
│  ├─ games/                  (Fase 3) perfiles por juego: reglas de carpetas y loaders
│  └─ ui/                     (Fase 4)
├─ tests/test_fase2.py        ✅ 10 pruebas sin red
├─ requirements.txt
└─ ARQUITECTURA.md
```

Datos del usuario en `%APPDATA%\ModHub\` (base de datos, ajustes) y la carpeta de
staging (mods descomprimidos) **en el mismo disco que cada juego** (ver §4).

## 3. Base de datos (SQLite, modo WAL)

- `games` → juego, ruta, tienda, método de despliegue, carpeta de staging.
- `game_source_ids` → el ID de ese juego en cada web (Nexus usa `skyrimspecialedition`,
  CurseForge `432`, Thunderstore `lethal-company`, Steam su AppID…). **Clave para la búsqueda unificada.**
- `mods` → mod instalado, origen, versión, estado, prioridad (orden de carga).
- `deployed_files` → cada archivo enlazado en el juego. Desinstalar = borrar esa lista. Segundos.
- `mod_dependencies`, `downloads` (cola persistente), `api_cache`.

## 4. Correcciones importantes al planteamiento inicial

1. **Hardlinks por defecto, no symlinks.** En Windows los symlinks exigen administrador o
   "Modo de desarrollador", y algunos juegos/antitrampas no los siguen. Vortex usa hardlinks.
   Limitación: el staging debe estar en el **mismo disco** que el juego → una carpeta de staging por disco.
   Fallback automático: symlink → copia.
2. **No podemos secuestrar `nxm://`.** Es de Nexus (lo usan Vortex y MO2). Lo registramos
   *opcionalmente* ("Usar ModHub para Nexus") y además un protocolo propio `modhub://`.
3. **"One-click" no es posible en todas las webs, y no se debe forzar.** Ver tabla §5.

## 5. Matriz de fuentes

| Fuente | Acceso | Clave | Descarga automática | Notas |
|---|---|---|---|---|
| **Modrinth** | API pública | No | ✅ Sí | La más sencilla. Mayormente Minecraft |
| **Thunderstore** | API pública | No | ✅ Sí | Índice por comunidad cacheado; protocolo `ror2mm://` |
| **CurseForge** | API oficial | Sí (gratis) | ✅ salvo si el autor lo prohíbe | `allowModDistribution=false` → abrir web |
| **mod.io** | API oficial | Sí (gratis) | ✅ normalmente | Algunos juegos piden login OAuth |
| **GameBanana** | API v11 pública | No | ✅ si el archivo está alojado allí | Botón 1-click requiere registrar el gestor en su web |
| **Nexus Mods** | API v1 + GraphQL v2 | Sí | ✅ Premium · ⚠️ Gratis solo con clic en web (`nxm://`) | Para distribuir: registrar app y usar su SSO |
| **Steam Workshop** | Web API | Para buscar | ⚠️ Vía Steam (suscripción) o steamcmd | No existen enlaces directos |
| **Mod DB** | Sin API: RSS + HTML | No | ❌ Abrir en navegador | RSS para listar archivos; respetar la web |
| **AyakaMods** | Foro tipo XenForo | ? | ❌ Abrir en navegador | Tiene docs de API por revisar; muchos archivos externos |

Los casos ❌/⚠️ no son un callejón sin salida: `DownloadPolicy` le dice a la UI qué hacer
(abrir la web, esperar el `nxm://`, lanzar Steam) y `watch_downloads.py` recoge el archivo
cuando el navegador lo termina de bajar, así que para el usuario sigue siendo casi automático.

## 6. Pendiente de verificar contra las APIs reales (Fase 2b)

- Nexus GraphQL v2: nombres exactos de filtros de búsqueda.
- GameBanana: endpoint de búsqueda `Util/Search/Results`.
- AyakaMods: selectores HTML y su API (docs.ayakamods.com).
- Mod DB: selectores de la página de búsqueda.
