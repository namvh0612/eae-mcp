"""Edit .NET HMI canvases: create a canvas, place CAT symbols, change or remove objects.

Canvases are HMI/<Canvas>.cnv.cs (+ .Designer.cs, .resx), registered in HMI.csproj with <Canvas>true</Canvas>
and listed in HMI/CanvasesResolutionList.xml. A placed CAT symbol is an object of type
<root>.Symbols.<Cat>.<symbol> whose TagName is the application instance ID.
Only InitializeComponent() is edited, through the constrained model in designer.py.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from ..io import xmlrt
from ..project import writer as w
from ..project.changes import ChangeSet, FileChange
from ..project.edit import EditError
from ..project.solution import Solution
from ..project.types import child, children, local
from . import codegen as cg
from . import designer as ds
from .ehmi_edit import _app_instance, etree_ns

CANVAS_NS = "HMI.Main.Canvases"
BOM = b"\xef\xbb\xbf"
ALL_RIGHTS = "((uint)(4294967295u))"


def _hmi_project(sol: Solution):
    projects = sol.projects_of("hmi")
    proj = next((p for p in projects if p.path.lower().startswith("hmi/")), projects[0] if projects else None)
    if proj is None:
        raise EditError("This solution has no .NET HMI project.")
    return proj


def _canvas_files(sol: Solution, canvas: str) -> tuple[str, str]:
    """Return (Designer.cs, .resx) solution-relative paths of a canvas."""
    proj = _hmi_project(sol)
    pdir = proj.path.rsplit("/", 1)[0]
    canvases = [i.posix for i in (proj.msbuild.items_of("Compile") if proj.msbuild else [])
                if i.posix.endswith(".cnv.cs") and i.metadata.get("Canvas") == "true"]
    hits = [c for c in canvases if Path(c).name[: -len(".cnv.cs")] == canvas or c[: -len(".cnv.cs")] == canvas]
    if not hits:
        names = ", ".join(Path(c).name[: -len(".cnv.cs")] for c in canvases) or "none"
        raise EditError(f"No .NET HMI canvas '{canvas}'. Canvases: {names}.")
    base = f"{pdir}/{hits[0][: -len('.cnv.cs')]}"
    return f"{base}.cnv.Designer.cs", f"{base}.cnv.resx"


def _load(sol: Solution, rel: str) -> tuple[ds.Designer, bytes, bool]:
    raw = (sol.root / rel).read_bytes()
    try:
        return ds.parse(raw.decode("utf-8-sig")), raw, raw.startswith(BOM)
    except ds.DesignerError as e:
        raise EditError(f"{rel}: {e}") from e


def _save(cs: ChangeSet, rel: str, d: ds.Designer, raw: bytes, bom: bool) -> None:
    new = (BOM if bom else b"") + ds.dumps(d).encode("utf-8")
    if new != raw:
        cs.changes[rel] = FileChange(rel, raw, new)


_META = '  <metadata name="{n}.Name" xml:space="preserve">\r\n    <value>{n}</value>\r\n  </metadata>\r\n'


def _resx_add(cs: ChangeSet, rel: str, name: str) -> None:
    path = cs.root / rel
    if not path.exists():
        return
    raw = cs.changes[rel].new if rel in cs.changes else path.read_bytes()
    text = raw.decode("utf-8-sig")
    entry = _META.format(n=name)
    names = [m for m in re.finditer(r'  <metadata name="([^"$][^"]*)\.Name" xml:space="preserve">\r\n.*?</metadata>\r\n',
                                    text, re.S)]
    if names:
        at = names[-1].end()
    else:
        m = re.search(r'  <metadata name="\$this\.', text) or re.search(r"</root>", text)
        at = m.start()
    new = (BOM if raw.startswith(BOM) else b"") + (text[:at] + entry + text[at:]).encode("utf-8")
    cs.changes[rel] = FileChange(rel, path.read_bytes(), new)


def _resx_remove(cs: ChangeSet, rel: str, name: str) -> None:
    path = cs.root / rel
    if not path.exists():
        return
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    new_text = text.replace(_META.format(n=name), "")
    if new_text != text:
        cs.changes[rel] = FileChange(rel, raw, (BOM if raw.startswith(BOM) else b"") + new_text.encode("utf-8"))


# -- operations ------------------------------------------------------------------------------


def place_symbol(sol: Solution, canvas: str, instance: str, symbol: str | None = None, x: float | None = None,
                 y: float | None = None, name: str | None = None, application: str | None = None) -> ChangeSet:
    """Show a CAT instance on a .NET HMI canvas with one of the CAT's symbols (not a faceplate)."""
    designer_rel, resx_rel = _canvas_files(sol, canvas)
    _app, fb = _app_instance(sol, instance, application)
    td = sol.find_type(fb.type, fb.namespace)
    cat = sol.cats.get(td.qualified_name) if td else None
    if cat is None:
        raise EditError(f"'{instance}' is a {fb.type}, not a CAT; only CAT instances have symbols.")
    symbols = [s for s in cat.symbols if s.technology == "hmi" and not s.is_faceplate]
    if not symbols:
        raise EditError(f"CAT {td.name} has no .NET HMI symbol.")
    sym = next((s for s in symbols if s.name == symbol), None) if symbol else symbols[0]
    if sym is None:
        raise EditError(f"CAT {td.name} has no .NET symbol '{symbol}'. Symbols: {', '.join(s.name for s in symbols)}.")
    d, raw, bom = _load(sol, designer_rel)
    name = name or instance
    w.check_identifier(name, "object name")
    if name in d.objects:
        i = 1
        while f"{name}_{i}" in d.objects:
            i += 1
        name = f"{name}_{i}"
    n = len(d.objects)
    x = 16 + 20 * n if x is None else x
    y = 8 + 20 * n if y is None else y
    type_ = f"{cg.ns_root(td.namespace)}.Symbols.{td.name}.{sym.name}"
    ds.add_object(d, name, type_, [("DesignMatrix", ds.matrix(x, y)), ("Name", ds.string(name)),
                                   ("SecurityToken", ALL_RIGHTS), ("TagName", ds.string(fb.id))])
    cs = ChangeSet(sol.root, f"place {instance} ({td.name}.{sym.name}) on HMI canvas {canvas}")
    _save(cs, designer_rel, d, raw, bom)
    _resx_add(cs, resx_rel, name)
    return cs


def remove_object(sol: Solution, canvas: str, name: str) -> ChangeSet:
    designer_rel, resx_rel = _canvas_files(sol, canvas)
    d, raw, bom = _load(sol, designer_rel)
    try:
        ds.remove_object(d, name)
    except ds.DesignerError as e:
        raise EditError(str(e)) from e
    cs = ChangeSet(sol.root, f"remove {name} from HMI canvas {canvas}")
    _save(cs, designer_rel, d, raw, bom)
    _resx_remove(cs, resx_rel, name)
    return cs


_EXPR = re.compile(r"^[^;\r\n{}]*(\{[^;\r\n{}]*\})?[^;\r\n{}]*$")


def update_object(sol: Solution, canvas: str, name: str, properties: dict[str, str | None] | None = None,
                  x: float | None = None, y: float | None = None) -> ChangeSet:
    """Set C# property values of a canvas object (single-line assignments, e.g. {"Visible": "false"}).
    x/y move a symbol (DesignMatrix translation) or a shape (Bounds origin)."""
    designer_rel, _ = _canvas_files(sol, canvas)
    d, raw, bom = _load(sol, designer_rel)
    sec = next((s for s in d.sections if s.name == name), None)
    if sec is None:
        raise EditError(f"No object '{name}' on {canvas}. Objects: {', '.join(d.objects) or 'none'}.")
    props = dict(properties or {})
    for prop, value in props.items():
        if value is not None and not _EXPR.match(value):
            raise EditError(f"{prop}: give one C# expression without ';' or line breaks.")
    if x is not None or y is not None:
        current = {p: sec.lines[i] for p, i in sec.prop_lines()}
        if "DesignMatrix" in current:
            m = re.search(r"Matrix2D\(([^)]*)\)", current["DesignMatrix"])
            parts = [p.strip() for p in m.group(1).split(",")]
            if x is not None:
                parts[4] = ds.num(x)
            if y is not None:
                parts[5] = ds.num(y)
            props["DesignMatrix"] = f"new NxtControl.Drawing.Matrix2D({', '.join(parts)})"
        elif "Bounds" in current:
            m = re.search(r"RectF\((.*)\);", current["Bounds"])
            vals = re.findall(r"\(\(float\)\(([^)]*)\)\)", m.group(1))
            if x is not None:
                vals[0] = ds.num(x)
            if y is not None:
                vals[1] = ds.num(y)
            props["Bounds"] = "new NxtControl.Drawing.RectF(" + ", ".join(f"((float)({v}))" for v in vals) + ")"
        else:
            raise EditError(f"{name} has neither DesignMatrix nor Bounds; set the position property directly.")
    if not props:
        raise EditError("Nothing to change.")
    try:
        for prop, value in props.items():
            ds.set_property(d, name, prop, value)
    except ds.DesignerError as e:
        raise EditError(str(e)) from e
    cs = ChangeSet(sol.root, f"update {name} on HMI canvas {canvas}")
    _save(cs, designer_rel, d, raw, bom)
    return cs


def create_canvas(sol: Solution, name: str, resolution: str | None = None, title: str = "",
                  now: _dt.datetime | None = None) -> ChangeSet:
    """New .NET HMI canvas, added to the top level of a canvas resolution."""
    w.check_identifier(name, "canvas name")
    proj = _hmi_project(sol)
    pdir = proj.path.rsplit("/", 1)[0]
    if (sol.root / f"{pdir}/{name}.cnv.cs").exists():
        raise EditError(f"A canvas '{name}' already exists.")
    res_rel = f"{pdir}/CanvasesResolutionList.xml"
    cs = ChangeSet(sol.root, f"create HMI canvas {name}")
    width, height = 1280, 730
    if (sol.root / res_rel).exists():
        xf = cs.doc(res_rel)
        resolutions = children(xf.root, "CanvasResolution")
        if resolution:
            res = next((r for r in resolutions if r.get("Name") == resolution), None)
            if res is None:
                raise EditError(f"No resolution '{resolution}': {', '.join(r.get('Name', '') for r in resolutions)}.")
        else:
            res = next((r for r in resolutions if r.get("Width") not in (None, "-1")),
                       resolutions[0] if resolutions else None)
        if res is not None:
            if any(c.get("Name") == name for c in xf.root.iter() if isinstance(c.tag, str) and local(c) == "Canvas"):
                raise EditError(f"A canvas '{name}' is already listed in {res_rel}.")
            if int(res.get("WorkAreaWidth", "-1")) > 0:
                width, height = int(res.get("WorkAreaWidth")), int(res.get("WorkAreaHeight"))
            ns = etree_ns(xf.root)
            topo = child(res, "Topology")
            if topo is None:
                topo = w._el("Topology", [("Name", "Default")], ns=ns)
                xmlrt.insert_child(res, topo)
            canvases = child(topo, "Canvases")
            if canvases is None:
                canvases = w._el("Canvases", ns=ns)
                xmlrt.insert_child(topo, canvases)
            el = w._el("Canvas", [("Name", name), ("Title", title), ("Tooltip", ""),
                                  ("Instance", f"{CANVAS_NS}.{name}")], ns=ns)
            w._sub(el, "Children")
            xmlrt.insert_child(canvases, el)
    now = now or _dt.datetime.now()
    vals = {"DATE": f"{now.month}/{now.day}/{now.year}", "TIME": now.strftime("%I:%M %p").lstrip("0"),
            "NAME": name, "W": str(width), "H": str(height)}
    for ext in ("cnv.cs", "cnv.Designer.cs", "cnv.resx"):
        data = w.template(f"hmi/canvas.{ext}")
        for k, v in vals.items():
            data = data.replace(f"@@{k}@@".encode(), v.encode())
        cs.create(f"{pdir}/{name}.{ext}", data)
    p = cs.doc(proj.path)
    cnv = f"{name}.cnv.cs"
    w.add_project_item(p, "Compile", cnv, [("Canvas", "true")])
    w.add_project_item(p, "Compile", f"{name}.cnv.Designer.cs", [("DependentUpon", cnv)])
    w.add_project_item(p, "EmbeddedResource", f"{name}.cnv.resx", [("DependentUpon", cnv)])
    cs.commit_docs()
    return cs
