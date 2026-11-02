"""Motor de despliegue: coloca los mods en la carpeta del juego mediante enlaces.

Funciona como Vortex: se calcula el ESTADO DESEADO (todos los mods activados, ordenados
por prioridad; el de mayor prioridad gana si dos tocan el mismo archivo) y se compara con
lo que hay desplegado. Solo se crea o quita la diferencia.

Garantías:
  • Cada archivo creado queda registrado en `deployed_files` con su identidad (disco:inodo).
  • Solo se borra un archivo si sigue siendo exactamente el que ModHub creó. Si el juego o el
    usuario lo cambió, se deja en paz y se avisa.
  • Si un mod sustituye un archivo ORIGINAL del juego, el original se aparta a backups/ y se
    restaura cuando ese mod se desactiva.
  • Cada operación en disco se apunta en la base de datos justo después, así un apagón a
    mitad nunca deja archivos huérfanos sin registrar.
  • Nunca se escribe fuera de la carpeta del juego.

Métodos de enlace, por orden: hardlink → symlink → copia.
  - Hardlink: mismo archivo con dos nombres. 0 bytes extra. Exige mismo disco.
  - Symlink: acceso directo real. Exige "Modo de desarrollador" o administrador en Windows.
  - Copia: siempre funciona, pero ocupa el doble.
"""
from __future__ import annotations

import filecmp
import logging
import os
import shutil
import stat
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath

from .extractor import InstallPlan
from .platform_paths import PathAdapter
from .user_dirs import detect_user_dirs, split_target
from .staging import same_volume

log = logging.getLogger("modhub.deploy")



class LinkMethod(str, Enum):
    HARDLINK = "hardlink"
    SYMLINK = "symlink"
    COPY = "copy"


ORDER = (LinkMethod.HARDLINK, LinkMethod.SYMLINK, LinkMethod.COPY)


class DeployError(Exception):
    pass


@dataclass
class DeployReport:
    added: int = 0
    removed: int = 0
    unchanged: int = 0
    backed_up: int = 0
    restored: int = 0
    conflicts: dict[str, list[int]] = field(default_factory=dict)   # ruta -> ids de mods (el último gana)
    methods: Counter = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class _Want:
    rel_path: str
    mod_id: int
    source: str


def _file_id(path: Path, *, with_content: bool = False) -> str:
    """Identidad del archivo: «dispositivo:inodo». Para las COPIAS se añade «:tamaño:mtime_ns», porque editar
    un archivo copiado conserva el inodo y, sin esto, se borrarían los cambios del usuario al desactivar el mod."""
    st = os.stat(path, follow_symlinks=False)
    base = f"{st.st_dev}:{st.st_ino}"
    return f"{base}:{st.st_size}:{st.st_mtime_ns}" if with_content else base


def _norm(p: str) -> str:
    """En Windows os.readlink puede devolver '\\\\?\\C:\\…'; se quita el prefijo para comparar."""
    for prefix in ("\\\\?\\", "\\??\\"):
        if p.startswith(prefix):
            p = p[len(prefix):]
    return os.path.normcase(os.path.normpath(p))


def _force_unlink(path: Path) -> None:
    try:
        path.unlink()
    except PermissionError:            # archivo de solo lectura en Windows
        os.chmod(path, stat.S_IWRITE)
        path.unlink()


class Deployer:
    def __init__(self, db, *, allow_symlink: bool = True, allow_copy: bool = True,
                 user_dirs: dict[str, Path] | None = None):
        self.db = db
        self.allow_symlink = allow_symlink
        self.allow_copy = allow_copy
        self._user_dirs = user_dirs                 # Documentos, AppData… (se detectan al usarlos)

    @property
    def user_dirs(self) -> dict[str, Path]:
        if self._user_dirs is None:
            self._user_dirs = detect_user_dirs()
        return self._user_dirs

    def _base(self, root: Path, rel_path: str) -> tuple[Path, str, bool]:
        """(carpeta base, ruta dentro de ella, ¿es una carpeta del usuario?) para una ruta del plan."""
        base, rest = split_target(rel_path)
        if base is None:
            return root, rel_path, False
        if base not in self.user_dirs:
            raise DeployError(f"Destino no permitido: {rel_path}")
        if len(PurePosixPath(rest).parts) < 2:
            # Nunca archivos sueltos en Documentos/AppData: siempre dentro de la carpeta de un juego/programa
            raise DeployError(f"Destino demasiado general: {rel_path}")
        return Path(self.user_dirs[base]), rest, True

    def _abs(self, root: Path, rel_path: str) -> Path:
        base, rest, _ = self._base(root, rel_path)
        return base / Path(*PurePosixPath(rest).parts)

    # ════════════════════════════════════════════════════════════════════
    #  Planes de instalación
    # ════════════════════════════════════════════════════════════════════
    def register_plan(self, mod_id: int, plan: InstallPlan) -> None:
        """Guarda qué archivos de staging van a qué rutas del juego para este mod."""
        with self.db.transaction():
            self.db.execute("DELETE FROM mod_files WHERE mod_id=?", (mod_id,))
            self.db.executemany("INSERT INTO mod_files (mod_id, rel_path, source_path) VALUES (?,?,?)",
                                [(mod_id, str(m.target), str(m.source)) for m in plan.mappings])
            self.db.execute("UPDATE mods SET install_rule=?, confidence=?, staging_dir=? WHERE id=?",
                            (plan.rule, plan.confidence, str(plan.mod_dir), mod_id))

    # ════════════════════════════════════════════════════════════════════
    #  Despliegue / purga
    # ════════════════════════════════════════════════════════════════════
    def deploy_game(self, game_id: int) -> DeployReport:
        """Sincroniza la carpeta del juego con los mods activados."""
        return self._sync(game_id, purge=False)

    def purge_game(self, game_id: int) -> DeployReport:
        """Quita TODO lo desplegado por ModHub y restaura los originales. El juego queda como estaba."""
        return self._sync(game_id, purge=True)

    def _game(self, game_id: int):
        row = self.db.one("SELECT * FROM games WHERE id=?", (game_id,))
        if row is None:
            raise DeployError(f"El juego {game_id} no existe.")
        root = Path(row["install_path"])
        if not root.is_dir():
            raise DeployError(f"No se encuentra la carpeta del juego: {root}")
        return row, root

    def desired_state(self, game_id: int) -> tuple[dict[str, _Want], dict[str, list[int]]]:
        rows = self.db.query(
            """SELECT f.rel_path, f.source_path, m.id AS mod_id FROM mod_files f
               JOIN mods m ON m.id = f.mod_id
               WHERE m.game_id=? AND m.state='enabled' ORDER BY m.priority, m.id""", (game_id,))
        desired: dict[str, _Want] = {}
        owners: dict[str, list[int]] = {}
        for r in rows:                     # orden ascendente: el último (más prioridad) sobrescribe
            key = r["rel_path"].lower()
            desired[key] = _Want(r["rel_path"], r["mod_id"], r["source_path"])
            owners.setdefault(r["rel_path"], []).append(r["mod_id"])
        conflicts = {k: v for k, v in owners.items() if len(v) > 1}
        return desired, conflicts

    @staticmethod
    def _adapt_to_game(root: Path, desired: dict[str, _Want]) -> dict[str, _Want]:
        """Xbox / Game Pass: lo que iba a Binaries/Win64 va a Binaries/WinGDK (ver platform_paths)."""
        adapter = PathAdapter(root)
        out: dict[str, _Want] = {}
        for want in desired.values():
            rel = adapter.adapt(want.rel_path)
            out[rel.lower()] = _Want(rel, want.mod_id, want.source) if rel != want.rel_path else want
        return out

    def _sync(self, game_id: int, *, purge: bool) -> DeployReport:
        game, root = self._game(game_id)
        report = DeployReport()
        preferred = LinkMethod(game["deploy_method"])
        backups_dir = Path(game["staging_path"]) / "backups"

        desired, report.conflicts = ({}, {}) if purge else self.desired_state(game_id)
        desired = self._adapt_to_game(root, desired)
        current = {r["rel_key"]: r for r in self.db.query("SELECT * FROM deployed_files WHERE game_id=?", (game_id,))}

        # 1) Quitar lo que sobra, cambió de mod o apunta a un archivo de staging viejo
        kept: set[str] = set()
        for key, row in current.items():
            want = desired.get(key)
            if want and self._still_valid(root, row, want):
                kept.add(key)
                report.unchanged += 1
                continue
            self._remove(game_id, root, row, report)

        # 2) Restaurar originales que ya ningún mod tapa (antes de crear, por si un mod nuevo los vuelve a tapar)
        for b in self.db.query("SELECT * FROM original_backups WHERE game_id=?", (game_id,)):
            if b["rel_key"] not in desired:
                self._restore(game_id, root, b, report)

        # 3) Crear lo que falta
        for key, want in desired.items():
            if key in kept:
                continue
            try:
                self._place(game_id, root, backups_dir, key, want, preferred, report)
            except (OSError, DeployError) as e:
                report.errors.append(f"{want.rel_path}: {e}")
                log.exception("No se pudo desplegar %s", want.rel_path)

        # 4) Borrar carpetas vacías que creó ModHub
        self._prune_dirs(game_id, root)
        return report

    # ════════════════════════════════════════════════════════════════════
    #  Piezas
    # ════════════════════════════════════════════════════════════════════
    def _dest(self, root: Path, rel_path: str) -> Path:
        base, rest, external = self._base(root, rel_path)
        rel = PurePosixPath(rest)
        if rel.is_absolute() or ".." in rel.parts or not rel.parts or ":" in rest:
            raise DeployError(f"Ruta de destino no válida: {rel_path}")
        dest = base.joinpath(*rel.parts)
        if not dest.parent.resolve().is_relative_to(base.resolve()):
            where = "la carpeta del usuario permitida" if external else "la carpeta del juego"
            raise DeployError(f"La ruta saldría de {where}: {rel_path}")
        if external and "steam" in rel.parts[0].lower() and "userdata" in (p.lower() for p in rel.parts):
            raise DeployError(f"No se tocan los datos de Steam: {rel_path}")
        return dest

    def _is_ours(self, dest: Path, row) -> bool:
        """¿El archivo que hay ahora en el juego es exactamente el que creó ModHub?"""
        if row["link_type"] == LinkMethod.SYMLINK.value:
            return dest.is_symlink() and _norm(os.readlink(dest)) == _norm(row["source_path"])
        if not dest.exists() or dest.is_symlink():
            return False
        recorded = row["file_id"] or ""
        if row["link_type"] == LinkMethod.COPY.value and recorded.count(":") >= 3:
            return _file_id(dest, with_content=True) == recorded       # editado tras copiarlo -> ya no es nuestro
        return _file_id(dest) == ":".join(recorded.split(":")[:2])

    def _still_valid(self, root: Path, row, want: _Want) -> bool:
        """El archivo desplegado sigue siendo correcto para el estado deseado."""
        if row["mod_id"] != want.mod_id or _norm(row["source_path"]) != _norm(want.source):
            return False
        dest, src = self._abs(root, row["rel_path"]), Path(want.source)
        if not self._is_ours(dest, row) or not src.is_file():
            return False
        if row["link_type"] == LinkMethod.HARDLINK.value:
            return os.path.samefile(dest, src)                 # si el mod se actualizó, el origen es otro inodo
        if row["link_type"] == LinkMethod.COPY.value:
            s, d = src.stat(), dest.stat()
            # Dos versiones pueden tener igual tamaño y fecha, incluso dentro del mismo segundo.
            # shallow=False evita dar por idénticos contenidos distintos.
            filecmp.clear_cache()
            return s.st_size == d.st_size and filecmp.cmp(src, dest, shallow=False)
        return True

    def _remove(self, game_id: int, root: Path, row, report: DeployReport) -> None:
        try:
            dest = self._abs(root, row["rel_path"])
        except DeployError:
            self.db.execute("DELETE FROM deployed_files WHERE game_id=? AND rel_key=?", (game_id, row["rel_key"]))
            return
        if dest.exists() or dest.is_symlink():
            if self._is_ours(dest, row):
                _force_unlink(dest)
                report.removed += 1
            else:
                report.warnings.append(
                    f"{row['rel_path']} se cambió fuera de ModHub (¿actualización del juego?); se deja como está.")
        self.db.execute("DELETE FROM deployed_files WHERE game_id=? AND rel_key=?", (game_id, row["rel_key"]))

    def _place(self, game_id, root, backups_dir, key, want: _Want, preferred, report: DeployReport) -> None:
        src = Path(want.source)
        if not src.is_file():
            raise DeployError("falta el archivo en staging; vuelve a instalar el mod")
        dest = self._dest(root, want.rel_path)

        if dest.exists() or dest.is_symlink():
            backup = self.db.one("SELECT 1 FROM original_backups WHERE game_id=? AND rel_key=?", (game_id, key))
            if backup:
                report.warnings.append(f"{want.rel_path}: ya hay una copia del original guardada y apareció otro "
                                       "archivo nuevo; no se toca.")
                return
            bpath = backups_dir.joinpath(*PurePosixPath(want.rel_path).parts)
            bpath.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest), str(bpath))
            self.db.execute("INSERT INTO original_backups VALUES (?,?,?,?)",
                            (game_id, key, want.rel_path, str(bpath)))
            report.backed_up += 1

        base, _, external = self._base(root, want.rel_path)
        self._make_dirs(game_id, base, dest.parent, prefix=want.rel_path[:want.rel_path.index("/") + 1]
                        if external else "")
        # Fuera del juego (configuración, partidas…) siempre COPIA: el juego reescribe esos archivos y con un
        # enlace también cambiaría el original del mod.
        method = self._link(src, dest, LinkMethod.COPY if external else preferred)
        self.db.execute(
            """INSERT OR REPLACE INTO deployed_files (game_id, rel_key, rel_path, mod_id, source_path, link_type, file_id)
               VALUES (?,?,?,?,?,?,?)""",
            (game_id, key, want.rel_path, want.mod_id, str(src), method.value,
             _file_id(dest, with_content=method is LinkMethod.COPY)))
        report.added += 1
        report.methods[method.value] += 1

    def _restore(self, game_id: int, root: Path, b, report: DeployReport) -> None:
        dest = self._abs(root, b["rel_path"])
        bpath = Path(b["backup_path"])
        if dest.exists() or dest.is_symlink():
            report.warnings.append(f"No se restaura {b['rel_path']}: ya hay otro archivo en su lugar.")
            return
        if bpath.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(bpath), str(dest))
            report.restored += 1
        else:
            report.warnings.append(f"Se perdió la copia del original de {b['rel_path']}.")
        self.db.execute("DELETE FROM original_backups WHERE game_id=? AND rel_key=?", (game_id, b["rel_key"]))

    def _make_dirs(self, game_id: int, root: Path, folder: Path, *, prefix: str = "") -> None:
        missing = []
        p = folder
        while p != root and not p.exists():
            missing.append(p)
            p = p.parent
        for d in reversed(missing):
            d.mkdir()
            rel = prefix + d.relative_to(root).as_posix()
            self.db.execute("INSERT OR IGNORE INTO deployed_dirs VALUES (?,?,?)", (game_id, rel.lower(), rel))

    def _prune_dirs(self, game_id: int, root: Path) -> None:
        rows = self.db.query("SELECT * FROM deployed_dirs WHERE game_id=?", (game_id,))
        for r in sorted(rows, key=lambda r: -len(PurePosixPath(r["rel_path"]).parts)):
            try:
                d = self._abs(root, r["rel_path"])
            except DeployError:
                d = None
            if d is None:
                self.db.execute("DELETE FROM deployed_dirs WHERE game_id=? AND rel_key=?", (game_id, r["rel_key"]))
                continue
            if d.is_dir():
                try:
                    next(d.iterdir())
                    continue                         # no está vacía: se queda
                except StopIteration:
                    d.rmdir()
            self.db.execute("DELETE FROM deployed_dirs WHERE game_id=? AND rel_key=?", (game_id, r["rel_key"]))

    # --- Enlaces --------------------------------------------------------
    def _link(self, src: Path, dest: Path, preferred: LinkMethod) -> LinkMethod:
        order = list(ORDER[ORDER.index(preferred):]) + [m for m in ORDER[:ORDER.index(preferred)]]
        errors = []
        for method in order:
            if method is LinkMethod.SYMLINK and not self.allow_symlink:
                continue
            if method is LinkMethod.COPY and not self.allow_copy:
                continue
            try:
                if method is LinkMethod.HARDLINK:
                    if not same_volume(src, dest.parent):
                        errors.append("hardlink: staging en otro disco")
                        continue
                    self._hardlink(src, dest)
                elif method is LinkMethod.SYMLINK:
                    os.symlink(src, dest)
                else:
                    shutil.copy2(src, dest)
                return method
            except OSError as e:
                errors.append(f"{method.value}: {e}")
                if dest.exists() or dest.is_symlink():
                    _force_unlink(dest)
        raise DeployError("no se pudo enlazar ni copiar (" + "; ".join(errors) + ")")

    @staticmethod
    def _hardlink(src: Path, dest: Path) -> None:
        # os.link usa la API nativa de Windows y comunica los fallos como OSError,
        # que _link puede capturar para intentar symlink o copia.
        os.link(src, dest)

    # ════════════════════════════════════════════════════════════════════
    #  Comprobación
    # ════════════════════════════════════════════════════════════════════
    def verify_game(self, game_id: int) -> list[str]:
        """Problemas encontrados entre lo registrado y lo que hay en disco (para "Comprobar integridad")."""
        _, root = self._game(game_id)
        issues = []
        for row in self.db.query("SELECT * FROM deployed_files WHERE game_id=?", (game_id,)):
            dest = self._abs(root, row["rel_path"])
            if not (dest.exists() or dest.is_symlink()):
                issues.append(f"Falta: {row['rel_path']}")
            elif not self._is_ours(dest, row):
                issues.append(f"Modificado fuera de ModHub: {row['rel_path']}")
            if not Path(row["source_path"]).is_file():
                issues.append(f"Falta en staging: {row['source_path']}")
        return issues
