"""Catalog of system-library types from the EAE library store.

Store layout (Windows default `C:\\ProgramData\\Schneider Electric\\Libraries`):
    <Library>-<Version>\\Files\\<Namespace>\\<Type>.{fbt,adp,dt,fct,res,dev}
Library type files are plain XML (interfaces readable; bodies are encrypted in
`nxtLibraryData`). The catalog can be exported to JSON for machines without EAE.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..model import AdapterDecl, DataTypeDef, EnumValue, Event, Interface, TypeDef, Var, to_dict
from .types import TYPE_SUFFIXES, load_type

_PACKAGE = re.compile(r"^(?P<name>.+)-(?P<version>\d+(?:\.\d+)+)$")


def _version_key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


@dataclass
class Package:
    name: str
    version: str
    path: Path


@dataclass
class Catalog:
    types: dict[str, TypeDef] = field(default_factory=dict)  # qualified name → type
    packages: list[dict] = field(default_factory=list)
    source: str = ""

    def find(self, name: str, namespace: str | None = None) -> TypeDef | None:
        if namespace and f"{namespace}.{name}" in self.types:
            return self.types[f"{namespace}.{name}"]
        matches = [t for t in self.types.values() if t.name == name]
        return matches[0] if matches else None

    # -- store ---------------------------------------------------------------

    @staticmethod
    def list_packages(store: Path) -> list[Package]:
        out = []
        if not store.is_dir():
            return out
        for d in sorted(store.iterdir()):
            m = _PACKAGE.match(d.name)
            if d.is_dir() and m:
                out.append(Package(m["name"], m["version"], d))
        return out

    @staticmethod
    def select_packages(store: Path, references: dict[str, str | None]) -> list[Package]:
        """Pick one version per referenced library (exact, else newest same major.minor, else newest)."""
        by_name: dict[str, list[Package]] = {}
        for p in Catalog.list_packages(store):
            by_name.setdefault(p.name, []).append(p)
        chosen = []
        for name, version in references.items():
            candidates = sorted(by_name.get(name, []), key=lambda p: _version_key(p.version))
            if not candidates:
                continue
            exact = [p for p in candidates if p.version == version]
            if exact:
                chosen.append(exact[0])
                continue
            if version:
                mm = version.split(".")[:2]
                same = [p for p in candidates if p.version.split(".")[:2] == mm]
                if same:
                    chosen.append(same[-1])
                    continue
            chosen.append(candidates[-1])
        return chosen

    @classmethod
    def from_store(cls, store: Path, references: dict[str, str | None]) -> "Catalog":
        cat = cls(source=str(store))
        for pkg in cls.select_packages(store, references):
            cat.packages.append({"name": pkg.name, "version": pkg.version})
            files = pkg.path / "Files"
            for f in sorted(files.rglob("*")) if files.is_dir() else []:
                if f.suffix.lower() not in TYPE_SUFFIXES or not f.is_file():
                    continue
                try:
                    td = load_type(f, f.relative_to(store).as_posix())
                except Exception:  # noqa: BLE001 - a broken library file must not break the catalog
                    continue
                td.source = "library"
                td.project = pkg.name
                td.library_version = pkg.version
                if not td.namespace:
                    td.namespace = f.parent.name
                # Library bodies are encrypted; keep interfaces only.
                td.algorithms, td.states, td.transitions = [], [], []
                cat.types.setdefault(td.qualified_name, td)
        return cat

    # -- JSON export/import ----------------------------------------------------

    def to_json(self) -> str:
        return json.dumps(
            {"source": self.source, "packages": self.packages,
             "types": [to_dict(t) for t in self.types.values()]},
            indent=1,
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Catalog":
        data = json.loads(path.read_text(encoding="utf-8"))
        cat = cls(source=data.get("source", str(path)), packages=data.get("packages", []))
        for t in data.get("types", []):
            td = _typedef_from_dict(t)
            cat.types[td.qualified_name] = td
        return cat


def _typedef_from_dict(d: dict) -> TypeDef:
    itf = d.get("interface", {})
    interface = Interface(
        event_inputs=[Event(**e) for e in itf.get("event_inputs", [])],
        event_outputs=[Event(**e) for e in itf.get("event_outputs", [])],
        input_vars=[Var(**v) for v in itf.get("input_vars", [])],
        output_vars=[Var(**v) for v in itf.get("output_vars", [])],
        adapter_inputs=[AdapterDecl(**a) for a in itf.get("adapter_inputs", [])],
        adapter_outputs=[AdapterDecl(**a) for a in itf.get("adapter_outputs", [])],
        return_type=itf.get("return_type"),
    )
    dt = d.get("datatype")
    datatype = None
    if dt:
        datatype = DataTypeDef(
            kind=dt.get("kind", "unknown"),
            base_type=dt.get("base_type"),
            members=[Var(**v) for v in dt.get("members", [])],
            values=[EnumValue(**v) for v in dt.get("values", [])],
            ranges=[tuple(r) for r in dt.get("ranges", [])],
        )
    return TypeDef(
        kind=d["kind"], name=d["name"], namespace=d.get("namespace"), guid=d.get("guid"),
        comment=d.get("comment"), path=d.get("path"), attributes=d.get("attributes", {}),
        interface=interface, datatype=datatype, project=d.get("project"),
        source="library", library_version=d.get("library_version"),
    )
