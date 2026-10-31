"""Nexus Mods — API REST v1 (https://app.swaggerhub.com/apis-docs/NexusMods/nexus-mods_public_api_params_in_form_data/1.0).

Autenticación (clase NexusAuth):
  • API key personal: se copia de nexusmods.com/users/myaccount?tab=api. Válida para uso propio.
  • SSO: el usuario pulsa "Iniciar sesión" y autoriza en la web; Nexus devuelve la API key por
    WebSocket. Requiere que Nexus haya aprobado la app y le haya dado un `application slug`.
    Es obligatorio si algún día se distribuye ModHub públicamente.
  La key se guarda en el Administrador de credenciales de Windows (keyring), nunca en texto plano.

Descargas:
  • Premium -> download_link.json devuelve el enlace del CDN (AUTOMATIC).
  • Gratis  -> solo con la `key`+`expires` del enlace nxm:// que genera el botón
    "Mod Manager Download" de la web (BROWSER_INTERCEPT por protocolo).

Búsqueda: la v1 no tiene buscador. Se usa la API GraphQL v2 y, si falla, se filtran
las listas de "tendencias" y "últimos actualizados" de la v1.

ID de juego (GameRef.ids["nexus"]) = dominio de la web: "skyrimspecialedition", "fallout4", "stardewvalley"…
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
import webbrowser
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from .base import (AdultFilter, AdultSupport, AuthRequired, DownloadPolicy, SearchFilterOptions, SortOrder,
                   best_name_match, strip_edition, GameRef, InterceptMethod, ModFile, ModInfo,
                   ModNotFound, ProtocolRequest, SourceAdapter)
from .http import APP_NAME, APP_VERSION, HttpError

log = logging.getLogger("modhub.nexus")

API_V1 = "https://api.nexusmods.com/v1"
API_V2 = "https://api.nexusmods.com/v2/graphql"
SSO_WS = "wss://sso.nexusmods.com"
SITE = "https://www.nexusmods.com"
KEYRING_SERVICE = "ModHub"
KEYRING_USER = "nexusmods_api_key"

# Categorías de archivo que no se ofrecen para instalar
_HIDDEN_CATEGORIES = {"ARCHIVED", "REMOVED", "DELETED"}
# Orden en GraphQL v2 (campos comprobados en el esquema real: relevance, downloads, endorsements, createdAt…)
_GQL_SORT = {SortOrder.RELEVANCE: {"relevance": {"direction": "DESC"}},
             SortOrder.TRENDING: {"downloads": {"direction": "DESC"}},
             SortOrder.DOWNLOADS: {"downloads": {"direction": "DESC"}},
             SortOrder.NEWEST: {"createdAt": {"direction": "DESC"}},
             SortOrder.UPDATED: {"updatedAt": {"direction": "DESC"}}}
TRENDING_DAYS = 90
_GQL_GAMES = "query($f: GamesSearchFilter) { games(filter: $f, count: 100) { nodes { name domainName } } }"

# (verificar) El esquema GraphQL v2 evoluciona; comprobar en su explorador si cambia.
_GQL_SEARCH = """
query ModSearch($filter: ModsFilter, $sort: [ModsSort!], $count: Int, $offset: Int) {
  mods(filter: $filter, sort: $sort, count: $count, offset: $offset) {
    nodes { modId name summary author pictureUrl thumbnailUrl downloads updatedAt createdAt adultContent
            modCategory { name } game { domainName } }
  }
}
"""


# ════════════════════════════════════════════════════════════════════════
#  Autenticación
# ════════════════════════════════════════════════════════════════════════
@dataclass(slots=True)
class NexusUser:
    user_id: int
    name: str
    is_premium: bool
    is_supporter: bool


class NexusAuth:
    """Guarda, recupera y obtiene (SSO) la API key de Nexus."""

    def __init__(self, api_key: str | None = None, *, use_keyring: bool = True):
        self._api_key = api_key
        self.use_keyring = use_keyring

    # --- Almacenamiento seguro ------------------------------------------
    def _keyring(self):
        if not self.use_keyring:
            return None
        try:
            import keyring
            return keyring
        except ImportError:
            return None

    @property
    def api_key(self) -> str | None:
        if self._api_key is None and (kr := self._keyring()):
            try:
                self._api_key = kr.get_password(KEYRING_SERVICE, KEYRING_USER)
            except Exception as e:  # noqa: BLE001 - keyring sin backend disponible
                log.debug("keyring no disponible: %s", e)
        return self._api_key

    def save(self, api_key: str) -> None:
        self._api_key = api_key.strip()
        if kr := self._keyring():
            kr.set_password(KEYRING_SERVICE, KEYRING_USER, self._api_key)

    def clear(self) -> None:
        self._api_key = None
        if kr := self._keyring():
            try:
                kr.delete_password(KEYRING_SERVICE, KEYRING_USER)
            except Exception:  # noqa: BLE001 - no había nada guardado
                pass

    # --- SSO por WebSocket ------------------------------------------------
    async def login_sso(self, app_slug: str, *, open_browser: Callable[[str], Any] = webbrowser.open,
                        timeout: float = 300.0) -> str:
        """Abre la web de Nexus para que el usuario autorice ModHub y devuelve la API key.

        Necesita: `pip install websockets` y un `app_slug` aprobado por Nexus.
        """
        try:
            import websockets
        except ImportError as e:
            raise AuthRequired("Falta la librería 'websockets' para el inicio de sesión SSO.") from e

        session_id = str(uuid.uuid4())
        connection_token: str | None = None

        async def flow() -> str:
            nonlocal connection_token
            async with websockets.connect(SSO_WS) as ws:
                await ws.send(json.dumps({"id": session_id, "token": connection_token, "protocol": 2}))
                open_browser(f"{SITE}/sso?id={session_id}&application={app_slug}")
                async for raw in ws:
                    msg = json.loads(raw)
                    if not msg.get("success"):
                        raise AuthRequired(f"Nexus SSO rechazó la conexión: {msg.get('error')}")
                    data = msg.get("data") or {}
                    if "connection_token" in data:          # primer mensaje: sesión creada
                        connection_token = data["connection_token"]
                    elif "api_key" in data:                 # el usuario autorizó en la web
                        return data["api_key"]
            raise AuthRequired("La conexión SSO con Nexus se cerró sin recibir la clave.")

        try:
            key = await asyncio.wait_for(flow(), timeout)
        except asyncio.TimeoutError as e:
            raise AuthRequired("Tiempo agotado esperando la autorización en Nexus.") from e
        self.save(key)
        return key


# ════════════════════════════════════════════════════════════════════════
#  Adaptador
# ════════════════════════════════════════════════════════════════════════
class NexusAdapter(SourceAdapter):
    key = "nexus"
    display_name = "Nexus Mods"
    requires_auth = True
    search_needs_auth = False                 # buscar/explorar es público; la clave hace falta para instalar
    protocol_schemes = ("nxm://",)
    adult_support = AdultSupport.FLAGGED      # contains_adult_content / adultContent

    def __init__(self, http, settings=None, *, auth: NexusAuth | None = None):
        super().__init__(http, settings)
        self.auth = auth or NexusAuth(self.settings.get("api_key"),
                                      use_keyring=self.settings.get("use_keyring", True))
        self._user: NexusUser | None = None
        self.quota: dict[str, int] = {}        # cupo restante que informa Nexus en cada respuesta
        self._games: list[dict] | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.auth.api_key)

    # --- Peticiones autenticadas -----------------------------------------
    def _headers(self) -> dict[str, str]:
        key = self.auth.api_key
        if not key:
            raise AuthRequired("Nexus Mods: inicia sesión o pega tu API key en Ajustes.")
        return {"apikey": key, "Application-Name": APP_NAME, "Application-Version": APP_VERSION}

    async def _get(self, path: str, **params) -> Any:
        try:
            resp = await self.http.request("GET", f"{API_V1}{path}", headers=self._headers(),
                                           params=params or None)
        except HttpError as e:
            if e.status == 401:
                self._user = None
                raise AuthRequired("Nexus Mods: la API key no es válida o ha caducado.") from e
            if e.status == 404:
                raise ModNotFound(f"Nexus Mods: no encontrado ({path}).") from e
            raise
        for h in ("x-rl-hourly-remaining", "x-rl-daily-remaining"):
            if h in resp.headers:
                self.quota[h.removeprefix("x-rl-").removesuffix("-remaining")] = int(resp.headers[h])
        return resp.json()

    async def validate(self, *, force: bool = False) -> NexusUser:
        """Comprueba la key y devuelve el usuario (con su estado premium)."""
        if self._user is None or force:
            d = await self._get("/users/validate.json")
            self._user = NexusUser(user_id=d["user_id"], name=d["name"],
                                   is_premium=bool(d.get("is_premium")), is_supporter=bool(d.get("is_supporter")))
        return self._user

    # --- Búsqueda ---------------------------------------------------------
    async def search_games(self, text):
        data = await self.http.post_json(API_V2, json={"query": _GQL_GAMES, "variables": {
            "f": {"name": [{"value": text.strip(), "op": "WILDCARD"}]}}})
        return [(g["name"], g["domainName"]) for g in ((data.get("data") or {}).get("games") or {}).get("nodes", [])]

    async def find_game_id(self, game_name):
        """Busca el juego en Nexus por GraphQL (sin clave). Prueba con el nombre y con su primera palabra."""
        words = [w for w in re.split(r"[^\w']+", game_name) if w]
        cands: list[tuple[str, str]] = []
        for term in dict.fromkeys([game_name.strip(), strip_edition(game_name), words[0] if words else ""]):
            if not term:
                continue
            data = await self.http.post_json(API_V2, json={"query": _GQL_GAMES, "variables": {
                "f": {"name": [{"value": term, "op": "WILDCARD"}]}}})
            cands += [(g["name"], g["domainName"]) for g in
                      ((data.get("data") or {}).get("games") or {}).get("nodes", [])]
            if (hit := best_name_match(game_name, cands)):
                return hit
        return None

    async def search(self, query, game_ref=None, *, page=1, page_size=20, filters=None):
        """Búsqueda y exploración por la API GraphQL v2 (pública: no necesita clave).
        Filtra en la propia web por juego, texto, contenido adulto y fecha, y ordena allí."""
        game = game_ref.require(self.key) if game_ref else None
        order = (filters or SearchFilterOptions()).effective_sort(query)
        try:
            return await self._search_graphql(query, game, page, page_size, filters, order)
        except Exception as e:  # noqa: BLE001
            log.info("Búsqueda GraphQL de Nexus falló (%s); uso las listas de la v1.", e)
            if not game or not self.is_configured:
                raise
            mods = await self._browse_v1(game, order)
            q = query.lower()
            hits = [m for m in mods if q in m.name.lower()]
            start = (page - 1) * page_size
            return hits[start:start + page_size]

    def install_policy(self) -> DownloadPolicy:
        """Premium = descarga directa; gratis = pasar por el botón de la web."""
        premium = self._user is not None and self._user.is_premium
        return DownloadPolicy.AUTOMATIC if premium else DownloadPolicy.BROWSER_INTERCEPT

    async def _search_graphql(self, query, game, page, page_size, filters=None,
                              order: SortOrder = SortOrder.RELEVANCE) -> list[ModInfo]:
        filters = filters or SearchFilterOptions()
        flt: dict[str, Any] = {}
        if query.strip():
            flt["nameStemmed"] = [{"value": query.strip(), "op": "MATCHES"}]
        if game:
            flt["gameDomainName"] = [{"value": game, "op": "EQUALS"}]
        # Contenido adulto filtrado en la propia web (equivale al panel «Content options»)
        if filters.adult is AdultFilter.HIDE:
            flt["adultContent"] = [{"value": False, "op": "EQUALS"}]
        elif filters.adult is AdultFilter.ONLY:
            flt["adultContent"] = [{"value": True, "op": "EQUALS"}]
        now = int(time.time())
        if filters.updated_within_days:
            flt["updatedAt"] = [{"value": str(now - filters.updated_within_days * 86400), "op": "GTE"}]
        if order is SortOrder.TRENDING:
            # «Populares ahora» = lo más descargado de lo publicado en los últimos 90 días
            flt["createdAt"] = [{"value": str(now - TRENDING_DAYS * 86400), "op": "GTE"}]
        variables: dict[str, Any] = {"filter": flt, "count": page_size, "offset": (page - 1) * page_size,
                                     "sort": [_GQL_SORT[order]]}
        data = await self.http.post_json(API_V2, json={"query": _GQL_SEARCH, "variables": variables})
        if data.get("errors"):
            raise RuntimeError(data["errors"][0].get("message", "error GraphQL")[:200])
        nodes = ((data.get("data") or {}).get("mods") or {}).get("nodes", [])
        return [ModInfo(
            source=self.key, mod_id=str(n["modId"]), name=n["name"], author=n.get("author") or "",
            summary=n.get("summary") or "", game_id=n["game"]["domainName"],
            page_url=f"{SITE}/{n['game']['domainName']}/mods/{n['modId']}",
            thumbnail=n.get("thumbnailUrl") or n.get("pictureUrl"),
            images=[n["pictureUrl"]] if n.get("pictureUrl") else [],
            downloads=n.get("downloads"), updated_at=n.get("updatedAt"), created_at=n.get("createdAt"),
            is_adult=n.get("adultContent"),
            raw_categories=[(n.get("modCategory") or {}).get("name", "")], install_support=self.install_policy())
            for n in nodes]

    async def _browse_v1(self, game, order: SortOrder) -> list[ModInfo]:
        """Plan B si GraphQL falla: listas de la v1 (unos 10 mods cada una, necesitan clave)."""
        names = {SortOrder.NEWEST: ["latest_added", "latest_updated", "trending"],
                 SortOrder.UPDATED: ["latest_updated", "latest_added", "trending"]}.get(
                     order, ["trending", "latest_updated", "latest_added"])
        lists = await asyncio.gather(*(self._get(f"/games/{game}/mods/{n}.json") for n in names),
                                     return_exceptions=True)
        seen, out = set(), []
        for lst in lists:
            if isinstance(lst, BaseException):
                continue
            for m in lst:
                if m.get("mod_id") in seen or not m.get("available", True):
                    continue
                seen.add(m["mod_id"])
                out.append(self._v1_to_info(m, game))
        return out

    def _v1_to_info(self, m: dict, game: str) -> ModInfo:
        return ModInfo(
            source=self.key, mod_id=str(m["mod_id"]), name=m.get("name") or f"Mod {m['mod_id']}",
            author=m.get("author") or "", summary=m.get("summary") or "", description=m.get("description") or "",
            page_url=f"{SITE}/{game}/mods/{m['mod_id']}", thumbnail=m.get("picture_url"),
            images=[m["picture_url"]] if m.get("picture_url") else [],
            downloads=m.get("mod_downloads"), updated_at=m.get("updated_time"),
            created_at=m.get("created_time"), game_id=game,
            is_adult=bool(m.get("contains_adult_content", False)), install_support=self.install_policy(),
            extra={"version": m.get("version"), "category_id": m.get("category_id")})

    # --- Ficha completa -----------------------------------------------------
    async def _fetch_mod_details(self, mod_id, game_ref):
        if not game_ref:
            raise ModNotFound("Nexus necesita el juego (dominio) para consultar un mod.")
        game = game_ref.require(self.key)
        mod, files = await asyncio.gather(self._get(f"/games/{game}/mods/{mod_id}.json"),
                                          self._get(f"/games/{game}/mods/{mod_id}/files.json"))
        info = self._v1_to_info(mod, game)
        visible = [f for f in files.get("files", []) if f.get("category_name") not in _HIDDEN_CATEGORIES]
        visible.sort(key=lambda f: (f.get("category_name") != "MAIN", -(f.get("uploaded_timestamp") or 0)))
        for f in visible:
            info.files.append(ModFile(
                source=self.key, mod_id=str(mod_id), file_id=str(f["file_id"]), name=f.get("name") or "",
                version=f.get("version") or f.get("mod_version") or "", filename=f.get("file_name") or "",
                size=f.get("size_in_bytes") or (f.get("size_kb") or 0) * 1024 or None,
                is_primary=bool(f.get("is_primary")) or f.get("category_name") == "MAIN",
                uploaded_at=f.get("uploaded_time"), hashes={"md5": f["md5"]} if f.get("md5") else {},
                extra={"category": f.get("category_name"), "description": f.get("description") or "",
                       "game": game}))
        # La v1 no expone requisitos; se completarán con la v2 o leyendo la descripción (Fase 3).
        return info

    # --- Descarga -----------------------------------------------------------
    async def get_download_ticket(self, file_id, mod_id, game_ref=None, *, context=None):
        context = context or {}
        game = context.get("game_id") or (game_ref.require(self.key) if game_ref else None)
        if not game:
            raise ModNotFound("Nexus necesita el juego para descargar.")
        ref = game_ref or GameRef.single(self.key, game)
        _, f = await self._find_file(file_id, mod_id, ref)

        has_nxm_key = bool(context.get("key") and context.get("expires"))
        if not has_nxm_key and not (await self.validate()).is_premium:
            return self._ticket(
                DownloadPolicy.BROWSER_INTERCEPT, f, intercept=InterceptMethod.PROTOCOL,
                page_url=f"{SITE}/{game}/mods/{mod_id}?tab=files&file_id={file_id}&nmm=1",
                note="Cuenta gratuita: pulsa «Mod Manager Download» en la web y ModHub seguirá solo.")

        params = {"key": context["key"], "expires": context["expires"]} if has_nxm_key else {}
        try:
            links = await self._get(f"/games/{game}/mods/{mod_id}/files/{file_id}/download_link.json", **params)
        except HttpError as e:
            if e.status in (403, 410):
                raise AuthRequired("Nexus rechazó la descarga: el enlace ha caducado o hace falta Premium.") from e
            raise
        preferred = self.settings.get("preferred_cdn")
        link = next((l for l in links if preferred and l.get("short_name") == preferred), links[0])
        return self._ticket(DownloadPolicy.AUTOMATIC, f, url=link["URI"])

    # --- Protocolo nxm:// ---------------------------------------------------
    def parse_protocol_url(self, url):
        # nxm://<juego>/mods/<mod_id>/files/<file_id>?key=…&expires=…&user_id=…
        if not url.lower().startswith("nxm://"):
            return None
        parts = urlsplit(url)
        segs = [s for s in parts.path.split("/") if s]
        if len(segs) < 4 or segs[0] != "mods" or segs[2] != "files":
            return None
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        game = (parts.hostname or "").lower()
        return ProtocolRequest(source=self.key, mod_id=segs[1], file_id=segs[3], game_id=game,
                               context={"game_id": game, "key": q.get("key"), "expires": q.get("expires"),
                                        "user_id": q.get("user_id")})
