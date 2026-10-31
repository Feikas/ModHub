-- ModHub · esquema SQLite (versión 2)
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

-- Juegos detectados o añadidos a mano
CREATE TABLE IF NOT EXISTS games (
    id              INTEGER PRIMARY KEY,
    slug            TEXT NOT NULL UNIQUE,            -- 'skyrimse', 'lethalcompany'…
    name            TEXT NOT NULL,
    install_path    TEXT NOT NULL,
    exe_path        TEXT,
    store           TEXT CHECK (store IN ('steam','epic','xbox','gog','custom')),
    store_app_id    TEXT,
    profile         TEXT NOT NULL DEFAULT 'generic',  -- slug del GameProfile
    deploy_method   TEXT NOT NULL DEFAULT 'hardlink' CHECK (deploy_method IN ('hardlink','symlink','copy')),
    staging_path    TEXT NOT NULL,                   -- en el MISMO disco que install_path
    added_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Un mismo juego tiene IDs distintos en cada web
CREATE TABLE IF NOT EXISTS game_source_ids (
    game_id     INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    source      TEXT NOT NULL,
    external_id TEXT NOT NULL,
    PRIMARY KEY (game_id, source)
);

-- Mods conocidos (descargados, instalados o activados)
CREATE TABLE IF NOT EXISTS mods (
    id              INTEGER PRIMARY KEY,
    game_id         INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    source          TEXT NOT NULL,                   -- 'nexus', 'thunderstore'… o 'local'
    source_mod_id   TEXT NOT NULL,
    source_file_id  TEXT NOT NULL DEFAULT '',
    name            TEXT NOT NULL,
    version         TEXT,
    author          TEXT,
    page_url        TEXT,
    archive_path    TEXT,                -- archivo descargado original
    staging_dir     TEXT,                -- carpeta descomprimida
    install_rule    TEXT,                -- regla del analizador que se usó
    confidence      REAL,
    state           TEXT NOT NULL DEFAULT 'downloaded'
                    CHECK (state IN ('downloading','downloaded','installed','enabled','disabled','needs_review','error')),
    priority        INTEGER NOT NULL DEFAULT 0,      -- mayor = gana en conflictos
    error           TEXT,
    installed_at    TEXT,
    UNIQUE (game_id, source, source_mod_id, source_file_id)
);

-- Plan de instalación de cada mod: qué archivo de staging va a qué ruta del juego
CREATE TABLE IF NOT EXISTS mod_files (
    mod_id          INTEGER NOT NULL REFERENCES mods(id) ON DELETE CASCADE,
    rel_path        TEXT NOT NULL,       -- destino relativo a la raíz del juego (con '/')
    source_path     TEXT NOT NULL,       -- ruta absoluta en staging
    PRIMARY KEY (mod_id, rel_path)
);

-- Lo que está REALMENTE desplegado ahora en la carpeta del juego
CREATE TABLE IF NOT EXISTS deployed_files (
    game_id         INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    rel_key         TEXT NOT NULL,       -- rel_path en minúsculas (Windows no distingue)
    rel_path        TEXT NOT NULL,
    mod_id          INTEGER REFERENCES mods(id) ON DELETE SET NULL,
    source_path     TEXT NOT NULL,
    link_type       TEXT NOT NULL CHECK (link_type IN ('hardlink','symlink','copy')),
    file_id         TEXT,                -- "dispositivo:inodo" del archivo creado (detecta si alguien lo cambió)
    deployed_at     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (game_id, rel_key)
);

-- Carpetas creadas por ModHub dentro del juego (se borran al purgar si quedan vacías)
CREATE TABLE IF NOT EXISTS deployed_dirs (
    game_id         INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    rel_key         TEXT NOT NULL,
    rel_path        TEXT NOT NULL,
    PRIMARY KEY (game_id, rel_key)
);

-- Archivos ORIGINALES del juego apartados porque un mod los sustituye
CREATE TABLE IF NOT EXISTS original_backups (
    game_id         INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    rel_key         TEXT NOT NULL,
    rel_path        TEXT NOT NULL,
    backup_path     TEXT NOT NULL,
    PRIMARY KEY (game_id, rel_key)
);

-- Dependencias declaradas por las webs o detectadas
CREATE TABLE IF NOT EXISTS mod_dependencies (
    mod_id          INTEGER NOT NULL REFERENCES mods(id) ON DELETE CASCADE,
    dep_source      TEXT NOT NULL,
    dep_mod_id      TEXT NOT NULL,
    dep_file_id     TEXT,
    required        INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (mod_id, dep_source, dep_mod_id)
);

-- Cola de descargas (sobrevive a reinicios)
CREATE TABLE IF NOT EXISTS downloads (
    id              INTEGER PRIMARY KEY,
    source          TEXT NOT NULL,
    source_mod_id   TEXT NOT NULL,
    source_file_id  TEXT,
    game_id         INTEGER REFERENCES games(id) ON DELETE SET NULL,
    url             TEXT,
    dest_path       TEXT,
    total_bytes     INTEGER,
    done_bytes      INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued','running','paused','done','failed','cancelled','needs_browser')),
    error           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Caché de respuestas de las APIs
CREATE TABLE IF NOT EXISTS api_cache (
    key         TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    expires_at  REAL NOT NULL
);
