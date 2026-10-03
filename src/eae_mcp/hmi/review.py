"""Review .NET HMI and eHMI displays against situation-awareness / high-performance HMI practice.

Sources (see knowledge/hmi-design.md): ANSI/ISA-101.01 (HMI lifecycle, display hierarchy), the ASM
Consortium display guidelines, The High Performance HMI Handbook (Hollifield et al.), ISA-18.2 / EEMUA 191
for alarms, Endsley's three levels of situation awareness, WCAG 2 contrast ratio for text.

Only what the display files show can be checked: static colors, fonts, images, object counts, bound values,
navigation depth. Colors set at runtime in code-behind are invisible here, so color findings are about
*static* use. Every finding names its rule so a site style guide can override it.
"""

from __future__ import annotations

import colorsys
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..io import xmlrt
from ..project.solution import Solution
from ..project.types import children

RGB_CS = re.compile(r"Color\(\(\(byte\)\((\d+)\)\), \(\(byte\)\((\d+)\)\), \(\(byte\)\((\d+)\)\)")
NAMED_CS = re.compile(r'new NxtControl\.Drawing\.(Brush|Pen|Color)\("([^"]+)"\)')
FONT_CS = re.compile(r'Font\("([^"]+)", ([\d.]+)F')
NEW_CS = re.compile(r"^\s*this\.(\w+) = new ([\w.<>]+)\(", re.M)
PROP_CS = re.compile(r"^\s*this\.(?:(\w+)\.)?(\w+) = (.*);\s*$", re.M)

# Indicator families by type name.
ANALOG = re.compile(r"Bar|Gauge|Tracker|Slider|Meter|Analog|Level", re.I)
TREND = re.compile(r"Trend|Chart|Sparkline", re.I)
NUMERIC = re.compile(r"Label|TextBox|Text|Value|Indication", re.I)
BRIDGE = re.compile(r"\.(s|se)Val(Changed|Control)$")

DENSITY = {1: 80, 2: 150, 3: 250, 4: 400}  # objects per display before a warning (heuristic per level)
ALARM_HUES = {"red", "orange", "yellow", "magenta"}


@dataclass
class Finding:
    rule: str
    severity: str  # warning | info
    message: str
    reference: str
    evidence: list[str] = field(default_factory=list)


@dataclass
class ColorUse:
    rgb: tuple[int, int, int] | None
    token: str | None
    role: str  # fill | text | line | background
    owner: str


@dataclass
class Display:
    name: str
    technology: str
    path: str
    objects: dict[str, str] = field(default_factory=dict)  # name → type
    bound: dict[str, str] = field(default_factory=dict)  # name → tag
    colors: list[ColorUse] = field(default_factory=list)
    fonts: list[tuple[str, float]] = field(default_factory=list)
    images: int = 0
    background: ColorUse | None = None
    text_pairs: list[tuple[str, ColorUse, ColorUse]] = field(default_factory=list)  # owner, text, fill


# -- color math ----------------------------------------------------------------------------------


def hue_name(rgb: tuple[int, int, int]) -> str:
    r, g, b = (x / 255 for x in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s < 0.25 or v < 0.2:
        return "gray"
    deg = h * 360
    for limit, name in ((15, "red"), (40, "orange"), (70, "yellow"), (165, "green"), (200, "cyan"),
                        (260, "blue"), (330, "magenta"), (361, "red")):
        if deg < limit:
            return name
    return "red"


def saturated(rgb: tuple[int, int, int]) -> bool:
    _, s, v = colorsys.rgb_to_hsv(*(x / 255 for x in rgb))
    return s >= 0.35 and v >= 0.35


def luminance(rgb: tuple[int, int, int]) -> float:
    def ch(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# -- theme ---------------------------------------------------------------------------------------


def load_theme(sol: Solution) -> dict[str, tuple[int, int, int]]:
    """Named colors/brushes of the active .NET HMI theme (HMI/Colors/<theme>.color.theme / .do.theme)."""
    out: dict[str, tuple[int, int, int]] = {}
    for proj in sol.projects_of("hmi"):
        pdir = sol.root / proj.path.rsplit("/", 1)[0]
        text = (pdir / Path(proj.path).name).read_text(encoding="utf-8-sig", errors="replace") \
            if (pdir / Path(proj.path).name).exists() else ""
        m = re.search(r"<Theme>([^:<]+)", text)
        names = [m.group(1)] if m else []
        names += ["Default"]
        for theme in names:
            for suffix in (".color.theme", ".do.theme"):
                path = pdir / "Colors" / f"{theme}{suffix}"
                if not path.exists():
                    continue
                try:
                    root = xmlrt.load(path).root
                except Exception:  # noqa: BLE001 - a broken theme must not stop a review
                    continue
                for el in children(root):
                    name = el.get("Name")
                    if not name or name in out:
                        continue
                    if el.get("R") is not None:
                        out[name] = (int(el.get("R")), int(el.get("G")), int(el.get("B")))
                    else:
                        value = "".join(el.itertext())
                        mm = re.search(r"Solid;\[(\d+), (\d+), (\d+)\]", value)
                        if mm:
                            out[name] = tuple(int(x) for x in mm.groups())  # type: ignore[assignment]
        break
    # Built-in EAE tokens that themes usually do not redefine.
    out.setdefault("CanvasBackColor", (230, 230, 230))
    out.setdefault("CanvasBrush", out["CanvasBackColor"])
    # EAE defaults (Definitions.ts of a built WEB project): FaceplateBrush = FaceplateBackColor = 230 gray.
    out.setdefault("FaceplateBackColor", (230, 230, 230))
    out.setdefault("FaceplateBrush", out["FaceplateBackColor"])
    return out


# -- extraction ----------------------------------------------------------------------------------


def _role(prop: str) -> str:
    p = prop.lower()
    if "text" in p or "font" in p or "fore" in p:
        return "text"
    if "pen" in p or "line" in p or "border" in p:
        return "line"
    return "fill"


def extract_dotnet(name: str, path: str, text: str) -> Display:
    d = Display(name, "hmi", path)
    for obj, typ in NEW_CS.findall(text):
        if obj != "components":
            d.objects[obj] = typ
    per_obj: dict[str, dict[str, ColorUse]] = {}
    for obj, prop, value in PROP_CS.findall(text):
        owner = obj or name
        if prop == "TagName" and obj:
            d.bound[obj] = value.strip('"')
        if prop == "ImageBytes" or "Image" in prop and "resources.Get" in value:
            d.images += 1
        for fam, size in FONT_CS.findall(value):
            d.fonts.append((fam, float(size)))
        uses = [ColorUse(tuple(int(x) for x in m), None, _role(prop), owner) for m in RGB_CS.findall(value)]
        uses += [ColorUse(None, tok, _role(prop) if kind != "Pen" else "line", owner)
                 for kind, tok in NAMED_CS.findall(value)]
        for u in uses:
            if not obj and prop in ("Brush", "BackColor"):
                u.role = "background"
                d.background = u
            d.colors.append(u)
            per_obj.setdefault(owner, {}).setdefault(u.role, u)
    for owner, roles in per_obj.items():
        if "text" in roles and "fill" in roles:
            d.text_pairs.append((owner, roles["text"], roles["fill"]))
    return d


def _json_color(value) -> tuple[tuple[int, int, int] | None, str | None]:
    if isinstance(value, list) and len(value) >= 3 and all(isinstance(x, (int, float)) for x in value[:3]):
        if len(value) > 3 and value[3] == 0:
            return None, "Transparent"
        return (int(value[0]), int(value[1]), int(value[2])), None
    if isinstance(value, str):
        return None, value
    return None, None


def extract_ehmi(name: str, path: str, data: dict) -> Display:
    d = Display(name, "ehmi", path)
    design = data.get("_design_", data)
    rgb, tok = _json_color(design.get("background"))
    if rgb or tok:
        d.background = ColorUse(rgb, tok, "background", name)
        d.colors.append(d.background)
    for o in data.get("objects", []):
        obj = o.get("name", "?")
        d.objects[obj] = o.get("type", "")
        if o.get("tagName"):
            d.bound[obj] = o["tagName"]
        if o.get("src") or "Image" in o.get("type", ""):
            d.images += 1
        if o.get("fontSize"):
            d.fonts.append((o.get("fontFamily", ""), float(o["fontSize"])))
        found: dict[str, ColorUse] = {}
        for key, value, role in (("brush", (o.get("brush") or {}).get("color"), "fill"),
                                 ("backColor", o.get("backColor"), "fill"),
                                 ("pen", (o.get("pen") or {}).get("color"), "line"),
                                 ("textColor", o.get("textColor"), "text")):
            rgb, tok = _json_color(value)
            if rgb or tok:
                u = ColorUse(rgb, tok, role, obj)
                d.colors.append(u)
                found.setdefault(role, u)
        if "text" in found and "fill" in found:
            d.text_pairs.append((obj, found["text"], found["fill"]))
    return d


# -- rules ---------------------------------------------------------------------------------------


def _resolve(u: ColorUse, theme: dict) -> tuple[int, int, int] | None:
    if u.rgb:
        return u.rgb
    if u.token and u.token != "Transparent":
        return theme.get(u.token)
    return None


def review_display(d: Display, theme: dict, level: int | None = None) -> list[Finding]:
    f: list[Finding] = []
    # HP-01 background
    if d.background is not None:
        rgb = _resolve(d.background, theme)
        if rgb is None:
            f.append(Finding("HP-01", "info", f"Background uses theme token '{d.background.token}' that is not "
                             "defined in the project theme; check it is a neutral gray.", "ASM/ISA-101: gray background"))
        elif saturated(rgb):
            f.append(Finding("HP-01", "warning", f"Background {rgb} is a saturated {hue_name(rgb)}; use a neutral "
                             "light/medium gray so colored abnormal states stand out.", "ASM/ISA-101: gray background"))
        elif luminance(rgb) > 0.9:
            f.append(Finding("HP-01", "warning", f"Background {rgb} is near white (glare in control rooms); "
                             "prefer a light gray (e.g. 200–230).", "ASM consortium: background luminance"))
    # HP-02/03/04 static color use
    sat = [(u, _resolve(u, theme)) for u in d.colors if u.role != "background"]
    sat = [(u, rgb) for u, rgb in sat if rgb and saturated(rgb)]
    if sat:
        hues: dict[str, set[str]] = {}
        for u, rgb in sat:
            hues.setdefault(hue_name(rgb), set()).add(u.owner)
        summary = ", ".join(f"{h}×{len(o)}" for h, o in sorted(hues.items(), key=lambda kv: -len(kv[1])))
        f.append(Finding("HP-02", "warning" if len({u.owner for u, _ in sat}) > 3 else "info",
                         f"{len({u.owner for u, _ in sat})} objects use saturated colors statically ({summary}). "
                         "Reserve color for abnormal conditions and alarms; draw normal equipment, pipes and "
                         "running/stopped states in grays (state as text or shape).",
                         "High Performance HMI: color for abnormal only",
                         sorted({u.owner for u, _ in sat})[:12]))
        if len(hues) > 4:
            f.append(Finding("HP-03", "warning", f"{len(hues)} different saturated hues on one display; a limited, "
                             "consistent palette (each color one meaning) is easier to learn.", "ASM: color palette"))
        alarmish = {h: o for h, o in hues.items() if h in ALARM_HUES}
        if alarmish:
            f.append(Finding("HP-04", "warning", "Alarm colors (" + ", ".join(sorted(alarmish)) + ") are used "
                             "statically. Keep red/orange/yellow/magenta exclusively for alarm priorities so they "
                             "keep their meaning, and pair them with a shape and priority text.",
                             "ISA-18.2/ISA-101: alarm color exclusivity, redundant coding",
                             sorted({x for o in alarmish.values() for x in o})[:12]))
    # HP-05 images
    if d.images:
        f.append(Finding("HP-05", "warning" if d.images > 5 else "info",
                         f"{d.images} bitmap images/photos. Detailed or 3D pictures add clutter without information; "
                         "use simple 2D outlines and put the attention on values and deviations.",
                         "High Performance HMI: no 3D/photo-realistic graphics"))
    # HP-06 typography
    sizes = sorted({s for _, s in d.fonts})
    fams = sorted({fam for fam, _ in d.fonts if fam})
    if len(sizes) > 4:
        f.append(Finding("HP-06", "warning", f"{len(sizes)} font sizes ({', '.join(f'{s:g}' for s in sizes)}); "
                         "use a small fixed set (e.g. title, label, value).", "ASM: typography consistency"))
    if len(fams) > 2:
        f.append(Finding("HP-06", "warning", f"{len(fams)} font families ({', '.join(fams)}); use one or two.",
                         "ASM: typography consistency"))
    if sizes and sizes[0] < 8:
        f.append(Finding("HP-06", "warning", f"Smallest font {sizes[0]:g} pt is hard to read at console distance.",
                         "ISA-101: legibility"))
    # HP-07 contrast
    low = []
    for owner, t, b in d.text_pairs:
        a, c = _resolve(t, theme), _resolve(b, theme)
        if a and c and contrast(a, c) < 4.5:
            low.append(f"{owner} ({contrast(a, c):.1f}:1)")
    if low:
        f.append(Finding("HP-07", "warning", f"{len(low)} text objects have contrast below 4.5:1 against their fill.",
                         "WCAG 2 contrast (legibility)", low[:12]))
    # HP-08 density
    limit = DENSITY.get(level or 0, 200)
    if len(d.objects) > limit:
        f.append(Finding("HP-08", "warning", f"{len(d.objects)} objects (guide for "
                         f"{'level ' + str(level) if level else 'an operating display'}: ≤ {limit}). Move detail to a "
                         "lower-level display or a faceplate.", "ISA-101: display hierarchy and density"))
    # HP-09 values without context / trends
    # Agile bridge symbols (HMI_Indication_Real.sValChanged, seValChanged/seValControl) are invisible data
    # sources, not number displays.
    numeric = [o for o in d.bound if NUMERIC.search(d.objects.get(o, "")) and not ANALOG.search(d.objects.get(o, ""))
               and not BRIDGE.search(d.objects.get(o, ""))]
    analog = [o for o, t in d.objects.items() if ANALOG.search(t)]
    # Code-driven moving indicators (eae-mcp SA symbols: span "trk…" + pointer "ptr…") count as analog.
    analog += [o for o in d.objects if o.startswith("ptr") and f"trk{o[3:]}" in d.objects]
    trends = [o for o, t in d.objects.items() if TREND.search(t)]
    if len(numeric) >= 4 and not analog:
        f.append(Finding("HP-09", "info", f"{len(numeric)} bound values are shown as numbers only. Show key values "
                         "with moving analog indicators that mark the normal range and alarm limits, so deviation "
                         "is seen without reading digits (SA level 2: comprehension).",
                         "High Performance HMI: analog indicators"))
    if level in (1, 2) and not trends:
        f.append(Finding("HP-10", "info", "No embedded trend on an overview/unit display. Short trends of key "
                         "values show direction and rate of change (SA level 3: projection).",
                         "High Performance HMI: embedded trends"))
    return f


def review_navigation(sol: Solution, technology: str | None = None) -> list[Finding]:
    """Canvas hierarchy from the resolution lists: depth and breadth versus the ISA-101 four-level model."""
    out = []
    for proj in sol.projects_of("hmi") + sol.projects_of("web"):
        tech = "hmi" if proj.kind == "hmi" else "ehmi"
        if technology and technology != tech:
            continue
        pdir = sol.root / proj.path.rsplit("/", 1)[0]
        files = list(pdir.glob("CanvasesResolutionList.xml")) + list(pdir.glob("*/WebCanvasesResolutionList.xml"))
        for path in files:
            try:
                root = xmlrt.load(path).root
            except Exception:  # noqa: BLE001
                continue
            for res in children(root, "CanvasResolution"):
                canvases = [c for c in res.iter() if isinstance(c.tag, str) and c.tag.endswith("}Canvas")]
                if not canvases:
                    continue
                top = [c for c in canvases if c.getparent() is not None and c.getparent().tag.endswith("Canvases")]
                levels = 1 + max((len([a for a in c.iterancestors() if a.tag.endswith("}Canvas")]) for c in canvases),
                                 default=0)
                label = f"{tech} {res.get('Name')} ({path.relative_to(sol.root).as_posix()})"
                if levels > 4:
                    out.append(Finding("NAV-01", "warning", f"{label}: canvas tree is {levels} levels deep; "
                                       "ISA-101 uses four levels (overview, unit, detail, diagnostic).",
                                       "ISA-101: display hierarchy"))
                if len(top) > 12:
                    out.append(Finding("NAV-02", "info", f"{label}: {len(top)} top-level canvases; group them under "
                                       "a few overview/unit displays so any display is reachable in ≤ 3 clicks.",
                                       "ISA-101: navigation"))
                if levels == 1 and len(canvases) > 3:
                    out.append(Finding("NAV-03", "info", f"{label}: {len(canvases)} canvases on one flat level; a "
                                       "level-1 overview that links to unit displays supports situation awareness.",
                                       "ISA-101: display hierarchy"))
    return out


MANUAL_CHECKS = [
    "SA level 1 (perception): abnormal states are visible at a glance from the overview (color + shape + text).",
    "SA level 2 (comprehension): key values show where they are versus normal range and limits, with units.",
    "SA level 3 (projection): trends or rate-of-change show where the process is heading.",
    "Alarm indicators combine color, shape and priority number/text (usable with color-vision deficiency).",
    "Alarm rate fits ISA-18.2/EEMUA 191 targets (≈1 alarm per 10 min per operator in steady state).",
    "Navigation is identical on every display; any display is reachable in ≤ 3 clicks.",
    "Controls (start/stop, setpoints) are separated from indications and confirm before acting.",
    "Each color has one documented meaning in the site HMI style guide (ISA-101 philosophy & style guide).",
]


def to_dict(fs: list[Finding]) -> list[dict]:
    return [{k: v for k, v in vars(x).items() if v not in (None, [], "")} for x in fs]


def load_display(sol: Solution, doc) -> Display | None:
    path = sol.root / doc.path
    if doc.technology == "hmi":
        designer = path.with_name(path.name.replace(".cnv.cs", ".cnv.Designer.cs"))
        if not designer.exists():
            return None
        return extract_dotnet(doc.name, doc.path, designer.read_text(encoding="utf-8-sig", errors="replace"))
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return extract_ehmi(doc.name, doc.path, data)


def review_alarm_classes(sol: Solution, theme: dict) -> tuple[list[dict], list[Finding]]:
    """HMI/Alarms/{SystemAlarmClasses,AlarmClasses}.xml against ISA-18.2 practice."""
    classes = []
    for proj in sol.projects_of("hmi"):
        adir = sol.root / proj.path.rsplit("/", 1)[0] / "Alarms"
        for fname in ("SystemAlarmClasses.xml", "AlarmClasses.xml"):
            path = adir / fname
            if not path.exists():
                continue
            try:
                root = xmlrt.load(path).root
            except Exception:  # noqa: BLE001
                continue
            for c in children(root, "Class"):
                states = {s.get("State"): s.get("Color") for s in children(c, "State")}
                short = next((s.get("Text") for s in children(c, "Shortcut")), None)
                classes.append({"name": c.get("Name"), "priority": int(c.get("Prio", "0") or 0), "states": states,
                                "shortcut": short, "file": path.relative_to(sol.root).as_posix()})
    out: list[Finding] = []
    if not classes:
        return classes, out
    if len(classes) > 5:
        out.append(Finding("ALM-01", "warning", f"{len(classes)} alarm classes; ISA-18.2 practice is 3–4 priorities "
                           "so the operator can tell them apart and the distribution stays meaningful.",
                           "ISA-18.2: alarm prioritization"))
    came = {}
    for c in classes:
        if not c["shortcut"]:
            out.append(Finding("ALM-02", "warning", f"Alarm class {c['name']} has no shortcut text; pair color with a "
                               "letter/number (and shape) for color-vision deficient operators.",
                               "ISA-101/ISA-18.2: redundant coding"))
        st = c["states"]
        if st.get("Came") and st.get("Came") == st.get("CameNA"):
            out.append(Finding("ALM-03", "warning", f"Alarm class {c['name']}: unacknowledged and acknowledged alarms "
                               "look the same; unacknowledged alarms must be distinguishable (e.g. blinking).",
                               "ISA-18.2: alarm states"))
        if st.get("Came"):
            came.setdefault(st["Came"], []).append(c["name"])
    for color, names in came.items():
        if len(names) > 1:
            out.append(Finding("ALM-04", "warning", f"Alarm classes {', '.join(names)} share the color {color}; each "
                               "priority needs its own color.", "ISA-18.2: priority indication"))
    prios = [c["priority"] for c in classes]
    if len(set(prios)) < len(prios):
        out.append(Finding("ALM-05", "info", "Several alarm classes have the same Prio value.", "ISA-18.2"))
    return classes, out
