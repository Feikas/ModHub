"""Gestor de descargas asíncrono.

- Varias descargas a la vez (límite configurable).
- Pausa / reanudación: lo descargado se guarda en "<archivo>.part" y se continúa con
  la cabecera HTTP Range. Si el servidor no admite Range, se empieza de cero.
- Reintentos automáticos ante cortes de red (reanudando, no desde cero).
- Progreso: callback con bytes descargados, total, velocidad y tiempo restante.
- Integridad: verifica MD5 / SHA-1 / SHA-256 / SHA-512 si la fuente los da.

    manager = DownloadManager(http, on_progress=lambda t: print(t.progress))
    task = manager.add_ticket(ticket, staging.downloads_dir(slug, game_path))
    await manager.wait(task.id)
"""
from __future__ import annotations

import asyncio
import hashlib
import itertools
import logging
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlsplit

import httpx

from ..sources.base import DownloadPolicy, DownloadTicket
from ..sources.http import HttpClient
from .staging import safe_folder_name

log = logging.getLogger("modhub.downloads")

SUPPORTED_HASHES = ("sha512", "sha256", "sha1", "md5")
_RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class DownloadState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL = {DownloadState.DONE, DownloadState.FAILED, DownloadState.CANCELLED}


class DownloadError(Exception):
    pass


class IntegrityError(DownloadError):
    """El archivo descargado no coincide con el hash o el tamaño esperados."""


class _Retryable(Exception):
    pass


@dataclass
class DownloadTask:
    id: int
    url: str
    dest: Path
    expected_size: int | None = None
    hashes: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    label: str = ""
    state: DownloadState = DownloadState.QUEUED
    done_bytes: int = 0
    total_bytes: int | None = None
    speed: float = 0.0                    # bytes/s (media suavizada)
    error: str | None = None
    attempts: int = 0
    # internos
    _runner: asyncio.Task | None = field(default=None, repr=False)
    _intent: str | None = field(default=None, repr=False)          # "pause" | "cancel"
    _finished: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    @property
    def part_path(self) -> Path:
        return self.dest.with_name(self.dest.name + ".part")

    @property
    def progress(self) -> float | None:
        """0.0–1.0, o None si no se conoce el tamaño."""
        return min(1.0, self.done_bytes / self.total_bytes) if self.total_bytes else None

    @property
    def eta_seconds(self) -> float | None:
        if not self.total_bytes or self.speed <= 0:
            return None
        return max(0.0, (self.total_bytes - self.done_bytes) / self.speed)


def _filename_from(url: str, fallback: str = "descarga") -> str:
    name = unquote(Path(urlsplit(url).path).name)
    return safe_folder_name(name or fallback, max_len=150)


def _parse_total(resp: httpx.Response, offset: int) -> int | None:
    cr = resp.headers.get("Content-Range")                 # "bytes 100-999/1000"
    if cr and (m := re.search(r"/(\d+)\s*$", cr)):
        return int(m.group(1))
    cl = resp.headers.get("Content-Length")
    if cl and cl.isdigit():
        return int(cl) + (offset if resp.status_code == 206 else 0)
    return None


class DownloadManager:
    def __init__(self, http: HttpClient, *, max_concurrent: int = 3, chunk_size: int = 1 << 20,
                 max_retries: int = 5, retry_delay: float = 2.0, progress_interval: float = 0.25,
                 on_progress: Callable[[DownloadTask], None] | None = None):
        self.http = http
        self.retry_delay = retry_delay
        self.chunk_size = chunk_size
        self.max_retries = max_retries
        self.progress_interval = progress_interval
        self.on_progress = on_progress
        self._sem = asyncio.Semaphore(max_concurrent)
        self._tasks: dict[int, DownloadTask] = {}
        self._ids = itertools.count(1)

    # ── API pública ────────────────────────────────────────────────────
    def add(self, url: str, dest: Path | str, *, size: int | None = None, hashes: dict[str, str] | None = None,
            headers: dict[str, str] | None = None, label: str = "") -> DownloadTask:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        clean_hashes = {k.lower(): v.lower() for k, v in (hashes or {}).items()
                        if v and k.lower() in SUPPORTED_HASHES}
        task = DownloadTask(id=next(self._ids), url=url, dest=dest, expected_size=size, hashes=clean_hashes,
                            headers=dict(headers or {}), label=label or dest.name)
        self._tasks[task.id] = task
        self._start(task)
        return task

    def add_ticket(self, ticket: DownloadTicket, dest_dir: Path | str, *, label: str = "") -> DownloadTask:
        """Encola un DownloadTicket AUTOMATIC de la Fase 2."""
        if ticket.policy is not DownloadPolicy.AUTOMATIC or not ticket.url:
            raise ValueError(f"Este ticket no se puede descargar automáticamente ({ticket.policy.value}).")
        name = safe_folder_name(ticket.filename, max_len=150) if ticket.filename else _filename_from(ticket.url)
        return self.add(ticket.url, Path(dest_dir) / name, size=ticket.size, hashes=ticket.hashes,
                        headers=ticket.headers, label=label)

    def get(self, task_id: int) -> DownloadTask:
        return self._tasks[task_id]

    @property
    def tasks(self) -> list[DownloadTask]:
        return list(self._tasks.values())

    async def pause(self, task_id: int) -> None:
        task = self._tasks[task_id]
        if task.state in (DownloadState.RUNNING, DownloadState.QUEUED) and task._runner:
            task._intent = "pause"
            task._runner.cancel()
            await asyncio.gather(task._runner, return_exceptions=True)

    def resume(self, task_id: int) -> None:
        task = self._tasks[task_id]
        if task.state in (DownloadState.PAUSED, DownloadState.FAILED):
            task.error = None
            task.attempts = 0
            task._finished = asyncio.Event()
            self._start(task)

    async def cancel(self, task_id: int) -> None:
        task = self._tasks[task_id]
        if task.state in TERMINAL:
            return
        task._intent = "cancel"
        if task._runner and not task._runner.done():
            task._runner.cancel()
            await asyncio.gather(task._runner, return_exceptions=True)
        else:   # estaba pausada
            self._mark_cancelled(task)

    async def wait(self, task_id: int) -> DownloadTask:
        """Espera a que termine (bien, mal o cancelada). Una pausa NO cuenta como fin."""
        task = self._tasks[task_id]
        await task._finished.wait()
        return task

    async def wait_all(self) -> list[DownloadTask]:
        return [await self.wait(t) for t in list(self._tasks)]

    async def close(self) -> None:
        for t in self._tasks.values():
            if t._runner and not t._runner.done():
                t._intent = "pause"
                t._runner.cancel()
        await asyncio.gather(*(t._runner for t in self._tasks.values() if t._runner), return_exceptions=True)

    # ── Funcionamiento interno ───────────────────────────────────────────
    def _emit(self, task: DownloadTask) -> None:
        if self.on_progress:
            try:
                self.on_progress(task)
            except Exception:  # noqa: BLE001 - un fallo en la UI no debe romper la descarga
                log.exception("Error en on_progress")

    def _start(self, task: DownloadTask) -> None:
        task.state = DownloadState.QUEUED
        task._intent = None
        task._runner = asyncio.create_task(self._guarded(task), name=f"descarga-{task.id}")

    def _mark_cancelled(self, task: DownloadTask) -> None:
        task.part_path.unlink(missing_ok=True)
        task.state = DownloadState.CANCELLED
        task._finished.set()
        self._emit(task)

    async def _guarded(self, task: DownloadTask) -> None:
        try:
            async with self._sem:
                await self._run(task)
        except asyncio.CancelledError:
            if task._intent == "pause":
                task.state = DownloadState.PAUSED
                task.speed = 0.0
                self._emit(task)
            elif task._intent == "cancel":
                self._mark_cancelled(task)
            else:
                raise

    async def _run(self, task: DownloadTask) -> None:
        task.state = DownloadState.RUNNING
        self._emit(task)
        while True:
            try:
                await self._transfer(task)
                await self._finish(task)
                task.state = DownloadState.DONE
                break
            except (httpx.TransportError, httpx.TimeoutException, _Retryable) as e:
                task.attempts += 1
                if task.attempts > self.max_retries:
                    task.state, task.error = DownloadState.FAILED, f"Sin conexión tras varios intentos: {e}"
                    break
                wait = min(30.0, self.retry_delay * 2 ** (task.attempts - 1))
                log.warning("Descarga %s interrumpida (%s); continúo en %.0fs", task.label, e, wait)
                await asyncio.sleep(wait)
            except IntegrityError as e:
                task.state, task.error = DownloadState.FAILED, str(e)
                break
            except (DownloadError, OSError) as e:
                task.state, task.error = DownloadState.FAILED, str(e)
                break
        task.speed = 0.0
        task._finished.set()
        self._emit(task)

    async def _transfer(self, task: DownloadTask) -> None:
        part = task.part_path
        offset = part.stat().st_size if part.exists() else 0
        if task.expected_size is not None and offset > task.expected_size:
            part.unlink()
            offset = 0
        if task.expected_size is not None and offset == task.expected_size and offset > 0:
            task.done_bytes = task.total_bytes = offset         # ya estaba entero
            return
        headers = dict(task.headers)
        if offset:
            headers["Range"] = f"bytes={offset}-"

        async with self.http.stream("GET", task.url, headers=headers) as resp:
            if resp.status_code == 416 and offset:            # rango imposible: empezamos de cero
                part.unlink(missing_ok=True)
                raise _Retryable("el servidor rechazó la reanudación")
            if resp.status_code in _RETRY_STATUS:
                raise _Retryable(f"HTTP {resp.status_code}")
            if resp.status_code >= 400:
                raise DownloadError(f"El servidor respondió HTTP {resp.status_code} para {task.label}.")
            if offset and resp.status_code != 206:             # no admite Range
                offset = 0
            task.total_bytes = _parse_total(resp, offset) or task.expected_size
            task.done_bytes = offset

            last_emit, window_bytes, window_start = 0.0, 0, time.monotonic()
            with open(part, "ab" if offset else "wb") as fh:
                async for chunk in resp.aiter_bytes(self.chunk_size):
                    await asyncio.to_thread(fh.write, chunk)
                    task.done_bytes += len(chunk)
                    window_bytes += len(chunk)
                    now = time.monotonic()
                    if now - window_start >= 0.5:
                        inst = window_bytes / (now - window_start)
                        task.speed = inst if task.speed == 0 else task.speed * 0.7 + inst * 0.3
                        window_bytes, window_start = 0, now
                    if now - last_emit >= self.progress_interval:
                        last_emit = now
                        self._emit(task)
        task.attempts = 0                                       # el tramo terminó bien

    async def _finish(self, task: DownloadTask) -> None:
        part = task.part_path
        size = part.stat().st_size
        if task.expected_size is not None and size != task.expected_size:
            part.unlink(missing_ok=True)
            raise IntegrityError(f"Tamaño incorrecto en {task.label}: {size} bytes en vez de {task.expected_size}.")
        if task.hashes:
            digests = await asyncio.to_thread(self._hash_file, part, list(task.hashes))
            for algo, expected in task.hashes.items():
                if digests[algo] != expected:
                    part.unlink(missing_ok=True)
                    raise IntegrityError(f"{task.label} está dañado ({algo} no coincide). Reintenta la descarga.")
        os.replace(part, task.dest)
        task.done_bytes = task.total_bytes = size

    @staticmethod
    def _hash_file(path: Path, algos: list[str]) -> dict[str, str]:
        hashers = {a: hashlib.new(a) for a in algos}
        with open(path, "rb") as fh:
            while chunk := fh.read(1 << 20):
                for h in hashers.values():
                    h.update(chunk)
        return {a: h.hexdigest() for a, h in hashers.items()}
