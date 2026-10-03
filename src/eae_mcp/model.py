"""Plain data model for EAE solution content. Everything serializes to JSON via `to_dict`."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def to_dict(obj: Any) -> Any:
    """Dataclass → dict, dropping None, empty containers and private fields."""
    if hasattr(obj, "__dataclass_fields__"):
        obj = asdict(obj)
    if isinstance(obj, dict):
        return {
            k: to_dict(v)
            for k, v in obj.items()
            if not k.startswith("_") and v not in (None, "", [], {})
        }
    if isinstance(obj, list):
        return [to_dict(v) for v in obj]
    return obj


@dataclass
class Event:
    name: str
    id: str | None = None
    comment: str | None = None
    with_vars: list[str] = field(default_factory=list)


@dataclass
class Var:
    name: str
    type: str
    id: str | None = None
    initial_value: str | None = None
    array_size: str | None = None
    comment: str | None = None
    namespace: str | None = None  # namespace of a user type (e.g. SE.Agile for ConnectionStatus_v1_0)


@dataclass
class AdapterDecl:
    """An adapter pin of an FB interface."""

    name: str
    type: str
    namespace: str | None = None
    id: str | None = None
    # "socket"/"plug" (Basic FB <Sockets>/<Plugs>) or "input"/"output" (<AdapterInputs>/<AdapterOutputs>).
    role: str = "input"


@dataclass
class Interface:
    event_inputs: list[Event] = field(default_factory=list)
    event_outputs: list[Event] = field(default_factory=list)
    input_vars: list[Var] = field(default_factory=list)
    output_vars: list[Var] = field(default_factory=list)
    adapter_inputs: list[AdapterDecl] = field(default_factory=list)
    adapter_outputs: list[AdapterDecl] = field(default_factory=list)
    return_type: str | None = None  # functions only
    inout_vars: list[Var] = field(default_factory=list)  # VAR_IN_OUT (functions; passed by reference)

    def pins(self) -> list[tuple[str, str, str, str | None]]:
        """(direction, kind, name, id) for every pin."""
        out = []
        for e in self.event_inputs:
            out.append(("in", "event", e.name, e.id))
        for e in self.event_outputs:
            out.append(("out", "event", e.name, e.id))
        for v in self.input_vars:
            out.append(("in", "data", v.name, v.id))
        for v in self.output_vars:
            out.append(("out", "data", v.name, v.id))
        for a in self.adapter_inputs:
            out.append(("in", "adapter", a.name, a.id))
        for a in self.adapter_outputs:
            out.append(("out", "adapter", a.name, a.id))
        return out

    def pin_name(self, key: str) -> str | None:
        """Resolve a pin reference (ID or name) to its name."""
        for _, _, name, pid in self.pins():
            if key == pid or key == name:
                return name
        return None


@dataclass
class ECAction:
    algorithm: str | None = None
    output: str | None = None


@dataclass
class ECState:
    name: str
    comment: str | None = None
    actions: list[ECAction] = field(default_factory=list)


@dataclass
class ECTransition:
    source: str
    destination: str
    condition: str


@dataclass
class Algorithm:
    name: str
    text: str = ""
    language: str = "ST"
    id: str | None = None
    comment: str | None = None
    local_vars: list[Var] = field(default_factory=list)


@dataclass
class FBInstance:
    name: str
    type: str
    id: str | None = None
    namespace: str | None = None
    kind: str = "FB"  # FB | SubApp
    parameters: dict[str, str] = field(default_factory=dict)  # raw keys as stored ($ID or name)
    attributes: dict[str, str] = field(default_factory=dict)
    mapping: str | None = None  # resource FBs: ID of the application FB they map
    x: float | None = None
    y: float | None = None


@dataclass
class BoundaryPin:
    name: str
    kind: str  # Event | Data | Adapter
    direction: str  # in | out
    id: str | None = None


@dataclass
class Connection:
    kind: str  # event | data | adapter
    source: str  # raw reference as stored
    destination: str


@dataclass
class Network:
    instances: list[FBInstance] = field(default_factory=list)
    pins: list[BoundaryPin] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)


@dataclass
class EnumValue:
    name: str
    value: str | None = None


@dataclass
class DataTypeDef:
    kind: str  # struct | enum | array | subrange | unknown
    base_type: str | None = None
    members: list[Var] = field(default_factory=list)
    values: list[EnumValue] = field(default_factory=list)
    ranges: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class TypeDef:
    """Any library element: FB (basic/composite/cat/sifb), adapter, datatype, function, subapp."""

    kind: str  # basic | composite | cat | cat_hmi | sifb | adapter | datatype | function | subapp | resource | device
    name: str
    namespace: str | None = None
    guid: str | None = None
    comment: str | None = None
    path: str | None = None  # relative to the solution root (or library store)
    format: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)
    interface: Interface = field(default_factory=Interface)
    internal_vars: list[Var] = field(default_factory=list)
    states: list[ECState] = field(default_factory=list)
    transitions: list[ECTransition] = field(default_factory=list)
    algorithms: list[Algorithm] = field(default_factory=list)
    network: Network | None = None
    datatype: DataTypeDef | None = None
    folder: str | None = None  # logical Solution Explorer folder (<Parent>)
    project: str | None = None  # owning project / library name
    source: str = "solution"  # solution | library
    library_version: str | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.name}" if self.namespace else self.name
