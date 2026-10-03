# HMI design for situation awareness (ISA-101, High Performance HMI, ISA-18.2)

Use this when designing or reviewing .NET HMI / eHMI canvases, CAT symbols and faceplates.
`eae_hmi_review` checks the parts that can be read from the files; the rest is in the manual checklist.
These are recognised practices, not EAE requirements: the **site HMI philosophy and style guide**
(ISA-101 lifecycle) decides the final colors and layouts.

## Situation awareness (Endsley)

Situation awareness is "the perception of the elements in the environment within a volume of time and
space, the comprehension of their meaning, and the projection of their status in the near future". A
display must support all three levels, not only show data:

| Level | Operator question | Display technique |
|---|---|---|
| 1 Perception | What is happening? Anything abnormal? | gray scene; color only for abnormal; alarm indicators with color + shape + text |
| 2 Comprehension | Is it OK? How far from normal? | moving analog indicators with normal range and alarm limits; values with units; state as text |
| 3 Projection | Where is it going? | embedded trends, rate-of-change arrows, targets/limits on trends |

Most legacy displays only support level 1 (raw numbers on a P&ID drawing) and leave levels 2–3 to the
operator's memory.

## ANSI/ISA-101.01 display hierarchy

| Level | Content | In EAE |
|---|---|---|
| 1 Overview | whole area at a glance: KPIs, area status, alarm counts per area; few controls | start canvas of a canvas resolution |
| 2 Unit control | one process unit: main loops, key values with analog indicators and trends; primary operating display | top-level canvases under the overview (`Canvases` in the resolution topology) |
| 3 Detail | all measurements and devices of a unit (P&ID-like) | child canvases (`Children`) |
| 4 Diagnostic | one device: parameters, tuning, maintenance data | CAT faceplates (`fMain`, `fAlarm`, …) opened from symbols |

Navigation should be identical on every display and reach any display in a few clicks. In EAE the
hierarchy is the topology in `CanvasesResolutionList.xml` / `WebCanvasesResolutionList.xml`; faceplates
give level 4 without extra canvases.

## High Performance HMI principles (ASM Consortium, Hollifield et al.)

- **Gray backgrounds** (light or medium gray), equipment drawn as simple 2D outlines in darker gray.
- **Color only for abnormal**: alarms and deviations get color; running/stopped, open/closed are shown by
  fill/shape and text, not red/green. Each color has one meaning across the whole HMI.
- **Alarm colors are exclusive**: red/orange/yellow (and magenta for e.g. suppressed) only for alarm
  priorities; never decorative.
- **Redundant coding**: alarm indicators combine color, shape and priority number/letter so they work for
  color-vision deficient operators (≈8% of men) and on grayscale.
- **Moving analog indicators** instead of bare numbers: a bar or pointer over the span, with the normal
  operating range shaded and alarm limits marked, answers "where am I versus where I should be".
- **Embedded trends** (short time window) for key values; show limits/targets on the trend.
- **No 3D, photos or animation** (spinning fans, flowing pipes): they add clutter and hide deviations.
- **Consistent typography**: one or two font families, a few sizes (title, label, value); readable at
  console distance (≥ 8–10 pt); text contrast ≥ 4.5:1.
- **Density**: show what the task needs; push detail down a level (faceplate) instead of filling a display.

## Alarms (ISA-18.2 / IEC 62682, EEMUA 191)

- 3–4 priorities, each with a distinct color, shape and text; priority distribution roughly
  low > medium > high.
- Unacknowledged alarms are distinguishable from acknowledged ones (e.g. blinking until acknowledged).
- Rate targets: about 1 alarm per 10 minutes per operator in steady state; ≤ 10 in 10 minutes during an
  upset is manageable. Floods, chattering and standing alarms are design defects.
- In EAE: alarm classes live in `HMI/Alarms/SystemAlarmClasses.xml` / `AlarmClasses.xml`
  (`<Class Name Prio>` with `State Came/CameNA/GoneNA` colors and a `Shortcut` letter); the review
  checks them (ALM-01…05).
- **Alarm-word profiles** (a common convention, read by `eae_hmi_scripts`): the logic packs alarm bits into a
  WORD (`AlarmWord.3 := <condition>;`, often with a `(* Bit 3: … *)` comment) and publishes it through an
  `HMI_Indication_Integer` block (`ALMW`); an HMI support class (`*.spt.cs`) maps bit → active text, clear text and
  priority (`Alarm(3, "…", "…")`, `Warning(…)`). The review cross-checks both sides: **ALM-06** a bit the logic sets
  has no text (the operator sees nothing meaningful), **ALM-07** a text for a bit never set, **ALM-08**
  duplicate/oversized bits, **ALM-09** priority distribution (aim ≈ 5% high / 15% medium / 80% low), **ALM-10**
  duplicate or missing texts. `eae_alarm_profile_add` appends texts in the file's own layout.

## Applying it in EAE with eae-mcp

1. Agree the style guide (palette, fonts, indicator set, alarm classes) and put the colors into the HMI
   theme (`HMI/Colors/*.color.theme`) so displays use **theme tokens**, not hard-coded RGB.
2. Build CAT symbols that carry SA: value + unit + analog bar with normal band + state text + alarm
   indicator (`eae_cat_add_symbol`, then draw in EAE). Faceplates are the level-4 detail.
3. Create the canvas hierarchy with `eae_hmi_canvas_create` / `eae_ehmi_canvas_create` (overview first) and
   place instances with `eae_hmi_place_symbol` / `eae_ehmi_place_symbol`.
4. Run `eae_hmi_review` (optionally `level=1..4` per display) and fix warnings; go through the manual checks.

Review rules: HP-01 background, HP-02 static saturated color, HP-03 palette size, HP-04 alarm colors used
statically, HP-05 images, HP-06 typography, HP-07 text contrast, HP-08 density, HP-09 numbers without
analog context, HP-10 no trend on level-1/2 displays, NAV-01…03 hierarchy, ALM-01…05 alarm classes.

## From description to HMI (generated, standard-compliant)

eae-mcp draws symbols and displays itself; the assistant only turns the description into a design.

| In the description | Element | Needs |
|---|---|---|
| a measurement or setpoint feedback (flow, level, temperature, power, speed) | `value` | unit, span `range`, `normal` band, `limits` (low/high alarm) and their `priority` |
| a discrete condition (running, open, mode, state code) | `state` | text per value (`'true'/'false'` for BOOL), which values are `abnormal`, priority |
| an alarm flag or alarm priority code | `alarm` | priority (BOOL) or a 0..4 code variable |
| a name, tag or message | `text` | — |
| an operator setpoint (flow SP, temperature SP) | `setpoint` | basic: an IThis **output** variable carried by an output event; Agile: an `HMI_Control_Real/Integer` path and its `step` |
| an operator command (start, stop, reset, mode) | `command` | basic: an IThis **output event** (+ `value` for its one WITH variable); Agile: an `HMI_Control_Bool/Integer` path and `value` (`true`/`false`/`toggle`/integer); `confirm` for critical actions |

Rules applied by the generators (do not override them in the design):
- static drawing is gray; values, pointers and states are dark gray/black; color is set at runtime only when a
  value is outside its alarm limits, a state is abnormal or an alarm is active, using the priority color;
- every alarm indicator = color + shape (▲ critical, ◆ high, ■ medium, ● low) + priority number;
- values always show their unit and, with a span, a moving analog indicator with the normal band shaded;
- one font family, three sizes; text contrast ≥ 4.5:1; cards on a light-gray canvas;
- displays: title, one framed group per area/section, symbols in a grid; ≤ 12 instances on level 1,
  ≤ 30 on level 2; content must fit the canvas (else split the display).

Workflow: prompt `design_hmi_from_description` → `eae_hmi_design_suggest` (draft + missing data) →
`eae_hmi_symbol_build` → `eae_hmi_display_build` (hmi, then ehmi) → `eae_hmi_review`.
Never invent engineering limits: ask when the description does not give them.

Example element list for "Feed pump P-101: flow 0–120 m³/h, normal 40–90, low alarm 20, high alarm 105
(high priority); state Stopped/Running/Fault (fault critical); run feedback; alarm priority code":

```json
[{"kind": "alarm", "var": "Alarm"},
 {"kind": "value", "var": "Flow", "unit": "m3/h", "range": [0, 120], "normal": [40, 90], "limits": [20, 105], "priority": 2},
 {"kind": "state", "var": "State", "states": {"0": "Stopped", "1": "Running", "2": "Fault"}, "abnormal": ["2"], "priority": 1},
 {"kind": "state", "var": "Running", "label": "Run", "states": {"true": "On", "false": "Off"}}]
```

Commands (setpoints and buttons) follow what EAE/SE.Agile do: a basic setpoint is a `TextBox<T>` bound to the
output variable (EAE writes it and fires its output event, as SE.Agile `sValueInput` does with `oValue`); a basic
button calls the symbol's generated `FireEvent_<EVENT>(value)`; an Agile control is written through its bridge,
`bridge.FireEvent_CNF(value)` (as SE.Agile control symbols do), with −/+ steps clamped to the block's
Minimum/Maximum. Buttons use the theme tokens (`ButtonBrush`, `ButtonFont`, …) of EAE's `DrawnButton`; `confirm`
asks before sending (ISA-101: confirm actions with significant consequences). Commands are generated for the .NET
HMI only — the samples show no verified eHMI write API (even SE.Agile's `seValControl` probes several); draw eHMI
commands in EAE.

Level 4 (detail) faceplates: `eae_hmi_faceplate_build` draws a .NET faceplate (`fSA`, namespace
`<Root>.Faceplates.<Cat>`, `HMIFaceplate` with `FaceplateBrush` background and `FaceplateClose = Automatic`, window
title from `AssetName`, as the SE.Agile faceplates) from the same element list; a click on the generated symbol
card opens it (`card.OpenFaceplates.Add(new OpenFaceplate("fSA", MouseButtonType.Click))`, as EAE symbols do). Keep the symbol to what level 1–2 needs; put limits, setpoints and commands on the faceplate.

Not generated yet (do in EAE, then re-run `eae_hmi_review`): embedded trends (TrendControl pens) and eHMI
faceplates.

## Two HMI styles in EAE: basic and Agile

`eae_hmi_review` classifies every CAT and display (`styles`, `cat_reviews`, `style` per document).

| | Basic style | Agile style (SE.Agile library) |
|---|---|---|
| Where signals live | IThis (CAT HMI interface) variables | one sub-CAT **HMI block** per signal: `HMI_Indication_Real/Bool/Integer/String`, `HMI_Control_*`, `ModeSelector`, `DA`/`DIA`/`FIP` |
| IThis | all displayed signals | usually only `AssetName` |
| Symbol content | widgets (`TextBox<T>`, `Label`, bars, LEDs) with `TagName = <IThis var>` | the blocks' own symbols embedded (`SE.Agile.Symbols.HMI_Indication_Real_v1_0.sValChanged`, `sValueBarVertIcon`, …) with `TagName = <sub-CAT path>` |
| Nesting | flat | application CAT `acX` (Control = `BroadcasterX`, Equipment = `bcX`) → base CAT `bcX` (HMI blocks + `IOSignal_*`); paths like `Equipment.I` |
| Per-signal metadata | in the symbol (static) | in the block: Minimum, Maximum, Units, DecimalPlaces, Category/Prefix/Scope (PLOAD) |
| When to use | small/simple CATs, quick symbols, generated SA symbols (`eae_hmi_symbol_build`) | libraries of reusable equipment, consistent faceplates, MQTT/Broadcaster integration |

Review rules: **BIND-01** a TagName resolves to neither an IThis variable nor a sub-CAT (empty widget at
runtime, e.g. a stale binding after a block was renamed or removed); **STY-01** one CAT mixes both styles;
**AG-01** an HMI block is not shown on any symbol/faceplate; **AG-02** an Agile CAT carries signals in IThis;
**BS-01** a basic CAT has IThis inputs that no symbol shows. Styles: `basic`, `agile`, `agile-block` (the
block itself), `mixed`, `none` (no bindings).

`eae_hmi_symbol_build` draws both styles; each element's `var` decides:
- **Basic:** an IThis input → widgets bound with `TagName = <var>` (Designer `ValueChanged` handlers / eHMI
  `ea.value`).
- **Agile:** an `HMI_Indication_*` block path (`Equipment.IX`, `Equipment.TOT.I`) → the block's invisible bridge
  is embedded with `TagName = <path>` (.NET `SE.Agile.Symbols.<Block>.sValChanged`, eHMI `seValChanged`) and the
  SA graphics are drawn from its value (`OnValChanged` + `Val` / `setValueChangedHandler`). On .NET, units,
  decimals and — when no `range` is given — the span come from the block at runtime (`ValUnits`,
  `ValDecimalPlaces`, `ValMinimum`/`ValMaximum`); eHMI needs `unit` and `range` in the design.
  `HMI_Indication_String` has no eHMI bridge (use technology `hmi`). `HMI_Control_*` blocks become setpoints and
  commands (.NET); `ModeSelector`/`DA` blocks are not drawn yet.
- Mixing IThis variables and block paths in one symbol works but reports STY-01.

`eae_hmi_design_suggest` drafts Agile CATs from their blocks (walking `acX` → `Equipment` → blocks): Real/Integer
→ `value`, Bool → `state`, String → `text`, Control_Real → `setpoint`, Control_Bool → `state` + toggle `command`,
Control_Integer → `command`s; other blocks are listed under `not_drawn`.
