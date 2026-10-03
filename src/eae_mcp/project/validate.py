"""Static checks for types, networks and project registration. Not a replacement for EAE's compiler."""

from __future__ import annotations

import re
from pathlib import Path

from .. import safety
from ..model import Network, TypeDef
from .solution import Solution, resolve_reference
from .writer import RESERVED

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
_ST_WORDS = RESERVED | {"TRUE", "FALSE", "NOT", "AND", "OR", "XOR", "MOD"}


def _issue(severity: str, where: str, message: str) -> dict:
    return {"severity": severity, "where": where, "message": message}


def validate_type(td: TypeDef, sol: Solution) -> list[dict]:
    out: list[dict] = []
    where = td.qualified_name
    itf = td.interface

    names: dict[str, int] = {}
    for _, _, name, _ in itf.pins():
        names[name] = names.get(name, 0) + 1
        if not _IDENT.match(name):
            out.append(_issue("error", where, f"Invalid identifier '{name}'."))
        elif name.upper() in RESERVED:
            out.append(_issue("error", where, f"'{name}' is a reserved word."))
    out += [_issue("error", where, f"Duplicate pin name '{n}'.") for n, c in names.items() if c > 1]

    ids: dict[str, int] = {}
    for _, _, _, pid in itf.pins():
        if pid:
            ids[pid] = ids.get(pid, 0) + 1
    out += [_issue("error", where, f"Duplicate pin ID {i}.") for i, c in ids.items() if c > 1]

    ins, outs = {v.name for v in itf.input_vars}, {v.name for v in itf.output_vars}
    for e in itf.event_inputs:
        out += [_issue("error", where, f"Input event {e.name} WITH {v}: not an input variable.")
                for v in e.with_vars if v not in ins]
    for e in itf.event_outputs:
        out += [_issue("error", where, f"Output event {e.name} WITH {v}: not an output variable.")
                for v in e.with_vars if v not in outs]

    if td.kind == "basic":
        out += _validate_ecc(td, where)
    if td.kind == "datatype" and td.datatype:
        dt = td.datatype
        seen: set[str] = set()
        for n in [m.name for m in dt.members] + [v.name for v in dt.values]:
            if n in seen:
                out.append(_issue("error", where, f"Duplicate member/value '{n}'."))
            seen.add(n)
    if td.network is not None:
        out += validate_network(td.network, sol, where, td)
    return out


def _validate_ecc(td: TypeDef, where: str) -> list[dict]:
    out = []
    states = {s.name for s in td.states}
    algs = {a.name for a in td.algorithms}
    out_events = {e.name for e in td.interface.event_outputs}
    known = ({e.name for e in td.interface.event_inputs} | {v.name for v in td.interface.input_vars}
             | {v.name for v in td.interface.output_vars} | {v.name for v in td.internal_vars})
    # Adapter pins: `Plant.CNF` in actions/conditions refers to an event of adapter `Plant`.
    adapters = {a.name for a in td.interface.adapter_inputs + td.interface.adapter_outputs}
    known |= adapters
    out_events |= {f"{a}." for a in adapters}
    if "START" not in states:
        out.append(_issue("error", where, "ECC has no START state."))
    for s in td.states:
        for a in s.actions:
            if a.algorithm and a.algorithm not in algs:
                out.append(_issue("error", where, f"State {s.name}: algorithm '{a.algorithm}' does not exist."))
            if a.output and a.output not in out_events and a.output.split(".")[0] + "." not in out_events:
                out.append(_issue("error", where, f"State {s.name}: '{a.output}' is not an output event."))
    reachable = {"START"}
    for t in td.transitions:
        for end in (t.source, t.destination):
            if end not in states:
                out.append(_issue("error", where, f"Transition {t.source}→{t.destination}: unknown state '{end}'."))
        reachable.add(t.destination)
        # Ignore string literals ('…') and typed literals (T#1s, SE.Agile.Status#Disabled, 16#FF).
        condition = re.sub(r"'[^']*'|\"[^\"]*\"", " ", t.condition)
        condition = re.sub(r"[A-Za-z_][\w.]*#[\w.]+|\d+#[\w.]+", " ", condition)
        for word in _WORD.findall(condition):
            base = word.split(".")[0]
            if base.upper() not in _ST_WORDS and base not in known and not word.replace(".", "").isdigit():
                out.append(_issue("warning", where, f"Transition {t.source}→{t.destination}: '{base}' in condition "
                                                    f"'{t.condition}' is not an event or variable of {td.name}."))
    for s in states - reachable:
        out.append(_issue("warning", where, f"State {s} is never entered."))
    order = td.attributes.get("FBType.Basic.Algorithm.Order")
    if order:
        for n in order.split(","):
            if n and n not in algs:
                out.append(_issue("warning", where, f"Algorithm order lists '{n}', which does not exist."))
    used = {a.algorithm for s in td.states for a in s.actions if a.algorithm}
    for a in algs - used:
        out.append(_issue("info", where, f"Algorithm {a} is not called from any state."))
    return out


def validate_network(net: Network, sol: Solution, where: str, owner: TypeDef | None = None) -> list[dict]:
    out = []
    generic = {inst.name for inst in net.instances if "Configuration.GenericFBType.InterfaceParams" in inst.attributes}
    for inst in net.instances:
        if inst.name in generic:
            continue  # generic FB (e.g. ADD_<hash>): interface depends on its parameters, not checked
        if sol.find_type(inst.type, inst.namespace) is None:
            sev = "warning" if sol.catalog is None else "error"
            out.append(_issue(sev, where, f"Instance {inst.name}: type {inst.namespace or ''}.{inst.type} not found"
                                          + (" (no library catalog loaded)" if sol.catalog is None else "") + "."))
    sources: dict[str, list[str]] = {}
    for c in net.connections:
        src = resolve_reference(c.source, net, sol, owner)
        dst = resolve_reference(c.destination, net, sol, owner)
        for end, ref in ((src, c.source), (dst, c.destination)):
            if not end.resolved and end.node not in generic:
                out.append(_issue("warning", where, f"{c.kind} connection end '{ref}' does not resolve."))
        if c.kind == "data":
            sources.setdefault(str(dst), []).append(str(src))
    for dst, srcs in sources.items():
        if len(srcs) > 1:
            out.append(_issue("error", where, f"Data input {dst} has {len(srcs)} sources: {', '.join(srcs)}."))
    return out


def validate_registration(sol: Solution) -> list[dict]:
    out = []
    for proj in sol.projects_of("iec61499"):
        if not proj.msbuild:
            continue
        pdir = proj.msbuild.dir
        registered = {i.posix.lower() for i in proj.msbuild.items}
        for i in proj.msbuild.items_of("Compile", "None", "Content"):
            if not (pdir / i.posix).exists() and not safety.is_sensitive(i.posix):
                out.append(_issue("warning", proj.name, f"Registered file is missing: {i.posix}"))
        for f in sorted(pdir.rglob("*")):
            rel = f.relative_to(pdir).as_posix()
            if f.suffix in {".fbt", ".adp", ".dt", ".fct", ".app"} and not safety.is_ignored(rel) \
                    and rel.lower() not in registered:
                out.append(_issue("warning", proj.name, f"File not registered in {Path(proj.path).name}: {rel}"))
    return out


def validate_solution(sol: Solution, name: str | None = None) -> dict:
    issues: list[dict] = []
    if name:
        td = sol.types.get(name) or next((t for t in sol.types.values() if t.name == name), None)
        if td is None:
            raise LookupError(f"Type '{name}' not found.")
        issues = validate_type(td, sol)
    else:
        for td in sol.types.values():
            issues += validate_type(td, sol)
        for s in sol.systems:
            for a in s.applications:
                for layer in a.layers:
                    if layer.network:
                        issues += validate_network(layer.network, sol, f"{a.name}/{layer.name}")
        issues += validate_registration(sol)
    counts = {sev: sum(1 for i in issues if i["severity"] == sev) for sev in ("error", "warning", "info")}
    return {"counts": counts, "issues": issues}
