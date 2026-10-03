"""Read-only access to .NET HMI (HMI/*.csproj) and eHMI (WEB/*.htmlproj) content.

.NET HMI: canvases/symbols/faceplates are C# designer files. We extract objects from
`InitializeComponent()` with a constrained pattern parser (no C# compiler needed).
eHMI: canvases/symbols are JSON (`.cnv.json`, `.sym.json`) plus TypeScript logic.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..io import xmlrt
from ..project.solution import Solution
from ..project.types import children

_NEW_OBJECT = re.compile(r"this\.(\w+)\s*=\s*new\s+([\w.<>,\s]+?)\(\)\s*;")
_PROPERTY = re.compile(r"this\.(\w+)\.(\w+)\s*=\s*(.+?);\s*$", re.M)
_SELF_PROPERTY = re.compile(r"^\s*this\.(Size|SymbolSize|Bounds)\s*=\s*(.+?);\s*$", re.M)
_INTERESTING = {"TagName", "Text", "Bounds", "DesignMatrix", "Name", "Visible"}


@dataclass
class HmiObject:
    name: str
    type: str
    tag_name: str | None = None
    properties: dict[str, str] = field(default_factory=dict)


@dataclass
class HmiDocument:
    """A .NET HMI canvas, symbol or faceplate, or an eHMI canvas/symbol/graphic."""

    name: str
    kind: str  # canvas | resolution | symbol | faceplate | graphic
    technology: str  # hmi | ehmi
    path: str
    cat: str | None = None
    device: str | None = None  # eHMI canvases live per device
    size: str | None = None
    objects: list[HmiObject] = field(default_factory=list)
    mapping: dict | None = None  # .cnv.xml (HMI symbol ↔ CAT HMI interface)


@dataclass
class Resolution:
    name: str
    technology: str
    start_canvas: str | None
    size: str
    canvases: list[str] = field(default_factory=list)
    device: str | None = None


def _clean(value: str) -> str:
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def parse_designer(text: str) -> tuple[list[HmiObject], str | None]:
    body = text.split("InitializeComponent()", 1)[-1]
    objects: dict[str, HmiObject] = {}
    for name, typ in _NEW_OBJECT.findall(body):
        objects[name] = HmiObject(name=name, type=" ".join(typ.split()))
    for name, prop, value in _PROPERTY.findall(body):
        obj = objects.get(name)
        if obj is None:
            continue
        if prop == "TagName":
            obj.tag_name = _clean(value)
        elif prop in _INTERESTING:
            obj.properties[prop] = _clean(value)
    size = None
    for prop, value in _SELF_PROPERTY.findall(body):
        if prop in ("Size", "SymbolSize"):
            m = re.search(r"\((\d+),\s*(\d+)\)", value)
            size = f"{m[1]}x{m[2]}" if m else value
    return list(objects.values()), size


def _mapping(path: Path) -> dict | None:
    """Parse a symbol's .cnv.xml mapping to the CAT HMI interface."""
    if not path.exists():
        return None
    root = xmlrt.load(path).root
    out: dict = {}
    for section in children(root):
        items = []
        for el in children(section):
            entry = {k: v for k, v in el.attrib.items()}
            if el.text and el.text.strip():
                entry["with"] = el.text.strip()
            items.append(entry)
        out[etree_local(section)] = items
    return out


def etree_local(el) -> str:
    from lxml import etree
    return etree.QName(el).localname


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _json_objects(data: dict) -> list[HmiObject]:
    out = []
    for o in data.get("objects", []):
        props = {k: str(o[k]) for k in ("left", "top", "width", "height", "text") if k in o}
        out.append(HmiObject(name=o.get("name", ""), type=o.get("type", ""), tag_name=o.get("tagName"),
                             properties=props))
    return out


@dataclass
class HmiIndex:
    documents: list[HmiDocument] = field(default_factory=list)
    resolutions: list[Resolution] = field(default_factory=list)

    def find(self, name: str, technology: str | None = None) -> list[HmiDocument]:
        return [d for d in self.documents
                if (technology is None or d.technology == technology)
                and (d.name == name or d.path.endswith(name) or f"{d.cat}.{d.name}" == name)]


def _cat_of(path: str) -> str | None:
    parts = path.split("/")
    return parts[-2] if len(parts) >= 2 and parts[-2] not in ("HMI", "WEB") else None


def load_hmi(sol: Solution) -> HmiIndex:
    idx = HmiIndex()
    faceplate_files = {s.file.split("/")[-1]
                       for c in sol.cats.values() for s in c.symbols if s.is_faceplate}
    device_names = {d.id: d.name for s in sol.systems for d in s.devices}

    for proj in sol.projects_of("hmi"):
        if not proj.msbuild:
            continue
        pdir = proj.msbuild.dir
        prel = proj.path.rsplit("/", 1)[0]
        resolutions = _resolutions(pdir / "CanvasesResolutionList.xml", "hmi", None)
        start_classes = {r.start_canvas.rsplit(".", 1)[-1] for r in resolutions if r.start_canvas}
        for item in proj.msbuild.items_of("Compile"):
            if not item.posix.endswith(".cnv.cs"):
                continue
            rel = f"{prel}/{item.posix}"
            designer = pdir / item.posix.replace(".cnv.cs", ".cnv.Designer.cs")
            if not designer.exists():
                continue
            objects, size = parse_designer(designer.read_text(encoding="utf-8-sig", errors="replace"))
            base = Path(item.posix).name[: -len(".cnv.cs")]
            cat = _cat_of(rel)
            if item.metadata.get("Canvas") == "true":
                kind = "canvas"
            elif base in start_classes or base.startswith("CanvasResolution"):
                kind = "resolution"  # frame canvas of a resolution (header, navigation, work area)
            elif Path(item.posix).name in faceplate_files:
                kind = "faceplate"
            elif cat:
                kind = "symbol"
            else:
                kind = "graphic"  # project-level reusable symbol
            name = base[len(cat) + 1:] if cat and base.startswith(cat + "_") else base
            idx.documents.append(HmiDocument(
                name=name, kind=kind, technology="hmi", path=rel, cat=cat, size=size, objects=objects,
                mapping=_mapping(pdir / item.posix.replace(".cnv.cs", ".cnv.xml")),
            ))
        idx.resolutions += resolutions

    for proj in sol.projects_of("web"):
        if not proj.msbuild:
            continue
        pdir = proj.msbuild.dir
        prel = proj.path.rsplit("/", 1)[0]
        for f in sorted(pdir.rglob("*.json")):
            relp = f.relative_to(pdir).as_posix()
            if "/bin/" in f"/{relp}" or "/obj/" in f"/{relp}":
                continue
            if f.name.endswith(".cnv.json"):
                kind, name = "canvas", f.name[: -len(".cnv.json")]
            elif f.name.endswith(".sym.json"):
                kind, name = "symbol", f.name[: -len(".sym.json")]
            else:
                continue
            folder = relp.split("/")[0] if "/" in relp else None
            device = device_names.get(folder) if folder else None
            cat = folder if folder and not device else None
            if kind == "symbol" and not cat:
                kind = "graphic"
            if cat and name.startswith(cat + "_"):
                name = name[len(cat) + 1:]
            try:
                data = _json(f)
            except ValueError:
                continue
            size = data.get("_design_", data)
            idx.documents.append(HmiDocument(
                name=name, kind=kind, technology="ehmi", path=f"{prel}/{relp}", cat=cat,
                device=device or (folder if kind == "canvas" else None),
                size=f"{size.get('width')}x{size.get('height')}" if size.get("width") else None,
                objects=_json_objects(data),
            ))
        for f in sorted(pdir.glob("*/WebCanvasesResolutionList.xml")):
            idx.resolutions += _resolutions(f, "ehmi", device_names.get(f.parent.name, f.parent.name))
    return idx


def _resolutions(path: Path, technology: str, device: str | None) -> list[Resolution]:
    if not path.exists():
        return []
    out = []
    for res in children(xmlrt.load(path).root, "CanvasResolution"):
        canvases = [c.get("Name", "") for c in res.iter() if isinstance(c.tag, str)
                    and etree_local(c) == "Canvas"]
        out.append(Resolution(
            name=res.get("Name", ""), technology=technology, start_canvas=res.get("StartCanvasClass"),
            size=f"{res.get('Width')}x{res.get('Height')}", canvases=canvases, device=device,
        ))
    return out
