"""CAT manifest (.cfg) parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..io import xmlrt
from .types import children, local


@dataclass
class CatSymbol:
    name: str
    file: str
    technology: str  # hmi (.NET) | ehmi (web)
    is_faceplate: bool = False
    doc_file: str | None = None
    dependent_files: list[str] = field(default_factory=list)


@dataclass
class SubCat:
    name: str
    type: str
    namespace: str | None = None


@dataclass
class CatManifest:
    name: str
    cfg_file: str
    cat_file: str | None = None
    folder: str | None = None
    hmi_interface: str | None = None  # instance name of the HMI SIFB (usually IThis)
    hmi_interface_file: str | None = None
    symbols: list[CatSymbol] = field(default_factory=list)
    sub_cats: list[SubCat] = field(default_factory=list)
    plugin_files: list[str] = field(default_factory=list)
    generated_files: list[str] = field(default_factory=list)  # SymbolDefFile, SymbolEventFile, DesignFile


def _p(value: str | None) -> str | None:
    return value.replace("\\", "/") if value else value


def parse_cfg(path: Path, rel: str) -> CatManifest:
    root = xmlrt.load(path).root
    m = CatManifest(
        name=root.get("Name", path.stem),
        cfg_file=rel,
        cat_file=_p(root.get("CATFile")),
        folder=root.get("Folder"),
        generated_files=[_p(root.get(k)) for k in ("SymbolDefFile", "SymbolEventFile", "DesignFile") if root.get(k)],
    )
    for el in children(root):
        name = local(el)
        if name == "SubCAT":
            m.sub_cats.append(SubCat(el.get("Name", ""), el.get("Type", ""), el.get("Namespace")))
        elif name == "HMIInterface":
            m.hmi_interface = el.get("Name")
            m.hmi_interface_file = _p(el.get("FileName"))
            for sym in children(el):
                kind = local(sym)
                if kind not in ("Symbol", "WebSymbol"):
                    continue
                m.symbols.append(CatSymbol(
                    name=sym.get("Name", ""),
                    file=_p(sym.get("FileName", "")) or "",
                    technology="hmi" if kind == "Symbol" else "ehmi",
                    is_faceplate=sym.get("IsFaceplate") == "true",
                    doc_file=_p(sym.get("DocFile")),
                    dependent_files=[_p(d.text or "") for d in children(sym, "DependentFiles")],
                ))
        elif name == "Plugin" and el.get("Value"):
            m.plugin_files.append(_p(el.get("Value")))
    return m
