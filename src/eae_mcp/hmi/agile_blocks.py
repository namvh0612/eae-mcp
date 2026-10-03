"""Agile HMI blocks (SE.Agile HMI_Indication_* / HMI_Control_* …) as data sources for generated symbols.

Learned from the library itself, not hard-coded: a block CAT offers a *bridge* symbol
- .NET: `sValChanged`, an invisible symbol (only Execute accessors) exposing `Val` (+ `ValMinimum`,
  `ValMaximum`, `ValUnits`, `ValDecimalPlaces` when the block has them) and `event EventHandler OnValChanged`;
- eHMI: `seValChanged` / `seValControl`, an invisible runtime symbol with `setValueChangedHandler(handler)`.
A parent symbol embeds the bridge with `TagName = <sub-CAT path>` and draws the value itself (SE.Agile pattern).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from ..project.solution import Solution
from . import codegen as cg

PROP = re.compile(r"public\s+([\w.]+)\s+(Val\w*)\b")


@dataclass
class Bridge:
    block: str  # block type name, e.g. HMI_Indication_Real_v1_0
    namespace: str
    path: str  # sub-CAT path from the CAT being drawn, e.g. "Equipment.I"
    net_class: str | None = None  # SE.Agile.Symbols.HMI_Indication_Real_v1_0.sValChanged
    val_type: str | None = None  # C# type of Val: float, bool, short, string
    props: set[str] = field(default_factory=set)  # Val, ValMinimum, ValMaximum, ValUnits, ValDecimalPlaces
    web_class: str | None = None

    @property
    def numeric(self) -> bool:
        return self.val_type in ("float", "double", "short", "int", "ushort", "byte")

    @property
    def control(self) -> bool:
        """HMI_Control_* blocks: the bridge also writes (FireEvent_CNF(value), as SE.Agile control symbols do)."""
        return self.block.startswith("HMI_Control_")

    @property
    def has_span(self) -> bool:
        return {"ValMinimum", "ValMaximum"} <= self.props


def leaf(sol: Solution, cat, path: str):
    """Type of the sub-CAT at `path` (dotted through nested CATs), or None."""
    current, td = cat, None
    for part in path.split("."):
        sub = next((s for s in current.sub_cats if s.name == part), None) if current else None
        if sub is None:
            return None
        td = sol.find_type(sub.type, sub.namespace)
        current = sol.cats.get(td.qualified_name) if td else None
    return td


def bridge(sol: Solution, cat, path: str) -> Bridge | None:
    td = leaf(sol, cat, path)
    block = sol.cats.get(td.qualified_name) if td else None
    if block is None:
        return None
    b = Bridge(td.name, td.namespace or "Main", path)
    proj_dir = block.cfg_file.rsplit("/", 2)[0]
    for s in block.symbols:
        file = os.path.normpath(f"{proj_dir}/{s.file}").replace("\\", "/")
        if s.technology == "hmi" and s.name == "sValChanged":
            try:
                text = (sol.root / file).read_text(encoding="utf-8-sig")
            except OSError:
                continue
            props = dict((name, typ) for typ, name in PROP.findall(text))
            if "Val" in props and "OnValChanged" in text:
                b.net_class = f"{cg.ns_root(td.namespace)}.Symbols.{td.name}.sValChanged"
                b.val_type = props["Val"].replace("System.Single", "float").replace("System.Boolean", "bool")
                b.props = set(props)
        elif s.technology == "ehmi" and s.name.startswith("seVal"):
            web_root = "WEB.Main" if (td.namespace or "Main") == "Main" else td.namespace
            b.web_class = b.web_class or f"{web_root}.Symbols.{td.name}.{s.name}"
    return b


def blocks(sol: Solution, cat, prefix: str = "", depth: int = 3) -> list[tuple[str, str]]:
    """(path, block type) of every HMI block reachable from `cat` through nested CATs (acX → Equipment → I)."""
    from .style import HMI_BLOCK
    out = []
    for s in cat.sub_cats:
        path = f"{prefix}{s.name}"
        if HMI_BLOCK.match(s.type):
            out.append((path, s.type))
            continue
        td = sol.find_type(s.type, s.namespace)
        sub = sol.cats.get(td.qualified_name) if td else None
        if sub is not None and depth > 1:
            out.extend(blocks(sol, sub, f"{path}.", depth - 1))
    return out


def suggest(sol: Solution, cat, title: str) -> dict:
    """Draft SA design for an Agile CAT: one element per HMI indication block, bound by sub-CAT path.

    Real/Integer → value (units, decimals and — on .NET — the span come from the block at runtime),
    Bool → state, String → text; HMI_Control_Real → setpoint, HMI_Control_Bool → state + toggle command,
    HMI_Control_Integer → command(s). Mode selectors and other blocks are listed under not_drawn.
    """
    elements, questions, skipped = [], [], []
    for path, typ in blocks(sol, cat):
        b = bridge(sol, cat, path)
        if b is None or not typ.startswith(("HMI_Indication_", "HMI_Control_")):
            skipped.append({"path": path, "type": typ})
            continue
        if b.control:
            if b.val_type in ("float", "short") and typ.startswith("HMI_Control_Real"):
                elements.append({"kind": "setpoint", "var": path, "step": None})
                questions.append(f"{path}: -/+ step of the setpoint (engineering units)")
            elif b.val_type == "bool":
                elements.append({"kind": "state", "var": path, "states": {"false": "Off", "true": "On"}, "abnormal": []})
                elements.append({"kind": "command", "var": path, "value": "toggle", "confirm": False})
                questions.append(f"{path}: button text, true/false/toggle, confirmation; state texts")
            elif b.val_type == "short":
                elements.append({"kind": "command", "var": path, "value": None, "confirm": False})
                questions.append(f"{path}: one command per value to send (e.g. modes 0/1/2) with its button text")
            else:
                skipped.append({"path": path, "type": typ})
            continue
        if b.val_type == "bool":
            elements.append({"kind": "state", "var": path, "states": {"false": "Off", "true": "On"}, "abnormal": []})
            questions.append(f"{path}: state texts (default Off/On) and whether true/false is abnormal "
                             "(an abnormal fault flag can instead be an 'alarm' element with a priority)")
        elif b.numeric:
            elements.append({"kind": "value", "var": path, "unit": None, "range": None, "normal": None,
                             "limits": None})
            questions.append(f"{path}: normal range and alarm limits (unit/span come from the block"
                             + (" on .NET; give unit and span for eHMI)" if b.has_span else "; give the span)"))
        elif b.val_type == "string" or typ.startswith("HMI_Indication_String"):
            elements.append({"kind": "text", "var": path})
            if not b.web_class:
                questions.append(f"{path}: HMI_Indication_String has no eHMI bridge; use technology='hmi'")
    order = {"alarm": 0, "value": 1, "state": 2, "text": 3, "setpoint": 4, "command": 5}
    elements.sort(key=lambda e: order[e["kind"]])
    return {"title": title, "elements": elements, "missing": questions, "not_drawn": skipped,
            "note": "Agile style: elements bind to HMI block paths (embedded sValChanged / seValChanged bridges). "
                    "Keep only the signals the operator needs at this level; do not guess limits. "
                    "Then call eae_hmi_symbol_build."}
