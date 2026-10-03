"""Lossless XML round-trip for EAE files.

EAE writes XML with .NET's XmlWriter (CRLF, two-space indentation, `<tag />`,
optional UTF-8 BOM, usually no trailing newline), but some files come from
hand-formatted templates. To reproduce any unmodified file byte for byte we:

* parse with whitespace, comments and CDATA preserved;
* keep the original prolog (declaration, DOCTYPE, whitespace) and epilog;
* keep the original text of every start tag and reuse it whenever the element's
  name and attributes are unchanged;
* otherwise fall back to .NET-style output (` />`, `&#xD;`, CRLF).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

BOM = b"\xef\xbb\xbf"

_PARSER = etree.XMLParser(
    remove_blank_text=False,
    strip_cdata=False,
    resolve_entities=False,
    load_dtd=False,
    no_network=True,
    huge_tree=True,
    remove_comments=False,
)

# Comments, CDATA, PIs, DOCTYPE, or a start tag (groups: name, attributes).
_TOKENS = re.compile(
    rb"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|<!DOCTYPE[^>]*>"
    rb"|<([A-Za-z_][\w:.\-]*)((?:\s+[^\s=/>]+\s*=\s*(?:\"[^\"]*\"|'[^']*'))*)\s*/?>",
    re.S,
)
_ATTR = re.compile(rb"\s+([^\s=/>]+)\s*=\s*(\"[^\"]*\"|'[^']*')")
_PROTECTED = re.compile(rb"(<!\[CDATA\[.*?\]\]>|<!--.*?-->)", re.S)
_DECLARATION = re.compile(rb"^\s*<\?xml[^>]*\?>")
_DOCTYPE = re.compile(rb"<!DOCTYPE[^>]*>")
# .NET writes character references in upper-case hex; lxml writes decimal.
_CHARREFS = {b"&#13;": b"&#xD;", b"&#10;": b"&#xA;", b"&#9;": b"&#x9;"}


class EmptyXmlError(ValueError):
    """The file has no XML content (EAE writes some .meta.xml files as just a BOM)."""


@dataclass
class XmlFile:
    """A parsed XML file plus what is needed to write it back unchanged."""

    path: Path | None
    root: etree._Element
    bom: bool = False
    newline: str = "\r\n"
    prolog: bytes = b""
    epilog: bytes = b""
    space_before_slash: bool = True
    original: bytes = field(default=b"", repr=False)
    # Original start-tag text per element, used when the element is unchanged.
    raw_tags: dict[etree._Element, bytes] = field(default_factory=dict, repr=False)

    @property
    def tree(self) -> etree._ElementTree:
        return self.root.getroottree()

    @property
    def declaration(self) -> bytes | None:
        m = _DECLARATION.match(self.prolog)
        return m.group(0).strip() if m else None

    @property
    def doctype(self) -> bytes | None:
        m = _DOCTYPE.search(self.prolog)
        return m.group(0) if m else None


def _start_tags(body: bytes):
    for m in _TOKENS.finditer(body):
        if m.group(1) is not None:
            yield m


def _elements(root: etree._Element) -> list[etree._Element]:
    return [e for e in root.iter() if isinstance(e.tag, str)]


def _attr_signature(tag_match: re.Match) -> tuple:
    # Literal tabs/newlines in attribute values are normalized to spaces by any XML
    # parser, so compare values the way the parser sees them.
    def norm(value: bytes) -> bytes:
        return re.sub(rb"[\t\r\n]", b" ", value)

    return (tag_match.group(1), frozenset(
        (a.group(1), norm(a.group(2)[1:-1])) for a in _ATTR.finditer(tag_match.group(2) or b"")
    ))


def _normalize_charrefs(data: bytes) -> bytes:
    for dec, hexref in _CHARREFS.items():
        data = data.replace(dec, hexref)
    return data


def parse_bytes(data: bytes, path: Path | None = None) -> XmlFile:
    body = data[len(BOM):] if data.startswith(BOM) else data
    if not body.strip():
        raise EmptyXmlError(str(path or "<bytes>"))
    root = etree.fromstring(body, _PARSER)
    tags = list(_start_tags(body))
    end_tag = re.search(rb"</[^>]+>\s*$|/>\s*$", body)
    self_closing = body.count(b"/>")
    xf = XmlFile(
        path=path,
        root=root,
        bom=data.startswith(BOM),
        newline="\r\n" if b"\r\n" in body else "\n",
        prolog=body[: tags[0].start()] if tags else b"",
        epilog=body[end_tag.end() - len(end_tag.group(0)) + len(end_tag.group(0).rstrip()):] if end_tag else b"",
        space_before_slash=self_closing == 0 or body.count(b" />") * 2 >= self_closing,
        original=data,
    )
    for el, tag in zip(_elements(root), tags):
        xf.raw_tags[el] = tag.group(0)
        if el.text is None and len(el) == 0 and not tag.group(0).endswith(b"/>"):
            el.text = ""  # keep `<a></a>` instead of `<a />`
    return xf


def load(path: str | Path) -> XmlFile:
    path = Path(path)
    return parse_bytes(path.read_bytes(), path)


def _restore_start_tags(body: bytes, xf: XmlFile) -> bytes:
    """Swap regenerated start tags for the original text where nothing changed."""
    out, last = [], 0
    for el, m in zip(_elements(xf.root), _start_tags(body)):
        raw = xf.raw_tags.get(el)
        if raw is None:
            continue
        raw_m = next(_start_tags(raw), None)
        same_shape = raw.endswith(b"/>") == m.group(0).endswith(b"/>")
        if raw_m and same_shape and _attr_signature(raw_m) == _attr_signature(
            next(_start_tags(_normalize_charrefs(m.group(0))))
        ):
            out.append(body[last:m.start()])
            out.append(raw)
            last = m.end()
    out.append(body[last:])
    return b"".join(out)


def _fix_markup(chunk: bytes, space_before_slash: bool) -> bytes:
    if space_before_slash:
        chunk = re.sub(rb"(?<![ \"'])/>|(?<=[\"'])/>", b" />", chunk)
    return _normalize_charrefs(chunk)


def dumps(xf: XmlFile) -> bytes:
    """Serialize. Unmodified files round-trip byte for byte."""
    body = etree.tostring(xf.root, encoding="utf-8", xml_declaration=False)
    nl = xf.newline.encode()
    if nl == b"\r\n":
        # lxml normalizes line endings to LF while parsing; restore CRLF.
        body = body.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    parts = _PROTECTED.split(body)
    body = b"".join(
        part if i % 2 else _fix_markup(part, xf.space_before_slash)
        for i, part in enumerate(parts)
    )
    body = _restore_start_tags(body, xf)
    return (BOM if xf.bom else b"") + xf.prolog + body + xf.epilog


def save(xf: XmlFile, path: str | Path | None = None) -> None:
    """Write atomically (temp file + rename)."""
    target = Path(path or xf.path)
    tmp = target.with_name(target.name + ".eae-mcp.tmp")
    tmp.write_bytes(dumps(xf))
    tmp.replace(target)


def roundtrips(path: str | Path) -> bool:
    try:
        xf = load(path)
    except EmptyXmlError:
        return True
    return dumps(xf) == xf.original


# -- building and editing ----------------------------------------------------------

EAE_DECLARATION = b'<?xml version="1.0" encoding="utf-8"?>'


def new_document(root: etree._Element, doctype: str | None = None, bom: bool = False,
                 path: Path | None = None) -> XmlFile:
    """Wrap a freshly built tree so `dumps` writes it in EAE style (CRLF, 2 spaces, ` />`)."""
    etree.indent(root, space="  ")
    prolog = EAE_DECLARATION + b"\r\n"
    if doctype:
        prolog += doctype.encode() + b"\r\n"
    return XmlFile(path=path, root=root, bom=bom, newline="\r\n", prolog=prolog, epilog=b"")


def _depth(el: etree._Element) -> int:
    return sum(1 for _ in el.iterancestors())


def insert_child(parent: etree._Element, new: etree._Element, index: int | None = None,
                 indent: str = "  ") -> etree._Element:
    """Insert `new` into `parent` keeping the surrounding indentation consistent."""
    depth = _depth(parent)
    child_ws = "\n" + indent * (depth + 1)
    close_ws = "\n" + indent * depth
    etree.indent(new, space=indent, level=depth + 1)
    if len(parent) == 0:
        parent.text = child_ws
        new.tail = close_ws
        parent.append(new)
    elif index is None or index >= len(parent):
        last = parent[-1]
        new.tail = last.tail if last.tail and not last.tail.strip() else close_ws
        last.tail = child_ws
        parent.append(new)
    else:
        parent.insert(index, new)
        new.tail = child_ws
    return new


def remove_child(el: etree._Element) -> None:
    """Remove `el`, giving its tail whitespace to the previous sibling when it was last."""
    parent = el.getparent()
    prev = el.getprevious()
    if el.getnext() is None and prev is not None:
        prev.tail = el.tail
    elif el.getnext() is None and prev is None:
        parent.text = None
    parent.remove(el)
