from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

import aiosqlite

from . import storage
from .config import Config
from .helix import HelixClient

log = logging.getLogger(__name__)


class KillCounterService:
    def __init__(self, db: aiosqlite.Connection, helix: HelixClient, config: Config) -> None:
        self._db = db
        self._helix = helix
        self._config = config
        self._count: int = 0
        self._subs: set[asyncio.Queue[str]] = set()
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        self._count = await storage.get_count(self._db)

    async def get_count(self) -> int:
        return self._count

    async def increment(self) -> int:
        async with self._lock:
            new = await storage.bump_count(self._db, +1)
            self._count = new
            try:
                logins = await self.get_present_users()
                if logins:
                    await storage.bump_witnesses(self._db, logins)
                    log.info("Death recorded; credited %d witnesses.", len(logins))
                else:
                    log.info("Death recorded; no present users to credit.")
            except Exception:
                log.exception("Failed to credit witnesses (count was still incremented).")
        await self._broadcast()
        return new

    async def decrement(self) -> int:
        async with self._lock:
            new = await storage.bump_count(self._db, -1)
            self._count = new
        await self._broadcast()
        return new

    async def set_count(self, n: int) -> int:
        async with self._lock:
            new = await storage.set_count(self._db, n)
            self._count = new
        await self._broadcast()
        return new

    async def get_present_users(self) -> list[str]:
        """Helix Get Chatters first, falling back to recent speakers."""
        try:
            chatters = await self._helix.get_chatters()
        except Exception:
            log.exception("Helix get_chatters raised; using fallback.")
            chatters = []
        if chatters:
            logins = [
                (c.get("user_login") or c.get("user_name") or "").lower()
                for c in chatters
            ]
            logins = [l for l in logins if l and l != self._config.bot_username]
            if logins:
                return logins
        cutoff = int(time.time()) - self._config.presence_window_sec
        fallback = await storage.recent_speakers(self._db, cutoff)
        return [l for l in fallback if l != self._config.bot_username]

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=16)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self._subs.discard(q)

    async def _broadcast(self) -> None:
        payload = json.dumps({"count": self._count})
        for q in list(self._subs):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Slow consumer; drop the update for this client.
                pass

    async def aclose(self) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait("__close__")
            except asyncio.QueueFull:
                pass
        self._subs.clear()
