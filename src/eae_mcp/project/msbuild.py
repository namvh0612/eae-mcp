"""Read MSBuild-style project files used by EAE (.dfbproj, .csproj, .htmlproj, …)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..io import xmlrt
from .types import children, local


@dataclass
class ProjectItem:
    kind: str  # Compile | None | Content | EmbeddedResource | Folder | Reference | ProjectReference | …
    include: str  # as stored (Windows separators)
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def posix(self) -> str:
        return self.include.replace("\\", "/")


@dataclass
class MsBuildProject:
    path: Path
    properties: dict[str, str] = field(default_factory=dict)
    items: list[ProjectItem] = field(default_factory=list)

    @property
    def dir(self) -> Path:
        return self.path.parent

    def items_of(self, *kinds: str) -> list[ProjectItem]:
        wanted = {k.lower() for k in kinds}
        return [i for i in self.items if i.kind.lower() in wanted]

    def references(self) -> dict[str, str | None]:
        """System library references: name → version (if recorded)."""
        return {i.include: i.metadata.get("Version") for i in self.items_of("Reference")}


def load_project(path: Path) -> MsBuildProject:
    root = xmlrt.load(path).root
    proj = MsBuildProject(path=path)
    for group in children(root, "PropertyGroup"):
        if group.get("Condition"):
            continue
        for prop in children(group):
            proj.properties.setdefault(local(prop), (prop.text or "").strip())
    for group in children(root, "ItemGroup"):
        for item in children(group):
            if item.get("Include") is None:
                continue
            proj.items.append(ProjectItem(
                kind=local(item),
                include=item.get("Include", ""),
                metadata={local(m): (m.text or "").strip() for m in children(item)},
            ))
    return proj
