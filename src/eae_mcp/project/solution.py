"""Load and index a whole EAE solution."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import safety
from ..model import Network, TypeDef
from .cat import CatManifest, parse_cfg
from .catalog import Catalog
from .folders import KIND_TO_CATEGORY, FolderTree, load_folders_xml
from .msbuild import MsBuildProject, load_project
from .system import System, load_systems
from .types import load_type

PROJECT_KINDS = {
    "EAD1E85F-CEF5-4861-AFF8-597F2DDE70FC": "iec61499",
    "FAE04EC0-301F-11D3-BF4B-00C04F79EFBC": "hmi",
    "38DB5ADD-0BC9-44D4-BE85-0EAFA8D5BD5F": "web",
    "72AEB29F-A91B-8B3D-40ED-1C96841A14A3": "hwconfig",
    "E25C2A81-DD87-490C-A304-820B0BA163F2": "assetlink",
    "B8625A29-8134-4135-BEBD-5371646CD051": "topology",
    "AF5A4E9E-1CB6-4325-AE5C-F3437C6CEE36": "atvdisplay",
}

# .dfbproj IEC61499Type → TypeDef.kind (CAT HMI SIFBs are detected separately).
DFB_KINDS = {
    "Adapter": "adapter", "Basic": "basic", "Composite": "composite", "CAT": "cat",
    "DataType": "datatype", "Function": "function", "SubApp": "subapp", "ServiceInterface": "sifb",
}

_SLN_PROJECT = re.compile(
    r'^Project\("\{(?P<type>[^}]+)\}"\)\s*=\s*"(?P<name>[^"]+)",\s*"(?P<path>[^"]+)",\s*"\{(?P<guid>[^}]+)\}"',
    re.M,
)


@dataclass
class SolutionProject:
    name: str
    kind: str
    path: str  # relative, POSIX
    guid: str
    msbuild: MsBuildProject | None = None

    @property
    def library(self) -> str:
        """Library prefix: '' for the main projects, 'SE.Agile' for SE.Agile.* projects."""
        return self.name if self.kind == "iec61499" and self.name != "IEC61499" else ""


@dataclass
class Solution:
    root: Path
    sln: Path
    projects: list[SolutionProject] = field(default_factory=list)
    types: dict[str, TypeDef] = field(default_factory=dict)  # qualified name → type
    cats: dict[str, CatManifest] = field(default_factory=dict)  # qualified CAT name → manifest
    systems: list[System] = field(default_factory=list)
    folders: dict[str, FolderTree] = field(default_factory=dict)  # IEC project name → tree
    references: dict[str, str | None] = field(default_factory=dict)  # system libs → version
    catalog: Catalog | None = None
    warnings: list[str] = field(default_factory=list)

    # -- lookup ------------------------------------------------------------------

    def projects_of(self, kind: str) -> list[SolutionProject]:
        return [p for p in self.projects if p.kind == kind]

    def find_type(self, name: str, namespace: str | None = None) -> TypeDef | None:
        if namespace and f"{namespace}.{name}" in self.types:
            return self.types[f"{namespace}.{name}"]
        if name in self.types:
            return self.types[name]
        local = [t for t in self.types.values() if t.name == name]
        if local:
            return local[0]
        return self.catalog.find(name, namespace) if self.catalog else None

    def search_types(self, query: str = "", kind: str | None = None, library: str | None = None,
                     folder: str | None = None) -> list[TypeDef]:
        q = query.lower()
        out = []
        for t in self.types.values():
            if kind and t.kind != kind:
                continue
            if library is not None and (t.project or "") != library:
                continue
            if folder and not (t.folder or "").startswith(folder):
                continue
            if q and q not in t.name.lower() and q not in (t.comment or "").lower():
                continue
            out.append(t)
        return sorted(out, key=lambda t: (t.kind, t.qualified_name))

    def abs(self, rel: str) -> Path:
        return safety.check_readable(self.root / rel, [self.root])


def _parse_sln(sln: Path) -> list[SolutionProject]:
    text = sln.read_text(encoding="utf-8-sig", errors="replace")
    out = []
    for m in _SLN_PROJECT.finditer(text):
        out.append(SolutionProject(
            name=m["name"],
            kind=PROJECT_KINDS.get(m["type"].upper(), "other"),
            path=m["path"].replace("\\", "/"),
            guid=m["guid"],
        ))
    return out


def find_sln(path: Path) -> Path:
    if path.is_file() and path.suffix == ".sln":
        return path
    slns = sorted(path.glob("*.sln"))
    if not slns:
        raise FileNotFoundError(f"No .sln file in {path}")
    return slns[0]


def load_solution(path: str | Path, catalog: Catalog | None = None,
                  library_store: Path | None = None) -> Solution:
    sln = find_sln(Path(path).expanduser().resolve())
    sol = Solution(root=sln.parent, sln=sln, projects=_parse_sln(sln))

    for proj in sol.projects:
        f = sol.root / proj.path
        if not f.exists():
            sol.warnings.append(f"Project file missing: {proj.path}")
            continue
        try:
            proj.msbuild = load_project(f)
        except Exception as e:  # noqa: BLE001
            sol.warnings.append(f"Cannot read {proj.path}: {e}")

    for proj in sol.projects_of("iec61499"):
        if proj.msbuild:
            _load_iec_project(sol, proj)

    if catalog is not None:
        sol.catalog = catalog
    elif library_store is not None and library_store.is_dir():
        sol.catalog = Catalog.from_store(library_store, sol.references)
    return sol


def _load_iec_project(sol: Solution, proj: SolutionProject) -> None:
    ms = proj.msbuild
    pdir = ms.dir
    prel = proj.path.rsplit("/", 1)[0] + "/" if "/" in proj.path else ""
    for name, version in ms.references().items():
        sol.references.setdefault(name, version)

    tree = FolderTree()
    load_folders_xml(pdir.parent / "General" / "Folders.xml", tree)
    sol.folders[proj.name] = tree

    for item in ms.items_of("Compile"):
        iec_type = item.metadata.get("IEC61499Type")
        kind = DFB_KINDS.get(iec_type or "")
        if not kind:
            continue
        rel = prel + item.posix
        if safety.is_sensitive(rel):
            continue
        f = sol.root / rel
        if not f.exists():
            sol.warnings.append(f"Registered file missing: {rel}")
            continue
        try:
            td = load_type(f, rel)
        except Exception as e:  # noqa: BLE001
            sol.warnings.append(f"Cannot parse {rel}: {e}")
            continue
        # The .dfbproj kind is authoritative, except that a CAT's HMI interface (<Cat>_HMI.fbt)
        # is also registered as "CAT" although it is a service-interface FB.
        td.kind = "cat_hmi" if kind == "cat" and td.kind == "sifb" else kind
        td.folder = item.metadata.get("Parent")
        td.project = proj.library
        if td.folder and td.kind in KIND_TO_CATEGORY:
            tree.add(KIND_TO_CATEGORY[td.kind], td.folder, td.name)
        sol.types[td.qualified_name] = td

    for item in ms.items_of("None", "Content"):
        if item.metadata.get("IEC61499Type") == "CAT" and item.posix.endswith(".cfg"):
            rel = prel + item.posix
            try:
                cfg = parse_cfg(sol.root / rel, rel)
            except Exception as e:  # noqa: BLE001
                sol.warnings.append(f"Cannot parse {rel}: {e}")
                continue
            td = next((t for t in sol.types.values() if t.path == prel + (cfg.cat_file or "")), None)
            sol.cats[td.qualified_name if td else cfg.name] = cfg

    # Older CATs do not register their .cfg with IEC61499Type=CAT.
    for td in list(sol.types.values()):
        if td.kind == "cat" and td.qualified_name not in sol.cats and td.path:
            cfg_path = sol.root / td.path.replace(".fbt", ".cfg")
            if cfg_path.exists():
                rel = td.path.replace(".fbt", ".cfg")
                sol.cats[td.qualified_name] = parse_cfg(cfg_path, rel)

    for system in load_systems(pdir, prel):
        sol.systems.append(system)
        for dev_item in ms.items_of("Compile"):
            if dev_item.metadata.get("IEC61499Type") == "SystemDevice" and dev_item.metadata.get("Parent"):
                for dev in system.devices:
                    if dev.path == prel + dev_item.posix:
                        dev.folder = dev_item.metadata["Parent"]


# -- reference resolution --------------------------------------------------------


@dataclass
class ResolvedEnd:
    node: str | None  # instance name, or None for a boundary pin of the enclosing type
    pin: str
    raw: str
    resolved: bool = True

    def __str__(self) -> str:
        return f"{self.node}.{self.pin}" if self.node else self.pin


def resolve_reference(ref: str, network: Network, sol: Solution, owner: TypeDef | None = None) -> ResolvedEnd:
    """Turn a stored reference (`$<node>.<pin>`, `$<pinId>`, `node.pin`) into names.

    `<node>` matches an instance/boundary-pin ID first, then a name. `<pin>` matches the
    target type's pin ID first, then its name.
    """
    body = ref[1:] if ref.startswith("$") else ref
    by_id = {i.id: i for i in network.instances if i.id}
    by_name = {i.name: i for i in network.instances}
    pins_by_id = {p.id: p for p in network.pins if p.id}
    pins_by_name = {p.name: p for p in network.pins}

    if "." not in body:
        pin = pins_by_id.get(body) or pins_by_name.get(body)
        return ResolvedEnd(None, pin.name if pin else body, ref, pin is not None)

    node_key, pin_key = body.split(".", 1)
    inst = by_id.get(node_key) or by_name.get(node_key)
    if inst is None and owner is not None:
        # Inside a composite, an adapter pin of the enclosing type behaves like an instance
        # of the adapter type: `$<adapter decl ID or name>.<adapter event/var ID or name>`.
        itf = owner.interface
        decl = next((a for a in itf.adapter_inputs + itf.adapter_outputs if node_key in (a.id, a.name)), None)
        if decl is not None:
            at = sol.find_type(decl.type, decl.namespace)
            pin_name = at.interface.pin_name(pin_key) if at else None
            return ResolvedEnd(decl.name, pin_name or pin_key, ref, pin_name is not None or at is None)
    if inst is None:
        # A boundary pin name containing no dot cannot reach here; treat as unresolved.
        return ResolvedEnd(node_key, pin_key, ref, False)
    td = sol.find_type(inst.type, inst.namespace)
    pin_name = td.interface.pin_name(pin_key) if td else None
    return ResolvedEnd(inst.name, pin_name or pin_key, ref, pin_name is not None or td is None)


def resolve_parameters(inst, sol: Solution) -> dict[str, str]:
    td = sol.find_type(inst.type, inst.namespace)
    out = {}
    for key, value in inst.parameters.items():
        k = key[1:] if key.startswith("$") else key
        out[(td.interface.pin_name(k) if td else None) or k] = value
    return out
