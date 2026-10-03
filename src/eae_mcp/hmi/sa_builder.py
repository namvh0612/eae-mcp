"""Generate situation-awareness CAT symbols (.NET HMI and eHMI) from a declarative design.

A design lists *elements*, each bound to a variable of the CAT's HMI interface (IThis input, i.e. data the
CAT sends to the HMI):

- value : label + live number (+ unit) and, with `range`, a moving analog indicator: gray span, normal band,
          alarm-limit ticks and a pointer that turns to the priority color only outside the limits;
- state : label + state text from a value→text table; abnormal states get the priority color at runtime;
- alarm : indicator in the title row: hidden when inactive, shape + color + number of the priority when active
          (BOOL active flag, or INT priority code 0..4);
- text  : label + live string;
- setpoint : operator entry of a number — basic: a TextBox on an IThis *output* variable (EAE writes it and fires
          the output event that carries it); Agile: the current value of an HMI_Control_Real/Integer block with
          −/+ step buttons that send `bridge.FireEvent_CNF(value)` (as SE.Agile control blocks are written);
- command  : a button — basic: fires an IThis output event (`FireEvent_<EVENT>(value)`); Agile: writes `value`
          (true/false/toggle or an integer) to an HMI_Control_Bool/Integer block; optional confirmation dialog.
          Commands are generated for .NET HMI only (no verified eHMI write API in the samples).

Widgets and code follow what EAE itself writes (learned from EAE-written files): static graphics are
gray, colors are only set at runtime by generated code, so the result passes eae_hmi_review.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass, field

from ..model import Interface
from . import designer as ds
from . import sa_style as st

NL = "\r\n"
T3 = "\t\t\t"

COMMAND_KINDS = ("setpoint", "command")
DOTNET_T = {"REAL": ("float", "0F"), "LREAL": ("double", "0D"), "INT": ("short", "((short)(0))"),
            "DINT": ("int", "0"), "UINT": ("ushort", "((ushort)(0))"), "USINT": ("byte", "((byte)(0))"),
            "BYTE": ("byte", "((byte)(0))"), "SINT": ("sbyte", "((sbyte)(0))"), "BOOL": ("bool", "false"),
            "STRING": ("string", "null")}
EHMI_T = {"REAL": "double", "LREAL": "double", "INT": "short", "BOOL": "bool", "STRING": "string"}


class DesignError(ValueError):
    pass


@dataclass
class ElementSpec:
    kind: str  # value | state | alarm | text
    var: str
    label: str | None = None
    unit: str | None = None
    range: tuple[float, float] | None = None  # span of the analog indicator
    normal: tuple[float, float] | None = None  # normal operating range (shaded)
    limits: tuple[float | None, float | None] | None = None  # low / high alarm limits
    priority: int = 2
    states: dict[str, str] = field(default_factory=dict)  # value → text ("true"/"false" for BOOL)
    abnormal: list[str] = field(default_factory=list)  # state values shown as abnormal
    step: float | None = None  # setpoint (Agile): −/+ step
    value: str | None = None  # command: value sent (true/false/toggle, integer, number)
    confirm: bool = False  # command/setpoint: ask the operator before sending


@dataclass
class SymbolDesign:
    title: str
    elements: list[ElementSpec]
    width: int = 240


# -- validation --------------------------------------------------------------------------------


def _base(t: str) -> str:
    return t.split("[")[0].upper()


PSEUDO_IEC = {"float": "REAL", "double": "LREAL", "short": "INT", "int": "DINT", "ushort": "UINT", "byte": "USINT",
              "bool": "BOOL", "string": "STRING"}


def check_design(d: SymbolDesign, hmi: Interface, technology: str, bridges: dict | None = None) -> list[str]:
    """Raise DesignError for errors; return warnings. `bridges`: var (sub-CAT path) → agile_blocks.Bridge."""
    bridges = bridges or {}
    warnings: list[str] = []
    if not d.title.strip() or len(d.title) > 40:
        raise DesignError("title must be 1..40 characters.")
    if not 160 <= d.width <= 600:
        raise DesignError("width must be 160..600 px.")
    if not d.elements:
        raise DesignError("A symbol needs at least one element.")
    types = {v.name: v.type for v in hmi.input_vars if v.name not in ("QI",)}
    for var, b in bridges.items():
        if b.val_type not in PSEUDO_IEC:
            raise DesignError(f"{var}: block {b.block} has no usable .NET bridge symbol (sValChanged with Val).")
        types[var] = PSEUDO_IEC[b.val_type]
        if technology in ("ehmi", "both") and not b.web_class:
            raise DesignError(f"{var}: block {b.block} has no eHMI bridge symbol (seVal…); use technology='hmi'.")
    carried = {w for e in hmi.event_inputs if e.name != "INIT" for w in e.with_vars} | set(bridges)
    seen = set()
    if any(e.kind in COMMAND_KINDS for e in d.elements) and technology in ("ehmi", "both"):
        warnings.append("info: setpoints/commands are drawn on the .NET symbol only (no verified eHMI write API); "
                        "the eHMI symbol shows Agile setpoint values read-only.")
    controlled = {e.var for e in d.elements if e.kind in COMMAND_KINDS}
    for e in d.elements:
        if e.kind not in ("value", "state", "alarm", "text") + COMMAND_KINDS:
            raise DesignError(f"{e.var}: kind must be value, state, alarm, text, setpoint or command.")
        if e.kind in COMMAND_KINDS:
            _check_command(e, hmi, bridges, warnings)
            key = (e.kind, e.var, e.value)
            if key in seen:
                raise DesignError(f"{e.var} is used twice as {e.kind}" + (f" with value {e.value}" if e.value else "") + ".")
            seen.add(key)
            continue
        if e.var in controlled and e.kind in ("value", "text"):
            raise DesignError(f"{e.var}: a setpoint/command block already shows its value; use a state element "
                              "for its feedback text instead.")
        if e.var not in types:
            raise DesignError(f"'{e.var}' is not an input of the HMI interface (vars: {', '.join(types) or 'none'}). "
                              "Add it with eae_fb_update_interface on <Cat>_HMI first.")
        if (e.kind, e.var) in seen:
            raise DesignError(f"{e.var} is used twice as {e.kind}.")
        seen.add((e.kind, e.var))
        t = _base(types[e.var])
        if t not in DOTNET_T or (technology in ("ehmi", "both") and t not in EHMI_T):
            raise DesignError(f"{e.var}: type {types[e.var]} is not supported for {technology} symbols.")
        if e.var not in carried:
            warnings.append(f"warning: {e.var} is not carried by any HMI input event (WITH); it will never update.")
        if e.kind == "value":
            if t in ("BOOL", "STRING"):
                raise DesignError(f"{e.var}: a value element needs a number, not {t}.")
            if e.range:
                lo, hi = e.range
                if not lo < hi:
                    raise DesignError(f"{e.var}: range must be [low, high] with low < high.")
                for name, pair in (("normal", e.normal), ("limits", e.limits)):
                    for x in pair or ():
                        if x is not None and not lo <= x <= hi:
                            raise DesignError(f"{e.var}: {name} {x} is outside range {e.range}.")
                if e.normal and not e.normal[0] < e.normal[1]:
                    raise DesignError(f"{e.var}: normal must be [low, high].")
            elif e.normal or e.limits:
                raise DesignError(f"{e.var}: normal/limits need a range for the analog indicator.")
            elif e.var in bridges and bridges[e.var].has_span:
                warnings.append(f"info: {e.var}: the .NET indicator uses the block's Minimum/Maximum at runtime; "
                                "give range/normal/limits to shade the normal band (the eHMI shows the number only).")
            elif not e.range:
                warnings.append(f"info: {e.var} has no range; add range/normal/limits so the operator sees the "
                                "value against its normal band (SA level 2).")
        if e.kind == "state" and not e.states:
            raise DesignError(f"{e.var}: a state element needs states, e.g. {{'0': 'Stopped', '1': 'Running'}}.")
        if e.kind == "state" and t == "BOOL" and not set(e.states) <= {"true", "false"}:
            raise DesignError(f"{e.var}: BOOL states use the keys 'true' and 'false'.")
        if e.kind == "alarm" and t not in ("BOOL", "INT", "SINT", "DINT", "USINT", "BYTE", "UINT"):
            raise DesignError(f"{e.var}: an alarm element needs BOOL (active) or an integer priority code.")
        if e.kind == "text" and t != "STRING":
            raise DesignError(f"{e.var}: a text element needs a STRING.")
        st.priority(e.priority)
        if any(c in (e.label or "") + (e.unit or "") + "".join(e.states.values()) for c in '"\\\r\n'):
            raise DesignError(f"{e.var}: texts must not contain quotes, backslashes or line breaks.")
    return warnings


def _check_command(e: ElementSpec, hmi: Interface, bridges: dict, warnings: list[str]) -> None:
    if any(c in (e.label or "") for c in '"\\\r\n'):
        raise DesignError(f"{e.var}: texts must not contain quotes, backslashes or line breaks.")
    br = bridges.get(e.var)
    if br is not None:
        if not br.control:
            raise DesignError(f"{e.var}: {e.kind} needs an HMI_Control_* block, {br.block} is an indication.")
        if e.kind == "setpoint":
            if br.val_type not in ("float", "short"):
                raise DesignError(f"{e.var}: a setpoint needs HMI_Control_Real or HMI_Control_Integer.")
            if not e.step or e.step <= 0:
                raise DesignError(f"{e.var}: give the −/+ step of the setpoint (step > 0, in engineering units).")
        else:
            if br.val_type == "bool":
                if (e.value or "").lower() not in ("true", "false", "toggle"):
                    raise DesignError(f"{e.var}: command value must be true, false or toggle.")
            elif br.val_type == "short":
                if not re.fullmatch(r"-?\d+", e.value or ""):
                    raise DesignError(f"{e.var}: command value must be an integer (e.g. a mode number).")
            else:
                raise DesignError(f"{e.var}: commands write HMI_Control_Bool/Integer blocks; use a setpoint for Real.")
        return
    outs = {v.name: v.type for v in hmi.output_vars if v.name not in ("QO", "STATUS")}
    events = {ev.name: ev for ev in hmi.event_outputs if ev.name != "INITO"}
    if e.kind == "setpoint":
        if e.var not in outs:
            raise DesignError(f"'{e.var}' is not an output variable of the HMI interface (outputs: "
                              f"{', '.join(outs) or 'none'}). Add it, carried by an output event, with "
                              "eae_fb_update_interface on <Cat>_HMI.")
        if _base(outs[e.var]) not in DOTNET_T or _base(outs[e.var]) in ("BOOL", "STRING"):
            raise DesignError(f"{e.var}: a setpoint needs a number, not {outs[e.var]}.")
        if not any(e.var in ev.with_vars for ev in events.values()):
            warnings.append(f"warning: {e.var} is not carried by any HMI output event (WITH); the CAT never receives it.")
        return
    if e.var not in events:
        raise DesignError(f"'{e.var}' is not an output event of the HMI interface (events: "
                          f"{', '.join(events) or 'none'}). A basic command fires an IThis output event.")
    ev = events[e.var]
    if len(ev.with_vars) > 1:
        raise DesignError(f"{e.var}: a command button sends one value; event {e.var} carries {ev.with_vars}.")
    if ev.with_vars:
        t = _base(outs.get(ev.with_vars[0], ""))
        ok = ((t == "BOOL" and (e.value or "").lower() in ("true", "false")) or
              (t in DOTNET_T and t not in ("BOOL", "STRING") and re.fullmatch(r"-?\d+(\.\d+)?", e.value or "")))
        if not ok:
            raise DesignError(f"{e.var}: give the value sent with {ev.with_vars[0]} ({t or '?'}).")


# -- layout ------------------------------------------------------------------------------------


@dataclass
class Box:
    name: str
    kind: str  # card | title | label | value | state | text | track | band | tick | pointer | alarm | alarmtext | exec
    x: float
    y: float
    w: float
    h: float
    text: str = ""
    var: str | None = None
    element: ElementSpec | None = None
    shape: str | None = None


def _id(s: str) -> str:
    return re.sub(r"\W", "_", s)


def layout(d: SymbolDesign, bridges: dict | None = None) -> tuple[list[Box], int, int]:
    W = d.width
    pad = 8
    boxes = [Box("card", "card", 0, 0, W, 0), Box("title", "title", pad, 4, W - 2 * pad - 30, 20, d.title)]
    bridges = bridges or {}
    execs: set[str] = set()

    def exec_box(e: ElementSpec) -> None:  # one data accessor per variable, shared by its elements
        if e.var not in execs:
            execs.add(e.var)
            boxes.append(Box(f"x{_id(e.var)}", "exec", 0, 0, 0, 0, var=e.var, element=e))
    alarms = [e for e in d.elements if e.kind == "alarm"]
    for i, e in enumerate(alarms):
        x = W - pad - 22 - i * 26
        n = _id(e.var)
        boxes.append(Box(f"alm{n}", "alarm", x, 4, 20, 20, var=e.var, element=e))
        boxes.append(Box(f"almTxt{n}", "alarmtext", x + 6, 6, 10, 14, var=e.var, element=e))
        exec_box(e)
    y = 30.0
    for e in d.elements:
        n = _id(e.var)
        label = e.label or e.var
        if e.kind == "value":
            text = f"{label} ({e.unit})" if e.unit else label
            boxes.append(Box(f"lbl{n}", "label", pad, y + 3, W - 2 * pad - 90, 18, text, element=e))
            boxes.append(Box(f"val{n}", "value", W - pad - 90, y, 90, 22, var=e.var, element=e))
            y += 26
            dynamic = not e.range and e.var in bridges and bridges[e.var].has_span
            if e.var in bridges:
                exec_box(e)
            if dynamic:
                tw = W - 2 * pad
                boxes.append(Box(f"trk{n}", "track", pad, y + 3, tw, 8, element=e, shape="dynamic"))
                boxes.append(Box(f"ptr{n}", "pointer", pad - 2, y, 4, 14, var=e.var, element=e, shape="dynamic"))
                y += 20
            if e.range:
                tw = W - 2 * pad
                boxes.append(Box(f"trk{n}", "track", pad, y + 3, tw, 8, element=e))
                lo, hi = e.range

                def px(v: float) -> float:
                    return pad + (v - lo) / (hi - lo) * tw
                if e.normal:
                    boxes.append(Box(f"nrm{n}", "band", px(e.normal[0]), y + 3, px(e.normal[1]) - px(e.normal[0]), 8,
                                     element=e))
                for j, lim in enumerate(e.limits or ()):
                    if lim is not None:
                        boxes.append(Box(f"lim{n}{'LH'[j]}", "tick", px(lim), y, 0, 14, element=e))
                boxes.append(Box(f"ptr{n}", "pointer", pad - 2, y, 4, 14, var=e.var, element=e))
                exec_box(e)
                y += 20
        elif e.kind == "state":
            boxes.append(Box(f"lbl{n}", "label", pad, y + 3, W / 2 - pad, 18, label, element=e))
            boxes.append(Box(f"sta{n}", "state", W / 2, y, W / 2 - pad, 22, "—", var=e.var, element=e))
            exec_box(e)
            y += 26
        elif e.kind == "text":
            boxes.append(Box(f"lbl{n}", "label", pad, y + 3, W / 2 - pad, 18, label, element=e))
            boxes.append(Box(f"txt{n}", "text", W / 2, y, W / 2 - pad, 22, var=e.var, element=e))
            if e.var in bridges:
                exec_box(e)
            y += 26
        elif e.kind == "setpoint":
            text = f"{label} ({e.unit})" if e.unit else label
            if e.var in bridges:
                boxes.append(Box(f"lbl{n}", "label", pad, y + 3, W - 2 * pad - 130, 18, text, element=e))
                boxes.append(Box(f"val{n}", "value", W - pad - 130, y, 76, 22, var=e.var, element=e))
                boxes.append(Box(f"dn{n}", "stepdn", W - pad - 50, y, 24, 22, "−", var=e.var, element=e))
                boxes.append(Box(f"up{n}", "stepup", W - pad - 24, y, 24, 22, "+", var=e.var, element=e))
                exec_box(e)
            else:
                boxes.append(Box(f"lbl{n}", "label", pad, y + 3, W - 2 * pad - 90, 18, text, element=e))
                boxes.append(Box(f"sp{n}", "entry", W - pad - 90, y, 90, 22, var=e.var, element=e))
            y += 26
        elif e.kind == "command":
            i = d.elements.index(e)
            boxes.append(Box(f"cmd{n}{i}", "button", pad, y, W - 2 * pad, 24, label, var=e.var, element=e))
            if e.var in bridges:
                exec_box(e)
            y += 28
    H = int(y + 6)
    boxes[0].h = H
    return boxes, W, H


# -- .NET HMI --------------------------------------------------------------------------------------


def _c(rgb: st.RGB) -> str:
    r, g, b = rgb
    return f"new NxtControl.Drawing.Color(((byte)({r})), ((byte)({g})), ((byte)({b})))"


def _brush(rgb: st.RGB) -> str:
    return f"new NxtControl.Drawing.Brush({_c(rgb)})"


def _pen(rgb: st.RGB, width: float = 1) -> str:
    return f"new NxtControl.Drawing.Pen({_c(rgb)}, {width:g}F, NxtControl.Drawing.DashStyle.Solid)"


def _font(size: int, bold: bool = False) -> str:
    return f'new NxtControl.Drawing.Font("{st.FONT}", {size}F, System.Drawing.FontStyle.{"Bold" if bold else "Regular"})'


def _rect(b: Box) -> str:
    return ("new NxtControl.Drawing.RectF(" + ", ".join(f"((float)({ds.num(v)}))" for v in (b.x, b.y, b.w, b.h)) + ")")


def _shape_points(b: Box, shape: str) -> list[tuple[float, float]]:
    x, y, w, h = b.x, b.y, b.w, b.h
    if shape == "triangle":
        return [(x + w / 2, y), (x + w, y + h), (x, y + h)]
    if shape == "diamond":
        return [(x + w / 2, y), (x + w, y + h / 2), (x + w / 2, y + h), (x, y + h / 2)]
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def _code_label(b: Box, align: str = "MiddleRight", bold: bool = False) -> list[tuple[str, str]]:
    """A plain Label whose Text is set by the generated code (values of Agile blocks)."""
    return [("AngleIgnore", "true"), ("BorderStyle", "System.Windows.Forms.BorderStyle.None"),
            ("Bounds", _rect(b)), ("Brush", 'new NxtControl.Drawing.Brush("Transparent")'),
            ("Font", _font(st.SIZE_TEXT, bold)), ("FontScale", "true"), ("Name", f'"{b.name}"'),
            ("Pen", 'new NxtControl.Drawing.Pen("Transparent")'), ("Text", ds.string("—")),
            ("TextAlignment", f"NxtControl.Drawing.ContentAlignment.{align}"),
            ("TextAutoSizeHorizontalOffset", "10"), ("TextColor", _c(st.TEXT)),
            ("TextPadding", "new NxtControl.Drawing.Padding(2)")]


def _dotnet_sections(boxes: list[Box], types: dict[str, str],
                     bridges: dict | None = None) -> tuple[list[str], list[tuple[str, list[str]]], list[str]]:
    bridges = bridges or {}
    prelude, sections, fields = [], [], []
    for b in boxes:
        n = b.name
        props: list[tuple[str, str]] = []
        extra: list[str] = []  # multi-line statements placed after the sorted properties
        begin = False
        if b.kind in ("card", "track", "band", "pointer"):
            typ = "NxtControl.GuiFramework.Rectangle"
            color = {"card": st.PANEL, "track": st.TRACK, "band": st.NORMAL_BAND, "pointer": st.POINTER}[b.kind]
            pen = st.BORDER if b.kind in ("card", "track") else color
            props = [("Bounds", _rect(b)), ("Brush", _brush(color)), ("Font", _font(8)), ("Name", f'"{n}"'),
                     ("Pen", _pen(pen))]
        elif b.kind in ("title", "label", "alarmtext"):
            typ = "NxtControl.GuiFramework.FreeText"
            size, bold, color = ((st.SIZE_TITLE, True, st.TEXT) if b.kind == "title" else
                                 (st.SIZE_TEXT, True, st.TEXT) if b.kind == "alarmtext" else (st.SIZE_TEXT, False, st.TEXT_2))
            props = [("Color", _c(color)), ("Font", _font(size, bold)), ("Location",
                     f"new NxtControl.Drawing.PointF({ds.num(b.x)}, {ds.num(b.y)})"), ("Name", f'"{n}"'),
                     ("Text", ds.string(b.text))]
            if b.kind == "alarmtext":
                props += [("Visible", "false")]
        elif b.kind == "tick":
            typ = "NxtControl.GuiFramework.Line"
            props = [("EndPoint", f"new NxtControl.Drawing.PointF({ds.num(b.x)}, {ds.num(b.y + b.h)})"),
                     ("Name", f'"{n}"'), ("Pen", _pen(st.LIMIT, 2)),
                     ("StartPoint", f"new NxtControl.Drawing.PointF({ds.num(b.x)}, {ds.num(b.y)})")]
        elif b.kind == "alarm":
            shape = st.priority(b.element.priority).shape
            if shape == "circle":
                typ = "NxtControl.GuiFramework.Ellipse"
                props = [("Bounds", _rect(b)), ("Brush", _brush(st.INDICATOR_IDLE)), ("Font", _font(8)),
                         ("Name", f'"{n}"'), ("Pen", _pen(st.BORDER))]
            else:
                typ = "NxtControl.GuiFramework.Polygon"
                props = [("Bounds", _rect(b)), ("Brush", _brush(st.INDICATOR_IDLE)), ("Closed", "true"),
                         ("Font", _font(8)), ("Name", f'"{n}"'), ("Pen", _pen(st.BORDER))]
                pts = _shape_points(b, shape)
                extra = [f"{T3}this.{n}.Points.AddRange(new NxtControl.Drawing.PointF[] {{"]
                extra += [f"{T3}new NxtControl.Drawing.PointF({ds.num(px)}, {ds.num(py)})," for px, py in pts]
                extra[-1] = extra[-1][:-1] + "});"
            props += [("Visible", "false")]
        elif b.kind in ("value", "text") and b.var in bridges:
            typ = "NxtControl.GuiFramework.Label"
            props = _code_label(b, "MiddleRight" if b.kind == "value" else "MiddleLeft")
        elif b.kind == "exec" and b.var in bridges:
            br = bridges[b.var]
            typ = br.net_class
            begin = True
            zero = DOTNET_T[_base(types[b.var])][1]
            props = [("DesignMatrix", ds.matrix(0, 0)), ("Name", f'"{n}"'),
                     ("SecurityToken", "((uint)(4294967295u))"), ("TagName", ds.string(br.path)), ("Val", zero)]
            if "ValDecimalPlaces" in br.props:
                props.append(("ValDecimalPlaces", "((byte)(0))"))
            for prop in ("ValMaximum", "ValMinimum"):
                if prop in br.props:
                    props.append((prop, zero))
            if "ValUnits" in br.props:
                props.append(("ValUnits", "null"))
        elif b.kind == "value":
            ct, zero = DOTNET_T[_base(types[b.var])]
            typ = f"System.HMI.Symbols.Base.TextBox<{ct}>"
            begin = True
            props = [("Brush", 'new NxtControl.Drawing.Brush("Transparent")'),
                     ("DesignMatrix", ds.matrix(b.x, b.y, b.w / 250, b.h / 27)), ("IgnoreMouseEvents", "true"),
                     ("IsOnlyInput", "true"), ("IsPrefixSuffixOutside", "false"), ("Name", f'"{n}"'),
                     ("NumberBase", "NxtControl.GuiFramework.NumberBase.Decimal"),
                     ("Pen", 'new NxtControl.Drawing.Pen("Transparent")'), ("TagName", ds.string(b.var)),
                     ("TextAlignment", "NxtControl.Drawing.ContentAlignment.MiddleRight"), ("Value", zero)]
        elif b.kind == "text":
            typ = "System.HMI.Symbols.Base.Label"
            begin = True
            props = [("BorderStyle", "System.Windows.Forms.BorderStyle.None"),
                     ("DesignMatrix", ds.matrix(b.x, b.y)), ("FontScale", "false"), ("IsOnlyInput", "true"),
                     ("Name", f'"{n}"'), ("Pen", 'new NxtControl.Drawing.Pen("LabelPen")'), ("TagName", ds.string(b.var))]
        elif b.kind == "state":
            typ = "NxtControl.GuiFramework.Label"
            props = [("AngleIgnore", "true"), ("BorderStyle", "System.Windows.Forms.BorderStyle.None"),
                     ("Bounds", _rect(b)), ("Brush", 'new NxtControl.Drawing.Brush("Transparent")'),
                     ("Font", _font(st.SIZE_TEXT, True)), ("FontScale", "true"), ("Name", f'"{n}"'),
                     ("Pen", 'new NxtControl.Drawing.Pen("Transparent")'), ("Text", ds.string(b.text)),
                     ("TextAlignment", "NxtControl.Drawing.ContentAlignment.MiddleLeft"),
                     ("TextAutoSizeHorizontalOffset", "10"), ("TextColor", _c(st.TEXT)),
                     ("TextPadding", "new NxtControl.Drawing.Padding(2)")]
        elif b.kind == "entry":
            # Operator entry on an IThis output variable (as SE.Agile sValueInput: TextBox bound to oValue).
            ct, zero = DOTNET_T[_base(types[b.var])]
            typ = f"System.HMI.Symbols.Base.TextBox<{ct}>"
            begin = True
            props = [("Brush", _brush(st.ENTRY)), ("DesignMatrix", ds.matrix(b.x, b.y, b.w / 250, b.h / 27)),
                     ("IsOnlyInput", "false"), ("IsPrefixSuffixOutside", "false"), ("Name", f'"{n}"'),
                     ("NumberBase", "NxtControl.GuiFramework.NumberBase.Decimal"), ("Pen", _pen(st.BORDER)),
                     ("TagName", ds.string(b.var)),
                     ("TextAlignment", "NxtControl.Drawing.ContentAlignment.MiddleRight"), ("Value", zero)]
        elif b.kind in ("button", "stepdn", "stepup"):
            # Theme-styled button as EAE draws it (SE.Agile SmartManager sDefault).
            typ = "NxtControl.GuiFramework.DrawnButton"
            props = [("Bounds", _rect(b)), ("Brush", 'new NxtControl.Drawing.Brush("ButtonBrush")'),
                     ("Click", f"new System.EventHandler(this.{n}Click)"),
                     ("Font", 'new NxtControl.Drawing.Font("ButtonFont")'),
                     ("InnerBorderColor", 'new NxtControl.Drawing.Color("ButtonInnerBorderColor")'),
                     ("Name", f'"{n}"'), ("Pen", 'new NxtControl.Drawing.Pen("ButtonPen")'), ("Radius", "4D"),
                     ("Text", ds.string(b.text)), ("TextColor", 'new NxtControl.Drawing.Color("ButtonTextColor")'),
                     ("TextColorMouseDown", 'new NxtControl.Drawing.Color("ButtonTextColorMouseDown")'),
                     ("Use3DEffect", "false")]
        elif b.kind == "exec":
            ct, zero = DOTNET_T[_base(types[b.var])]
            typ = f"System.HMI.Symbols.Base.Execute<{ct}>"
            begin = True
            props = [("DesignMatrix", "new NxtControl.Drawing.Matrix2D(1D, 0D, 0D, 1D, double.NaN, double.NaN)"),
                     ("IsOnlyInput", "true"), ("Location", "new NxtControl.Drawing.PointF(double.NaN, double.NaN)"),
                     ("Name", f'"{n}"'), ("Size", "new NxtControl.Drawing.SizeF(double.NegativeInfinity, "
                                                  "double.NegativeInfinity)"),
                     ("TagName", ds.string(b.var)), ("Value", zero),
                     ("ValueChanged", f"new System.EventHandler<NxtControl.GuiFramework.ValueChangedEventArgs>"
                                      f"(this.{n}ValueChanged)")]
        else:
            raise DesignError(b.kind)
        prelude.append(f"{T3}this.{n} = new {typ}();")
        lines = [f"{T3}this.{n}.BeginInit();"] if begin else []
        entries = [(p, [f"{T3}this.{n}.{p} {'+=' if p in ('ValueChanged', 'Click') else '='} {v};"]) for p, v in props]
        if extra:
            entries.append(("Points", extra))  # multi-line statement, kept at its alphabetical place
        for _, ls in sorted(entries, key=lambda e: e[0].lower()):
            lines += ls
        if begin:
            lines.append(f"{T3}this.{n}.EndInit();")
        sections.append((n, lines))
        fields.append(f"\t\tprivate {typ} {n};")
    return prelude, sections, fields


def dotnet_designer(header: str, ns: str, sym: str, boxes: list[Box], types: dict[str, str], W: int, H: int,
                    bridges: dict | None = None, faceplate: bool = False, open_faceplate: str | None = None) -> str:
    prelude, sections, fields = _dotnet_sections(boxes, types, bridges)
    if open_faceplate:
        # A click on the symbol card opens the faceplate (OpenFaceplates, as EAE symbols do).
        for name, lines in sections:
            if name == "card":
                at = next(i for i, ln in enumerate(lines) if ".Pen = " in ln)
                lines.insert(at, f'{T3}this.card.OpenFaceplates.Add(new NxtControl.GuiFramework.OpenFaceplate('
                                 f'"{open_faceplate}", NxtControl.GuiFramework.MouseButtonType.Click));')
    shapes = [n for n, _ in sections]
    body = "".join(p + NL for p in prelude)
    parts = [f"{T3}// {NL}{T3}// {n}{NL}{T3}// {NL}" + NL.join(lines) for n, lines in sections]
    own = []
    if faceplate:  # as SE.Agile faceplates (bcFlowTransmitter fData)
        own += [f"{T3}this.Bounds = new NxtControl.Drawing.RectF(((float)(0D)), ((float)(0D)), ((float)({W}D)), "
                f"((float)({H}D)));", f'{T3}this.Brush = new NxtControl.Drawing.Brush("FaceplateBrush");',
                f"{T3}this.FaceplateClose = NxtControl.GuiFramework.CloseFaceplateBehavior.Automatic;"]
    own += [f"{T3}this.Shapes.AddRange(new System.ComponentModel.IComponent[] {{"]
    own += [f"{T3}this.{n}," for n in shapes]
    own[-1] = own[-1][:-1] + "});"
    own.append(f"{T3}this.Size = new System.Drawing.Size({W}, {H});" if faceplate
               else f"{T3}this.SymbolSize = new System.Drawing.Size({W}, {H});")
    parts.append(f"{T3}// {NL}{T3}// {sym}{NL}{T3}// {NL}" + NL.join(own) + NL)
    text = (header + NL.join(["using System;", "using System.ComponentModel;", "using System.Collections;",
                              "using NxtControl.GuiFramework;", "", f"namespace {ns}", "{", "\t/// <summary>",
                              f"\t/// Summary description for {sym}.", "\t/// </summary>", f"\tpartial class {sym}", "\t{",
                              "", "\t\t#region Component Designer generated code", "\t\t/// <summary>",
                              "\t\t/// Required method for Designer support - do not modify",
                              "\t\t/// the contents of this method with the code editor.", "\t\t/// </summary>",
                              "\t\tprivate void InitializeComponent()", "\t\t{", ""])
            + body + NL.join(parts) + f"{NL}\t\t}}{NL}" + "".join(f + NL for f in fields)
            + NL.join(["\t\t#endregion", "\t}", "}", ""]))
    ds.parse(text)  # must fit the constrained model so later edits work
    return text


def _cs_color(rgb: st.RGB) -> str:
    return f"new NxtControl.Drawing.Color((byte){rgb[0]}, (byte){rgb[1]}, (byte){rgb[2]})"


def _limit_cond(e: ElementSpec) -> str:
    low, high = (e.limits or (None, None))
    return " || ".join(c for c in ((f"v < {low!r}" if low is not None else ""),
                                   (f"v > {high!r}" if high is not None else "")) if c) or "false"


def _cs_element(e: ElementSpec, boxes: list[Box], types: dict[str, str], br, acc: str) -> list[str]:
    """C# statements updating one element from `object raw` (accessor name `acc` for Agile bridges)."""
    n = _id(e.var)
    p = st.priority(e.priority)
    is_bool = _base(types[e.var]) == "BOOL"
    m: list[str] = []
    if e.kind in ("value", "setpoint"):
        m += ["\t\t\t{", "\t\t\t\tdouble v;",
              "\t\t\t\ttry { v = Convert.ToDouble(raw); } catch (Exception) { return; }"]
        if br is not None:
            dec = f'"F" + {acc}.ValDecimalPlaces' if "ValDecimalPlaces" in br.props else '"0.##"'
            unit = (f' + (string.IsNullOrEmpty({acc}.ValUnits) ? "" : " " + {acc}.ValUnits)'
                    if "ValUnits" in br.props and not e.unit else "")
            m += [f"\t\t\t\tval{n}.Text = v.ToString({dec}){unit};"]
        ptr = next((x for x in boxes if x.name == f"ptr{n}"), None)
        trk = next((x for x in boxes if x.name == f"trk{n}"), None)
        if ptr is not None:
            if ptr.shape == "dynamic":
                m += [f"\t\t\t\tdouble lo = {acc}.ValMinimum, hi = {acc}.ValMaximum;",
                      "\t\t\t\tif (hi <= lo) return;", "\t\t\t\tbool abnormal = false;"]
            else:
                lo, hi = e.range
                m += [f"\t\t\t\tdouble lo = {lo!r}, hi = {hi!r};", f"\t\t\t\tbool abnormal = {_limit_cond(e)};"]
            m += ["\t\t\t\tdouble k = (v - lo) / (hi - lo);", "\t\t\t\tif (k < 0) k = 0; else if (k > 1) k = 1;",
                  f"\t\t\t\tfloat x = (float)({trk.x!r} + k * {trk.w!r}) - 2F;",
                  f"\t\t\t\tptr{n}.Bounds = new NxtControl.Drawing.RectF(x, {ptr.y!r}F, abnormal ? 6F : 4F, {ptr.h!r}F);",
                  f"\t\t\t\tptr{n}.Brush = new NxtControl.Drawing.Brush(abnormal ? {_cs_color(p.color)} : "
                  f"{_cs_color(st.POINTER)});"]
        m += ["\t\t\t}"]
    elif e.kind == "state":
        m += ["\t\t\t{", "\t\t\t\tstring key = raw == null ? \"\" : " +
              ("(raw is bool ? ((bool)raw ? \"true\" : \"false\") : raw.ToString());" if is_bool else "raw.ToString();"),
              "\t\t\t\tstring text = key;", "\t\t\t\tbool abnormal = false;", "\t\t\t\tswitch (key)", "\t\t\t\t{"]
        for k, v in e.states.items():
            m += [f"\t\t\t\t\tcase \"{k}\": text = \"{v}\"; abnormal = {'true' if k in e.abnormal else 'false'}; break;"]
        m += ["\t\t\t\t}", f"\t\t\t\tsta{n}.Text = text;",
              f"\t\t\t\tsta{n}.Brush = abnormal ? new NxtControl.Drawing.Brush({_cs_color(p.color)}) : "
              "new NxtControl.Drawing.Brush(\"Transparent\");",
              f"\t\t\t\tsta{n}.TextColor = abnormal ? {_cs_color(p.text_color)} : {_cs_color(st.TEXT)};", "\t\t\t}"]
    elif e.kind == "alarm":
        m += ["\t\t\t{"]
        if is_bool:
            m += [f"\t\t\t\tint level = (raw is bool && (bool)raw) ? {e.priority} : 0;"]
        else:
            m += ["\t\t\t\tint level = 0;", "\t\t\t\ttry { level = Convert.ToInt32(raw); } catch (Exception) { }"]
        m += ["\t\t\t\tNxtControl.Drawing.Color c;", "\t\t\t\tswitch (level)", "\t\t\t\t{"]
        for lvl, pr in st.PRIORITIES.items():
            m += [f"\t\t\t\t\tcase {lvl}: c = {_cs_color(pr.color)}; break;"]
        m += ["\t\t\t\t\tdefault: c = " + _cs_color(st.INDICATOR_IDLE) + "; break;", "\t\t\t\t}",
              f"\t\t\t\talm{n}.Brush = new NxtControl.Drawing.Brush(c);", f"\t\t\t\talm{n}.Visible = level > 0;",
              f"\t\t\t\talmTxt{n}.Text = level > 0 ? level.ToString() : \"\";",
              f"\t\t\t\talmTxt{n}.Visible = level > 0;", "\t\t\t}"]
    elif e.kind == "text" and br is not None:
        m += [f"\t\t\ttxt{n}.Text = raw == null ? \"\" : raw.ToString();"]
    return m


def _elements_by_var(boxes: list[Box]) -> dict[str, list[ElementSpec]]:
    out: dict[str, list[ElementSpec]] = {}
    for b in boxes:
        if b.element is not None and b.kind in ("value", "state", "alarm", "text") and b.var is not None:
            lst = out.setdefault(b.var, [])
            if b.element not in lst:
                lst.append(b.element)
    return out


def _confirm(e: ElementSpec, what: str) -> list[str]:
    if not e.confirm:
        return []
    text = f"{what}?".replace('"', "")
    return [f'\t\t\tif (System.Windows.Forms.MessageBox.Show("{text}", "Confirm", '
            "System.Windows.Forms.MessageBoxButtons.YesNo, System.Windows.Forms.MessageBoxIcon.Question) != "
            "System.Windows.Forms.DialogResult.Yes)", "\t\t\t\treturn;"]


def _cs_literal(value: str, net: str) -> str:
    if net == "bool":
        return value.lower()
    if net == "float":
        return f"{float(value)!r}F"
    if net == "double":
        return f"{float(value)!r}"
    return f"(({net})({int(float(value))}))"


def _cs_commands(boxes: list[Box], types: dict[str, str], bridges: dict, outputs: dict[str, list]) -> list[str]:
    """Click handlers: basic → FireEvent_<EVENT>(value) of the symbol; Agile → bridge.FireEvent_CNF(value)."""
    m: list[str] = []
    for b in boxes:
        if b.kind not in ("button", "stepdn", "stepup"):
            continue
        e, br, n = b.element, bridges.get(b.var), _id(b.var)
        label = e.label or e.var
        m += ["", f"\t\tvoid {b.name}Click(object sender, EventArgs e)", "\t\t{"]
        if b.kind in ("stepdn", "stepup"):
            sign = "-" if b.kind == "stepdn" else "+"
            m += _confirm(e, f"Change {label}")
            m += [f"\t\t\tdouble v = Convert.ToDouble(x{n}.Val) {sign} {e.step!r};",
                  f"\t\t\tdouble lo = x{n}.ValMinimum, hi = x{n}.ValMaximum;",
                  "\t\t\tif (hi > lo) { if (v < lo) v = lo; if (v > hi) v = hi; }",
                  f"\t\t\tx{n}.FireEvent_CNF({'(float)v' if br.val_type == 'float' else '(short)Math.Round(v)'});"]
        elif br is not None:
            m += _confirm(e, label)
            if br.val_type == "bool" and e.value.lower() == "toggle":
                m += [f"\t\t\tx{n}.FireEvent_CNF(!x{n}.Val);"]
            else:
                m += [f"\t\t\tx{n}.FireEvent_CNF({_cs_literal(e.value, br.val_type)});"]
        else:
            m += _confirm(e, label)
            args = outputs.get(e.var, [])
            if args:
                net = DOTNET_T[_base(args[0][1])][0]
                m += [f"\t\t\tFireEvent_{e.var}({_cs_literal(e.value, net)});"]
            else:
                m += [f"\t\t\tFireEvent_{e.var}();"]
        m.append("\t\t}")
    return m


def dotnet_code_behind(header: str, ns: str, sym: str, boxes: list[Box], types: dict[str, str],
                       bridges: dict | None = None, hmi_outputs: dict[str, list] | None = None,
                       faceplate: bool = False) -> str:
    """C# handlers: values/pointers, abnormal states and alarm indicators; Agile bridges via OnValChanged."""
    bridges = bridges or {}
    by_var = _elements_by_var(boxes)
    ctor_extra, m = [], []
    for b in boxes:
        if b.kind != "exec":
            continue
        br = bridges.get(b.var)
        if br is not None:
            ctor_extra.append(f"\t\t\t{b.name}.OnValChanged += {b.name}ValChanged;")
            m += ["", f"\t\tvoid {b.name}ValChanged(object sender, EventArgs e)", "\t\t{",
                  f"\t\t\tobject raw = {b.name}.Val;"]
        else:
            m += ["", f"\t\tvoid {b.name}ValueChanged(object sender, ValueChangedEventArgs e)", "\t\t{",
                  "\t\t\tobject raw = e.Value;"]
        for e in by_var.get(b.var, []):
            m += _cs_element(e, boxes, types, br, b.name)
        if faceplate and b.var == "AssetName":
            m.append("\t\t\tTitle = raw == null ? \"\" : raw.ToString();  // faceplate window title, as SE.Agile fData")
        m.append("\t\t}")
    m += _cs_commands(boxes, types, bridges, hmi_outputs or {})
    base = "HMIFaceplate" if faceplate else "HMISymbol"
    return (header + NL.join(["", "using System;", "using NxtControl.GuiFramework;", "", f"namespace {ns}", "{",
                              "\t/// <summary>", f"\t/// {sym}: situation-awareness symbol generated by eae-mcp.",
                              "\t/// </summary>", f"\tpublic partial class {sym} : NxtControl.GuiFramework.{base}", "\t{",
                              f"\t\tpublic {sym}()", "\t\t{", "\t\t\t//",
                              "\t\t\t// The InitializeComponent() call is required for Windows Forms designer support.",
                              "\t\t\t//", "\t\t\tInitializeComponent();"] + ctor_extra + ["\t\t}"] + m
                             + ["\t}", "}", ""]))


def dotnet_resx(template: bytes, boxes: list[Box], W: int, H: int) -> bytes:
    text = template.decode("utf-8-sig")
    meta = "".join(f'  <metadata name="{b.name}.Name" xml:space="preserve">{NL}    <value>{b.name}</value>{NL}'
                   f"  </metadata>{NL}" for b in boxes)
    text = text.replace('  <metadata name="$this.Size"', meta + '  <metadata name="$this.Size"', 1)
    text = text.replace("<value>600, 400</value>", f"<value>{W}, {H}</value>", 1)
    return text.encode("utf-8")


# -- eHMI --------------------------------------------------------------------------------------------


def _jc(rgb: st.RGB) -> list:
    return [rgb[0], rgb[1], rgb[2], 1]


def ehmi_objects(boxes: list[Box], types: dict[str, str], bridges: dict | None = None) -> list[dict]:
    bridges = bridges or {}
    out = []
    for b in boxes:
        n = b.name
        if b.shape == "dynamic":
            continue  # the eHMI bridges carry no Minimum/Maximum: no dynamic indicator on the web
        if b.kind in ("entry", "button", "stepdn", "stepup") or (b.element is not None and b.element.kind in COMMAND_KINDS
                                                                and b.var not in bridges):
            continue  # commands: .NET only
        if b.kind in ("card", "track", "band", "pointer", "alarm"):
            color = {"card": st.PANEL, "track": st.TRACK, "band": st.NORMAL_BAND, "pointer": st.POINTER,
                     "alarm": st.INDICATOR_IDLE}[b.kind]
            shape = st.priority(b.element.priority).shape if b.kind == "alarm" else "square"
            o = {"type": "NxtControl.GuiFramework.Ellipse" if shape == "circle" else "NxtControl.GuiFramework.Rectangle",
                 "name": n, "left": b.x, "top": b.y, "width": b.w, "height": b.h, "brush": {"color": _jc(color)},
                 "pen": {"color": _jc(st.BORDER if b.kind in ("card", "track", "alarm") else color)}}
            if b.kind == "alarm":
                o["visible"] = False
                if shape == "diamond":
                    o["radius"] = 6
            out.append(o)
        elif b.kind in ("title", "label", "alarmtext"):
            o = {"type": "NxtControl.GuiFramework.FreeText", "name": n, "left": b.x, "top": b.y, "width": b.w,
                 "height": b.h, "text": b.text,
                 "fontSize": st.SIZE_TITLE if b.kind == "title" else st.SIZE_TEXT, "fontFamily": st.FONT,
                 "fontStyle": "normal", "textColor": _jc(st.TEXT_2 if b.kind == "label" else st.TEXT)}
            if b.kind != "label":
                o["fontWeight"] = "bold"
            if b.kind == "alarmtext":
                o["visible"] = False
            out.append(o)
        elif b.kind == "tick":
            out.append({"type": "NxtControl.GuiFramework.Rectangle", "name": n, "left": b.x - 1, "top": b.y,
                        "width": 2, "height": b.h, "brush": {"color": _jc(st.LIMIT)}, "pen": {"color": _jc(st.LIMIT)}})
        elif b.kind in ("value", "text") and b.var in bridges:
            out.append({"type": "NxtControl.GuiFramework.FreeText", "name": n, "left": b.x, "top": b.y + 3,
                        "width": b.w, "height": 18, "text": "—", "fontSize": st.SIZE_TEXT, "fontFamily": st.FONT,
                        "fontStyle": "normal", "textColor": _jc(st.TEXT),
                        **({"textAlign": "right"} if b.kind == "value" else {})})
        elif b.kind in ("value", "text"):
            out.append({"type": "System.WEB.Symbols.Base.Label", "name": n, "left": b.x, "top": b.y, "width": b.w,
                        "height": b.h, "brush": {"color": "Transparent"}, "pen": {"color": "Transparent"},
                        "text": "${Value}", "backColor": "Transparent", "textColor": _jc(st.TEXT),
                        "prefixColor": _jc(st.TEXT_2), "suffixColor": _jc(st.TEXT_2), "tagName": b.var,
                        "valueType": EHMI_T[_base(types[b.var])], "numberBase": 0,
                        "ranges": {"defaultProps": {"text": "${Value}", "pen": {"color": "Transparent"},
                                                    "backColor": "Transparent", "textColor": _jc(st.TEXT)},
                                   "range": []}})
        elif b.kind == "state":
            out.append({"type": "NxtControl.GuiFramework.FreeText", "name": n, "left": b.x, "top": b.y + 3,
                        "width": b.w, "height": 18, "text": b.text, "fontSize": st.SIZE_TEXT, "fontFamily": st.FONT,
                        "fontStyle": "normal", "fontWeight": "bold", "textColor": _jc(st.TEXT)})
        elif b.kind == "exec" and b.var in bridges:
            # Agile bridge: invisible library symbol bound to the sub-CAT (SE.Agile pattern).
            out.append({"type": bridges[b.var].web_class, "name": n, "left": 0, "top": 0, "width": 1, "height": 1,
                        "tagName": bridges[b.var].path})
        elif b.kind == "exec":
            out.append({"type": "System.WEB.Symbols.Base.Execute", "name": n, "left": None, "top": None,
                        "_events_": [{"name": "valueChanged", "eventName": f"{n}_valueChanged"}], "tagName": b.var,
                        "valueType": EHMI_T[_base(types[b.var])], "isOnlyInput": True})
    return out


def _ts_color(rgb: st.RGB) -> str:
    return f"[{rgb[0]}, {rgb[1]}, {rgb[2]}, 1]"


def _ts_element(e: ElementSpec, boxes: list[Box], types: dict[str, str], bridged: bool) -> list[str]:
    n = _id(e.var)
    p = st.priority(e.priority)
    m: list[str] = []
    if e.kind in ("value", "setpoint"):
        m += ["      {", "        const v = Number(raw);", "        if (!isNaN(v)) {"]
        if bridged:
            m += [f"          this.put(this.find('val{n}'), 'text', String(Math.round(v * 100) / 100));"]
        ptr = next((x for x in boxes if x.name == f"ptr{n}" and x.shape != "dynamic"), None)
        trk = next((x for x in boxes if x.name == f"trk{n}" and x.shape != "dynamic"), None)
        if ptr is not None:
            lo, hi = e.range
            m += [f"          const k = Math.min(1, Math.max(0, (v - {lo!r}) / ({hi!r} - {lo!r})));",
                  f"          const abnormal = {_limit_cond(e)};", f"          const ptr = this.find('ptr{n}');",
                  f"          this.put(ptr, 'left', {trk.x!r} + k * {trk.w!r} - 2);",
                  f"          this.put(ptr, 'width', abnormal ? 6 : {ptr.w!r});",
                  f"          this.put(ptr, 'brush', {{ color: abnormal ? {_ts_color(p.color)} : {_ts_color(st.POINTER)} }});"]
        m += ["        }", "      }"]
    elif e.kind == "state":
        m += ["      {", "        const key = raw === true ? 'true' : raw === false ? 'false' : String(raw);",
              f"        const texts: any = {json.dumps(e.states)};",
              f"        const abnormalKeys: string[] = {json.dumps(e.abnormal)};",
              "        const abnormal = abnormalKeys.indexOf(key) >= 0;", f"        const lbl = this.find('sta{n}');",
              "        this.put(lbl, 'text', texts[key] !== undefined ? texts[key] : key);",
              f"        this.put(lbl, 'textColor', abnormal ? {_ts_color(p.color)} : {_ts_color(st.TEXT)});", "      }"]
    elif e.kind == "alarm":
        colors = json.dumps({str(k): list(v.color) + [1] for k, v in st.PRIORITIES.items()})
        m += ["      {"]
        if _base(types[e.var]) == "BOOL":
            m += [f"        const level = raw === true || raw === 'true' ? {e.priority} : 0;"]
        else:
            m += ["        const level = Math.round(Number(raw)) || 0;"]
        m += [f"        const colors: any = {colors};", f"        const shape = this.find('alm{n}');",
              f"        const text = this.find('almTxt{n}');",
              "        this.put(shape, 'visible', level > 0);", "        this.put(text, 'visible', level > 0);",
              "        if (level > 0) {", "          this.put(shape, 'brush', { color: colors[String(level)] || colors['1'] });",
              "          this.put(text, 'text', String(level));", "        }", "      }"]
    elif e.kind == "text" and bridged:
        m += [f"      this.put(this.find('txt{n}'), 'text', raw === null || raw === undefined ? '' : String(raw));"]
    return m


def ehmi_ts(header: str, ns: str, sym: str, boxes: list[Box], types: dict[str, str],
            bridges: dict | None = None) -> str:
    bridges = bridges or {}
    by_var = _elements_by_var(boxes)
    m: list[str] = []
    bind: list[str] = []
    for b in boxes:
        if b.kind != "exec":
            continue
        if b.var in bridges:
            bind.append(f"      this.bridge('{b.name}', (raw: any) => this.{b.name}_update(raw));")
        else:
            m += ["", f"    protected {b.name}_valueChanged(sender: any, ea: any) {{",
                  f"      this.{b.name}_update(this.ev(sender, ea));", "    }"]
        m += ["", f"    private {b.name}_update(raw: any): void {{"]
        for e in by_var.get(b.var, []):
            m += _ts_element(e, boxes, types, b.var in bridges)
        m.append("    }")
    load = []
    if bind:
        load = ["", "    load(options: any): this {", "      // do not delete next 2 lines", "      options = options || {};",
                "      super.load(options);"] + bind + ["      return this;", "    }", "",
                "    private bridge(name: string, update: any): void {", "      const b: any = this.find(name);",
                "      if (!b) return;",
                "      const read = (snap: any) => update(snap && snap.value !== undefined ? snap.value : "
                "(b.getValue ? b.getValue() : null));",
                "      if (b.setValueChangedHandler) b.setValueChangedHandler((sender: any, snap: any) => read(snap));",
                "      else if (b.valueChanged && b.valueChanged.add) b.valueChanged.add((sender: any, snap: any) => read(snap));",
                "    }"]
    helpers = ["", "    private ev(sender: any, ea: any): any {",
               "      if (ea) { if (ea.value !== undefined) return ea.value; if (ea.Value !== undefined) return ea.Value; }",
               "      if (sender) { if (sender.value !== undefined) return sender.value; "
               "if (sender.Value !== undefined) return sender.Value; }",
               "      return null;", "    }", "",
               "    private put(obj: any, key: string, value: any): void {",
               "      if (!obj) return;", "      if (obj._set) obj._set(key, value, true); else obj[key] = value;", "    }"]
    return (header + NL.join([f"namespace {ns} {{", "", f"  export class {sym} extends NxtControl.GuiFramework.RuntimeSymbol {{",
                              "", "    /**", "     * Type of an object (never change this)", "     * @type String",
                              "     * @default", "     */", f"    @System.DefaultValue('{ns}.{sym}')",
                              "    protected type: string;", "", "    /**** DO NOT DELETE CONSTRUCTOR *****/",
                              "    constructor() {", "      // do not delete next line", "      super();", "    }"]
                             + load + m + helpers + ["  } ", "}", ""]))


# -- design review gate -----------------------------------------------------------------------------


def gate(technology: str, name: str, designer_text: str | None, json_data: dict | None) -> list[dict]:
    """Run eae_hmi_review rules on the generated content; generated symbols must have no warnings."""
    from . import review as rv
    found = []
    if designer_text is not None:
        found += rv.review_display(rv.extract_dotnet(name, "", designer_text), {})
    if json_data is not None:
        found += rv.review_display(rv.extract_ehmi(name, "", json_data), {})
    return [vars(f) for f in found if f.severity == "warning"]


def stamp(now: _dt.datetime | None = None) -> dict[str, str]:
    now = now or _dt.datetime.now()
    return {"DATE": f"{now.month}/{now.day}/{now.year}", "TIME": now.strftime("%I:%M %p").lstrip("0")}


# -- draft from the HMI interface --------------------------------------------------------------------

_ALARM = re.compile(r"alarm|alm|fault|trip|prio", re.I)
_STATE = re.compile(r"state|status|mode|run|open|close|on$|enable|ready|auto", re.I)


def suggest(hmi: Interface, title: str) -> dict:
    """A draft design from the HMI interface plus the engineering data still missing.

    Never invents ranges, limits or state texts: those come from the description or the user.
    """
    elements, questions = [], []
    for v in hmi.input_vars:
        if v.name in ("QI",):
            continue
        t = _base(v.type)
        if t == "STRING":
            elements.append({"kind": "text", "var": v.name})
        elif _ALARM.search(v.name) and t in ("BOOL", "INT", "SINT", "DINT", "USINT", "BYTE", "UINT"):
            elements.append({"kind": "alarm", "var": v.name, "priority": 2})
            questions.append(f"{v.name}: alarm priority (1 critical … 4 low)" +
                             ("" if t == "BOOL" else ", or confirm it carries the priority code 0..4"))
        elif t == "BOOL" or (_STATE.search(v.name) and t in DOTNET_T and t not in ("REAL", "LREAL")):
            states = {"false": "Off", "true": "On"} if t == "BOOL" else {}
            elements.append({"kind": "state", "var": v.name, "states": states, "abnormal": []})
            questions.append(f"{v.name}: state texts" + (" (default Off/On)" if t == "BOOL" else " for each value")
                             + " and which states are abnormal")
        elif t in DOTNET_T:
            elements.append({"kind": "value", "var": v.name, "unit": None, "range": None, "normal": None,
                             "limits": None})
            questions.append(f"{v.name}: unit, span [low, high], normal range, low/high alarm limits")
    outs = {v.name: v.type for v in hmi.output_vars if v.name not in ("QO", "STATUS")}
    for ev in hmi.event_outputs:
        if ev.name == "INITO":
            continue
        numeric = [w for w in ev.with_vars if _base(outs.get(w, "")) in DOTNET_T
                   and _base(outs[w]) not in ("BOOL", "STRING")]
        if len(ev.with_vars) == 1 and numeric:
            elements.append({"kind": "setpoint", "var": numeric[0], "unit": None})
            questions.append(f"{numeric[0]}: unit of the setpoint")
        elif len(ev.with_vars) <= 1:
            elements.append({"kind": "command", "var": ev.name, "label": ev.name,
                             **({"value": "true"} if ev.with_vars else {}), "confirm": False})
            questions.append(f"{ev.name}: button text" + (f", value sent with {ev.with_vars[0]}" if ev.with_vars else "")
                             + ", and whether the operator must confirm it (critical actions)")
    order = {"alarm": 0, "value": 1, "state": 2, "text": 3, "setpoint": 4, "command": 5}
    elements.sort(key=lambda e: order[e["kind"]])
    return {"title": title, "elements": elements, "missing": questions,
            "note": "Fill units/ranges/limits/state texts from the description or ask the engineer; "
                    "do not guess limits. Setpoints and commands are drawn on the .NET symbol only. "
                    "Then call eae_hmi_symbol_build."}
