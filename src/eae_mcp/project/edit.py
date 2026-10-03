"""Solution-level create/edit operations. Each returns a ChangeSet (nothing is written here)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from lxml import etree

from ..io import xmlrt
from ..io.ids import new_guid, new_id16
from ..model import Algorithm, DataTypeDef, ECAction, ECState, ECTransition, Event, Interface, Var
from . import writer as w
from .changes import ChangeSet
from .solution import Solution, SolutionProject
from .types import child, children, local, parse_type_element

FOLDER_CATEGORY = {"adapter": "Adapter", "datatype": "DataType", "basic": "Basic", "composite": "Composite",
                   "cat": "CAT", "subapp": "SubApp", "function": "Function"}


class EditError(ValueError):
    pass


@dataclass
class Target:
    project: SolutionProject
    dir: str  # project directory relative to the solution root, with trailing '/'
    dfbproj: str
    folders_xml: str
    namespace: str


def target_project(sol: Solution, library: str | None = None) -> Target:
    """The IEC61499 project to write into: the main one, or a library project such as 'SE.Agile'."""
    projects = sol.projects_of("iec61499")
    if library:
        proj = next((p for p in projects if p.name == library), None)
    else:
        proj = next((p for p in projects if p.name == "IEC61499"), projects[0] if projects else None)
    if proj is None:
        raise EditError(f"No IEC61499 project '{library or 'IEC61499'}' in this solution.")
    pdir = proj.path.rsplit("/", 1)[0] + "/" if "/" in proj.path else ""
    parent = pdir.rstrip("/").rsplit("/", 1)[0] + "/" if "/" in pdir.rstrip("/") else ""
    return Target(proj, pdir, proj.path, f"{parent}General/Folders.xml", library or "Main")


def _ensure_new_name(sol: Solution, name: str) -> None:
    w.check_identifier(name, "type name")
    clash = [t.qualified_name for t in sol.types.values() if t.name.lower() == name.lower()]
    if clash:
        raise EditError(f"A type named {clash[0]} already exists.")


def _register(cs: ChangeSet, t: Target, kind: str, file_include: str, companions: list[str],
              folder: str | None) -> None:
    proj = cs.doc(t.dfbproj)
    meta = [("IEC61499Type", {"adapter": "Adapter", "datatype": "DataType", "basic": "Basic"}[kind])]
    if folder:
        meta.append(("Parent", folder))
        if (cs.root / t.folders_xml).exists():
            w.ensure_folder(cs.doc(t.folders_xml), FOLDER_CATEGORY[kind], folder)
    w.add_project_item(proj, "Compile", file_include, meta)
    dependent = file_include.replace("\\", "/").rsplit("/", 1)[-1]
    for inc in companions:
        w.add_project_item(proj, "None", inc, [("DependentUpon", dependent)])


def _register_files(cs: ChangeSet, t: Target, iec_type: str, file_include: str,
                    companions: list[tuple[str, list[tuple[str, str]]]], folder: str | None,
                    category: str) -> None:
    """Register a type file (Compile) and its companions (None, DependentUpon + extra metadata)."""
    proj = cs.doc(t.dfbproj)
    meta = [("IEC61499Type", iec_type)]
    if folder:
        meta.append(("Parent", folder))
        if (cs.root / t.folders_xml).exists():
            w.ensure_folder(cs.doc(t.folders_xml), category, folder)
    w.add_project_item(proj, "Compile", file_include, meta)
    dependent = file_include.replace("\\", "/").rsplit("/", 1)[-1]
    for inc, extra in companions:
        w.add_project_item(proj, "None", inc, [("DependentUpon", dependent)] + list(extra))


def _finish(cs: ChangeSet, sol: Solution, rel: str) -> ChangeSet:
    cs.commit_docs()
    # Parse what we are about to write, so broken output never reaches the disk.
    data = cs.changes[rel].new
    td = parse_type_element(xmlrt.parse_bytes(data).root, rel)
    from .validate import validate_type
    cs.warnings += [f"{i['severity']}: {i['message']}" for i in validate_type(td, sol)]
    return cs


# -- create --------------------------------------------------------------------------------


def create_adapter(sol: Solution, name: str, itf: Interface, comment: str | None = None,
                   folder: str | None = None, library: str | None = None) -> ChangeSet:
    _ensure_new_name(sol, name)
    t = target_project(sol, library)
    cs = ChangeSet(sol.root, f"create adapter {name}")
    rel = f"{t.dir}{name}.adp"
    cs.create(rel, w.build_adapter(name, itf, t.namespace, comment))
    cs.create(f"{t.dir}{name}.doc.xml", w.template("doc.xml"))
    _register(cs, t, "adapter", f"{name}.adp", [f"{name}.doc.xml"], folder)
    return _finish(cs, sol, rel)


def create_datatype(sol: Solution, name: str, dt: DataTypeDef, comment: str | None = None,
                    folder: str | None = None, library: str | None = None) -> ChangeSet:
    _ensure_new_name(sol, name)
    t = target_project(sol, library)
    cs = ChangeSet(sol.root, f"create datatype {name}")
    rel = f"{t.dir}DataType/{name}.dt"
    cs.create(rel, w.build_datatype(name, dt, t.namespace, comment))
    cs.create(f"{t.dir}DataType/{name}.doc.xml", w.template("doc_datatype.xml"))
    _register(cs, t, "datatype", f"DataType\\{name}.dt", [f"DataType\\{name}.doc.xml"], folder)
    return _finish(cs, sol, rel)


def create_basic(sol: Solution, name: str, itf: Interface, internal_vars: list[Var], states: list[ECState],
                 transitions: list[ECTransition], algorithms: list[Algorithm], comment: str | None = None,
                 folder: str | None = None, library: str | None = None, cs: ChangeSet | None = None) -> ChangeSet:
    _ensure_new_name(sol, name)
    t = target_project(sol, library)
    cs = cs or ChangeSet(sol.root, f"create basic FB {name}")
    rel = f"{t.dir}{name}.fbt"
    cs.create(rel, w.build_basic(name, itf, internal_vars, states, transitions, algorithms, t.namespace, comment))
    cs.create(f"{t.dir}{name}.doc.xml", w.template("doc.xml"))
    cs.create(f"{t.dir}{name}.meta.xml", w.template("meta.xml"))
    _register(cs, t, "basic", f"{name}.fbt", [f"{name}.doc.xml", f"{name}.meta.xml"], folder)
    return _finish(cs, sol, rel)


# -- edit existing types -----------------------------------------------------------------------


def _type_doc(sol: Solution, name: str, kinds: tuple[str, ...]):
    td = sol.types.get(name) or next((t for t in sol.types.values() if t.name == name), None)
    if td is None:
        raise EditError(f"Type '{name}' not found in the solution (library types are read-only).")
    if td.kind not in kinds:
        raise EditError(f"{td.qualified_name} is a {td.kind}; this operation supports {', '.join(kinds)}.")
    cs = ChangeSet(sol.root)
    return td, cs, cs.doc(td.path)


def pin_usages(sol: Solution, type_name: str, pin_name: str, pin_id: str | None) -> list[str]:
    """Network connections/parameters that reference a pin of `type_name` (by ID or name)."""
    keys = {pin_name} | ({pin_id} if pin_id else set())
    found = []
    networks = [(t.qualified_name, t.network) for t in sol.types.values() if t.network]
    for s in sol.systems:
        networks += [(f"{a.name}/{l.name}", l.network) for a in s.applications for l in a.layers if l.network]
        networks += [(f"{d.name}/{r.name}", r.network) for d in s.devices for r in d.resources if r.network]
    for where, net in networks:
        for inst in net.instances:
            if inst.type != type_name:
                continue
            node_keys = {inst.name} | ({inst.id} if inst.id else set())
            for c in net.connections:
                for ref in (c.source, c.destination):
                    body = ref.lstrip("$")
                    if "." in body:
                        node, pin = body.split(".", 1)
                        if node in node_keys and pin in keys:
                            found.append(f"{where}: connection {c.source} → {c.destination} ({inst.name}.{pin_name})")
            for p in inst.parameters:
                if p.lstrip("$") in keys:
                    found.append(f"{where}: parameter {inst.name}.{pin_name}")
    return found


_SECTION_ORDER = ["EventInputs", "EventOutputs", "InputVars", "OutputVars", "AdapterInputs", "AdapterOutputs",
                  "Sockets", "Plugs"]


def _section(itf_el, name: str):
    sec = child(itf_el, name)
    if sec is not None:
        return sec
    sec = w._el(name)
    existing = children(itf_el)
    rank = _SECTION_ORDER.index(name)
    index = next((i for i, e in enumerate(existing) if local(e) in _SECTION_ORDER
                  and _SECTION_ORDER.index(local(e)) > rank), None)
    xmlrt.insert_child(itf_el, sec, index)
    return sec


def _taken_ids(root) -> set[str]:
    return {e.get("ID") for e in root.iter() if isinstance(e.tag, str) and e.get("ID")}


def update_interface(sol: Solution, name: str, add_events: list[tuple[str, Event]] | None = None,
                     add_vars: list[tuple[str, Var]] | None = None, remove: list[str] | None = None,
                     set_with: dict[str, list[str]] | None = None, force: bool = False) -> ChangeSet:
    """Add events/vars (direction 'input'/'output'), remove pins, or replace an event's WITH list."""
    td, cs, xf = _type_doc(sol, name, ("adapter", "basic", "composite", "cat", "cat_hmi", "sifb"))
    cs.description = f"update interface of {td.qualified_name}"
    itf_el = child(xf.root, "InterfaceList")
    taken = _taken_ids(xf.root)
    existing = {e.get("Name") for e in itf_el.iter() if isinstance(e.tag, str) and local(e) in ("Event", "VarDeclaration")}

    for pin in remove or []:
        el = next((e for e in itf_el.iter() if isinstance(e.tag, str) and local(e) in ("Event", "VarDeclaration")
                   and e.get("Name") == pin), None)
        if el is None:
            raise EditError(f"{td.name} has no pin '{pin}'.")
        uses = pin_usages(sol, td.name, pin, el.get("ID"))
        if td.kind == "basic":
            uses += [f"ECC action in state {s.name}" for s in td.states for a in s.actions if a.output == pin]
            uses += [f"ECC transition {t.source}→{t.destination}" for t in td.transitions
                     if re.search(rf"\b{re.escape(pin)}\b", t.condition)]
            uses += [f"algorithm {a.name}" for a in td.algorithms if re.search(rf"\b{re.escape(pin)}\b", a.text)]
        if uses and not force:
            raise EditError(f"'{pin}' is still used ({len(uses)}): " + "; ".join(uses[:10])
                            + ". Remove the uses first or pass force=true.")
        xmlrt.remove_child(el)
        for wv in [x for x in itf_el.iter() if isinstance(x.tag, str) and local(x) == "With" and x.get("Var") == pin]:
            xmlrt.remove_child(wv)
        existing.discard(pin)

    for direction, var in add_vars or []:
        if var.name in existing:
            raise EditError(f"{td.name} already has a pin named '{var.name}'.")
        xmlrt.insert_child(_section(itf_el, "InputVars" if direction == "input" else "OutputVars"),
                           w.var_element(var, True, taken))
        existing.add(var.name)
    for direction, ev in add_events or []:
        if ev.name in existing:
            raise EditError(f"{td.name} already has a pin named '{ev.name}'.")
        xmlrt.insert_child(_section(itf_el, "EventInputs" if direction == "input" else "EventOutputs"),
                           w.event_element(ev, taken))
        existing.add(ev.name)
    for event_name, vars_ in (set_with or {}).items():
        ev = next((e for e in itf_el.iter() if isinstance(e.tag, str) and local(e) == "Event"
                   and e.get("Name") == event_name), None)
        if ev is None:
            raise EditError(f"{td.name} has no event '{event_name}'.")
        for wv in children(ev, "With"):
            xmlrt.remove_child(wv)
        for v in vars_:
            xmlrt.insert_child(ev, w._el("With", [("Var", v)]))
    _check_interface_xml(itf_el)
    _finish(cs, sol, td.path)
    if td.kind == "cat_hmi":
        from .cat_edit import refresh_hmi_code
        refresh_hmi_code(cs, sol, td.path)
    return cs


def _check_interface_xml(itf_el) -> None:
    from .types import parse_interface
    w.check_interface(parse_interface(itf_el))


def upsert_algorithm(sol: Solution, name: str, algorithm: str, text: str, comment: str | None = None,
                     local_vars: list[Var] | None = None) -> ChangeSet:
    td, cs, xf = _type_doc(sol, name, ("basic",))
    basic = child(xf.root, "BasicFB")
    alg = next((a for a in children(basic, "Algorithm") if a.get("Name") == algorithm), None)
    if alg is None:
        cs.description = f"add algorithm {algorithm} to {td.qualified_name}"
        new = w.algorithm_element(Algorithm(algorithm, text, comment=comment, local_vars=local_vars or []))
        xmlrt.insert_child(basic, new)
        order = next((a for a in children(basic, "Attribute") if a.get("Name") == "FBType.Basic.Algorithm.Order"), None)
        if order is not None:
            names = [n for n in order.get("Value", "").split(",") if n]
            order.set("Value", ",".join(names + [algorithm]))
    else:
        cs.description = f"update algorithm {algorithm} of {td.qualified_name}"
        st = child(alg, "ST")
        if st is None:
            raise EditError(f"Algorithm {algorithm} is not ST; only ST algorithms can be edited.")
        st.text = etree.CDATA(text.replace("\r\n", "\n"))
        if comment is not None:
            alg.set("Comment", comment)
        if local_vars is not None:
            for v in children(alg, "VarDeclaration"):
                xmlrt.remove_child(v)
            for i, v in enumerate(local_vars):
                xmlrt.insert_child(alg, w.var_element(v, with_id=False), i)
    return _finish(cs, sol, td.path)


def update_ecc(sol: Solution, name: str, add_states: list[ECState] | None = None,
               remove_states: list[str] | None = None, add_transitions: list[ECTransition] | None = None,
               remove_transitions: list[ECTransition] | None = None,
               set_actions: dict[str, list[ECAction]] | None = None) -> ChangeSet:
    td, cs, xf = _type_doc(sol, name, ("basic",))
    cs.description = f"update ECC of {td.qualified_name}"
    ecc = child(child(xf.root, "BasicFB"), "ECC")
    states = {s.get("Name"): s for s in children(ecc, "ECState")}
    for sname in remove_states or []:
        if sname == "START":
            raise EditError("START cannot be removed.")
        if sname not in states:
            raise EditError(f"No state '{sname}'.")
        xmlrt.remove_child(states.pop(sname))
        for tr in children(ecc, "ECTransition"):
            if sname in (tr.get("Source"), tr.get("Destination")):
                xmlrt.remove_child(tr)
    for tr_spec in remove_transitions or []:
        hits = [tr for tr in children(ecc, "ECTransition") if tr.get("Source") == tr_spec.source
                and tr.get("Destination") == tr_spec.destination
                and (not tr_spec.condition or tr.get("Condition") == tr_spec.condition)]
        if not hits:
            raise EditError(f"No transition {tr_spec.source}→{tr_spec.destination}.")
        for tr in hits:
            xmlrt.remove_child(tr)
    for s in add_states or []:
        w.check_identifier(s.name, "state name")
        if s.name in states:
            raise EditError(f"State '{s.name}' already exists.")
        x, y = w._state_xy(len(states))
        el = w._el("ECState", [("Name", s.name), ("Comment", s.comment), ("x", x), ("y", y)])
        for a in s.actions:
            el.append(w._el("ECAction", [("Algorithm", a.algorithm), ("Output", a.output)]))
        last_state = children(ecc, "ECState")[-1]
        xmlrt.insert_child(ecc, el, ecc.index(last_state) + 1)
        states[s.name] = el
    for sname, actions in (set_actions or {}).items():
        el = states.get(sname)
        if el is None:
            raise EditError(f"No state '{sname}'.")
        for a in children(el, "ECAction"):
            xmlrt.remove_child(a)
        for a in actions:
            xmlrt.insert_child(el, w._el("ECAction", [("Algorithm", a.algorithm), ("Output", a.output)]))
    for t in add_transitions or []:
        sx, sy = float(states[t.source].get("x", 0)) if t.source in states else 0, float(states[t.source].get("y", 0)) if t.source in states else 0
        dx, dy = float(states[t.destination].get("x", 0)) if t.destination in states else 0, float(states[t.destination].get("y", 0)) if t.destination in states else 0
        xmlrt.insert_child(ecc, w._el("ECTransition", [
            ("Source", t.source), ("Destination", t.destination), ("Condition", t.condition),
            ("x", f"{(sx + dx) / 2:g}"), ("y", f"{(sy + dy) / 2:g}"),
        ]))
    # Re-check the resulting ECC against the (possibly unchanged) interface and algorithms.
    new_td = parse_type_element(xf.root, td.path)
    try:
        w.check_basic(new_td.interface, new_td.internal_vars, new_td.states, new_td.transitions, new_td.algorithms)
    except w.SpecError as e:
        raise EditError(str(e)) from e
    return _finish(cs, sol, td.path)


def replace_datatype(sol: Solution, name: str, dt: DataTypeDef) -> ChangeSet:
    td, cs, xf = _type_doc(sol, name, ("datatype",))
    cs.description = f"update datatype {td.qualified_name}"
    root = xf.root
    # The nxtDataType blob describes the old definition; drop it so EAE regenerates it (C3c/C3d).
    for attr in children(root, "Attribute"):
        if attr.get("Name") == "nxtDataType":
            xmlrt.remove_child(attr)
    old = next((c for c in children(root) if local(c) in ("StructuredType", "EnumeratedType", "ArrayType", "SubrangeType")), None)
    new = w.datatype_body(dt)
    if old is not None:
        index = root.index(old)
        tail = old.tail
        root.remove(old)
        xmlrt.insert_child(root, new, index)
        new.tail = tail
    else:
        xmlrt.insert_child(root, new)
    return _finish(cs, sol, td.path)


_ = (new_guid, new_id16)


# -- functions (POU) --------------------------------------------------------------------------------


def _function_root(name: str, namespace: str, inputs: list[Var], outputs: list[Var], inouts: list[Var],
                   return_type: str | None, temp_vars: list[Var], code: str, comment: str | None):
    taken: set[str] = set()
    seen: set[str] = set()
    for v in inputs + outputs + inouts + temp_vars:
        w.check_identifier(v.name, "variable name")
        if v.name.lower() in seen or v.name.lower() == name.lower():
            raise EditError(f"Duplicate name '{v.name}' (variables must differ from each other and the function).")
        seen.add(v.name.lower())
    root = w._el("POUType", [("GUID", new_guid()), ("Name", name), ("Comment", comment or "Function"),
                             ("Namespace", namespace)])
    w._header(root, "1131-3", "Template")
    itf = w._sub(root, "InterfaceList", [("ReturnValueType", return_type or "")])
    for tag, vars_ in (("InputVars", inputs), ("OutputVars", outputs), ("InputOutputVars", inouts)):
        if vars_:
            sec = w._sub(itf, tag)
            for v in vars_:
                sec.append(w.var_element(v, True, taken))
    body = w._sub(root, "POUBasicFunction")
    if temp_vars:
        sec = w._sub(body, "TempVars")
        for v in temp_vars:
            sec.append(w.var_element(v, True, taken))
    alg = w._sub(body, "Algorithm", [("Name", name), ("Comment", "Algorithm")])
    w._sub(alg, "ST").text = etree.CDATA(code.replace("\r\n", "\n"))
    return root


def _function_warnings(name: str, return_type: str | None, code: str, inouts: list[Var]) -> list[str]:
    out = []
    if return_type and not re.search(rf"\b{re.escape(name)}\s*:=", code):
        out.append(f"warning: the code never assigns the return value ({name} := …).")
    if not return_type and re.search(rf"\b{re.escape(name)}\s*:=", code):
        out.append(f"warning: the code assigns {name} but the function has no return type.")
    for v in inouts:
        if v.array_size == "*" and "UPPER_BOUND" not in code.upper():
            out.append(f"info: {v.name} is a variable-length array; use UPPER_BOUND({v.name}, 1) to loop over it.")
    return out


def create_function(sol: Solution, name: str, code: str, inputs: list[Var] | None = None,
                    outputs: list[Var] | None = None, inouts: list[Var] | None = None,
                    return_type: str | None = None, temp_vars: list[Var] | None = None,
                    comment: str | None = None, library: str | None = None) -> ChangeSet:
    """New IEC 61131-3 function in POU/<name>.fct, exactly like EAE 26."""
    _ensure_new_name(sol, name)
    inputs, outputs, inouts, temp_vars = inputs or [], outputs or [], inouts or [], temp_vars or []
    if not code.strip():
        raise EditError("A function needs ST code.")
    t = target_project(sol, library)
    cs = ChangeSet(sol.root, f"create function {name}")
    rel = f"{t.dir}POU/{name}.fct"
    root = _function_root(name, t.namespace, inputs, outputs, inouts, return_type, temp_vars, code, comment)
    cs.create(rel, xmlrt.dumps(xmlrt.new_document(root, w.DOCTYPE.format(root="POUType"))))
    cs.create(f"{t.dir}POU/{name}.doc.xml", w.template("doc.xml"))
    proj = cs.doc(t.dfbproj)
    w.add_project_item(proj, "Compile", f"POU\\{name}.fct", [("IEC61499Type", "Function")])
    w.add_project_item(proj, "None", f"POU\\{name}.doc.xml", [("DependentUpon", f"{name}.fct")])
    _finish(cs, sol, rel)
    cs.warnings += _function_warnings(name, return_type, code, inouts)
    return cs


def update_function(sol: Solution, name: str, code: str | None = None, temp_vars: list[Var] | None = None) -> ChangeSet:
    """Replace a function's ST code and/or its temporary variables; the interface is kept."""
    td, cs, xf = _type_doc(sol, name, ("function",))
    cs.description = f"update function {td.qualified_name}"
    body = child(xf.root, "POUBasicFunction")
    if body is None:
        raise EditError(f"{td.name} has no POUBasicFunction body.")
    if code is not None:
        alg = child(body, "Algorithm")
        st = child(alg, "ST") if alg is not None else None
        if st is None:
            raise EditError(f"{td.name} has no ST algorithm.")
        st.text = etree.CDATA(code.replace("\r\n", "\n"))
    if temp_vars is not None:
        old = child(body, "TempVars")
        if old is not None:
            xmlrt.remove_child(old)
        if temp_vars:
            taken = _taken_ids(xf.root)
            sec = w._el("TempVars", ns=etree.QName(body).namespace)
            for v in temp_vars:
                sec.append(w.var_element(v, True, taken))
            xmlrt.insert_child(body, sec, 0)
    _finish(cs, sol, td.path)
    current = code if code is not None else "\n".join(a.text for a in td.algorithms)
    cs.warnings += _function_warnings(td.name, td.interface.return_type, current, td.interface.inout_vars)
    return cs
