"""MCP server for EcoStruxure Automation Expert 26 solutions: read tools, resources and prompts."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__, safety, services
from .config import Config

INSTRUCTIONS = """\
Tools for understanding and editing EcoStruxure Automation Expert (EAE) 26 solutions (IEC 61499):
types, networks, CATs, the System (devices, resources, mapping, OPC UA), .NET HMI and eHMI.
Start with eae_list_solutions / eae_open_solution, then eae_summary.
Every tool takes an optional `solution`: the solution name (e.g. 'MyPlant') or its folder path.
Only solutions under the configured roots can be opened.
- Learn a concept: resource eae://concepts/<name> (overview, adapter, datatype, basic-fb, composite-fb,
  subapp, function, cat, system, hmi-dotnet, ehmi, folders, library, standard-library, rest-client,
  hmi-design) or eae_knowledge '<topic>' for just the matching sections.
- Understand: eae_explain (any type, instance, HMI document, device), eae_trace (canvas → instance → CAT →
  algorithms), eae_cat_describe, eae_system_describe, eae_hmi_describe, eae_show_component_files.
- Reuse before creating: eae_library_guide, eae_generic_fbs.
- Write tools default to dry_run=true and return a diff; show it to the user and write with dry_run=false
  only after they agree. Writing needs allow_write in the server config. Run eae_validate after changes.
- HMI: eae_hmi_design_suggest → eae_hmi_symbol_build / eae_hmi_faceplate_build → eae_hmi_display_build →
  eae_hmi_review. Never invent engineering limits (ranges, alarm limits): ask the user.
- Names are accepted everywhere; IDs are resolved and generated internally.
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def _json(data: Any) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False, default=str)


def create_server(config: Config | None = None) -> MCPServer:
    ws = services.Workspace(config or Config.load())
    mcp = MCPServer(name="eae-mcp", instructions=INSTRUCTIONS, version=__version__)

    def run(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (services.NotFound, LookupError, PermissionError, FileNotFoundError) as e:
            raise ToolError(str(e)) from e

    def sol(solution: str | None):
        return run(ws.get, solution)

    # -- solutions --------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    def eae_list_solutions() -> list[dict]:
        """List EAE solutions (.sln) found under the configured project roots."""
        return services.list_solutions(ws)

    @mcp.tool(annotations=READ_ONLY)
    def eae_open_solution(path: str) -> dict:
        """Open (index) an EAE solution folder or .sln file and make it the current solution.

        Returns a summary: projects, libraries, type counts, systems, library references.
        """
        return services.solution_summary(run(ws.open, path))

    @mcp.tool(annotations=READ_ONLY)
    def eae_summary(solution: str | None = None) -> dict:
        """Summary of the current (or named) solution."""
        return services.solution_summary(sol(solution))

    # -- types --------------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    def eae_list(kind: str | None = None, query: str = "", library: str | None = None,
                 folder: str | None = None, solution: str | None = None) -> list[dict] | dict:
        """List components.

        kind: adapter | datatype | basic | composite | subapp | function | cat | cat_hmi | sifb |
              system | hmi | hmi_canvas | hmi_symbol | hmi_faceplate | ehmi | ehmi_canvas | ehmi_symbol.
        Omit kind to list all types. library: '' for the main project, or e.g. 'SE.Agile'.
        folder: logical folder prefix such as '.Standard'.
        """
        s = sol(solution)
        return run(services.list_items, s, ws, kind, library, folder, query)

    @mcp.tool(annotations=READ_ONLY)
    def eae_get(name: str, include_xml: bool = False, solution: str | None = None) -> dict:
        """Full definition of a type (FB, CAT, adapter, datatype, function, subapp, library type).
        name: a type name, or an application instance name (resolves to its type). To list
        types of a kind, use eae_list instead.

        Networks are returned with connections resolved to names (`FB1.CNF -> FB2.START`).
        """
        s = sol(solution)
        td = run(services.find_type, s, name)
        return run(services.type_view, s, td, include_xml)

    @mcp.tool(annotations=READ_ONLY)
    def eae_find_usages(name: str, solution: str | None = None) -> list[dict]:
        """Where a type is used: type networks, adapter pins, variable types, CAT sub-CATs,
        application layers, resources and HMI documents."""
        s = sol(solution)
        return run(services.find_usages, s, ws, name)

    @mcp.tool(annotations=READ_ONLY)
    def eae_library_guide(library: str | None = None, folder: str | None = None, query: str | None = None,
                          solution: str | None = None) -> dict:
        """Compact, ranked map of the libraries in the solution (start here before building something).
        No arguments: libraries with kind counts, system libraries and generic FB families.
        library='SE.Agile': its folders with type families (versions collapsed), most used first.
        library + folder='.Standard.HMI': one line per type with its signature and usage count
        (`superseded` marks older versions). query='valve': search names/folders across libraries."""
        from .project.library_guide import library_guide
        return run(library_guide, sol(solution), library, folder, query)

    @mcp.tool(annotations=READ_ONLY)
    def eae_generic_fbs(base: str | None = None, solution: str | None = None) -> list[dict]:
        """Generic FB types used in the solution (VALFORMAT_<hash>, PERSISTENCE_<hash>, …): template,
        library, parameter string, pins learned from existing connections, and where they are used.
        Reuse one with eae_net_add_fb type=<template> generic_params=<parameters>."""
        from .project import library_guide as lg
        s = sol(solution)
        out = []
        for g in (lg.find_generic(s, base) if base else lg.generic_registry(s)):
            td = lg.generic_typedef(s, g["type"], g["namespace"])
            out.append({**{k: g[k] for k in ("base", "type", "namespace", "params", "uses", "used_in")},
                        "pins": lg.signature(td) if td else None})
        return out

    @mcp.tool(annotations=READ_ONLY)
    def eae_knowledge(query: str, limit: int = 4) -> list[dict]:
        """Search the built-in EAE knowledge (concepts, standard library, generic FBs, functions, HMI
        design standards) and return only the matching sections. Cheaper than reading whole concepts."""
        return run(services.knowledge_search, query, limit)

    @mcp.tool(annotations=READ_ONLY)
    def eae_hmi_review(name: str | None = None, technology: str | None = None, level: int | None = None,
                       solution: str | None = None) -> dict:
        """Review HMI displays for situation awareness / high-performance HMI practice (ISA-101, ASM,
        ISA-18.2): background, static use of saturated and alarm colors, images, fonts, text contrast,
        density, numbers without analog context, trends, canvas hierarchy and alarm classes. Also tells the
        two HMI styles apart per CAT and display (basic: widgets bound to IThis variables; agile: SE.Agile
        HMI blocks embedded by sub-CAT path) and checks every symbol binding (BIND-01 broken TagName,
        STY-01 mixed style, AG-01/AG-02, BS-01).
        name: one canvas/symbol (default: all canvases); technology: hmi | ehmi; level: ISA-101 display
        level 1-4 of the reviewed display(s) for density/trend rules. Background: eae_knowledge 'hmi design'."""
        return run(services.hmi_review, sol(solution), ws, name, technology, level)

    @mcp.tool(annotations=READ_ONLY)
    def eae_hmi_scripts(solution: str | None = None) -> dict:
        """HMI support classes (*.spt.cs: themes, helpers, event logs) and alarm-word profiles: arrays such as
        `AlarmDefinition[] Inverter = { Alarm(0, "active", "clear"), … }` cross-checked with the Basic FBs that
        pack alarm bits into a WORD (`AlarmWord.3 := …;` published through an HMI_Indication_Integer block).
        Findings: ALM-06 bit set by the logic without text (with the ST condition and comment as hints),
        ALM-07 text for a bit never set, ALM-08 duplicate/oversized bits, ALM-09 priority distribution,
        ALM-10 duplicate or missing texts. Add missing texts with eae_alarm_profile_add."""
        from .hmi import scripts as sc
        s = sol(solution)
        return {"scripts": [vars(x) for x in sc.list_scripts(s)], **sc.alarm_profiles(s)}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                                          openWorldHint=True))
    def eae_http_probe(url: str, method: str = "GET", token: str | None = None, auth: str = "bearer",
                       headers: dict[str, str] | None = None) -> dict:
        """Call a REST API once from this machine (GET/HEAD only) to check it before generating an EAE
        client with eae_rest_client_create: status, size versus the client's 4 KB buffer, and JSON fields
        with the path/occurrence/type the generated extractor needs. Must be enabled in eae-mcp.toml
        ([http_probe] enabled, allowed_hosts). The token is used for this call only and never returned."""
        from .rest_probe import probe
        return run(probe, ws.config, url, method, token, auth, headers)

    @mcp.tool(annotations=READ_ONLY)
    def eae_hmi_design_suggest(cat: str, title: str | None = None, solution: str | None = None) -> dict:
        """Draft situation-awareness symbol design for a CAT: element kind per signal (value / state / alarm /
        text) and the engineering data still missing (units, spans, normal ranges, alarm limits, state texts).
        Basic CATs: signals are IThis inputs. Agile CATs (IThis = AssetName, signals in SE.Agile HMI blocks):
        elements bind to the block paths, e.g. 'Equipment.IX'. Complete it from the user's description, then
        call eae_hmi_symbol_build. It never invents limits."""
        import os as _os
        from .hmi import sa_builder
        s = sol(solution)
        td = run(services.find_type, s, cat)
        c = s.cats.get(td.qualified_name)
        if c is None:
            raise ToolError(f"{td.name} is not a CAT.")
        rel = _os.path.normpath(f"{c.cfg_file.rsplit('/', 2)[0]}/{c.hmi_interface_file}").replace("\\", "/")
        hmi = next((t for t in s.types.values() if t.path == rel), None)
        if hmi is None:
            raise ToolError(f"{td.name} has no HMI interface.")
        from .hmi import agile_blocks
        from .hmi.style import NAME_VARS
        signals = [v for v in hmi.interface.input_vars if v.name not in NAME_VARS and v.name != "QI"]
        found = agile_blocks.blocks(s, c)
        if found and not signals:
            draft = agile_blocks.suggest(s, c, title or td.name)
            draft["style"] = "agile"
        else:
            draft = sa_builder.suggest(hmi.interface, title or td.name)
            draft["style"] = "mixed" if found else "basic"
            if found:
                draft["agile_blocks"] = [{"path": p, "type": t} for p, t in found]
                draft["note"] += (" This CAT also has Agile HMI blocks: elements may bind to their paths "
                                  "(e.g. 'Equipment.I') instead of IThis variables; mixing styles gives STY-01.")
        return draft

    @mcp.tool(annotations=READ_ONLY)
    def eae_search(text: str, limit: int = 50, solution: str | None = None) -> list[dict]:
        """Full-text search over type names, comments, variables and ST algorithm code."""
        return services.search(sol(solution), text, limit)

    @mcp.tool(annotations=READ_ONLY)
    def eae_folders(solution: str | None = None) -> dict:
        """Logical Solution Explorer folder tree per project and category, with members."""
        return services.folders_view(sol(solution))

    # -- CAT / system -----------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    def eae_cat_describe(name: str, solution: str | None = None) -> dict:
        """Everything about a CAT: interface, network, HMI interface (values to/from HMI),
        sub-CATs, .NET HMI and eHMI symbols with their bindings, and all files.
        name: the CAT type (e.g. 'catPump') or an application instance of it (e.g. 'PUMP1')."""
        s = sol(solution)
        return run(services.cat_describe, s, ws, name)

    @mcp.tool(annotations=READ_ONLY)
    def eae_system_describe(solution: str | None = None) -> list[dict]:
        """Systems: applications (layers and FB networks), devices, resources, mapping, OPC UA exposure."""
        return services.system_view(sol(solution))

    # -- HMI ------------------------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    def eae_hmi_list(technology: str | None = None, kind: str | None = None, query: str = "",
                     solution: str | None = None) -> list[dict]:
        """List .NET HMI ('hmi') and eHMI ('ehmi') documents.

        kind: canvas | resolution | symbol | faceplate | graphic.
        """
        s = sol(solution)
        return services.hmi_list(s, ws, technology, kind, query)

    @mcp.tool(annotations=READ_ONLY)
    def eae_hmi_describe(name: str, technology: str | None = None, solution: str | None = None) -> dict:
        """Describe a canvas, symbol or faceplate: objects, bindings (resolved to application
        instances for canvases), size, resolution and symbol mapping."""
        s = sol(solution)
        return run(services.hmi_describe, s, ws, name, technology)

    # -- understanding -------------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    def eae_explain(target: str, solution: str | None = None) -> dict:
        """Explain anything by name: a type, an application instance (e.g. 'PPC001'),
        an HMI/eHMI document or a device. Includes links to concept resources."""
        s = sol(solution)
        return run(services.explain, s, ws, target)

    @mcp.tool(annotations=READ_ONLY)
    def eae_trace(target: str, solution: str | None = None) -> dict:
        """Follow the chain HMI canvas → application instance → CAT → sub-CATs / inner FBs →
        algorithms. target: canvas name, instance name or type name."""
        s = sol(solution)
        return run(services.trace, s, ws, target)

    @mcp.tool(annotations=READ_ONLY)
    def eae_show_component_files(name: str, solution: str | None = None) -> dict:
        """Every file that makes up a component and its role (CATs span IEC61499, HMI and WEB)."""
        s = sol(solution)
        return run(services.component_files, s, name)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True,
                                          openWorldHint=False))
    def eae_doc_scaffold(name: str, save: bool = True, solution: str | None = None) -> dict:
        """Markdown documentation skeleton for a component, with [SCREENSHOT: …] placeholders for
        captures taken in EAE. With save=true (default) it is also written to
        <solution>/.eae-mcp/docs/<Type>.md (never into the EAE project). Show the user the
        `markdown` field verbatim and tell them the `saved_to` path."""
        s = sol(solution)
        return run(services.doc_scaffold_saved, s, ws, name, save)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True,
                                          openWorldHint=False))
    def eae_catalog_build(store: str | None = None, output: str | None = None,
                          solution: str | None = None) -> dict:
        """Index system-library types (E_CYCLE, E_DELAY, Soft_dPAC, …) from the EAE library store
        (default C:/ProgramData/Schneider Electric/Libraries) for the versions the solution references,
        and save the catalog as JSON (default <solution>/.eae-mcp/catalog.json) for use on machines
        without EAE. Does not modify the solution."""
        s = sol(solution)
        return run(services.catalog_build, ws, s, store, output)

    from .server_write import register_write_tools

    register_write_tools(mcp, ws, run, sol)

    # -- resources --------------------------------------------------------------------------

    @mcp.resource("eae://concepts", mime_type="text/markdown")
    def concepts_index() -> str:
        """Index of EAE concept documents."""
        return "# EAE concepts\n\n" + "\n".join(f"- eae://concepts/{n}" for n in services.concept_names())

    @mcp.resource("eae://concepts/{name}", mime_type="text/markdown")
    def concept(name: str) -> str:
        """An EAE concept document (overview, adapter, datatype, basic-fb, composite-fb, subapp,
        function, cat, system, hmi-dotnet, ehmi, folders, library)."""
        return run(services.concept, name)

    @mcp.resource("eae://solution/summary", mime_type="application/json")
    def solution_summary() -> str:
        """Summary of the current solution."""
        return _json(services.solution_summary(sol(None)))

    @mcp.resource("eae://type/{name}", mime_type="application/json")
    def type_resource(name: str) -> str:
        """Normalized JSON definition of a type in the current solution."""
        s = sol(None)
        return _json(services.type_view(s, run(services.find_type, s, name)))

    # -- prompts -----------------------------------------------------------------------------

    @mcp.prompt()
    def learn_component(name: str) -> str:
        """Teach the concept behind a component using the open solution as the example."""
        return (
            f"Explain the EAE component '{name}' to an automation engineer.\n"
            "1. Call eae_explain and read the concept resource it links to.\n"
            "2. If it is a CAT, also call eae_cat_describe and eae_show_component_files.\n"
            "3. Explain: purpose, interface (events and the data they carry), internal behaviour "
            "(ECC/algorithms or network), how it is used in the application and shown on HMI/eHMI.\n"
            "4. Finish with the IEC 61499 concept it illustrates and one practical tip."
        )

    @mcp.prompt()
    def review_application(application: str = "APP1") -> str:
        """Review an application network for problems."""
        return (
            f"Review the EAE application '{application}'. Use eae_system_describe and eae_get on the "
            "types involved. Look for unconnected event inputs, data inputs without a source or "
            "parameter, unmapped instances, instances not shown on any HMI, and unresolved connections. "
            "Report findings as a prioritized list with the instance names."
        )

    @mcp.prompt()
    def design_basic_fb(description: str) -> str:
        """Design a Basic FB (interface + ECC + ST) from a functional description."""
        return (
            "Design an EAE 26 Basic FB for this requirement:\n"
            f"{description}\n\n"
            "Read eae://concepts/basic-fb first. Produce: interface (events with WITH, typed vars), "
            "internal vars, ECC (states, transitions with conditions, actions), and ST algorithms. "
            "Follow the template convention INIT/INITO + REQ/CNF with QI/QO. Avoid reserved words as "
            "identifiers (e.g. ON)."
        )

    @mcp.prompt()
    def design_cat(description: str) -> str:
        """Design a CAT (logic + HMI interface + symbols) from a description."""
        return (
            "Design an EAE 26 CAT for this equipment:\n"
            f"{description}\n\n"
            "Read eae://concepts/cat. Use an existing CAT as reference (eae_list kind=cat, then "
            "eae_cat_describe). Produce: CAT interface, inner network (Basic FB for logic + IThis HMI "
            "interface), the HMI interface variables and events, and the content of a default .NET "
            "symbol and an eHMI symbol (widgets and their tag bindings)."
        )

    @mcp.prompt()
    def design_hmi_from_description(description: str) -> str:
        """Turn a plain-language description into situation-awareness HMI (symbols + displays), both HMIs."""
        return (
            "Design the EAE HMI described below, following ISA-101 / High Performance HMI.\n\n"
            f"Description:\n{description}\n\n"
            "Steps (dry run first; show the user the plan before writing):\n"
            "1. eae_knowledge 'from description to HMI' and 'high performance principles'.\n"
            "2. List the equipment, signals and operator actions in the description. Map each equipment to a CAT "
            "(eae_list kind=cat / eae_library_guide) or create one: basic style with eae_cat_create (IThis carries "
            "the signals as inputs and the setpoints/commands as output events; add missing ones with "
            "eae_fb_update_interface on <Cat>_HMI), or Agile style when the solution uses SE.Agile "
            "(eae_agile_cat_create: one HMI_Indication/HMI_Control block per signal; eae_agile_signal_add later).\n"
            "3. For each CAT: eae_hmi_design_suggest, then complete units, spans, normal ranges, alarm limits, "
            "state texts and priorities from the description. If a value is not given, ASK - never invent limits.\n"
            "4. eae_hmi_symbol_build (technology both) for each CAT with what levels 1-2 need; put details, "
            "setpoints and commands (confirm=true for critical actions) on a faceplate with "
            "eae_hmi_faceplate_build (.NET; the symbol opens it).\n"
            "5. Make sure the instances exist and are mapped (eae_net_add_fb, eae_map_to_resource).\n"
            "6. Displays: a level-1 overview if there are several areas, then one level-2 display per area "
            "(eae_hmi_display_build, once for hmi and once for ehmi with the device).\n"
            "7. eae_hmi_review on every new display; fix warnings; report the open manual checks "
            "(trends, navigation) to the user; for alarm words also run eae_hmi_scripts (ALM-06…10)."
        )

    @mcp.prompt()
    def design_hmi_sa(area: str, level: int = 2) -> str:
        """Design or improve an HMI display for situation awareness (ISA-101 / High Performance HMI)."""
        return (
            f"Design an EAE HMI display for: {area} (ISA-101 level {level}).\n"
            "1. Read eae_knowledge 'situation awareness hierarchy' and 'high performance principles'.\n"
            "2. Find the instances to show (eae_system_describe) and their CATs' symbols (eae_cat_describe).\n"
            "3. Propose the layout: what supports SA level 1 (abnormal at a glance), 2 (values versus normal "
            "range, analog indicators) and 3 (trends); which details go to faceplates (level 4).\n"
            "4. Create/extend canvases and place symbols with the eae_hmi_* / eae_ehmi_* tools (dry run first).\n"
            "5. Run eae_hmi_review with this level, fix warnings, and list the manual checks still open."
        )

    return mcp
