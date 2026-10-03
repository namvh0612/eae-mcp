"""MCP server for EcoStruxure Automation Expert (EAE) 26."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("eae-mcp")
except PackageNotFoundError:  # running from a source checkout
    __version__ = "0.0.0"
