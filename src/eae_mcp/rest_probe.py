"""eae_http_probe: try a REST call from the MCP host before generating the EAE client.

Disabled unless `[http_probe] enabled = true` and the host is in `allowed_hosts`. Only GET/HEAD. The token is
sent to that host only and never returned or logged. The answer is summarised: status, size against the
generated client's limits, and JSON fields with the `path`/`occurrence` that the generated ST extractor
(`rest_client.extract`, same algorithm) needs to read each value.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import Config
from .project.rest_client import BUF, extract

MAX_READ = 256 * 1024
SAFE_HEADERS = {"content-type", "content-length", "transfer-encoding", "date", "cache-control", "server",
                "x-ratelimit-limit", "x-ratelimit-remaining", "retry-after"}


class ProbeRefused(PermissionError):
    pass


def _leaves(obj: Any, prefix: str = "", out: list | None = None, limit: int = 60) -> list[tuple[str, Any]]:
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            _leaves(v, f"{prefix}.{k}" if prefix else str(k), out, limit)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:20]):
            _leaves(v, f"{prefix}[{i}]", out, limit)
    elif obj is not None:
        out.append((prefix, obj))
    return out


def _same(raw: str, value: Any) -> bool:
    if isinstance(value, bool):
        return raw == ("true" if value else "false")
    if isinstance(value, (int, float)):
        try:
            return float(raw) == float(value)
        except ValueError:
            return False
    return raw == str(value)[:255]


def suggest_fields(text: str, data: Any, limit: int = 30) -> list[dict]:
    """For each JSON leaf, the shortest (path, occurrence) the generated extractor needs to read it."""
    out = []
    for full, value in _leaves(data):
        keys = [k.split("[")[0] for k in full.replace("]", "").split(".") if k.split("[")[0]]
        if not keys:
            continue
        found = None
        for start in range(len(keys) - 1, -1, -1):  # last key alone first, then with parents
            path = ".".join(keys[start:])
            for occ in range(1, 51):
                got = extract(text, path, occ)
                if got == "" and occ > 1 and text.count(f'"{keys[-1]}"') < occ:
                    break
                if _same(got, value):
                    found = (path, occ)
                    break
            if found:
                break
        if found:
            t = ("BOOL" if isinstance(value, bool) else "DINT" if isinstance(value, int)
                 else "REAL" if isinstance(value, float) else "STRING")
            out.append({"json": full, "value": value, "path": found[0], "occurrence": found[1], "type": t})
        if len(out) >= limit:
            break
    return out


def probe(cfg: Config, url: str, method: str = "GET", token: str | None = None, auth: str = "bearer",
          headers: dict[str, str] | None = None, timeout: float = 10.0) -> dict:
    if not cfg.http_probe:
        raise ProbeRefused("eae_http_probe is disabled. Set [http_probe] enabled = true and list the host in "
                           "allowed_hosts in eae-mcp.toml, then restart the server.")
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("https", "http") or not host:
        raise ProbeRefused("url must be http(s)://host/path.")
    if host not in cfg.http_probe_hosts:
        raise ProbeRefused(f"{host} is not in [http_probe] allowed_hosts.")
    if method.upper() not in ("GET", "HEAD"):
        raise ProbeRefused("Only GET and HEAD are allowed (a probe must not change anything).")
    req = urllib.request.Request(url, method=method.upper())
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "eae-mcp-probe")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if token:
        if auth == "bearer":
            req.add_header("Authorization", f"Bearer {token}")
        elif auth.startswith("header:"):
            req.add_header(auth.split(":", 1)[1], token)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            status, hdrs, raw = resp.status, dict(resp.headers), resp.read(MAX_READ + 1)
    except urllib.error.HTTPError as e:
        status, hdrs, raw = e.code, dict(e.headers or {}), e.read(MAX_READ + 1) if e.fp else b""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"url": url, "error": str(getattr(e, "reason", e))}
    text = raw[:MAX_READ].decode("utf-8", errors="replace")
    header_size = sum(len(k) + len(v) + 4 for k, v in hdrs.items()) + 20
    result: dict = {
        "url": url, "status": status,
        "headers": {k: v for k, v in hdrs.items() if k.lower() in SAFE_HEADERS},
        "body_bytes": len(raw[:MAX_READ]), "truncated": len(raw) > MAX_READ,
        "chunked": "chunked" in hdrs.get("Transfer-Encoding", hdrs.get("transfer-encoding", "")).lower(),
    }
    total = header_size + len(raw)
    result["fits_generated_client"] = total <= BUF
    if total > BUF:
        result["advice"] = (f"The answer is about {total} bytes with headers; the generated client buffers {BUF}. "
                            "Narrow it with query parameters (fewer rows/metrics, shorter time range).")
    try:
        data = json.loads(text)
    except ValueError:
        result["json"] = False
        result["body_preview"] = text[:400]
        return result
    result["json"] = True
    result["top_level"] = list(data.keys())[:30] if isinstance(data, dict) else f"list[{len(data)}]"
    result["fields"] = suggest_fields(text, data)
    result["note"] = ("fields[].path/occurrence/type can be passed to eae_rest_client_create. "
                      "The token was used for this call only and is not stored.")
    return result
