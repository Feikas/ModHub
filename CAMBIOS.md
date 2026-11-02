# Cambios

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
