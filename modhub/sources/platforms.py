"""¿Para qué versión del juego es un mod? (Steam · Xbox/PC Game Pass · Epic · GOG)

Muchos juegos tienen versiones distintas según la tienda (sobre todo Xbox / PC Game Pass, compilada con el
GDK de Microsoft) y los autores lo indican en el nombre del mod o del archivo: «Game Pass version»,
«Steam only», «Not compatible with Xbox»… Aquí se lee ese texto.

- platforms_in(texto)  -> (para_cuáles, excluidas). Vacío = universal (lo normal).
- is_compatible(...)   -> False solo si el mod dice claramente que es para OTRA versión o que no vale para esta.
"""
from __future__ import annotations

import re

PLATFORMS = ("steam", "xbox", "epic", "gog")
PLATFORM_NAMES = {"steam": "Steam", "xbox": "Xbox / Game Pass", "epic": "Epic Games", "gog": "GOG"}
STORE_TO_PLATFORM = {"steam": "steam", "xbox": "xbox", "epic": "epic", "gog": "gog"}

_PATTERNS = {
    "xbox": r"(?:pc\s*)?game\s*-?\s*pass|gamepass|"
            r"xbox(?!\s*(?:series|one\b|360|controllers?|gamepad|pad|buttons?|prompts?|icons?|glyphs?|layout|"
            r"ui\b|style|elite|wireless|achievements?|live|cloud|controllers?))(?:\s*(?:app|pc|game\s*pass|version|store))?|"
            r"ms\s*store|microsoft\s*store|windows\s*store|winstore|wingdk|\bgdk\b|\buwp\b",
    "steam": r"(?<![./])steam(?!\s*(?:workshop|deck|input|overlay|cloud|achievements?|api|controller|link|os|punk|"
             r"vr|engine|powered|community|ed|ing|er|s\b)|powered|community|\.)",
    "epic": r"(?<!by\s)epic\s*games?(?!\s*launcher\s*download)(?:\s*store)?|epic\s*(?:store|version|edition|launcher)|"
            r"\begs\b",
    "gog": r"\bgog(?:\.com|\s*galaxy)?\b",
}
_ANY = re.compile(r"\b(" + "|".join(f"(?P<{k}>{v})" for k, v in _PATTERNS.items()) + r")", re.I)
# «not compatible with X», «doesn't work on X», «no X support», «X not supported», «except X», «sin X»…
_NEG_BEFORE = re.compile(r"(?:\bnot?\b|\bnon\b|doesn'?t|does\s+not|won'?t|will\s+not|cannot|can'?t|incompatible|"
                         r"except|excluding|without|\bsin\b|\bni\b)[\w\s/,'-]{0,25}$", re.I)
_NEG_AFTER = re.compile(r"^[\w\s/,'-]{0,12}\b(?:not\s+(?:supported|compatible|working)|unsupported|"
                        r"incompatible|isn'?t\s+supported|no\s+(?:funciona|compatible))", re.I)


_UNKNOWN_AFTER = re.compile(r"^[\w\s/,'-]{0,12}\b(?:untested|not\s+tested|sin\s+probar|unknown|may\s+not)", re.I)


def platforms_in(*texts: str | None) -> tuple[frozenset[str], frozenset[str]]:
    """Plataformas que el texto menciona como destino, y las que menciona como NO compatibles."""
    yes: set[str] = set()
    no: set[str] = set()
    for text in texts:
        if not text:
            continue
        for m in _ANY.finditer(text):
            key = next(k for k in _PATTERNS if m.group(k))
            before, after = text[max(0, m.start() - 40):m.start()], text[m.end():m.end() + 40]
            if _UNKNOWN_AFTER.search(after):
                continue                                  # «Game Pass untested»: no se sabe, no se marca
            (no if _NEG_BEFORE.search(before) or _NEG_AFTER.search(after) else yes).add(key)
    return frozenset(yes - no), frozenset(no)


def is_compatible(platforms, excluded, game_platform: str | None) -> bool:
    if not game_platform:
        return True
    if game_platform in (excluded or ()):
        return False
    return not platforms or game_platform in platforms


def label(platforms) -> str:
    return " · ".join(PLATFORM_NAMES[p] for p in PLATFORMS if p in (platforms or ()))


def pick_file(files, game_platform: str | None, default=None):
    """Archivo que conviene preseleccionar para esta versión del juego (None = dejar el de siempre).

    Solo se cambia la elección por defecto cuando hay motivo claro: un archivo marcado para tu versión
    («Game Pass version»), o que el de por defecto diga ser para OTRA versión."""
    if not game_platform or not files:
        return None
    default = default or next((f for f in files if f.is_primary), files[0])
    exact = [f for f in files if game_platform in f.platforms and game_platform not in f.excluded_platforms]
    if exact:
        return next((f for f in exact if f.is_primary), exact[0])
    if not is_compatible(default.platforms, default.excluded_platforms, game_platform):
        ok = [f for f in files if is_compatible(f.platforms, f.excluded_platforms, game_platform)]
        return next((f for f in ok if f.is_primary), ok[0]) if ok else None
    return None
