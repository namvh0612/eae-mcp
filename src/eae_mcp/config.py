"""Configuration: eae-mcp.toml plus environment overrides."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_LIBRARY_STORE = Path("C:/ProgramData/Schneider Electric/Libraries")


@dataclass
class Config:
    roots: list[Path] = field(default_factory=list)
    library_store: Path | None = None
    catalog_file: Path | None = None
    eae_version: str = "26.0"
    transport: str = "stdio"
    http_host: str = "127.0.0.1"
    http_port: int = 8765
    allow_write: bool = False  # write tools refuse to touch disk unless this is set
    http_probe: bool = False  # eae_http_probe may call external REST APIs (GET/HEAD only)
    http_probe_hosts: list[str] = field(default_factory=list)  # allowed host names; empty = none

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        cfg = cls()
        path = path or os.environ.get("EAE_MCP_CONFIG")
        if path and Path(path).exists():
            data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
            project, eae, server = data.get("project", {}), data.get("eae", {}), data.get("server", {})
            cfg.roots = [Path(r) for r in project.get("roots", [])]
            cfg.allow_write = bool(project.get("allow_write", False))
            if eae.get("library_store"):
                cfg.library_store = Path(eae["library_store"])
            if eae.get("catalog_file"):
                cfg.catalog_file = Path(eae["catalog_file"])
            cfg.eae_version = str(eae.get("version", cfg.eae_version))
            cfg.transport = server.get("transport", cfg.transport)
            probe = data.get("http_probe", {})
            cfg.http_probe = bool(probe.get("enabled", False))
            cfg.http_probe_hosts = [str(h).lower() for h in probe.get("allowed_hosts", [])]
            bind = server.get("http_bind")
            if bind and ":" in bind:
                host, port = bind.rsplit(":", 1)
                cfg.http_host, cfg.http_port = host, int(port)
        if os.environ.get("EAE_MCP_ALLOW_WRITE"):
            cfg.allow_write = os.environ["EAE_MCP_ALLOW_WRITE"].lower() in ("1", "true", "yes")
        if os.environ.get("EAE_MCP_ROOTS"):
            cfg.roots = [Path(p) for p in os.environ["EAE_MCP_ROOTS"].split(os.pathsep) if p]
        if os.environ.get("EAE_MCP_LIBRARY_STORE"):
            cfg.library_store = Path(os.environ["EAE_MCP_LIBRARY_STORE"])
        if os.environ.get("EAE_MCP_CATALOG"):
            cfg.catalog_file = Path(os.environ["EAE_MCP_CATALOG"])
        if cfg.library_store is None and DEFAULT_LIBRARY_STORE.is_dir():
            cfg.library_store = DEFAULT_LIBRARY_STORE
        return cfg
