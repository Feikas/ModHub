# Guía para probar ModHub en Windows

Esta guía está pensada para hacerlo todo con doble clic, sin escribir código.
Tiempo estimado: 15 minutos la primera vez.

---

## 1. Requisitos (una sola vez)

| Programa | Para qué | Dónde |
|---|---|---|
| **Python 3.12** (o 3.13) | Ejecuta ModHub | https://www.python.org/downloads/ — en el instalador **marca «Add python.exe to PATH»** |
| **7-Zip** | Abrir mods en .7z y .rar | https://www.7-zip.org/ (opcional, pero muy recomendado) |

## 2. Instalar ModHub

1. Descomprime `ModHub-fase4.zip` en una carpeta fija, por ejemplo `D:\ModHub`.
   No la muevas después: Windows apuntará ahí cuando pulses enlaces en el navegador.
2. Haz doble clic en **`instalar.bat`**.
   Crea un entorno aislado (`.venv`) y descarga las librerías. No toca el resto de tu PC.
3. Si Windows muestra «Windows protegió su PC» (SmartScreen): *Más información → Ejecutar de todas formas*.
   Es normal con archivos .bat descargados.

## 3. Primera prueba: modo DEMO (100 % seguro)

Doble clic en **`demo.bat`**. Se abre ModHub con **3 juegos de mentira** en una carpeta temporal
y webs simuladas. Nada de lo que hagas aquí toca tus juegos.

Prueba esto:

- [ ] **Juegos**: aparecen Lethal Company (BepInEx ✔), Stardew Valley (SMAPI ✖) y Skyrim.
- [ ] **Buscador global** → elige «Lethal Company», escribe `company` y pulsa **Buscar**.
      Deben salir 3 resultados de Thunderstore con la insignia verde **Automática**.
- [ ] Pulsa **⚙ Filtros** → marca **Mostrar solo contenido adulto** → Buscar con `suits`:
      aparece solo «SpicySuits» con 🔞. Vuelve a **Ocultar contenido adulto**.
- [ ] Selecciona **MoreCompany** → **⬇ Instalar**. Aparece el aviso verde «instalado y activado».
- [ ] **Descargas**: la barra llega al 100 %.
- [ ] **Mods instalados**: desmarca la casilla «Activo» → el mod se quita del juego al instante;
      márcala otra vez → vuelve. Luego **🗑 Desinstalar limpio**.

## 4. Prueba real con un juego

> 💡 Empieza con un juego pequeño y que no te importe reinstalar (por ejemplo Lethal Company,
> Valheim o Stardew Valley). ModHub guarda una copia de cualquier archivo original que un mod
> sustituya, y «Purgar todo» deja el juego como estaba; aun así, lo prudente es probar primero
> en un juego poco delicado.

1. Doble clic en **`ejecutar.bat`**.
2. **Juegos → 🔍 Detectar juegos**. Deben aparecer tus juegos de Steam, Epic, Xbox/Game Pass y GOG.
   Si falta alguno: **📁 Añadir carpeta…** y elige la carpeta donde está su `.exe`.
3. **Buscador global**: elige el juego, busca un mod e instálalo.
4. Abre la carpeta del juego (**📂 Abrir carpeta**) y comprueba que los archivos del mod están
   en su sitio (p. ej. `BepInEx\plugins\Autor-Mod\`).
5. Lanza el juego y comprueba que el mod funciona.
6. Vuelve a ModHub → **Juegos → 🧹 Purgar todo**: la carpeta del juego debe quedar como antes.

## 5. Claves de las webs (Ajustes)

Thunderstore, Modrinth y GameBanana funcionan **sin clave**. Para el resto:

| Web | Clave | Coste |
|---|---|---|
| Nexus Mods | *Ajustes → Nexus → Conseguir…* (pestaña API de tu cuenta) | Gratis. Con cuenta gratuita las descargas pasan por el botón «Mod Manager Download» de la web |
| CurseForge | console.curseforge.com | Gratis (cuenta de desarrollador) |
| mod.io | mod.io/me/access | Gratis |
| Steam | steamcommunity.com/dev/apikey | Gratis; solo para buscar en el Workshop |

Pega la clave → **Guardar ajustes**. Con Nexus pulsa **Comprobar**: debe decir tu usuario y si eres Premium.
Las claves se guardan en el *Administrador de credenciales* de Windows, no en un archivo de texto.

## 6. Botón «Mod Manager Download» de Nexus (nxm://)

1. **Ajustes → Enlaces desde el navegador → nxm:// → Usar ModHub**.
   Si tenías Vortex o Mod Organizer 2, ModHub guarda su configuración; con **Dejar de usar** se la devuelve.
2. Añade el juego en ModHub (debe estar en la lista de Juegos).
3. En nexusmods.com abre un mod → pestaña *Files* → **Mod Manager Download**.
4. El navegador pregunta si quieres abrir ModHub → **Abrir**.
5. ModHub (aunque ya esté abierto) recibe el enlace, descarga e instala. Lo verás en
   **Descargas → Interceptaciones** (la clave secreta del enlace se oculta).

Prueba también la instancia única: con ModHub abierto, vuelve a hacer doble clic en `ejecutar.bat`.
No se abre una segunda ventana; la existente pasa al frente.

## 7. Descargas que pasan por la web (CurseForge restringido, Mod DB, AyakaMods)

Al pulsar Instalar, ModHub abre la página y espera. Descarga el archivo normalmente con el navegador:
cuando termina, ModHub lo detecta en tu carpeta **Descargas** y muestra **«¿Instalarlo en…?» → Instalar**.

## 8. Mods con instalador (FOMOD)

Muchos mods grandes de Skyrim o Fallout traen opciones (resolución, parches…). ModHub los marca como
**Revisar**: en *Mods instalados* pulsa **🔎 Revisar…**, elige las opciones paso a paso y confirma el resumen.

---

## Si algo falla

| Problema | Solución |
|---|---|
| «No se encuentra Python» | Reinstala Python marcando *Add python.exe to PATH* |
| El enlace nxm:// no abre ModHub | *Ajustes → nxm:// → Usar ModHub* otra vez (sobre todo si moviste la carpeta) |
| Un mod .rar no se abre | Instala 7-Zip o indica la ruta de `7z.exe` en Ajustes |
| Error con «symlink» | Normal sin el *Modo de desarrollador*: ModHub usa hardlink o copia automáticamente |
| El antivirus bloquea algo | ModHub crea enlaces en la carpeta del juego; añade una excepción si tu antivirus es muy estricto |
| Cualquier otra cosa | Mándame el archivo de registro: `%APPDATA%\ModHub\modhub.log` |

**Pruebas automáticas:** `probar.bat` ejecuta las 73 pruebas del proyecto (deben salir todas en verde).
