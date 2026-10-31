"""Carga de imágenes de las webs (miniaturas y galerías) con caché en memoria y en disco."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QPixmap

log = logging.getLogger("modhub.images")

MAX_BYTES = 8 * 1024 * 1024          # una imagen de más de 8 MB no se descarga


class ImageLoader:
    def __init__(self, http, cache_dir: Path | str, *, memory_items: int = 400, concurrency: int = 6):
        self.http = http
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._mem: OrderedDict[str, QPixmap] = OrderedDict()
        self._mem_items = memory_items
        self._failed: set[str] = set()
        self._inflight: dict[str, asyncio.Future] = {}
        self._sem = asyncio.Semaphore(concurrency)

    def _path(self, url: str) -> Path:
        return self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".img")

    def cached(self, url: str | None) -> QPixmap | None:
        if not url:
            return None
        pix = self._mem.get(url)
        if pix is not None:
            self._mem.move_to_end(url)
        return pix

    def _remember(self, url: str, pix: QPixmap) -> QPixmap:
        self._mem[url] = pix
        while len(self._mem) > self._mem_items:
            self._mem.popitem(last=False)
        return pix

    @staticmethod
    def _decode(data: bytes) -> QPixmap | None:
        pix = QPixmap()
        return pix if pix.loadFromData(QByteArray(data)) and not pix.isNull() else None

    async def load(self, url: str | None) -> QPixmap | None:
        """Devuelve la imagen o None si no se pudo (nunca lanza excepciones)."""
        if not url or not url.startswith(("http://", "https://")) or url in self._failed:
            return None
        if (pix := self.cached(url)) is not None:
            return pix
        disk = self._path(url)
        if disk.is_file():
            if (pix := self._decode(disk.read_bytes())) is not None:
                return self._remember(url, pix)
        if url in self._inflight:                      # otra petición ya la está bajando
            return await asyncio.shield(self._inflight[url])
        fut = asyncio.get_running_loop().create_future()
        self._inflight[url] = fut
        pix = None
        try:
            async with self._sem:
                resp = await self.http.request("GET", url, headers={"Accept": "image/*"}, timeout=20)
            data = resp.content
            if len(data) <= MAX_BYTES and (pix := self._decode(data)) is not None:
                try:
                    disk.write_bytes(data)
                except OSError:
                    pass
                self._remember(url, pix)
            else:
                self._failed.add(url)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - una imagen rota no debe molestar
            log.debug("Imagen no disponible %s: %s", url, e)
            self._failed.add(url)
        finally:
            self._inflight.pop(url, None)
            if not fut.done():
                fut.set_result(pix)
        return pix
