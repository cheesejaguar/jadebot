from __future__ import annotations

import logging
import time

import aiosqlite
from twitchio.ext import commands

from . import storage
from .config import Config
from .services import KillCounterService

log = logging.getLogger(__name__)


class JadeBot(commands.Bot):
    def __init__(self, config: Config, db: aiosqlite.Connection, service: KillCounterService) -> None:
        super().__init__(
            token=config.irc_token,
            prefix="!",
            initial_channels=[config.broadcaster_login],
        )
        self._config = config
        self._db = db
        self._service = service

    async def event_ready(self) -> None:
        log.info("Bot ready as %s, joined #%s", self.nick, self._config.broadcaster_login)

    async def event_message(self, message) -> None:
        # Ignore the bot's own echoed messages and any messages with no author (system/USERNOTICE).
        if getattr(message, "echo", False):
            return
        author = getattr(message, "author", None)
        if author is None:
            return
        login = (getattr(author, "name", "") or "").lower()
        if not login or login == self._config.bot_username:
            return

        try:
            user_id = str(getattr(author, "id", "") or "") or None
            display_name = getattr(author, "display_name", None) or login
            content = message.content or ""
            ts = int(time.time())
            await storage.log_chat(
                self._db,
                ts=ts,
                user_id=user_id,
                user_login=login,
                display_name=display_name,
                message=content,
                is_action=False,
            )
            await storage.upsert_speaker(
                self._db, user_login=login, user_id=user_id, last_seen=ts
            )
        except Exception:
            log.exception("Failed to persist chat message from %s", login)

        await self.handle_commands(message)

    @commands.cooldown(rate=1, per=2.0, bucket=commands.Bucket.user)
    @commands.command(name="deaths")
    async def cmd_deaths(self, ctx: commands.Context, target: str | None = None) -> None:
        broadcaster = self._config.broadcaster_login
        if target is None:
            count = await self._service.get_count()
            await ctx.send(f"{broadcaster} has died {count} times.")
            return
        login = target.lstrip("@").strip().lower()
        if not login:
            count = await self._service.get_count()
            await ctx.send(f"{broadcaster} has died {count} times.")
            return
        n = await storage.get_witness_count(self._db, login)
        await ctx.send(
            f"@{login} has witnessed {n} of {broadcaster}'s deaths."
        )
