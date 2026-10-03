"""Expose (or un-expose) a variable to OPC UA the way EAE 26 does.

EAE writes the exposure twice, as an `Exposed` attribute keyed by a dotted path of IDs:
- in the application layer's opcua.xml (`UID` = application ID, path starts with the layer FB ID),
- in every resource the instance is mapped to (`UID` = device ID, path starts with the resource copy's ID).
"""

from __future__ import annotations

from ..io import xmlrt
from . import writer as w
from .changes import ChangeSet
from .edit import EditError
from .solution import Solution
from .types import children

MASK = "True;True|False;True"


def _resolve_path(sol: Solution, path: str, application: str | None):
    """'CAT1.IThis.OUT1' → (application, layer, layer FB, [inner IDs ..., var ID])."""
    parts = path.split(".")
    if len(parts) < 2:
        raise EditError("path must be <instance>[.<inner FB>...].<variable>, e.g. CAT1.IThis.OUT1.")
    hits = [(app, layer, fb) for s in sol.systems for app in s.applications
            if not application or app.name == application
            for layer in app.layers for fb in (layer.network.instances if layer.network else [])
            if fb.name == parts[0]]
    if not hits:
        raise EditError(f"No instance '{parts[0]}' in {'application ' + application if application else 'the applications'}.")
    if len(hits) > 1:
        raise EditError(f"'{parts[0]}' exists in several applications; pass application=.")
    app, layer, fb = hits[0]
    ids: list[str] = []
    td = sol.find_type(fb.type, fb.namespace)
    walked = parts[0]
    for name in parts[1:-1]:
        inner = next((i for i in (td.network.instances if td and td.network else []) if i.name == name), None)
        if inner is None or not inner.id:
            raise EditError(f"{walked} ({fb.type if td is None else td.name}) has no inner FB '{name}'.")
        ids.append(inner.id)
        td = sol.find_type(inner.type, inner.namespace)
        walked += f".{name}"
    if td is None:
        raise EditError(f"Type of {walked} is unknown (run eae_catalog_build for library types).")
    var = next((v for v in td.interface.input_vars + td.interface.output_vars if v.name == parts[-1]), None)
    if var is None:
        names = ", ".join(v.name for v in td.interface.input_vars + td.interface.output_vars)
        raise EditError(f"{td.name} has no variable '{parts[-1]}'. Variables: {names}.")
    if not var.id:
        raise EditError(f"{td.name}.{var.name} has no ID in its type file; open the type in EAE once to assign IDs.")
    return app, layer, fb, ids + [var.id]


def _set(cs: ChangeSet, rel: str, uid: str, context: str, exposed: bool) -> bool:
    if not (cs.root / rel).exists():
        raise EditError(f"{rel} is missing; EAE creates it with the layer/resource.")
    xf = cs.doc(rel)
    root = xf.root
    ns = w._ns(root)
    obj = next((o for o in children(root, "OPCUAComplexObject") if o.get("UID") == uid), None)
    attr = None
    if obj is not None:
        attr = next((a for a in children(obj, "OPCUAAttribute")
                     if a.get("Name") == "Exposed" and a.get("Context") == context), None)
    if not exposed:
        if attr is None:
            return False
        xmlrt.remove_child(attr)
        if not children(obj):
            xmlrt.remove_child(obj)
        return True
    if attr is not None:
        if attr.get("Value") == "True":
            return False
        attr.set("Value", "True")
        return True
    if obj is None:
        obj = w._el("OPCUAComplexObject", [("UID", uid)], ns=ns)
        xmlrt.insert_child(root, obj)
    xmlrt.insert_child(obj, w._el("OPCUAAttribute", [("Name", "Exposed"), ("Value", "True"), ("Locked", "false"),
                                                     ("AttributeMask", MASK), ("Context", context)], ns=ns))
    return True


def set_exposed(sol: Solution, path: str, exposed: bool = True, application: str | None = None) -> ChangeSet:
    app, layer, fb, rest = _resolve_path(sol, path, application)
    cs = ChangeSet(sol.root, f"{'expose' if exposed else 'unexpose'} {path} on OPC UA")
    changed = _set(cs, layer.path.rsplit(".", 1)[0] + "/opcua.xml", app.id, ".".join([fb.id] + rest), exposed)
    mapped = 0
    for s in sol.systems:
        for dev in s.devices:
            for res in dev.resources:
                copy = next((i for i in (res.network.instances if res.network else []) if i.mapping == fb.id), None)
                if copy is None:
                    continue
                mapped += 1
                changed |= _set(cs, res.path.rsplit(".", 1)[0] + "/opcua.xml", dev.id,
                                ".".join([copy.id] + rest), exposed)
    if not changed:
        cs.warnings.append(f"info: {path} is already {'exposed' if exposed else 'not exposed'}.")
    if exposed and not mapped:
        cs.warnings.append(f"warning: {fb.name} is not mapped to a resource; map it (eae_map_to_resource) and "
                           "expose again so the device side is written too.")
    cs.commit_docs()
    return cs
