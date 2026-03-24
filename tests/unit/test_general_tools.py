# -*- coding: utf-8 -*-
"""Unit tests for general_tools: edit_file and grep."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.builtin.general_tools import edit_file, grep, list_directory


# ── edit_file ──


@pytest.mark.asyncio
async def test_edit_single_replacement(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("hello world\nfoo bar\n")
    result = await edit_file(str(f), "hello", "goodbye")
    assert "Replaced 1" in result
    assert f.read_text() == "goodbye world\nfoo bar\n"


@pytest.mark.asyncio
async def test_edit_ambiguous_match(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("foo foo foo\n")
    result = await edit_file(str(f), "foo", "bar")
    assert "3 times" in result
    # File should be unchanged
    assert f.read_text() == "foo foo foo\n"


@pytest.mark.asyncio
async def test_edit_replace_all(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("foo foo foo\n")
    result = await edit_file(str(f), "foo", "bar", replace_all=True)
    assert "Replaced 3" in result
    assert f.read_text() == "bar bar bar\n"


@pytest.mark.asyncio
async def test_edit_not_found(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("hello\n")
    result = await edit_file(str(f), "nonexistent", "replacement")
    assert "not found" in result


@pytest.mark.asyncio
async def test_edit_identical_strings(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("hello\n")
    result = await edit_file(str(f), "hello", "hello")
    assert "identical" in result


@pytest.mark.asyncio
async def test_edit_missing_file():
    result = await edit_file("/nonexistent/path.py", "a", "b")
    assert "not found" in result


# ── grep ──


@pytest.mark.asyncio
async def test_grep_in_file(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("line one\nline two\nline three\n")
    result = await grep("two", str(f))
    assert "line two" in result
    assert ":2:" in result  # line number


@pytest.mark.asyncio
async def test_grep_in_directory(tmp_path):
    (tmp_path / "a.py").write_text("apple\nbanana\n")
    (tmp_path / "b.py").write_text("cherry\napple pie\n")
    result = await grep("apple", str(tmp_path))
    assert result.count("apple") >= 2


@pytest.mark.asyncio
async def test_grep_with_glob_filter(tmp_path):
    (tmp_path / "a.py").write_text("target\n")
    (tmp_path / "b.txt").write_text("target\n")
    result = await grep("target", str(tmp_path), glob_filter="*.py")
    assert "a.py" in result
    assert "b.txt" not in result


@pytest.mark.asyncio
async def test_grep_no_match(tmp_path):
    (tmp_path / "a.py").write_text("hello\n")
    result = await grep("nonexistent", str(tmp_path))
    assert "No matches" in result


@pytest.mark.asyncio
async def test_grep_invalid_regex():
    result = await grep("[invalid", ".")
    assert "invalid regex" in result


@pytest.mark.asyncio
async def test_grep_max_results(tmp_path):
    f = tmp_path / "big.py"
    f.write_text("\n".join(f"match line {i}" for i in range(100)))
    result = await grep("match", str(f), max_results=5)
    assert "truncated" in result


# ── list_directory (enhanced) ──


@pytest.mark.asyncio
async def test_list_directory_returns_full_paths(tmp_path):
    (tmp_path / "foo.py").write_text("")
    result = await list_directory(str(tmp_path))
    assert str(tmp_path / "foo.py") in result


@pytest.mark.asyncio
async def test_list_directory_glob_recursive(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.py").write_text("")
    (tmp_path / "top.py").write_text("")
    result = await list_directory(str(tmp_path), pattern="**/*.py")
    assert "deep.py" in result
    assert "top.py" in result
