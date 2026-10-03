"""Identifier generation in EAE's formats."""

from __future__ import annotations

import secrets
import uuid


def new_id16(taken: set[str] | None = None) -> str:
    """16 upper-case hex digits, as EAE uses for events, variables and FB instances."""
    taken = taken or set()
    while True:
        value = secrets.token_hex(8).upper()
        if value not in taken:
            taken.add(value)
            return value


def new_guid() -> str:
    """Lower-case GUID, as EAE uses for type GUIDs and algorithm IDs."""
    return str(uuid.uuid4())
