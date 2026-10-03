# Changelog

## Unreleased

- **Agile CATs:** `eae_agile_cat_create` builds an SE.Agile-style CAT (IThis = AssetName, logic Basic FB with one
  plug/socket per signal, HMI_Indication/HMI_Control blocks with Min/Max/Units, initialization skeleton and HMI_INIT
  chain); `eae_agile_signal_add` extends an existing one. Basic FBs can declare Sockets/Plugs.
- **Situation-awareness HMI:** `eae_hmi_design_suggest`, `eae_hmi_symbol_build`, `eae_hmi_faceplate_build`,
  `eae_hmi_display_build` and the prompt `design_hmi_from_description` draw symbols, faceplates and displays to
  ISA-101 / High Performance HMI rules (gray graphics, analog indicators with normal band and limits, color + shape +
  number alarm indicators, color only when abnormal), for basic and Agile CATs, with setpoints and command buttons on
  the .NET HMI.
- **HMI review:** `eae_hmi_review` (HP-01…10, NAV-01…03, ALM-01…10, binding and style checks BIND-01, STY-01, AG-01/02,
  BS-01, plus manual checks); `eae_hmi_scripts` and `eae_alarm_profile_add` for alarm-word profiles in HMI support
  classes.
- **REST clients:** `eae_http_probe` (opt-in, GET/HEAD, allowed hosts only, token never returned) and
  `eae_rest_client_create` (generated CAT, request/response FBs, JSON extraction function, TLS socket).
- **Library and functions:** `eae_library_guide`, `eae_generic_fbs` (generic FB types usable in any solution),
  `eae_knowledge`; `eae_function_create` / `eae_function_update`.

## 0.5.0

- **.NET HMI:** create canvases, place CAT symbols, move/update/remove objects.
- **CATs and eHMI:** create CATs like EAE's "New CAT"; add symbols/faceplates; sub-CAT bookkeeping; IThis changes
  regenerate code and mappings; OPC UA expose/unexpose; eHMI canvases and symbol placement.
- **Networks:** Composite and SubApp creation, add/remove FBs, connect/disconnect, parameters, resource mapping.

## 0.2.0

- Create/edit Adapter, DataType, Basic FB; `eae_validate`; dry-run diffs, backups, audit log.

## 0.1.0

- Read-only server: solution index, library catalog, explain/trace, HMI/eHMI readers, knowledge layer.
