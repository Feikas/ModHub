"""Cliente HTTP compartido por todas las fuentes.

- Un solo `httpx.AsyncClient` (reutiliza conexiones, HTTP/2 si está disponible).
- Límite de peticiones por dominio (token bucket): ninguna web recibe más de lo pactado.
- Reintentos con backoff exponencial + jitter en errores de red, 429 y 5xx,
  respetando la cabecera Retry-After.
- User-Agent identificable, como piden Modrinth, Thunderstore y Nexus.
"""
from __future__ import annotations

import asyncio
import email.utils
import logging
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("modhub.http")

APP_NAME = "ModHub"
APP_VERSION = "0.8.0"
USER_AGENT = f"{APP_NAME}/{APP_VERSION} (Windows mod manager; contacto: https://github.com/tu-usuario/modhub)"


@dataclass(frozen=True, slots=True)
class RateLimit:
    per_second: float      # ritmo sostenido
    burst: int = 1         # ráfaga permitida


# Valores conservadores a propósito. Las webs de scraping van muy despacio.
DEFAULT_LIMITS: dict[str, RateLimit] = {
    "api.modrinth.com": RateLimit(5, burst=10),      # Modrinth permite 300/min
    "thunderstore.io": RateLimit(3, burst=5),
    "api.nexusmods.com": RateLimit(2, burst=5),      # Nexus: cupo horario/diario por usuario
    "api.curseforge.com": RateLimit(4, burst=5),
    "api.mod.io": RateLimit(3, burst=5),
    "gamebanana.com": RateLimit(2, burst=3),
    "api.steampowered.com": RateLimit(3, burst=5),
    "www.moddb.com": RateLimit(0.5),
    "rss.moddb.com": RateLimit(0.5),
    "ayakamods.com": RateLimit(1.0),
    # CDNs de imágenes: están hechos para servir mucho; aun así, con límite
    **{h: RateLimit(12, burst=12) for h in (
        "staticdelivery.nexusmods.com", "cdn.modrinth.com", "gcdn.thunderstore.io", "media.forgecdn.net",
        "thumb.modcdn.io", "images.gamebanana.com", "images.steamusercontent.com",
        "steamuserimages-a.akamaihd.net", "shared.fastly.steamstatic.com")},
}
FALLBACK_LIMIT = RateLimit(2, burst=2)
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


def _safe(url: str) -> str:
    """URL sin claves (para mensajes y registros)."""
    import re
    return re.sub(r"((?:api_)?key=)[^&]+", r"\1***", url)


class HttpError(Exception):
    def __init__(self, message: str, status: int | None = None, url: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url


class _TokenBucket:
    def __init__(self, limit: RateLimit):
        self.rate = limit.per_second
        self.capacity = float(limit.burst)
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)

    def penalize(self, seconds: float) -> None:
        """Tras un 429: vacía el cubo para que todo el dominio espere."""
        self.tokens = -seconds * self.rate


class HttpClient:
    def __init__(self, *, timeout: float = 30.0, max_retries: int = 4, backoff_base: float = 1.0,
                 backoff_max: float = 60.0, limits: dict[str, RateLimit] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self._limits = {**DEFAULT_LIMITS, **(limits or {})}
        self._buckets: dict[str, _TokenBucket] = {}
        try:
            import h2  # noqa: F401
            http2 = True
        except ImportError:
            http2 = False
        self.client = httpx.AsyncClient(
            http2=http2, timeout=httpx.Timeout(timeout, connect=10.0), follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            transport=transport,
        )

    # --- Contexto async: `async with HttpClient() as http:` ---------------
    async def __aenter__(self) -> "HttpClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    def _bucket(self, url: str) -> _TokenBucket:
        host = (urlsplit(url).hostname or "").lower()
        if host not in self._buckets:
            self._buckets[host] = _TokenBucket(self._limits.get(host, FALLBACK_LIMIT))
        return self._buckets[host]

    def _backoff(self, attempt: int) -> float:
        delay = min(self.backoff_max, self.backoff_base * (2 ** attempt))
        return delay * (0.5 + random.random() / 2)        # "equal jitter"

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float | None:
        value = resp.headers.get("Retry-After")
        if not value:
            return None
        if value.strip().isdigit():
            return float(value)
        try:  # formato fecha HTTP
            return max(0.0, email.utils.parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError):
            return None

    async def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        bucket = self._bucket(url)
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            await bucket.acquire()
            try:
                resp = await self.client.request(method, url, **kw)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_exc = e
                if attempt == self.max_retries:
                    break
                wait = self._backoff(attempt)
                log.warning("%s %s falló (%s); reintento en %.1fs", method, _safe(url), e, wait)
                await asyncio.sleep(wait)
                continue

            if resp.status_code in RETRY_STATUS and attempt < self.max_retries:
                wait = self._retry_after(resp) or self._backoff(attempt)
                wait = min(wait, self.backoff_max)
                if resp.status_code == 429:
                    bucket.penalize(wait)
                log.warning("%s %s -> %s; reintento en %.1fs", method, _safe(url), resp.status_code, wait)
                await asyncio.sleep(wait)
                continue

            if resp.is_error:
                if resp.status_code in (403, 503) and "cloudflare" in (resp.headers.get("server", "") +
                                                                     resp.text[:2000]).lower():
                    raise HttpError(f"{urlsplit(url).hostname} bloquea las búsquedas automáticas (Cloudflare). "
                                    "Ábrelo en el navegador.", resp.status_code, url)
                detail = resp.text[:300].strip()
                raise HttpError(f"HTTP {resp.status_code} en {_safe(url)}: {detail}", resp.status_code, url)
            return resp

        raise HttpError(f"Sin respuesta de {_safe(url)} tras {self.max_retries + 1} intentos: {last_exc}", None, url)

    @asynccontextmanager
    async def stream(self, method: str, url: str, **kw: Any) -> AsyncIterator[httpx.Response]:
        """Respuesta en streaming para descargas grandes. Respeta el límite del dominio pero NO
        reintenta: el gestor de descargas reanuda por su cuenta desde el último byte recibido."""
        await self._bucket(url).acquire()
        async with self.client.stream(method, url, **kw) as resp:
            yield resp

    # --- Atajos ---------------------------------------------------------
    async def get_json(self, url: str, **kw: Any) -> Any:
        return (await self.request("GET", url, **kw)).json()

    async def post_json(self, url: str, **kw: Any) -> Any:
        return (await self.request("POST", url, **kw)).json()

    async def get_text(self, url: str, **kw: Any) -> str:
        headers = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                   **kw.pop("headers", {})}
        return (await self.request("GET", url, headers=headers, **kw)).text

    async def aclose(self) -> None:
        await self.client.aclose()
