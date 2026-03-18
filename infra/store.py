# -*- coding: utf-8 -*-
"""Atomic JSON persistence with per-file locking."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from typing import Any, Callable

log = logging.getLogger("agentic.store")


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)


def _load_sync(path: str, default: Any = None) -> dict:
    """Load JSON file synchronously. Falls back to .bak on corruption."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default if default is not None else {}
    except json.JSONDecodeError as e:
        log.warning("Corrupt JSON at %s: %s, trying backup", path, e)
        bak = path + ".bak"
        if os.path.exists(bak):
            try:
                with open(bak, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e2:
                log.warning("Backup also corrupt at %s: %s", bak, e2)
        return default if default is not None else {}


def _save_sync(path: str, data: dict) -> None:
    """Atomic write: temp file -> os.replace -> .bak backup."""
    _ensure_dir(path)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    try:
        shutil.copy2(path, path + ".bak")
    except Exception:
        pass


class JsonStore:
    """Async JSON persistence with per-file locking and atomic writes."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, path: str) -> asyncio.Lock:
        if path not in self._locks:
            self._locks[path] = asyncio.Lock()
        # Sweep unlocked entries when dict grows large
        if len(self._locks) > 50:
            for k in list(self._locks):
                if k != path and not self._locks[k].locked():
                    del self._locks[k]
        return self._locks[path]

    async def load(self, path: str, default: Any = None) -> dict:
        """Load JSON from file."""
        return await asyncio.to_thread(_load_sync, path, default)

    async def save(self, path: str, data: dict) -> None:
        """Atomically save JSON to file."""
        await asyncio.to_thread(_save_sync, path, data)

    async def update(self, path: str, updater: Callable[[dict], dict]) -> dict:
        """Atomically read-modify-write under lock.

        The updater receives the current data dict and must return the new dict.
        """
        lock = self._get_lock(path)
        async with lock:
            data = await self.load(path, {})
            data = updater(data)
            await self.save(path, data)
            return data

    async def update_key(self, path: str, key: str, value: Any) -> None:
        """Atomically update a single key in a JSON dict file."""
        lock = self._get_lock(path)
        async with lock:
            data = await self.load(path, {})
            data[key] = value
            await self.save(path, data)

    async def delete_key(self, path: str, key: str) -> None:
        """Atomically remove a key from a JSON dict file."""
        lock = self._get_lock(path)
        async with lock:
            data = await self.load(path, {})
            data.pop(key, None)
            await self.save(path, data)
