from __future__ import annotations

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

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Client-Id": self._client_id,
                    "Authorization": f"Bearer {self._token}",
                }
            )
        return self._session

    async def get_chatters(self) -> list[dict]:
        """Return the list of users currently in chat. Returns [] on auth failure."""
        session = await self._ensure_session()
        chatters: list[dict] = []
        cursor: Optional[str] = None
        while True:
            params = {
                "broadcaster_id": self._broadcaster_id,
                "moderator_id": self._moderator_id,
                "first": "1000",
            }
            if cursor:
                params["after"] = cursor
            try:
                async with session.get(CHATTERS_URL, params=params) as resp:
                    if resp.status in (401, 403):
                        body = await resp.text()
                        log.warning(
                            "Helix Get Chatters denied (%s). Falling back to recent speakers. "
                            "Make sure the bot is a moderator and the token has "
                            "'moderator:read:chatters'. Body: %s",
                            resp.status,
                            body[:200],
                        )
                        return []
                    resp.raise_for_status()
                    data = await resp.json()
            except aiohttp.ClientError as e:
                log.warning("Helix Get Chatters network error: %s", e)
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
