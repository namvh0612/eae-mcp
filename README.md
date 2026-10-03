# eae-mcp

An MCP (Model Context Protocol) server that lets an AI assistant **understand and edit EcoStruxure Automation
Expert (EAE) 26 solutions** (IEC 61499): Adapters, DataTypes, Basic/Composite FBs, SubApps, Functions, CATs, the
System (applications, devices, resources, mapping, OPC UA), .NET HMI and eHMI.

It reads and writes the solution files directly, without an EAE API, the same way EAE writes them. EAE does not
need to be running.

- Step-by-step setup and first use for people: [TUTORIAL.md](TUTORIAL.md)
- Version history: [CHANGELOG.md](CHANGELOG.md)

## What it can do

| Area | Read / explain | Create / edit |
|---|---|---|
| Types | list, search, explain any type; usages; ST code; ECC | Adapter, DataType, Basic FB (interface, ECC, ST), Composite FB, Function |
| Networks | instances, connections, parameters | add/remove FBs, connect/disconnect, parameters, generic FBs |
| CATs | interface, network, HMI interface (IThis), sub-CATs, symbols, files | new CAT, extra symbols/faceplates, Agile-style CATs (SE.Agile HMI blocks) |
| System | applications, devices, resources, mapping, OPC UA | map/unmap to resources, expose variables on OPC UA, SubApps |
| .NET HMI / eHMI | canvases, symbols, faceplates, bindings, styles | canvases, place/move/remove CAT symbols, generated situation-awareness symbols, faceplates and displays |
| HMI quality | review against ISA-101 / High Performance HMI / ISA-18.2, binding checks, alarm profiles | add alarm texts |
| Integration | REST API probe | generated REST/HTTP client CAT |
| Knowledge | EAE concepts, standard library, HMI design practice | — |

## Install (Windows, on the EAE PC)

Python 3.11 or newer is required.

```powershell
git clone <repository-url> C:\Tools\eae-mcp
cd C:\Tools\eae-mcp
py -m venv .venv
.venv\Scripts\pip install -e .
Copy-Item eae-mcp.example.toml eae-mcp.toml
```

## Configure (`eae-mcp.toml`)

Every key is optional.

```toml
[project]
roots = ["C:/EAE"]           # folders with EAE solutions the server may open
allow_write = false          # true lets write tools change files (they still dry-run unless asked)

[eae]
library_store = "C:/ProgramData/Schneider Electric/Libraries"   # EAE system libraries (default)
# catalog_file = "C:/EAE/.eae-mcp/catalog.json"                 # for machines without EAE

[server]
transport = "stdio"          # or "http"
http_bind = "127.0.0.1:8765"

[http_probe]                 # optional: lets eae_http_probe call these REST hosts (GET/HEAD only)
enabled = false
allowed_hosts = []
```

Environment variables override the file: `EAE_MCP_CONFIG` (config path), `EAE_MCP_ROOTS` (paths separated by
`;`), `EAE_MCP_ALLOW_WRITE=1`, `EAE_MCP_LIBRARY_STORE`, `EAE_MCP_CATALOG`, `EAE_MCP_TOKEN` (HTTP bearer token).

Command line: `eae-mcp [--config FILE] [--transport stdio|http] [--host HOST] [--port PORT]`.

## Connect an MCP client

**Claude Desktop** (`claude_desktop_config.json`) or any stdio client:

```json
{
  "mcpServers": {
    "eae": {
      "command": "C:\\Tools\\eae-mcp\\.venv\\Scripts\\eae-mcp.exe",
      "args": ["--config", "C:\\Tools\\eae-mcp\\eae-mcp.toml"]
    }
  }
}
```

**Claude Code:** `claude mcp add eae -- C:\Tools\eae-mcp\.venv\Scripts\eae-mcp.exe --config C:\Tools\eae-mcp\eae-mcp.toml`

**VS Code** (`.vscode/mcp.json`):

```json
{
  "servers": {
    "eae": {
      "type": "stdio",
      "command": "C:\\Tools\\eae-mcp\\.venv\\Scripts\\eae-mcp.exe",
      "args": ["--config", "C:\\Tools\\eae-mcp\\eae-mcp.toml"]
    }
  }
}
```

**Remote (HTTP):**

```powershell
$env:EAE_MCP_TOKEN = "<long random secret>"
C:\Tools\eae-mcp\.venv\Scripts\eae-mcp.exe --config C:\Tools\eae-mcp\eae-mcp.toml --transport http --host 0.0.0.0 --port 8765
```

Clients connect to `http://<host>:8765/mcp` with the header `Authorization: Bearer <token>`. The server refuses
to listen on a non-loopback address without a token.

**Machines without EAE:** run `eae_catalog_build` once on the EAE PC. It writes `<solution>/.eae-mcp/catalog.json`
with the system-library types the solution uses. Copy it with the solution and set `catalog_file`.

## Guide for AI assistants

Conventions:
- Every tool takes an optional `solution` (solution name or folder). Open one first with `eae_open_solution`;
  later calls use it by default. Only solutions under `roots` can be opened.
- Types, instances and pins are addressed **by name**; EAE IDs are resolved and generated internally.
- **Write tools default to `dry_run=true`** and return a unified diff plus warnings. Show the diff to the user and
  call again with `dry_run=false` only after they agree. Writing also needs `allow_write = true`.
- Run `eae_validate` after changes. Engineering data (ranges, alarm limits, setpoints) must come from the user or
  the description: ask instead of inventing them.

Typical workflows:

| Goal | Tools in order |
|---|---|
| Understand a solution | `eae_open_solution` → `eae_summary` → `eae_explain` / `eae_trace` / `eae_cat_describe` |
| Find something to reuse | `eae_library_guide` → `eae_generic_fbs` → `eae_get` |
| New logic block | `eae_basic_create` (or `eae_function_create`) → `eae_validate` |
| New equipment (CAT) | `eae_cat_create` (basic style) or `eae_agile_cat_create` (SE.Agile style) → `eae_net_add_fb` into the application → `eae_map_to_resource` |
| HMI from a description | prompt `design_hmi_from_description`, or `eae_hmi_design_suggest` → `eae_hmi_symbol_build` → `eae_hmi_faceplate_build` → `eae_hmi_display_build` → `eae_hmi_review` |
| Review an HMI | `eae_hmi_review` (+ `eae_hmi_scripts` for alarm-word profiles) |
| Call a REST API from EAE | `eae_http_probe` → `eae_rest_client_create` |

Background knowledge: read the resource `eae://concepts/<name>` or call `eae_knowledge "<topic>"`.

### Read tools

| Tool | Purpose |
|---|---|
| `eae_list_solutions`, `eae_open_solution`, `eae_summary` | Find, index and summarize solutions |
| `eae_list`, `eae_get`, `eae_search`, `eae_find_usages`, `eae_folders` | Browse types, ST code, usages and logical folders |
| `eae_explain` | Explain any type, application instance, HMI document or device |
| `eae_trace` | HMI canvas → instance → CAT → sub-CATs/FBs → algorithms |
| `eae_cat_describe` | A CAT's interface, network, HMI interface, sub-CATs, symbols and files |
| `eae_system_describe` | Applications, devices, resources, mapping, OPC UA exposure |
| `eae_hmi_list`, `eae_hmi_describe` | .NET HMI and eHMI canvases, symbols, faceplates and bindings |
| `eae_show_component_files` | Every file of a component and its role |
| `eae_validate` | Static checks: identifiers, reserved words, WITH, ECC, connections, registration |
| `eae_library_guide` | Ranked drill-down of libraries: folders → type families → signatures, usage counts |
| `eae_generic_fbs` | Generic FB types (e.g. NETIO, VALFORMAT): parameters and pins |
| `eae_knowledge` | Search the built-in knowledge; returns only the matching sections |
| `eae_hmi_review` | Situation-awareness / High Performance HMI review of canvases, symbols, navigation, alarm classes; tells basic and Agile HMI styles apart and checks every binding |
| `eae_hmi_scripts` | HMI support classes (`*.spt.cs`) and alarm-word profiles, cross-checked with the bits the logic sets |
| `eae_hmi_design_suggest` | Draft symbol design for a CAT and the engineering data still missing |
| `eae_http_probe` | Try a REST API (GET/HEAD, only hosts allowed in `[http_probe]`) and list its JSON fields |

### Tools that write files

`eae_doc_scaffold` and `eae_catalog_build` write only into `<solution>/.eae-mcp/`. All others change the
solution, default to `dry_run=true` and need `allow_write`.

| Tool | Purpose |
|---|---|
| `eae_doc_scaffold` | Markdown documentation skeleton for a component |
| `eae_catalog_build` | Index system-library types for use without EAE |
| `eae_adapter_create` | New Adapter |
| `eae_datatype_create`, `eae_datatype_update` | New/changed DataType: struct, enum, array, subrange |
| `eae_basic_create` | New Basic FB: interface (incl. plugs/sockets), internal vars, ECC, ST algorithms |
| `eae_fb_update_interface` | Add/remove events and variables, change WITH; IDs and wiring are kept |
| `eae_basic_upsert_algorithm`, `eae_basic_update_ecc` | Add/replace ST algorithms; add/remove states and transitions |
| `eae_function_create`, `eae_function_update` | Functions (POU): inputs/outputs/VAR_IN_OUT, temp vars, ST |
| `eae_composite_create`, `eae_subapp_create` | New Composite FB / SubApp |
| `eae_net_add_fb`, `eae_net_remove_fb` | Add/remove instances (incl. generic FBs) in a Composite, CAT, SubApp or application |
| `eae_net_connect`, `eae_net_disconnect`, `eae_net_set_param` | Event/data/adapter connections, instance parameters |
| `eae_map_to_resource`, `eae_unmap` | Map application instances to `Device/Resource` |
| `eae_opcua_expose` | Expose/unexpose a variable on OPC UA |
| `eae_cat_create`, `eae_cat_add_symbol` | New CAT (IThis, .NET + eHMI symbol, generated code); add symbols/faceplates |
| `eae_agile_cat_create`, `eae_agile_signal_add` | Agile-style CAT: logic Basic FB with HMI adapters, one `HMI_Indication_*`/`HMI_Control_*` block per signal; add signals later |
| `eae_rest_client_create` | REST/HTTP client CAT: request/response FBs, TLS socket, JSON field extraction, HMI |
| `eae_hmi_canvas_create`, `eae_hmi_place_symbol`, `eae_hmi_update_object`, `eae_hmi_remove_object` | .NET HMI canvases and objects |
| `eae_ehmi_canvas_create`, `eae_ehmi_place_symbol`, `eae_ehmi_update_object`, `eae_ehmi_remove_object` | eHMI canvases and objects |
| `eae_hmi_symbol_build` | Situation-awareness CAT symbol (.NET + eHMI): values with analog indicators, states, alarm indicators; setpoints and command buttons on .NET |
| `eae_hmi_faceplate_build` | Detail faceplate (.NET) opened from the CAT's symbol |
| `eae_hmi_display_build` | ISA-101 display: canvas, title, framed sections, CAT symbols in a grid |
| `eae_alarm_profile_add` | Add alarm texts to an alarm-word profile |

### Resources and prompts

- Resources: `eae://concepts` (index), `eae://concepts/{name}` (overview, adapter, datatype, basic-fb,
  composite-fb, subapp, function, cat, system, hmi-dotnet, ehmi, folders, library, standard-library, rest-client,
  hmi-design), `eae://solution/summary`, `eae://type/{name}`.
- Prompts: `learn_component(name)`, `review_application(application)`, `design_basic_fb(description)`,
  `design_cat(description)`, `design_hmi_from_description(description)`, `design_hmi_sa(area, level)`.

## Safety

- Read-only unless `allow_write = true`; write tools still dry-run unless called with `dry_run=false`.
- Only paths inside `roots` are opened. `bin/`, `obj/` and `SnapshotCompiles/` are ignored.
- Security material is never read or returned: `General/Security`, certificates, `se-rbac-*.json`, `*.db`.
- REST tokens are runtime inputs of the generated CAT; they are never written to files or returned.
- Every write backs up the modified files to `<solution>/.eae-mcp/backup/<timestamp>/`, appends to
  `<solution>/.eae-mcp/audit.jsonl`, writes atomically and refuses if a file changed on disk after it was read.
- .NET HMI edits only touch `InitializeComponent()` of `*.cnv.Designer.cs`; files that cannot be modelled are
  refused, never rewritten.

## Limits

- Close the edited types in EAE (or keep no unsaved changes) while writing; EAE reloads changed files. Run
  **Tools › Check Changes** in EAE afterwards.
- Commands (setpoints/buttons) and faceplates are generated for the .NET HMI only; draw them in EAE for eHMI.
- Embedded trends are not generated.
