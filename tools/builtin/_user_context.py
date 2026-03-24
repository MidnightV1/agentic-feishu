# -*- coding: utf-8 -*-
"""User context for tool execution — provides current user identity and contacts to skill tools."""
import contextvars
from typing import Any

# Set by adapter before each agent turn, read by skill tools
current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_user_id", default=""
)

# Global ContactStore reference — set once by adapter at startup
_contacts: Any = None


def get_current_user_id() -> str:
    """Get the current user's open_id. Returns empty string if not set."""
    return current_user_id.get()


def set_current_user_id(open_id: str) -> contextvars.Token:
    """Set the current user's open_id. Returns a token for reset."""
    return current_user_id.set(open_id)


def set_contacts(contacts: Any) -> None:
    """Set the shared ContactStore instance (called once by adapter)."""
    global _contacts
    _contacts = contacts


def get_contacts() -> Any:
    """Get the shared ContactStore. Returns None if not configured."""
    return _contacts


def resolve_user(name_or_id: str) -> str:
    """Resolve a user name to open_id using ContactStore.

    If name_or_id already looks like an open_id (ou_*), returns as-is.
    If ContactStore is available and finds a match, returns the open_id.
    Otherwise returns the original string unchanged.
    """
    if not name_or_id or name_or_id.startswith("ou_"):
        return name_or_id
    if _contacts is None:
        return name_or_id
    resolved = _contacts.resolve_name(name_or_id)
    return resolved if resolved else name_or_id
