"""Extracción segura de mods y análisis de su estructura.

Extracción (Extractor):
  - Motor principal: 7z.exe (7-Zip) -> abre .zip, .7z, .rar, .tar, .gz… con una sola herramienta.
  - Sin 7-Zip: zipfile / tarfile de Python, y py7zr para .7z si está instalado. .rar exige 7-Zip.
  - Seguridad: rechaza rutas absolutas o con "..", bombas de descompresión y enlaces
    simbólicos dentro del archivo. Nunca se ejecuta nada de lo que viene en un mod.
  - Se extrae en una carpeta temporal y se renombra al final: o queda completo o no queda nada.

Análisis (ModAnalyzer):
  - Lista los archivos extraídos y aplica las reglas del perfil del juego (games/rules.py).
  - Devuelve un InstallPlan: qué archivo va a qué ruta del juego, con qué confianza y avisos.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from ..games.base import GameProfile, ModLoader
from ..games.rules import Fallback, RuleContext
from .staging import safe_folder_name

log = logging.getLogger("modhub.extract")

MAX_TOTAL_BYTES = 64 * 1024 ** 3        # 64 GB descomprimidos
MAX_FILES = 300_000
MAX_RATIO = 1_000                        # compresión 1000:1 en un archivo grande = bomba
# Archivos de mod que no vienen comprimidos: se copian tal cual
LOOSE_EXTENSIONS = {".dll", ".jar", ".pak", ".ucas", ".utoc", ".esp", ".esm", ".esl", ".bsa", ".ba2",
                    ".asi", ".archive", ".lua", ".reds", ".xl", ".ini", ".json", ".txt", ".cfg"}


class ArchiveError(Exception):
    pass


class UnsafeArchive(ArchiveError):
    """El archivo intenta escribir fuera de su carpeta o es una bomba de descompresión."""


@dataclass
class ExtractResult:
    dest: Path
    files: int
    total_bytes: int
    method: str
    warnings: list[str] = field(default_factory=list)


# ════════════════════════════════════════════════════════════════════════
#  Utilidades de seguridad
# ════════════════════════════════════════════════════════════════════════
def safe_member_path(name: str) -> PurePosixPath:
    """Valida el nombre de una entrada del archivo y lo devuelve como ruta relativa segura."""
    clean = name.replace("\\", "/").strip()
    if not clean or clean.startswith("/") or (len(clean) > 1 and clean[1] == ":"):
        raise UnsafeArchive(f"Ruta absoluta dentro del archivo: {name!r}")
    parts = [p for p in clean.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise UnsafeArchive(f"Ruta que intenta salir de la carpeta: {name!r}")
    if any(":" in p for p in parts):
        raise UnsafeArchive(f"Nombre no válido en Windows: {name!r}")
    if not parts:
        raise UnsafeArchive(f"Entrada vacía: {name!r}")
    return PurePosixPath(*parts)


def detect_format(path: Path) -> str | None:
    """'zip' | '7z' | 'rar' | 'tar' | 'gzip' | 'bzip2' | 'xz' | None (no es un archivo comprimido)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(512)
    except OSError as e:
        raise ArchiveError(f"No se puede leer {path}: {e}") from e
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return "zip"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    if head.startswith(b"Rar!\x1a\x07"):
        return "rar"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    if head.startswith(b"BZh"):
        return "bzip2"
    if head.startswith(b"\xfd7zXZ\x00"):
        return "xz"
    if len(head) >= 262 and head[257:262] == b"ustar":
        return "tar"
    return None


def find_7zip(explicit: Path | str | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if env := os.environ.get("MODHUB_7Z"):
        candidates.append(Path(env))
    app_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    exe_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else app_dir
    candidates += [app_dir / "tools" / "7z.exe", exe_dir / "tools" / "7z.exe", app_dir / "tools" / "7zz"]
    for name in ("7z", "7za", "7zz"):
        if found := shutil.which(name):
            candidates.append(Path(found))
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        if base := os.environ.get(env_name):
            candidates.append(Path(base) / "7-Zip" / "7z.exe")
    return next((c for c in candidates if c.is_file()), None)


# ════════════════════════════════════════════════════════════════════════
#  Extractor
# ════════════════════════════════════════════════════════════════════════
class Extractor:
    def __init__(self, seven_zip: Path | str | None = None, *, max_total_bytes: int = MAX_TOTAL_BYTES,
                 max_files: int = MAX_FILES, timeout: float = 3600):
        self.seven_zip = find_7zip(seven_zip)
        self.max_total_bytes = max_total_bytes
        self.max_files = max_files
        self.timeout = timeout

    async def extract(self, archive: Path | str, dest: Path | str) -> ExtractResult:
        """Extrae en `dest` (que no debe existir o debe estar vacía). Se ejecuta en un hilo aparte."""
        return await asyncio.to_thread(self.extract_sync, Path(archive), Path(dest))

    def extract_sync(self, archive: Path, dest: Path) -> ExtractResult:
        if dest.exists() and any(dest.iterdir()):
            raise ArchiveError(f"La carpeta de destino no está vacía: {dest}")
        tmp = dest.with_name(dest.name + ".partial")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        try:
            fmt = detect_format(archive)
            if fmt is None:
                if archive.suffix.lower() not in LOOSE_EXTENSIONS:
                    raise ArchiveError(f"«{archive.name}» no es un archivo comprimido reconocible.")
                shutil.copy2(archive, tmp / archive.name)
                method = "copia"
            elif self.seven_zip:
                method = self._extract_7z_cli(archive, tmp)
            elif fmt == "zip":
                method = self._extract_zip(archive, tmp)
            elif fmt in ("tar", "gzip", "bzip2", "xz"):
                method = self._extract_tar(archive, tmp)
            elif fmt == "7z":
                method = self._extract_py7zr(archive, tmp)
            else:
                raise ArchiveError("Para abrir archivos .rar hace falta 7-Zip (7z.exe).")
            files, total, warnings = self._post_check(tmp)
            if files == 0:
                raise ArchiveError(f"«{archive.name}» está vacío.")
            if dest.exists():
                dest.rmdir()
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, dest)
            return ExtractResult(dest=dest, files=files, total_bytes=total, method=method, warnings=warnings)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise

    # --- motores ----------------------------------------------------------
    def _run7z(self, *args: str) -> subprocess.CompletedProcess:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            return subprocess.run([str(self.seven_zip), *args], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=self.timeout, creationflags=flags)
        except subprocess.TimeoutExpired as e:
            raise ArchiveError("La extracción tardó demasiado y se ha cancelado.") from e

    def _extract_7z_cli(self, archive: Path, out: Path) -> str:
        # 1) Listar y validar ANTES de extraer. Contraseña falsa para que nunca se quede esperando.
        listing = self._run7z("l", "-slt", "-ba", "-sccUTF-8", "-pModHubSinClave", str(archive))
        if listing.returncode != 0:
            raise ArchiveError(self._7z_error(listing))
        total, count = 0, 0
        for block in listing.stdout.split("\n\n"):
            props = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
            if "Path" not in props:
                continue
            safe_member_path(props["Path"])
            if props.get("Folder") == "+":
                continue
            count += 1
            total += int(props.get("Size") or 0)
            self._check_limits(count, total)
        # 2) Extraer
        result = self._run7z("x", "-y", "-bso0", "-bsp0", "-sccUTF-8", "-pModHubSinClave", f"-o{out}", str(archive))
        if result.returncode not in (0, 1):            # 1 = avisos no graves
            raise ArchiveError(self._7z_error(result))
        return "7-zip"

    @staticmethod
    def _7z_error(proc: subprocess.CompletedProcess) -> str:
        text = (proc.stderr or proc.stdout or "").strip()
        if "Wrong password" in text or "password" in text.lower():
            return "El archivo está protegido con contraseña."
        if "Can not open" in text or "not archive" in text.lower():
            return "El archivo está dañado o no es un formato compatible."
        return f"7-Zip falló: {text[-300:]}"

    def _check_limits(self, count: int, total: int) -> None:
        if count > self.max_files:
            raise UnsafeArchive(f"Demasiados archivos (más de {self.max_files}).")
        if total > self.max_total_bytes:
            raise UnsafeArchive("El contenido descomprimido es demasiado grande.")

    def _extract_zip(self, archive: Path, out: Path) -> str:
        try:
            zf = zipfile.ZipFile(archive)
        except zipfile.BadZipFile as e:
            raise ArchiveError("El .zip está dañado.") from e
        with zf:
            members, total = [], 0
            for info in zf.infolist():
                rel = safe_member_path(info.filename)
                if info.is_dir():
                    continue
                if (info.external_attr >> 16) & 0o170000 == 0o120000:   # enlace simbólico Unix
                    continue
                if info.flag_bits & 0x1:
                    raise ArchiveError("El archivo está protegido con contraseña.")
                total += info.file_size
                if info.compress_size and info.file_size > 100 * 1024 ** 2 \
                        and info.file_size / info.compress_size > MAX_RATIO:
                    raise UnsafeArchive(f"Compresión sospechosa en {info.filename} (posible bomba).")
                members.append((info, rel))
                self._check_limits(len(members), total)
            for info, rel in members:
                target = out.joinpath(*rel.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
        return "zipfile"

    def _extract_tar(self, archive: Path, out: Path) -> str:
        try:
            with tarfile.open(archive) as tf:
                members, total = [], 0
                for m in tf.getmembers():
                    safe_member_path(m.name)
                    if m.issym() or m.islnk() or m.isdev():
                        continue
                    if m.isfile():
                        total += m.size
                        members.append(m)
                        self._check_limits(len(members), total)
                kwargs = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
                tf.extractall(out, members=members, **kwargs)
        except tarfile.TarError as e:
            raise ArchiveError(f"El archivo tar está dañado: {e}") from e
        return "tarfile"

    def _extract_py7zr(self, archive: Path, out: Path) -> str:
        try:
            import py7zr
        except ImportError as e:
            raise ArchiveError("Para abrir .7z instala 7-Zip o el paquete py7zr.") from e
        with py7zr.SevenZipFile(archive) as z:
            if z.needs_password():
                raise ArchiveError("El archivo está protegido con contraseña.")
            infos = z.list()
            total = sum(i.uncompressed or 0 for i in infos if not i.is_directory)
            for i in infos:
                safe_member_path(i.filename)
            self._check_limits(len(infos), total)
            z.extractall(out)
        return "py7zr"

    def _post_check(self, root: Path) -> tuple[int, int, list[str]]:
        """Última barrera: ningún enlace ni nada fuera de la carpeta."""
        files, total, warnings = 0, 0, []
        root_resolved = root.resolve()
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames + filenames:
                p = Path(dirpath) / name
                if p.is_symlink() or getattr(p, "is_junction", lambda: False)():
                    try:
                        p.unlink()
                    except OSError:              # enlace a carpeta en Windows
                        os.rmdir(p)
                    warnings.append(f"Se ha eliminado un enlace simbólico del mod: {p.relative_to(root)}")
                    continue
                if not p.resolve().is_relative_to(root_resolved):
                    raise UnsafeArchive(f"Archivo fuera de la carpeta del mod: {p}")
            for name in filenames:
                p = Path(dirpath) / name
                if p.exists():
                    files += 1
                    total += p.stat().st_size
        return files, total, warnings


# ════════════════════════════════════════════════════════════════════════
#  Análisis de estructura
# ════════════════════════════════════════════════════════════════════════
@dataclass
class FileMapping:
    source: Path                    # archivo real en staging
    target: PurePosixPath           # destino relativo a la raíz del juego


@dataclass
class InstallPlan:
    mod_dir: Path
    rule: str
    confidence: float
    mappings: list[FileMapping]
    ignored: list[PurePosixPath] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_loaders: list[ModLoader] = field(default_factory=list)
    provides_loaders: list[ModLoader] = field(default_factory=list)
    has_fomod: bool = False

    REVIEW_THRESHOLD = 0.5

    @property
    def needs_review(self) -> bool:
        """True si conviene que el usuario confirme antes de instalar."""
        return self.confidence < self.REVIEW_THRESHOLD or self.has_fomod or not self.mappings

    def targets(self) -> list[str]:
        return [str(m.target) for m in self.mappings]

    def summary(self) -> dict:
        return {"rule": self.rule, "confidence": self.confidence, "files": len(self.mappings),
                "ignored": len(self.ignored), "warnings": self.warnings, "needs_review": self.needs_review,
                "missing_loaders": [l.name for l in self.missing_loaders],
                "top_targets": sorted({str(m.target).split("/")[0] for m in self.mappings})[:10]}


class ModAnalyzer:
    def __init__(self, profile: GameProfile):
        self.profile = profile

    def _ignored_dir(self, rel: PurePosixPath) -> bool:
        dirs = {d.lower() for d in self.profile.ignore_dirs}
        return any(part.lower() in dirs for part in rel.parts[:-1])

    def _ignored_file(self, rel: PurePosixPath) -> bool:
        name = rel.name.lower()
        return any(fnmatch(name, pat) for pat in self.profile.ignore)

    def analyze(self, mod_dir: Path | str, mod_name: str, game_root: Path | str | None = None) -> InstallPlan:
        mod_dir = Path(mod_dir)
        all_files = sorted(PurePosixPath(p.relative_to(mod_dir).as_posix())
                           for p in mod_dir.rglob("*") if p.is_file() and not p.is_symlink())
        has_fomod = any(str(f).lower().endswith("fomod/moduleconfig.xml") for f in all_files)
        files = [f for f in all_files if not self._ignored_dir(f)]
        ignored = [f for f in all_files if self._ignored_dir(f)]

        ctx = RuleContext(mod_name=safe_folder_name(mod_name))
        match = None
        for rule in (*self.profile.rules, Fallback(self.profile.fallback_target)):
            match = rule.match(files, ctx)
            if match and match.mappings:
                break

        warnings = list(match.notes) if match else []
        mappings: list[FileMapping] = []
        mapped_sources = set()
        seen_targets: dict[str, PurePosixPath] = {}
        for src, dst in (match.mappings if match else []):
            mapped_sources.add(src)
            if self._ignored_file(src):
                ignored.append(src)
                continue
            key = str(dst).lower()
            if key in seen_targets:
                warnings.append(f"Dos archivos del mod van al mismo sitio: {dst} (se usa {seen_targets[key]}).")
                continue
            seen_targets[key] = src
            mappings.append(FileMapping(source=mod_dir.joinpath(*src.parts), target=dst))
        ignored += [f for f in files if f not in mapped_sources]

        if has_fomod:
            warnings.insert(0, "El mod trae un instalador FOMOD con opciones: revisa qué archivos quieres instalar.")

        targets = [str(m.target) for m in mappings]
        loaders = (*self.profile.required_loaders, *self.profile.optional_loaders)
        provides = [l for l in loaders if l.provided_by(targets)]
        missing = []
        if game_root is not None:
            missing = [l for l in self.profile.missing_loaders(Path(game_root)) if l not in provides]

        return InstallPlan(mod_dir=mod_dir, rule=match.rule if match else "ninguna",
                           confidence=match.confidence if match else 0.0, mappings=mappings,
                           ignored=sorted(set(ignored)), warnings=warnings, missing_loaders=missing,
                           provides_loaders=provides, has_fomod=has_fomod)
