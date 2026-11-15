"""
Minimal Music Assistant stand-ins that make ``@use_cache`` work in unit tests.

The provider hands its payload caching to the Music Assistant cache controller via
``@use_cache``.  A provider under test therefore needs the slice of
``MusicAssistant`` that decorator touches (``mass.cache`` plus ``mass.create_task``)
and a cache that stores entries the same way the controller does.  The managed
objects and the fake client never suspend, so a scheduled task can be driven to
completion immediately instead of being left pending when a test ends.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Coroutine
from typing import Any

from music_assistant.helpers.json import json_dumps, json_loads

#: Expiration used when a test does not care how long the entry stays valid.
DEFAULT_CACHE_EXPIRATION = 3600


def drive_coroutine(coro: Coroutine[Any, Any, Any]) -> Any:
    """Drive *coro* to completion; fail loudly when it waits on real I/O."""
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value
    coro.close()
    msg = "fake create_task cannot await real I/O"
    raise RuntimeError(msg)


class FakeCache:
    """In-memory stand-in for ``MusicAssistant.cache`` as used by ``@use_cache``."""

    def __init__(self) -> None:
        """Start with an empty store."""
        self.entries: dict[tuple[str, int, str], tuple[Any, int]] = {}
        self.set_calls: list[str] = []

    async def get_with_freshness(
        self,
        key: str,
        provider: str = "default",
        category: int = 0,
        checksum: str | int | None = None,
        allow_bypass: bool | None = None,
        base_class: Any = None,
        include_expired: bool = False,
    ) -> tuple[Any, bool, bool]:
        """Return ``(data, is_fresh, found)`` like the real cache controller."""
        entry = self.entries.get((provider, category, key))
        if entry is None:
            return None, False, False
        data, expires = entry
        is_fresh = expires >= int(time.time())
        if not is_fresh and not include_expired:
            return None, False, False
        return data, is_fresh, True

    async def set(
        self,
        key: str,
        data: Any,
        expiration: int = DEFAULT_CACHE_EXPIRATION,
        provider: str = "default",
        category: int = 0,
        checksum: str | int | None = None,
        persistent: bool = False,
        allow_expired_cache: bool = False,
    ) -> None:
        """Store *data* under *key* for *expiration* seconds, as JSON like the real cache."""
        # The real controller dumps the value to JSON on write and loads it on read.
        self.entries[(provider, category, key)] = (
            json_loads(json_dumps(data)),
            int(time.time() + expiration),
        )
        self.set_calls.append(key)


class FakeMassBase:
    """Base for the ``MusicAssistant`` stand-ins: cache and task plumbing."""

    def __init__(self) -> None:
        """Attach an empty in-memory cache."""
        self.cache = FakeCache()

    def create_task(
        self, coro: Coroutine[Any, Any, Any], task_id: str | None = None
    ) -> asyncio.Future[Any]:
        """Run *coro* immediately and return its result as an already-done future."""
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        try:
            future.set_result(drive_coroutine(coro))
        except Exception as err:
            future.set_exception(err)
        return future
