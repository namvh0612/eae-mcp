"""Create and edit IEC 61499 types exactly the way EAE 26 writes them."""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from lxml import etree

from ..io import xmlrt
from ..io.ids import new_guid, new_id16
from ..model import AdapterDecl, Algorithm, DataTypeDef, ECState, ECTransition, Event, Interface, Var
from .changes import ChangeSet
from .types import child, children, local

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
DOCTYPE = '<!DOCTYPE {root} SYSTEM "../LibraryElement.dtd">'

# IEC 61131-3 / 61499 keywords (plus words EAE rejects, e.g. ON) that cannot be identifiers.
RESERVED = {
    "ABS", "ACTION", "AND", "ANY", "ARRAY", "AT", "BOOL", "BY", "BYTE", "CASE", "CONFIGURATION", "CONSTANT",
    "DATE", "DINT", "DO", "DT", "DWORD", "ELSE", "ELSIF", "END_ACTION", "END_CASE", "END_CONFIGURATION",
    "END_FOR", "END_FUNCTION", "END_FUNCTION_BLOCK", "END_IF", "END_PROGRAM", "END_REPEAT", "END_RESOURCE",
    "END_STEP", "END_STRUCT", "END_TRANSITION", "END_TYPE", "END_VAR", "END_WHILE", "EXIT", "FALSE", "FOR",
    "FROM", "FUNCTION", "FUNCTION_BLOCK", "IF", "INITIAL_STEP", "INT", "LINT", "LREAL", "LWORD", "MOD", "NOT",
    "OF", "ON", "OR", "PRIORITY", "PROGRAM", "READ_ONLY", "READ_WRITE", "REAL", "REPEAT", "RESOURCE", "RETAIN",
    "RETURN", "SINT", "STEP", "STRING", "STRUCT", "TASK", "THEN", "TIME", "TO", "TOD", "TRANSITION", "TRUE",
    "TYPE", "UDINT", "UINT", "ULINT", "UNTIL", "USINT", "VAR", "VAR_ACCESS", "VAR_CONFIG", "VAR_EXTERNAL",
    "VAR_GLOBAL", "VAR_INPUT", "VAR_IN_OUT", "VAR_OUTPUT", "VAR_TEMP", "WHILE", "WITH", "WORD", "XOR",
}
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SpecError(ValueError):
    pass


def check_identifier(name: str, what: str = "name") -> None:
    if not _IDENT.match(name or "") or "__" in name:
        raise SpecError(f"Invalid {what} '{name}': use letters, digits and single underscores, not starting with a digit.")
    if name.upper() in RESERVED:
        raise SpecError(f"'{name}' is a reserved word in EAE/IEC 61131-3 and cannot be used as a {what}.")


def eae_date(today: _dt.date | None = None) -> str:
    d = today or _dt.date.today()
    return f"{d.month}/{d.day}/{d.year}"


def _el(tag: str, attrs: list[tuple[str, str | None]] | None = None, ns: str | None = None) -> etree._Element:
    el = etree.Element(f"{{{ns}}}{tag}" if ns else tag)
    for k, v in attrs or []:
        if v is not None:
            el.set(k, str(v))
    return el


def _sub(parent, tag: str, attrs: list[tuple[str, str | None]] | None = None):
    el = _el(tag, attrs, etree.QName(parent).namespace)
    parent.append(el)
    return el


# -- interface -------------------------------------------------------------------------


def var_element(v: Var, with_id: bool = True, taken: set[str] | None = None) -> etree._Element:
    check_identifier(v.name, "variable name")
    if not v.type:
        raise SpecError(f"Variable '{v.name}' needs a type.")
    if with_id and not v.id:
        v.id = new_id16(taken)
    return _el("VarDeclaration", [
        ("ID", v.id if with_id else None), ("Name", v.name), ("Type", v.type), ("Namespace", v.namespace),
        ("ArraySize", v.array_size),
        ("InitialValue", v.initial_value), ("Comment", v.comment),
    ])


def event_element(e: Event, taken: set[str] | None = None) -> etree._Element:
    check_identifier(e.name, "event name")
    if not e.id:
        e.id = new_id16(taken)
    el = _el("Event", [("ID", e.id), ("Name", e.name), ("Comment", e.comment)])
    for w in e.with_vars:
        _sub(el, "With", [("Var", w)])
    return el


def check_interface(itf: Interface) -> None:
    names: dict[str, str] = {}
    for kind, items in (("event", itf.event_inputs + itf.event_outputs),
                        ("variable", itf.input_vars + itf.output_vars)):
        for it in items:
            if it.name in names:
                raise SpecError(f"Duplicate interface name '{it.name}'.")
            names[it.name] = kind
    ins = {v.name for v in itf.input_vars}
    outs = {v.name for v in itf.output_vars}
    for e in itf.event_inputs:
        for w in e.with_vars:
            if w not in ins:
                raise SpecError(f"Input event {e.name} WITH {w}: '{w}' is not an input variable.")
    for e in itf.event_outputs:
        for w in e.with_vars:
            if w not in outs:
                raise SpecError(f"Output event {e.name} WITH {w}: '{w}' is not an output variable.")


def interface_element(itf: Interface, taken: set[str]) -> etree._Element:
    check_interface(itf)
    root = _el("InterfaceList")
    for tag, events in (("EventInputs", itf.event_inputs), ("EventOutputs", itf.event_outputs)):
        if events:
            section = _sub(root, tag)
            for e in events:
                section.append(event_element(e, taken))
    for tag, variables in (("InputVars", itf.input_vars), ("OutputVars", itf.output_vars)):
        if variables:
            section = _sub(root, tag)
            for v in variables:
                section.append(var_element(v, True, taken))
    # Basic FBs: <Sockets>/<Plugs> (Composite/CAT boundaries use AdapterInputs/AdapterOutputs, not written here).
    for tag, role in (("Sockets", "socket"), ("Plugs", "plug")):
        decls = [a for a in itf.adapter_inputs + itf.adapter_outputs if a.role == role]
        if decls:
            section = _sub(root, tag)
            for a in decls:
                section.append(adapter_element(a))
    return root


def adapter_element(a: AdapterDecl) -> etree._Element:
    check_identifier(a.name, "adapter name")
    return _el("AdapterDeclaration", [("ID", a.id), ("Name", a.name), ("Type", a.type), ("Namespace", a.namespace)])


def _header(root, standard: str, remarks: str | None) -> None:
    _sub(root, "Identification", [("Standard", standard)])
    _sub(root, "VersionInfo", [("Organization", "Schneider Electric"), ("Version", "0.0"), ("Author", " "),
                               ("Date", eae_date()), ("Remarks", remarks)])


# -- new types ---------------------------------------------------------------------------

_ADAPTER_SERVICE = [
    ("request_confirm", [("SOCKET", "REQ", "REQD", "PLUG", "REQ", "REQD"), ("PLUG", "CNF", "CNFD", "SOCKET", "CNF", "CNFD")]),
    ("indication_response", [("PLUG", "IND", "INDD", "SOCKET", "IND", "INDD"), ("SOCKET", "RSP", "RSPD", "PLUG", "RSP", "RSPD")]),
]


def build_adapter(name: str, itf: Interface, namespace: str = "Main", comment: str | None = None) -> bytes:
    check_identifier(name, "type name")
    taken: set[str] = set()
    root = _el("AdapterType", [("GUID", new_guid()), ("Name", name), ("Comment", comment or "Adapter Interface"),
                               ("Namespace", namespace)])
    _header(root, "61499-1", None)
    root.append(interface_element(itf, taken))
    service = _sub(root, "Service", [("RightInterface", "PLUG"), ("LeftInterface", "SOCKET")])
    for seq_name, transactions in _ADAPTER_SERVICE:
        seq = _sub(service, "ServiceSequence", [("Name", seq_name)])
        for i_if, i_ev, i_par, o_if, o_ev, o_par in transactions:
            tr = _sub(seq, "ServiceTransaction")
            _sub(tr, "InputPrimitive", [("Interface", i_if), ("Event", i_ev), ("Parameters", i_par)])
            _sub(tr, "OutputPrimitive", [("Interface", o_if), ("Event", o_ev), ("Parameters", o_par)])
    return xmlrt.dumps(xmlrt.new_document(root, DOCTYPE.format(root="AdapterType")))


def build_datatype(name: str, dt: DataTypeDef, namespace: str = "Main", comment: str | None = None) -> bytes:
    check_identifier(name, "type name")
    root = _el("DataType", [("Namespace", namespace), ("Name", name), ("Comment", comment or "IEC61131-3, Table 14#5")])
    # nxtDataType is omitted on purpose: EAE regenerates it on the next edit and builds without it (C3d).
    _header(root, "1131-3", "Template")
    _sub(root, "CompilerInfo")
    root.append(datatype_body(dt))
    return xmlrt.dumps(xmlrt.new_document(root, DOCTYPE.format(root="DataType")))


def datatype_body(dt: DataTypeDef) -> etree._Element:
    if dt.kind == "struct":
        if not dt.members:
            raise SpecError("A structure needs at least one member.")
        body = _el("StructuredType")
        seen = set()
        for m in dt.members:
            if m.name in seen:
                raise SpecError(f"Duplicate member '{m.name}'.")
            seen.add(m.name)
            body.append(var_element(m, with_id=False))
    elif dt.kind == "enum":
        if not dt.values:
            raise SpecError("An enumeration needs at least one value.")
        body = _el("EnumeratedType", [("Type", dt.base_type or "USINT")])
        seen = set()
        for i, v in enumerate(dt.values):
            check_identifier(v.name, "enumeration value")
            if v.name in seen:
                raise SpecError(f"Duplicate enumeration value '{v.name}'.")
            seen.add(v.name)
            _sub(body, "EnumeratedValue", [("Name", v.name), ("Value", v.value if v.value is not None else str(i))])
    elif dt.kind in ("array", "subrange"):
        if not dt.base_type or not dt.ranges:
            raise SpecError(f"An {dt.kind} needs base_type and ranges, e.g. [[0, 9]].")
        if dt.kind == "array":
            body = _el("ArrayType", [("BaseType", dt.base_type), ("Namespace", "")])
        else:
            body = _el("SubrangeType", [("BaseType", dt.base_type), ("InitialValue", "")])
        for lo, hi in dt.ranges:
            _sub(body, "Subrange", [("LowerLimit", str(lo)), ("UpperLimit", str(hi))])
    else:
        raise SpecError("kind must be struct, enum, array or subrange.")
    return body


def build_basic(name: str, itf: Interface, internal_vars: list[Var], states: list[ECState],
                transitions: list[ECTransition], algorithms: list[Algorithm], namespace: str = "Main",
                comment: str | None = None) -> bytes:
    check_identifier(name, "type name")
    taken: set[str] = set()
    root = _el("FBType", [("GUID", new_guid()), ("Name", name), ("Comment", comment or "Basic Function Block Type"),
                          ("Namespace", namespace)])
    _header(root, "61499-2", "Template")
    root.append(interface_element(itf, taken))
    basic = _sub(root, "BasicFB", [("ID", new_guid())])
    basic.append(_el("Attribute", [("Name", "FBType.Basic.Algorithm.Order"),
                                   ("Value", ",".join(a.name for a in algorithms))]))
    if internal_vars:
        iv = _sub(basic, "InternalVars")
        for v in internal_vars:
            iv.append(var_element(v, True, taken))
    basic.append(ecc_element(itf, states, transitions, algorithms))
    for a in algorithms:
        basic.append(algorithm_element(a))
    check_basic(itf, internal_vars, states, transitions, algorithms)
    return xmlrt.dumps(xmlrt.new_document(root, DOCTYPE.format(root="FBType")))


def _state_xy(i: int) -> tuple[str, str]:
    if i == 0:
        return "552.9412", "429.4117"  # EAE's default START position
    col, row = (i - 1) % 3, (i - 1) // 3
    return str(1200 + 650 * col), str(200 + 550 * row)


def ecc_element(itf: Interface, states: list[ECState], transitions: list[ECTransition],
                algorithms: list[Algorithm]) -> etree._Element:
    ecc = _el("ECC")
    if not states or states[0].name != "START":
        states = [ECState("START", "Initial State")] + [s for s in states if s.name != "START"]
    pos = {}
    for i, s in enumerate(states):
        x, y = _state_xy(i)
        pos[s.name] = (float(x), float(y))
        st = _sub(ecc, "ECState", [("Name", s.name), ("Comment", s.comment), ("x", x), ("y", y)])
        for a in s.actions:
            _sub(st, "ECAction", [("Algorithm", a.algorithm), ("Output", a.output)])
    for t in transitions:
        (x1, y1), (x2, y2) = pos.get(t.source, (0, 0)), pos.get(t.destination, (0, 0))
        _sub(ecc, "ECTransition", [("Source", t.source), ("Destination", t.destination), ("Condition", t.condition),
                                   ("x", f"{(x1 + x2) / 2:g}"), ("y", f"{(y1 + y2) / 2:g}")])
    return ecc


def algorithm_element(a: Algorithm) -> etree._Element:
    check_identifier(a.name, "algorithm name")
    el = _el("Algorithm", [("ID", a.id or new_guid()), ("Name", a.name), ("Comment", a.comment)])
    for v in a.local_vars:
        el.append(var_element(v, with_id=False))
    st = _sub(el, "ST")
    st.text = etree.CDATA(a.text.replace("\r\n", "\n"))
    return el


def check_basic(itf: Interface, internal_vars: list[Var], states: list[ECState],
                transitions: list[ECTransition], algorithms: list[Algorithm]) -> None:
    state_names = {"START"} | {s.name for s in states}
    alg_names = {a.name for a in algorithms}
    out_events = {e.name for e in itf.event_outputs}
    adapters = {a.name for a in itf.adapter_inputs + itf.adapter_outputs}
    for s in states:
        check_identifier(s.name, "state name")
        for a in s.actions:
            if a.algorithm and a.algorithm not in alg_names:
                raise SpecError(f"State {s.name}: algorithm '{a.algorithm}' is not defined.")
            if a.output and a.output not in out_events and a.output.split(".")[0] not in adapters:
                raise SpecError(f"State {s.name}: '{a.output}' is not an output event.")
    for t in transitions:
        for end in (t.source, t.destination):
            if end not in state_names:
                raise SpecError(f"Transition {t.source}→{t.destination}: unknown state '{end}'.")
        if not t.condition:
            raise SpecError(f"Transition {t.source}→{t.destination} needs a condition (an input event, a guard, or 1).")
    names = {v.name for v in itf.input_vars + itf.output_vars + internal_vars}
    for v in internal_vars:
        check_identifier(v.name, "variable name")
    if len(names) < len(itf.input_vars) + len(itf.output_vars) + len(internal_vars):
        raise SpecError("Internal variable names must differ from interface variable names.")


# -- project registration -------------------------------------------------------------------


def _ns(root) -> str | None:
    return etree.QName(root).namespace


def add_project_item(xf: xmlrt.XmlFile, kind: str, include: str, metadata: list[tuple[str, str]],
                     allow_duplicate: bool = False) -> None:
    """Insert an MSBuild item, sorted case-insensitively by Include like EAE does.

    `allow_duplicate`: EAE registers some CAT companions twice with different DependentUpon values.
    """
    root = xf.root
    ns = _ns(root)
    groups = [g for g in children(root, "ItemGroup") if any(local(i).lower() == kind.lower() for i in children(g))]
    if groups:
        group = groups[0]
    else:
        group = _el("ItemGroup", ns=ns)
        last = children(root, "ItemGroup")[-1]
        xmlrt.insert_child(root, group, root.index(last) + 1)
    for existing in children(group):
        if local(existing).lower() == kind.lower() and existing.get("Include") == include:
            same = [(local(m), m.text) for m in children(existing)] == list(metadata)
            if same or not allow_duplicate:
                return  # already registered
    item = _el(kind, [("Include", include)], ns=ns)
    for k, v in metadata:
        m = _el(k, ns=ns)
        m.text = v
        item.append(m)
    index = None
    for i, existing in enumerate(children(group)):
        if local(existing).lower() == kind.lower() and existing.get("Include", "").lower() > include.lower():
            index = i
            break
    if index is None:
        same = [i for i, e in enumerate(children(group)) if local(e).lower() == kind.lower()]
        index = same[-1] + 1 if same else None
    xmlrt.insert_child(group, item, index)


def set_item_metadata(xf: xmlrt.XmlFile, kind: str, include: str, key: str, value: str | None) -> bool:
    ns = _ns(xf.root)
    for g in children(xf.root, "ItemGroup"):
        for item in children(g, kind):
            if item.get("Include") == include:
                existing = child(item, key)
                if value is None:
                    if existing is not None:
                        xmlrt.remove_child(existing)
                elif existing is not None:
                    existing.text = value
                else:
                    m = _el(key, ns=ns)
                    m.text = value
                    xmlrt.insert_child(item, m)
                return True
    return False


def ensure_folder(xf: xmlrt.XmlFile, category: str, folder: str) -> None:
    """Add `<Folder Type=category Name=…>` entries for every level of a dotted folder path."""
    if not folder.startswith(".") or not all(_IDENT.match(p) or re.match(r"^[\w\- ]+$", p)
                                             for p in folder.split(".")[1:]):
        raise SpecError(f"Folder must look like '.Name' or '.Parent.Child', got '{folder}'.")
    root = xf.root
    ns = _ns(root)
    parts = folder.split(".")[1:]
    for i in range(1, len(parts) + 1):
        name = "." + ".".join(parts[:i])
        folders = children(root, "Folder")
        if any(f.get("Type") == category and f.get("Name") == name for f in folders):
            continue
        el = _el("Folder", [("Type", category), ("Name", name)], ns=ns)
        _sub(el, "Items")
        same = [j for j, f in enumerate(folders) if f.get("Type") == category]
        devices = [j for j, f in enumerate(folders) if f.get("Type") == "SystemDevice"]
        index = same[-1] + 1 if same else (devices[0] if devices else None)
        xmlrt.insert_child(root, el, index)


def template(name: str) -> bytes:
    return (TEMPLATES / name).read_bytes()
