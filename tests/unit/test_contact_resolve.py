# -*- coding: utf-8 -*-
"""Unit tests for contact resolution in skill tools."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.builtin._user_context import resolve_user, set_contacts, _contacts


@pytest.fixture(autouse=True)
def _reset_contacts():
    """Reset global contacts after each test."""
    from tools.builtin import _user_context
    original = _user_context._contacts
    yield
    _user_context._contacts = original


def test_passthrough_open_id():
    """open_id strings pass through unchanged."""
    assert resolve_user("ou_abc123") == "ou_abc123"


def test_empty_string():
    assert resolve_user("") == ""


def test_no_contacts_returns_original():
    """Without ContactStore, names pass through unchanged."""
    set_contacts(None)
    assert resolve_user("张三") == "张三"


def test_resolve_via_contacts():
    """ContactStore.resolve_name hit → returns open_id."""
    mock_store = MagicMock()
    mock_store.resolve_name.return_value = "ou_found"
    set_contacts(mock_store)
    assert resolve_user("张三") == "ou_found"
    mock_store.resolve_name.assert_called_once_with("张三")


def test_resolve_miss_returns_original():
    """ContactStore.resolve_name miss → returns original name."""
    mock_store = MagicMock()
    mock_store.resolve_name.return_value = None
    set_contacts(mock_store)
    assert resolve_user("Unknown Person") == "Unknown Person"
