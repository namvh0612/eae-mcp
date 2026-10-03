# eae-mcp Tutorial

One successful run, from installation to a first change in an EAE solution. Every command is ready to copy.
The guide uses these folders; change them only if yours differ:

| What | Path in this guide |
|---|---|
| eae-mcp program | `C:\Tools\eae-mcp` |
| Folder with your EAE solutions | `C:\EAE` |
| Practice copy of a solution | `C:\EAE\MyPlant_Copy` (contains `MyPlant.sln`) |

---

## Step 1 — Install Python (once)

Open **PowerShell** (Start › type `PowerShell` › Enter) and run:

```powershell
winget install Python.Python.3.12
```

Close and reopen PowerShell, then check: `py --version` must show `Python 3.11` or newer.

## Step 2 — Install eae-mcp (once)

**With git** (recommended):

```powershell
git clone https://github.com/namvh0612/eae-mcp.git C:\Tools\eae-mcp
cd C:\Tools\eae-mcp
py -m venv .venv
.venv\Scripts\pip install -e .
Copy-Item eae-mcp.example.toml eae-mcp.toml
```

**Without git:** on the GitHub page click **Code › Download ZIP**. The ZIP contains one folder `eae-mcp-main`.
Unzip so that the files end up directly in `C:\Tools\eae-mcp` (you must see `C:\Tools\eae-mcp\pyproject.toml`, not
`C:\Tools\eae-mcp\eae-mcp-main\pyproject.toml`). Then run the commands above from `cd C:\Tools\eae-mcp` on.

**Check the installation** before going further:

```powershell
cd C:\Tools\eae-mcp
.venv\Scripts\python -c "import eae_mcp.server; print('eae-mcp OK')"
Test-Path .venv\Scripts\eae-mcp.exe
```

You must see `eae-mcp OK` and `True`.

## Step 3 — Prepare a practice copy of a solution

For the first run, work on a **copy**, not on your real project:

1. Close EAE.
2. Copy the whole solution folder (the folder with the `.sln` file and its sub-folders such as `IEC61499`, `HMI`,
   `WEB`, `General`, `Topology`) to `C:\EAE\MyPlant_Copy`. If the solution is a ZIP/archive, extract it first.
3. Check: `C:\EAE\MyPlant_Copy\MyPlant.sln` exists. The `.sln` must be at most 3 folders below `C:\EAE`.

## Step 4 — Configure eae-mcp

Open `C:\Tools\eae-mcp\eae-mcp.toml` in Notepad and set:

```toml
[project]
roots = ["C:/EAE"]
allow_write = false
```

- `roots`: the folder(s) holding your solutions, with forward slashes `/`. Always set it: without roots, the
  server does not restrict paths.
- `allow_write = false`: tools that change solutions are blocked. (Two helper tools still write their own output
  under `.eae-mcp`: a documentation skeleton and a library catalog.)

## Step 5 — Connect your AI app

Add an entry named `eae`. **If the file already lists other MCP servers, keep them** and add `eae` next to them.

**Claude Desktop:** **Settings › Developer › Edit Config** opens `claude_desktop_config.json`.

If the file is empty or has no `mcpServers`, use:

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

If `mcpServers` already exists, add only this block inside it (with a comma after the previous entry):

```json
    "eae": {
      "command": "C:\\Tools\\eae-mcp\\.venv\\Scripts\\eae-mcp.exe",
      "args": ["--config", "C:\\Tools\\eae-mcp\\eae-mcp.toml"]
    }
```

Save, then quit Claude Desktop completely (also from the tray icon) and start it again.

**Claude Code:**

```powershell
claude mcp add eae -- C:\Tools\eae-mcp\.venv\Scripts\eae-mcp.exe --config C:\Tools\eae-mcp\eae-mcp.toml
```

**VS Code:** in your workspace, `.vscode\mcp.json` (add the `eae` entry inside `"servers"` if the file exists):

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

Note: if the `--config` path is wrong, the server still starts but ignores your settings (empty solution list,
read-only). Copy the paths exactly.

## Step 6 — First questions (read-only)

Ask the assistant:

> List my EAE solutions.

You should see `MyPlant` (from `C:\EAE\MyPlant_Copy`). Then:

> Open MyPlant_Copy and give me a summary.

> Explain the CAT `<one of your CATs>`: its interface, what is inside, and its symbols.

> Which devices and resources exist, and what is mapped where?

> Which standard library blocks could I use for a cyclic timer?

## Step 7 — First change on the practice copy

1. In `eae-mcp.toml` set `allow_write = true`, save, and **restart the AI app** (settings are read at start-up).
2. Keep EAE closed (or make sure the edited types have no unsaved changes).
3. Ask, giving the full interface so the assistant does not have to guess it:

   > In MyPlant_Copy, create a Basic FB `fbLevelAlarm`:
   > - input event `REQ` WITH input `Level : REAL`
   > - output event `CNF` WITH output `High : BOOL`
   > - on every `REQ`: `High := Level > 90.0;` then emit `CNF`.
   > Show me the diff first.

   This is how IEC 61499 works: data (`Level`) is read only when its event (`REQ`) arrives, and results (`High`)
   are sent with an output event (`CNF`).

4. The assistant shows a **preview (diff)**. Nothing is written yet. Read it.
5. Say **"OK, write it"**. Then ask:

   > Run eae_validate on fbLevelAlarm.

6. Open the solution in EAE: **Tools › Check Changes**, open `fbLevelAlarm`, build, and try it (for example place
   an instance in an application, set `Level` to 95 and send `REQ`: `High` must become TRUE).

Tip: the server makes "preview first" the default, but an assistant could skip it. Saying *"show me the diff
first"* keeps you in control.

## Step 8 — Undo a change (if needed)

Every write keeps the previous version of each modified file in
`C:\EAE\MyPlant_Copy\.eae-mcp\backup\<date-time>\` and a list of all touched files in
`C:\EAE\MyPlant_Copy\.eae-mcp\audit.jsonl`.

1. Close EAE.
2. Copy the files from the backup folder back into the solution (same sub-folders), overwriting.
3. Files that the change **created** (for example `fbLevelAlarm.fbt`, `.doc.xml`, `.meta.xml`) are not in the
   backup: delete them by hand. The restored `IEC61499.dfbproj` no longer lists them.
4. Open EAE and run **Tools › Check Changes**.

For the practice copy, simply deleting `C:\EAE\MyPlant_Copy` and copying the solution again is also fine.

## Step 9 — Design an HMI from a description

Give the numbers; the assistant asks for missing ones instead of guessing:

> For catPump in MyPlant_Copy: Value is the flow, 0–100 m3/h, normal 30–70, low alarm 10, high alarm 90
> (high priority). Add a Start button with confirmation and a faceplate with the details. Show the diffs first.

You get a symbol in the high-performance style (gray graphics, color only when something is abnormal), a detail
faceplate (.NET HMI) and a display, reviewed automatically. Build the HMI in EAE and check it in the runtime.

---

## Optional: use eae-mcp from another PC (HTTP)

**On the EAE PC** (server):

```powershell
$env:EAE_MCP_TOKEN = "choose-a-long-random-password"
C:\Tools\eae-mcp\.venv\Scripts\eae-mcp.exe --config C:\Tools\eae-mcp\eae-mcp.toml --transport http --host 0.0.0.0 --port 8765
```

Keep this window open: it is the server. Allow port 8765 in the Windows firewall for the trusted network only.

**On the other PC** (client), for example Claude Code:

```powershell
claude mcp add --transport http eae http://<EAE-PC-name>:8765/mcp --header "Authorization: Bearer choose-a-long-random-password"
```

Important:
- Plain HTTP is **not encrypted**: the password and the project data can be read on the network. Use it only on a
  trusted network, or through a VPN/SSH tunnel or an HTTPS reverse proxy.
- After changing `eae-mcp.toml`, restart the **server window** (Ctrl+C, run the command again), not the client app.

## Optional: use eae-mcp on a PC without EAE

1. On the EAE PC, ask: *"Build the library catalog for MyPlant_Copy."* This creates
   `C:\EAE\MyPlant_Copy\.eae-mcp\catalog.json`.
2. On the other PC, install eae-mcp (Steps 1–2) and connect the AI app (Step 5).
3. Copy the solution folder **with** `.eae-mcp`, for example to `D:\EAE\MyPlant_Copy`.
4. In `eae-mcp.toml` on that PC, set both:
   ```toml
   [project]
   roots = ["D:/EAE"]

   [eae]
   catalog_file = "D:/EAE/MyPlant_Copy/.eae-mcp/catalog.json"
   ```
5. Restart the AI app.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `py` is not recognized | Reinstall Python with "Add python.exe to PATH", reopen PowerShell |
| `pip install` fails | Check internet/proxy access; run it inside `C:\Tools\eae-mcp` (where `pyproject.toml` is) |
| The import check does not print `eae-mcp OK` | Reinstall: `.venv\Scripts\pip install -e .` and read the error |
| The assistant shows no `eae_…` tools | Check the paths in the app config, save, quit the app completely and restart |
| The solution list is empty | `--config` path correct? `roots` correct (forward slashes)? `.sln` at most 3 folders deep? |
| "… is outside the configured project roots" | Add the solution's folder to `roots`, restart the app |
| "Writing is disabled" | `allow_write = true`, restart the app |
| "File changed on disk since it was read" | Save or close the type in EAE, then ask again |
| Library blocks (E_CYCLE, …) are unknown | Install EAE on this PC, or use a catalog (see above) |
| HTTP: `401 unauthorized` | The header must be exactly `Authorization: Bearer <same password as EAE_MCP_TOKEN>` |

Server messages: Claude Desktop keeps them in `%APPDATA%\Claude\logs\mcp-server-eae.log`; for HTTP they appear in
the server window.

## Good to know

- eae-mcp edits files; it does not compile or deploy. Always build and test in EAE.
- The HMI review gives recommendations based on HMI standards; it is not a certification.
- Files of security folders, user/role files (`se-rbac-*.json`), databases and certificates are filtered out.
  Still, do not keep passwords or keys inside solution folders.
- Commands, setpoints and faceplates are generated for the .NET HMI; for eHMI, draw them in EAE.
