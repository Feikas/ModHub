"""¿Dónde dice la descripción del mod que hay que instalarlo?

Muchos autores lo escriben en la descripción: «Extract to Documents\\My Games\\Skyrim Special Edition»,
«copy Engine.ini to %LOCALAPPDATA%\\Pal\\Saved\\Config\\Windows», «drop the .pak into Content\\Paks\\~mods»,
«extrae en la carpeta del juego»… Este módulo lo lee y propone a qué ruta va cada archivo del mod.

Criterios acordados entre Claude, ChatGPT y Gemini:
- Lo que se lee son PISTAS, nunca órdenes: se usan solo si las reglas del juego no están seguras, y si el destino
  queda fuera de la carpeta del juego se pide confirmación citando la frase de la descripción.
- Se ignoran frases de borrar/renombrar/desinstalar, de versiones antiguas y de datos de Steam (userdata).
- Fuera del juego solo carpetas del usuario de una lista blanca (Documentos, AppData, LocalLow, Saved Games) y
  siempre dentro de una subcarpeta (nunca archivos sueltos en Documentos).
- Se decide por GRUPOS de archivos: «copia los .ini a…» solo afecta a los .ini; si el zip ya trae parte de la ruta
  (Saved/Config/…), se encaja por segmentos completos; si el zip trae carpetas «Documents», «AppData»…, se respetan.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .user_dirs import PREFIX

# ── texto ────────────────────────────────────────────────────────────────
_TAGS = re.compile(r"<(?:br|/p|/div|/li|li|p|div)[^>]*>", re.I)
_ANY_TAG = re.compile(r"</?(?:a|b|i|u|s|em|strong|span|font|img|br|p|div|li|ul|ol|h[1-6]|table|tbody|thead|tr|td|th|"
                      r"blockquote|code|pre|hr|center|sup|sub|small|big|iframe|figure|figcaption|section)"
                      r"(?:\s[^<>]*)?/?>", re.I)   # solo etiquetas HTML: «<game folder>» se conserva
_BBCODE = re.compile(r"\[/?(?:b|i|u|s|size|color|font|center|quote|code|spoiler|list|\*|url|img|line|heading)"
                     r"(?:=[^\]]*)?\]", re.I)
_SENTENCES = re.compile(r"(?<=[.!?;:])\s+(?=[A-Z0-9¿¡\"'(])|\n+")

INSTALL_VERBS = re.compile(
    r"\b(extract|install|unzip|unpack|place|put|copy|paste|drop|drag|move|merge|goes?\s+(?:in|to)|"
    r"extrae|extraer|descomprim\w*|instala\w*|copia\w*|pega\w*|coloca\w*|pon\b|poner|mueve|mover|arrastra\w*)\b",
    re.I)
SKIP_SENTENCE = re.compile(
    r"\b(delete|remove|uninstall|rename|backup|back\s+up|old(?:er)?\s+version|previous\s+version|if\s+you\s+"
    r"(?:had|have|used|updated)|borra\w*|elimina\w*|desinstala\w*|renombra\w*|copia\s+de\s+seguridad|"
    r"versi[oó]n\s+anterior|userdata|steam\s+cloud|log\s+file|crash)\b", re.I)

# Una ruta: varios segmentos separados por \ o /  (admite %VAR%, C:\, <placeholder>, espacios dentro)
_SEG = r"(?:%[A-Za-z_]+%|<[^<>\\/\n]{1,40}>|\[[^\[\]\\/\n]{1,40}\]|[\w~&'()+.-]+(?:[ ][\w~&'()+.-]+){0,5})"
PATH = re.compile(rf"(?:[A-Za-z]:)?[\\/]?{_SEG}(?:\s*[\\/]\s*{_SEG})+[\\/]?")
# Carpetas nombradas sin barras: «the Data folder», «carpeta ~mods», «root folder», «game folder»
NAMED = re.compile(r"\b(?:the\s+|your\s+|la\s+carpeta\s+|carpeta\s+)?(?P<name>[\w~.-]{2,30}|game|root|ra[ií]z)\s+"
                   r"(?:folder|directory|dir)\b|\bcarpeta\s+(?:de\s+|del\s+)?(?P<es>juego|ra[ií]z|[\w~.-]{2,30})", re.I)
EXT = re.compile(r"(?<![\w\\/])(?:\*)?\.(pak|ucas|utoc|ini|dll|asi|esp|esm|esl|bsa|ba2|lua|json|cfg|xml|txt|"
                 r"dat|zip|bk2|usm|awb|acb|wem|bnk|sav|save)\b", re.I)

GAME_WORDS = {"game", "juego", "root", "raíz", "raiz", "install", "installation", "gamefolder", "game folder",
              "game directory", "game dir", "game root", "game install", "game install folder",
              "steam folder", "install folder", "install directory", "game's folder", "games folder"}
GENERIC_NAMED = {"the", "this", "that", "a", "your", "same", "new", "zip", "archive", "downloads", "download",
                 "mod", "mods manager", "temp", "desktop", "escritorio", "descargas", "my", "main", "any"}


@dataclass
class PathHint:
    target: str                      # «@documents/My Games/X», «Content/Paks/~mods» o «» (raíz del juego)
    quote: str                       # frase de la descripción (para mostrarla al usuario)
    confidence: float
    exts: frozenset[str] = frozenset()   # si la frase habla solo de .ini, .pak… (vacío = todo)
    filename: str = ""               # si la ruta acababa en un archivo concreto («…\\Engine.ini»)

    @property
    def external(self) -> bool:
        return self.target.startswith(PREFIX)


def to_text(description: str) -> str:
    text = _TAGS.sub("\n", description or "")
    text = _BBCODE.sub(" ", _ANY_TAG.sub(" ", text))
    return html.unescape(text).replace("\xa0", " ")


# ── interpretar una ruta escrita por un humano ─────────────────────────────
_LEAD = re.compile(r"^.*\b(?:to|into|in|inside|at|on|onto|under|with|from|en|a|dentro\s+de|hacia|dans)\s+", re.I)


def _clean_segments(raw: str) -> list[str]:
    segs = [s.strip().strip("\"'`.,;:)(") for s in re.split(r"\s*[\\/]\s*", raw.strip())]
    segs = [s for s in segs if s and s not in (".",)]
    if segs and " " in segs[0] and not segs[0].startswith(("<", "[", "%")):
        # «Extract the contents to Documents» -> «Documents» (la frase se coló en el primer trozo de la ruta)
        first = _LEAD.sub("", segs[0]).strip()
        m = re.search(r"(<[^<>]+>|\[[^\[\]]+\])$", segs[0])
        segs[0] = m.group(1) if m else first
        segs = [s for s in segs if s]
    if segs and " " in segs[-1]:
        # «…\Starfield and paste StarfieldCustom.ini» -> «Starfield» (la frase sigue después de la ruta)
        segs[-1] = re.sub(r"\s+(?:and|then|or|to|into|for|if|so|before|after|y|e|luego|para|o|si|antes|"
                          r"despu[eé]s)\b.*$", "", segs[-1], flags=re.I).strip()
        segs = [s for s in segs if s]
    return segs


def _is_placeholder(seg: str) -> bool:
    low = seg.lower().strip("<>[]{}%")
    return seg.startswith(("<", "[", "{")) or low in {"username", "user", "yourname", "your name", "usuario",
                                                       "tu usuario", "name", "user name"}


def interpret_path(raw: str) -> str | None:
    """Ruta escrita en la descripción -> destino de ModHub («@documents/…», relativa al juego, «» = raíz)
    o None si no es un destino que se pueda usar (sistema, Program Files sin juego, Steam userdata…)."""
    segs = _clean_segments(raw)
    if not segs:
        return None
    low = [s.lower() for s in segs]
    if any(s == "userdata" for s in low) or any(s in ("windows", "system32", "syswow64") for s in low[:2]):
        return None
    if re.fullmatch(r"[a-z]:?", low[0]) and len(low) > 1:  # C: / D: (o «C» si se le comió los dos puntos)
        segs, low = segs[1:], low[1:]
    env = {"%userprofile%": "home", "%homepath%": "home", "%appdata%": "@appdata", "%localappdata%": "@localappdata",
           "%documents%": "@documents", "%mydocuments%": "@documents", "%savedgames%": "@savedgames",
           "%public%": None, "%programfiles%": "pf", "%programfiles(x86)%": "pf", "%temp%": None}
    if low and low[0] in env:
        kind = env[low[0]]
        if kind is None:
            return None
        if kind.startswith("@"):
            return _join(kind, segs[1:])
        segs, low = segs[1:], low[1:]
        if kind == "pf":
            return _game_relative(segs, low)
    if low[:1] == ["users"] and len(segs) >= 2:          # C:\Users\<tu usuario>\…
        segs, low = segs[2:], low[2:]
    elif low and _is_placeholder(segs[0]) and low[0].strip("<>[]{}%") in {"username", "user", "usuario", "yourname"}:
        segs, low = segs[1:], low[1:]
    if not segs:
        return None
    # Carpetas del usuario
    if low[0] in ("documents", "my documents", "documentos", "mis documentos"):
        return _join("@documents", segs[1:])
    if low[0] == "my games":
        return _join("@documents", segs)
    if low[0] in ("saved games", "partidas guardadas"):
        return _join("@savedgames", segs[1:])
    if low[0] == "appdata" and len(low) >= 2:
        base = {"roaming": "@appdata", "local": "@localappdata", "locallow": "@locallow"}.get(low[1])
        return _join(base, segs[2:]) if base else None
    if low[0] in ("program files", "program files (x86)", "programdata", "windows"):
        return _game_relative(segs[1:], low[1:]) if low[0].startswith("program files") else None
    return _game_relative(segs, low)


def _join(base: str, segs: list[str]) -> str | None:
    segs = [s for s in segs if not _is_placeholder(s)]
    if len(segs) < 1:
        return None
    return f"{base}/" + "/".join(segs)


def _game_relative(segs: list[str], low: list[str]) -> str | None:
    """Rutas dentro del juego: quita «steamapps\\common\\<Juego>», «<game folder>», «XboxGames\\<Juego>\\Content»."""
    if "common" in low and (i := low.index("common")) + 1 < len(low) and (i == 0 or low[i - 1] == "steamapps"):
        segs, low = segs[i + 2:], low[i + 2:]
    elif "xboxgames" in low and (i := low.index("xboxgames")) + 1 < len(low):
        segs, low = segs[i + 2:], low[i + 2:]
        if low[:1] == ["content"]:
            segs, low = segs[1:], low[1:]
    elif low[0] in ("steam", "steamlibrary") and "steamapps" in low:
        return _game_relative(segs[low.index("steamapps"):], low[low.index("steamapps"):])
    while segs and (_is_placeholder(segs[0]) or low[0] in GAME_WORDS or
                    (" " in low[0] and any(w in low[0] for w in ("game", "juego")) and
                     any(w in low[0] for w in ("folder", "dir", "root", "install", "carpeta")))):
        segs, low = segs[1:], low[1:]
    return "/".join(segs)


# ── leer la descripción ────────────────────────────────────────────────────
def extract_hints(description: str) -> list[PathHint]:
    hints: dict[tuple[str, frozenset, str], PathHint] = {}
    for sentence in _SENTENCES.split(to_text(description)):
        sentence = sentence.strip()
        if len(sentence) < 6 or SKIP_SENTENCE.search(sentence):
            continue
        verb = INSTALL_VERBS.search(sentence)
        exts = frozenset(e.lower() for e in EXT.findall(sentence))
        found: list[tuple[str, str]] = []
        for m in PATH.finditer(sentence):
            raw = m.group(0)
            if raw.lower().startswith(("http", "www.")) or "://" in sentence[max(0, m.start() - 8):m.start() + 3]:
                continue
            filename = ""
            segs = _clean_segments(raw)
            if segs and re.search(r"\.[A-Za-z0-9]{2,4}$", segs[-1]) and not segs[-1].startswith("~"):
                filename, raw = segs[-1], "/".join(segs[:-1])
                if filename.lower().endswith((".exe", ".zip", ".7z", ".rar")):
                    filename = ""          # «junto a Game.exe»: la carpeta vale, el archivo no es del mod
            if (target := interpret_path(raw)) is not None:
                found.append((target, filename))
        if not found and verb:
            for m in NAMED.finditer(sentence):
                name = (m.group("name") or m.group("es") or "").strip()
                low = name.lower()
                if low in ("game", "root", "juego", "raiz", "raíz") or low.endswith("'s"):
                    found.append(("", ""))
                elif low not in GENERIC_NAMED and not low.startswith(("download", "desktop")):
                    found.append((name, ""))
        for target, filename in found:
            conf = 0.8 if verb else 0.45
            if target == "" and not verb:
                continue
            key = (target.lower(), exts, filename.lower())
            if key not in hints or hints[key].confidence < conf:
                hints[key] = PathHint(target=target, quote=sentence[:240], confidence=conf, exts=exts,
                                      filename=filename)
    return sorted(hints.values(), key=lambda h: -h.confidence)


# ── encajar los archivos del mod con las pistas ──────────────────────────────
USER_ANCHORS = {"documents", "my documents", "documentos", "my games", "appdata", "saved games"}


@dataclass
class HintPlan:
    mappings: list[tuple[PurePosixPath, PurePosixPath]] = field(default_factory=list)
    used: list[PathHint] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def external(self) -> bool:
        return any(str(dst).startswith(PREFIX) for _, dst in self.mappings)


def _strip_wrapper(files: list[PurePosixPath]) -> int:
    """Nº de carpetas envoltorio comunes a todos los archivos (p. ej. «MiMod-1.2/»)."""
    n = 0
    while files and all(len(f.parts) > n + 1 for f in files) and len({f.parts[n].lower() for f in files}) == 1:
        n += 1
    return n


def _overlap(rel: tuple[str, ...], target_segs: tuple[str, ...]) -> int:
    """Cuántos segmentos del principio del archivo coinciden con el final de la ruta de destino («Config/Windows»)."""
    low_rel = [p.lower() for p in rel[:-1]]
    low_t = [p.lower() for p in target_segs]
    for k in range(min(len(low_rel), len(low_t)), 0, -1):
        if low_rel[:k] == low_t[-k:]:
            return k
    return 0


def plan_from_hints(files: list[PurePosixPath], hints: list[PathHint]) -> HintPlan | None:
    """Destino de cada archivo según las pistas. None si no se puede decidir con seguridad."""
    if not files or not hints:
        return None
    wrap = _strip_wrapper(files)
    plan = HintPlan()
    used: dict[int, PathHint] = {}
    for f in files:
        # 1) El zip ya trae la ruta del usuario: «…/Documents/My Games/X/…», «…/AppData/Local/Pal/…»
        anchor = next((i for i, p in enumerate(f.parts[:-1]) if p.lower() in USER_ANCHORS), None)
        if anchor is not None:
            direct = interpret_path("/".join(f.parts[anchor:-1]))
            if direct and direct.startswith(PREFIX) and len(PurePosixPath(direct).parts) >= 3:
                plan.mappings.append((f, PurePosixPath(direct) / f.name))
                continue
        rel = f.parts[wrap:]
        # 2) La mejor pista para este archivo: la que habla de su tipo y encaja más segmentos
        ext = f.suffix.lower().lstrip(".")
        best, best_score, best_k = None, -1.0, 0
        for i, h in enumerate(hints):
            if h.exts and ext not in h.exts:
                continue
            if h.filename and h.filename.lower() != f.name.lower() and h.exts:
                continue
            tsegs = tuple(PurePosixPath(h.target).parts)
            k = _overlap(rel, tsegs)
            score = h.confidence + 0.2 * k + (0.3 if h.exts else 0) + (0.3 if h.filename.lower() == f.name.lower() else 0)
            if score > best_score:
                best, best_score, best_k = i, score, k
        if best is None:
            return None                        # un archivo sin destino claro: mejor que lo revise el usuario
        h = hints[best]
        used[best] = h
        tsegs = tuple(PurePosixPath(h.target).parts)
        tail = rel[-1:] if h.filename and h.filename.lower() == f.name.lower() else rel
        dst = PurePosixPath(*tsegs[:len(tsegs) - best_k], *tail)
        plan.mappings.append((f, dst))
    plan.used = list(used.values())
    plan.confidence = min((h.confidence for h in plan.used), default=0.8)
    return plan
