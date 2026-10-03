"""M3: edit FB networks (Composite/CAT/SubApp types, application layers, resources) and mapping.

References are written the way the container already writes them:
* Format 2.0 (all new EAE files, layers, resources): `$<nodeID>.<pinID>`; library pins (no ID) by name.
* Legacy networks: `<instanceName>.<pinName>`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lxml import etree

from ..io import xmlrt
from ..io.ids import new_guid, new_id16
from ..model import Event, Interface, Network, TypeDef, Var
from . import writer as w
from .changes import ChangeSet
from .edit import EditError, _finish, _register_files, target_project
from .solution import Solution
from .system import Application, Device, Layer, Resource
from .types import child, children, local, parse_interface, parse_network, parse_type_element

NETWORK_TAGS = ("FBNetwork", "SubAppNetwork")
SECTION_ORDER = ["EventConnections", "DataConnections", "AdapterConnections"]


@dataclass
class Container:
    kind: str  # type | layer | resource
    label: str
    rel: str
    xf: xmlrt.XmlFile
    net_el: etree._Element
    owner: TypeDef | None = None  # enclosing type (type containers only)
    format2: bool = True
    layer: Layer | None = None
    application: Application | None = None
    resource: Resource | None = None
    device: Device | None = None
    taken: set[str] = field(default_factory=set)

    @property
    def network(self) -> Network:
        return parse_network(self.net_el) or Network()


def _taken_ids(xf: xmlrt.XmlFile) -> set[str]:
    return {e.get("ID") for e in xf.root.iter() if isinstance(e.tag, str) and e.get("ID")}


def _type_container(cs: ChangeSet, sol: Solution, td: TypeDef) -> Container:
    if td.kind not in ("composite", "cat", "subapp"):
        raise EditError(f"{td.qualified_name} is a {td.kind}; only Composite, CAT and SubApp types have a network.")
    xf = cs.doc(td.path)
    net_el = next((c for c in children(xf.root) if local(c) in NETWORK_TAGS), None)
    if net_el is None:
        net_el = w._el("SubAppNetwork" if td.kind == "subapp" else "FBNetwork")
        xmlrt.insert_child(xf.root, net_el)
    owner = parse_type_element(xf.root, td.path)
    return Container("type", td.qualified_name, td.path, xf, net_el, owner,
                     format2=xf.root.get("Format") == "2.0", taken=_taken_ids(xf))


def resolve_container(cs: ChangeSet, sol: Solution, network: str) -> Container:
    """`network`: a Composite/CAT/SubApp type, an application ('APP1' or 'APP1/Layer'),
    or a resource ('Device/Resource')."""
    if "/" in network:
        a, b = network.split("/", 1)
        for s in sol.systems:
            for dev in s.devices:
                for res in dev.resources:
                    if dev.name == a and res.name == b:
                        xf = cs.doc(res.path)
                        return Container("resource", f"resource {dev.name}/{res.name}", res.path, xf,
                                         child(xf.root, "FBNetwork"), resource=res, device=dev, taken=_taken_ids(xf))
    app_name, _, layer_name = network.partition("/")
    for s in sol.systems:
        for app in s.applications:
            if app.name == app_name:
                layer = next((l for l in app.layers if (l.name == layer_name if layer_name else l.is_default)),
                             app.layers[0] if app.layers and not layer_name else None)
                if layer is None:
                    raise EditError(f"Application {app.name} has no layer '{layer_name}'.")
                xf = cs.doc(layer.path)
                net_el = child(xf.root, "SubAppNetwork")
                if net_el is None:
                    net_el = w._el("SubAppNetwork", ns=etree.QName(xf.root).namespace)
                    xmlrt.insert_child(xf.root, net_el)
                return Container("layer", f"application {app.name}/{layer.name}", layer.path, xf, net_el,
                                 layer=layer, application=app, taken=_taken_ids(xf))
    td = sol.types.get(network) or next((t for t in sol.types.values() if t.name == network), None)
    if td is not None:
        return _type_container(cs, sol, td)
    raise EditError(f"No network '{network}'. Use a Composite/CAT/SubApp type name, an application "
                    "name (e.g. APP1 or APP1/Default) or Device/Resource (e.g. EcoRT_0/RES0).")


def _ns(c: Container) -> str | None:
    return etree.QName(c.net_el).namespace


def _instances(c: Container) -> list[etree._Element]:
    return [e for e in children(c.net_el) if local(e) in ("FB", "SubApp")]


def _instance(c: Container, name: str) -> etree._Element:
    el = next((e for e in _instances(c) if e.get("Name") == name), None)
    if el is None:
        raise EditError(f"{c.label} has no instance '{name}'.")
    return el


def _new_instance_id(c: Container) -> str:
    counter = next((a for a in children(c.xf.root, "Attribute") if a.get("Name") == "Configuration.FB.IDCounter"), None)
    existing = [e.get("ID") for e in _instances(c) if e.get("ID")]
    if not c.format2 and counter is not None and existing and all(i.isdigit() for i in existing):
        value = int(counter.get("Value", "0")) + 1
        counter.set("Value", str(value))
        return str(value)
    return new_id16(c.taken)


# -- pins and references ---------------------------------------------------------------------


@dataclass
class End:
    ref: str  # what goes into Source/Destination
    kind: str  # event | data | adapter
    side: str  # "source" (can drive) or "sink" (can be driven)
    data_type: str | None
    label: str
    node_id: str | None = None  # instance ID (or None for boundary pins)


def _pin_lookup(itf: Interface, pin: str):
    for e in itf.event_inputs:
        if e.name == pin:
            return "event", "in", e.id, None
    for e in itf.event_outputs:
        if e.name == pin:
            return "event", "out", e.id, None
    for v in itf.input_vars:
        if v.name == pin:
            return "data", "in", v.id, v.type
    for v in itf.output_vars:
        if v.name == pin:
            return "data", "out", v.id, v.type
    for a in itf.adapter_inputs:
        if a.name == pin:
            return "adapter", "in", a.id, a.type
    for a in itf.adapter_outputs:
        if a.name == pin:
            return "adapter", "out", a.id, a.type
    return None


def resolve_end(c: Container, sol: Solution, spec: str) -> End:
    """'FB1.REQ' (instance pin) or 'REQ' (boundary pin of the enclosing type)."""
    prefix = "$" if c.format2 else ""
    if "." in spec:
        inst_name, pin = spec.split(".", 1)
        inst = _instance(c, inst_name)
        td = _find_type(sol, inst.get("Type", ""), inst.get("Namespace"))
        if td is None:
            raise EditError(f"Type {inst.get('Type')} of {inst_name} is unknown (run eae_catalog_build for library types).")
        found = _pin_lookup(td.interface, pin)
        if found is None:
            raise EditError(f"{inst_name} ({td.name}) has no pin '{pin}'.")
        kind, direction, pin_id, dtype = found
        node = inst.get("ID") if c.format2 and inst.get("ID") else inst_name
        key = pin_id if c.format2 and pin_id else pin
        return End(f"{prefix}{node}.{key}", kind, "source" if direction == "out" else "sink", dtype, spec,
                   inst.get("ID") or inst_name)
    if c.owner is None:
        raise EditError(f"'{spec}' needs the form Instance.Pin in {c.label}.")
    found = _pin_lookup(c.owner.interface, spec)
    if found is None:
        raise EditError(f"{c.owner.name} has no interface pin '{spec}'.")
    kind, direction, pin_id, dtype = found
    boundary = next((p for p in children(c.net_el) if local(p) in ("Input", "Output") and p.get("Name") == spec), None)
    bid = boundary.get("ID") if boundary is not None else pin_id
    key = bid if c.format2 and bid else spec
    # A boundary *input* drives the network; a boundary *output* is driven by it.
    return End(f"{prefix}{key}", kind, "source" if direction == "in" else "sink", dtype, spec)


def _section(c: Container, kind: str) -> etree._Element:
    name = {"event": "EventConnections", "data": "DataConnections", "adapter": "AdapterConnections"}[kind]
    sec = child(c.net_el, name)
    if sec is not None:
        return sec
    sec = w._el(name, ns=_ns(c))
    later = [i for i, e in enumerate(children(c.net_el))
             if local(e) in SECTION_ORDER and SECTION_ORDER.index(local(e)) > SECTION_ORDER.index(name)]
    xmlrt.insert_child(c.net_el, sec, later[0] if later else None)
    return sec


def _connections(c: Container):
    for sec in children(c.net_el):
        if local(sec) in SECTION_ORDER:
            for conn in children(sec, "Connection"):
                yield local(sec), conn


def _add_connection(c: Container, kind: str, source: str, destination: str) -> None:
    xmlrt.insert_child(_section(c, kind), w._el("Connection", [("Source", source), ("Destination", destination)], ns=_ns(c)))


def _remove_connection_el(conn: etree._Element) -> None:
    sec = conn.getparent()
    xmlrt.remove_child(conn)
    if len(children(sec)) == 0:
        xmlrt.remove_child(sec)


# -- mapping helpers -------------------------------------------------------------------------------


def _mapped_copies(sol: Solution, cs: ChangeSet, layer_fb_id: str) -> list[Container]:
    """Resource containers holding a copy of the layer FB `layer_fb_id`."""
    out = []
    for s in sol.systems:
        for dev in s.devices:
            for res in dev.resources:
                if res.network and any(i.mapping == layer_fb_id for i in res.network.instances):
                    out.append(resolve_container(cs, sol, f"{dev.name}/{res.name}"))
    return out


def _copy_id(res: Container, layer_fb_id: str) -> str | None:
    el = next((e for e in _instances(res) if e.get("Mapping") == layer_fb_id), None)
    return el.get("ID") if el is not None else None


def _translate(ref: str, mapping: dict[str, str]) -> str | None:
    body = ref.lstrip("$")
    node, dot, pin = body.partition(".")
    if node not in mapping:
        return None
    return f"${mapping[node]}{dot}{pin}"


# -- operations ---------------------------------------------------------------------------------------


def _find_type(sol: Solution, name: str, namespace: str | None) -> TypeDef | None:
    from .library_guide import generic_typedef

    return sol.find_type(name, namespace) or generic_typedef(sol, name, namespace)


def _generic_by_base(sol: Solution, base: str, params: str | None) -> TypeDef | None:
    """'VALFORMAT' (+ 'I:=1;VALUE${I}:STRING') → an existing concrete generic type with those parameters."""
    from .library_guide import find_generic, generic_typedef

    found = find_generic(sol, base, params)
    if not found:
        return None
    variants = {g["params"] for g in found}
    if len(variants) > 1:
        raise EditError(f"{base} is used with several parameter sets; pass generic_params, one of: "
                        + "; ".join(sorted(variants)))
    return generic_typedef(sol, found[0]["type"], found[0]["namespace"])


def add_fb(sol: Solution, network: str, name: str, type_name: str, namespace: str | None = None,
           parameters: dict[str, str] | None = None, x: float | None = None, y: float | None = None,
           generic_params: str | None = None) -> ChangeSet:
    cs = ChangeSet(sol.root)
    c = resolve_container(cs, sol, network)
    cs.description = f"add {name}: {type_name} to {c.label}"
    if c.kind == "resource":
        raise EditError("Add FBs to the application and map them (eae_map_to_resource); resource-only FBs are not supported yet.")
    w.check_identifier(name, "instance name")
    if any(e.get("Name") == name for e in _instances(c)):
        raise EditError(f"{c.label} already has an instance '{name}'.")
    td = _find_type(sol, type_name, namespace)
    if td is None and generic_params is not None or (td is None and type_name.isupper()):
        td = _generic_by_base(sol, type_name, generic_params)
    if td is None:
        raise EditError(f"Type '{type_name}' not found in the solution or the library catalog "
                        "(run eae_catalog_build for system-library types).")
    if td.kind in ("adapter", "datatype", "function", "cat_hmi") or (td.kind == "subapp" and c.kind == "type"):
        raise EditError(f"{td.qualified_name} ({td.kind}) cannot be instantiated in {c.label}.")
    if c.owner is not None and td.qualified_name == c.owner.qualified_name:
        raise EditError("A type cannot contain an instance of itself.")
    xs = [float(e.get("x", 0)) for e in _instances(c)]
    el = w._el("SubApp" if td.kind == "subapp" else "FB", [
        ("ID", _new_instance_id(c)), ("Name", name), ("Type", td.name), ("Namespace", td.namespace),
        ("x", f"{x if x is not None else (max(xs) + 700 if xs else 1000):g}"),
        ("y", f"{y if y is not None else 1000:g}"),
    ], ns=_ns(c))
    if td.kind == "generic":
        # Concrete generic types are generated per project: use the network's own namespace.
        el.set("Namespace", (c.owner.namespace if c.owner is not None else None) or "Main")
        w._sub(el, "Attribute", [("Name", "Configuration.GenericFBType.InterfaceParams"),
                                 ("Value", td.attributes["Configuration.GenericFBType.InterfaceParams"])])
    if c.kind == "type":
        # Type networks write x/y before Namespace; layers write Namespace first.
        for attr in ("Namespace",):
            value = el.attrib.pop(attr)
            el.set(attr, value)
    last = [i for i, e in enumerate(children(c.net_el)) if local(e) in ("FB", "SubApp")]
    xmlrt.insert_child(c.net_el, el, last[-1] + 1 if last else 0)
    for var, value in (parameters or {}).items():
        _set_param_el(c, sol, el, td, var, value)
    _sync_subcat(cs, sol, c, name, td)
    return _finish_net(cs, sol, c)


def _sync_subcat(cs: ChangeSet, sol: Solution, c: Container, name: str, td: TypeDef | None) -> None:
    """A CAT instance inside a CAT is listed in the outer CAT's .cfg as <SubCAT> (before <HMIInterface>)."""
    if c.kind != "type" or c.owner is None:
        return
    cat = sol.cats.get(c.owner.qualified_name)
    if cat is None or not (cs.root / cat.cfg_file).exists():
        return
    root = cs.doc(cat.cfg_file).root
    for old in [e for e in children(root, "SubCAT") if e.get("Name") == name]:
        xmlrt.remove_child(old)
    if td is None or td.kind != "cat":
        return
    el = w._el("SubCAT", [("Name", name), ("Type", td.name), ("Namespace", td.namespace), ("UsedInCAT", "true")],
               ns=w._ns(root))
    kids = children(root)
    subs = [i for i, e in enumerate(kids) if local(e) == "SubCAT"]
    hmi = [i for i, e in enumerate(kids) if local(e) == "HMIInterface"]
    xmlrt.insert_child(root, el, subs[-1] + 1 if subs else (hmi[0] if hmi else 0))


def _set_param_el(c: Container, sol: Solution, inst_el, td: TypeDef, var: str, value: str | None) -> None:
    v = next((v for v in td.interface.input_vars if v.name == var), None)
    if v is None:
        raise EditError(f"{td.name} has no input variable '{var}'.")
    key = (f"${v.id}" if v.id else f"${var}") if c.format2 else var
    existing = next((p for p in children(inst_el, "Parameter") if p.get("Name") in (key, var, f"${var}", f"${v.id}")), None)
    if value is None:
        if existing is not None:
            xmlrt.remove_child(existing)
        return
    if existing is not None:
        existing.set("Value", value)
    else:
        params = children(inst_el, "Parameter")
        index = inst_el.index(params[-1]) + 1 if params else None
        xmlrt.insert_child(inst_el, w._el("Parameter", [("Name", key), ("Value", value)], ns=etree.QName(inst_el).namespace), index)


def set_param(sol: Solution, network: str, instance: str, var: str, value: str | None) -> ChangeSet:
    cs = ChangeSet(sol.root)
    c = resolve_container(cs, sol, network)
    cs.description = f"set {instance}.{var} in {c.label}"
    inst = _instance(c, instance)
    td = _find_type(sol, inst.get("Type", ""), inst.get("Namespace"))
    if td is None:
        raise EditError(f"Type {inst.get('Type')} is unknown.")
    _set_param_el(c, sol, inst, td, var, value)
    if c.kind == "layer":
        for res in _mapped_copies(sol, cs, inst.get("ID")):
            copy = next(e for e in _instances(res) if e.get("Mapping") == inst.get("ID"))
            _set_param_el(res, sol, copy, td, var, value)
    return _finish_net(cs, sol, c)


def connect(sol: Solution, network: str, source: str, destination: str, replace: bool = False) -> ChangeSet:
    cs = ChangeSet(sol.root)
    c = resolve_container(cs, sol, network)
    cs.description = f"connect {source} → {destination} in {c.label}"
    a, b = resolve_end(c, sol, source), resolve_end(c, sol, destination)
    if a.kind != b.kind:
        raise EditError(f"Cannot connect {a.kind} pin {a.label} to {b.kind} pin {b.label}.")
    if a.side == "sink" and b.side == "source":
        a, b = b, a  # accept reversed order
    if not (a.side == "source" and b.side == "sink"):
        raise EditError(f"{source} → {destination}: a connection goes from an output (or boundary input) "
                        "to an input (or boundary output).")
    for _, conn in _connections(c):
        if conn.get("Source") == a.ref and conn.get("Destination") == b.ref:
            raise EditError("This connection already exists.")
    if a.kind == "data":
        if a.data_type and b.data_type and a.data_type != b.data_type:
            cs.warnings.append(f"warning: data types differ ({a.data_type} → {b.data_type}); EAE may reject it "
                               "when CheckConnectionsStrictly is on.")
        existing = [conn for k, conn in _connections(c) if k == "DataConnections" and conn.get("Destination") == b.ref]
        if existing and not replace:
            raise EditError(f"{b.label} already has a source. Pass replace=true to replace it.")
        for conn in existing:
            _remove_connection_el(conn)
    _add_connection(c, a.kind, a.ref, b.ref)
    if c.kind == "layer":
        _mirror_to_resources(cs, sol, c, a, b)
    return _finish_net(cs, sol, c)


def _mirror_to_resources(cs: ChangeSet, sol: Solution, c: Container, a: End, b: End, remove: bool = False) -> None:
    """Keep resource copies in sync for connections between FBs mapped to the same resource."""
    if not (a.node_id and b.node_id):
        return
    ra = {r.rel: r for r in _mapped_copies(sol, cs, a.node_id)}
    rb = {r.rel: r for r in _mapped_copies(sol, cs, b.node_id)}
    for rel in ra.keys() & rb.keys():
        res = ra[rel]
        m = {a.node_id: _copy_id(res, a.node_id), b.node_id: _copy_id(res, b.node_id)}
        src, dst = _translate(a.ref, m), _translate(b.ref, m)
        if remove:
            for _, conn in list(_connections(res)):
                if conn.get("Source") == src and conn.get("Destination") == dst:
                    _remove_connection_el(conn)
        else:
            _add_connection(res, a.kind, src, dst)
    if not remove and ra.keys() ^ rb.keys() and ra and rb:
        cs.warnings.append("warning: the two FBs are mapped to different resources; cross-resource "
                           "communication is not generated by this tool.")


def disconnect(sol: Solution, network: str, source: str, destination: str) -> ChangeSet:
    cs = ChangeSet(sol.root)
    c = resolve_container(cs, sol, network)
    cs.description = f"disconnect {source} → {destination} in {c.label}"
    a, b = resolve_end(c, sol, source), resolve_end(c, sol, destination)
    if a.side == "sink" and b.side == "source":
        a, b = b, a
    hits = [conn for _, conn in _connections(c) if conn.get("Source") == a.ref and conn.get("Destination") == b.ref]
    if not hits:
        raise EditError(f"No connection {source} → {destination} in {c.label}.")
    for conn in hits:
        _remove_connection_el(conn)
    if c.kind == "layer":
        _mirror_to_resources(cs, sol, c, a, b, remove=True)
    return _finish_net(cs, sol, c)


def remove_fb(sol: Solution, network: str, instance: str, force: bool = False) -> ChangeSet:
    cs = ChangeSet(sol.root)
    c = resolve_container(cs, sol, network)
    cs.description = f"remove {instance} from {c.label}"
    inst = _instance(c, instance)
    node = inst.get("ID") or instance
    if c.kind == "layer":
        from ..hmi.reader import load_hmi
        bound = [f"{d.technology} {d.kind} {d.name}" for d in load_hmi(sol).documents
                 if any(o.tag_name == inst.get("ID") for o in d.objects)]
        if bound and not force:
            raise EditError(f"{instance} is shown on {', '.join(bound)}; remove it there first or pass force=true.")
        for res in _mapped_copies(sol, cs, inst.get("ID")):
            _unmap_in(res, inst.get("ID"))
    for _, conn in list(_connections(c)):
        for ref in (conn.get("Source", ""), conn.get("Destination", "")):
            if ref.lstrip("$").split(".")[0] in (node, instance):
                _remove_connection_el(conn)
                break
    xmlrt.remove_child(inst)
    _sync_subcat(cs, sol, c, instance, None)
    return _finish_net(cs, sol, c)


def map_to_resource(sol: Solution, instance: str, resource: str, application: str | None = None) -> ChangeSet:
    cs = ChangeSet(sol.root)
    apps = [a for s in sol.systems for a in s.applications if application in (None, a.name)]
    hit = None
    for app in apps:
        for layer in app.layers:
            if layer.network and any(i.name == instance for i in layer.network.instances):
                hit = (app, layer)
    if hit is None:
        raise EditError(f"No application instance '{instance}'.")
    app, layer = hit
    lc = resolve_container(cs, sol, f"{app.name}/{layer.name}")
    rc = resolve_container(cs, sol, resource)
    if rc.kind != "resource":
        raise EditError(f"'{resource}' is not a Device/Resource.")
    cs.description = f"map {app.name}/{instance} to {resource}"
    src = _instance(lc, instance)
    if _mapped_copies(sol, cs, src.get("ID")):
        raise EditError(f"{instance} is already mapped; unmap it first.")
    copy = w._el(local(src), [("ID", new_id16(rc.taken)), ("Name", instance), ("Type", src.get("Type")),
                              ("Namespace", src.get("Namespace")), ("Mapping", src.get("ID")),
                              ("x", src.get("x")), ("y", src.get("y"))], ns=_ns(rc))
    for p in children(src, "Parameter"):
        copy.append(w._el("Parameter", [("Name", p.get("Name")), ("Value", p.get("Value"))], ns=_ns(rc)))
    last = [i for i, e in enumerate(children(rc.net_el)) if local(e) in ("FB", "SubApp")]
    xmlrt.insert_child(rc.net_el, copy, last[-1] + 1 if last else 0)
    # Copy connections to FBs already mapped to the same resource.
    ids = {src.get("ID"): copy.get("ID")}
    for e in _instances(rc):
        if e.get("Mapping"):
            ids.setdefault(e.get("Mapping"), e.get("ID"))
    for kind, conn in _connections(lc):
        s, d = _translate(conn.get("Source", ""), ids), _translate(conn.get("Destination", ""), ids)
        if s and d and src.get("ID") in (conn.get("Source", "").lstrip("$").split(".")[0],
                                           conn.get("Destination", "").lstrip("$").split(".")[0]):
            _add_connection(rc, kind.replace("Connections", "").lower(), s, d)
    return _finish_net(cs, sol, lc)


def _unmap_in(res: Container, layer_fb_id: str) -> None:
    el = next((e for e in _instances(res) if e.get("Mapping") == layer_fb_id), None)
    if el is None:
        return
    rid = el.get("ID")
    for _, conn in list(_connections(res)):
        if rid in (conn.get("Source", "").lstrip("$").split(".")[0], conn.get("Destination", "").lstrip("$").split(".")[0]):
            _remove_connection_el(conn)
    xmlrt.remove_child(el)


def unmap(sol: Solution, instance: str, application: str | None = None) -> ChangeSet:
    cs = ChangeSet(sol.root)
    for s in sol.systems:
        for app in s.applications:
            if application not in (None, app.name):
                continue
            for layer in app.layers:
                inst = next((i for i in (layer.network.instances if layer.network else []) if i.name == instance), None)
                if inst:
                    copies = _mapped_copies(sol, cs, inst.id)
                    if not copies:
                        raise EditError(f"{instance} is not mapped.")
                    cs.description = f"unmap {app.name}/{instance}"
                    for res in copies:
                        _unmap_in(res, inst.id)
                    cs.commit_docs()
                    return cs
    raise EditError(f"No application instance '{instance}'.")


def _finish_net(cs: ChangeSet, sol: Solution, c: Container) -> ChangeSet:
    cs.commit_docs()
    if c.kind == "type" and c.rel in cs.changes:
        td = parse_type_element(xmlrt.parse_bytes(cs.changes[c.rel].new).root, c.rel)
        from .validate import validate_type
        cs.warnings += [f"{i['severity']}: {i['message']}" for i in validate_type(td, sol)
                        if i["severity"] != "info"]
    return cs


# -- new container types ----------------------------------------------------------------------------


def _boundary_pins(itf: Interface) -> list[etree._Element]:
    pins = []
    y_in, y_out = 12, 12
    for tag, items, direction in (("Input", itf.event_inputs, "Event"), ("Input", itf.input_vars, "Data"),
                                  ("Output", itf.event_outputs, "Event"), ("Output", itf.output_vars, "Data")):
        for it in items:
            if tag == "Input":
                x, y, y_in = "12", str(y_in), y_in + 60
            else:
                x, y, y_out = "2400", str(y_out), y_out + 60
            pins.append(w._el(tag, [("ID", it.id), ("Name", it.name), ("x", x), ("y", y), ("Type", direction)]))
    return pins


def create_composite(sol: Solution, name: str, itf: Interface, comment: str | None = None,
                     folder: str | None = None, library: str | None = None) -> ChangeSet:
    from .edit import _ensure_new_name

    _ensure_new_name(sol, name)
    t = target_project(sol, library)
    cs = ChangeSet(sol.root, f"create composite FB {name}")
    taken: set[str] = set()
    root = w._el("FBType", [("GUID", new_guid()), ("Comment", comment or "Composite Function Block Type"),
                            ("Name", name), ("Format", "2.0"), ("Namespace", t.namespace)])
    w._header(root, "61499-2", "template")
    root.append(w.interface_element(itf, taken))
    net = w._sub(root, "FBNetwork")
    for pin in _boundary_pins(itf):
        net.append(pin)
    rel = f"{t.dir}{name}.fbt"
    cs.create(rel, xmlrt.dumps(xmlrt.new_document(root, w.DOCTYPE.format(root="FBType"))))
    cs.create(f"{t.dir}{name}.composite.offline.xml", w.template("offline.xml"))
    cs.create(f"{t.dir}{name}.doc.xml", w.template("doc.xml"))
    cs.create(f"{t.dir}{name}.meta.xml", w.template("meta.xml"))
    _register_files(cs, t, "Composite", f"{name}.fbt",
                    [(f"{name}.composite.offline.xml", []), (f"{name}.doc.xml", []), (f"{name}.meta.xml", [])],
                    folder, "Composite")
    return _finish(cs, sol, rel)


def create_subapp(sol: Solution, name: str, application: str, instance: str | None = None,
                  itf: Interface | None = None, x: float = 1000, y: float = 1000) -> ChangeSet:
    """Create a SubApp in an application: its content file <name>/<name>.app plus the layer instance."""
    from .edit import _ensure_new_name

    _ensure_new_name(sol, name)
    itf = itf or Interface()
    if itf.input_vars or itf.output_vars:
        raise EditError("SubApp data pins are not supported yet (format not captured); use events only.")
    t = target_project(sol, None)
    cs = ChangeSet(sol.root, f"create SubApp {name} in {application}")
    taken: set[str] = set()
    root = w._el("SubAppType", [("GUID", new_guid()), ("Name", name), ("Format", "2.0"),
                                ("Comment", "Subapplication "), ("Namespace", t.namespace)])
    w._header(root, "61499-2", "template")
    sil = w._sub(root, "SubAppInterfaceList")
    for tag, events in (("SubAppEventInputs", itf.event_inputs), ("SubAppEventOutputs", itf.event_outputs)):
        if events:
            sec = w._sub(sil, tag)
            for e in events:
                w.check_identifier(e.name, "event name")
                e.id = e.id or new_id16(taken)
                w._sub(sec, "SubAppEvent", [("ID", e.id), ("Name", e.name)])
    net = w._sub(root, "SubAppNetwork")
    for pin in _boundary_pins(itf):
        net.append(pin)
    base = f"{t.dir}{name}/{name}"
    cs.create(f"{base}.app", xmlrt.dumps(xmlrt.new_document(root, w.DOCTYPE.format(root="SubAppType"))))
    cs.create(f"{base}.doc.xml", w.template("doc.xml"))
    cs.create(f"{base}.meta.xml", w.template("meta.xml"))
    cs.create(f"{base}.subapp.offline.xml", w.template("offline.xml"))
    cs.create(f"{base}.subapp.opcua.xml", w.template("opcua_complex.xml"))
    plug_off = [("Plugin", "OfflineParametrizationEditor"), ("IEC61499Type", "CAT_OFFLINE")]
    plug_opc = [("Plugin", "OPCUAConfigurator"), ("IEC61499Type", "CAT_OPCUA")]
    _register_files(cs, t, "SubApp", f"{name}\\{name}.app",
                    [(f"{name}\\{name}.doc.xml", []), (f"{name}\\{name}.meta.xml", []),
                     (f"{name}\\{name}.subapp.offline.xml", plug_off),
                     (f"{name}\\{name}.subapp.opcua.xml", plug_opc)], None, "SubApp")
    w.add_project_item(cs.doc(t.dfbproj), "Folder", name, [])
    # Instance in the application layer.
    lc = resolve_container(cs, sol, application)
    if lc.kind != "layer":
        raise EditError(f"'{application}' is not an application.")
    inst_name = instance or name.upper()
    w.check_identifier(inst_name, "instance name")
    el = w._el("SubApp", [("ID", new_id16(lc.taken)), ("Name", inst_name), ("Type", name),
                          ("Namespace", t.namespace), ("x", f"{x:g}"), ("y", f"{y:g}")], ns=_ns(lc))
    last = [i for i, e in enumerate(children(lc.net_el)) if local(e) in ("FB", "SubApp")]
    xmlrt.insert_child(lc.net_el, el, last[-1] + 1 if last else 0)
    return _finish(cs, sol, f"{base}.app")


_ = (Event, Var, parse_interface)
