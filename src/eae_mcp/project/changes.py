"""Change sets: compute file edits, show them as a diff, apply them with backup and audit log."""

from __future__ import annotations

import difflib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..io import xmlrt


@dataclass
class FileChange:
    rel: str  # path relative to the solution root (POSIX)
    old: bytes | None  # None = new file
    new: bytes


@dataclass
class ChangeSet:
    root: Path
    description: str = ""
    changes: dict[str, FileChange] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    _docs: dict[str, xmlrt.XmlFile] = field(default_factory=dict, repr=False)

    # -- building -----------------------------------------------------------------

    def doc(self, rel: str) -> xmlrt.XmlFile:
        """Load (once) an existing XML file for editing; `commit_doc` records the edit."""
        if rel not in self._docs:
            # A file created (or already edited) earlier in this change set is edited on top of that content,
            # so several builders can share one change set.
            pending = self.changes.get(rel)
            self._docs[rel] = xmlrt.parse_bytes(pending.new, self.root / rel) if pending else xmlrt.load(self.root / rel)
        return self._docs[rel]

    def commit_docs(self) -> None:
        for rel, xf in self._docs.items():
            new = xmlrt.dumps(xf)
            if new != xf.original:
                prior = self.changes.get(rel)
                old = prior.old if prior else xf.original
                self.changes[rel] = FileChange(rel, old, new)
                xf.original = new

    def create(self, rel: str, content: bytes) -> None:
        if (self.root / rel).exists() or rel in self.changes:
            raise FileExistsError(f"{rel} already exists")
        self.changes[rel] = FileChange(rel, None, content)

    # -- output -----------------------------------------------------------------------

    def diff(self, max_lines: int = 400) -> str:
        out: list[str] = []
        for rel, ch in sorted(self.changes.items()):
            old = (ch.old or b"").decode("utf-8-sig", errors="replace").splitlines()
            new = ch.new.decode("utf-8-sig", errors="replace").splitlines()
            out += difflib.unified_diff(old, new, f"a/{rel}" if ch.old is not None else "/dev/null",
                                        f"b/{rel}", lineterm="", n=2)
        if len(out) > max_lines:
            out = out[:max_lines] + [f"… ({len(out) - max_lines} more diff lines)"]
        return "\n".join(out)

    def summary(self) -> list[dict]:
        return [{"file": rel, "action": "create" if ch.old is None else "modify"}
                for rel, ch in sorted(self.changes.items())]

    def apply(self) -> dict:
        """Write all files atomically after backing up modified ones; append to the audit log."""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        state_dir = self.root / ".eae-mcp"
        backup_dir = state_dir / "backup" / stamp
        for rel, ch in self.changes.items():
            target = self.root / rel
            if ch.old is not None:
                if target.read_bytes() != ch.old:
                    raise RuntimeError(f"{rel} changed on disk since it was read; nothing was written")
                (backup_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                (backup_dir / rel).write_bytes(ch.old)
        written = []
        for rel, ch in sorted(self.changes.items()):
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".eae-mcp.tmp")
            tmp.write_bytes(ch.new)
            tmp.replace(target)
            written.append(rel)
        state_dir.mkdir(exist_ok=True)
        with open(state_dir / "audit.jsonl", "a", encoding="utf-8") as log:
            log.write(json.dumps({"time": stamp, "action": self.description, "files": self.summary(),
                                  "backup": str(backup_dir) if backup_dir.exists() else None}) + "\n")
        return {"written": written, "backup": str(backup_dir) if backup_dir.exists() else None}
