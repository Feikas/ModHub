"""Instaladores FOMOD (fomod/ModuleConfig.xml), el formato estándar de Nexus para mods con opciones.

Soporta lo que usan la inmensa mayoría de mods:
  • requiredInstallFiles (siempre se instalan)
  • installSteps -> optionalFileGroups -> plugins, con los 5 tipos de grupo
    (SelectExactlyOne, SelectAtMostOne, SelectAtLeastOne, SelectAll, SelectAny)
  • Tipos de plugin Required / Recommended / Optional / NotUsable / CouldBeUsable,
    incluidos los que dependen de condiciones (dependencyType)
  • conditionFlags + pasos visibles según flags + conditionalFileInstalls
  • fileDependency (¿existe ese .esp en el juego?) y orden Explicit/Ascending/Descending
  • prioridad de archivos cuando dos opciones escriben en el mismo sitio

No se ejecuta NINGÚN script (los antiguos script.cs de C# no se soportan, por seguridad).

    cfg = load_fomod(mod_dir)                 # None si el mod no trae FOMOD
    session = FomodSession(cfg, file_state=lambda f: "Active" if (data / f).exists() else "Missing")
    sel = session.default_selections()        # lo que marcaría el instalador por defecto
    plan = session.build_plan(sel, profile, game_root)   # -> InstallPlan (igual que el analizador)
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

from ..games.base import GameProfile
from .extractor import FileMapping, InstallPlan


class FomodError(Exception):
    pass


# ════════════════════════════════════════════════════════════════════════
#  Modelo
# ════════════════════════════════════════════════════════════════════════
@dataclass
class Condition:
    """<dependencies operator="And|Or"> con flagDependency / fileDependency / anidadas."""

    operator: str = "And"
    flags: list[tuple[str, str]] = field(default_factory=list)       # (flag, valor)
    files: list[tuple[str, str]] = field(default_factory=list)       # (archivo, Active|Inactive|Missing)
    children: list["Condition"] = field(default_factory=list)

    def evaluate(self, flags: dict[str, str], file_state: Callable[[str], str]) -> bool:
        results = [flags.get(f, "") == v for f, v in self.flags]
        results += [_state_matches(file_state(f), s) for f, s in self.files]
        results += [c.evaluate(flags, file_state) for c in self.children]
        if not results:
            return True
        return any(results) if self.operator.lower() == "or" else all(results)


def _state_matches(actual: str, wanted: str) -> bool:
    if wanted == "Inactive":       # sin gestor de orden de carga, "instalado pero inactivo" = presente
        return actual in ("Active", "Inactive")
    return actual == wanted


@dataclass
class FileEntry:
    source: str                    # relativo a la raíz del mod (puede usar "\")
    destination: str               # relativo a la raíz de instalación ("" = raíz)
    is_folder: bool = False
    priority: int = 0


@dataclass
class Plugin:
    name: str
    description: str = ""
    image: str | None = None
    files: list[FileEntry] = field(default_factory=list)
    flags: dict[str, str] = field(default_factory=dict)
    default_type: str = "Optional"
    type_patterns: list[tuple[Condition, str]] = field(default_factory=list)

    def type_for(self, flags: dict[str, str], file_state: Callable[[str], str]) -> str:
        for cond, t in self.type_patterns:
            if cond.evaluate(flags, file_state):
                return t
        return self.default_type


GROUP_TYPES = ("SelectExactlyOne", "SelectAtMostOne", "SelectAtLeastOne", "SelectAll", "SelectAny")


@dataclass
class Group:
    name: str
    type: str
    plugins: list[Plugin]


@dataclass
class Step:
    name: str
    groups: list[Group]
    visible: Condition | None = None


@dataclass
class FomodConfig:
    module_name: str
    root: Path                     # carpeta del mod que contiene "fomod/"
    image: str | None = None
    required_files: list[FileEntry] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    conditional: list[tuple[Condition, list[FileEntry]]] = field(default_factory=list)
    module_dependencies: Condition | None = None


# Selección: {(paso, grupo): {índices de plugins elegidos}}
Selections = dict[tuple[int, int], set[int]]


# ════════════════════════════════════════════════════════════════════════
#  Lectura del XML
# ════════════════════════════════════════════════════════════════════════
def find_fomod(mod_dir: Path) -> Path | None:
    """Ruta de ModuleConfig.xml (sin distinguir mayúsculas, aunque esté en una carpeta envoltorio)."""
    candidates = [p for p in mod_dir.rglob("*") if p.is_file() and p.name.lower() == "moduleconfig.xml"
                  and p.parent.name.lower() == "fomod"]
    return min(candidates, key=lambda p: len(p.parts)) if candidates else None


def _parse_xml(data: bytes) -> ET.Element:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        # Muchos ModuleConfig.xml declaran una codificación y usan otra: probamos a mano.
        for enc in ("utf-8-sig", "utf-16", "cp1252"):
            try:
                text = data.decode(enc)
                text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
                root = ET.fromstring(text)
                break
            except (UnicodeDecodeError, ET.ParseError):
                continue
        else:
            raise FomodError("El ModuleConfig.xml del mod está mal formado.")
    for el in root.iter():                                  # quitar espacios de nombres
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _ordered(items: list, order: str | None, key=lambda x: x.name) -> list:
    if (order or "Ascending") == "Explicit":
        return items
    return sorted(items, key=lambda x: key(x).lower(), reverse=(order == "Descending"))


def _condition(el: ET.Element | None) -> Condition | None:
    if el is None:
        return None
    # <visible> puede contener directamente flagDependency o un <dependencies>
    if el.tag in ("visible", "pattern") and el.find("dependencies") is not None:
        el = el.find("dependencies")
    cond = Condition(operator=el.get("operator", "And"))
    for child in el:
        if child.tag == "flagDependency":
            cond.flags.append((child.get("flag", ""), child.get("value", "")))
        elif child.tag == "fileDependency":
            cond.files.append((child.get("file", ""), child.get("state", "Active")))
        elif child.tag == "dependencies":
            cond.children.append(_condition(child))
        # gameDependency / fommDependency / foseDependency: se consideran cumplidas
    return cond


def _files(el: ET.Element | None) -> list[FileEntry]:
    out = []
    if el is None:
        return out
    for child in el:
        if child.tag not in ("file", "folder"):
            continue
        source = child.get("source", "")
        dest = child.get("destination")
        if dest is None:                                     # sin atributo: misma ruta que el origen
            dest = source if child.tag == "folder" else source
        out.append(FileEntry(source=source, destination=dest, is_folder=child.tag == "folder",
                             priority=int(child.get("priority", "0") or 0)))
    return out


def _plugin(el: ET.Element) -> Plugin:
    p = Plugin(name=el.get("name", "Opción"))
    if (d := el.find("description")) is not None and d.text:
        p.description = d.text.strip()
    if (img := el.find("image")) is not None:
        p.image = img.get("path")
    p.files = _files(el.find("files"))
    if (cf := el.find("conditionFlags")) is not None:
        p.flags = {f.get("name", ""): (f.text or "").strip() for f in cf.findall("flag")}
    td = el.find("typeDescriptor")
    if td is not None:
        if (t := td.find("type")) is not None:
            p.default_type = t.get("name", "Optional")
        elif (dt := td.find("dependencyType")) is not None:
            if (dflt := dt.find("defaultType")) is not None:
                p.default_type = dflt.get("name", "Optional")
            for pat in dt.findall("patterns/pattern"):
                cond, t = _condition(pat), pat.find("type")
                if cond is not None and t is not None:
                    p.type_patterns.append((cond, t.get("name", "Optional")))
    return p


def load_fomod(mod_dir: Path | str) -> FomodConfig | None:
    mod_dir = Path(mod_dir)
    path = find_fomod(mod_dir)
    if path is None:
        return None
    root = _parse_xml(path.read_bytes())
    if root.tag != "config":
        raise FomodError("ModuleConfig.xml no tiene el formato FOMOD esperado.")
    cfg = FomodConfig(module_name=(root.findtext("moduleName") or mod_dir.name).strip(), root=path.parent.parent)
    if (img := root.find("moduleImage")) is not None:
        cfg.image = img.get("path")
    cfg.module_dependencies = _condition(root.find("moduleDependencies"))
    cfg.required_files = _files(root.find("requiredInstallFiles"))

    steps_el = root.find("installSteps")
    if steps_el is not None:
        steps = []
        for s in steps_el.findall("installStep"):
            groups_el = s.find("optionalFileGroups")
            groups = []
            if groups_el is not None:
                for g in groups_el.findall("group"):
                    plugins_el = g.find("plugins")
                    plugins = [_plugin(p) for p in plugins_el.findall("plugin")] if plugins_el is not None else []
                    plugins = _ordered(plugins, plugins_el.get("order") if plugins_el is not None else None)
                    gtype = g.get("type", "SelectAny")
                    groups.append(Group(name=g.get("name", ""), type=gtype if gtype in GROUP_TYPES else "SelectAny",
                                        plugins=plugins))
                groups = _ordered(groups, groups_el.get("order"))
            steps.append(Step(name=s.get("name", ""), groups=groups, visible=_condition(s.find("visible"))))
        cfg.steps = _ordered(steps, steps_el.get("order"))

    for pat in root.findall("conditionalFileInstalls/patterns/pattern"):
        cond = _condition(pat)
        if cond is not None:
            cfg.conditional.append((cond, _files(pat.find("files"))))
    return cfg


# ════════════════════════════════════════════════════════════════════════
#  Sesión de instalación (lo que usa la ventana de opciones)
# ════════════════════════════════════════════════════════════════════════
class FomodSession:
    def __init__(self, config: FomodConfig, file_state: Callable[[str], str] | None = None):
        self.config = config
        self.file_state = file_state or (lambda _f: "Missing")
        self._index = {p.relative_to(config.root).as_posix().lower(): p
                       for p in config.root.rglob("*")}

    # --- flags y visibilidad ---------------------------------------------
    def flags_until(self, step_index: int, selections: Selections) -> dict[str, str]:
        """Flags acumulados por los pasos VISIBLES anteriores a `step_index`."""
        flags: dict[str, str] = {}
        for i, step in enumerate(self.config.steps[:step_index]):
            if not self.is_visible(i, flags):
                continue
            for g, group in enumerate(step.groups):
                for p in sorted(selections.get((i, g), set())):
                    flags.update(group.plugins[p].flags)
        return flags

    def is_visible(self, step_index: int, flags: dict[str, str]) -> bool:
        vis = self.config.steps[step_index].visible
        return vis is None or vis.evaluate(flags, self.file_state)

    def visible_steps(self, selections: Selections) -> list[int]:
        return [i for i in range(len(self.config.steps))
                if self.is_visible(i, self.flags_until(i, selections))]

    def all_flags(self, selections: Selections) -> dict[str, str]:
        return self.flags_until(len(self.config.steps), selections)

    def plugin_type(self, step: int, group: int, plugin: int, selections: Selections) -> str:
        p = self.config.steps[step].groups[group].plugins[plugin]
        return p.type_for(self.flags_until(step, selections), self.file_state)

    # --- selección por defecto y validación ------------------------------
    def default_for_step(self, step: int, selections: Selections) -> Selections:
        out: Selections = {}
        for g, group in enumerate(self.config.steps[step].groups):
            types = [self.plugin_type(step, g, i, selections) for i in range(len(group.plugins))]
            usable = [i for i, t in enumerate(types) if t != "NotUsable"]
            chosen = {i for i, t in enumerate(types) if t in ("Required", "Recommended")}
            if group.type == "SelectAll":
                chosen = set(range(len(group.plugins)))
            elif group.type in ("SelectExactlyOne", "SelectAtMostOne"):
                first = next((i for i in sorted(chosen) if i in usable), None)
                if first is None and group.type == "SelectExactlyOne" and usable:
                    first = usable[0]
                chosen = {first} if first is not None else set()
            elif group.type == "SelectAtLeastOne" and not chosen and usable:
                chosen = {usable[0]}
            out[(step, g)] = chosen
        return out

    def default_selections(self) -> Selections:
        sel: Selections = {}
        for i in range(len(self.config.steps)):
            sel.update(self.default_for_step(i, sel))
        return sel

    def validate_step(self, step: int, selections: Selections) -> list[str]:
        errors = []
        for g, group in enumerate(self.config.steps[step].groups):
            chosen = selections.get((step, g), set())
            for i in chosen:
                if self.plugin_type(step, g, i, selections) == "NotUsable":
                    errors.append(f"«{group.plugins[i].name}» no se puede usar con tu configuración.")
            for i in range(len(group.plugins)):
                if self.plugin_type(step, g, i, selections) == "Required" and i not in chosen:
                    errors.append(f"«{group.plugins[i].name}» es obligatorio.")
            n = len(chosen)
            if group.type == "SelectExactlyOne" and n != 1:
                errors.append(f"Elige exactamente una opción en «{group.name}».")
            elif group.type == "SelectAtMostOne" and n > 1:
                errors.append(f"Elige como mucho una opción en «{group.name}».")
            elif group.type == "SelectAtLeastOne" and n < 1:
                errors.append(f"Elige al menos una opción en «{group.name}».")
            elif group.type == "SelectAll" and n != len(group.plugins):
                errors.append(f"Todas las opciones de «{group.name}» son obligatorias.")
        return errors

    # --- resultado ----------------------------------------------------------
    def collect_entries(self, selections: Selections) -> list[FileEntry]:
        entries = list(self.config.required_files)
        visible = set(self.visible_steps(selections))
        for (s, g), chosen in selections.items():
            if s not in visible:
                continue
            for p in chosen:
                entries += self.config.steps[s].groups[g].plugins[p].files
        flags = self.all_flags(selections)
        for cond, files in self.config.conditional:
            if cond.evaluate(flags, self.file_state):
                entries += files
        return entries

    def _resolve(self, rel: str) -> Path | None:
        key = rel.replace("\\", "/").strip("/").lower()
        return self._index.get(key)

    def build_plan(self, selections: Selections, profile: GameProfile, game_root: Path | str | None = None,
                   install_root: str | None = None) -> InstallPlan:
        """Convierte la selección en un InstallPlan. Los destinos FOMOD son relativos a la carpeta de
        datos del juego (Data/ en Skyrim/Fallout = `profile.fallback_target`)."""
        base = PurePosixPath(install_root if install_root is not None else profile.fallback_target)
        chosen: dict[str, tuple[int, int, Path, PurePosixPath]] = {}     # destino -> (prioridad, orden, origen, destino)
        warnings: list[str] = []
        order = 0
        for entry in self.collect_entries(selections):
            src = self._resolve(entry.source) if entry.source else self.config.root
            if src is None:
                warnings.append(f"El instalador menciona «{entry.source}», pero no está en el archivo.")
                continue
            dest = PurePosixPath(entry.destination.replace("\\", "/").strip("/")) if entry.destination else None
            if entry.is_folder or src.is_dir():
                files = [p for p in src.rglob("*") if p.is_file()]
                pairs = [(p, (dest / p.relative_to(src).as_posix()) if dest else
                          PurePosixPath(p.relative_to(src).as_posix())) for p in files]
            else:
                pairs = [(src, dest if dest else PurePosixPath(src.name))]
            for s, d in pairs:
                if "fomod" in [x.lower() for x in d.parts[:1]]:
                    continue
                target = base / d if str(base) not in ("", ".") else d
                if ".." in target.parts:
                    warnings.append(f"Ruta peligrosa ignorada: {target}")
                    continue
                key = str(target).lower()
                order += 1
                prev = chosen.get(key)
                if prev is None or (entry.priority, order) >= (prev[0], prev[1]):
                    chosen[key] = (entry.priority, order, s, target)

        mappings = [FileMapping(source=s, target=t) for _, _, s, t in sorted(chosen.values(), key=lambda v: str(v[3]))]
        plan = InstallPlan(mod_dir=self.config.root, rule="fomod", confidence=1.0, mappings=mappings,
                           warnings=warnings)
        if game_root is not None:
            loaders = (*profile.required_loaders, *profile.optional_loaders)
            plan.provides_loaders = [l for l in loaders if l.provided_by(plan.targets())]
            plan.missing_loaders = [l for l in profile.missing_loaders(Path(game_root)) if l not in plan.provides_loaders]
        return plan


def data_file_state(data_dir: Path | str) -> Callable[[str], str]:
    """file_state para fileDependency: "Active" si el archivo existe en la carpeta de datos del juego."""
    data_dir = Path(data_dir)

    def state(name: str) -> str:
        return "Active" if (data_dir / name.replace("\\", "/")).exists() else "Missing"
    return state
