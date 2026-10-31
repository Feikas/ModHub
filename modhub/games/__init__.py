"""Catálogo de perfiles de juegos."""
from __future__ import annotations

from pathlib import Path

from .base import GameProfile, ModLoader, normalize_name
from .profiles import GENERIC, PROFILES

_BY_SLUG = {p.slug: p for p in PROFILES} | {GENERIC.slug: GENERIC}


def get_profile(slug: str | None) -> GameProfile:
    """Perfil por slug; si no existe, el genérico."""
    return _BY_SLUG.get(slug or "", GENERIC)


def find_profile(store: str, store_id: str, name: str, install_path: Path | None = None) -> GameProfile | None:
    """El perfil que mejor encaja con un juego detectado, o None si ninguno supera el umbral."""
    best, best_score = None, 0
    for p in PROFILES:
        score = p.match_score(store, store_id, name, install_path)
        if score > best_score:
            best, best_score = p, score
    return best if best_score >= 50 else None


def register_profile(profile: GameProfile) -> None:
    """Permite añadir perfiles desde fuera (p. ej. plugins o archivos de usuario)."""
    _BY_SLUG[profile.slug] = profile
    global PROFILES
    PROFILES = (*PROFILES, profile)


__all__ = ["GameProfile", "ModLoader", "GENERIC", "PROFILES", "get_profile", "find_profile",
           "register_profile", "normalize_name"]
