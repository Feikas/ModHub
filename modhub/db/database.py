"""Acceso a la base de datos SQLite local."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 2
_SCHEMA_FILE = Path(__file__).with_name("schema.sql")


class Database:
    """Envoltorio fino sobre sqlite3, seguro para usar desde varios hilos.

    Por defecto cada sentencia se confirma sola; para agrupar varias usa:
        with db.transaction():
            db.execute(...); db.execute(...)
    """

    def __init__(self, path: Path | str):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()
        self._depth = 0
        self._migrate()

    # --- Esquema y migraciones --------------------------------------------
    def _version(self) -> int:
        try:
            row = self._conn.execute("SELECT version FROM schema_version").fetchone()
            return row[0] if row else 0
        except sqlite3.OperationalError:
            return 0

    def _migrate(self) -> None:
        with self._lock:
            current = self._version()
            if current == 1:
                # v1 era solo un borrador (nunca guardó mods reales): se recrea desde cero.
                tables = [r[0] for r in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                self._conn.execute("PRAGMA foreign_keys = OFF")
                for t in tables:
                    self._conn.execute(f'DROP TABLE IF EXISTS "{t}"')
                self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.executescript(_SCHEMA_FILE.read_text(encoding="utf-8"))
            self._conn.execute("DELETE FROM schema_version")
            self._conn.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))

    # --- Consultas --------------------------------------------------------
    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def executemany(self, sql: str, rows) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.executemany(sql, rows)

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @contextmanager
    def transaction(self) -> Iterator["Database"]:
        """Todo o nada. Se puede anidar (solo la más externa confirma)."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._depth -= 1
                if outer:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if outer:
                    self._conn.execute("COMMIT")

    # --- Caché para las APIs -------------------------------------------
    def cache_get(self, key: str):
        row = self.one("SELECT payload, expires_at FROM api_cache WHERE key=?", (key,))
        if row and row["expires_at"] > time.time():
            return json.loads(row["payload"])
        return None

    def cache_set(self, key: str, value, ttl_seconds: float) -> None:
        self.execute("INSERT OR REPLACE INTO api_cache VALUES (?,?,?)",
                     (key, json.dumps(value), time.time() + ttl_seconds))

    def external_game_id(self, game_id: int, source: str) -> str | None:
        row = self.one("SELECT external_id FROM game_source_ids WHERE game_id=? AND source=?", (game_id, source))
        return row["external_id"] if row else None

    def close(self) -> None:
        self._conn.close()
