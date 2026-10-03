"""A constrained code model of EAE's `*.cnv.Designer.cs` (.NET HMI canvases, symbols, faceplates).

`InitializeComponent()` always has the same shape:

    <prelude: optional ComponentResourceManager line, then `this.X = new T();` per object>
    //
    // X
    //
    <property assignments of X, sorted by property name; BeginInit()/EndInit() around symbols>
    ...
    //
    // <ClassName>
    //
    <the class's own properties; `this.Shapes.AddRange(new System.ComponentModel.IComponent[] {` lists objects>
    <empty line>

followed by one `private T X;` field per object. Only this region is edited; everything else in the file
is kept byte for byte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

NL = "\r\n"
T3 = "\t\t\t"
_HEAD = re.compile(r"\t\tprivate void InitializeComponent\(\)\r\n\t\t\{\r\n")
_SECTION = re.compile(r"^\t\t\t// \r\n\t\t\t// (\S+)\r\n\t\t\t// (?=\r\n)", re.M)
_FIELD = re.compile(r"^\t\tprivate (.+) (\w+);$")


class DesignerError(ValueError):
    pass


@dataclass
class Section:
    name: str
    lines: list[str] = field(default_factory=list)

    def prop_lines(self) -> list[tuple[str, int]]:
        """(property name, line index) for single-line `this.<name>.<Prop> = …;` assignments."""
        prefix = "this." if self.is_self else f"this.{self.name}."
        out = []
        for i, line in enumerate(self.lines):
            s = line.strip()
            if s.startswith(prefix):
                m = re.match(r"(\w+)", s[len(prefix):])
                if m:
                    out.append((m.group(1), i))
        return out

    is_self: bool = False


@dataclass
class Designer:
    before: str  # file text up to and including `{` of InitializeComponent
    prelude: list[str]
    sections: list[Section]
    fields: list[str]  # raw field lines
    after: str
    lead: int = 0  # empty lines between InitializeComponent() and the fields (some files have one)  # from `\t\t#endregion` (or whatever follows the fields) to the end

    @property
    def objects(self) -> list[str]:
        return [s.name for s in self.sections if not s.is_self]

    def own(self) -> Section:
        return self.sections[-1]


def parse(text: str) -> Designer:
    m = _HEAD.search(text)
    if not m:
        raise DesignerError("No InitializeComponent() in this Designer file.")
    start = m.end()
    end = text.find(f"{NL}\t\t}}{NL}", start)
    if end < 0:
        raise DesignerError("InitializeComponent() is not closed the way EAE writes it.")
    body = text[start:end]
    rest = text[end + len(f"{NL}\t\t}}{NL}"):]
    fields = []
    lines = rest.split(NL)
    i = 0
    while i < len(lines) - 1 and lines[i] == "" and _FIELD.match(lines[i + 1]):
        i += 1
    lead = i
    while i < len(lines) and _FIELD.match(lines[i]):
        fields.append(lines[i])
        i += 1
    after = NL.join(lines[i:])
    marks = list(_SECTION.finditer(body))
    if not marks:
        raise DesignerError("InitializeComponent() has no sections.")
    prelude_text = body[: marks[0].start()]
    prelude = prelude_text.split(NL)[:-1] if prelude_text else []
    sections = []
    for k, mk in enumerate(marks):
        chunk_end = marks[k + 1].start() if k + 1 < len(marks) else len(body)
        chunk = body[mk.end() + len(NL): chunk_end]
        sec_lines = chunk.split(NL)
        if k + 1 < len(marks):
            sec_lines = sec_lines[:-1]  # the chunk ends with the newline before the next marker
        sections.append(Section(mk.group(1), sec_lines))
    sections[-1].is_self = True
    d = Designer(text[:start], prelude, sections, fields, after, lead)
    if dumps(d) != text:
        raise DesignerError("Designer file layout is not the one EAE writes; refusing to edit it.")
    return d


def dumps(d: Designer) -> str:
    body = "".join(line + NL for line in d.prelude)
    parts = []
    for s in d.sections:
        parts.append(f"{T3}// {NL}{T3}// {s.name}{NL}{T3}// {NL}" + NL.join(s.lines))
    body += NL.join(parts)
    rest = NL * d.lead + "".join(f + NL for f in d.fields) + d.after
    return d.before + body + f"{NL}\t\t}}{NL}" + rest


# -- edits -----------------------------------------------------------------------------------


def _shapes_range(sec: Section) -> tuple[int, int] | None:
    for i, line in enumerate(sec.lines):
        if line.strip() == "this.Shapes.AddRange(new System.ComponentModel.IComponent[] {":
            j = i + 1
            while j < len(sec.lines) and not sec.lines[j].rstrip().endswith("});"):
                j += 1
            return i, j
    return None


def _insert_sorted(sec: Section, prop: str, new_lines: list[str]) -> None:
    """Insert assignment lines at the alphabetical position EAE uses (inside BeginInit/EndInit)."""
    props = [(p, i) for p, i in sec.prop_lines() if p not in ("BeginInit", "EndInit")]
    index = None
    for p, i in props:
        if p.lower() > prop.lower():
            index = i
            break
    if index is None:
        end_init = [i for p, i in sec.prop_lines() if p == "EndInit"]
        if end_init:
            index = end_init[0]
        elif props:
            last = props[-1][1]
            rng = _shapes_range(sec)
            index = (rng[1] + 1) if rng and rng[0] == last else last + 1
        else:
            index = len(sec.lines)
            while index > 0 and sec.lines[index - 1] == "":
                index -= 1
    sec.lines[index:index] = new_lines


def add_object(d: Designer, name: str, type_: str, properties: list[tuple[str, str]], begin_init: bool = True,
               resources: bool = False) -> None:
    if name in d.objects or name == d.own().name:
        raise DesignerError(f"An object named '{name}' already exists.")
    prelude_new = [i for i, line in enumerate(d.prelude) if re.match(r"\t\t\tthis\.\w+ = new ", line)]
    at = prelude_new[-1] + 1 if prelude_new else len(d.prelude)
    d.prelude.insert(at, f"{T3}this.{name} = new {type_}();")
    lines = [f"{T3}this.{name}.BeginInit();"] if begin_init else []
    lines += [f"{T3}this.{name}.{p} = {v};" for p, v in sorted(properties, key=lambda pv: pv[0].lower())]
    if begin_init:
        lines.append(f"{T3}this.{name}.EndInit();")
    d.sections.insert(len(d.sections) - 1, Section(name, lines))
    own = d.own()
    rng = _shapes_range(own)
    if rng:
        last = own.lines[rng[1]]
        own.lines[rng[1]] = last[: -len("});")] + ","
        own.lines.insert(rng[1] + 1, f"{T3}this.{name}}});")
    else:
        _insert_sorted(own, "Shapes", [f"{T3}this.Shapes.AddRange(new System.ComponentModel.IComponent[] {{",
                                       f"{T3}this.{name}}});"])
    d.fields.append(f"\t\tprivate {type_} {name};")


def remove_object(d: Designer, name: str) -> None:
    if name not in d.objects:
        raise DesignerError(f"No object '{name}'. Objects: {', '.join(d.objects) or 'none'}.")
    d.prelude = [line for line in d.prelude if not line.startswith(f"{T3}this.{name} = new ")]
    d.sections = [s for s in d.sections if s.is_self or s.name != name]
    own = d.own()
    rng = _shapes_range(own)
    if rng:
        items = [line.strip().rstrip(",").removesuffix("});") for line in own.lines[rng[0] + 1: rng[1] + 1]]
        items = [x for x in items if x != f"this.{name}"]
        if items:
            new = [f"{T3}{x}," for x in items]
            new[-1] = new[-1][:-1] + "});"
            own.lines[rng[0] + 1: rng[1] + 1] = new
        else:
            del own.lines[rng[0]: rng[1] + 1]
    d.fields = [f for f in d.fields if not re.match(rf"\t\tprivate .+ {re.escape(name)};$", f)]
    for s in d.sections:  # other objects must not keep pointing at it
        if any(f"this.{name}" in line for line in s.lines):
            raise DesignerError(f"'{name}' is still referenced by {s.name}; edit it in EAE.")


def set_property(d: Designer, name: str, prop: str, value: str | None) -> None:
    """Set (or with value=None remove) a single-line property assignment of an object or of the class itself."""
    sec = d.own() if name == d.own().name else next((s for s in d.sections if s.name == name), None)
    if sec is None:
        raise DesignerError(f"No object '{name}'. Objects: {', '.join(d.objects) or 'none'}.")
    if not re.match(r"^\w+$", prop) or prop in ("BeginInit", "EndInit", "Shapes"):
        raise DesignerError(f"'{prop}' cannot be set with this tool.")
    prefix = "this." if sec.is_self else f"this.{name}."
    hits = [i for p, i in sec.prop_lines() if p == prop]
    if hits:
        i = hits[0]
        if not sec.lines[i].rstrip().endswith(";"):
            raise DesignerError(f"{name}.{prop} spans several lines; edit it in EAE.")
        if value is None:
            del sec.lines[i]
        else:
            sec.lines[i] = f"{T3}{prefix}{prop} = {value};"
        return
    if value is not None:
        _insert_sorted(sec, prop, [f"{T3}{prefix}{prop} = {value};"])


# -- value helpers ---------------------------------------------------------------------------


def num(x: float) -> str:
    return f"{x:g}D" if "e" not in f"{x:g}" else f"{x}D"


def matrix(x: float, y: float, sx: float = 1, sy: float = 1) -> str:
    return f"new NxtControl.Drawing.Matrix2D({num(sx)}, 0D, 0D, {num(sy)}, {num(x)}, {num(y)})"


def string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
