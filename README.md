# eae-mcp

An MCP (Model Context Protocol) server that lets an AI assistant **read, explain and edit EcoStruxure Automation
Expert (EAE) solutions** (IEC 61499): Adapters, DataTypes, Basic/Composite FBs, SubApps, Functions, CATs, the
System (applications, devices, resources, mapping, OPC UA), .NET HMI and eHMI.

It works on the solution files directly, without an EAE API; EAE does not need to be running.

- First successful use, step by step: [TUTORIAL.md](TUTORIAL.md)
- Version history: [CHANGELOG.md](CHANGELOG.md)
- Repository and issue tracker: https://github.com/namvh0612/eae-mcp (report problems under **Issues**)

## Scope and verification status

| Item | Status |
|---|---|
| EAE | File formats of **EAE 26.0** (Buildtime). Other versions/builds are not verified. |
| OS | Windows (where EAE runs). The server itself is plain Python and also runs on Linux/macOS for read-only use. |
| Python | 3.11 or newer. |
| MCP clients | Any client that supports MCP over stdio or Streamable HTTP (e.g. Claude Desktop, Claude Code, VS Code). |
| Verification | Readers and writers are checked automatically against files written by EAE 26.0 (byte-identical round trip, generated code compiles against API stubs). Opening, building and running **every** generated artifact in EAE has not been verified yet: check results in EAE before relying on them. |
| Support | Community project, no warranty. A license has not been chosen yet; until one is added, ask the owner before reusing the code. |

## What it can do

| Area | Read / explain | Create / edit |
|---|---|---|
| Types | list, search, explain any type; usages; ST code; ECC | Adapter, DataType, Basic FB (interface, ECC, ST), Composite FB, Function |
| Networks | instances, connections, parameters | add/remove FBs, connect/disconnect, parameters, generic FBs |
| CATs | interface, network, HMI interface (IThis), sub-CATs, symbols, files | new CAT, extra symbols/faceplates, Agile-style CATs (SE.Agile HMI blocks) |
| System | applications, devices, resources, mapping, OPC UA | map/unmap to resources, expose variables on OPC UA, SubApps |
| .NET HMI / eHMI | canvases, symbols, faceplates, bindings, styles | canvases, place/move/remove CAT symbols, generated symbols, faceplates and displays |
| HMI quality | heuristic review based on ISA-101 / High Performance HMI / ISA-18.2, binding checks, alarm profiles | add alarm texts |
| Integration | REST API probe | generated REST/HTTP client CAT |
| Knowledge | EAE concepts, standard library, HMI design practice | — |

### What it does not do

- It does not compile, build, deploy or download to devices, and it does not connect to a running runtime.
- `eae_validate` is a static check of the files, not an EAE compile. Always build in EAE.
- `eae_hmi_review` applies heuristics; it does not certify compliance with ISA-101 or any other standard.
- Setpoints, command buttons and faceplates are generated for the .NET HMI only; embedded trends are not generated.
- Generated REST clients handle responses of at most 4,096 bytes.

## Install (Windows)

```powershell
git clone https://github.com/namvh0612/eae-mcp.git C:\Tools\eae-mcp
cd C:\Tools\eae-mcp
py -m venv .venv
.venv\Scripts\pip install -e .
Copy-Item eae-mcp.example.toml eae-mcp.toml
.venv\Scripts\python -c "import eae_mcp.server; print('eae-mcp OK')"
```

The commands must run in the folder that contains `pyproject.toml`.

## Configure

The configuration file is read **only** from `--config <file>` or the environment variable `EAE_MCP_CONFIG`.
There is no automatic lookup of `eae-mcp.toml` in the current folder. **A path that does not exist is silently
ignored** and the defaults are used (no roots, read-only), so double-check the path in the client configuration.

```toml
[project]
roots = ["C:/EAE"]           # folders with EAE solutions the server may open
allow_write = false          # true enables the tools that change solutions

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

- **Always set `roots`.** With an empty list, paths are not restricted.
- Environment variables override the file: `EAE_MCP_ROOTS` (paths separated by `;`), `EAE_MCP_ALLOW_WRITE=1`,
  `EAE_MCP_LIBRARY_STORE`, `EAE_MCP_CATALOG`, `EAE_MCP_TOKEN` (HTTP bearer token).
- Command line: `eae-mcp [--config FILE] [--transport stdio|http] [--host HOST] [--port PORT]`.
- Configuration is read at start-up: after changing it, restart the server (for stdio clients: restart the app).

## Connect an MCP client

Add an `eae` entry to the client's MCP server list. **Keep the other servers already listed.**

Claude Desktop (`claude_desktop_config.json`, inside `"mcpServers"`):

```json
"eae": {
  "command": "C:\\Tools\\eae-mcp\\.venv\\Scripts\\eae-mcp.exe",
  "args": ["--config", "C:\\Tools\\eae-mcp\\eae-mcp.toml"]
}
```

Claude Code: `claude mcp add eae -- C:\Tools\eae-mcp\.venv\Scripts\eae-mcp.exe --config C:\Tools\eae-mcp\eae-mcp.toml`

VS Code (`.vscode\mcp.json`, inside `"servers"`):

```json
"eae": {
  "type": "stdio",
  "command": "C:\\Tools\\eae-mcp\\.venv\\Scripts\\eae-mcp.exe",
  "args": ["--config", "C:\\Tools\\eae-mcp\\eae-mcp.toml"]
}
```

### Remote access (HTTP)

```powershell
$env:EAE_MCP_TOKEN = "<long random secret>"
C:\Tools\eae-mcp\.venv\Scripts\eae-mcp.exe --config C:\Tools\eae-mcp\eae-mcp.toml --transport http --host 0.0.0.0 --port 8765
```

- Endpoint `http://<host>:8765/mcp`, header `Authorization: Bearer <token>`. Example for Claude Code on the other
  PC: `claude mcp add --transport http eae http://<host>:8765/mcp --header "Authorization: Bearer <token>"`.
- The server refuses to listen on a non-loopback address without a token.
- **The server speaks plain HTTP: the token and all data travel unencrypted.** Use it only on a trusted network, or
  put it behind HTTPS (a reverse proxy) or a tunnel (SSH, VPN).
- The HTTP server is a separate process: after a configuration change restart that process, not the client app.

### Machines without EAE

Run `eae_catalog_build` on the EAE PC. It writes the catalog of system-library types the solution uses (default
`<solution>/.eae-mcp/catalog.json`, or the `output`/`catalog_file` path). On the other machine install eae-mcp,
copy the solution and the catalog, and set both `roots` and `catalog_file` to the paths there.

## Safety model

| Mechanism | What it does | Limits |
|---|---|---|
| `allow_write` | Tools that change a solution refuse to write unless it is `true` | `eae_doc_scaffold` and `eae_catalog_build` write their own output files (`.eae-mcp/…` or a chosen path) regardless |
| `dry_run` | Tools that change a solution default to `dry_run=true` and return a diff | The server does not enforce a human review: the client may call `dry_run=false` directly. Ask your assistant to show diffs first |
| `roots` | Only paths inside `roots` are opened | No restriction when `roots` is empty |
| Security filter | Files under `Security`/`Certificates` folders, `se-rbac-*.json`, `*.db` and key/certificate files are refused | A filter on the paths the server reads, not a sandbox: keep secrets out of solution folders anyway |
| Backup and audit | Each write saves the previous version of modified files to `<solution>/.eae-mcp/backup/<timestamp>/` and logs to `<solution>/.eae-mcp/audit.jsonl` | Files are written one by one (each atomically); there is no automatic rollback of a whole change |
| Conflict check | A write is refused if a file changed on disk after it was read | — |
| REST tokens | Tokens are runtime inputs of generated CATs, never written to files or returned | — |

### Restoring after a write

1. Close the affected types in EAE.
2. Copy the files from `<solution>\.eae-mcp\backup\<timestamp>\` back over the solution (same relative paths).
3. Files that the change **created** are not in the backup: delete them, and remove their entries from the
   project files (`.dfbproj`, `HMI.csproj`, `WEB.htmlproj`) if the restored project files do not already drop them.
   `audit.jsonl` lists every file of each change.
4. In EAE run **Tools › Check Changes** and build.

## Update

```powershell
cd C:\Tools\eae-mcp
git pull
.venv\Scripts\pip install -e .
```

Close the client app (or stop the HTTP server) first, then start it again. `eae-mcp.toml` is not tracked by git and
is kept. To go back to a version that worked: `git log --oneline`, then `git checkout <commit>` and reinstall.

## Troubleshooting

| Symptom | Check |
|---|---|
| `pip install` fails | Python 3.11+ (`py --version`); network/proxy access to PyPI; run in the folder with `pyproject.toml` |
| The client shows no `eae_…` tools | Paths in the client entry; the import check of the install step; fully restart the client |
| Settings seem ignored (empty solution list, read-only) | `--config` path exists (a wrong path is silently ignored); `roots` uses `/` |
| Server does not start | Run the `command` from the client entry by hand in PowerShell and read the error |
| "Writing is disabled" | `allow_write = true`, then restart |
| "… outside the configured project roots" | Add the solution's folder to `roots` |
| "changed on disk since it was read" | Save/close the type in EAE, ask again |
| HTTP `401 unauthorized` | Header is exactly `Authorization: Bearer <token>` with the server's `EAE_MCP_TOKEN` |
| Library blocks unknown | `library_store` on the EAE PC, or `catalog_file` elsewhere |

Logs: the server writes diagnostics to its standard error. Clients keep it in their MCP logs (Claude Desktop:
`%APPDATA%\Claude\logs\mcp-server-eae.log`; VS Code: command **MCP: List Servers** › `eae` › **Show Output**).
For HTTP, read the server's console.

## Guide for AI assistants

- Every tool takes an optional `solution` (name or folder). Open one first with `eae_open_solution`; later calls use
  it by default.
- Types, instances and pins are addressed **by name**; EAE IDs are resolved and generated internally.
- Call change tools with the default `dry_run=true`, show the diff to the user, and use `dry_run=false` only after
  they agree. Then run `eae_validate` and ask the user to build in EAE.
- Engineering data (interfaces, ranges, alarm limits, setpoints) must come from the user: ask instead of inventing.
- Background: resource `eae://concepts/<name>` or `eae_knowledge "<topic>"`.

| Goal | Tools in order |
|---|---|
| Understand a solution | `eae_open_solution` → `eae_summary` → `eae_explain` / `eae_trace` / `eae_cat_describe` |
| Find something to reuse | `eae_library_guide` → `eae_generic_fbs` → `eae_get` |
| New logic block | `eae_basic_create` (or `eae_function_create`) → `eae_validate` |
| New equipment (CAT) | `eae_cat_create` or `eae_agile_cat_create` → `eae_net_add_fb` → `eae_map_to_resource` |
| HMI from a description | prompt `design_hmi_from_description`, or `eae_hmi_design_suggest` → `eae_hmi_symbol_build` → `eae_hmi_faceplate_build` → `eae_hmi_display_build` → `eae_hmi_review` |
| Review an HMI | `eae_hmi_review` (+ `eae_hmi_scripts` for alarm-word profiles) |
| Call a REST API from EAE | `eae_http_probe` → `eae_rest_client_create` |

## Tool reference

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
| `eae_hmi_review` | Heuristic HMI review of canvases, symbols, navigation, alarm classes; basic/Agile style and binding checks |
| `eae_hmi_scripts` | HMI support classes (`*.spt.cs`) and alarm-word profiles, cross-checked with the bits the logic sets |
| `eae_hmi_design_suggest` | Draft symbol design for a CAT and the engineering data still missing |
| `eae_http_probe` | Try a REST API (GET/HEAD, only hosts allowed in `[http_probe]`) and list its JSON fields |

### Tools that write files

`eae_doc_scaffold` and `eae_catalog_build` write only their own output files. All others change the solution,
default to `dry_run=true` and need `allow_write`.

| Tool | Purpose |
|---|---|
| `eae_doc_scaffold` | Markdown documentation skeleton for a component (`<solution>/.eae-mcp/docs/`) |
| `eae_catalog_build` | Catalog of system-library types for use without EAE |
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
| `eae_hmi_symbol_build` | CAT symbol (.NET + eHMI): values with analog indicators, states, alarm indicators; setpoints and buttons on .NET |
| `eae_hmi_faceplate_build` | Detail faceplate (.NET) opened from the CAT's symbol |
| `eae_hmi_display_build` | Display: canvas, title, framed sections, CAT symbols in a grid |
| `eae_alarm_profile_add` | Add alarm texts to an alarm-word profile |

### Resources and prompts

- Resources: `eae://concepts` (index), `eae://concepts/{name}` (overview, adapter, datatype, basic-fb,
  composite-fb, subapp, function, cat, system, hmi-dotnet, ehmi, folders, library, standard-library, rest-client,
  hmi-design), `eae://solution/summary`, `eae://type/{name}`.
- Prompts: `learn_component(name)`, `review_application(application)`, `design_basic_fb(description)`,
  `design_cat(description)`, `design_hmi_from_description(description)`, `design_hmi_sa(area, level)`.
