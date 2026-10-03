"""Read-only operations behind the MCP tools. Every function returns JSON-ready data."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import safety
from .config import Config
from .hmi.reader import HmiIndex, load_hmi
from .model import Network, TypeDef, to_dict
from .project.catalog import Catalog
from .project.folders import KIND_TO_CATEGORY
from .project.solution import Solution, load_solution, resolve_parameters, resolve_reference

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"
CONCEPT_FOR_KIND = {
    "basic": "basic-fb", "composite": "composite-fb", "cat": "cat", "cat_hmi": "cat", "adapter": "adapter",
    "datatype": "datatype", "function": "function", "subapp": "subapp", "sifb": "composite-fb",
}
TYPE_KINDS = ("adapter", "datatype", "basic", "composite", "subapp", "function", "cat", "cat_hmi", "sifb")
COMPANIONS = (".doc.xml", ".meta.xml", ".composite.offline.xml", ".subapp.offline.xml", ".subapp.opcua.xml")


class NotFound(LookupError):
    pass


@dataclass
class Workspace:
    """Server state: configuration, loaded solutions and the current one."""

    config: Config
    solutions: dict[str, Solution] = field(default_factory=dict)
    hmi: dict[str, HmiIndex] = field(default_factory=dict)
    current: str | None = None
    _catalog: Catalog | None = None

    # -- loading -----------------------------------------------------------------

    def catalog(self) -> Catalog | None:
        if self._catalog is None and self.config.catalog_file and self.config.catalog_file.exists():
            self._catalog = Catalog.load(self.config.catalog_file)
        return self._catalog

    def open(self, path: str) -> Solution:
        p = safety.check_inside(Path(path), self.config.roots)
        sol = load_solution(p, catalog=self.catalog(), library_store=self.config.library_store)
        key = str(sol.root)
        self.solutions[key] = sol
        self.hmi[key] = load_hmi(sol)
        self.current = key
        return sol

    def get(self, solution: str | None = None) -> Solution:
        """Current solution, or the one named by an open key, .sln/folder name, or path."""
        if solution:
            for key, sol in self.solutions.items():
                if solution in (key, sol.sln.name, sol.sln.stem, sol.root.name):
                    return sol
            if not Path(solution).expanduser().exists() or not Path(solution).is_absolute():
                # A bare name such as 'MyPlant': look it up under the configured roots.
                matches = [s for s in list_solutions(self) if s["name"] == solution
                           or Path(s["path"]).name == solution]
                if len(matches) == 1:
                    return self.open(matches[0]["path"])
                if len(matches) > 1:
                    raise NotFound(f"Several solutions are named '{solution}': "
                                   + ", ".join(m["path"] for m in matches) + ". Pass the full path.")
                if not Path(solution).expanduser().exists():
                    raise NotFound(f"No solution '{solution}' under the configured roots "
                                   f"({', '.join(str(r) for r in self.config.roots) or 'none'}). "
                                   "Use eae_list_solutions.")
            return self.open(solution)
        if self.current:
            return self.solutions[self.current]
        raise NotFound("No solution is open. Call eae_open_solution first (see eae_list_solutions).")

    def hmi_of(self, sol: Solution) -> HmiIndex:
        return self.hmi[str(sol.root)]


# -- solutions ---------------------------------------------------------------------


def list_solutions(ws: Workspace, max_depth: int = 3) -> list[dict]:
    out = []
    for root in ws.config.roots:
        root = root.expanduser()
        if not root.is_dir():
            continue
        for sln in sorted(root.rglob("*.sln")):
            depth = len(sln.relative_to(root).parts)
            if depth <= max_depth and not safety.is_ignored(sln):
                out.append({"name": sln.stem, "path": str(sln.parent)})
    return out


def solution_summary(sol: Solution) -> dict:
    counts: dict[str, int] = {}
    for t in sol.types.values():
        counts[t.kind] = counts.get(t.kind, 0) + 1
    iec = next((p for p in sol.projects_of("iec61499") if p.name == "IEC61499"), None)
    return {
        "name": sol.sln.stem,
        "root": str(sol.root),
        "eae_version": iec.msbuild.properties.get("NxtVersion") if iec and iec.msbuild else None,
        "projects": [{"name": p.name, "kind": p.kind, "path": p.path} for p in sol.projects],
        "libraries": sorted({t.project for t in sol.types.values() if t.project}),
        "system_library_references": sol.references,
        "catalog": {"types": len(sol.catalog.types), "packages": sol.catalog.packages} if sol.catalog else None,
        "type_counts": counts,
        "systems": [
            {"name": s.name,
             "applications": [a.name for a in s.applications],
             "devices": [f"{d.name} ({d.type})" for d in s.devices]}
            for s in sol.systems
        ],
        "warnings": sol.warnings[:20],
    }


# -- types -------------------------------------------------------------------------


KIND_WORDS = {"adapter", "adapters", "datatype", "datatypes", "basic", "composite", "cat", "cats", "subapp",
              "function", "functions", "sifb", "fb", "fbs", "type", "types"}


def find_type(sol: Solution, name: str) -> TypeDef:
    """Find a type by name or qualified name; an application instance name resolves to its type."""
    name = name.strip()
    ns, _, short = name.rpartition(".") if "." in name and name not in sol.types else ("", "", name)
    td = sol.types.get(name) or sol.find_type(short or name, ns or None)
    if td is not None:
        return td
    hit = find_instance(sol, name)
    if hit is not None:
        inst = hit[3]
        td = sol.find_type(inst.type, inst.namespace)
        if td is not None:
            return td
    if name.lower() in KIND_WORDS:
        raise NotFound(f"'{name}' is a kind of component, not a name. Use eae_list with kind='{name.rstrip('s').lower()}' "
                       "to list them, then pass one of the names.")
    import difflib

    names = {t.name: t.qualified_name for t in sol.types.values()}
    close = difflib.get_close_matches(name, list(names), n=6, cutoff=0.6)
    close += [n for n in names if name.lower() in n.lower() and n not in close][: 6 - len(close)]
    raise NotFound(f"Type '{name}' not found." + (f" Did you mean: {', '.join(names[n] for n in close)}?" if close else
                                                   " Use eae_list or eae_search to find names."))


def network_view(net: Network | None, sol: Solution, owner: TypeDef | None = None) -> dict | None:
    """Human-readable network: instances with resolved parameters, connections by name."""
    if net is None:
        return None
    instances = []
    for inst in net.instances:
        td = sol.find_type(inst.type, inst.namespace)
        instances.append({
            "name": inst.name,
            "type": inst.type,
            "namespace": inst.namespace,
            "kind": inst.kind if inst.kind != "FB" else (td.kind if td else "unknown"),
            "type_found": td is not None,
            "parameters": resolve_parameters(inst, sol),
            "mapped_from": inst.mapping,
        })
    connections = []
    for c in net.connections:
        src = resolve_reference(c.source, net, sol, owner)
        dst = resolve_reference(c.destination, net, sol, owner)
        connections.append({
            "kind": c.kind, "from": str(src), "to": str(dst),
            **({"unresolved": True} if not (src.resolved and dst.resolved) else {}),
        })
    return {
        "instances": instances,
        "boundary_pins": [{"name": p.name, "kind": p.kind, "direction": p.direction} for p in net.pins],
        "connections": connections,
    }


def type_view(sol: Solution, td: TypeDef, include_xml: bool = False) -> dict:
    data = to_dict(td)
    data.pop("network", None)
    data["qualified_name"] = td.qualified_name
    if td.network is not None:
        data["network"] = network_view(td.network, sol, td)
    if td.kind == "cat" and td.qualified_name in sol.cats:
        data["cat_manifest"] = to_dict(sol.cats[td.qualified_name])
    data["concept"] = f"eae://concepts/{CONCEPT_FOR_KIND.get(td.kind, 'overview')}"
    if include_xml and td.path and td.source == "solution":
        data["xml"] = sol.abs(td.path).read_text(encoding="utf-8-sig", errors="replace")
    return data


def list_items(sol: Solution, ws: Workspace, kind: str | None = None, library: str | None = None,
               folder: str | None = None, query: str = "") -> list[dict]:
    if kind in (None, "") or kind in TYPE_KINDS:
        return [
            {"name": t.qualified_name, "kind": t.kind, "folder": t.folder, "library": t.project or "(main)",
             "comment": t.comment, "path": t.path}
            for t in sol.search_types(query, kind or None, library, folder)
        ]
    if kind in ("system", "application", "device"):
        return to_dict(system_view(sol))
    if kind.startswith(("hmi", "ehmi")):
        tech, _, sub = kind.partition("_")
        return hmi_list(sol, ws, tech, sub or None, query)
    raise NotFound(f"Unknown kind '{kind}'.")


def find_usages(sol: Solution, ws: Workspace, name: str) -> list[dict]:
    td = find_type(sol, name)
    out = []
    for t in sol.types.values():
        if t.network:
            for inst in t.network.instances:
                if inst.type == td.name:
                    out.append({"where": "type network", "in": t.qualified_name, "instance": inst.name})
        if any(a.type == td.name for a in t.interface.adapter_inputs + t.interface.adapter_outputs):
            out.append({"where": "adapter pin", "in": t.qualified_name})
        for v in t.interface.input_vars + t.interface.output_vars + t.internal_vars:
            if v.type == td.name:
                out.append({"where": "variable type", "in": t.qualified_name, "variable": v.name})
    for qn, cfg in sol.cats.items():
        for sc in cfg.sub_cats:
            if sc.type == td.name:
                out.append({"where": "CAT sub-CAT", "in": qn, "instance": sc.name})
    for s in sol.systems:
        for app in s.applications:
            for layer in app.layers:
                for inst in (layer.network.instances if layer.network else []):
                    if inst.type == td.name:
                        out.append({"where": "application", "in": f"{app.name}/{layer.name}", "instance": inst.name})
        for dev in s.devices:
            for res in dev.resources:
                for inst in (res.network.instances if res.network else []):
                    if inst.type == td.name:
                        out.append({"where": "resource", "in": f"{dev.name}/{res.name}", "instance": inst.name})
    for doc in ws.hmi_of(sol).documents:
        if any(f"Symbols.{td.name}." in o.type for o in doc.objects):
            out.append({"where": f"{doc.technology} {doc.kind}", "in": doc.path})
    return out


def search(sol: Solution, text: str, limit: int = 50) -> list[dict]:
    q = text.lower()
    out = []
    for t in sol.types.values():
        if q in t.name.lower() or q in (t.comment or "").lower():
            out.append({"type": t.qualified_name, "match": "name/comment"})
        for a in t.algorithms:
            for i, line in enumerate(a.text.splitlines(), 1):
                if q in line.lower():
                    out.append({"type": t.qualified_name, "algorithm": a.name, "line": i, "text": line.strip()})
        for v in (t.interface.input_vars + t.interface.output_vars + t.internal_vars):
            if q in v.name.lower() or q in (v.comment or "").lower():
                out.append({"type": t.qualified_name, "variable": v.name, "var_type": v.type})
        if len(out) >= limit:
            break
    return out[:limit]


def folders_view(sol: Solution) -> dict:
    device_names = {d.id: d.name for s in sol.systems for d in s.devices}
    out = {}
    for project, tree in sol.folders.items():
        t = tree.as_tree()
        if "SystemDevice" in t:
            t["SystemDevice"] = {k: [device_names.get(m, m) for m in v] for k, v in t["SystemDevice"].items()}
        out[project or "IEC61499"] = t
    return out


# -- CAT, system ---------------------------------------------------------------------


def cat_describe(sol: Solution, ws: Workspace, name: str) -> dict:
    td = find_type(sol, name)
    if td.kind != "cat":
        raise NotFound(f"{td.qualified_name} is a {td.kind}, not a CAT.")
    cfg = sol.cats.get(td.qualified_name)
    hmi_itf = sol.find_type(f"{td.name}_HMI", td.namespace)
    docs = [d for d in ws.hmi_of(sol).documents if d.cat == td.name]
    return {
        "name": td.qualified_name,
        "comment": td.comment,
        "folder": td.folder,
        "interface": to_dict(td.interface),
        "network": network_view(td.network, sol, td),
        "hmi_interface": {
            "instance": cfg.hmi_interface if cfg else None,
            "type": hmi_itf.qualified_name if hmi_itf else None,
            "values_to_hmi": [to_dict(v) for v in hmi_itf.interface.input_vars] if hmi_itf else [],
            "values_from_hmi": [to_dict(v) for v in hmi_itf.interface.output_vars] if hmi_itf else [],
            "events": [to_dict(e) for e in hmi_itf.interface.event_inputs] if hmi_itf else [],
        },
        "sub_cats": to_dict(cfg.sub_cats) if cfg else [],
        "symbols": [
            {"name": d.name, "technology": d.technology, "kind": d.kind, "path": d.path,
             "bindings": sorted({o.tag_name for o in d.objects if o.tag_name})}
            for d in docs
        ],
        "files": component_files(sol, td.qualified_name)["files"],
        "concept": "eae://concepts/cat",
    }


def system_view(sol: Solution) -> list[dict]:
    out = []
    for s in sol.systems:
        out.append({
            "name": s.name, "id": s.id,
            "applications": [
                {"name": a.name, "id": a.id,
                 "layers": [{"name": l.name, "default": l.is_default, "network": network_view(l.network, sol)}
                            for l in a.layers]}
                for a in s.applications
            ],
            "devices": [
                {"name": d.name, "type": d.type, "namespace": d.namespace, "folder": d.folder,
                 "properties": d.properties,
                 "resources": [{"name": r.name, "type": r.type, "network": network_view(r.network, sol)}
                               for r in d.resources]}
                for d in s.devices
            ],
            "mapping": mapping_view(sol, s),
            "opcua_exposed": [to_dict(o) for o in s.opcua],
            "concept": "eae://concepts/system",
        })
    return out


def mapping_view(sol: Solution, system) -> list[dict]:
    app_fbs = {}
    for app in system.applications:
        for layer in app.layers:
            for inst in (layer.network.instances if layer.network else []):
                app_fbs[inst.id] = (app.name, inst.name)
    out = []
    for dev in system.devices:
        for res in dev.resources:
            for inst in (res.network.instances if res.network else []):
                if inst.mapping and inst.mapping in app_fbs:
                    app, fb = app_fbs[inst.mapping]
                    out.append({"application": app, "instance": fb, "device": dev.name, "resource": res.name})
    return out


def find_instance(sol: Solution, name_or_id: str):
    """Locate an application instance by name or ID → (system, app, layer, instance)."""
    for s in sol.systems:
        for app in s.applications:
            for layer in app.layers:
                for inst in (layer.network.instances if layer.network else []):
                    if name_or_id in (inst.name, inst.id):
                        return s, app, layer, inst
    return None


# -- HMI -------------------------------------------------------------------------------


def hmi_list(sol: Solution, ws: Workspace, technology: str | None = None, kind: str | None = None,
             query: str = "") -> list[dict]:
    idx = ws.hmi_of(sol)
    out = []
    for d in idx.documents:
        if technology and d.technology != technology:
            continue
        if kind and d.kind != kind.rstrip("s"):
            continue
        if query and query.lower() not in d.path.lower():
            continue
        out.append({"name": d.name, "technology": d.technology, "kind": d.kind, "cat": d.cat,
                    "device": d.device, "path": d.path, "objects": len(d.objects)})
    return out


def hmi_describe(sol: Solution, ws: Workspace, name: str, technology: str | None = None) -> dict:
    docs = ws.hmi_of(sol).find(name, technology)
    if not docs:
        raise NotFound(f"No HMI/eHMI document named '{name}'. Use eae_hmi_list.")
    out = []
    for d in docs:
        data = to_dict(d)
        bindings = []
        for o in d.objects:
            if not o.tag_name:
                continue
            hit = find_instance(sol, o.tag_name) if d.kind == "canvas" else None
            bindings.append({
                "object": o.name, "type": o.type, "tag_name": o.tag_name,
                "instance": f"{hit[1].name}/{hit[3].name} ({hit[3].type})" if hit else None,
            })
        data["bindings"] = bindings
        data["resolutions"] = [to_dict(r) for r in ws.hmi_of(sol).resolutions
                               if d.kind == "canvas" and d.name in r.canvases and r.technology == d.technology]
        data["concept"] = "eae://concepts/" + ("ehmi" if d.technology == "ehmi" else "hmi-dotnet")
        out.append(data)
    return out[0] if len(out) == 1 else {"matches": out}


# -- understanding -------------------------------------------------------------------


def component_files(sol: Solution, name: str) -> dict:
    td = find_type(sol, name)
    if td.source != "solution":
        return {"name": td.qualified_name, "files": [{"path": td.path, "role": "library type (read-only)"}]}
    files = [{"path": td.path, "role": f"{td.kind} definition"}]
    base = td.path.rsplit(".", 1)[0] if td.path else ""
    for suffix in COMPANIONS:
        p = base + suffix
        if (sol.root / p).exists():
            files.append({"path": p, "role": {
                ".doc.xml": "documentation (DocBook)", ".meta.xml": "metadata",
            }.get(suffix, "offline/OPC UA configuration")})
    if td.kind == "cat":
        cfg = sol.cats.get(td.qualified_name)
        proj = td.path.split("/")[0]
        if cfg:
            files.append({"path": cfg.cfg_file, "role": "CAT manifest"})
            for p in cfg.plugin_files:
                files.append({"path": f"{proj}/{p}", "role": "CAT plugin file (offline/OPC UA configuration)"})
            hmi = sol.find_type(f"{td.name}_HMI", td.namespace)
            if hmi:
                files.append({"path": hmi.path, "role": "HMI interface SIFB (IThis)"})
            for s in cfg.symbols:
                role = f"{'eHMI' if s.technology == 'ehmi' else '.NET HMI'} {'faceplate' if s.is_faceplate else 'symbol'} {s.name}"
                files.append({"path": _norm(proj, s.file), "role": role})
                for dep in s.dependent_files:
                    files.append({"path": _norm(proj, dep), "role": f"{role} (dependent)"})
            for g in cfg.generated_files:
                files.append({"path": _norm(proj, g), "role": "generated by EAE (do not edit)"})
    seen, unique = set(), []
    for f in files:
        if f["path"] and f["path"] not in seen:
            seen.add(f["path"])
            f["exists"] = (sol.root / f["path"]).exists()
            unique.append(f)
    return {"name": td.qualified_name, "kind": td.kind, "files": unique}


def _norm(project_dir: str, rel: str) -> str:
    parts = []
    for part in f"{project_dir}/{rel}".split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part and part != ".":
            parts.append(part)
    return "/".join(parts)


def explain(sol: Solution, ws: Workspace, target: str) -> dict:
    """Structured explanation of a type, an application instance, an HMI document or a device."""
    hit = find_instance(sol, target)
    if hit:
        system, app, layer, inst = hit
        td = sol.find_type(inst.type, inst.namespace)
        mapped = [m for m in mapping_view(sol, system) if m["instance"] == inst.name]
        shown_on = [f"{d.technology} {d.kind} {d.name}" for d in ws.hmi_of(sol).documents
                    if any(o.tag_name == inst.id for o in d.objects)]
        return {
            "what": "application instance",
            "name": inst.name,
            "type": inst.type,
            "type_kind": td.kind if td else "unknown (type not found)",
            "location": f"system {system.name} › application {app.name} › layer {layer.name}",
            "parameters": resolve_parameters(inst, sol),
            "runs_on": mapped,
            "shown_on": shown_on,
            "type_summary": _type_summary(td) if td else None,
            "concepts": ["eae://concepts/system", f"eae://concepts/{CONCEPT_FOR_KIND.get(td.kind, 'overview') if td else 'overview'}"],
        }
    try:
        td = find_type(sol, target)
    except NotFound:
        docs = ws.hmi_of(sol).find(target)
        if docs:
            return {"what": "HMI document", **hmi_describe(sol, ws, target)}
        for s in sol.systems:
            for d in s.devices:
                if target in (d.name, d.id):
                    return {"what": "device", "name": d.name, "type": d.type, "namespace": d.namespace,
                            "resources": [r.name for r in d.resources], "properties": d.properties,
                            "concepts": ["eae://concepts/system"]}
        raise
    return {"what": f"{td.kind} type", **_type_summary(td),
            "used_in": find_usages(sol, ws, td.qualified_name)[:20],
            "concepts": [f"eae://concepts/{CONCEPT_FOR_KIND.get(td.kind, 'overview')}"]}


def _vt(v) -> str:
    text = f"{v.name}: {v.type}" + (f"[{v.array_size}]" if v.array_size else "")
    return text + (f" := {v.initial_value}" if v.initial_value else "")


def _type_summary(td: TypeDef) -> dict:
    itf = td.interface
    s = {
        "name": td.qualified_name, "kind": td.kind, "comment": td.comment, "folder": td.folder,
        "source": td.source if td.source == "library" else (td.project or "main project"),
        "inputs": {"events": [f"{e.name}" + (f" (with {', '.join(e.with_vars)})" if e.with_vars else "") for e in itf.event_inputs],
                   "data": [_vt(v) for v in itf.input_vars],
                   "adapters": [f"{a.name}: {a.type}" for a in itf.adapter_inputs]},
        "outputs": {"events": [f"{e.name}" + (f" (with {', '.join(e.with_vars)})" if e.with_vars else "") for e in itf.event_outputs],
                    "data": [_vt(v) for v in itf.output_vars],
                    "adapters": [f"{a.name}: {a.type}" for a in itf.adapter_outputs]},
    }
    if td.kind == "basic":
        s["ecc"] = {
            "states": [st.name for st in td.states],
            "transitions": [f"{t.source} → {t.destination} when {t.condition}" for t in td.transitions],
            "actions": {st.name: [f"{a.algorithm or '-'} → {a.output or '-'}" for a in st.actions] for st in td.states if st.actions},
        }
        s["algorithms"] = [a.name for a in td.algorithms]
        s["internal_vars"] = [_vt(v) for v in td.internal_vars]
    if td.network:
        s["contains"] = [f"{i.name}: {i.type}" for i in td.network.instances]
    if td.datatype:
        s["datatype"] = to_dict(td.datatype)
    return s


def trace(sol: Solution, ws: Workspace, target: str) -> dict:
    """Follow a chain: HMI canvas → instances → CAT → sub-CATs/inner FBs → algorithms."""
    steps = []
    instances = []
    docs = [d for d in ws.hmi_of(sol).find(target) if d.kind == "canvas"]
    if docs:
        for d in docs:
            for o in d.objects:
                hit = find_instance(sol, o.tag_name) if o.tag_name else None
                if hit:
                    steps.append({"step": f"{d.technology} canvas {d.name}", "object": o.name,
                                  "binds": f"tagName {o.tag_name} → instance {hit[3].name}"})
                    instances.append(hit)
    else:
        hit = find_instance(sol, target)
        if hit:
            instances.append(hit)
    if not instances:
        td = find_type(sol, target)
        return {"target": target, "type_tree": _type_tree(sol, td, depth=0)}
    for system, app, layer, inst in instances:
        td = sol.find_type(inst.type, inst.namespace)
        steps.append({
            "step": f"instance {inst.name} in {app.name}/{layer.name}",
            "type": inst.type,
            "runs_on": [f"{m['device']}/{m['resource']}" for m in mapping_view(sol, system) if m["instance"] == inst.name],
            "type_tree": _type_tree(sol, td, depth=0) if td else "type not found",
        })
    return {"target": target, "chain": steps}


def _type_tree(sol: Solution, td: TypeDef, depth: int, max_depth: int = 3) -> dict:
    node: dict = {"type": td.qualified_name, "kind": td.kind}
    if td.kind == "basic":
        node["algorithms"] = [a.name for a in td.algorithms]
    if td.network and depth < max_depth:
        node["contains"] = []
        for inst in td.network.instances:
            child = sol.find_type(inst.type, inst.namespace)
            if child and child.source == "solution":
                node["contains"].append({"instance": inst.name, **_type_tree(sol, child, depth + 1, max_depth)})
            else:
                node["contains"].append({"instance": inst.name, "type": inst.type,
                                         "kind": child.kind if child else "library/unknown"})
    return node


def doc_scaffold(sol: Solution, ws: Workspace, name: str) -> str:
    """Markdown skeleton documenting a component, with screenshot placeholders."""
    td = find_type(sol, name)
    s = _type_summary(td)
    lines = [f"# {td.name}", "", f"*Kind:* {td.kind} · *Namespace:* {td.namespace} · *Folder:* {td.folder or '-'}", "",
             f"> {td.comment or 'TODO: one-sentence purpose.'}", "", "## Purpose", "", "TODO", "",
             f"[SCREENSHOT: {td.name} interface in the EAE editor]", "", "## Interface", ""]
    for direction in ("inputs", "outputs"):
        lines.append(f"### {direction.title()}")
        for group, items in s[direction].items():
            for it in items:
                lines.append(f"- **{group[:-1] if group.endswith('s') else group}** `{it}` — TODO")
        lines.append("")
    if td.kind == "basic":
        lines += ["## Behaviour (ECC)", "", "[SCREENSHOT: ECC]", ""]
        lines += [f"- {t}" for t in s["ecc"]["transitions"]] + [""]
        lines += ["## Algorithms", ""] + [f"### {a.name}\n\n```st\n{a.text.strip()}\n```\n" for a in td.algorithms]
    if td.network:
        lines += ["## Internal network", "", f"[SCREENSHOT: {td.name} FB network]", ""]
        lines += [f"- `{c}` — TODO role" for c in s.get("contains", [])] + [""]
    if td.kind == "cat":
        lines += ["## Operator view", "", "[SCREENSHOT: default symbol (.NET HMI)]", "",
                  "[SCREENSHOT: eHMI symbol]", "", "[SCREENSHOT: faceplate]", ""]
        lines += ["## Files", ""] + [f"- `{f['path']}` — {f['role']}" for f in component_files(sol, name)["files"]] + [""]
    used = find_usages(sol, ws, td.qualified_name)
    if used:
        lines += ["## Where it is used", ""] + [f"- {u['where']}: {u['in']}" + (f" ({u['instance']})" if u.get("instance") else "") for u in used[:20]] + [""]
    return "\n".join(lines)


def concept(name: str) -> str:
    path = KNOWLEDGE_DIR / f"{name}.md"
    if not path.exists():
        available = ", ".join(p.stem for p in sorted(KNOWLEDGE_DIR.glob("*.md")))
        raise NotFound(f"No concept '{name}'. Available: {available}")
    return path.read_text(encoding="utf-8")


def concept_names() -> list[str]:
    return [p.stem for p in sorted(KNOWLEDGE_DIR.glob("*.md"))]


def catalog_build(ws: Workspace, sol: Solution, store: str | None = None, output: str | None = None) -> dict:
    store_path = Path(store) if store else ws.config.library_store
    if not store_path or not store_path.is_dir():
        raise NotFound("Library store not found. Pass `store` or set [eae] library_store "
                       "(default C:/ProgramData/Schneider Electric/Libraries).")
    cat = Catalog.from_store(store_path, sol.references or {})
    sol.catalog = cat
    ws._catalog = cat
    out = Path(output) if output else (ws.config.catalog_file or sol.root / ".eae-mcp" / "catalog.json")
    cat.save(out)
    return {"packages": cat.packages, "types": len(cat.types), "saved_to": str(out)}


_ = KIND_TO_CATEGORY  # re-exported for callers that map kinds to folder categories


# -- writing (M2) ------------------------------------------------------------------------


def eae_running() -> bool:
    """Best effort: is EAE Buildtime running on this (Windows) machine?"""
    import subprocess
    import sys

    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq EcoStruxureAutomationExpert.exe"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "EcoStruxureAutomationExpert.exe" in out


def run_change(ws: Workspace, sol: Solution, build, dry_run: bool) -> dict:
    """Build a ChangeSet; return its diff (dry run) or apply it and reload the solution."""
    from .project.edit import EditError
    from .project.writer import SpecError


    try:
        cs = build()
    except (EditError, SpecError, FileExistsError) as e:
        raise NotFound(str(e)) from e
    result = {"action": cs.description, "files": cs.summary(), "warnings": cs.warnings}
    if dry_run:
        result["dry_run"] = True
        result["diff"] = cs.diff()
        result["next_step"] = "Review the diff, then call again with dry_run=false to write."
        return result
    if not ws.config.allow_write:
        raise NotFound("Writing is disabled. Set allow_write = true under [project] in eae-mcp.toml "
                       "(or EAE_MCP_ALLOW_WRITE=1) and restart the server.")
    result.update(cs.apply())
    if eae_running():
        result["note"] = ("EAE is running: it reloads changed files automatically, but unsaved edits to the "
                          "same files inside EAE would overwrite these changes when saved there.")
    ws.open(str(sol.root))  # re-index
    result["next_step"] = "Open the type in EAE (or Tools › Check Changes) to verify it compiles."
    return result


def validate(sol: Solution, name: str | None = None) -> dict:
    from .project.validate import validate_solution

    try:
        return validate_solution(sol, name)
    except LookupError as e:
        raise NotFound(str(e)) from e


def doc_scaffold_saved(sol: Solution, ws: Workspace, name: str, save: bool = True) -> dict:
    """Scaffold plus (optionally) a copy under <solution>/.eae-mcp/docs/<Type>.md (not a project file)."""
    md = doc_scaffold(sol, ws, name)
    out = {"markdown": md}
    if save:
        td = find_type(sol, name)
        path = sol.root / ".eae-mcp" / "docs" / f"{td.name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(md, encoding="utf-8")
        out["saved_to"] = str(path)
    return out


def knowledge_search(query: str, limit: int = 4) -> list[dict]:
    """Best-matching sections (by '## ' heading) of the concept docs: small answers instead of whole files."""
    terms = [t for t in re.findall(r"[\w$.-]+", query.lower()) if len(t) > 1]
    if not terms:
        raise NotFound("Give a few words to search for, e.g. 'E_PERMIT interlock' or 'alarm colors'.")
    scored = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = text.splitlines()[0].lstrip("# ").strip() if text else path.stem
        for section in re.split(r"\n(?=## )", text):
            low = section.lower()
            score = sum(low.count(t) for t in terms) + 3 * sum(t in low.split("\n", 1)[0] for t in terms)
            if score:
                heading = section.split("\n", 1)[0].lstrip("# ").strip()
                scored.append((score, path.stem, title, heading, section.strip()))
    scored.sort(key=lambda s: -s[0])
    return [{"concept": c, "doc": t, "section": h, "text": body[:4000]} for _, c, t, h, body in scored[:limit]]


def hmi_review(sol: Solution, ws: Workspace, name: str | None = None, technology: str | None = None,
               level: int | None = None) -> dict:
    """Situation-awareness / high-performance HMI review of canvases (or one canvas/symbol)."""
    from .hmi import review as rv

    idx = ws.hmi_of(sol)
    docs = idx.find(name, technology) if name else [d for d in idx.documents if d.kind == "canvas"
                                                    and (technology is None or d.technology == technology)]
    if name and not docs:
        raise NotFound(f"No HMI document '{name}'. eae_hmi_list shows canvases and symbols.")
    theme = rv.load_theme(sol)
    displays = []
    totals: dict[str, int] = {}
    for doc in docs:
        disp = rv.load_display(sol, doc)
        if disp is None:
            continue
        found = rv.review_display(disp, theme, level)
        for x in found:
            totals[x.rule] = totals.get(x.rule, 0) + 1
        displays.append({"display": f"{doc.technology} {doc.kind} {doc.name}", "path": doc.path,
                         "objects": len(disp.objects), "bound_values": len(disp.bound),
                         "findings": rv.to_dict(found)})
    from .hmi import style as hs
    styled = {qn: hs.classify_cat(sol, idx, qn) for qn, c in sol.cats.items() if c.symbols}
    cat_style = {qn: r["style"] for qn, r in styled.items()}
    by_name = {sol.types[qn].name: qn for qn in styled}
    ids = hs.instance_types(sol)
    for entry, doc in zip(displays, [d for d in docs if rv.load_display(sol, d) is not None]):
        if doc.kind in ("symbol", "faceplate") and doc.cat in by_name:
            entry["style"] = cat_style[by_name[doc.cat]]
        elif doc.kind == "canvas":
            entry["style"] = hs.classify_display(doc, cat_style, ids)
    nav = [] if name else rv.review_navigation(sol, technology)
    classes, alarm_findings = ([], []) if name else rv.review_alarm_classes(sol, theme)
    for x in nav + alarm_findings:
        totals[x.rule] = totals.get(x.rule, 0) + 1
    profile_findings = []
    if not name:
        from .hmi import scripts as sc
        profile_findings = sc.alarm_profiles(sol)["findings"]
        for f in profile_findings:
            totals[f["rule"]] = totals.get(f["rule"], 0) + 1
    if name:
        cats_out = [styled[by_name[d.cat]] for d in docs if d.cat in by_name][:1]
    else:
        cats_out = [r for r in styled.values() if r["findings"]]
    for r in cats_out:
        for f in r["findings"]:
            totals[f["rule"]] = totals.get(f["rule"], 0) + 1
    style_counts = dict(Counter(cat_style.values()))
    return {"styles": {"cats": style_counts,
                       "legend": "basic = widgets bind IThis variables; agile = symbols embed SE.Agile HMI blocks "
                                 "(HMI_Indication_*/HMI_Control_*) bound by sub-CAT path; agile-block = such a block; "
                                 "mixed = both in one CAT"},
            "cat_reviews": cats_out,
            "displays": displays, "navigation": rv.to_dict(nav),
            "alarm_classes": classes, "alarm_findings": rv.to_dict(alarm_findings) + profile_findings,
            "rule_counts": totals,
            "manual_checks": rv.MANUAL_CHECKS,
            "note": "Static review of display files; colors set in code-behind at runtime are not seen. "
                    "Rules are heuristics from ISA-101, ASM, High Performance HMI and ISA-18.2; a site style "
                    "guide may override them (eae_knowledge 'hmi design')."}
