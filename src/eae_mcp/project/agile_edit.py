"""Agile-style CATs (SE.Agile library pattern): signals as HMI block sub-CATs wired to the logic.

The pattern of SE.Agile application CATs:

- the logic is a Basic FB (`fb<Name>`) with one adapter per signal: a **plug** `aHMI_Indication_<T>_v1_0`
  for each indication (logic → HMI) and a **socket** `aHMI_Control_<T>_v1_0` for each control (HMI → logic);
- each signal is a sub-CAT `HMI_Indication_<T>_v1_0` / `HMI_Control_<T>_v1_0` named like the signal; the logic
  plug connects to the block's `Indication` socket, the block's `Control` plug to the logic socket;
- every block loops `PREQ → PLOAD` (it loads its Minimum/Maximum/Units/… parameters itself);
- all blocks sit on the `HMI_INIT` chain: `Register.HMI_INITO → B1.HMI_INIT`, `B1.HMI_INITO → B2.HMI_INIT`, …,
  `Bn.HMI_INITO → Register.HMI_INIT` (`Register` = `InitComponent_v1_0`);
- the skeleton: `PLOAD → ResolvedAssetName (GetAssetName_v1_0).REQ → Register.INIT (ClassId) → logic.INIT`,
  `logic.INITO → Register.INIT_HMI`, `Register.HMI_READY → IThis.INIT`, `IThis.INITO → Start (EVENTCHAIN).ACK`,
  `Start.EO → PREQ`; IThis carries only `AssetName`.

In the logic, an indication is published with `X.Output := value;` and the event `X.SET`; a control arrives
with `X.ONCHANGE` and `X.Input` (`X.SET` with `X.Output` writes a value back to the HMI).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from lxml import etree

from ..io import xmlrt
from ..model import AdapterDecl, Algorithm, ECAction, ECState, ECTransition, Event, Interface, Var
from . import writer as w
from .changes import ChangeSet
from .edit import EditError, _section as _itf_section
from .network_edit import Container, _add_connection, _connections, _instances, _ns, _set_param_el
from .solution import Solution
from .types import child, children, local, parse_type_element

AGILE = "SE.Agile"
KINDS = ("indication", "control")
TYPES = {"real": "REAL", "bool": "BOOL", "integer": "INT", "string": "STRING"}
CLASS_ID_APPLICATION = 201  # application CATs register with ClassId 201


@dataclass
class AgileSignal:
    name: str
    kind: str = "indication"  # indication (logic → HMI) | control (HMI → logic)
    type: str = "real"  # real | bool | integer | string
    minimum: float | None = None
    maximum: float | None = None
    units: str | None = None
    decimals: int | None = None
    default: str | None = None  # control: value before the HMI writes one
    comment: str | None = None

    @property
    def block(self) -> str:
        return f"HMI_{self.kind.capitalize()}_{self.type.capitalize()}_v1_0"

    @property
    def adapter(self) -> str:
        return f"a{self.block}"

    @property
    def iec_type(self) -> str:
        return TYPES[self.type]


@dataclass
class _Ctx:
    sol: Solution
    c: Container
    cfg: xmlrt.XmlFile | None
    logic_id: str
    logic_itf: Interface
    register_id: str
    hints: list[str] = field(default_factory=list)


def check_signals(sol: Solution, signals: list[AgileSignal]) -> None:
    seen = set()
    for s in signals:
        w.check_identifier(s.name, "signal name")
        if s.name in seen:
            raise EditError(f"Signal '{s.name}' is listed twice.")
        seen.add(s.name)
        if s.kind not in KINDS:
            raise EditError(f"{s.name}: kind must be indication or control.")
        if s.type not in TYPES or (s.kind == "control" and s.type == "string"):
            raise EditError(f"{s.name}: type must be real, bool, integer" + (" or string" if s.kind == "indication" else "")
                            + " (SE.Agile has no HMI_Control_String).")
        if s.type == "bool" and any(v is not None for v in (s.minimum, s.maximum, s.units, s.decimals)):
            raise EditError(f"{s.name}: BOOL blocks have no Minimum/Maximum/Units/DecimalPlaces.")
        if s.minimum is not None and s.maximum is not None and s.minimum >= s.maximum:
            raise EditError(f"{s.name}: minimum must be below maximum.")
        for name in (s.block, s.adapter, "InitComponent_v1_0", "GetAssetName_v1_0"):
            if sol.find_type(name, AGILE) is None:
                raise EditError(f"{name} is not in this solution: add the SE.Agile library (or run eae_catalog_build).")


# -- references -----------------------------------------------------------------------------------------


def _pin_ref(inst_id: str, itf: Interface, pin: str) -> str:
    for p in itf.event_inputs + itf.event_outputs + itf.input_vars + itf.output_vars:
        if p.name == pin:
            return f"${inst_id}.{p.id or pin}"
    for a in itf.adapter_inputs + itf.adapter_outputs:
        if a.name == pin:
            return f"${inst_id}.{a.id or pin}"
    raise EditError(f"No pin '{pin}' on instance {inst_id}.")


def _itf(sol: Solution, type_name: str, namespace: str = AGILE) -> Interface:
    td = sol.find_type(type_name, namespace)
    if td is None:
        raise EditError(f"{namespace}.{type_name} is not in this solution.")
    return td.interface


def _real(v: float) -> str:
    text = f"{v:g}"
    return text if any(ch in text for ch in ".e") else f"{text}.0"


def _string(s: str) -> str:
    return "'" + s.replace("$", "$$").replace("'", "$'") + "'"


# -- network edits ----------------------------------------------------------------------------------------


def _fb(c: Container, name: str, type_name: str, namespace: str, x: float, y: float, inst_id: str | None = None):
    taken = {e.get("ID") for e in _instances(c)} | {e.get("Name") for e in _instances(c)}
    if name in {e.get("Name") for e in _instances(c)}:
        raise EditError(f"{c.label} already has an instance '{name}'.")
    if inst_id is None:
        inst_id = name if name not in taken else w.new_id16(c.taken)
    el = w._el("FB", [("ID", inst_id), ("Name", name), ("Type", type_name), ("x", f"{x:g}"), ("y", f"{y:g}"),
                      ("Namespace", namespace)], ns=_ns(c))
    last = [i for i, e in enumerate(children(c.net_el)) if local(e) in ("FB", "SubApp")]
    xmlrt.insert_child(c.net_el, el, last[-1] + 1 if last else 0)
    c.taken.add(inst_id)
    return el


def _subcat(cfg: xmlrt.XmlFile | None, name: str, type_name: str, namespace: str) -> None:
    if cfg is None:
        return
    root = cfg.root
    if any(e.get("Name") == name for e in children(root, "SubCAT")):
        return
    el = w._el("SubCAT", [("Name", name), ("Type", type_name), ("Namespace", namespace), ("UsedInCAT", "true")],
               ns=w._ns(root))
    kids = children(root)
    subs = [i for i, e in enumerate(kids) if local(e) == "SubCAT"]
    hmi = [i for i, e in enumerate(kids) if local(e) == "HMIInterface"]
    xmlrt.insert_child(root, el, subs[-1] + 1 if subs else (hmi[0] if hmi else 0))


def _column(c: Container, kind: str, default_x: float) -> tuple[float, float]:
    """Indications stack right of the logic, controls left of it (SE.Agile layout)."""
    blocks = [e for e in _instances(c) if e.get("Type", "").startswith(f"HMI_{kind.capitalize()}_")]
    if not blocks:
        return default_x, 2180.0
    x = float(blocks[0].get("x", default_x))
    return x, max(float(e.get("y", 0)) for e in blocks) + (1260 if kind == "indication" else 1400)


def _add_signal(ctx: _Ctx, s: AgileSignal, logic_x: float) -> None:
    sol, c = ctx.sol, ctx.c
    x, y = _column(c, s.kind, logic_x + 2000 if s.kind == "indication" else logic_x - 1700)
    el = _fb(c, s.name, s.block, AGILE, x, y)
    bid = el.get("ID")
    td = sol.find_type(s.block, AGILE)
    for var, value in (("Minimum", None if s.minimum is None else _real(s.minimum)),
                       ("Maximum", None if s.maximum is None else _real(s.maximum)),
                       ("Units", None if s.units is None else _string(s.units)),
                       ("DecimalPlaces", None if s.decimals is None else str(s.decimals)),
                       ("Default", s.default)):
        if value is not None and any(v.name == var for v in td.interface.input_vars):
            _set_param_el(c, sol, el, td, var, value)
    _subcat(ctx.cfg, s.name, s.block, AGILE)
    bitf = td.interface
    # The block loads its own parameters.
    _add_connection(c, "event", _pin_ref(bid, bitf, "PREQ"), _pin_ref(bid, bitf, "PLOAD"))
    # Logic ↔ block.
    logic_pin = f"${ctx.logic_id}.{s.name}"
    if s.kind == "indication":
        _add_connection(c, "adapter", logic_pin, _pin_ref(bid, bitf, "Indication"))
    else:
        _add_connection(c, "adapter", _pin_ref(bid, bitf, "Control"), logic_pin)
    # HMI_INIT chain: insert before the link that closes the chain on Register.HMI_INIT.
    ritf = _itf(sol, "InitComponent_v1_0")
    close = _pin_ref(ctx.register_id, ritf, "HMI_INIT")
    end = next((conn for kind, conn in _connections(c) if kind == "AdapterConnections"
                and conn.get("Destination") in (close, f"${ctx.register_id}.HMI_INIT")), None)
    if end is not None:
        end.set("Destination", _pin_ref(bid, bitf, "HMI_INIT"))
    else:
        _add_connection(c, "adapter", _pin_ref(ctx.register_id, ritf, "HMI_INITO"), _pin_ref(bid, bitf, "HMI_INIT"))
    _add_connection(c, "adapter", _pin_ref(bid, bitf, "HMI_INITO"), close)


# -- logic Basic FB ------------------------------------------------------------------------------------


def _add_adapters_to_logic(cs: ChangeSet, rel: str, signals: list[AgileSignal]) -> None:
    xf = cs.doc(rel)
    itf_el = child(xf.root, "InterfaceList")
    existing = {e.get("Name") for e in itf_el.iter() if isinstance(e.tag, str)
                and local(e) in ("Event", "VarDeclaration", "AdapterDeclaration")}
    for s in signals:
        if s.name in existing:
            raise EditError(f"The logic FB already has a pin named '{s.name}'.")
        sec = _itf_section(itf_el, "Plugs" if s.kind == "indication" else "Sockets")
        xmlrt.insert_child(sec, w.adapter_element(AdapterDecl(s.name, s.adapter, AGILE)))


def st_hints(signals: list[AgileSignal]) -> list[str]:
    out = []
    for s in signals:
        if s.kind == "indication":
            out.append(f"{s.name}: in an algorithm `{s.name}.Output := <{s.iec_type} value>;` then emit `{s.name}.SET` "
                       "(ECAction Output, or only when the value changed)")
        else:
            out.append(f"{s.name}: transition condition `{s.name}.ONCHANGE`, read `{s.name}.Input`; "
                       f"write back with `{s.name}.Output` + `{s.name}.SET`")
    return out


def logic_template(name: str, signals: list[AgileSignal], namespace: str = "Main") -> tuple:
    """Interface, internal vars, ECC and algorithms of a new Agile logic Basic FB."""
    ind = [s for s in signals if s.kind == "indication"]
    ctl = [s for s in signals if s.kind == "control"]
    itf = Interface(
        event_inputs=[Event("INIT", comment="Initialization Request", with_vars=["QI"]),
                      Event("REQ", comment="Normal Execution Request", with_vars=["QI"])],
        event_outputs=[Event("INITO", comment="Initialization Confirm", with_vars=["QO"]),
                       Event("CNF", comment="Execution Confirmation", with_vars=["QO"])],
        input_vars=[Var("QI", "BOOL", comment="Input event qualifier")],
        output_vars=[Var("QO", "BOOL", comment="Output event qualifier")],
        adapter_inputs=[AdapterDecl(s.name, s.adapter, AGILE, role="socket") for s in ctl],
        adapter_outputs=[AdapterDecl(s.name, s.adapter, AGILE, role="plug") for s in ind])
    internal = [Var(s.name + "_Value", s.iec_type if s.type != "string" else "STRING[1024]",
                    comment=f"Last value {'sent to' if s.kind == 'indication' else 'received from'} the HMI ({s.name})")
                for s in signals]
    init = "QO := QI;\n" + "".join(f"{s.name}_Value := {s.default};\n" for s in ctl if s.default is not None)
    req = ["QO := QI;", "(* Compute the indications here; each is sent to its HMI block. *)"]
    req += [f"{s.name}.Output := {s.name}_Value;" for s in ind]
    algorithms = [Algorithm("INIT", init, comment="Initialization algorithm"),
                  Algorithm("REQ", "\n".join(req) + "\n", comment="Normally executed algorithm")]
    states = [ECState("START", "Initial State"),
              ECState("INIT", "Initialization", [ECAction("INIT", "INITO")]),
              ECState("REQ", "Normal execution", [ECAction("REQ", "CNF")] + [ECAction(None, f"{s.name}.SET") for s in ind])]
    transitions = [ECTransition("START", "INIT", "INIT"), ECTransition("INIT", "START", "1"),
                   ECTransition("START", "REQ", "REQ"), ECTransition("REQ", "START", "1")]
    if ctl:
        algorithms.append(Algorithm("CONTROL", "".join(f"{s.name}_Value := {s.name}.Input;\n" for s in ctl),
                                    comment="Values written by the operator"))
        states.append(ECState("CONTROL", "Operator input", [ECAction("CONTROL", None)]))
        transitions += [ECTransition("START", "CONTROL", " OR ".join(f"{s.name}.ONCHANGE" for s in ctl)),
                        ECTransition("CONTROL", "START", "1")]
    return itf, internal, states, transitions, algorithms


# -- public operations ---------------------------------------------------------------------------------


def _find(c: Container, type_name: str) -> etree._Element | None:
    return next((e for e in _instances(c) if e.get("Type") == type_name), None)


def _logic_instance(sol: Solution, c: Container, logic: str | None):
    if logic:
        el = next((e for e in _instances(c) if e.get("Name") == logic), None)
        if el is None:
            raise EditError(f"{c.label} has no instance '{logic}'.")
    else:
        basics = [e for e in _instances(c) if (td := sol.find_type(e.get("Type", ""), e.get("Namespace")))
                  and td.kind == "basic" and any(a.type.startswith("aHMI_") for a in
                                                 td.interface.adapter_inputs + td.interface.adapter_outputs)]
        if len(basics) != 1:
            raise EditError("Name the logic Basic FB instance (logic=…): "
                            + (f"candidates {[e.get('Name') for e in basics]}." if basics else "none carries HMI adapters."))
        el = basics[0]
    td = sol.find_type(el.get("Type", ""), el.get("Namespace"))
    if td is None or td.kind != "basic":
        raise EditError(f"{el.get('Name')} is not a Basic FB of this solution; Agile signals need adapters on the logic.")
    return el, td


def add_signals(sol: Solution, cat: str, signals: list[AgileSignal], logic: str | None = None) -> ChangeSet:
    """Add Agile signals to an existing CAT: HMI blocks, adapters on the logic Basic FB, all wiring."""
    from .network_edit import _finish_net, resolve_container

    check_signals(sol, signals)
    cs = ChangeSet(sol.root, f"add Agile signals {', '.join(s.name for s in signals)} to {cat}")
    c = resolve_container(cs, sol, cat)
    cat_obj = sol.cats.get(c.owner.qualified_name if c.owner else "")
    if cat_obj is None:
        raise EditError(f"{cat} is not a CAT.")
    reg = _find(c, "InitComponent_v1_0")
    if reg is None:
        raise EditError(f"{cat} has no InitComponent_v1_0 (Register): it is not an Agile CAT. "
                        "Create one with eae_agile_cat_create.")
    logic_el, logic_td = _logic_instance(sol, c, logic)
    names = {e.get("Name") for e in _instances(c)}
    for s in signals:
        if s.name in names:
            raise EditError(f"{cat} already has an instance '{s.name}'.")
    _add_adapters_to_logic(cs, logic_td.path, signals)
    itf = copy.deepcopy(logic_td.interface)
    ctx = _Ctx(sol, c, cs.doc(cat_obj.cfg_file), logic_el.get("ID") or logic_el.get("Name"), itf,
               reg.get("ID") or reg.get("Name"))
    for s in signals:
        _add_signal(ctx, s, float(logic_el.get("x", 4080)))
    _finish_net(cs, sol, c)
    # The validator still sees the logic's old interface: the new adapter pins are written in this change set.
    stale = {f"${ctx.logic_id}.{s.name}" for s in signals}
    cs.warnings = [x for x in cs.warnings if not any(f"'{ref}'" in x for ref in stale)]
    cs.warnings += [f"info: logic {logic_td.name}: {h}" for h in st_hints(signals)]
    parse_type_element(xmlrt.parse_bytes(cs.changes[logic_td.path].new).root, logic_td.path)
    return cs


def create_agile_cat(sol: Solution, name: str, signals: list[AgileSignal], logic: str | None = None,
                     class_id: int = CLASS_ID_APPLICATION, folder: str | None = None,
                     symbol: str = "sDefault", web_symbol: str | None = "seDefault",
                     comment: str | None = None, now=None) -> ChangeSet:
    """New Agile CAT: IThis = AssetName, a new logic Basic FB with one adapter per signal, the SE.Agile
    skeleton (GetAssetName → InitComponent → logic → EVENTCHAIN) and one HMI block per signal."""
    from .cat_edit import create_cat
    from .edit import create_basic, target_project

    check_signals(sol, signals)
    logic = logic or f"fb{name[2:] if name.startswith('ac') else name}"
    cs = ChangeSet(sol.root, f"create Agile CAT {name}")
    t = target_project(sol)
    litf, internal, states, transitions, algorithms = logic_template(logic, signals, t.namespace)
    create_basic(sol, logic, litf, internal, states, transitions, algorithms,
                 comment=f"Logic of {name} (Agile)", folder=folder, cs=cs)
    asset = Var("AssetName", "STRING[32]")
    itf = Interface(event_inputs=[Event("PLOAD", comment="Load parameters", with_vars=["AssetName"]),
                                  Event("REQ", comment="Normal Execution Request")],
                    event_outputs=[Event("PREQ", comment="Parameters loaded"),
                                   Event("CNF", comment="Execution Confirmation")],
                    input_vars=[asset])
    hmi = Interface(event_inputs=[Event("REQ", with_vars=["AssetName"])], input_vars=[Var("AssetName", "STRING")])
    create_cat(sol, name, itf, hmi, symbol, web_symbol, folder, None, comment, now, cs=cs)

    rel = f"{t.dir}{name}/{name}.fbt"
    xf = cs.doc(rel)
    owner = parse_type_element(xf.root, rel)
    net_el = child(xf.root, "FBNetwork")
    c = Container("type", name, rel, xf, net_el, owner, True,
                  taken={e.get("ID") for e in xf.root.iter() if isinstance(e.tag, str) and e.get("ID")})
    ithis = next(e for e in _instances(c) if e.get("Name") == "IThis")
    ithis.set("x", "5020"), ithis.set("y", "660")
    hitf = parse_type_element(xmlrt.parse_bytes(cs.changes[f"{t.dir}{name}/{name}_HMI.fbt"].new).root, rel).interface
    lid = w.new_id16(c.taken)
    _fb(c, "ResolvedAssetName", "GetAssetName_v1_0", AGILE, 1820, 600)
    reg = _fb(c, "Register", "InitComponent_v1_0", AGILE, 2940, 600)
    _set_param_el(c, sol, reg, sol.find_type("InitComponent_v1_0", AGILE), "ClassId", str(class_id))
    _fb(c, "Logic", logic, t.namespace, 4080, 1720, lid)
    start = _fb(c, "Start", "EVENTCHAIN", "Runtime.Standard", 580, 600)
    for k, v in (("TIMEOUT", "T#0ms"), ("DELAY", "T#2ms"), ("NAME", "'Sequences'"), ("PRIO", "100")):
        xmlrt.insert_child(start, w._el("Parameter", [("Name", f"${k}"), ("Value", v)], ns=_ns(c)))
    g, r = _itf(sol, "GetAssetName_v1_0"), _itf(sol, "InitComponent_v1_0")
    ith = ithis.get("ID")
    b = {p.name: f"${p.id or p.name}" for p in itf.event_inputs + itf.event_outputs + itf.input_vars}
    ev = [(b["PLOAD"], _pin_ref("ResolvedAssetName", g, "REQ")),
          (_pin_ref("ResolvedAssetName", g, "CNF"), _pin_ref("Register", r, "INIT")),
          (_pin_ref("ResolvedAssetName", g, "CNF"), _pin_ref(ith, hitf, "REQ")),
          (_pin_ref("Register", r, "GET_CONFIG"), _pin_ref("Register", r, "END_CONFIG")),
          (_pin_ref("Register", r, "INITO"), _pin_ref(lid, litf, "INIT")),
          (_pin_ref(lid, litf, "INITO"), _pin_ref("Register", r, "INIT_HMI")),
          (_pin_ref("Register", r, "HMI_READY"), _pin_ref(ith, hitf, "INIT")),
          (_pin_ref(ith, hitf, "INITO"), "$Start.ACK"),
          ("$Start.EO", b["PREQ"]),
          (b["REQ"], _pin_ref(lid, litf, "REQ")),
          (_pin_ref(lid, litf, "CNF"), b["CNF"])]
    for src, dst in ev:
        _add_connection(c, "event", src, dst)
    for src, dst in ((b["AssetName"], _pin_ref("ResolvedAssetName", g, "AssetName")),
                     (_pin_ref("ResolvedAssetName", g, "ResolvedAssetName"), _pin_ref("Register", r, "AssetName")),
                     (_pin_ref("ResolvedAssetName", g, "ResolvedAssetName"), _pin_ref(ith, hitf, "AssetName"))):
        _add_connection(c, "data", src, dst)
    ctx = _Ctx(sol, c, cs.doc(f"{t.dir}{name}/{name}.cfg"), lid, litf, "Register")
    for s in signals:
        _add_signal(ctx, s, 4080)
    if not signals:
        _add_connection(c, "adapter", _pin_ref("Register", r, "HMI_INITO"), _pin_ref("Register", r, "HMI_INIT"))
    cs.commit_docs()
    parse_type_element(xmlrt.parse_bytes(cs.changes[rel].new).root, rel)
    cs.warnings += [f"info: logic {logic}: {h}" for h in st_hints(signals)]
    cs.warnings.append(f"info: the REQ algorithm of {logic} only forwards <signal>_Value; put the computation there "
                       "(eae_basic_upsert_algorithm).")
    return cs
