# Cambios

## 0.7.0 — Nexus con cuenta gratuita, como Vortex

- Al instalar un mod de Nexus sin Premium, ModHub abre la **página de descarga de ese archivo** (no la ficha):
  pulsas «Slow download» y ModHub recibe el enlace, descarga, instala y activa solo.
- **Cola de páginas:** si pides varios mods (o sus dependencias), se abren de una en una; al llegar el enlace de
  uno se abre el siguiente. Avisos con «Volver a abrir», «Saltar» y «Cancelar todos»; en la barra de estado se ve
  qué mod espera tu clic y cuántos quedan.
- El enlace se reconoce aunque lo pulses en otra pestaña, y va **al juego desde el que lo pediste** sin preguntar.
- Si `nxm://` no está activado para ModHub (lo tiene Vortex/MO2), avisa de cómo activarlo en Ajustes → Enlaces.
- Las dependencias de Nexus que requieren la web ya no se pierden: pasan a la cola.

Pruebas: 144, todas pasan.

## 0.6.0 — versión del juego: Steam, Xbox / PC Game Pass, Epic, GOG

- **Cada juego sabe qué versión es** (la tienda donde se detectó; un juego añadido a mano dentro de `XboxGames`
  o con binarios `WinGDK` se reconoce como Game Pass).
- **Los mods y sus archivos se clasifican** leyendo nombre, archivo y descripción («Game Pass version»,
  «Steam only», «Not compatible with Xbox»…), con negaciones («no funciona en…», «untested») y falsos positivos
  (Steam Workshop/Deck, mandos de Xbox, «epic armor», steampunk…).
- **Filtro «Mi versión»** (Automática = la de tu juego): oculta lo que el autor marca solo para otra versión.
  En los resultados se ve la etiqueta `[Xbox / Game Pass]`, `[Steam]`…
- **Al instalar se preselecciona el archivo de tu versión**; los de otra versión salen con ⚠.
- **Rutas Game Pass:** lo que va a `Binaries/Win64` se despliega en `Binaries/WinGDK` cuando el juego usa GDK
  (se decide mirando la carpeta real, también si un gestor antiguo dejó un `Win64` vacío). UE4SS y otros loaders
  se detectan en `WinGDK`.
- **Loaders que no existen en Game Pass** (SKSE64, F4SE) se marcan con ⛔ y ModHub avisa en vez de ofrecerlos.
- `docs/plataformas_investigacion.json`: investigación de ChatGPT (16 juegos, palabras clave, falsos positivos,
  fuentes). Sus tres correcciones al plan están aplicadas: «sin etiqueta» = desconocido (no se oculta), solo se
  cambia el archivo por defecto con motivo claro, y la ruta se decide con los archivos reales.

Pruebas: 139, todas pasan.

## 0.5.0 — trabajo conjunto Claude + ChatGPT

**De la revisión de ChatGPT** (integrado tal cual):
- Instalados: buscador por nombre/versión/fuente, filtro por estado y contador. No se puede actuar sobre filas ocultas.
- Descargas reanudadas más seguras: se comprueba `Content-Range`, se pide sin compresión y se detectan transferencias cortas.
- No se permiten dos descargas al mismo archivo; un error inesperado ya no bloquea la cola.
- La extracción rechaza nombres que Windows no admite (`CON`, `AUX.txt`, `COM1`, acabados en punto…).
- Los hardlinks usan `os.link`, así un fallo pasa limpiamente a symlink o copia.
- Mods desplegados por copia: se comparan contenidos para detectar actualizaciones con igual tamaño y fecha.

**Añadido por Claude:**
- Descargas largas con conexión inestable: el cupo de reintentos se renueva cada vez que se avanza más que nunca
  (antes fallaban tras unos pocos cortes aunque cada intento avanzara); una conexión que no avanza se rinde igual.
- Al abrir ModHub, las descargas que se quedaron a medias al cerrar se ofrecen con **Continuar** (sigue desde el
  `.part`) o **Descartar**.
- Archivos desplegados por copia que **editas tú** (p. ej. un `.ini`): al desactivar el mod ya no se borran tus cambios
  (fallo que detectó ChatGPT; editar en el sitio conserva el inodo y antes se daba por «nuestro»).
- Limpieza: fuera el import de `win32file` que quedó sin uso; finales de línea normalizados.

Pruebas: 119, todas pasan.

## 0.4.0
Primera versión pública.
