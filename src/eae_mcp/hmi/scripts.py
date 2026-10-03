"""HMI support classes (`*.spt.cs`, `<SupportClass>true</SupportClass>` in HMI.csproj) and alarm-word profiles.

Support classes are plain C# shared by symbols and canvases (themes, helpers, event logs). eae-mcp lists them and
reads one common convention: **alarm-word profiles**. The logic packs alarm bits into a WORD
(`AlarmWord.3 := <condition>;`) and publishes it through an `HMI_Indication_Integer` block (`ALMW.Output :=
WORD_TO_INT(AlarmWord)`); a support class maps each bit to texts and a priority:

    public static readonly AlarmDefinition[] Inverter = { Alarm(0, "Inverter fault", "Inverter fault cleared"), … };
    private static AlarmDefinition Alarm(int bit, string activeText, string clearText) { … EventPriority.Alarm … }

`alarm_profiles` cross-checks both sides (bits set by the logic ↔ bits described in the profile) and reviews the
profiles against ISA-18.2 / EEMUA 191 (unique meaningful texts, priority distribution).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from ..io import xmlrt
from ..project.solution import Solution
from ..project.types import children, local

ARRAY = re.compile(r"(?:public|internal|private)?\s*static\s+readonly\s+(\w+)\s*\[\]\s+(\w+)\s*=\s*\{(.*?)\};", re.S)
ENTRY = re.compile(r"\b(\w+)\s*\(\s*(\d+)\s*,\s*\"((?:[^\"\\]|\\.)*)\"\s*,\s*\"((?:[^\"\\]|\\.)*)\"")
HELPER = re.compile(r"static\s+\w+\s+(\w+)\s*\(\s*int\s+\w+\s*,[^)]*\)\s*\{(.*?)\n\t*\}", re.S)
PRIORITY = re.compile(r"\b\w*Priority\.(\w+)")
BIT_SET = re.compile(r"^\s*(\w+)\.(\d+)\s*:=\s*(.*?);", re.M | re.S)
BIT_NOTE = re.compile(r"\(\*\s*Bit\s*(\d+)\s*[:\-]\s*(.*?)\s*\*\)", re.I)
WORD_TYPES = {"BYTE": 8, "WORD": 16, "DWORD": 32, "LWORD": 64}
# Lower rank = more important. Unknown names keep their order of appearance after these.
RANK = {"critical": 0, "emergency": 0, "urgent": 0, "high": 1, "alarm": 1, "medium": 2, "warning": 2, "low": 3,
        "info": 4, "information": 4, "event": 4}


@dataclass
class Script:
    file: str
    classes: list[str]
    lines: int
    public_members: list[str]


@dataclass
class AlarmEntry:
    bit: int
    active: str
    clear: str
    helper: str
    priority: str | None


@dataclass
class Profile:
    name: str
    file: str
    entries: list[AlarmEntry] = field(default_factory=list)


def _csproj_scripts(sol: Solution) -> list[str]:
    out = []
    for proj in sol.projects_of("hmi"):
        pdir = proj.path.rsplit("/", 1)[0] + "/" if "/" in proj.path else ""
        try:
            xf = xmlrt.load(sol.root / proj.path)
        except (OSError, ValueError):
            continue
        for el in xf.root.iter():
            if isinstance(el.tag, str) and local(el) == "Compile" and (el.get("Include") or "").endswith(".spt.cs"):
                out.append(pdir + el.get("Include").replace("\\", "/"))
    return out


def _read(sol: Solution, rel: str) -> str | None:
    try:
        return (sol.root / rel).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None


def list_scripts(sol: Solution) -> list[Script]:
    out = []
    for rel in _csproj_scripts(sol):
        text = _read(sol, rel)
        if text is None:
            continue
        classes = re.findall(r"\bclass\s+(\w+)", text)
        members = re.findall(r"public\s+(?:static\s+)?(?:readonly\s+)?[\w.<>\[\],]+\s+(\w+)\s*(?:[({=;]|$)", text, re.M)
        out.append(Script(rel, list(dict.fromkeys(classes)), text.count("\n") + 1, list(dict.fromkeys(members))[:40]))
    return out


def read_profiles(sol: Solution) -> list[Profile]:
    profiles = []
    for rel in _csproj_scripts(sol):
        text = _read(sol, rel)
        if text is None or "[]" not in text:
            continue
        helpers = {}
        for name, body in HELPER.findall(text):
            m = PRIORITY.search(body)
            if m:
                helpers[name] = m.group(1)
        for _type, name, body in ARRAY.findall(text):
            entries = [AlarmEntry(int(bit), active, clear, helper, helpers.get(helper))
                       for helper, bit, active, clear in ENTRY.findall(body)]
            if entries:
                profiles.append(Profile(name, rel, entries))
    return profiles


def _norm(name: str) -> str:
    return re.sub(r"(_v\d+_\d+)$", "", name).lower().removeprefix("fb").rstrip("0123456789")


def alarm_words(sol: Solution) -> list[dict]:
    """Basic FBs that pack alarm bits into a BYTE/WORD/DWORD variable (`Var.N := …;`)."""
    out = []
    for td in sol.types.values():
        if td.kind != "basic" or not td.algorithms:
            continue
        words = {v.name: v.type.upper() for v in td.internal_vars + td.interface.output_vars
                 if v.type.upper() in WORD_TYPES}
        if not words:
            continue
        text = "\n".join(a.text for a in td.algorithms)
        bits: dict[str, dict[int, str]] = {}
        for var, bit, expr in BIT_SET.findall(text):
            if var in words:
                bits.setdefault(var, {})[int(bit)] = " ".join(expr.split())[:120]
        notes = {int(b): n for b, n in BIT_NOTE.findall(text)}  # "(* Bit 5: Energy measurement invalid *)"
        for var, found in bits.items():
            feeds = re.findall(rf"(\w+)\.Output\s*:=\s*[\w]*\(?\s*{re.escape(var)}\b", text)
            out.append({"fb": td.qualified_name, "word": var, "type": words[var], "bits": found, "feeds": feeds,
                        "notes": {b: n for b, n in notes.items() if b in found}})
    return out


def _finding(rule: str, severity: str, message: str, where: str, evidence=None) -> dict:
    f = {"rule": rule, "severity": severity, "message": message, "where": where}
    if evidence is not None:
        f["evidence"] = evidence
    return f


def alarm_profiles(sol: Solution) -> dict:
    """Profiles, alarm words and the cross-check / ISA-18.2 findings (ALM-06…10)."""
    profiles = read_profiles(sol)
    words = alarm_words(sol)
    findings: list[dict] = []
    for p in profiles:
        where = f"{p.file}: {p.name}"
        dup = [b for b, n in Counter(e.bit for e in p.entries).items() if n > 1]
        if dup:
            findings.append(_finding("ALM-08", "warning", f"Bit(s) {sorted(dup)} are described twice; the operator "
                                     "sees whichever text the lookup finds first.", where))
        texts = [e.active.strip().lower() for e in p.entries]
        same = sorted({t for t in texts if texts.count(t) > 1})
        if same:
            findings.append(_finding("ALM-10", "warning", "Identical alarm texts for different bits: each alarm must "
                                     "say which condition is active (ISA-18.2 alarm rationalization).", where, same[:5]))
        lazy = [e.bit for e in p.entries if not e.clear.strip() or e.clear.strip() == e.active.strip()]
        if lazy:
            findings.append(_finding("ALM-10", "info", "Clear text missing or equal to the active text; the event log "
                                     "cannot tell the alarm coming from going.", where, lazy[:10]))
        prios = [e.priority or e.helper for e in p.entries]
        top = min(prios, key=lambda x: RANK.get(x.lower(), 9)) if prios else None
        share = prios.count(top) / len(prios) if prios else 0
        if top and len(prios) >= 5 and len(set(prios)) > 0 and share > 0.5:
            findings.append(_finding("ALM-09", "info", f"{share:.0%} of the alarms use the highest priority "
                                     f"'{top}'. EEMUA 191 / ISA-18.2 aim for roughly 5% high, 15% medium, 80% low: "
                                     "review the consequence and response time of each.", where,
                                     dict(Counter(prios))))
    for w in words:
        width = WORD_TYPES[w["type"]]
        over = [b for b in w["bits"] if b >= width]
        if over:
            findings.append(_finding("ALM-08", "warning", f"Bit(s) {over} do not fit a {w['type']}.",
                                     f"{w['fb']}.{w['word']}"))
        matches = [p for p in profiles if _norm(p.name) and _norm(p.name) == _norm(w["fb"].split(".")[-1])]
        w["profiles"] = [f"{p.file}: {p.name}" for p in matches]
        for p in matches:
            described = {e.bit for e in p.entries}
            set_bits = set(w["bits"])
            missing = sorted(set_bits - described)
            if missing:
                findings.append(_finding("ALM-06", "warning", f"{len(missing)} bit(s) set by {w['fb']} have no text in "
                                         f"profile {p.name}: the operator gets no (or a generic) message.",
                                         f"{p.file}: {p.name}",
                                         {str(b): {"condition": w["bits"][b],
                                                   **({"comment": w["notes"][b]} if b in w["notes"] else {})}
                                          for b in missing[:10]}))
            dead = sorted(described - set_bits)
            if dead:
                findings.append(_finding("ALM-07", "info", f"Profile {p.name} describes bit(s) {dead} that "
                                         f"{w['fb']} never sets (stale or not implemented).", f"{p.file}: {p.name}"))
    return {"profiles": [{"name": p.name, "file": p.file, "alarms": len(p.entries),
                          "priorities": dict(Counter(e.priority or e.helper for e in p.entries)),
                          "entries": [vars(e) for e in p.entries]} for p in profiles],
            "alarm_words": words, "findings": findings}


# -- edit ------------------------------------------------------------------------------------------------


def add_profile_entries(sol: Solution, profile: str, entries: list[tuple[int, str, str, str]]):
    """Append `Helper(bit, "active", "clear"),` lines to an existing profile array (same indentation)."""
    from ..project.changes import ChangeSet, FileChange
    from ..project.edit import EditError

    found = [p for p in read_profiles(sol) if p.name == profile]
    if not found:
        raise EditError(f"No alarm profile '{profile}' (profiles: {', '.join(p.name for p in read_profiles(sol)) or 'none'}).")
    p = found[0]
    raw = (sol.root / p.file).read_bytes()
    text = raw.decode("utf-8-sig")
    helpers = {h for h, body in HELPER.findall(text) if PRIORITY.search(body)} | {e.helper for e in p.entries}
    used = {e.bit for e in p.entries}
    for bit, active, clear, helper in entries:
        if helper not in helpers:
            raise EditError(f"Helper '{helper}' is not defined; use one of {sorted(helpers)}.")
        if bit in used:
            raise EditError(f"Bit {bit} is already described in {profile}.")
        if bit < 0 or bit > 63:
            raise EditError(f"Bit {bit} is out of range.")
        for s in (active, clear):
            if not s.strip() or any(c in s for c in '"\\\r\n'):
                raise EditError("Texts must be non-empty, without quotes, backslashes or line breaks.")
        used.add(bit)
    m = next(m for m in ARRAY.finditer(text) if m.group(2) == profile)
    body_end = m.end(3)
    body = m.group(3)
    nl = "\r\n" if "\r\n" in text else "\n"
    first = re.search(r"\n([ \t]*)\w+\s*\(", body)
    indent = first.group(1) if first else "\t\t\t"
    inner = re.search(r"\(\s*\n([ \t]*)\d", body)
    multi = inner is not None  # one argument per line
    arg = inner.group(1) if multi else ""
    stripped = body.rstrip()
    lead = body[:len(stripped)]
    add = []
    for bit, active, clear, helper in entries:
        if multi:
            add.append(f"{indent}{helper}({nl}{arg}{bit},{nl}{arg}\"{active}\",{nl}{arg}\"{clear}\")")
        else:
            add.append(f"{indent}{helper}({bit}, \"{active}\", \"{clear}\")")
    sep = "," if not stripped.endswith(",") else ""
    new_body = lead + sep + nl + nl.join(f"{nl}{a}," if multi else f"{a}," for a in add).rstrip(",") + body[len(stripped):]
    new = text[:m.start(3)] + new_body + text[body_end:]
    data = (b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b"") + new.encode("utf-8")
    cs = ChangeSet(sol.root, f"add {len(entries)} alarm text(s) to profile {profile}")
    cs.changes[p.file] = FileChange(p.file, raw, data)
    return cs
