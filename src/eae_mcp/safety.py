"""Path guards: keep the server inside configured roots and away from security material."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

# Never read, list contents of, or return these (relative to a solution or project root).
SENSITIVE_DIR_NAMES = {"security", "certificates"}
SENSITIVE_FILE_NAMES = {"se-rbac-users.json", "se-rbac-roles.json"}
SENSITIVE_SUFFIXES = {".db", ".pfx", ".p12", ".key", ".pem", ".crt", ".cer"}
SENSITIVE_MARKERS = ("_devicecertificate",)
# Build output and caches: ignored by every scan.
IGNORED_DIR_NAMES = {"bin", "obj", "snapshotcompiles", ".vs", ".eae-mcp"}


class AccessDenied(PermissionError):
    pass


def is_sensitive(path: str | Path) -> bool:
    parts = [p.lower() for p in PurePosixPath(str(path).replace("\\", "/")).parts]
    if any(p in SENSITIVE_DIR_NAMES for p in parts[:-1]):
        return True
    name = parts[-1] if parts else ""
    if name in SENSITIVE_FILE_NAMES or any(m in name for m in SENSITIVE_MARKERS):
        return True
    return PurePosixPath(name).suffix in SENSITIVE_SUFFIXES


def is_ignored(path: str | Path) -> bool:
    parts = [p.lower() for p in PurePosixPath(str(path).replace("\\", "/")).parts]
    return any(p in IGNORED_DIR_NAMES for p in parts)


def check_inside(path: Path, roots: list[Path]) -> Path:
    """Resolve `path` and require it to be inside one of `roots` (if any are configured)."""
    resolved = path.expanduser().resolve()
    if roots and not any(resolved == r or r in resolved.parents for r in (x.resolve() for x in roots)):
        allowed = ", ".join(str(r) for r in roots)
        raise AccessDenied(
            f"{resolved} is outside the configured project roots ({allowed}). Open a solution under these "
            "roots (eae_list_solutions shows them), or add the folder to [project] roots in eae-mcp.toml "
            "and restart the server.")
    return resolved


def check_readable(path: Path, roots: list[Path]) -> Path:
    resolved = check_inside(path, roots)
    if is_sensitive(resolved):
        raise AccessDenied(f"{resolved.name} is security material and is never read by this server")
    return resolved
