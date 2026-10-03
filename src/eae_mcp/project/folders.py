"""Logical Solution Explorer folders (General/Folders.xml + <Parent> metadata).

Folders are per category and named as dotted paths (".Standard.IO"). A category's
tree is the union of Folders.xml entries and the <Parent> values of its items:
EAE tolerates the two disagreeing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..io import xmlrt
from .types import children

# TypeDef.kind → Folders.xml category
KIND_TO_CATEGORY = {
    "basic": "Basic",
    "composite": "Composite",
    "cat": "CAT",
    "adapter": "Adapter",
    "datatype": "DataType",
    "subapp": "SubApp",
    "function": "Function",
    "sifb": "ServiceInterface",
}


@dataclass
class FolderTree:
    # category → folder path → member names
    folders: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    def add(self, category: str, folder: str, member: str | None = None) -> None:
        cat = self.folders.setdefault(category, {})
        # Register every ancestor (".A.B" → ".A", ".A.B").
        parts = [p for p in folder.split(".") if p]
        for i in range(1, len(parts) + 1):
            cat.setdefault("." + ".".join(parts[:i]), [])
        if member:
            cat.setdefault(folder, []).append(member)

    def as_tree(self) -> dict:
        return {cat: {k: sorted(v) for k, v in sorted(paths.items())} for cat, paths in sorted(self.folders.items())}


def load_folders_xml(path: Path, tree: FolderTree) -> None:
    if not path.exists():
        return
    for folder in children(xmlrt.load(path).root, "Folder"):
        category, name = folder.get("Type", ""), folder.get("Name", "")
        items = [i.text or "" for i in children(children(folder, "Items")[0])] if children(folder, "Items") else []
        if name == "Root":
            continue
        tree.add(category, name)
        for item in items:
            if not item.startswith(":"):
                tree.add(category, name, item)
