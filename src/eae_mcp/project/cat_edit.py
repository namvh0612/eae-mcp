"""Create CATs (Composite Automation Types) the way EAE 26 does: the IEC 61499 part, the HMI interface SIFB
(IThis), a .NET HMI symbol and optionally an eHMI (web) symbol.
"""

from __future__ import annotations

import copy
import datetime as _dt
from dataclasses import dataclass
from xml.sax.saxutils import quoteattr

from ..hmi import codegen as cg
from ..io import xmlrt
from ..io.ids import new_guid
from ..model import Event, Interface, Var
from . import writer as w
from .changes import ChangeSet
from .edit import EditError, Target, _ensure_new_name, target_project
from .network_edit import _boundary_pins
from .solution import Solution

NL = "\r\n"
PLUG_OFFLINE = [("Plugin", "OfflineParametrizationEditor"), ("IEC61499Type", "CAT_OFFLINE")]
PLUG_OPCUA = [("Plugin", "OPCUAConfigurator"), ("IEC61499Type", "CAT_OPCUA")]
HMI_RESERVED = {"INIT", "INITO", "QI", "QO", "STATUS"}


def default_cat_interface() -> Interface:
    """The interface EAE gives a new CAT."""
    return Interface(
        event_inputs=[Event("INIT", comment="Initialization Request", with_vars=["QI"]),
                      Event("REQ", comment="Normal Execution Request", with_vars=["QI"])],
        event_outputs=[Event("INITO", comment="Initialization Confirm", with_vars=["QO"]),
                       Event("CNF", comment="Execution Confirmation", with_vars=["QO"])],
        input_vars=[Var("QI", "BOOL", comment="Input event qualifier")],
        output_vars=[Var("QO", "BOOL", comment="Output event qualifier")])


def hmi_interface(user: Interface | None) -> Interface:
    """The IThis SIFB interface: EAE's INIT/INITO/QI/QO/STATUS plus the user's HMI events and data.

    Input events/vars carry data from the CAT to the HMI; output events/vars come back from the HMI.
    """
    user = copy.deepcopy(user) if user else Interface()
    for it in user.event_inputs + user.event_outputs + user.input_vars + user.output_vars:
        if it.name.upper() in HMI_RESERVED:
            raise EditError(f"'{it.name}' is part of every HMI interface already; choose another name.")
    for v in user.input_vars + user.output_vars:
        cg.net_type(v.type)  # only types the .NET HMI accessors support
    return Interface(
        event_inputs=[Event("INIT", with_vars=["QI"])] + user.event_inputs,
        event_outputs=[Event("INITO", with_vars=["QO", "STATUS"])] + user.event_outputs,
        input_vars=[Var("QI", "BOOL")] + user.input_vars,
        output_vars=[Var("QO", "BOOL", comment="Event Output Qualifier"),
                     Var("STATUS", "STRING", comment="Service Status")] + user.output_vars)


@dataclass
class CatPaths:
    name: str
    iec: str  # IEC61499 project dir relative to the solution root ('IEC61499/')
    hmi_proj: str | None
    web_proj: str | None
    hmi: str  # <solution-relative> HMI/<Cat>/
    web: str

    @classmethod
    def of(cls, sol: Solution, t: Target, name: str) -> "CatPaths":
        parent = t.dir.rstrip("/").rsplit("/", 1)[0] + "/" if "/" in t.dir.rstrip("/") else ""

        def proj(kind: str, sub: str) -> str | None:
            found = [p.path for p in sol.projects_of(kind) if p.path.lower().startswith(f"{parent}{sub}/".lower())]
            return found[0] if found else None

        return cls(name, t.dir, proj("hmi", "HMI"), proj("web", "WEB"), f"{parent}HMI/{name}/", f"{parent}WEB/{name}/")


def _fill(name: str, **values: str) -> bytes:
    data = w.template(f"cat/{name}")
    for k, v in values.items():
        data = data.replace(f"@@{k}@@".encode(), v.encode())
    return data


def _stamp(now: _dt.datetime) -> dict[str, str]:
    return {"DATE": f"{now.month}/{now.day}/{now.year}", "TIME": now.strftime("%I:%M %p").lstrip("0")}


def _web_root(namespace: str) -> str:
    return "WEB.Main" if namespace in ("", "Main") else namespace


def build_hmi_fbt(name: str, itf: Interface, namespace: str) -> bytes:
    root = w._el("FBType", [("GUID", new_guid()), ("Name", f"{name}_HMI"),
                            ("Comment", "Service Interface Function Block Type"), ("Namespace", namespace)])
    w._header(root, "61499-2", "template")
    root.append(w.interface_element(itf, set()))
    service = w._sub(root, "Service", [("RightInterface", ""), ("LeftInterface", "")])
    w._sub(service, "ServiceSequence", [("Name", "")])
    return xmlrt.dumps(xmlrt.new_document(root, w.DOCTYPE.format(root="FBType")))


def build_cat_fbt(name: str, itf: Interface, hmi_qi_id: str, namespace: str, comment: str | None) -> bytes:
    root = w._el("FBType", [("GUID", new_guid()), ("Name", name), ("Format", "2.0"),
                            ("Comment", comment or "CAT Function Block Type"), ("Namespace", namespace)])
    w._sub(root, "Attribute", [("Name", "HMI.Alias"), ("Value", "")])
    w._header(root, "61499-2", "template")
    taken: set[str] = set()
    root.append(w.interface_element(itf, taken))
    net = w._sub(root, "FBNetwork")
    from ..io.ids import new_id16
    fb = w._sub(net, "FB", [("ID", new_id16(taken)), ("Name", "IThis"), ("Type", f"{name}_HMI"),
                            ("x", "2680"), ("y", "960"), ("Namespace", namespace)])
    w._sub(fb, "Parameter", [("Name", f"${hmi_qi_id}"), ("Value", "TRUE")])
    for pin in _boundary_pins(itf):
        net.append(pin)
    return xmlrt.dumps(xmlrt.new_document(root, w.DOCTYPE.format(root="FBType")))


def build_cfg(p: CatPaths, symbol: str, web_symbol: str | None, folder: str | None, project: str) -> bytes:
    n = p.name
    hmi, web = f"..\\HMI\\{n}\\{n}", f"..\\WEB\\{n}\\{n}"
    attrs = [("Name", n), ("CATFile", f"{n}\\{n}.fbt"), ("SymbolDefFile", f"{hmi}.def.cs"),
             ("SymbolEventFile", f"{hmi}.event.cs"), ("DesignFile", f"{hmi}.Design.resx"),
             ("DocFile", f"{n}\\{n}.doc.xml"), ("MetaFile", f"{n}\\{n}.meta.xml")]
    if folder:
        attrs.append(("Folder", folder))
    head = " ".join(f"{k}={quoteattr(v)}" for k, v in attrs)
    lines = ['<?xml version="1.0" encoding="utf-8"?>',
             '<CAT xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
             f'{head} xmlns="http://www.nxtcontrol.com/IEC61499.xsd">',
             f'  <HMIInterface Name="IThis" FileName="{n}\\{n}_HMI.fbt" DocFile="{n}\\{n}_HMI.doc.xml" UsedInCAT="true">',
             f'    <Symbol Name="{symbol}" FileName="{hmi}_{symbol}.cnv.cs" DocFile="{hmi}_{symbol}.doc.xml">']
    lines += [f"      <DependentFiles>{hmi}_{symbol}.cnv.{ext}</DependentFiles>" for ext in ("Designer.cs", "resx", "xml")]
    lines.append("    </Symbol>")
    if web_symbol:
        lines.append(f'    <WebSymbol Name="{web_symbol}" FileName="{web}_{web_symbol}.sym.ts">')
        lines += [f"      <DependentFiles>{web}_{web_symbol}.{ext}</DependentFiles>" for ext in ("sym.json", "sym.xml", "user.cs")]
        lines.append("    </WebSymbol>")
    lines += [f"    <MetaFile>{n}\\{n}_HMI.meta.xml</MetaFile>", "  </HMIInterface>"]
    for plugin, kind, part in (("OfflineParametrizationEditor", "CAT_OFFLINE", "CAT.offline"),
                               ("OfflineParametrizationEditor", "CAT_OFFLINE", "HMI.offline"),
                               ("OPCUAConfigurator", "CAT_OPCUA", "CAT.opcua"),
                               ("OPCUAConfigurator", "CAT_OPCUA", "HMI.opcua")):
        lines.append(f'  <Plugin Name="Plugin={plugin};IEC61499Type={kind};$ItemType$=None" Project="{project}" '
                     f'Value="{n}\\{n}_{part}.xml" />')
    lines += ['  <HWConfiguration xsi:nil="true" />', "</CAT>"]
    return NL.join(lines).encode()


def build_hmi_opcua(itf: Interface) -> bytes:
    """One disabled OPC UA variable per user HMI variable (QI/QO/STATUS are never exposed)."""
    head = ('<?xml version="1.0" encoding="utf-8"?>' + NL + '<OPCUAObject xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"')
    variables = [v for v in itf.input_vars + itf.output_vars if v.name not in HMI_RESERVED]
    if not variables:
        return (head + " />").encode()
    body = [head + ">"]
    for v in variables:
        body += [f'  <OPCUAVariable UID="{v.id}" Enabled="false">', "    <Extensions>", "      <Extension>",
                 "        <RTAddress>V1;${VariableFullPath}</RTAddress>", "      </Extension>", "    </Extensions>",
                 "  </OPCUAVariable>"]
    return NL.join(body + ["</OPCUAObject>"]).encode()


def build_mapping(itf: Interface) -> bytes:
    """<Symbol>.cnv.xml / .sym.xml: which HMI events carry which values."""
    types = {v.name: v.type for v in itf.input_vars + itf.output_vars}

    def events(tag: str, items: list[Event], skip: str) -> list[str]:
        items = [e for e in items if e.name != skip]
        if not items:
            return [f"  <{tag} />"]
        return ([f"  <{tag}>"] + [f'    <Event Name="{e.name}">{";".join(e.with_vars)}</Event>' for e in items]
                + [f"  </{tag}>"])

    def data(tag: str, item: str, items: list[Var]) -> list[str]:
        items = [v for v in items if v.name not in HMI_RESERVED]
        if not items:
            return [f"  <{tag} />"]
        return [f"  <{tag}>"] + [f'    <{item} Name="{v.name}" Type="{types[v.name]}" />' for v in items] + [f"  </{tag}>"]

    lines = (['<?xml version="1.0" encoding="utf-8"?>', "<Mapping>"] + events("EventInputs", itf.event_inputs, "INIT")
             + events("EventOutputs", itf.event_outputs, "INITO") + data("Inputs", "Input", itf.input_vars)
             + data("Outputs", "Output", itf.output_vars) + ["</Mapping>"])
    return NL.join(lines).encode()


def _dotnet_symbol_files(p: CatPaths, ns_root: str, symbol: str, itf: Interface, stamp: dict) -> dict[str, bytes]:
    base = f"{p.hmi}{p.name}_{symbol}"
    vals = dict(stamp, NS=ns_root, CAT=p.name, SYM=symbol)
    return {f"{base}.cnv.cs": _fill("cnv.cs", **vals), f"{base}.cnv.Designer.cs": _fill("cnv.Designer.cs", **vals),
            f"{base}.cnv.resx": w.template("cat/cnv.resx"), f"{base}.cnv.xml": build_mapping(itf),
            f"{base}.doc.xml": w.template("doc.xml")}


def _web_symbol_files(p: CatPaths, ns_root: str, symbol: str, stamp: dict) -> dict[str, bytes]:
    base = f"{p.web}{p.name}_{symbol}"
    vals = dict(stamp, NS=ns_root, CAT=p.name, SYM=symbol)
    return {f"{base}.sym.ts": _fill("sym.ts", **vals), f"{base}.sym.json": w.template("cat/sym.json"),
            f"{base}.sym.xml": w.template("cat/sym.xml"), f"{base}.user.cs": _fill("user.cs", **vals),
            f"{base}.doc.xml": w.template("doc.xml")}


def _register_hmi_symbol(cs: ChangeSet, p: CatPaths, symbol: str) -> None:
    proj = cs.doc(p.hmi_proj)
    n, cnv = p.name, f"{p.name}_{symbol}.cnv.cs"
    w.add_project_item(proj, "Compile", f"{n}\\{cnv}", [])
    w.add_project_item(proj, "Compile", f"{n}\\{n}_{symbol}.cnv.Designer.cs", [("DependentUpon", cnv)])
    w.add_project_item(proj, "EmbeddedResource", f"{n}\\{n}_{symbol}.cnv.resx", [("DependentUpon", cnv)])
    w.add_project_item(proj, "EmbeddedResource", f"{n}\\{n}_{symbol}.cnv.xml", [("DependentUpon", cnv)])


def _register_web_symbol(cs: ChangeSet, p: CatPaths, symbol: str) -> None:
    proj = cs.doc(p.web_proj)
    n, ts = p.name, f"{p.name}_{symbol}.sym.ts"
    w.add_project_item(proj, "Compile", f"{n}\\{n}_{symbol}.user.cs", [("DependentUpon", ts)])
    w.add_project_item(proj, "None", f"{n}\\{ts}", [])
    w.add_project_item(proj, "EmbeddedResource", f"{n}\\{n}_{symbol}.sym.json", [("DependentUpon", ts)])
    w.add_project_item(proj, "EmbeddedResource", f"{n}\\{n}_{symbol}.sym.xml", [("DependentUpon", ts)])


def create_cat(sol: Solution, name: str, itf: Interface | None = None, hmi: Interface | None = None,
               symbol: str = "sDefault", web_symbol: str | None = "seDefault", folder: str | None = None,
               library: str | None = None, comment: str | None = None,
               now: _dt.datetime | None = None, cs: ChangeSet | None = None) -> ChangeSet:
    """New CAT with an IThis HMI interface, a .NET HMI symbol and (unless web_symbol=None) an eHMI symbol."""
    _ensure_new_name(sol, name)
    _ensure_new_name(sol, f"{name}_HMI")
    w.check_identifier(symbol, "symbol name")
    if web_symbol:
        w.check_identifier(web_symbol, "web symbol name")
    t = target_project(sol, library)
    p = CatPaths.of(sol, t, name)
    if p.hmi_proj is None:
        raise EditError("This solution has no .NET HMI project next to the IEC61499 project; EAE needs one for CATs.")
    if web_symbol and p.web_proj is None:
        raise EditError("This solution has no WEB (eHMI) project; pass web_symbol=null to create the CAT without one.")
    itf = copy.deepcopy(itf) if itf else default_cat_interface()
    hitf = hmi_interface(hmi)
    now = now or _dt.datetime.now()
    stamp = _stamp(now)
    ns = t.namespace
    cs = cs or ChangeSet(sol.root, f"create CAT {name}")

    # IEC 61499 part. Build the HMI SIFB first: its QI ID is the IThis parameter in the CAT network.
    d = f"{t.dir}{name}/{name}"
    cs.create(f"{d}_HMI.fbt", build_hmi_fbt(name, hitf, ns))
    cs.create(f"{d}.fbt", build_cat_fbt(name, itf, hitf.input_vars[0].id, ns, comment))
    project_name = t.project.name
    cs.create(f"{d}.cfg", build_cfg(p, symbol, web_symbol, folder, project_name))
    for part in ("", "_HMI"):
        cs.create(f"{d}{part}.doc.xml", w.template("doc.xml"))
        cs.create(f"{d}{part}.meta.xml", w.template("meta.xml"))
    cs.create(f"{d}_CAT.offline.xml", w.template("offline.xml"))
    cs.create(f"{d}_HMI.offline.xml", w.template("offline.xml"))
    cs.create(f"{d}_CAT.opcua.xml", w.template("opcua_complex.xml"))
    cs.create(f"{d}_HMI.opcua.xml", build_hmi_opcua(hitf))

    # .NET HMI: generated support code, design resources and the symbol.
    root = cg.ns_root(ns)
    h = f"{p.hmi}{name}"
    cs.create(f"{h}.event.cs", cg.event_cs(name, ns, hitf, [cg.SymbolRef(symbol)], now).encode())
    cs.create(f"{h}.def.cs", cg.def_cs(name, ns, [cg.SymbolRef(symbol)], now).encode())
    cs.create(f"{h}.Design.resx", w.template("cat/Design.resx"))
    for rel, data in _dotnet_symbol_files(p, root, symbol, hitf, stamp).items():
        cs.create(rel, data)
    if web_symbol:
        for rel, data in _web_symbol_files(p, _web_root(ns), web_symbol, stamp).items():
            cs.create(rel, data)

    # Registration, as EAE writes it (some companions are listed twice).
    proj = cs.doc(t.dfbproj)
    meta = [("IEC61499Type", "CAT")] + ([("Parent", folder)] if folder else [])
    if folder and (cs.root / t.folders_xml).exists():
        w.ensure_folder(cs.doc(t.folders_xml), "CAT", folder)
    fbt = f"{name}.fbt"
    w.add_project_item(proj, "Compile", f"{name}\\{fbt}", meta)
    w.add_project_item(proj, "Compile", f"{name}\\{name}_HMI.fbt",
                       [("IEC61499Type", "CAT"), ("Usage", "Private"), ("DependentUpon", fbt),
                        ("HMI", f"..\\HMI\\{name}\\{name}_{symbol}.cnv.cs")])
    dep = [("DependentUpon", fbt)]
    none_items = [
        (f"..\\HMI\\{name}\\{name}_{symbol}.doc.xml", dep),
        (f"{name}\\{name}.cfg", dep + [("IEC61499Type", "CAT")]),
        (f"{name}\\{name}.doc.xml", dep), (f"{name}\\{name}.meta.xml", dep),
        (f"{name}\\{name}_CAT.offline.xml", dep + PLUG_OFFLINE), (f"{name}\\{name}_CAT.opcua.xml", dep + PLUG_OPCUA),
        (f"{name}\\{name}_HMI.doc.xml", [("DependentUpon", f"{name}_HMI.fbt")]), (f"{name}\\{name}_HMI.doc.xml", dep),
        (f"{name}\\{name}_HMI.meta.xml", [("DependentUpon", f"{name}_HMI.fbt")]), (f"{name}\\{name}_HMI.meta.xml", dep),
        (f"{name}\\{name}_HMI.offline.xml", dep + PLUG_OFFLINE), (f"{name}\\{name}_HMI.opcua.xml", dep + PLUG_OPCUA),
    ]
    for include, md in none_items:
        w.add_project_item(proj, "None", include, md, allow_duplicate=True)
    hproj = cs.doc(p.hmi_proj)
    w.add_project_item(hproj, "Compile", f"{name}\\{name}.def.cs", [])
    w.add_project_item(hproj, "Compile", f"{name}\\{name}.event.cs", [])
    w.add_project_item(hproj, "EmbeddedResource", f"{name}\\{name}.Design.resx", [])
    _register_hmi_symbol(cs, p, symbol)
    if web_symbol:
        _register_web_symbol(cs, p, web_symbol)
    cs.commit_docs()
    _check(cs, sol, f"{d}.fbt", f"{d}_HMI.fbt")
    return cs


def _check(cs: ChangeSet, sol: Solution, *rels: str) -> None:
    """Parse what will be written so broken output never reaches the disk."""
    from .types import parse_type_element

    for rel in rels:
        parse_type_element(xmlrt.parse_bytes(cs.changes[rel].new).root, rel)


def refresh_hmi_code(cs: ChangeSet, sol: Solution, hmi_rel: str, now: _dt.datetime | None = None) -> None:
    """After the IThis interface (<Cat>_HMI.fbt) changed: regenerate <Cat>.event.cs and every .NET symbol's
    .cnv.xml mapping, and keep <Cat>_HMI.opcua.xml in step (new variables added disabled, removed ones dropped).
    """
    import os

    from .types import parse_type_element

    cat = next((c for c in sol.cats.values() if c.hmi_interface_file and
                os.path.normpath(f"{c.cfg_file.rsplit('/', 2)[0]}/{c.hmi_interface_file}") == os.path.normpath(hmi_rel)),
               None)
    if cat is None or hmi_rel not in cs.changes:
        return
    td = sol.types.get(cat_qualified(sol, cat))
    itf = parse_type_element(xmlrt.parse_bytes(cs.changes[hmi_rel].new).root, hmi_rel).interface
    for v in itf.input_vars + itf.output_vars:
        cg.net_type(v.type)
    proj_dir = cat.cfg_file.rsplit("/", 2)[0]

    def rel(p: str) -> str:
        return os.path.normpath(f"{proj_dir}/{p}").replace("\\", "/")

    def put(path: str, data: bytes) -> None:
        old = (cs.root / path).read_bytes() if (cs.root / path).exists() else None
        if old != data:
            from .changes import FileChange
            cs.changes[path] = FileChange(path, old, data)

    dotnet = [s for s in cat.symbols if s.technology == "hmi"]
    event_file = next((g for g in cat.generated_files if g.endswith(".event.cs")), None)
    if event_file and td is not None:
        put(rel(event_file), cg.event_cs(td.name, td.namespace, itf,
                                         [cg.SymbolRef(s.name, s.is_faceplate) for s in dotnet], now).encode())
    for s in dotnet:
        mapping = rel(s.file).replace(".cnv.cs", ".cnv.xml")
        if (cs.root / mapping).exists():
            put(mapping, build_mapping(itf))
    opcua = hmi_rel[: -len(".fbt")] + ".opcua.xml"
    if (cs.root / opcua).exists():
        _sync_hmi_opcua(cs, opcua, itf)
        cs.commit_docs()


def cat_qualified(sol: Solution, cat) -> str:
    return next(q for q, c in sol.cats.items() if c is cat)


def _sync_hmi_opcua(cs: ChangeSet, rel: str, itf: Interface) -> None:
    from .types import children

    xf = cs.doc(rel)
    root = xf.root
    wanted = [v for v in itf.input_vars + itf.output_vars if v.name not in HMI_RESERVED and v.id]
    have = {e.get("UID"): e for e in children(root, "OPCUAVariable")}
    for uid, el in have.items():
        if uid not in {v.id for v in wanted}:
            xmlrt.remove_child(el)
    for v in wanted:
        if v.id in have:
            continue
        el = w._el("OPCUAVariable", [("UID", v.id), ("Enabled", "false")], ns=w._ns(root))
        ext = w._sub(w._sub(el, "Extensions"), "Extension")
        w._sub(ext, "RTAddress").text = "V1;${VariableFullPath}"
        xmlrt.insert_child(root, el)


# -- add a symbol / faceplate to an existing CAT -------------------------------------------------------


def _cat_of(sol: Solution, name: str):
    td = sol.find_type(name)
    if td is None or td.kind != "cat":
        raise EditError(f"'{name}' is not a CAT of this solution.")
    cat = sol.cats.get(td.qualified_name)
    if cat is None:
        raise EditError(f"CAT {td.qualified_name} has no .cfg manifest.")
    return td, cat


def _project_at(sol: Solution, kind: str, directory: str):
    found = [p for p in sol.projects_of(kind) if p.path.rsplit("/", 1)[0].lower() == directory.lower()]
    if not found:
        raise EditError(f"No {kind.upper()} project in {directory}/.")
    return found[0]


def add_symbol(sol: Solution, cat_name: str, name: str, technology: str = "hmi", faceplate: bool = False,
               now: _dt.datetime | None = None, cs: ChangeSet | None = None) -> ChangeSet:
    """Add a .NET HMI symbol or faceplate (technology='hmi') or an eHMI symbol ('ehmi') to a CAT."""
    import os

    from .types import children, local

    td, cat = _cat_of(sol, cat_name)
    w.check_identifier(name, "symbol name")
    if any(s.name == name for s in cat.symbols):
        raise EditError(f"CAT {td.name} already has a symbol '{name}'.")
    if technology not in ("hmi", "ehmi"):
        raise EditError("technology must be 'hmi' (.NET HMI) or 'ehmi' (web).")
    if faceplate and technology != "hmi":
        raise EditError("eHMI faceplates are not supported (no sample to learn the format from).")
    hmi_rel = os.path.normpath(f"{cat.cfg_file.rsplit('/', 2)[0]}/{cat.hmi_interface_file}").replace("\\", "/")
    hmi_td = next((t for t in sol.types.values() if t.path == hmi_rel), None)
    if hmi_td is None:
        raise EditError(f"CAT {td.name} has no HMI interface type ({hmi_rel}).")
    now = now or _dt.datetime.now()
    stamp = _stamp(now)
    ns = td.namespace or "Main"
    proj_dir = cat.cfg_file.rsplit("/", 2)[0]
    parent = proj_dir.rsplit("/", 1)[0] + "/" if "/" in proj_dir else ""
    n = td.name
    cs = cs or ChangeSet(sol.root, f"add {'faceplate' if faceplate else technology + ' symbol'} {name} to CAT {n}")
    cfg = cs.doc(cat.cfg_file)
    root = cfg.root
    kids = children(root, "HMIInterface")
    if not kids:
        raise EditError(f"{cat.cfg_file} has no <HMIInterface>.")
    hmi_el = kids[0]
    entries = children(hmi_el)

    if technology == "hmi":
        proj = _project_at(sol, "hmi", f"{parent}HMI")
        base = f"{parent}HMI/{n}/{n}_{name}"
        vals = dict(stamp, NS=cg.ns_root(ns), CAT=n, SYM=name)
        cnv_cs = _fill("cnv.cs", **vals)
        designer = _fill("cnv.Designer.cs", **vals)
        if faceplate:
            cnv_cs = (cnv_cs.replace(b".Symbols.", b".Faceplates.")
                      .replace(b"NxtControl.GuiFramework.HMISymbol", b"NxtControl.GuiFramework.HMIFaceplate"))
            designer = designer.replace(b".Symbols.", b".Faceplates.").replace(
                f'this.Name = "{name}";'.encode(),
                f'this.Name = "{name}";\r\n\t\t\tthis.Size = new System.Drawing.Size(400, 300);'.encode())
        cs.create(f"{base}.cnv.cs", cnv_cs)
        cs.create(f"{base}.cnv.Designer.cs", designer)
        cs.create(f"{base}.cnv.resx", w.template("cat/cnv.resx"))
        if not faceplate:
            cs.create(f"{base}.cnv.xml", build_mapping(hmi_td.interface))
        cs.create(f"{base}.doc.xml", w.template("doc.xml"))
        hp = cs.doc(proj.path)
        cnv = f"{n}_{name}.cnv.cs"
        w.add_project_item(hp, "Compile", f"{n}\\{cnv}", [])
        w.add_project_item(hp, "Compile", f"{n}\\{n}_{name}.cnv.Designer.cs", [("DependentUpon", cnv)])
        w.add_project_item(hp, "EmbeddedResource", f"{n}\\{n}_{name}.cnv.resx", [("DependentUpon", cnv)])
        if not faceplate:
            w.add_project_item(hp, "EmbeddedResource", f"{n}\\{n}_{name}.cnv.xml", [("DependentUpon", cnv)])
        w.add_project_item(hp, "None", f"{n}\\{n}_{name}.doc.xml", [("DependentUpon", cnv)])
        hmi = f"..\\HMI\\{n}\\{n}_{name}"
        el = w._el("Symbol", [("Name", name), ("FileName", f"{hmi}.cnv.cs"), ("DocFile", f"{hmi}.doc.xml"),
                              ("IsFaceplate", "true" if faceplate else None)], ns=w._ns(root))
        for ext in ("cnv.Designer.cs", "cnv.resx") + (() if faceplate else ("cnv.xml",)):
            w._sub(el, "DependentFiles").text = f"{hmi}.{ext}"
        after = [i for i, e in enumerate(entries) if local(e) == "Symbol"]
        before = [i for i, e in enumerate(entries) if local(e) in ("WebSymbol", "MetaFile")]
        xmlrt.insert_child(hmi_el, el, after[-1] + 1 if after else (before[0] if before else None))

        # Generated code follows the symbol list.
        symbols = [cg.SymbolRef(s.name, s.is_faceplate) for s in cat.symbols if s.technology == "hmi"]
        symbols.append(cg.SymbolRef(name, faceplate))
        for gen in cat.generated_files:
            path = os.path.normpath(f"{proj_dir}/{gen}").replace("\\", "/")
            if gen.endswith(".event.cs"):
                data = cg.event_cs(n, ns, hmi_td.interface, symbols, now)
            elif gen.endswith(".def.cs"):
                data = cg.def_cs(n, ns, symbols, now)
            else:
                continue
            _put(cs, path, data.encode())
    else:
        proj = _project_at(sol, "web", f"{parent}WEB")
        p = CatPaths(n, proj_dir + "/", None, proj.path, f"{parent}HMI/{n}/", f"{parent}WEB/{n}/")
        for rel, data in _web_symbol_files(p, _web_root(ns), name, stamp).items():
            cs.create(rel, data)
        _register_web_symbol(cs, p, name)
        web = f"..\\WEB\\{n}\\{n}_{name}"
        el = w._el("WebSymbol", [("Name", name), ("FileName", f"{web}.sym.ts")], ns=w._ns(root))
        for ext in ("sym.json", "sym.xml", "user.cs"):
            w._sub(el, "DependentFiles").text = f"{web}.{ext}"
        after = [i for i, e in enumerate(entries) if local(e) in ("Symbol", "WebSymbol")]
        before = [i for i, e in enumerate(entries) if local(e) == "MetaFile"]
        xmlrt.insert_child(hmi_el, el, after[-1] + 1 if after else (before[0] if before else None))
    cs.commit_docs()
    return cs


def _put(cs: ChangeSet, path: str, data: bytes) -> None:
    from .changes import FileChange

    old = (cs.root / path).read_bytes() if (cs.root / path).exists() else None
    if old != data:
        cs.changes[path] = FileChange(path, old, data)
