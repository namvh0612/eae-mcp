"""Build situation-awareness symbols into a CAT and lay out displays (wraps sa_builder for the tools)."""

from __future__ import annotations

import datetime as _dt
import json
import os

from ..io import jsonrt
from ..project import writer as w
from ..project.cat_edit import _cat_of, add_symbol
from ..project.changes import ChangeSet, FileChange
from ..project.edit import EditError
from ..project.solution import Solution
from . import codegen as cg
from . import sa_builder as sb


def _put(cs: ChangeSet, root, rel: str, data: bytes) -> None:
    if rel in cs.changes:
        cs.changes[rel] = FileChange(rel, cs.changes[rel].old, data)
    else:
        old = (root / rel).read_bytes() if (root / rel).exists() else None
        cs.changes[rel] = FileChange(rel, old, data)


def _prepare(sol: Solution, cat_name: str, design: sb.SymbolDesign, technology: str):
    """Resolve every element (IThis variable/event or Agile block path), validate the design, collect types."""
    td, cat = _cat_of(sol, cat_name)
    hmi_rel = os.path.normpath(f"{cat.cfg_file.rsplit('/', 2)[0]}/{cat.hmi_interface_file}").replace("\\", "/")
    hmi_td = next((t for t in sol.types.values() if t.path == hmi_rel), None)
    if hmi_td is None:
        raise EditError(f"CAT {td.name} has no HMI interface type.")
    from . import agile_blocks as ab
    hitf = hmi_td.interface
    ithis = {v.name for v in hitf.input_vars}
    out_vars = {v.name: v.type for v in hitf.output_vars if v.name not in ("QO", "STATUS")}
    out_events = {ev.name for ev in hitf.event_outputs if ev.name != "INITO"}
    bridges = {}
    for e in design.elements:
        if e.var in ithis or e.var in bridges:
            continue
        if (e.kind == "setpoint" and e.var in out_vars) or (e.kind == "command" and e.var in out_events):
            continue
        br = ab.bridge(sol, cat, e.var)
        if br is None:
            subs = ", ".join(s.name for s in cat.sub_cats) or "none"
            raise EditError(f"'{e.var}' is neither an IThis input of {td.name} nor the path of an Agile HMI block "
                            f"(sub-CATs: {subs}; nested paths like Equipment.I are allowed).")
        if technology in ("hmi", "both") and not br.net_class:
            raise EditError(f"{e.var}: block {br.block} has no .NET bridge symbol sValChanged.")
        bridges[e.var] = br
    try:
        warnings = sb.check_design(design, hmi_td.interface, technology, bridges)
    except (sb.DesignError, ValueError) as e:
        raise EditError(str(e)) from e
    types = {v.name: v.type for v in hmi_td.interface.input_vars}
    types.update(out_vars)
    hmi_outputs = {ev.name: [(w, out_vars.get(w, "")) for w in ev.with_vars] for ev in hitf.event_outputs}
    types.update({var: sb.PSEUDO_IEC[br.val_type] for var, br in bridges.items()})
    return td, cat, hmi_td, bridges, warnings, types, hmi_outputs, ithis


def build_symbol(sol: Solution, cat_name: str, design: sb.SymbolDesign, technology: str = "both",
                 symbol: str = "sSA", web_symbol: str = "seSA", overwrite: bool = False,
                 now: _dt.datetime | None = None, open_faceplate: str | None = None) -> ChangeSet:
    """Create (or with overwrite=True replace) SA symbols of a CAT from a design."""
    if technology not in ("hmi", "ehmi", "both"):
        raise EditError("technology must be hmi, ehmi or both.")
    td, cat, hmi_td, bridges, warnings, types, hmi_outputs, ithis = _prepare(sol, cat_name, design, technology)
    boxes, W, H = sb.layout(design, bridges)
    signal_vars = [e.var for e in design.elements if e.var in ithis and e.var != "AssetName"]
    if bridges and signal_vars:
        warnings.append("info: this symbol mixes IThis variables and Agile HMI blocks (STY-01); prefer one style.")
    now = now or _dt.datetime.now()
    header = cg.header(now)
    proj_dir = cat.cfg_file.rsplit("/", 2)[0]
    parent = proj_dir.rsplit("/", 1)[0] + "/" if "/" in proj_dir else ""
    n = td.name
    ns = td.namespace or "Main"
    cs = ChangeSet(sol.root, f"build SA symbol(s) for CAT {n}")
    existing = {s.name for s in cat.symbols}
    gate_issues: list[dict] = []

    if technology in ("hmi", "both"):
        w.check_identifier(symbol, "symbol name")
        if symbol in existing and not overwrite:
            raise EditError(f"CAT {n} already has a symbol '{symbol}'; pass overwrite=true to regenerate it.")
        if symbol not in existing:
            add_symbol(sol, n, symbol, "hmi", now=now, cs=cs)
        base = f"{parent}HMI/{n}/{n}_{symbol}"
        sym_ns = f"{cg.ns_root(ns)}.Symbols.{n}"
        if open_faceplate is None:
            open_faceplate = next((s.name for s in cat.symbols if s.is_faceplate and s.name == "fSA"), None)
        designer = sb.dotnet_designer(header, sym_ns, symbol, boxes, types, W, H, bridges,
                                      open_faceplate=open_faceplate)
        gate_issues += sb.gate("hmi", symbol, designer, None)
        _put(cs, sol.root, f"{base}.cnv.Designer.cs", designer.encode("utf-8"))
        _put(cs, sol.root, f"{base}.cnv.cs", sb.dotnet_code_behind(header, sym_ns, symbol, boxes, types,
                                                                                 bridges, hmi_outputs).encode("utf-8"))
        _put(cs, sol.root, f"{base}.cnv.resx", sb.dotnet_resx(w.template("cat/cnv.resx"), boxes, W, H))
    if technology in ("ehmi", "both"):
        w.check_identifier(web_symbol, "web symbol name")
        if web_symbol in existing and not overwrite:
            raise EditError(f"CAT {n} already has a symbol '{web_symbol}'; pass overwrite=true to regenerate it.")
        if web_symbol not in existing:
            add_symbol(sol, n, web_symbol, "ehmi", now=now, cs=cs)
        base = f"{parent}WEB/{n}/{n}_{web_symbol}"
        data = {"objects": sb.ehmi_objects(boxes, types, bridges),
                "_design_": {"width": W, "height": H, "background": "CanvasBackColor", "overlay": "Transparent"}}
        gate_issues += sb.gate("ehmi", web_symbol, None, data)
        _put(cs, sol.root, f"{base}.sym.json", jsonrt.new(data))
        ehmi_header = header.replace(" * User:  ", " * User:    ")
        web_ns = f"{'WEB.Main' if ns == 'Main' else ns}.Symbols.{n}"
        _put(cs, sol.root, f"{base}.sym.ts", b"\xef\xbb\xbf" + sb.ehmi_ts(ehmi_header, web_ns, web_symbol, boxes,
                                                                         types, bridges).encode("utf-8"))
    if gate_issues:
        raise EditError("Generated symbol fails the HMI review (please report): "
                        + json.dumps(gate_issues, default=str)[:800])
    cs.commit_docs()
    cs.warnings += warnings + [f"info: symbol size {W}x{H}; colors are gray until a value is abnormal or an alarm "
                               "is active (set at runtime by the generated code)."]
    return cs


def build_faceplate(sol: Solution, cat_name: str, design: sb.SymbolDesign, faceplate: str = "fSA",
                    symbol: str | None = "sSA", overwrite: bool = False,
                    now: _dt.datetime | None = None) -> ChangeSet:
    """Level-4 (ISA-101 detail) faceplate of a CAT drawn from a design (.NET HMI), opened by a click on the
    CAT's SA symbol `symbol` when it exists. Window title = AssetName when IThis has it (as SE.Agile faceplates)."""
    td, cat, hmi_td, bridges, warnings, types, hmi_outputs, ithis = _prepare(sol, cat_name, design, "hmi")
    boxes, W, H = sb.layout(design, bridges)
    if "AssetName" in ithis and not any(b.var == "AssetName" and b.kind == "exec" for b in boxes):
        boxes.append(sb.Box("xAssetName", "exec", 0, 0, 0, 0, var="AssetName"))
    now = now or _dt.datetime.now()
    header = cg.header(now)
    proj_dir = cat.cfg_file.rsplit("/", 2)[0]
    parent = proj_dir.rsplit("/", 1)[0] + "/" if "/" in proj_dir else ""
    n, ns = td.name, td.namespace or "Main"
    w.check_identifier(faceplate, "faceplate name")
    cs = ChangeSet(sol.root, f"build SA faceplate {faceplate} for CAT {n}")
    existing = {s.name: s for s in cat.symbols}
    if faceplate in existing and not (overwrite and existing[faceplate].is_faceplate):
        raise EditError(f"CAT {n} already has a symbol '{faceplate}'"
                        + ("; pass overwrite=true to regenerate it." if existing[faceplate].is_faceplate else "."))
    if faceplate not in existing:
        add_symbol(sol, n, faceplate, "hmi", faceplate=True, now=now, cs=cs)
    base = f"{parent}HMI/{n}/{n}_{faceplate}"
    fp_ns = f"{cg.ns_root(ns)}.Faceplates.{n}"
    designer = sb.dotnet_designer(header, fp_ns, faceplate, boxes, types, W, H, bridges, faceplate=True)
    issues = sb.gate("hmi", faceplate, designer, None)
    if issues:
        raise EditError("Generated faceplate fails the HMI review (please report): " + json.dumps(issues, default=str)[:800])
    _put(cs, sol.root, f"{base}.cnv.Designer.cs", designer.encode("utf-8"))
    _put(cs, sol.root, f"{base}.cnv.cs", sb.dotnet_code_behind(header, fp_ns, faceplate, boxes, types, bridges,
                                                             hmi_outputs, faceplate=True).encode("utf-8"))
    _put(cs, sol.root, f"{base}.cnv.resx", sb.dotnet_resx(w.template("cat/cnv.resx"), boxes, W, H))
    linked = False
    if symbol and symbol in existing and not existing[symbol].is_faceplate:
        rel = f"{parent}HMI/{n}/{n}_{symbol}.cnv.Designer.cs"
        text = (sol.root / rel).read_bytes().decode("utf-8-sig") if (sol.root / rel).exists() else ""
        if text and "this.card." in text and f'OpenFaceplate("{faceplate}"' not in text:
            d = ds.parse(text)
            sec = next(s for s in d.sections if s.name == "card")
            at = next(i for i, ln in enumerate(sec.lines) if ".Pen = " in ln)
            sec.lines.insert(at, f'\t\t\tthis.card.OpenFaceplates.Add(new NxtControl.GuiFramework.OpenFaceplate('
                                 f'"{faceplate}", NxtControl.GuiFramework.MouseButtonType.Click));')
            _put(cs, sol.root, rel, b"\xef\xbb\xbf" + ds.dumps(d).encode("utf-8") if (sol.root / rel).read_bytes()
                 .startswith(b"\xef\xbb\xbf") else ds.dumps(d).encode("utf-8"))
            linked = True
        elif f'OpenFaceplate("{faceplate}"' in text:
            linked = True
    cs.commit_docs()
    cs.warnings += [w_ for w_ in warnings if "eHMI" not in w_]
    cs.warnings.append(f"info: faceplate {W}x{H}; " + (f"a click on {symbol} opens it." if linked else
                       f"open it from a symbol (OpenFaceplates) — no generated symbol '{symbol}' to link."))
    return cs


# -- displays -------------------------------------------------------------------------------------------

import re  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402

from . import designer as ds  # noqa: E402
from . import dotnet_edit as de  # noqa: E402
from . import ehmi_edit as ee  # noqa: E402
from . import sa_style as st  # noqa: E402

MAX_INSTANCES = {1: 12, 2: 30, 3: 60, 4: 80}


@dataclass
class SectionDesign:
    title: str
    instances: list[str]
    columns: int = 0  # 0 = as many as fit


@dataclass
class DisplayDesign:
    canvas: str
    title: str
    sections: list[SectionDesign]
    level: int = 2
    symbol: str | None = None  # preferred symbol name (default: sSA/seSA if the CAT has it)
    device: str | None = None  # eHMI device
    replace: bool = False


def _symbol_for(sol: Solution, instance: str, technology: str, preferred: str | None):
    _app, fb = ee._app_instance(sol, instance, None)
    td = sol.find_type(fb.type, fb.namespace)
    cat = sol.cats.get(td.qualified_name) if td else None
    if cat is None:
        raise EditError(f"'{instance}' is a {fb.type}, not a CAT; only CAT instances can be placed.")
    cands = [s for s in cat.symbols if s.technology == technology and not s.is_faceplate]
    if not cands:
        raise EditError(f"CAT {td.name} has no {technology} symbol; build one with eae_hmi_symbol_build.")
    want = [preferred] if preferred else []
    want += ["sSA"] if technology == "hmi" else ["seSA"]
    sym = next((s for name in want for s in cands if s.name == name), cands[0])
    proj_dir = cat.cfg_file.rsplit("/", 2)[0]
    path = os.path.normpath(f"{proj_dir}/{sym.file}").replace("\\", "/")
    size = (240, 120)
    try:
        if technology == "hmi":
            text = (sol.root / path.replace(".cnv.cs", ".cnv.Designer.cs")).read_text(encoding="utf-8-sig")
            m = re.search(r"this\.SymbolSize = new System\.Drawing\.Size\((\d+), (\d+)\)", text)
            if m:
                size = (int(m.group(1)), int(m.group(2)))
        else:
            design = jsonrt.load(sol.root / path.replace(".sym.ts", ".sym.json")).data.get("_design_", {})
            size = (int(design.get("width", 240)), int(design.get("height", 120)))
    except (OSError, ValueError):
        pass
    sa = sym.name in ("sSA", "seSA")
    return fb, td, sym, size, sa


def _grid(design: DisplayDesign, sizes: dict[str, tuple[int, int]], W: int) -> tuple[list[dict], int]:
    """Frames, titles and symbol positions. Returns (items, bottom)."""
    items = [{"kind": "title", "name": "dispTitle", "x": 16, "y": 8, "text": design.title}]
    y = 40
    for i, sec in enumerate(design.sections, 1):
        cw = max(sizes[n][0] for n in sec.instances) + 16
        ch = max(sizes[n][1] for n in sec.instances) + 16
        fit = max(1, (W - 48) // cw)
        cols = min(sec.columns or fit, fit, len(sec.instances))
        rows = -(-len(sec.instances) // cols)
        h = 30 + rows * ch
        items.append({"kind": "frame", "name": f"sec{i}", "x": 16, "y": y, "w": W - 32, "h": h})
        items.append({"kind": "section", "name": f"secTitle{i}", "x": 24, "y": y + 6, "text": sec.title})
        for k, inst in enumerate(sec.instances):
            r, c = divmod(k, cols)
            items.append({"kind": "symbol", "name": inst, "x": 24 + c * cw, "y": y + 30 + r * ch})
        y += h + 12
    return items, y


def _check_display(design: DisplayDesign) -> list[str]:
    if design.level not in MAX_INSTANCES:
        raise EditError("level must be 1..4 (ISA-101 display hierarchy).")
    names = [n for s in design.sections for n in s.instances]
    if not names:
        raise EditError("A display needs at least one section with instances.")
    if len(set(names)) != len(names):
        raise EditError("An instance appears twice on the display.")
    out = []
    if len(names) > MAX_INSTANCES[design.level]:
        out.append(f"warning: {len(names)} instances on a level-{design.level} display (guide ≤ "
                   f"{MAX_INSTANCES[design.level]}); split it or move detail to a lower level.")
    if design.level == 1:
        out.append("info: a level-1 overview should show area status and KPIs; consider summary CATs per area.")
    return out


def build_display(sol: Solution, design: DisplayDesign, technology: str = "hmi") -> ChangeSet:
    if technology not in ("hmi", "ehmi"):
        raise EditError("technology must be hmi or ehmi (build each display once per technology).")
    warnings = _check_display(design)
    w.check_identifier(design.canvas, "canvas name")
    cs = ChangeSet(sol.root, f"build {technology} display {design.canvas} (level {design.level})")
    resolved = {}
    for sec in design.sections:
        for inst in sec.instances:
            fb, td, sym, size, sa = _symbol_for(sol, inst, technology, design.symbol)
            resolved[inst] = (fb, td, sym, size)
            if not sa:
                warnings.append(f"info: {inst} uses {td.name}.{sym.name}; build an SA symbol with eae_hmi_symbol_build "
                                "for analog indicators and alarm indicators.")
    sizes = {k: v[3] for k, v in resolved.items()}
    if technology == "hmi":
        _build_dotnet(sol, cs, design, resolved, sizes, warnings)
    else:
        _build_ehmi(sol, cs, design, resolved, sizes, warnings)
    cs.commit_docs()
    cs.warnings += warnings
    return cs


def _build_dotnet(sol, cs, design, resolved, sizes, warnings) -> None:
    proj = de._hmi_project(sol)
    pdir = proj.path.rsplit("/", 1)[0]
    designer_rel, resx_rel = f"{pdir}/{design.canvas}.cnv.Designer.cs", f"{pdir}/{design.canvas}.cnv.resx"
    if (sol.root / designer_rel).exists():
        designer_rel, resx_rel = de._canvas_files(sol, design.canvas)
        raw = (sol.root / designer_rel).read_bytes()
    else:
        created = de.create_canvas(sol, design.canvas)
        cs.changes.update(created.changes)
        cs._docs.update(created._docs)
        raw = cs.changes[designer_rel].new
    d = ds.parse(raw.decode("utf-8-sig"))
    if d.objects and not design.replace:
        raise EditError(f"Canvas {design.canvas} already has objects; pass replace=true to rebuild it.")
    for name in list(d.objects):
        ds.remove_object(d, name)
    text = ds.dumps(d)
    m = re.search(r"this\.Size = new System\.Drawing\.Size\((\d+), (\d+)\)", text)
    W, H = (int(m.group(1)), int(m.group(2))) if m else (1280, 730)
    items, bottom = _grid(design, sizes, W)
    if bottom > H:
        warnings.append(f"warning: the content needs {bottom}px but the canvas is {H}px high; split the display.")
    names = []
    for it in items:
        n = it["name"]
        if it["kind"] in ("title", "section"):
            size = st.SIZE_TITLE if it["kind"] == "title" else st.SIZE_TEXT
            ds.add_object(d, n, "NxtControl.GuiFramework.FreeText", [
                ("Color", de_c(st.TEXT)),
                ("Font", f'new NxtControl.Drawing.Font("{st.FONT}", {size}F, System.Drawing.FontStyle.Bold)'),
                ("Location", f"new NxtControl.Drawing.PointF({ds.num(it['x'])}, {ds.num(it['y'])})"),
                ("Name", ds.string(n)), ("Text", ds.string(it["text"]))], begin_init=False)
        elif it["kind"] == "frame":
            rect = ", ".join(f"((float)({ds.num(v)}))" for v in (it["x"], it["y"], it["w"], it["h"]))
            ds.add_object(d, n, "NxtControl.GuiFramework.Rectangle", [
                ("Bounds", f"new NxtControl.Drawing.RectF({rect})"),
                ("Brush", f"new NxtControl.Drawing.Brush({de_c((222, 222, 222))})"),
                ("Font", f'new NxtControl.Drawing.Font("{st.FONT}", 8F, System.Drawing.FontStyle.Regular)'),
                ("Name", ds.string(n)),
                ("Pen", f"new NxtControl.Drawing.Pen({de_c(st.BORDER)}, 1F, NxtControl.Drawing.DashStyle.Solid)")],
                begin_init=False)
        else:
            fb, td, sym, _ = resolved[n]
            w.check_identifier(n, "object name")
            ds.add_object(d, n, f"{cg.ns_root(td.namespace)}.Symbols.{td.name}.{sym.name}", [
                ("DesignMatrix", ds.matrix(it["x"], it["y"])), ("Name", ds.string(n)),
                ("SecurityToken", de.ALL_RIGHTS), ("TagName", ds.string(fb.id))])
        names.append(n)
    final = ds.dumps(d)
    issues = sb.gate("hmi", design.canvas, final, None)
    if issues:
        raise EditError("Generated display fails the HMI review: " + json.dumps(issues, default=str)[:800])
    bom = raw.startswith(b"\xef\xbb\xbf")
    _put(cs, sol.root, designer_rel, (b"\xef\xbb\xbf" if bom else b"") + final.encode("utf-8"))
    for n in names:
        de._resx_add(cs, resx_rel, n)


def de_c(rgb) -> str:
    return f"new NxtControl.Drawing.Color(((byte)({rgb[0]})), ((byte)({rgb[1]})), ((byte)({rgb[2]})))"


def _build_ehmi(sol, cs, design, resolved, sizes, warnings) -> None:
    try:
        ref = ee.find_canvas(sol, f"{design.device}/{design.canvas}" if design.device else design.canvas)
        jf = jsonrt.load(sol.root / ref.json_rel)
        rel, data, original = ref.json_rel, jf.data, jf
    except EditError:
        if not design.device:
            raise EditError(f"eHMI canvas {design.canvas} does not exist; pass device to create it.")
        created = ee.create_canvas(sol, design.device, design.canvas)
        cs.changes.update(created.changes)
        cs._docs.update(created._docs)
        rel = next(r for r in created.changes if r.endswith(f"/{design.canvas}.cnv.json"))
        original = jsonrt.parse_bytes(created.changes[rel].new)
        data = original.data
    if data.get("objects") and not design.replace:
        raise EditError(f"Canvas {design.canvas} already has objects; pass replace=true to rebuild it.")
    W = int(data.get("width") or 1024)
    H = int(data.get("height") or 688)
    items, bottom = _grid(design, sizes, W)
    if bottom > H:
        warnings.append(f"warning: the content needs {bottom}px but the canvas is {H}px high; split the display.")
    objs = []
    for it in items:
        if it["kind"] in ("title", "section"):
            objs.append({"type": "NxtControl.GuiFramework.FreeText", "name": it["name"], "left": it["x"], "top": it["y"],
                         "width": 400, "height": 20, "text": it["text"],
                         "fontSize": st.SIZE_TITLE if it["kind"] == "title" else st.SIZE_TEXT, "fontFamily": st.FONT,
                         "fontStyle": "normal", "fontWeight": "bold", "textColor": [*st.TEXT, 1]})
        elif it["kind"] == "frame":
            objs.append({"type": "NxtControl.GuiFramework.Rectangle", "name": it["name"], "left": it["x"], "top": it["y"],
                         "width": it["w"], "height": it["h"], "brush": {"color": [222, 222, 222, 1]},
                         "pen": {"color": [*st.BORDER, 1]}})
        else:
            fb, td, sym, size = resolved[it["name"]]
            ns = "WEB.Main" if (td.namespace or "Main") == "Main" else td.namespace
            objs.append({"type": f"{ns}.Symbols.{td.name}.{sym.name}", "name": it["name"], "left": it["x"],
                         "top": it["y"], "width": size[0], "height": size[1], "tagName": fb.id})
    data["objects"] = objs
    issues = sb.gate("ehmi", design.canvas, None, data)
    if issues:
        raise EditError("Generated display fails the HMI review: " + json.dumps(issues, default=str)[:800])
    _put(cs, sol.root, rel, jsonrt.dumps(original))
