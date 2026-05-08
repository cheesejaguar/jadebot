from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)

CHATTERS_URL = "https://api.twitch.tv/helix/chat/chatters"


class HelixClient:
    """Minimal Helix client. Currently exposes only Get Chatters."""

    def __init__(
        self,
        *,
        client_id: str,
        token: str,
        broadcaster_id: str,
        moderator_id: str,
    ) -> None:
        self._client_id = client_id
        # Helix expects a bearer token without the "oauth:" prefix.
        self._token = token[6:] if token.lower().startswith("oauth:") else token
        self._broadcaster_id = broadcaster_id
        self._moderator_id = moderator_id
        self._session: Optional[aiohttp.ClientSession] = None
        # Latch so we don't spam the same WARN line for a misconfigured scope.
        self._auth_warned: bool = False

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(
                headers={
                    "Client-Id": self._client_id,
                    "Authorization": f"Bearer {self._token}",
                },
                timeout=timeout,
            )
        return self._session

    async def get_chatters(self) -> list[dict]:
        """Return the list of users currently in chat. Returns [] on auth failure or persistent errors."""
        session = await self._ensure_session()
        chatters: list[dict] = []
        cursor: Optional[str] = None
        attempts_per_page = 3
        while True:
            params = {
                "broadcaster_id": self._broadcaster_id,
                "moderator_id": self._moderator_id,
                "first": "1000",
            }
            if cursor:
                params["after"] = cursor

            data: Optional[dict] = None
            for attempt in range(attempts_per_page):
                try:
                    async with session.get(CHATTERS_URL, params=params) as resp:
                        if resp.status in (401, 403):
                            if not self._auth_warned:
                                body = await resp.text()
                                log.error(
                                    "Helix Get Chatters denied (%s). Falling back to "
                                    "recent speakers. Make the bot a moderator and grant "
                                    "'moderator:read:chatters' to its token. Body: %s",
                                    resp.status,
                                    body[:200],
                                )
                                self._auth_warned = True
                            return []
                        if 500 <= resp.status < 600:
                            log.warning(
                                "Helix Get Chatters %s; retry %d/%d",
                                resp.status,
                                attempt + 1,
                                attempts_per_page,
                            )
                            raise aiohttp.ClientError(f"server error {resp.status}")
                        resp.raise_for_status()
                        data = await resp.json()
                        # Re-arm the auth warning once we successfully talk to Helix.
                        self._auth_warned = False
                        break
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    if attempt == attempts_per_page - 1:
                        log.warning(
                            "Helix Get Chatters failed after %d attempts: %s",
                            attempts_per_page,
                            e,
                        )
                        return []
                    await asyncio.sleep(0.5 * (2**attempt))
            if data is None:
                return []
            chatters.extend(data.get("data", []))
            cursor = (data.get("pagination") or {}).get("cursor")
            if not cursor:
                break
        return chatters

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None
