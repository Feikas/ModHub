"""Contrato común que cumplen todas las fuentes de mods.

La UI y el motor de instalación solo hablan con `SourceAdapter`; nunca saben si
detrás hay una API REST, GraphQL, un RSS o scraping de HTML.

Flujo típico:
    infos  = await adapter.search("sodium", game)            # lista de ModInfo (sin archivos)
    info   = await adapter.get_mod_details(infos[0].mod_id, game)  # ModInfo con .files
    ticket = await adapter.get_download_ticket(info.files[0].file_id, info.mod_id, game)
    -> el motor de descargas (Fase 3) actúa según ticket.policy
"""
from __future__ import annotations

import abc
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

from .platforms import is_compatible, platforms_in

if TYPE_CHECKING:
    from .http import HttpClient


# ════════════════════════════════════════════════════════════════════════
#  Políticas de descarga
# ════════════════════════════════════════════════════════════════════════
class DownloadPolicy(str, Enum):
    """Cómo llega realmente el archivo al disco."""

    AUTOMATIC = "automatic"
    """La fuente da una URL descargable: ModHub la baja solo (Modrinth, Thunderstore, Nexus Premium…)."""

    BROWSER_INTERCEPT = "browser_intercept"
    """Hay que pasar por la web. ModHub abre `page_url` y captura el resultado:
    - intercept=PROTOCOL: la web devuelve un enlace nxm:// / modhub:// con permiso de descarga
      (Nexus gratis) y la app lo recibe vía el manejador de protocolo.
    - intercept=DOWNLOADS_FOLDER: el navegador baja el archivo y ModHub lo detecta en
      la carpeta Descargas (CurseForge restringido, Mod DB, AyakaMods)."""

    MANUAL_STREAM = "manual_stream"
    """El archivo llega por un canal externo que no es el navegador (cliente de Steam,
    steamcmd…). ModHub lanza `command` o `page_url` y luego importa desde `watch_path`."""


class InterceptMethod(str, Enum):
    PROTOCOL = "protocol"
    DOWNLOADS_FOLDER = "downloads_folder"


# ════════════════════════════════════════════════════════════════════════
#  Modelos de datos
# ════════════════════════════════════════════════════════════════════════
@dataclass(slots=True)
class GameRef:
    """Un juego y su identificador en cada web.

    Cada fuente llama al mismo juego de forma distinta:
        GameRef("Lethal Company", ids={"thunderstore": "lethal-company",
                                       "nexus": "lethalcompany", "steam": "1966720"})
    Se construye desde la tabla `game_source_ids` de la base de datos.
    """

    name: str
    ids: dict[str, str] = field(default_factory=dict)
    slug: str = ""

    def id_for(self, source: str) -> str | None:
        return self.ids.get(source)

    def require(self, source: str) -> str:
        value = self.ids.get(source)
        if not value:
            raise GameNotSupported(f"'{self.name}' no tiene ID configurado para {source}.")
        return value

    @classmethod
    def single(cls, source: str, external_id: str, name: str = "") -> "GameRef":
        return cls(name=name or external_id, ids={source: external_id})


@dataclass(slots=True)
class Dependency:
    source: str
    mod_id: str
    file_id: str | None = None        # versión concreta exigida, si la fuente la indica
    required: bool = True
    name: str = ""


@dataclass(slots=True)
class ModFile:
    source: str
    mod_id: str
    file_id: str
    name: str
    version: str = ""
    filename: str = ""
    size: int | None = None
    is_primary: bool = False
    uploaded_at: str | None = None
    hashes: dict[str, str] = field(default_factory=dict)     # {'sha1': …, 'sha512': …, 'md5': …}
    game_versions: list[str] = field(default_factory=list)
    loaders: list[str] = field(default_factory=list)          # fabric, forge, bepinex…
    dependencies: list[Dependency] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)       # datos propios de la fuente
    platforms: frozenset[str] = frozenset()        # versión del juego para la que es (vacío = todas)
    excluded_platforms: frozenset[str] = frozenset()


@dataclass(slots=True)
class ModInfo:
    source: str
    mod_id: str
    name: str
    author: str = ""
    summary: str = ""
    description: str = ""
    page_url: str = ""
    thumbnail: str | None = None
    images: list[str] = field(default_factory=list)   # galería de capturas publicadas en la web
    downloads: int | None = None
    updated_at: str | None = None
    created_at: str | None = None              # fecha de publicación del mod
    game_id: str | None = None                 # ID del juego en esa fuente
    files: list[ModFile] = field(default_factory=list)   # vacío en resultados de búsqueda
    extra: dict[str, Any] = field(default_factory=dict)
    # --- Datos para los filtros (los rellena cada adaptador) ---
    is_adult: bool | None = None                    # None = la fuente no lo indica
    categories: list[str] = field(default_factory=list)       # categorías normalizadas (ver CATEGORIES)
    raw_categories: list[str] = field(default_factory=list)   # tal cual las da la web
    install_support: DownloadPolicy | None = None   # cómo se instalaría (estimado sin pedir el ticket)
    platforms: frozenset[str] = frozenset()         # «Steam», «Xbox / Game Pass»… según su texto (vacío = todas)
    excluded_platforms: frozenset[str] = frozenset()  # versiones que el autor dice que NO valen

    @property
    def uid(self) -> str:
        """Identificador global único: 'modrinth:AANobbMI'."""
        return f"{self.source}:{self.mod_id}"

    def latest_file(self) -> ModFile | None:
        """Archivo recomendado: el principal más reciente, o el primero."""
        primary = [f for f in self.files if f.is_primary]
        return (primary or self.files or [None])[0]


# ════════════════════════════════════════════════════════════════════════
#  Filtros de búsqueda unificados
# ════════════════════════════════════════════════════════════════════════
class AdultFilter(str, Enum):
    HIDE = "hide"          # Ocultar contenido adulto (por defecto)
    ONLY = "only"          # Mostrar solo contenido adulto
    ALL = "all"            # Mostrarlo todo


class AdultSupport(str, Enum):
    """Qué sabe cada fuente sobre el contenido adulto."""
    NEVER = "never"        # la web lo prohíbe en sus normas (Modrinth, CurseForge)
    FLAGGED = "flagged"    # cada mod viene marcado (Nexus, Thunderstore, mod.io, Steam)
    UNKNOWN = "unknown"    # no hay forma fiable de saberlo (scraping, GameBanana)


# Categorías comunes de ModHub. Cada web usa las suyas: se traducen con palabras clave.
CATEGORIES: dict[str, str] = {
    "ui": "Interfaz (UI/HUD)",
    "cosmetic": "Skins y cosméticos",
    "gameplay": "Jugabilidad",
    "overhaul": "Overhauls / conversiones",
    "fixes": "Correcciones",
    "graphics": "Gráficos y texturas",
    "audio": "Audio y música",
    "qol": "Calidad de vida",
    "content": "Contenido nuevo (mapas, objetos, criaturas)",
    "characters": "Personajes y NPCs",
    "library": "Librerías y dependencias",
    "tools": "Herramientas y utilidades",
    "performance": "Rendimiento",
}
_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ui": ("ui", "user interface", "interface", "hud", "menu", "inventory screen"),
    "cosmetic": ("skin", "cosmetic", "suit", "outfit", "hair", "clothing", "armour appearance",
                 "emote", "decoration", "appearance", "body", "face"),
    "gameplay": ("gameplay", "game-mechanics", "mechanic", "combat", "balance", "difficulty", "economy",
                 "crafting", "survival", "magic", "adventure"),
    "overhaul": ("overhaul", "conversion", "total conversion", "rework", "modpack"),
    "fixes": ("fix", "bug", "patch", "unofficial patch", "stability"),
    "graphics": ("graphic", "texture", "visual", "lighting", "shader", "enb", "reshade", "weather",
                 "model", "mesh", "resourcepack"),
    "audio": ("audio", "sound", "music", "voice", "boombox"),
    "qol": ("quality of life", "qol", "utility", "convenience", "client-side", "tweak"),
    "content": ("map", "moon", "level", "location", "quest", "item", "weapon", "equipment", "creature",
                "monster", "mob", "vehicle", "building", "worldgen", "dungeon", "new content"),
    "characters": ("character", "npc", "follower", "companion", "race", "player"),
    "library": ("librar", "api", "framework", "dependenc", "bepinex", "core mod"),
    "tools": ("tool", "utilit", "editor", "cheat", "debug", "management", "modders resource"),
    "performance": ("performance", "optimization", "optimisation", "fps"),
}


def classify_categories(raw: list[str] | tuple[str, ...]) -> list[str]:
    """['Bug Fixes', 'Cosmetics'] -> ['fixes', 'cosmetic']"""
    texts = [str(t).lower().replace("_", " ").replace("-", " ") for t in raw]
    # La palabra clave debe empezar una palabra: "ui" no coincide con "suits", "fix" sí con "bug fixes".
    return [key for key, words in _CATEGORY_KEYWORDS.items()
            if any(re.search(r"\b" + re.escape(w.replace("-", " ")), t) for t in texts for w in words)]


def parse_date(value: Any) -> datetime | None:
    """Fechas de cualquier API (ISO 8601, timestamp Unix…) -> datetime con zona UTC."""
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


class SortOrder(str, Enum):
    """Orden de los resultados (selector «Ordenar por» del buscador)."""

    RELEVANCE = "relevance"     # lo que mejor encaja con el texto (solo tiene sentido si hay texto)
    TRENDING = "trending"       # populares del momento
    DOWNLOADS = "downloads"     # más descargados de siempre
    NEWEST = "newest"           # publicados más recientemente
    UPDATED = "updated"         # actualizados más recientemente


SORT_LABELS: dict[SortOrder, str] = {
    SortOrder.RELEVANCE: "Recomendado", SortOrder.TRENDING: "Populares ahora",
    SortOrder.DOWNLOADS: "Más descargados", SortOrder.NEWEST: "Más nuevos",
    SortOrder.UPDATED: "Última actualización",
}

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ROMAN = {"ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}


def sort_key(mod: "ModInfo", order: SortOrder) -> float | None:
    """Valor numérico para ordenar de mayor a menor, o None si el orden lo decide cada web."""
    if order is SortOrder.DOWNLOADS:
        return float(mod.downloads or 0)
    if order is SortOrder.NEWEST:
        return ((parse_date(mod.created_at) or parse_date(mod.updated_at) or _EPOCH) - _EPOCH).total_seconds()
    if order is SortOrder.UPDATED:
        return ((parse_date(mod.updated_at) or _EPOCH) - _EPOCH).total_seconds()
    return None


def normalize_game_name(name: str) -> str:
    """'The Elder Scrolls V: Skyrim® Special Edition' -> 'theelderscrollsvskyrimspecialedition'"""
    import unicodedata
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    text = text.replace("&", "and")
    # «II» = «2»: los números romanos sueltos cuentan como arábigos (Slay the Spire II = Slay the Spire 2)
    text = re.sub(r"\b(ii|iii|iv|v|vi|vii|viii|ix|x)\b",
                  lambda m: str(_ROMAN[m.group(1)]), text)
    return re.sub(r"[^a-z0-9]", "", text)


_EDITION_WORDS = re.compile(
    r"(?:\s|^)(supporter|deluxe|complete|goty|game of the year|remastered|definitive|enhanced|anniversary|"
    r"ultimate|digital|standard|gold|premium|collector'?s|special|director'?s cut|legendary|edition|"
    r"bundle|pack)\b", re.I)


def _initials(text: str, *, skip_small: bool) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text)
    if skip_small:
        words = [w for w in words if w.lower() not in ("to", "of", "the", "a", "an", "and", "in", "on")]
    return "".join(w[0] for w in words).lower()


def drop_acronym(name: str) -> str:
    """'NTE: Neverness to Everness' / 'Neverness to Everness (NTE)' -> 'Neverness to Everness'."""
    name = (name or "").strip()
    pre = re.match(r"^([A-Za-z0-9]{2,6})\s*[:\-–]\s*(.+)$", name)          # «NTE: Neverness to Everness»
    post = re.match(r"^(.+?)\s*\(([A-Za-z0-9]{2,6})\)$", name)             # «Neverness to Everness (NTE)»
    for acr, rest in ((pre.group(1), pre.group(2)) if pre else ("", ""), (post.group(2), post.group(1)) if post else ("", "")):
        if acr and acr.lower() in (_initials(rest, skip_small=False), _initials(rest, skip_small=True)):
            return rest.strip()
    return name
    acr, rest = (m.group(1), m.group(2)) if ":" in name or "-" in name or "–" in name else (m.group(2), m.group(1))
    if name.endswith(")"):
        acr, rest = m.group(2), m.group(1)
    if acr.lower() in (_initials(rest, skip_small=False), _initials(rest, skip_small=True)):
        return rest.strip()
    return name


def strip_edition(name: str) -> str:
    """'Solasta II Supporter Edition' -> 'Solasta II' (para emparejar con el nombre «base» del juego)."""
    out = _EDITION_WORDS.sub(" ", drop_acronym(name or ""))
    return re.sub(r"\s{2,}", " ", out).strip(" -:")


def best_name_match(name: str, candidates: list[tuple[str, str]]) -> str | None:
    """Elige el ID cuyo nombre coincide con `name`. candidates = [(nombre, id), …].
    Primero coincidencia exacta normalizada; si no, la más parecida que contenga o esté contenida (≥ 80 %)."""
    target = normalize_game_name(name)
    if not target:
        return None
    for cand_name, cid in candidates:
        if normalize_game_name(cand_name) == target:
            return cid
    base = normalize_game_name(strip_edition(name))         # sin «Supporter Edition», «GOTY»…
    if base and base != target:
        for cand_name, cid in candidates:
            if normalize_game_name(strip_edition(cand_name)) == base:
                return cid
    best, best_ratio = None, 0.0
    for cand_name, cid in candidates:
        c = normalize_game_name(cand_name)
        if c and (c in target or target in c):
            short, long_ = sorted((c, target), key=len)
            extra = long_.replace(short, "", 1)
            if any(ch.isdigit() for ch in extra):         # «Fallout» ≠ «Fallout 4»: nunca confundir secuelas
                continue
            ratio = len(short) / len(long_)
            if ratio > best_ratio:
                best, best_ratio = cid, ratio
    if best_ratio >= 0.85:
        return best
    # Último intento: parecido aproximado («Slay spire 2» ≈ «Slay the Spire 2») con los MISMOS números
    import difflib
    digits = re.sub(r"\D", "", base or target)
    best, best_ratio = None, 0.0
    for cand_name, cid in candidates:
        c = normalize_game_name(strip_edition(cand_name)) or normalize_game_name(cand_name)
        if not c or re.sub(r"\D", "", c) != digits:
            continue
        ratio = difflib.SequenceMatcher(None, base or target, c).ratio()
        if ratio > best_ratio:
            best, best_ratio = cid, ratio
    return best if best_ratio >= 0.86 else None


@dataclass(slots=True)
class SearchFilterOptions:
    """Lo que la UI manda junto a cada búsqueda (panel "Opciones de contenido")."""

    adult: AdultFilter = AdultFilter.HIDE
    auto_install_only: bool = False               # solo "Compatible con ModHub" (AUTOMATIC)
    updated_within_days: int | None = None        # p. ej. 180 = actualizados en los últimos 6 meses
    game_version: str | None = None               # p. ej. "1.21.1" (lo usan Modrinth y CurseForge)
    categories: frozenset[str] = frozenset()      # claves de CATEGORIES; vacío = todas
    sort: SortOrder = SortOrder.RELEVANCE          # «Ordenar por» (Recomendado)
    platform: str | None = None                   # versión del juego elegido (xbox, steam…): oculta lo de otras

    def effective_sort(self, query: str) -> SortOrder:
        """Sin texto, «Relevancia» no significa nada: se muestran los populares del momento."""
        if self.sort is SortOrder.RELEVANCE and not (query or "").strip():
            return SortOrder.TRENDING
        return self.sort

    @property
    def is_default(self) -> bool:
        return self == SearchFilterOptions()

    def accepts(self, mod: ModInfo, *, now: datetime | None = None) -> bool:
        """Filtro común que se aplica a TODOS los resultados, haya o no filtro nativo en la web."""
        if self.adult is AdultFilter.HIDE and mod.is_adult is True:
            return False
        if self.adult is AdultFilter.ONLY and mod.is_adult is not True:
            return False
        if self.auto_install_only and mod.install_support is not DownloadPolicy.AUTOMATIC:
            return False
        if self.updated_within_days is not None:
            updated = parse_date(mod.updated_at)
            limit = (now or datetime.now(timezone.utc)) - timedelta(days=self.updated_within_days)
            if updated is None or updated < limit:
                return False
        if self.categories and not (set(mod.categories) & self.categories):
            return False
        if self.platform and not is_compatible(mod.platforms, mod.excluded_platforms, self.platform):
            return False
        return True

    def apply(self, mods: list[ModInfo]) -> list[ModInfo]:
        now = datetime.now(timezone.utc)
        return [m for m in mods if self.accepts(m, now=now)]

    def to_dict(self) -> dict[str, Any]:
        return {"adult": self.adult.value, "auto_install_only": self.auto_install_only,
                "updated_within_days": self.updated_within_days, "game_version": self.game_version,
                "categories": sorted(self.categories), "sort": self.sort.value}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "SearchFilterOptions":
        d = d or {}
        return cls(adult=AdultFilter(d.get("adult", "hide")), auto_install_only=bool(d.get("auto_install_only")),
                   updated_within_days=d.get("updated_within_days"), game_version=d.get("game_version") or None,
                   categories=frozenset(d.get("categories") or ()),
                   sort=SortOrder(d.get("sort") or SortOrder.RELEVANCE.value))


@dataclass(slots=True)
class DownloadTicket:
    policy: DownloadPolicy
    source: str
    mod_id: str
    file_id: str
    url: str | None = None                     # AUTOMATIC
    filename: str | None = None
    size: int | None = None
    hashes: dict[str, str] = field(default_factory=dict)   # para verificar tras descargar
    headers: dict[str, str] = field(default_factory=dict)
    page_url: str | None = None                # BROWSER_INTERCEPT / MANUAL_STREAM
    intercept: InterceptMethod | None = None   # BROWSER_INTERCEPT
    command: list[str] | None = None           # MANUAL_STREAM (p. ej. steamcmd)
    watch_path: str | None = None              # MANUAL_STREAM: dónde aparecerá el archivo
    note: str = ""                             # mensaje para el usuario


@dataclass(slots=True)
class ProtocolRequest:
    """Lo que trae una URL de protocolo (nxm://, ror2mm://, modhub://…) ya interpretada."""

    source: str
    mod_id: str
    file_id: str | None = None
    game_id: str | None = None
    context: dict[str, Any] = field(default_factory=dict)  # key/expires de Nexus, etc.


# ════════════════════════════════════════════════════════════════════════
#  Errores
# ════════════════════════════════════════════════════════════════════════
class SourceError(Exception):
    """Error genérico de una fuente."""


class AuthRequired(SourceError):
    """Falta la API key / inicio de sesión, o ha caducado."""


class GameNotSupported(SourceError):
    """Esta fuente no tiene el juego pedido (o no está configurado su ID)."""


class ModNotFound(SourceError):
    pass


# ════════════════════════════════════════════════════════════════════════
#  Interfaz común
# ════════════════════════════════════════════════════════════════════════
class SourceAdapter(abc.ABC):
    key: str = ""
    display_name: str = ""
    requires_auth: bool = False
    search_needs_auth: bool = True          # False si se puede buscar sin clave (Nexus GraphQL)
    needs_game: bool = True                 # ¿la búsqueda exige un juego?
    text_fallback: bool = False             # web multijuego: si no tiene el juego enlazado, buscar su nombre
    required_settings: tuple[str, ...] = () # ajustes sin los cuales no puede funcionar
    protocol_schemes: tuple[str, ...] = ()
    details_ttl: float = 300.0              # segundos que se reutiliza get_mod_details
    adult_support: AdultSupport = AdultSupport.UNKNOWN
    default_install: DownloadPolicy | None = None   # cómo se instalan normalmente sus mods
    can_auto_install: bool = True                   # False si NUNCA da descargas automáticas

    def __init__(self, http: "HttpClient", settings: dict[str, Any] | None = None):
        self.http = http
        self.settings = settings or {}
        self._details_cache: dict[tuple[str, str | None], tuple[float, ModInfo]] = {}

    # --- Obligatorios ---------------------------------------------------
    @abc.abstractmethod
    async def search(self, query: str, game_ref: GameRef | None = None, *, page: int = 1, page_size: int = 20,
                     filters: SearchFilterOptions | None = None) -> list[ModInfo]:
        """Busca mods. Devuelve ModInfo SIN archivos (más rápido).

        `filters` se usa para el filtrado NATIVO de la web cuando existe (p. ej. Nexus con el contenido
        adulto). El registro aplica después `filters.apply()` a todo, así el resultado es coherente."""

    @abc.abstractmethod
    async def _fetch_mod_details(self, mod_id: str, game_ref: GameRef | None) -> ModInfo:
        """Implementación real de get_mod_details (sin caché)."""

    @abc.abstractmethod
    async def get_download_ticket(self, file_id: str, mod_id: str, game_ref: GameRef | None = None, *,
                                  context: dict[str, Any] | None = None) -> DownloadTicket:
        """Dice cómo conseguir un archivo. `context` lleva datos extra (p. ej. key/expires de nxm://)."""

    # --- Comunes --------------------------------------------------------
    def enrich(self, mod: ModInfo) -> ModInfo:
        """Completa los datos de filtrado que la web no dio explícitamente."""
        if mod.install_support is None:
            mod.install_support = self.default_install
        if not mod.categories and mod.raw_categories:
            mod.categories = classify_categories(mod.raw_categories)
        if mod.is_adult is None and self.adult_support is AdultSupport.NEVER:
            mod.is_adult = False
        if not mod.platforms and not mod.excluded_platforms:
            mod.platforms, mod.excluded_platforms = platforms_in(mod.name, mod.summary)
        return mod

    @property
    def is_configured(self) -> bool:
        """¿Tiene todo lo necesario (API keys…) para funcionar?"""
        return all(self.settings.get(s) for s in self.required_settings)

    async def get_mod_details(self, mod_id: str, game_ref: GameRef | None = None) -> ModInfo:
        """Ficha completa del mod con todos sus archivos y dependencias (cacheada unos minutos)."""
        cache_key = (mod_id, game_ref.id_for(self.key) if game_ref else None)
        hit = self._details_cache.get(cache_key)
        if hit and time.monotonic() - hit[0] < self.details_ttl:
            return hit[1]
        info = await self._fetch_mod_details(mod_id, game_ref)
        for f in info.files:                   # ¿para qué versión del juego es cada archivo?
            if not f.platforms and not f.excluded_platforms:
                f.platforms, f.excluded_platforms = platforms_in(
                    f.name, f.filename, str(f.extra.get("description") or ""), str(f.extra.get("category") or ""))
        if not info.platforms and not info.excluded_platforms:
            info.platforms, info.excluded_platforms = platforms_in(info.name, info.summary)
        self._details_cache[cache_key] = (time.monotonic(), info)
        return info

    async def _find_file(self, file_id: str, mod_id: str, game_ref: GameRef | None) -> tuple[ModInfo, ModFile]:
        info = await self.get_mod_details(mod_id, game_ref)
        for f in info.files:
            if f.file_id == file_id:
                return info, f
        raise ModNotFound(f"{self.display_name}: el archivo {file_id} no existe en el mod {mod_id}.")

    def parse_protocol_url(self, url: str) -> ProtocolRequest | None:
        """Convierte una URL de protocolo en un ProtocolRequest, o None si no es de esta fuente."""
        return None

    async def search_games(self, text: str) -> list[tuple[str, str]]:
        """Juegos de esta web cuyo nombre contiene `text`: [(nombre, id)] (para el autocompletado)."""
        return []

    async def find_game_id(self, game_name: str) -> str | None:
        """ID de un juego en esta web a partir de su nombre (para enlazar juegos solos). None = no está."""
        return None

    def game_id(self, game_ref: GameRef | None) -> str | None:
        return game_ref.id_for(self.key) if game_ref else None

    def _setting(self, name: str) -> str:
        value = self.settings.get(name)
        if not value:
            raise AuthRequired(f"{self.display_name}: falta '{name}' en Ajustes.")
        return value

    def _ticket(self, policy: DownloadPolicy, f: ModFile, **kw) -> DownloadTicket:
        return DownloadTicket(policy=policy, source=self.key, mod_id=f.mod_id, file_id=f.file_id,
                              filename=f.filename or None, size=f.size, hashes=dict(f.hashes), **kw)

    async def close(self) -> None:
        """Libera recursos propios (la mayoría no tiene)."""
