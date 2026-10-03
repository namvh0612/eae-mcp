"""JSON files written by EAE (eHMI .cnv.json / .sym.json): edit and write back in the same layout.

EAE writes tab-indented JSON with LF line ends, usually with a BOM and no final newline; hand-edited
files may use spaces. The layout of the original is detected and reused.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

BOM = b"\xef\xbb\xbf"


@dataclass
class JsonFile:
    data: object
    original: bytes
    bom: bool = True
    indent: str = "\t"
    newline: str = "\n"
    final_newline: bool = False


def parse_bytes(raw: bytes) -> JsonFile:
    text = raw.decode("utf-8-sig")
    m = re.search(r"\n([ \t]+)\S", text)
    return JsonFile(json.loads(text), raw, raw.startswith(BOM), m.group(1) if m else "\t",
                    "\r\n" if "\r\n" in text else "\n", text.endswith("\n"))


def load(path: Path) -> JsonFile:
    return parse_bytes(path.read_bytes())


def dumps(jf: JsonFile) -> bytes:
    text = json.dumps(jf.data, indent=jf.indent, ensure_ascii=False).replace("\n", jf.newline)
    if jf.final_newline:
        text += jf.newline
    return (BOM if jf.bom else b"") + text.encode("utf-8")


def new(data: object) -> bytes:
    return dumps(JsonFile(data, b""))
