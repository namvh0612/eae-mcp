"""Parsers for IEC 61499 library element files (.fbt, .adp, .dt, .fct, .app, .res, .dev).

Parsing is namespace-agnostic: EAE files mix no-namespace documents (types) with
`https://www.se.com/LibraryElements` (system files).
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from ..io import xmlrt
from ..model import (
    AdapterDecl,
    Algorithm,
    BoundaryPin,
    Connection,
    DataTypeDef,
    ECAction,
    ECState,
    ECTransition,
    EnumValue,
    Event,
    FBInstance,
    Interface,
    Network,
    TypeDef,
    Var,
)

TYPE_SUFFIXES = {".fbt", ".adp", ".dt", ".fct", ".app", ".res", ".dev"}


def local(el: etree._Element) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def children(el: etree._Element | None, name: str | None = None) -> list[etree._Element]:
    if el is None:
        return []
    return [c for c in el if isinstance(c.tag, str) and (name is None or local(c) == name)]


def child(el: etree._Element | None, name: str) -> etree._Element | None:
    for c in children(el, name):
        return c
    return None


def _float(v: str | None) -> float | None:
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def parse_var(el: etree._Element) -> Var:
    return Var(
        name=el.get("Name", ""),
        type=el.get("Type", ""),
        id=el.get("ID"),
        initial_value=el.get("InitialValue"),
        array_size=el.get("ArraySize"),
        comment=el.get("Comment"),
        namespace=el.get("Namespace"),
    )


def parse_event(el: etree._Element) -> Event:
    return Event(
        name=el.get("Name", ""),
        id=el.get("ID"),
        comment=el.get("Comment"),
        with_vars=[w.get("Var", "") for w in children(el, "With")],
    )


def parse_interface(el: etree._Element | None) -> Interface:
    itf = Interface()
    if el is None:
        return itf
    itf.return_type = el.get("ReturnValueType")
    for section in children(el):
        name = local(section)
        if name in ("EventInputs", "SubAppEventInputs"):
            itf.event_inputs = [parse_event(e) for e in children(section)]
        elif name in ("EventOutputs", "SubAppEventOutputs"):
            itf.event_outputs = [parse_event(e) for e in children(section)]
        elif name in ("InputVars", "SubAppInputVars"):
            itf.input_vars = [parse_var(v) for v in children(section)]
        elif name in ("OutputVars", "SubAppOutputVars"):
            itf.output_vars = [parse_var(v) for v in children(section)]
        elif name == "InputOutputVars":
            itf.inout_vars = [parse_var(v) for v in children(section)]
        # An interface may contain both <AdapterInputs>/<AdapterOutputs> and <Sockets>/<Plugs>.
        elif name in ("AdapterInputs", "Sockets"):
            role = "socket" if name == "Sockets" else "input"
            itf.adapter_inputs += [_adapter(a, role) for a in children(section)]
        elif name in ("AdapterOutputs", "Plugs"):
            role = "plug" if name == "Plugs" else "output"
            itf.adapter_outputs += [_adapter(a, role) for a in children(section)]
    return itf


def _adapter(el: etree._Element, role: str) -> AdapterDecl:
    return AdapterDecl(
        name=el.get("Name", ""),
        type=el.get("Type", ""),
        namespace=el.get("Namespace"),
        id=el.get("ID"),
        role=role,
    )


def parse_network(el: etree._Element | None) -> Network | None:
    """Parse <FBNetwork> / <SubAppNetwork>."""
    if el is None:
        return None
    net = Network()
    for item in children(el):
        name = local(item)
        if name in ("FB", "SubApp"):
            net.instances.append(FBInstance(
                name=item.get("Name", ""),
                type=item.get("Type", ""),
                id=item.get("ID"),
                namespace=item.get("Namespace"),
                kind=name,
                parameters={p.get("Name", ""): p.get("Value", "") for p in children(item, "Parameter")},
                attributes={a.get("Name", ""): a.get("Value", "") for a in children(item, "Attribute")},
                mapping=item.get("Mapping"),
                x=_float(item.get("x")),
                y=_float(item.get("y")),
            ))
        elif name in ("Input", "Output"):
            net.pins.append(BoundaryPin(
                name=item.get("Name", ""),
                kind=item.get("Type", "Data"),
                direction="in" if name == "Input" else "out",
                id=item.get("ID"),
            ))
        elif name in ("EventConnections", "DataConnections", "AdapterConnections"):
            kind = name.replace("Connections", "").lower()
            for c in children(item, "Connection"):
                net.connections.append(Connection(kind, c.get("Source", ""), c.get("Destination", "")))
    return net


def _parse_basic(td: TypeDef, el: etree._Element) -> None:
    td.attributes.update({a.get("Name", ""): a.get("Value", "") for a in children(el, "Attribute")})
    td.internal_vars = [parse_var(v) for v in children(child(el, "InternalVars"))]
    ecc = child(el, "ECC")
    for s in children(ecc, "ECState"):
        td.states.append(ECState(
            name=s.get("Name", ""),
            comment=s.get("Comment"),
            actions=[ECAction(a.get("Algorithm"), a.get("Output")) for a in children(s, "ECAction")],
        ))
    for t in children(ecc, "ECTransition"):
        td.transitions.append(ECTransition(t.get("Source", ""), t.get("Destination", ""), t.get("Condition", "")))
    for a in children(el, "Algorithm"):
        td.algorithms.append(_parse_algorithm(a))


def _parse_algorithm(a: etree._Element) -> Algorithm:
    body = next((c for c in children(a) if local(c) not in ("VarDeclaration",)), None)
    return Algorithm(
        name=a.get("Name", ""),
        id=a.get("ID"),
        comment=a.get("Comment"),
        language=local(body) if body is not None else "ST",
        text=(body.text or "") if body is not None else "",
        local_vars=[parse_var(v) for v in children(a, "VarDeclaration")],
    )


def _parse_datatype(el: etree._Element) -> DataTypeDef:
    for c in children(el):
        name = local(c)
        if name == "StructuredType":
            return DataTypeDef("struct", members=[parse_var(v) for v in children(c, "VarDeclaration")])
        if name == "EnumeratedType":
            return DataTypeDef(
                "enum",
                base_type=c.get("Type"),
                values=[EnumValue(v.get("Name", ""), v.get("Value")) for v in children(c, "EnumeratedValue")],
            )
        if name == "ArrayType":
            return DataTypeDef(
                "array",
                base_type=c.get("BaseType"),
                ranges=[(s.get("LowerLimit", ""), s.get("UpperLimit", "")) for s in children(c, "Subrange")],
            )
        if name == "SubrangeType":
            return DataTypeDef(
                "subrange",
                base_type=c.get("BaseType"),
                ranges=[(s.get("LowerLimit", ""), s.get("UpperLimit", "")) for s in children(c, "Subrange")],
            )
    return DataTypeDef("unknown")


def parse_type_element(root: etree._Element, path: str | None = None) -> TypeDef:
    """Build a TypeDef from a parsed library element root."""
    tag = local(root)
    td = TypeDef(
        kind="unknown",
        name=root.get("Name", Path(path).stem if path else ""),
        namespace=root.get("Namespace"),
        guid=root.get("GUID"),
        comment=root.get("Comment"),
        path=path,
        format=root.get("Format"),
        attributes={a.get("Name", ""): a.get("Value", "") for a in children(root, "Attribute")},
    )
    # Encrypted/opaque blobs are noise for readers.
    for key in ("nxtDataType", "nxtLibraryData"):
        if key in td.attributes:
            td.attributes[key] = "<opaque>"

    if tag == "AdapterType":
        td.kind = "adapter"
        td.interface = parse_interface(child(root, "InterfaceList"))
    elif tag == "DataType":
        td.kind = "datatype"
        td.datatype = _parse_datatype(root)
    elif tag == "POUType":
        td.kind = "function"
        td.interface = parse_interface(child(root, "InterfaceList"))
        body = child(root, "POUBasicFunction")
        if body is not None:
            td.internal_vars = [parse_var(v) for v in children(child(body, "TempVars"))]
            td.algorithms = [_parse_algorithm(a) for a in children(body, "Algorithm")]
    elif tag == "SubAppType":
        td.kind = "subapp"
        td.interface = parse_interface(child(root, "SubAppInterfaceList"))
        td.network = parse_network(child(root, "SubAppNetwork"))
    elif tag == "FBType":
        td.interface = parse_interface(child(root, "InterfaceList"))
        basic = child(root, "BasicFB")
        network = child(root, "FBNetwork")
        if basic is not None:
            td.kind = "basic"
            _parse_basic(td, basic)
        elif network is not None:
            td.kind = "composite"
            td.network = parse_network(network)
        else:
            td.kind = "sifb"
    else:
        # Resource (.res) and device (.dev) types: format not captured yet, keep what is generic.
        td.kind = {"ResourceType": "resource", "DeviceType": "device"}.get(tag, "unknown")
        td.interface = parse_interface(child(root, "InterfaceList"))
    return td


def load_type(path: Path, rel: str | None = None) -> TypeDef:
    xf = xmlrt.load(path)
    td = parse_type_element(xf.root, rel or str(path))
    if td.kind == "unknown" and path.suffix == ".res":
        td.kind = "resource"
    if td.kind == "unknown" and path.suffix == ".dev":
        td.kind = "device"
    return td
