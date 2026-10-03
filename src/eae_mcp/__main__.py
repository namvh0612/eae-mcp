"""Command line entry point: `eae-mcp [--config eae-mcp.toml] [--transport stdio|http]`."""

from __future__ import annotations

import argparse
import hmac
import os
import sys

from .config import Config
from .server import create_server

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def http_app(server, token: str | None, host: str):
    """Streamable HTTP app; requires `Authorization: Bearer <token>` when a token is set."""
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.responses import JSONResponse

    security = None
    if host not in LOOPBACK:
        # Remote clients reach us by IP/hostname; the bearer token is the access control.
        security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    app = server.streamable_http_app(transport_security=security, host=host)
    if not token:
        return app

    expected = f"Bearer {token}".encode()

    async def guarded(scope, receive, send):
        if scope["type"] == "http":
            auth = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(auth, expected):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await app(scope, receive, send)

    return guarded


def main() -> None:
    parser = argparse.ArgumentParser(prog="eae-mcp", description="MCP server for EcoStruxure Automation Expert 26")
    parser.add_argument("--config", help="path to eae-mcp.toml (or set EAE_MCP_CONFIG)")
    parser.add_argument("--transport", choices=["stdio", "http"], help="override [server] transport")
    parser.add_argument("--host", help="HTTP bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, help="HTTP port (default 8765)")
    args = parser.parse_args()

    config = Config.load(args.config)
    server = create_server(config)
    if (args.transport or config.transport) != "http":
        server.run("stdio")
        return

    import uvicorn

    host, port = args.host or config.http_host, args.port or config.http_port
    token = os.environ.get("EAE_MCP_TOKEN")
    if host not in LOOPBACK and not token:
        sys.exit("Refusing to listen on a non-loopback address without EAE_MCP_TOKEN set.")
    print(f"eae-mcp: streamable HTTP on http://{host}:{port}/mcp" + (" (bearer token required)" if token else ""),
          file=sys.stderr)
    uvicorn.run(http_app(server, token, host), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
