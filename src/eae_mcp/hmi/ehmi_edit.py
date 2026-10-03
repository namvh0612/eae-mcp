"""Edit eHMI (web) canvases: create a canvas on a device, place/remove CAT symbols.

Canvases live per device in WEB/<device id>/ (<Canvas>.cnv.ts, .user.cs, .cnv.json) and are listed in
WEB/<device id>/WebCanvasesResolutionList.xml. A placed symbol is a .cnv.json object whose `type` is the
symbol class and whose `tagName` is the ID of the CAT instance in the application layer.
"""

from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass

from ..io import jsonrt, xmlrt
from ..project import writer as w
from ..project.changes import ChangeSet
from ..project.edit import EditError
from ..project.solution import Solution
from ..project.system import Device
from ..project.types import child, children, local

CANVAS_NS = "WEB.Main.Canvases"


def _web_project(sol: Solution):
    projects = sol.projects_of("web")
    proj = next((p for p in projects if p.path.lower().startswith("web/")), projects[0] if projects else None)
    if proj is None:
        raise EditError("This solution has no WEB (eHMI) project.")
    return proj


def _devices(sol: Solution) -> list[Device]:
    return [d for s in sol.systems for d in s.devices]


def find_device(sol: Solution, device: str) -> Device:
    found = [d for d in _devices(sol) if device in (d.name, d.id)]
    if not found:
        names = ", ".join(d.name for d in _devices(sol)) or "none"
        raise EditError(f"No device '{device}'. Devices: {names}.")
    return found[0]


@dataclass
class CanvasRef:
    device: Device
    name: str
    json_rel: str  # solution-relative path of <Canvas>.cnv.json


def find_canvas(sol: Solution, canvas: str) -> CanvasRef:
    """'Canvas1' or '<Device>/Canvas1'."""
    web_dir = _web_project(sol).path.rsplit("/", 1)[0]
    device_part, _, name = canvas.rpartition("/")
    hits = []
    for d in _devices(sol):
        if device_part and device_part not in (d.name, d.id):
            continue
        rel = f"{web_dir}/{d.id}/{name}.cnv.json"
        if (sol.root / rel).exists():
            hits.append(CanvasRef(d, name, rel))
    if not hits:
        raise EditError(f"No eHMI canvas '{canvas}'. eae_hmi_list shows the eHMI canvases per device.")
    if len(hits) > 1:
        raise EditError(f"Canvas '{name}' exists on several devices ({', '.join(h.device.name for h in hits)}); "
                        "use '<Device>/<Canvas>'.")
    return hits[0]


def _app_instance(sol: Solution, instance: str, application: str | None):
    hits = []
    for s in sol.systems:
        for app in s.applications:
            if application and app.name != application:
                continue
            for layer in app.layers:
                for fb in (layer.network.instances if layer.network else []):
                    if fb.name == instance:
                        hits.append((app, fb))
    if not hits:
        raise EditError(f"No instance '{instance}' in {'application ' + application if application else 'any application'}.")
    if len(hits) > 1:
        raise EditError(f"'{instance}' exists in several applications; pass application=.")
    return hits[0]


def _mapped_to(sol: Solution, fb_id: str, device: Device) -> bool:
    return any(i.mapping == fb_id for r in device.resources for i in (r.network.instances if r.network else []))


def place_symbol(sol: Solution, canvas: str, instance: str, symbol: str | None = None, left: float | None = None,
                 top: float | None = None, width: float | None = None, height: float | None = None,
                 name: str | None = None, application: str | None = None) -> ChangeSet:
    """Show a CAT instance on an eHMI canvas with one of the CAT's web symbols."""
    ref = find_canvas(sol, canvas)
    _app, fb = _app_instance(sol, instance, application)
    td = sol.find_type(fb.type, fb.namespace)
    cat = sol.cats.get(td.qualified_name) if td else None
    if cat is None:
        raise EditError(f"'{instance}' is a {fb.type}, not a CAT; only CAT instances have symbols.")
    web = [s for s in cat.symbols if s.technology == "ehmi"]
    if not web:
        raise EditError(f"CAT {td.name} has no eHMI symbol. .NET symbols: "
                        f"{', '.join(s.name for s in cat.symbols) or 'none'}.")
    sym = next((s for s in web if s.name == symbol), None) if symbol else web[0]
    if sym is None:
        raise EditError(f"CAT {td.name} has no eHMI symbol '{symbol}'. eHMI symbols: {', '.join(s.name for s in web)}.")
    proj_dir = cat.cfg_file.rsplit("/", 2)[0]
    sym_json = os.path.normpath(f"{proj_dir}/{sym.file}").replace("\\", "/").replace(".sym.ts", ".sym.json")
    design = {}
    if (sol.root / sym_json).exists():
        design = jsonrt.load(sol.root / sym_json).data.get("_design_", {})

    cs = ChangeSet(sol.root, f"place {instance} ({td.name}.{sym.name}) on eHMI canvas {ref.device.name}/{ref.name}")
    jf = jsonrt.load(sol.root / ref.json_rel)
    objects = jf.data.setdefault("objects", [])
    names = {o.get("name") for o in objects}
    if name is None:
        i = 1
        while f"symbol{i}" in names:
            i += 1
        name = f"symbol{i}"
    elif name in names:
        raise EditError(f"Canvas {ref.name} already has an object named '{name}'.")
    if top is None:
        top = max((o.get("top", 0) + o.get("height", 0) for o in objects), default=0) + 10
    ns = "WEB.Main" if (td.namespace or "Main") == "Main" else td.namespace
    objects.append({"type": f"{ns}.Symbols.{td.name}.{sym.name}", "name": name,
                    "left": left if left is not None else 10, "top": top,
                    "width": width or design.get("width", 150), "height": height or design.get("height", 100),
                    "tagName": fb.id})
    cs.changes[ref.json_rel] = _change(ref.json_rel, jf)
    if not _mapped_to(sol, fb.id, ref.device):
        cs.warnings.append(f"warning: {instance} is not mapped to {ref.device.name}; the symbol shows no live values "
                           f"until it is (eae_map_to_resource).")
    return cs


def remove_object(sol: Solution, canvas: str, name: str) -> ChangeSet:
    ref = find_canvas(sol, canvas)
    jf = jsonrt.load(sol.root / ref.json_rel)
    objects = jf.data.get("objects", [])
    keep = [o for o in objects if o.get("name") != name]
    if len(keep) == len(objects):
        raise EditError(f"Canvas {ref.name} has no object '{name}'. Objects: "
                        f"{', '.join(o.get('name', '?') for o in objects) or 'none'}.")
    jf.data["objects"] = keep
    cs = ChangeSet(sol.root, f"remove {name} from eHMI canvas {ref.device.name}/{ref.name}")
    cs.changes[ref.json_rel] = _change(ref.json_rel, jf)
    return cs


def _change(rel: str, jf: jsonrt.JsonFile):
    from ..project.changes import FileChange

    return FileChange(rel, jf.original, jsonrt.dumps(jf))


def create_canvas(sol: Solution, device: str, name: str, resolution: str | None = None, title: str = "",
                  now: _dt.datetime | None = None) -> ChangeSet:
    """New eHMI canvas on a device, added to a canvas resolution's topology (top level)."""
    w.check_identifier(name, "canvas name")
    dev = find_device(sol, device)
    proj = _web_project(sol)
    web_dir = proj.path.rsplit("/", 1)[0]
    res_rel = f"{web_dir}/{dev.id}/WebCanvasesResolutionList.xml"
    if not (sol.root / res_rel).exists():
        raise EditError(f"Device {dev.name} has no eHMI canvases yet. Create the first one in EAE "
                        "(eHMI › Add canvas on the device) so EAE sets up its resolution list; then this tool can add more.")
    cs = ChangeSet(sol.root, f"create eHMI canvas {name} on {dev.name}")
    xf = cs.doc(res_rel)
    resolutions = children(xf.root, "CanvasResolution")
    if resolution:
        res = next((r for r in resolutions if r.get("Name") == resolution), None)
        if res is None:
            raise EditError(f"No resolution '{resolution}' on {dev.name}: {', '.join(r.get('Name', '') for r in resolutions)}.")
    else:
        res = next((r for r in resolutions if r.get("Width") not in (None, "-1")), resolutions[0] if resolutions else None)
        if res is None:
            raise EditError(f"{res_rel} has no CanvasResolution.")
    all_names = {c.get("Name") for c in xf.root.iter() if isinstance(c.tag, str) and local(c) == "Canvas"}
    if name in all_names:
        raise EditError(f"Device {dev.name} already has a canvas '{name}'.")
    topo = child(res, "Topology")
    canvases = child(topo, "Canvases")
    ns = etree_ns(xf.root)
    if canvases is None:
        canvases = w._el("Canvases", ns=ns)
        xmlrt.insert_child(topo, canvases)
    el = w._el("Canvas", [("Name", name), ("Title", title), ("Tooltip", ""), ("Instance", f"{CANVAS_NS}.{name}")], ns=ns)
    w._sub(el, "Children")
    xmlrt.insert_child(canvases, el)

    width = int(res.get("WorkAreaWidth", "-1"))
    height = int(res.get("WorkAreaHeight", "-1"))
    now = now or _dt.datetime.now()
    stamp = {"DATE": f"{now.month}/{now.day}/{now.year}", "TIME": now.strftime("%I:%M %p").lstrip("0"), "NAME": name}
    base = f"{web_dir}/{dev.id}/{name}"
    for ext in ("cnv.ts", "user.cs"):
        data = w.template(f"ehmi/canvas.{ext}")
        for k, v in stamp.items():
            data = data.replace(f"@@{k}@@".encode(), v.encode())
        cs.create(f"{base}.{ext}", data)
    body = {"objects": [], "background": "CanvasBackColor", "overlay": "Transparent"}
    if width > 0 and height > 0:
        body.update(width=width, height=height)
    cs.create(f"{base}.cnv.json", jsonrt.new(body))
    p = cs.doc(proj.path)
    ts = f"{name}.cnv.ts"
    w.add_project_item(p, "Compile", f"{dev.id}\\{name}.user.cs", [("DependentUpon", ts)])
    w.add_project_item(p, "None", f"{dev.id}\\{ts}", [("Canvas", "true")])
    w.add_project_item(p, "EmbeddedResource", f"{dev.id}\\{name}.cnv.json", [("DependentUpon", ts)])
    cs.commit_docs()
    return cs


def etree_ns(el) -> str | None:
    from lxml import etree

    return etree.QName(el).namespace


def update_object(sol: Solution, canvas: str, name: str, properties: dict) -> ChangeSet:
    """Set JSON properties of a canvas object (left, top, width, height, tagName, text, colors …);
    a value of null removes the property."""
    if not properties:
        raise EditError("No properties given.")
    if "type" in properties or "name" in properties:
        raise EditError("'type' and 'name' cannot be changed; remove the object and place a new one.")
    ref = find_canvas(sol, canvas)
    jf = jsonrt.load(sol.root / ref.json_rel)
    obj = next((o for o in jf.data.get("objects", []) if o.get("name") == name), None)
    if obj is None:
        raise EditError(f"Canvas {ref.name} has no object '{name}'.")
    for key, value in properties.items():
        if value is None:
            obj.pop(key, None)
        else:
            obj[key] = value
    cs = ChangeSet(sol.root, f"update {name} on eHMI canvas {ref.device.name}/{ref.name}")
    cs.changes[ref.json_rel] = _change(ref.json_rel, jf)
    return cs
