"""Stand-ins for the Music Assistant objects the live e2e scripts need.

``BilibiliProvider`` hands its payload caching to the Music Assistant cache
controller through ``@use_cache``, so a bare provider instance needs
``mass.cache`` and ``mass.create_task`` next to the shared aiohttp session.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp


class InMemoryCache:
    """Tiny in-memory cache with the ``@use_cache`` public surface."""

    def __init__(self) -> None:
        """Start with an empty store."""
        self._entries: dict[tuple[str, int, str], tuple[Any, int]] = {}

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
        """Return ``(data, is_fresh, found)`` for *key* like the real controller."""
        entry = self._entries.get((provider, category, key))
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
        expiration: int = 3600,
        provider: str = "default",
        category: int = 0,
        checksum: str | int | None = None,
        persistent: bool = False,
        allow_expired_cache: bool = False,
    ) -> None:
        """Store *data* under *key* for *expiration* seconds."""
        self._entries[(provider, category, key)] = (data, int(time.time() + expiration))


class E2EMass:
    """The slice of ``mass`` that BilibiliClient and ``@use_cache`` touch."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Keep the shared session, an in-memory cache and the background tasks."""
        self.http_session = session
        self.cache = InMemoryCache()
        self._tasks: set[asyncio.Task[Any]] = set()

    def create_task(self, coro: Any, task_id: str | None = None) -> asyncio.Task[Any]:
        """Schedule *coro* the way ``MusicAssistant.create_task`` does."""
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task
