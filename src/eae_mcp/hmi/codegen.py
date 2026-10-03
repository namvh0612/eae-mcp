"""Generate a CAT's .NET HMI support code (<Cat>.event.cs, <Cat>.def.cs) the way EAE 26 does.

Output is identical to what EAE generates.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

from ..model import Interface

# IEC type → (.NET type, accessor, local var type, default)
TYPE_MAP = {
    "BOOL": ("System.Boolean", "GetBoolValue", "bool", "false"),
    "BYTE": ("System.Byte", "GetByteValue", "byte", "0"),
    "INT": ("System.Int16", "GetInt64Value", "System.Int64", "0"),
    "UINT": ("System.UInt16", "GetInt64Value", "System.Int64", "0"),
    "DINT": ("System.Int32", "GetInt64Value", "System.Int64", "0"),
    "UDINT": ("System.UInt32", "GetInt64Value", "System.Int64", "0"),
    "SINT": ("System.SByte", "GetInt64Value", "System.Int64", "0"),
    "USINT": ("System.Byte", "GetByteValue", "byte", "0"),
    "LINT": ("System.Int64", "GetInt64Value", "System.Int64", "0"),
    "REAL": ("System.Single", "GetFloatValue", "float", "0"),
    "LREAL": ("System.Double", "GetDoubleValue", "double", "0"),
    "STRING": ("System.String", "GetStringValue", "string", "null"),
}
NL = "\r\n"


class UnsupportedHmiType(ValueError):
    pass


def net_type(iec: str) -> tuple[str, str, str, str]:
    base = iec.split("[")[0].upper()
    if base not in TYPE_MAP:
        raise UnsupportedHmiType(f"HMI interface type {iec} is not supported for .NET HMI code generation yet.")
    return TYPE_MAP[base]


@dataclass
class SymbolRef:
    name: str
    is_faceplate: bool = False


def header(now: _dt.datetime | None = None) -> str:
    now = now or _dt.datetime.now()
    time = now.strftime("%I:%M %p").lstrip("0")
    return (f"/*{NL} * Created by EcoStruxure Automation Expert.{NL} * User:  {NL} * Date: {now.month}/{now.day}/{now.year}"
            f"{NL} * Time: {time}{NL} * {NL} */{NL}")


def ns_root(namespace: str | None) -> str:
    return "HMI.Main" if namespace in (None, "", "Main") else namespace


def _var_types(itf: Interface) -> dict[str, str]:
    return {v.name: v.type for v in itf.input_vars + itf.output_vars}


def _input_args(event: str, vars_: list[tuple[str, str]]) -> str:
    out = [f"  public class {event}EventArgs : System.EventArgs", "  {",
           "    IHMIAccessorService accessorService;", "    int channelId;", "    int cookie; ", "    int eventIndex;", "",
           f"    public {event}EventArgs(int channelId, int cookie, int eventIndex)", "    {",
           "      this.accessorService = (IHMIAccessorService)ServiceProvider.GetService(typeof(IHMIAccessorService));",
           "      this.channelId = channelId;", "      this.cookie = cookie;", "      this.eventIndex = eventIndex;", "    }"]
    for index, (name, iec) in enumerate(vars_):
        dotnet, getter, local, default = net_type(iec)
        out += [f"    public bool Get_{name}(ref {dotnet} value)", "    {", "      if (accessorService == null)",
                "        return false;", f"      {local} var = {default};",
                f"      bool ret = accessorService.{getter}(channelId, cookie, eventIndex, true,{index}, ref var);",
                f"      if (ret) value = ({dotnet}) var;", "      return ret;", "    }", "",
                f"    public {dotnet}? {name}" if dotnet != "System.String" else f"    public {dotnet} {name}",
                "    { get {", "      if (accessorService == null)", "        return null;", f"      {local} var = {default};",
                f"      bool ret = accessorService.{getter}(channelId, cookie, eventIndex, true,{index}, ref var);",
                "      if (!ret) return null;", f"      return ({dotnet}) var;", "    }  }", ""]
    out += ["", "  }", "", ""]
    return NL.join(out)


def _output_args(event: str, vars_: list[tuple[str, str]]) -> str:
    out = [f"  public class {event}EventArgs : System.EventArgs", "  {",
           f"    public {event}EventArgs()", "    {", "    }"]
    for name, iec in vars_:
        dotnet = net_type(iec)[0]
        nullable = dotnet if dotnet == "System.String" else f"{dotnet}?"
        out += [f"    private {nullable} {name}_field = null;", f"    public {nullable} {name}", "    {",
                f"       get {{ return {name}_field; }}", f"       set {{ {name}_field = value; }}", "    }"]
    out += ["", "  }", "", ""]
    return NL.join(out)


def _block(ns: str, classes: list[str]) -> str:
    return f"namespace {ns}{NL}{{{NL}{NL}" + "".join(classes) + f"}}{NL}{NL}"


def _symbol_part(sym_ns: str, args_ns: str, sym: str, inputs: list[tuple[str, list]], outputs: list[tuple[str, list]]) -> str:
    out = [f"namespace {sym_ns}", "{", f"  partial class {sym}", "  {", ""]
    for i, (event, _) in enumerate(inputs):
        out += [""] * (i > 0) + [f"    private event EventHandler<{args_ns}.{event}EventArgs> {event}_Fired;"]
    out += ["", "    protected override void OnEndInit()", "    {"]
    for i, (event, _) in enumerate(inputs):
        out += [f"      if ({event}_Fired != null)", f"        AttachEventInput({i});"]
    out += ["", "    }", "", "    protected override void FireEventCallback(int channelId, int cookie, int eventIndex)",
            "    {", "      switch(eventIndex)", "      {", "        default:", "          break;"]
    for i, (event, _) in enumerate(inputs):
        out += [f"        case {i}:", f"          if ({event}_Fired != null)", "          {", "            try", "            {",
                f"              {event}_Fired(this, new {args_ns}.{event}EventArgs(channelId, cookie, eventIndex));",
                "            }", "            catch (System.Exception e)", "            {",
                "              NxtControl.Services.LoggingService.ErrorFormatted(@\"In Event Callback for event:'{0}' "
                "Type:'{1}' CAT:'{2}' came exception:{3}",
                "stack Trace:", f"{{4}}\",\"{event}_Fired\", this.GetType().Name, this.CATName, e.Message, e.StackTrace);",
                "            }", "          }", "        break; "]
    out += [""] * bool(inputs) + ["      }", "    }"]
    for i, (event, vars_) in enumerate(outputs):
        if not vars_:
            out += [f"    public bool FireEvent_{event}()", "    {",
                    f"      return ((IHMIAccessorOutput)this).FireEvent({i}, new object[] {{}});", "    }"]
        else:
            params = ", ".join(f"{net_type(t)[0]} {n}" for n, t in vars_)
            out += [f"    public bool FireEvent_{event}({params})", "    {",
                    f"      return ((IHMIAccessorOutput)this).FireEvent({i}, new object[] {{{', '.join(n for n, _ in vars_)}}});",
                    "    }"]
        out += [f"    public bool FireEvent_{event}({args_ns}.{event}EventArgs ea)", "    {",
                f"      object[] _values_ = new object[{len(vars_)}];"]
        for j, (n, t) in enumerate(vars_):
            if net_type(t)[0] == "System.String":
                out.append(f"      if (ea.{n} != null) _values_[{j}] = ea.{n};")
            else:
                out.append(f"      if (ea.{n}.HasValue) _values_[{j}] = ea.{n}.Value;")
        out += [f"      return ((IHMIAccessorOutput)this).FireEvent({i}, _values_);", "    }"]
        if vars_:
            params = ", ".join(f"{net_type(t)[0]} {n}, bool ignore_{n}" for n, t in vars_)
            out += [f"    public bool FireEvent_{event}({params})", "    {",
                    f"      object[] _values_ = new object[{len(vars_)}];"]
            out += [f"      if (!ignore_{n}) _values_[{j}] = {n};" for j, (n, _) in enumerate(vars_)]
            out += [f"      return ((IHMIAccessorOutput)this).FireEvent({i}, _values_);", "    }"]
    out += ["", "  }", "}", ""]
    return NL.join(out)


def event_cs(cat: str, namespace: str | None, hmi_itf: Interface, symbols: list[SymbolRef],
             now: _dt.datetime | None = None) -> str:
    root = ns_root(namespace)
    args_ns = f"{root}.Symbols.{cat}"
    types = _var_types(hmi_itf)
    inputs = [(e.name, [(v, types.get(v, "STRING")) for v in e.with_vars])
              for e in hmi_itf.event_inputs if e.name != "INIT"]
    outputs = [(e.name, [(v, types.get(v, "STRING")) for v in e.with_vars])
               for e in hmi_itf.event_outputs if e.name != "INITO"]
    head = header(now) + NL.join(["using System;", "using NxtControl.GuiFramework;", "using NxtControl.Services;", "",
                                  "#region Definitions;", ""])
    if not inputs and not outputs:
        return head + f"{NL}#endregion Definitions;{NL}"
    text = head + f"#region #{cat}_HMI;{NL}{NL}"
    if inputs:
        text += _block(args_ns, [_input_args(e, v) for e, v in inputs])
    if outputs:
        text += _block(args_ns, [_output_args(e, v) for e, v in outputs])
    text += NL.join(_symbol_part(f"{root}.{'Faceplates' if s.is_faceplate else 'Symbols'}.{cat}", args_ns, s.name,
                                 inputs, outputs) for s in symbols)
    text += f"#endregion #{cat}_HMI;{NL}{NL}#endregion Definitions;{NL}"
    return text


def _faceplate_accessor(fp_ns: str, fp: str, from_faceplate: bool) -> list[str]:
    cls = f"{fp_ns}.{fp}"
    info = ("this.TagName, this.ConnectionSymbolPath, this.ChannelId, this.ParentType" if from_faceplate
            else "this.TagName, this.SymbolPath, this.ChannelId, GetType()")
    return [f"    private {cls} {fp}", "    {", "      get", "      { ", "        if (IsOpenFaceplateSecure() == false)",
            "          return null;", "", f"        {cls} faceplate = null;", "        ",
            "        IHMIManagementService hmiManagementService = (IHMIManagementService)ServiceProvider.GetService("
            "typeof(IHMIManagementService));", "        if (hmiManagementService != null)",
            f"          faceplate = ({cls})hmiManagementService.GetRegisteredHMIFaceplate(MapPath, typeof({cls}));",
            "        ", "        if (faceplate == null)", "        {", f"          faceplate = new {cls}();", "",
            f"          faceplate.SetConnectionInfo({info});", "", "          if (hmiManagementService != null)",
            "            hmiManagementService.RegisterHMIFaceplate(faceplate);", "        }", "        return faceplate;",
            "      }", "    }", "     "]


def _open_faceplate(signature: str, key: str, faceplates: list[str]) -> list[str]:
    out = [f"    {signature}", "    {", "      NxtControl.GuiFramework.HMIFaceplate hmiFaceplate = null;", ""]
    for fp in faceplates:
        out += [f"      if (\"{fp}\" == {key})", f"        hmiFaceplate = {fp};", ""]
    return out + ["      if (hmiFaceplate != null)", "      {", "        if (hmiFaceplate.Initialized == true)",
                  "          hmiFaceplate.Activate();", "        else", "        {",
                  "          OnInitializeFaceplate(hmiFaceplate);", "          hmiFaceplate.Show(this);", "        }",
                  "      }", "    }"]


def def_cs(cat: str, namespace: str | None, symbols: list[SymbolRef], now: _dt.datetime | None = None) -> str:
    """Faceplate accessors for each symbol; just the region markers when the CAT has no faceplates."""
    root = ns_root(namespace)
    faceplates = [s.name for s in symbols if s.is_faceplate]
    usings = header(now) + NL.join(["using System;", "using NxtControl.GuiFramework;", "using NxtControl.Services;", ""])
    if not faceplates:
        return usings + NL.join(["", "#region Definitions;", "", "#endregion Definitions;", ""])
    fp_ns = f"{root}.Faceplates.{cat}"
    blocks = []
    for s in symbols:
        others = [f for f in faceplates if f != s.name]
        out = [f"namespace {root}.{'Faceplates' if s.is_faceplate else 'Symbols'}.{cat}", "{", f"  partial class {s.name}",
               "  {", ""]
        for fp in others:
            out += _faceplate_accessor(fp_ns, fp, s.is_faceplate)
        out += _open_faceplate("protected override void DoOpenFaceplate(OpenFaceplate openFaceplate)",
                               "(string)openFaceplate.FaceplateType", others)
        out += [""] + _open_faceplate("public override void DoOpenFaceplate(string openFaceplate)", "openFaceplate", others)
        out += ["", "  }", "}", ""]
        blocks.append(NL.join(out))
    return (usings + NL.join(["", "", "#region Definitions;", f"#region {cat}_HMI;", "", ""]) + NL.join(blocks)
            + NL.join([f"#endregion {cat}_HMI;", "", "#endregion Definitions;", "", "", ""]))
