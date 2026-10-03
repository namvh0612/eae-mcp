# eae-mcp Tutorial

This guide gets you from zero to asking an AI assistant about your EAE solution, step by step.
Everything below is ready to copy. The examples use these folders; change them only if yours differ:

| What | Path used in this guide |
|---|---|
| eae-mcp program | `C:\Tools\eae-mcp` |
| Your EAE solutions | `C:\EAE` (for example `C:\EAE\MyPlant\MyPlant.sln`) |

---

## Step 1 — Install Python (once)

1. Open **PowerShell**.
2. Run:
   ```powershell
   winget install Python.Python.3.12
   ```
3. Close and reopen PowerShell, then check:
   ```powershell
   py --version
   ```
   You should see `Python 3.12.x` (3.11 or newer is fine).

## Step 2 — Install eae-mcp (once)

```powershell
git clone <repository-url> C:\Tools\eae-mcp
cd C:\Tools\eae-mcp
py -m venv .venv
.venv\Scripts\pip install -e .
Copy-Item eae-mcp.example.toml eae-mcp.toml
```

No git? Download the repository as a ZIP, unzip it to `C:\Tools\eae-mcp`, and run the commands from `cd` on.

## Step 3 — Tell it where your solutions are

Open `C:\Tools\eae-mcp\eae-mcp.toml` in Notepad. The important line is `roots`:

```toml
[project]
roots = ["C:/EAE"]
allow_write = false
```

- `roots`: the folder(s) that hold your EAE solutions. Use forward slashes `/`.
- `allow_write = false`: the assistant can only **read** at first. That is the safe way to start.

## Step 4 — Connect your AI app

Pick the app you use.

**Claude Desktop**: menu **Settings › Developer › Edit Config**, paste this, save, and restart Claude Desktop:

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

**Claude Code** (one command in PowerShell):

```powershell
claude mcp add eae -- C:\Tools\eae-mcp\.venv\Scripts\eae-mcp.exe --config C:\Tools\eae-mcp\eae-mcp.toml
```

**VS Code**: create `.vscode\mcp.json` in your workspace:

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

## Step 5 — Check that it works

Ask the assistant:

> List my EAE solutions.

It should answer with the solutions under `C:\EAE`. Then:

> Open MyPlant and give me a summary.

If you get an answer with projects, types and devices, you are done with the setup.

---

## Using it: things you can ask

### Understand a solution (read-only)

> Explain the CAT `catPump`: its interface, what is inside, and its symbols.

> Trace what happens when the operator looks at canvas `Overview`: which instances, CATs and algorithms are behind it.

> Which devices and resources exist, and what is mapped where?

> Where is `fbMotor` used?

> Which standard library blocks could I use for a cyclic timer and value formatting?

### Make changes safely

1. In `eae-mcp.toml`, set `allow_write = true`, save, and **restart your AI app**.
2. Close the type you want to change in EAE (or make sure it has no unsaved edits).
3. Ask for the change, for example:

   > Create a Basic FB `fbLevelAlarm` with input `Level : REAL`, output `High : BOOL`, set `High` when Level > 90.

4. The assistant first shows a **preview (diff)**. Nothing is written yet.
5. Say **"OK, write it"** to apply.
6. In EAE, run **Tools › Check Changes**.

Every write keeps a backup in `<your solution>\.eae-mcp\backup\` and a log in `<your solution>\.eae-mcp\audit.jsonl`.

More change examples:

> Add an instance `PUMP1` of `catPump` to application `APP1` and map it to `EcoRT_0/RES0`.

> Expose `PUMP1.IThis.Value` on OPC UA.

> Create a .NET HMI canvas `Pumps` and place `PUMP1` on it at x=40, y=40.

### Design an HMI from a description

Describe the equipment in plain words. Give the numbers (ranges, normal values, alarm limits); the assistant
will ask for any that are missing instead of guessing.

> Design the HMI for catPump: Value is the flow, 0–100 m3/h, normal 30–70, low alarm 10, high alarm 90 (high priority).
> Add a Start button with confirmation, and a faceplate with the details.

The assistant builds a symbol in the high-performance style (gray graphics, color only when something is abnormal),
a detail faceplate, and a display, then reviews them.

### Review an existing HMI

> Review all HMI canvases for situation awareness and list the problems by priority.

> Check the alarm texts: is every alarm bit the logic sets described?

---

## Optional: use it from another PC (HTTP)

On the EAE PC:

```powershell
$env:EAE_MCP_TOKEN = "choose-a-long-random-password"
C:\Tools\eae-mcp\.venv\Scripts\eae-mcp.exe --config C:\Tools\eae-mcp\eae-mcp.toml --transport http --host 0.0.0.0 --port 8765
```

On the other PC, connect the MCP client to `http://<EAE-PC-name>:8765/mcp` with the header
`Authorization: Bearer choose-a-long-random-password`. Allow port 8765 in the Windows firewall only for trusted
networks.

## Optional: use it on a PC without EAE

1. On the EAE PC, ask: *"Build the library catalog for MyPlant."* This creates `C:\EAE\MyPlant\.eae-mcp\catalog.json`.
2. Copy the whole solution folder (with `.eae-mcp`) to the other PC.
3. In `eae-mcp.toml` on that PC, set under `[eae]`:
   ```toml
   catalog_file = "D:/EAE/MyPlant/.eae-mcp/catalog.json"
   ```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `py` is not recognized | Reinstall Python and tick "Add python.exe to PATH", then reopen PowerShell |
| The assistant does not see any `eae_…` tools | Check the paths in the app config, save, and fully restart the app |
| The list of solutions is empty | Check `roots` in `eae-mcp.toml` (forward slashes, folder exists, the `.sln` is at most 3 folders deep), then restart the app |
| "… is outside the configured project roots" | Move the solution under a folder listed in `roots`, or add its folder to `roots` |
| "Writing is disabled" | Set `allow_write = true` and restart the app |
| "File changed on disk since it was read" | Save or close the type in EAE, then ask again |
| Library blocks (E_CYCLE, …) are unknown | Install EAE on this PC, or build and use a catalog (see above) |

## Good to know

- The assistant never reads security files (users, certificates, RBAC) of your solution.
- Writing is off until you turn it on, and every change is shown as a preview first.
- Commands and faceplates are generated for the .NET HMI; for eHMI, draw them in EAE.
