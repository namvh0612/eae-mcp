"""Compact, ranked knowledge about the libraries a solution uses.

EAE libraries carry almost no prose documentation (doc.xml files are empty templates and comments are
EAE defaults), so the guide is derived from structure: logical folders, type families (name without
the `_vX_Y` version suffix), interfaces, and how often each type is used in this solution. It is built
for drill-down so an assistant reads a few hundred tokens instead of a whole library:

    library_guide(sol)                          → libraries with folder/kind counts
    library_guide(sol, "SE.Agile")              → folders with their families
    library_guide(sol, "SE.Agile", ".Standard.HMI") → one line per type with its signature

Generic FBs (`VALFORMAT_8708B18B173C5ABA`, …) are instances of parameterised library templates; see
`generic_registry`.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from ..model import Interface, TypeDef
from .solution import Solution

VERSION = re.compile(r"_v(\d+)_(\d+)$")
GENERIC = re.compile(r"^(?P<base>[A-Z][A-Z0-9_]*?)_(?P<hash>[0-9A-F]{12,20})$")
GENERIC_PARAMS = "Configuration.GenericFBType.InterfaceParams"
DEFAULT_COMMENTS = {
    "Function", "Basic Function Block Type", "Composite Function Block Type", "CAT Function Block Type",
    "Service Interface Function Block Type", "Adapter Interface", "Subapplication", "Subapplication ",
}


def folder_of(t: TypeDef) -> str:
    """Logical folder, or a per-kind bucket for types EAE keeps outside folders (datatypes, functions)."""
    return t.folder or f"({t.kind})"


def family(name: str) -> str:
    return VERSION.sub("", name)


def _version(name: str) -> tuple[int, int]:
    m = VERSION.search(name)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _networks(sol: Solution):
    for td in sol.types.values():
        if td.network:
            yield td.qualified_name, td.network
    for s in sol.systems:
        for app in s.applications:
            for layer in app.layers:
                if layer.network:
                    yield f"{app.name}/{layer.name}", layer.network
        for dev in s.devices:
            for res in dev.resources:
                if res.network:
                    yield f"{dev.name}/{res.name}", res.network


def usage_counts(sol: Solution) -> Counter:
    """How often each type is used: instances in networks, sub-CATs, variable and adapter types."""
    c: Counter = Counter()
    for _, net in _networks(sol):
        for inst in net.instances:
            if inst.mapping:  # resource copies of application instances are not extra uses
                continue
            c[f"{inst.namespace or 'Main'}.{inst.type}"] += 1
    for td in sol.types.values():
        itf = td.interface
        for v in itf.input_vars + itf.output_vars + itf.inout_vars + td.internal_vars:
            c[f"{v.namespace or td.namespace}.{v.type.split('[')[0]}"] += 1
        for a in itf.adapter_inputs + itf.adapter_outputs:
            c[f"{getattr(a, 'namespace', None) or td.namespace}.{a.type}"] += 1
    return c


def signature(td: TypeDef, max_len: int = 220) -> str:
    """One line: events and data, e.g. `INIT,REQ(SP,PV) -> INITO,CNF(OUT) | SP:REAL PV:REAL -> OUT:REAL`."""
    itf: Interface = td.interface
    if td.kind == "datatype" and td.datatype is not None:
        dt = td.datatype
        if getattr(dt, "members", None):
            text = "STRUCT " + " ".join(f"{m.name}:{m.type}" for m in dt.members)
        elif getattr(dt, "values", None):
            text = "ENUM " + ",".join(v.name for v in dt.values)
        else:
            text = f"{dt.kind} {getattr(dt, 'base_type', '') or ''}".strip()
        return text[:max_len]

    def ev(events):
        return ",".join(e.name + (f"({','.join(e.with_vars)})" if e.with_vars else "") for e in events)

    def vs(vars_):
        return " ".join(f"{v.name}:{v.type}" + (f"[{v.array_size}]" if v.array_size else "") for v in vars_)

    parts = []
    if itf.event_inputs or itf.event_outputs:
        parts.append(f"{ev(itf.event_inputs)} -> {ev(itf.event_outputs)}")
    data = vs(itf.input_vars)
    if itf.inout_vars:
        data += " | INOUT " + vs(itf.inout_vars)
    if itf.output_vars or itf.return_type:
        outs = [vs(itf.output_vars)] + ([f"RETURN:{itf.return_type}"] if itf.return_type else [])
        data += " -> " + " ".join(o for o in outs if o)
    if data.strip():
        parts.append(data.strip())
    adapters = [f"{a.name}:{a.type}" for a in itf.adapter_inputs + itf.adapter_outputs]
    if adapters:
        parts.append("adapters " + " ".join(adapters))
    text = " | ".join(parts)
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def _purpose(td: TypeDef) -> str | None:
    comment = (td.comment or "").strip()
    return comment if comment and comment not in DEFAULT_COMMENTS else None


def library_guide(sol: Solution, library: str | None = None, folder: str | None = None,
                  query: str | None = None, limit: int = 120) -> dict:
    uses = usage_counts(sol)
    types = [t for t in sol.types.values() if t.kind != "cat_hmi"]
    if query:
        q = query.lower()
        hits = [t for t in types if q in t.name.lower() or q in (t.folder or "").lower()
                or q in (t.comment or "").lower()]
        hits.sort(key=lambda t: (-uses[t.qualified_name], t.qualified_name))
        return {"query": query, "results": [_line(t, uses) for t in hits[:limit]],
                "more": max(0, len(hits) - limit)}

    if library is None:
        libs: dict[str, Counter] = defaultdict(Counter)
        used: Counter = Counter()
        for t in types:
            libs[t.namespace or "Main"][t.kind] += 1
            used[t.namespace or "Main"] += uses[t.qualified_name]
        return {
            "libraries": [{"library": n, "kinds": dict(k), "uses_in_solution": used[n]}
                          for n, k in sorted(libs.items())],
            "system_libraries": dict(sol.references),
            "generic_fb_families": sorted({g["base"] for g in generic_registry(sol)}),
            "next": "library_guide(library=…) for its folders; library_guide(query=…) to search.",
        }

    lib_types = [t for t in types if (t.namespace or "Main") == library]
    if not lib_types:
        raise LookupError(f"No types in library '{library}'.")
    if folder is None:
        folders: dict[str, list[TypeDef]] = defaultdict(list)
        for t in lib_types:
            folders[folder_of(t)].append(t)
        out = []
        for name, ts in sorted(folders.items()):
            fams: dict[str, list[TypeDef]] = defaultdict(list)
            for t in ts:
                fams[family(t.name)].append(t)
            ranked = sorted(fams.items(), key=lambda kv: (-sum(uses[t.qualified_name] for t in kv[1]), kv[0]))
            out.append({"folder": name, "types": len(ts),
                        "kinds": dict(Counter(t.kind for t in ts)),
                        "families": [f + (f" (v{'/'.join(_vs(v))})" if len(v) > 1 or VERSION.search(v[0].name) else "")
                                     for f, v in ranked]})
        return {"library": library, "folders": out,
                "next": "library_guide(library, folder=…) for signatures."}

    sel = [t for t in lib_types if folder_of(t) == folder or (t.folder or "").startswith(folder + ".")]
    sel.sort(key=lambda t: (t.folder or "", family(t.name), _version(t.name)))
    latest = {}
    for t in sel:
        latest[family(t.name)] = t
    return {"library": library, "folder": folder,
            "types": [_line(t, uses, superseded=latest[family(t.name)] is not t) for t in sel[:limit]],
            "more": max(0, len(sel) - limit)}


def _vs(ts: list[TypeDef]) -> list[str]:
    return [f"{a}.{b}" for a, b in sorted(_version(t.name) for t in ts)]


def _line(t: TypeDef, uses: Counter, superseded: bool = False) -> dict:
    d = {"name": t.qualified_name, "kind": t.kind, "signature": signature(t)}
    if t.folder:
        d["folder"] = t.folder
    p = _purpose(t)
    if p:
        d["purpose"] = p
    if uses[t.qualified_name]:
        d["uses"] = uses[t.qualified_name]
    if superseded:
        d["superseded"] = True  # a newer _vX_Y of the same family exists; prefer it for new work
    return d


# -- generic FBs -----------------------------------------------------------------------------


def parse_generic_params(value: str) -> dict:
    """'Runtime.Standard#I:=2;VALUE${I}:STRING,INT' → {'library': 'Runtime.Standard',
    'counts': {'I': 2}, 'pins': {'VALUE${I}': ['STRING', 'INT']}}"""
    lib, _, spec = value.partition("#")
    counts, pins = {}, {}
    for part in [p for p in spec.split(";") if p]:
        if ":=" in part:
            k, v = part.split(":=", 1)
            counts[k] = int(v) if v.isdigit() else v
        elif ":" in part:
            k, v = part.split(":", 1)
            pins[k] = v.split(",")
    return {"library": lib, "counts": counts, "pins": pins}


def generic_registry(sol: Solution) -> list[dict]:
    """Every generic FB type used in the solution: base template, parameters, concrete type name, uses.

    The hash suffix depends only on the full parameter string, library included (identical strings give
    the same suffix even across templates), so an existing concrete type can be reused for them.
    """
    reg: dict[tuple[str, str], dict] = {}
    for where, net in _networks(sol):
        for inst in net.instances:
            params = inst.attributes.get(GENERIC_PARAMS)
            m = GENERIC.match(inst.type)
            if not params or not m:
                continue
            key = (inst.namespace or "Main", inst.type)
            entry = reg.setdefault(key, {"base": m.group("base"), "type": inst.type,
                                         "namespace": inst.namespace or "Main", "params": params,
                                         **parse_generic_params(params), "uses": 0, "used_in": []})
            if not inst.mapping:
                entry["uses"] += 1
                if len(entry["used_in"]) < 3:
                    entry["used_in"].append(f"{where}:{inst.name}")
    return sorted(reg.values(), key=lambda e: (e["base"], -e["uses"]))


KNOWN_GENERICS = Path(__file__).resolve().parent.parent / "knowledge" / "generic_types.json"


def known_generics() -> dict[str, dict]:
    """Generic types learned from sample solutions; usable in any solution
    because EAE derives the concrete type from the parameter string at build time."""
    try:
        return json.loads(KNOWN_GENERICS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def find_generic(sol: Solution, base: str, params: str | None = None) -> list[dict]:
    """Concrete generic types for a template, optionally with exactly these parameters (solution first,
    then the built-in table)."""
    base = base.upper()
    out = [g for g in generic_registry(sol) if g["base"] == base]
    seen = {g["type"] for g in out}
    for type_name, k in known_generics().items():
        if k["base"] == base and type_name not in seen:
            out.append({"base": base, "type": type_name, "namespace": "Main", "params": k["params"],
                        **parse_generic_params(k["params"]), "uses": 0, "used_in": [], "known": True})
    if params:
        norm = params.replace(" ", "")
        out = [g for g in out if g["params"].replace(" ", "") == norm or
               g["params"].split("#", 1)[-1].replace(" ", "") == norm]
    return out


def learned_pins(sol: Solution) -> dict[str, dict[str, tuple[str, str]]]:
    """Pins of generic FB types learned from existing connections: {type: {pin: (kind, 'in'|'out')}}.

    Library pins are referenced by name (`$<FB id>.<PIN>`), so every connection to a generic instance
    reveals one pin, its kind (event/data/adapter section) and direction (destination = input).
    """
    out: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
    for _, net in _networks(sol):
        by_node = {}
        for inst in net.instances:
            if GENERIC.match(inst.type):
                for key in (inst.id, inst.name):
                    if key:
                        by_node[key] = inst.type
        if not by_node:
            continue
        for conn in net.connections:
            for ref, direction in ((conn.source, "out"), (conn.destination, "in")):
                node, _, pin = ref.lstrip("$").partition(".")
                if pin and node in by_node:
                    out[by_node[node]][pin] = (conn.kind, direction)
    return dict(out)


def _expand(spec: dict) -> dict[str, str]:
    """Pin name → data type from the InterfaceParams pin patterns ('VALUE${I}': ['STRING','INT'] →
    VALUE1:STRING, VALUE2:INT; a single type applies to every index)."""
    out = {}
    counts = spec.get("counts", {})
    for pattern, types in spec.get("pins", {}).items():
        m = re.search(r"\$\{(\w+)\}", pattern)
        if m is None and len(types) == 1:
            out[pattern] = types[0]  # fixed pin such as SD:STRING
            continue
        if not m or "," in m.group(1):
            continue
        n = counts.get(m.group(1), len(types))
        n = n if isinstance(n, int) else len(types)
        for i in range(1, n + 1):
            out[pattern.replace(m.group(0), str(i))] = types[i - 1] if len(types) >= i else types[-1]
    return out


def generic_typedef(sol: Solution, type_name: str, namespace: str | None = None) -> TypeDef | None:
    """A best-effort TypeDef for a generic FB type, from its parameters and the pins used in the solution."""
    if not GENERIC.match(type_name or ""):
        return None
    cache = sol.__dict__.setdefault("_generic_cache", {})
    if not cache:
        cache["registry"] = generic_registry(sol)
        cache["pins"] = learned_pins(sol)
        params: dict[str, set] = defaultdict(set)
        for _, net in _networks(sol):
            for inst in net.instances:
                if GENERIC.match(inst.type):
                    params[inst.type] |= {k.lstrip("$") for k in inst.parameters}
        cache["params"] = params
    entries = [g for g in cache["registry"] if g["type"] == type_name]
    known = known_generics().get(type_name)
    if not entries and known:
        entries = [{"base": known["base"], "type": type_name, "namespace": namespace or "Main",
                    "params": known["params"], **parse_generic_params(known["params"])}]
    if not entries:
        return None
    entry = next((g for g in entries if g["namespace"] == namespace), entries[0])
    types = _expand(entry)
    pins = dict(cache["pins"].get(type_name, {}))
    for pin, spec in (known or {}).get("pins", {}).items():
        pins.setdefault(pin, (spec[0], spec[1]))
        if len(spec) > 2:
            types.setdefault(pin, spec[2])
    for p in cache["params"].get(type_name, set()):
        pins.setdefault(p, ("data", "in"))
    from ..model import Event, Var
    itf = Interface()
    for pin, (kind, direction) in sorted(pins.items()):
        if kind == "event":
            (itf.event_inputs if direction == "in" else itf.event_outputs).append(Event(pin))
        elif kind == "data":
            (itf.input_vars if direction == "in" else itf.output_vars).append(Var(pin, types.get(pin, "")))
    return TypeDef(kind="generic", name=type_name, namespace=entry["namespace"],
                   comment=f"Generic {entry['base']} ({entry['params']}); pins learned from this solution",
                   attributes={GENERIC_PARAMS: entry["params"]}, interface=itf, source="generic")
