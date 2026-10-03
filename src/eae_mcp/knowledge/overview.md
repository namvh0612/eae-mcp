# EcoStruxure Automation Expert (EAE) 26 — Overview

EAE is Schneider Electric's IEC 61499 automation platform. **Buildtime** is the Windows IDE; **Runtime** (dPAC: Soft dPAC, Modicon M262/M251/M580 dPAC, industrial PCs) executes the application.

## How a solution is organized

| Layer | What it is | Where it lives |
|---|---|---|
| **Solution** | `.sln` + `.nxtsln`; a set of projects | solution root |
| **Library** | A set of projects with the same prefix (`SE.Agile`, `SE.Agile.HMI`, `SE.Agile.WEB`, …) | sub-folder |
| **IEC61499 project** (`.dfbproj`) | Registry of every type, the System, folders | `IEC61499/` |
| **HMI project** (`.csproj`) | .NET HMI: canvases, CAT symbols, faceplates (C#) | `HMI/` |
| **WEB project** (`.htmlproj`) | eHMI: web canvases and symbols (JSON + TypeScript) | `WEB/` |
| **System libraries** | Runtime.Base, SE.DPAC, SE.Standard, … referenced by name + version | `C:\ProgramData\Schneider Electric\Libraries\<Lib>-<Ver>` |

## The building blocks (from smallest to largest)

1. **DataType** — struct, enum, array, subrange. → `eae://concepts/datatype`
2. **Adapter** — a bundle of events + data that travels over one connection. → `eae://concepts/adapter`
3. **Function** — IEC 61131-3 function (ST), no state. → `eae://concepts/function`
4. **Basic FB** — event-driven state machine (ECC) running ST algorithms. → `eae://concepts/basic-fb`
5. **Composite FB** — a network of FB instances behind one interface. → `eae://concepts/composite-fb`
6. **CAT** (Composite Automation Type) — a composite FB packaged with its HMI symbols, faceplates, OPC UA and offline configuration. → `eae://concepts/cat`
7. **SubApp** — groups part of an application; packaging, not a reusable type. → `eae://concepts/subapp`
8. **System** — Applications (what runs) + Devices/Resources (where it runs) + mapping. → `eae://concepts/system`
9. **.NET HMI** and **eHMI** — operator screens bound to CAT instances. → `eae://concepts/hmi-dotnet`, `eae://concepts/ehmi`

## Key rules that hold everywhere

- **Everything is event-driven (IEC 61499).** Data moves when an event carries it (`With` associations).
- **References are by ID.** Files store `$<FBID>.<PinID>`; names are resolved through the type definitions. Library pins (no IDs) are referenced by name.
- **Types are registered.** A type exists for EAE only if its project file lists it.
- **Folders are logical.** Solution Explorer folders never create directories. → `eae://concepts/folders`
