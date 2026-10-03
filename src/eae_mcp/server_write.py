"""M2 write tools: create/edit Adapter, DataType, Basic FB; validate. All default to dry_run=True."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import services
from .model import Algorithm, DataTypeDef, ECAction, ECState, ECTransition, EnumValue, Event, Interface, Var
from .hmi import dotnet_edit, ehmi_edit, sa_builder, sa_tools
from .project import agile_edit, cat_edit, edit, network_edit, opcua_edit, rest_client

CREATE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
MODIFY = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


class VarSpec(BaseModel):
    name: str
    type: str = Field(description="IEC type, e.g. BOOL, INT, REAL, STRING[32], TIME, or a DataType name")
    initial_value: str | None = None
    array_size: str | None = Field(None, description="e.g. '10' for an array of 10 elements")
    comment: str | None = None
    namespace: str | None = Field(None, description="Namespace of a library DataType, e.g. 'SE.Agile'")

    def to_model(self) -> Var:
        return Var(self.name, self.type, initial_value=self.initial_value, array_size=self.array_size,
                   comment=self.comment, namespace=self.namespace)


class HmiElementSpec(BaseModel):
    kind: Literal["value", "state", "alarm", "text", "setpoint", "command"]
    var: str = Field(description="value/state/alarm/text: IThis input or Agile HMI_Indication/Control block path; "
                                 "setpoint: IThis output variable or HMI_Control_Real/Integer path; "
                                 "command: IThis output event or HMI_Control_Bool/Integer path")
    label: str | None = None
    unit: str | None = None
    range: list[float] | None = Field(None, description="[low, high] span of the analog indicator")
    normal: list[float] | None = Field(None, description="[low, high] normal operating range (shaded band)")
    limits: list[float | None] | None = Field(None, description="[low alarm, high alarm]; null for none")
    priority: int = Field(2, description="Alarm priority 1 (critical) .. 4 (low) used for abnormal display")
    states: dict[str, str] | None = Field(None, description="value -> text, BOOL uses 'true'/'false'")
    abnormal: list[str] | None = Field(None, description="state values shown as abnormal")
    step: float | None = Field(None, description="setpoint on an Agile block: -/+ step in engineering units")
    value: str | None = Field(None, description="command: value sent (true/false/toggle, integer, number)")
    confirm: bool = Field(False, description="command/setpoint: ask the operator to confirm (critical actions)")

    def to_model(self) -> "sa_builder.ElementSpec":
        return sa_builder.ElementSpec(self.kind, self.var, self.label, self.unit,
                                      tuple(self.range) if self.range else None,
                                      tuple(self.normal) if self.normal else None,
                                      tuple(self.limits) if self.limits else None, self.priority,
                                      dict(self.states or {}), list(self.abnormal or []), self.step, self.value,
                                      self.confirm)


class HmiSectionSpec(BaseModel):
    title: str
    instances: list[str] = Field(description="Application instance names (CATs)")
    columns: int = 0


class RestFieldSpec(BaseModel):
    name: str = Field(description="Output/HMI variable name")
    path: str = Field(description="Dotted JSON keys, e.g. 'price' or 'data.price'")
    type: str = Field("REAL", description="REAL, LREAL, DINT, INT, BOOL or STRING")
    occurrence: int = Field(1, description="n-th match of the last key (arrays of objects)")


class AgileSignalSpec(BaseModel):
    name: str = Field(description="Signal / HMI block name, e.g. FLOW, IRUN, SP")
    kind: Literal["indication", "control"] = Field("indication",
                                                    description="indication: logic → HMI; control: operator → logic")
    type: Literal["real", "bool", "integer", "string"] = "real"
    minimum: float | None = None
    maximum: float | None = None
    units: str | None = None
    decimals: int | None = None
    default: str | None = Field(None, description="control: initial value (IEC literal)")

    def to_model(self):
        from .project.agile_edit import AgileSignal
        return AgileSignal(self.name, self.kind, self.type, self.minimum, self.maximum, self.units, self.decimals,
                           self.default)


class AlarmTextSpec(BaseModel):
    bit: int
    active: str = Field(description="Text while the alarm is active (what is wrong, where)")
    clear: str = Field(description="Text when it clears")
    helper: str = Field(description="Profile helper giving the priority, e.g. Alarm or Warning")


class EventSpec(BaseModel):
    name: str
    comment: str | None = None
    with_vars: list[str] = Field(default_factory=list,
                                 description="Data sampled (inputs) or sent (outputs) with this event")

    def to_model(self) -> Event:
        return Event(self.name, comment=self.comment, with_vars=list(self.with_vars))


class ActionSpec(BaseModel):
    algorithm: str | None = None
    output: str | None = Field(None, description="Output event to emit after the algorithm (or adapter event 'Plug.CNF')")


class StateSpec(BaseModel):
    name: str
    comment: str | None = None
    actions: list[ActionSpec] = Field(default_factory=list)

    def to_model(self) -> ECState:
        return ECState(self.name, self.comment, [ECAction(a.algorithm, a.output) for a in self.actions])


class TransitionSpec(BaseModel):
    source: str
    destination: str
    condition: str = Field("", description="Input event, guard expression, or '1' (always)")

    def to_model(self) -> ECTransition:
        return ECTransition(self.source, self.destination, self.condition)


class AlgorithmSpec(BaseModel):
    name: str
    text: str = Field(description="Structured Text body")
    comment: str | None = None
    local_vars: list[VarSpec] = Field(default_factory=list)

    def to_model(self) -> Algorithm:
        return Algorithm(self.name, self.text, comment=self.comment, local_vars=[v.to_model() for v in self.local_vars])


class EnumValueSpec(BaseModel):
    name: str
    value: str | None = None


def _interface(event_inputs, event_outputs, input_vars, output_vars) -> Interface:
    return Interface(
        event_inputs=[e.to_model() for e in event_inputs or []],
        event_outputs=[e.to_model() for e in event_outputs or []],
        input_vars=[v.to_model() for v in input_vars or []],
        output_vars=[v.to_model() for v in output_vars or []],
    )


def _datatype(kind, members, values, base_type, ranges) -> DataTypeDef:
    return DataTypeDef(
        kind=kind,
        base_type=base_type,
        members=[m.to_model() for m in members or []],
        values=[EnumValue(v.name, v.value) for v in values or []],
        ranges=[(str(r[0]), str(r[1])) for r in ranges or []],
    )


def register_write_tools(mcp: MCPServer, ws: services.Workspace, run, sol) -> None:
    def change(solution, build, dry_run):
        s = sol(solution)
        return run(services.run_change, ws, s, lambda: build(s), dry_run)

    @mcp.tool(annotations=CREATE)
    def eae_adapter_create(name: str, event_inputs: list[EventSpec] | None = None,
                           event_outputs: list[EventSpec] | None = None, input_vars: list[VarSpec] | None = None,
                           output_vars: list[VarSpec] | None = None, comment: str | None = None,
                           folder: str | None = None, library: str | None = None, dry_run: bool = True,
                           solution: str | None = None) -> dict:
        """Create an Adapter type (.adp + .doc.xml, registered in the .dfbproj).

        Directions are from the plug side: event_inputs/input_vars flow socket → plug,
        event_outputs/output_vars flow plug → socket. folder: logical folder like '.Connectors'.
        library: write into a library project (e.g. 'SE.Agile') instead of the main project.
        dry_run (default true) returns the diff without writing.
        """
        itf = _interface(event_inputs, event_outputs, input_vars, output_vars)
        return change(solution, lambda s: edit.create_adapter(s, name, itf, comment, folder, library), dry_run)

    @mcp.tool(annotations=CREATE)
    def eae_datatype_create(name: str, kind: Literal["struct", "enum", "array", "subrange"],
                            members: list[VarSpec] | None = None, values: list[EnumValueSpec] | None = None,
                            base_type: str | None = None, ranges: list[list[int]] | None = None,
                            comment: str | None = None, folder: str | None = None, library: str | None = None,
                            dry_run: bool = True, solution: str | None = None) -> dict:
        """Create a DataType (IEC61499/DataType/<name>.dt + .doc.xml, registered in the .dfbproj).

        struct: members. enum: values (+ base_type, default USINT). array: base_type + ranges [[0, 9]].
        subrange: base_type + ranges [[0, 100]]. Reserved words (e.g. ON) are rejected.
        """
        dt = _datatype(kind, members, values, base_type, ranges)
        return change(solution, lambda s: edit.create_datatype(s, name, dt, comment, folder, library), dry_run)

    @mcp.tool(annotations=MODIFY)
    def eae_datatype_update(name: str, kind: Literal["struct", "enum", "array", "subrange"],
                            members: list[VarSpec] | None = None, values: list[EnumValueSpec] | None = None,
                            base_type: str | None = None, ranges: list[list[int]] | None = None,
                            dry_run: bool = True, solution: str | None = None) -> dict:
        """Replace the definition of an existing DataType (members/values/ranges)."""
        dt = _datatype(kind, members, values, base_type, ranges)
        return change(solution, lambda s: edit.replace_datatype(s, name, dt), dry_run)

    @mcp.tool(annotations=CREATE)
    def eae_basic_create(name: str, event_inputs: list[EventSpec], event_outputs: list[EventSpec],
                         input_vars: list[VarSpec] | None = None, output_vars: list[VarSpec] | None = None,
                         internal_vars: list[VarSpec] | None = None, states: list[StateSpec] | None = None,
                         transitions: list[TransitionSpec] | None = None,
                         algorithms: list[AlgorithmSpec] | None = None, comment: str | None = None,
                         folder: str | None = None, library: str | None = None, dry_run: bool = True,
                         solution: str | None = None) -> dict:
        """Create a Basic FB (.fbt + .doc.xml + .meta.xml) with interface, ECC and ST algorithms.

        START is added automatically if missing. Each transition needs a condition (input event,
        guard, or '1'). Typical template: INIT(with QI)→INITO(with QO), REQ→CNF.
        """
        itf = _interface(event_inputs, event_outputs, input_vars, output_vars)
        build = lambda s: edit.create_basic(  # noqa: E731
            s, name, itf, [v.to_model() for v in internal_vars or []], [st.to_model() for st in states or []],
            [t.to_model() for t in transitions or []], [a.to_model() for a in algorithms or []],
            comment, folder, library)
        return change(solution, build, dry_run)

    @mcp.tool(annotations=MODIFY)
    def eae_fb_update_interface(name: str, add_event_inputs: list[EventSpec] | None = None,
                                add_event_outputs: list[EventSpec] | None = None,
                                add_input_vars: list[VarSpec] | None = None,
                                add_output_vars: list[VarSpec] | None = None, remove: list[str] | None = None,
                                set_with: dict[str, list[str]] | None = None, force: bool = False,
                                dry_run: bool = True, solution: str | None = None) -> dict:
        """Change the interface of an Adapter, Basic, Composite or CAT type.

        remove: pin names; refused while a pin is used in any network, ECC action or transition
        (unless force=true). set_with: {event: [vars]} replaces an event's WITH list.
        New pins get new IDs; existing IDs (and therefore connections) are preserved.
        """
        events = [("input", e.to_model()) for e in add_event_inputs or []] + \
                 [("output", e.to_model()) for e in add_event_outputs or []]
        vars_ = [("input", v.to_model()) for v in add_input_vars or []] + \
                [("output", v.to_model()) for v in add_output_vars or []]
        return change(solution, lambda s: edit.update_interface(s, name, events, vars_, remove, set_with, force),
                      dry_run)

    @mcp.tool(annotations=MODIFY)
    def eae_basic_upsert_algorithm(name: str, algorithm: str, text: str, comment: str | None = None,
                                   local_vars: list[VarSpec] | None = None, dry_run: bool = True,
                                   solution: str | None = None) -> dict:
        """Add a new ST algorithm to a Basic FB, or replace the ST code of an existing one."""
        lv = [v.to_model() for v in local_vars] if local_vars is not None else None
        return change(solution, lambda s: edit.upsert_algorithm(s, name, algorithm, text, comment, lv), dry_run)

    @mcp.tool(annotations=MODIFY)
    def eae_basic_update_ecc(name: str, add_states: list[StateSpec] | None = None,
                             remove_states: list[str] | None = None,
                             add_transitions: list[TransitionSpec] | None = None,
                             remove_transitions: list[TransitionSpec] | None = None,
                             set_actions: dict[str, list[ActionSpec]] | None = None, dry_run: bool = True,
                             solution: str | None = None) -> dict:
        """Edit a Basic FB's ECC: add/remove states and transitions, or replace a state's actions.
        Removing a state also removes its transitions. START cannot be removed."""
        actions = {k: [ECAction(a.algorithm, a.output) for a in v] for k, v in (set_actions or {}).items()}
        build = lambda s: edit.update_ecc(  # noqa: E731
            s, name, [st.to_model() for st in add_states or []], remove_states,
            [t.to_model() for t in add_transitions or []], [t.to_model() for t in remove_transitions or []],
            actions)
        return change(solution, build, dry_run)

    @mcp.tool(annotations=CREATE)
    def eae_function_create(name: str, code: str, return_type: str | None = None,
                            inputs: list[VarSpec] | None = None, outputs: list[VarSpec] | None = None,
                            inouts: list[VarSpec] | None = None, temp_vars: list[VarSpec] | None = None,
                            comment: str | None = None, library: str | None = None, dry_run: bool = True,
                            solution: str | None = None) -> dict:
        """Create a helper Function (IEC 61131-3 POU, POU/<name>.fct): stateless ST code called from
        Basic FB algorithms, e.g. `s := FormatValue(Value := x);`. Assign the result to the function
        name (`<name> := …;`). inouts are VAR_IN_OUT (by reference); array_size "*" accepts arrays of
        any length (loop with UPPER_BOUND(arr, 1)). temp_vars are locals reset on every call."""
        conv = lambda xs: [x.to_model() for x in xs or []]  # noqa: E731
        return change(solution, lambda s: edit.create_function(s, name, code, conv(inputs), conv(outputs),
                                                               conv(inouts), return_type, conv(temp_vars),
                                                               comment, library), dry_run)

    @mcp.tool(annotations=MODIFY)
    def eae_function_update(name: str, code: str | None = None, temp_vars: list[VarSpec] | None = None,
                            dry_run: bool = True, solution: str | None = None) -> dict:
        """Replace a Function's ST code and/or its temp variables. The interface is kept; to change
        inputs/outputs, create a new function (e.g. <name>_v1_1) and switch callers to it."""
        tv = [x.to_model() for x in temp_vars] if temp_vars is not None else None
        return change(solution, lambda s: edit.update_function(s, name, code, tv), dry_run)

    # -- M3: networks ---------------------------------------------------------------------------

    @mcp.tool(annotations=CREATE)
    def eae_composite_create(name: str, event_inputs: list[EventSpec] | None = None,
                             event_outputs: list[EventSpec] | None = None, input_vars: list[VarSpec] | None = None,
                             output_vars: list[VarSpec] | None = None, comment: str | None = None,
                             folder: str | None = None, library: str | None = None, dry_run: bool = True,
                             solution: str | None = None) -> dict:
        """Create a Composite FB with its interface and an empty network (boundary pins included).
        Then fill it with eae_net_add_fb / eae_net_connect using network=<name>."""
        itf = _interface(event_inputs, event_outputs, input_vars, output_vars)
        return change(solution, lambda s: network_edit.create_composite(s, name, itf, comment, folder, library),
                      dry_run)

    # -- M4: CATs ------------------------------------------------------------------------------

    @mcp.tool(annotations=CREATE)
    def eae_cat_create(name: str, event_inputs: list[EventSpec] | None = None,
                       event_outputs: list[EventSpec] | None = None, input_vars: list[VarSpec] | None = None,
                       output_vars: list[VarSpec] | None = None, hmi_event_inputs: list[EventSpec] | None = None,
                       hmi_event_outputs: list[EventSpec] | None = None, hmi_input_vars: list[VarSpec] | None = None,
                       hmi_output_vars: list[VarSpec] | None = None, symbol: str = "sDefault",
                       web_symbol: str | None = "seDefault", comment: str | None = None, folder: str | None = None,
                       library: str | None = None, dry_run: bool = True, solution: str | None = None) -> dict:
        """Create a CAT exactly like EAE's "New CAT": <name>.fbt (composite network with the IThis
        HMI interface instance, QI=TRUE), <name>_HMI.fbt, .cfg, doc/meta/offline/opcua files, the .NET
        HMI symbol `symbol` (+ generated .event.cs/.def.cs) and the eHMI symbol `web_symbol`
        (null = no eHMI symbol). All files are registered in the .dfbproj / HMI.csproj / WEB.htmlproj.

        CAT interface: omit event/var lists to get EAE's default (INIT/REQ, INITO/CNF, QI, QO).
        HMI interface (IThis): INIT/INITO/QI/QO/STATUS are always present. hmi_event_inputs with
        hmi_input_vars carry values from the CAT to the HMI (e.g. REQ WITH OUT1); hmi_event_outputs
        with hmi_output_vars carry commands from the HMI back. Supported HMI types: BOOL, BYTE, SINT,
        INT, DINT, LINT, USINT, UINT, UDINT, REAL, LREAL, STRING.
        Wire the inside with eae_net_add_fb / eae_net_connect using network=<name> (pins of IThis are
        addressed as IThis.<pin>). Open the CAT in EAE afterwards to draw the symbols."""
        given = any(x for x in (event_inputs, event_outputs, input_vars, output_vars))
        itf = _interface(event_inputs, event_outputs, input_vars, output_vars) if given else None
        hmi = _interface(hmi_event_inputs, hmi_event_outputs, hmi_input_vars, hmi_output_vars)
        return change(solution, lambda s: cat_edit.create_cat(s, name, itf, hmi, symbol, web_symbol or None, folder,
                                                              library, comment), dry_run)

    # -- REST clients -----------------------------------------------------------------------------

    @mcp.tool(annotations=CREATE)
    def eae_rest_client_create(name: str, host: str, path: str, fields: list[RestFieldSpec], method: str = "GET",
                               port: int = 443, tls: bool = True, auth: Literal["bearer", "none"] | str = "bearer",
                               headers: dict[str, str] | None = None, body: str | None = None,
                               period: str = "T#5m", endpoint: str | None = None, folder: str | None = ".RestApi",
                               library: str | None = None, dry_run: bool = True,
                               solution: str | None = None) -> dict:
        """Generate a REST/HTTP client CAT (hand-built HTTP over the NETIO socket block): a CAT `name`
        with INIT(QI, Token, Endpoint)/REQ -> CNF(Status, fields…) and an HMI interface, plus
        <name>_Request (builds the HTTP request), <name>_Response (status, Content-Length/chunked, JSON
        fields), function <name>_Json, NETIO (TLS socket), E_CYCLE polling every `period` and E_PERMIT.
        fields: [{name, path (dotted JSON keys), type REAL|LREAL|DINT|INT|BOOL|STRING, occurrence}] — run
        eae_http_probe first to get them. auth: bearer | none | header:<Name>; the token is a CAT input set
        at runtime, never written into files. Answers must fit in 4 KB (narrow the query)."""
        spec = rest_client.RestClientSpec(
            name=name, host=host, path=path, method=method, port=port, tls=tls, headers=headers or {}, body=body,
            auth=auth, fields=[rest_client.RestField(f.name, f.path, f.type.upper(), f.occurrence) for f in fields],
            period=period, endpoint=endpoint, folder=folder, library=library)
        return change(solution, lambda s: rest_client.create_rest_client(s, spec), dry_run)

    # -- Agile CATs (SE.Agile) -------------------------------------------------------------------

    @mcp.tool(annotations=CREATE)
    def eae_agile_cat_create(name: str, signals: list[AgileSignalSpec], logic: str | None = None,
                             class_id: int = 201, folder: str | None = None, dry_run: bool = True,
                             solution: str | None = None) -> dict:
        """Create an Agile-style CAT (SE.Agile library pattern): IThis carries only AssetName;
        a new logic Basic FB (default fb<Name>) gets one plug aHMI_Indication_<T> per indication and one socket
        aHMI_Control_<T> per control; each signal becomes an HMI_Indication_<T>/HMI_Control_<T> sub-CAT with
        Minimum/Maximum/Units/DecimalPlaces, wired to the logic, on the HMI_INIT chain, with the standard skeleton
        (GetAssetName → InitComponent(ClassId) → logic → EVENTCHAIN). The logic's REQ algorithm only forwards
        <signal>_Value: write the computation afterwards. Then draw it with eae_hmi_design_suggest +
        eae_hmi_symbol_build (elements bind to the block names)."""
        sigs = [x.to_model() for x in signals]
        return change(solution, lambda s: agile_edit.create_agile_cat(s, name, sigs, logic, class_id, folder), dry_run)

    @mcp.tool(annotations=MODIFY)
    def eae_agile_signal_add(cat: str, signals: list[AgileSignalSpec], logic: str | None = None,
                             dry_run: bool = True, solution: str | None = None) -> dict:
        """Add signals to an existing Agile CAT: HMI block sub-CATs, the matching plug/socket on its logic Basic
        FB (found automatically, or name the instance with logic=…), PREQ→PLOAD, logic↔block adapter wiring and
        the HMI_INIT chain. The logic's ECC/ST is not changed: the result lists the lines to add."""
        sigs = [x.to_model() for x in signals]
        return change(solution, lambda s: agile_edit.add_signals(s, cat, sigs, logic), dry_run)

    @mcp.tool(annotations=MODIFY)
    def eae_alarm_profile_add(profile: str, alarms: list[AlarmTextSpec], dry_run: bool = True,
                              solution: str | None = None) -> dict:
        """Add alarm texts to an existing alarm-word profile in an HMI support class (see eae_hmi_scripts), in
        the file's own layout. Use it for ALM-06 findings: the bit's ST condition/comment is the evidence; write
        texts that name the condition (ISA-18.2) and pick the priority helper from the consequence."""
        from .hmi import scripts as sc
        rows = [(a.bit, a.active, a.clear, a.helper) for a in alarms]
        return change(solution, lambda s: sc.add_profile_entries(s, profile, rows), dry_run)

    # -- M4: eHMI canvases ------------------------------------------------------------------------

    @mcp.tool(annotations=CREATE)
    def eae_ehmi_canvas_create(device: str, name: str, resolution: str | None = None, title: str = "",
                               dry_run: bool = True, solution: str | None = None) -> dict:
        """Create an eHMI (web) canvas on a device (eHMI canvases are per device) and add it to the
        top level of a canvas resolution (default: the first one with a real size, e.g. 1024x768).
        The device must already have one eHMI canvas made in EAE (that sets up its resolution list)."""
        return change(solution, lambda s: ehmi_edit.create_canvas(s, device, name, resolution, title), dry_run)

    @mcp.tool(annotations=CREATE)
    def eae_ehmi_place_symbol(canvas: str, instance: str, symbol: str | None = None, left: float | None = None,
                              top: float | None = None, width: float | None = None, height: float | None = None,
                              name: str | None = None, application: str | None = None, dry_run: bool = True,
                              solution: str | None = None) -> dict:
        """Show a CAT instance of an application on an eHMI canvas using one of its eHMI symbols
        (default: the CAT's first web symbol). canvas: 'Canvas1' or '<Device>/Canvas1'. The object
        is bound to the instance by its ID (tagName). Size defaults to the symbol's design size;
        position defaults to below the existing objects. Warns if the instance is not mapped to the
        canvas's device."""
        return change(solution, lambda s: ehmi_edit.place_symbol(s, canvas, instance, symbol, left, top, width,
                                                                 height, name, application), dry_run)

    @mcp.tool(annotations=MODIFY)
    def eae_ehmi_update_object(canvas: str, name: str, properties: dict[str, Any], dry_run: bool = True,
                               solution: str | None = None) -> dict:
        """Change properties of an eHMI canvas object, e.g. {"left": 40, "top": 20, "width": 200}.
        Keys are the .cnv.json keys shown by eae_hmi_describe; null removes a key."""
        return change(solution, lambda s: ehmi_edit.update_object(s, canvas, name, properties), dry_run)

    @mcp.tool(annotations=CREATE)
    def eae_cat_add_symbol(cat: str, name: str, technology: Literal["hmi", "ehmi"] = "hmi", faceplate: bool = False,
                           dry_run: bool = True, solution: str | None = None) -> dict:
        """Add an empty symbol to a CAT: a .NET HMI symbol (technology='hmi', name like 'sBig'), a .NET
        faceplate (faceplate=true, name like 'fMain') or an eHMI symbol (technology='ehmi', name like
        'seBig'). Creates the files, the .cfg entry and project registration, and regenerates the CAT's
        .event.cs/.def.cs. Draw the content in EAE afterwards."""
        return change(solution, lambda s: cat_edit.add_symbol(s, cat, name, technology, faceplate), dry_run)

    @mcp.tool(annotations=MODIFY)
    def eae_ehmi_remove_object(canvas: str, name: str, dry_run: bool = True, solution: str | None = None) -> dict:
        """Remove an object (e.g. a placed symbol, 'symbol1') from an eHMI canvas."""
        return change(solution, lambda s: ehmi_edit.remove_object(s, canvas, name), dry_run)

    @mcp.tool(annotations=MODIFY)
    def eae_opcua_expose(path: str, exposed: bool = True, application: str | None = None, dry_run: bool = True,
                         solution: str | None = None) -> dict:
        """Expose a variable to OPC UA (or remove the exposure with exposed=false), like ticking it in
        EAE. path: <application instance>[.<inner FB>...].<variable>, e.g. 'CAT1.IThis.OUT1'. Written
        to the application layer and to every resource the instance is mapped to."""
        return change(solution, lambda s: opcua_edit.set_exposed(s, path, exposed, application), dry_run)

    # -- SA HMI generation ---------------------------------------------------------------------------

    @mcp.tool(annotations=CREATE)
    def eae_hmi_symbol_build(cat: str, title: str, elements: list[HmiElementSpec],
                             technology: Literal["hmi", "ehmi", "both"] = "both", width: int = 240,
                             symbol: str = "sSA", web_symbol: str = "seSA", overwrite: bool = False,
                             dry_run: bool = True, solution: str | None = None) -> dict:
        """Draw a situation-awareness symbol for a CAT (.NET HMI and/or eHMI) from a design, following
        ISA-101 / High Performance HMI: gray card, title, live values with units and a moving analog
        indicator (span, shaded normal band, alarm-limit ticks, pointer that turns to the priority color
        only when outside the limits), state texts with abnormal states highlighted at runtime, and alarm
        indicators with color + shape + priority number (hidden when no alarm).
        Each element's var is either an IThis input of the CAT (basic style; add it first with
        eae_fb_update_interface on <Cat>_HMI) or the path of an SE.Agile HMI_Indication_* block (Agile style,
        e.g. 'Equipment.IX'): the block's invisible bridge (sValChanged / seValChanged) is embedded and the
        value drawn from it; on .NET units, decimals and span come from the block when not given.
        Operator actions (.NET only): setpoint (IThis output variable → entry box; Agile HMI_Control_Real/Integer →
        value with −/+ step buttons) and command (IThis output event → button firing it with `value`; Agile
        HMI_Control_Bool/Integer → button writing true/false/toggle or an integer); confirm=true for critical actions. The result is generated code (Designer + C# / JSON + TypeScript) that must pass
        eae_hmi_review; existing symbols are only replaced with overwrite=true."""
        design = sa_builder.SymbolDesign(title, [e.to_model() for e in elements], width)
        return change(solution, lambda s: sa_tools.build_symbol(s, cat, design, technology, symbol, web_symbol,
                                                                overwrite), dry_run)

    @mcp.tool(annotations=CREATE)
    def eae_hmi_faceplate_build(cat: str, title: str, elements: list[HmiElementSpec], width: int = 360,
                                faceplate: str = "fSA", symbol: str | None = "sSA", overwrite: bool = False,
                                dry_run: bool = True, solution: str | None = None) -> dict:
        """Draw a level-4 (ISA-101 detail) .NET faceplate for a CAT from a design with the same elements as
        eae_hmi_symbol_build (values with normal band/limits, states, alarms, setpoints, commands) — put the
        detail and the operator actions here and keep the symbol small. The faceplate window title follows
        AssetName when IThis has it; a click on the CAT's generated symbol `symbol` opens it (OpenFaceplates).
        eHMI faceplates are not generated (no sample to learn the format from)."""
        design = sa_builder.SymbolDesign(title, [e.to_model() for e in elements], width)
        return change(solution, lambda s: sa_tools.build_faceplate(s, cat, design, faceplate, symbol, overwrite),
                      dry_run)

    @mcp.tool(annotations=CREATE)
    def eae_hmi_display_build(canvas: str, title: str, sections: list[HmiSectionSpec], level: int = 2,
                              technology: Literal["hmi", "ehmi"] = "hmi", device: str | None = None,
                              symbol: str | None = None, replace: bool = False, dry_run: bool = True,
                              solution: str | None = None) -> dict:
        """Lay out an operating display (ISA-101 level 1-4): creates the canvas if needed (eHMI: pass
        device), adds a title, one gray framed group per section and places each CAT instance with its
        SA symbol (sSA/seSA, else the CAT's first symbol) in a grid. Warns when the display is too dense
        for its level or does not fit; the result must pass eae_hmi_review. Run once per technology."""
        design = sa_tools.DisplayDesign(canvas, title, [sa_tools.SectionDesign(x.title, x.instances, x.columns)
                                                        for x in sections], level, symbol, device, replace)
        return change(solution, lambda s: sa_tools.build_display(s, design, technology), dry_run)

    # -- M5: .NET HMI canvases -------------------------------------------------------------------

    @mcp.tool(annotations=CREATE)
    def eae_hmi_canvas_create(name: str, resolution: str | None = None, title: str = "", dry_run: bool = True,
                              solution: str | None = None) -> dict:
        """Create a .NET HMI canvas (HMI/<name>.cnv.cs + .Designer.cs + .resx), registered in HMI.csproj
        and added to the top level of a canvas resolution (default: the first with a real size). Its
        size is the resolution's work area."""
        return change(solution, lambda s: dotnet_edit.create_canvas(s, name, resolution, title), dry_run)

    @mcp.tool(annotations=CREATE)
    def eae_hmi_place_symbol(canvas: str, instance: str, symbol: str | None = None, x: float | None = None,
                             y: float | None = None, name: str | None = None, application: str | None = None,
                             dry_run: bool = True, solution: str | None = None) -> dict:
        """Show a CAT instance of an application on a .NET HMI canvas using one of its symbols
        (default: the CAT's first symbol, e.g. sDefault). The object is named after the instance and
        bound to it by ID (TagName). x/y: position in canvas pixels."""
        return change(solution, lambda s: dotnet_edit.place_symbol(s, canvas, instance, symbol, x, y, name,
                                                                   application), dry_run)

    @mcp.tool(annotations=MODIFY)
    def eae_hmi_update_object(canvas: str, name: str, properties: dict[str, str | None] | None = None,
                              x: float | None = None, y: float | None = None, dry_run: bool = True,
                              solution: str | None = None) -> dict:
        """Change a .NET HMI canvas object. x/y move it. properties: C# values exactly as the Designer
        writes them, e.g. {"Visible": "false", "Text": "\"Pump 1\""}; null removes an assignment.
        Use the canvas name as `name` to change the canvas itself (e.g. Brush)."""
        return change(solution, lambda s: dotnet_edit.update_object(s, canvas, name, properties, x, y), dry_run)

    @mcp.tool(annotations=MODIFY)
    def eae_hmi_remove_object(canvas: str, name: str, dry_run: bool = True, solution: str | None = None) -> dict:
        """Remove an object (e.g. a placed CAT symbol) from a .NET HMI canvas."""
        return change(solution, lambda s: dotnet_edit.remove_object(s, canvas, name), dry_run)

    @mcp.tool(annotations=CREATE)
    def eae_subapp_create(name: str, application: str = "APP1", instance: str | None = None,
                          event_inputs: list[EventSpec] | None = None, event_outputs: list[EventSpec] | None = None,
                          dry_run: bool = True, solution: str | None = None) -> dict:
        """Create a SubApp inside an application (packaging of application content, not a reusable
        type). EAE stores its content in IEC61499/<name>/<name>.app; the application gets an instance
        (default name: NAME in upper case). Fill it with eae_net_* using network=<name>.
        Event pins only (data pins are not supported yet)."""
        itf = _interface(event_inputs, event_outputs, None, None)
        return change(solution, lambda s: network_edit.create_subapp(s, name, application, instance, itf), dry_run)

    @mcp.tool(annotations=CREATE)
    def eae_net_add_fb(network: str, name: str, type: str, namespace: str | None = None,
                       parameters: dict[str, str] | None = None, x: float | None = None, y: float | None = None,
                       generic_params: str | None = None, dry_run: bool = True,
                       solution: str | None = None) -> dict:
        """Add an FB/CAT instance to a network. type: solution type or system-library type
        (e.g. E_DELAY; run eae_catalog_build first). parameters: {input var: ST literal},
        e.g. {"DT": "T#1s", "Name": "'Pump1'"}. Position is chosen automatically unless x/y given.
        network: a Composite/CAT/SubApp type name, or an application ('APP1' or 'APP1/Layer').
        Generic FBs: type='VALFORMAT' with generic_params='I:=1;VALUE${I}:STRING' reuses the concrete
        type already used in the solution (see eae_generic_fbs).
        """
        return change(solution, lambda s: network_edit.add_fb(s, network, name, type, namespace, parameters, x, y,
                                                              generic_params), dry_run)

    @mcp.tool(annotations=MODIFY)
    def eae_net_remove_fb(network: str, name: str, force: bool = False, dry_run: bool = True,
                          solution: str | None = None) -> dict:
        """Remove an instance and all its connections. In an application this also removes its
        mapped resource copy; refused while it is shown on an HMI/eHMI canvas (unless force=true).
        network: a Composite/CAT/SubApp type, an application ('APP1', 'APP1/Layer') or a resource
        ('EcoRT_0/RES0')."""
        return change(solution, lambda s: network_edit.remove_fb(s, network, name, force), dry_run)

    @mcp.tool(annotations=CREATE)
    def eae_net_connect(network: str, source: str, destination: str, replace: bool = False,
                        dry_run: bool = True, solution: str | None = None) -> dict:
        """Connect two pins: 'Instance.Pin' or a boundary pin name of the enclosing type ('REQ').
        Kind (event/data/adapter) is inferred; direction is checked (reversed order is accepted).
        A data input accepts one source (replace=true replaces it). In an application, the
        connection is also copied into a resource when both FBs are mapped to it.
        network: a Composite/CAT/SubApp type, an application ('APP1', 'APP1/Layer') or a resource
        ('EcoRT_0/RES0')."""
        return change(solution, lambda s: network_edit.connect(s, network, source, destination, replace), dry_run)

    @mcp.tool(annotations=MODIFY)
    def eae_net_disconnect(network: str, source: str, destination: str, dry_run: bool = True,
                           solution: str | None = None) -> dict:
        """Remove a connection (and its resource copy for application networks).
        network: a Composite/CAT/SubApp type, an application ('APP1', 'APP1/Layer') or a resource
        ('EcoRT_0/RES0')."""
        return change(solution, lambda s: network_edit.disconnect(s, network, source, destination), dry_run)

    @mcp.tool(annotations=MODIFY)
    def eae_net_set_param(network: str, instance: str, var: str, value: str | None, dry_run: bool = True,
                          solution: str | None = None) -> dict:
        """Set (or clear with value=null) a parameter on an instance input. Values are ST literals:
        5, TRUE, T#1s, 'text'. Application parameters are synced to the mapped resource copy.
        network: a Composite/CAT/SubApp type, an application ('APP1', 'APP1/Layer') or a resource
        ('EcoRT_0/RES0')."""
        return change(solution, lambda s: network_edit.set_param(s, network, instance, var, value), dry_run)

    @mcp.tool(annotations=CREATE)
    def eae_map_to_resource(instance: str, resource: str, application: str | None = None, dry_run: bool = True,
                            solution: str | None = None) -> dict:
        """Map an application instance to a resource ('EcoRT_0/RES0'): creates the resource copy
        (new ID, Mapping=<application FB ID>, same parameters) and copies connections to FBs already
        mapped to the same resource. Cross-resource communication is not generated."""
        return change(solution, lambda s: network_edit.map_to_resource(s, instance, resource, application), dry_run)

    @mcp.tool(annotations=MODIFY)
    def eae_unmap(instance: str, application: str | None = None, dry_run: bool = True,
                  solution: str | None = None) -> dict:
        """Remove an application instance's resource copy (and its resource connections)."""
        return change(solution, lambda s: network_edit.unmap(s, instance, application), dry_run)

    @mcp.tool(annotations=READ_ONLY)
    def eae_validate(name: str | None = None, solution: str | None = None) -> dict:
        """Static checks: identifiers and reserved words, duplicate pins/IDs, WITH associations,
        ECC consistency, unresolved connections, multiple sources on a data input, unknown types,
        and project registration. Pass a type name, or omit to check the whole solution.
        This does not replace EAE's compiler (Tools › Check Changes)."""
        return run(services.validate, sol(solution), name)
