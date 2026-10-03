# Basic Function Block

An event-driven state machine. File `<Name>.fbt` with `<BasicFB>`, `IEC61499Type=Basic`.

## Parts
| Part | Meaning |
|---|---|
| Interface | Event inputs/outputs; input/output variables; `With` says which data is sampled/sent with an event |
| Internal variables | State kept between executions |
| **ECC** (Execution Control Chart) | States + transitions. A transition fires on an input event and/or a guard condition (`REQ`, `INIT AND QI`, `1`) |
| Actions | In a state: run an algorithm, then emit an output event |
| Algorithms | ST code (`<ST><![CDATA[…]]>`), may declare local variables |

## Execution in one sentence
An input event arrives → its `With` data is sampled → the ECC leaves the current state through the first true transition → the new state's actions run their algorithms and emit output events (which carry their `With` data) → transitions with condition `1` lead back to `START`.

## EAE details
- New FBs start from a template: `INIT`/`REQ` → `INITO`/`CNF`, `QI`/`QO`.
- `FBType.Basic.Algorithm.Order` lists algorithms; newer files give each algorithm a GUID `ID`.
- Events and variables have 16-hex IDs; networks reference pins by these IDs.
