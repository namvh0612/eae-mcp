"""Tell the two HMI styles of EAE CATs apart and check their bindings.

**Basic style** — the CAT's HMI interface (IThis) carries the signals; widgets of its symbols bind with
`TagName = <IThis variable>` (simple CATs, the HMI_* blocks themselves).

**Agile style** (SE.Agile library) — IThis carries little (usually only `AssetName`); every
signal is a sub-CAT *HMI block* (`HMI_Indication_Real/Bool/Integer/String`, `HMI_Control_*`, `ModeSelector`, …)
with its own interface (Value, Minimum, Maximum, Units, Category/Prefix/Scope via PLOAD) and its own symbols.
A CAT symbol embeds the blocks' symbols and binds them with `TagName = <sub-CAT path>` (`I`, or through nested
CATs such as the application CAT `acX` → base CAT `Equipment` (`bcX`) → `Equipment.I`).

Classification is evidence-based: every TagName of every symbol and faceplate is resolved either to an IThis
variable or to a sub-CAT path. Unresolved TagNames are broken bindings in either style.
"""

from __future__ import annotations

import os
import re
from collections import Counter

from ..project.solution import Solution

HMI_BLOCK = re.compile(r"^(HMI_(Indication|Control)_\w+|ModeSelector|DA|DIA|FIP|FIP_DIA)(_v\d+_\d+)?$")
NAME_VARS = {"AssetName"}


def _hmi_type(sol: Solution, cat):
    if not cat.hmi_interface_file:
        return None
    rel = os.path.normpath(f"{cat.cfg_file.rsplit('/', 2)[0]}/{cat.hmi_interface_file}").replace("\\", "/")
    return next((t for t in sol.types.values() if t.path == rel), None)


def _cat_by_type(sol: Solution, type_name: str, namespace: str | None):
    td = sol.find_type(type_name, namespace)
    return (td, sol.cats.get(td.qualified_name)) if td else (None, None)


def resolve_tag(sol: Solution, cat, tag: str) -> tuple[str, str] | None:
    """('ithis', var) | ('subcat', 'A.B') | ('subcat-var', 'A.Var') | None when it does not resolve."""
    hmi = _hmi_type(sol, cat)
    ithis = {v.name for v in hmi.interface.input_vars + hmi.interface.output_vars} if hmi else set()
    parts = tag.split(".")
    if len(parts) == 1 and tag in ithis:
        return ("ithis", tag)
    current = cat
    for i, part in enumerate(parts):
        sub = next((s for s in current.sub_cats if s.name == part), None) if current else None
        if sub is None:
            if i == len(parts) - 1 and i > 0 and current is not None:
                h = _hmi_type(sol, current)
                if h and part in {v.name for v in h.interface.input_vars + h.interface.output_vars}:
                    return ("subcat-var", tag)
            return None
        _, current = _cat_by_type(sol, sub.type, sub.namespace)
    return ("subcat", tag)


def _symbol_tags(sol: Solution, idx, cat_name: str) -> list[tuple[str, str, str]]:
    """(document, object, tag) for every binding in the CAT's symbols and faceplates (both technologies)."""
    out = []
    for d in idx.documents:
        if d.cat != cat_name or d.kind not in ("symbol", "faceplate"):
            continue
        for o in d.objects:
            if o.tag_name:
                out.append((f"{d.technology} {d.kind} {d.name}", o.name, o.tag_name))
    return out


def classify_cat(sol: Solution, idx, qualified: str) -> dict:
    cat = sol.cats[qualified]
    td = sol.types[qualified]
    hmi = _hmi_type(sol, cat)
    ithis = [v.name for v in (hmi.interface.input_vars + hmi.interface.output_vars if hmi else [])
             if v.name not in ("QI", "QO", "STATUS")]
    blocks = []
    for s in cat.sub_cats:
        btd, bcat = _cat_by_type(sol, s.type, s.namespace)
        if HMI_BLOCK.match(s.type):
            blocks.append(s.name)
    kinds: Counter = Counter()
    broken, used_subcats, used_vars = [], set(), set()
    for doc, obj, tag in _symbol_tags(sol, idx, td.name):
        r = resolve_tag(sol, cat, tag)
        if r is None:
            broken.append({"in": doc, "object": obj, "tag": tag})
            continue
        kinds[r[0]] += 1
        if r[0] == "ithis":
            used_vars.add(r[1])
        else:
            used_subcats.add(r[1].split(".")[0])
    signal_vars = [v for v in ithis if v not in NAME_VARS]
    direct = sum(1 for v in used_vars if v not in NAME_VARS)
    via_sub = kinds["subcat"] + kinds["subcat-var"]
    symbols = [s.name for s in cat.symbols]
    if not symbols:
        style = "none"
    elif HMI_BLOCK.match(td.name):
        style = "agile-block"
    elif via_sub and direct:
        style = "mixed"
    elif via_sub or (blocks and not signal_vars):
        style = "agile"
    elif direct or signal_vars:
        style = "basic"
    else:
        style = "none"
    result = {"cat": qualified, "style": style, "ithis_vars": ithis, "hmi_blocks": blocks,
              "bindings": {"ithis": kinds["ithis"], "subcat": via_sub}, "symbols": symbols}
    findings = []
    if broken:
        findings.append({"rule": "BIND-01", "severity": "warning",
                         "message": f"{len(broken)} TagName(s) resolve neither to an IThis variable nor to a sub-CAT; "
                                    "the widget stays empty at runtime.", "evidence": broken[:10]})
    if style == "mixed":
        findings.append({"rule": "STY-01", "severity": "info",
                         "message": "Symbols mix both styles (IThis variables and sub-CAT blocks). Pick one per CAT: "
                                    "Agile blocks bring Min/Max/Units/Category per signal, basic IThis is simpler.",
                         "evidence": sorted(v for v in used_vars if v not in NAME_VARS)[:10]})
    if style in ("agile", "mixed"):
        hidden = [b for b in blocks if b not in used_subcats]
        if hidden:
            findings.append({"rule": "AG-01", "severity": "info",
                             "message": f"{len(hidden)} HMI block(s) are not shown on any symbol or faceplate of the CAT.",
                             "evidence": hidden[:15]})
        extra = [v for v in signal_vars]
        if extra and style == "agile":
            findings.append({"rule": "AG-02", "severity": "info",
                             "message": "Agile CATs usually keep IThis to AssetName and put signals in HMI blocks.",
                             "evidence": extra[:10]})
    if style == "basic":
        unshown = [v for v in (hmi.interface.input_vars if hmi else []) if v.name not in used_vars
                   and v.name not in ("QI",) and v.name not in NAME_VARS]
        if unshown:
            findings.append({"rule": "BS-01", "severity": "info",
                             "message": f"{len(unshown)} IThis input(s) are not shown on any symbol.",
                             "evidence": [v.name for v in unshown][:10]})
    result["findings"] = findings
    return result


def instance_types(sol: Solution) -> dict[str, str]:
    """Application instance ID → qualified type (canvas objects bind instances by ID)."""
    out = {}
    for s in sol.systems:
        for app in s.applications:
            for layer in app.layers:
                for inst in (layer.network.instances if layer.network else []):
                    td = sol.find_type(inst.type, inst.namespace)
                    if inst.id and td:
                        out[inst.id] = td.qualified_name
    return out


def classify_display(doc, cat_styles: dict[str, str], ids: dict[str, str]) -> str:
    """Style of a canvas: the style(s) of the CATs placed on it."""
    styles = Counter(cat_styles.get(ids.get(o.tag_name or ""), "") for o in doc.objects if o.tag_name)
    styles.pop("", None)
    styles.pop("none", None)
    if not styles:
        return "none"
    if len(styles) == 1:
        return next(iter(styles))
    return "mixed (" + ", ".join(f"{k}×{v}" for k, v in styles.most_common()) + ")"
