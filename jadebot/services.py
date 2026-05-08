from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Awaitable, Callable, Optional

import aiosqlite

from . import storage
from .config import Config
from .helix import HelixClient

log = logging.getLogger(__name__)


# Callback signature: announce(new_count, witness_count). Awaitable, never raises to caller.
AnnounceCallback = Callable[[int, int], Awaitable[None]]


class KillCounterService:
    def __init__(self, db: aiosqlite.Connection, helix: HelixClient, config: Config) -> None:
        self._db = db
        self._helix = helix
        self._config = config
        self._count: int = 0
        self._session_count: int = 0
        self._subs: set[asyncio.Queue[str]] = set()
        self._lock = asyncio.Lock()
        # History of (action, prev_count, witnessed_logins) for undo. Bounded.
        self._history: deque[tuple[str, int, list[str]]] = deque(
            maxlen=max(1, config.history_size)
        )
        self._announce: Optional[AnnounceCallback] = None

    async def load(self) -> None:
        self._count = await storage.get_count(self._db)
        self._session_count = 0
        self._history.clear()

    def set_announce_callback(self, cb: Optional[AnnounceCallback]) -> None:
        self._announce = cb

    async def get_count(self) -> int:
        return self._count

    async def get_session_count(self) -> int:
        return self._session_count

    async def get_state(self) -> dict:
        return {
            "count": self._count,
            "session": self._session_count,
            "can_undo": bool(self._history),
        }

    async def increment(self) -> int:
        announce_args: Optional[tuple[int, int]] = None
        async with self._lock:
            prev = self._count
            new = await storage.bump_count(self._db, +1)
            self._count = new
            self._session_count += 1
            credited: list[str] = []
            try:
                logins = await self.get_present_users()
                if logins:
                    await storage.bump_witnesses(self._db, logins)
                    credited = list(logins)
                    log.info("Death recorded; credited %d witnesses.", len(credited))
                else:
                    log.info("Death recorded; no present users to credit.")
            except Exception:
                log.exception(
                    "Failed to credit witnesses for death #%d (count was still incremented).",
                    new,
                )
            self._history.append(("increment", prev, credited))
            announce_args = (new, len(credited))
        await self._broadcast()
        cb = self._announce
        if cb is not None and announce_args is not None:
            try:
                await cb(*announce_args)
            except Exception:
                log.exception("Announcement callback failed.")
        return new

    async def decrement(self) -> int:
        async with self._lock:
            prev = self._count
            new = await storage.bump_count(self._db, -1)
            self._count = new
            self._session_count = max(0, self._session_count - 1)
            self._history.append(("decrement", prev, []))
        await self._broadcast()
        return new

    async def set_count(self, n: int) -> int:
        async with self._lock:
            prev = self._count
            new = await storage.set_count(self._db, n)
            self._count = new
            # Set is intentionally session-neutral: don't move session_count.
            self._history.append(("set", prev, []))
        await self._broadcast()
        return new

    async def undo(self) -> Optional[int]:
        """Reverse the most recent action. Returns the new count, or None if nothing to undo."""
        async with self._lock:
            if not self._history:
                return None
            action, prev, credited = self._history.pop()
            new = await storage.set_count(self._db, prev)
            self._count = new
            if action == "increment":
                self._session_count = max(0, self._session_count - 1)
                if credited:
                    try:
                        await storage.decrement_witnesses(self._db, credited)
                    except Exception:
                        log.exception("Failed to roll back witness credits.")
            elif action == "decrement":
                self._session_count += 1
        await self._broadcast()
        return new

    async def reset_session(self) -> int:
        async with self._lock:
            self._session_count = 0
        await self._broadcast()
        return self._session_count

    async def top_witnesses(self, limit: int = 5) -> list[tuple[str, int]]:
        return await storage.top_witnesses(self._db, limit=limit)

    async def all_witnesses(self) -> list[tuple[str, Optional[str], int, int]]:
        return await storage.all_witnesses(self._db)

    async def get_present_users(self) -> list[str]:
        """Helix Get Chatters first, falling back to recent speakers.

        For Helix-sourced chatters, also persist (login, user_id) so subsequent
        exports / leaderboards have a stable user_id even for lurkers.
        """
        try:
            chatters = await self._helix.get_chatters()
        except Exception:
            log.exception("Helix get_chatters raised; using fallback.")
            chatters = []
        if chatters:
            now = int(time.time())
            present: list[str] = []
            for c in chatters:
                login = (c.get("user_login") or c.get("user_name") or "").lower()
                if not login or login == self._config.bot_username:
                    continue
                user_id = c.get("user_id") or None
                # Cache the user_id so the export and leaderboard have it later.
                try:
                    await storage.upsert_speaker(
                        self._db, user_login=login, user_id=user_id, last_seen=now
                    )
                except Exception:
                    log.exception("Failed to cache chatter %s", login)
                present.append(login)
            if present:
                return present
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
        payload = json.dumps(
            {
                "count": self._count,
                "session": self._session_count,
                "can_undo": bool(self._history),
            }
        )
        dropped = 0
        for q in list(self._subs):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dropped += 1
        if dropped:
            log.debug("SSE broadcast: dropped frame for %d slow subscriber(s).", dropped)

    async def aclose(self) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait("__close__")
            except asyncio.QueueFull:
                pass
        self._subs.clear()
