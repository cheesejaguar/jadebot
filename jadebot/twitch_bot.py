from __future__ import annotations

import logging
import time
from typing import Optional

import aiosqlite
from twitchio.ext import commands

from . import storage
from .config import Config
from .services import KillCounterService

log = logging.getLogger(__name__)


def _is_privileged(ctx: commands.Context) -> bool:
    """True if the message author is a moderator or the broadcaster."""
    author = ctx.author
    if author is None:
        return False
    return bool(getattr(author, "is_mod", False) or getattr(author, "is_broadcaster", False))


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

    async def announce_death(self, count: int, witnesses: int) -> None:
        """Used as the announcement callback wired from __main__."""
        if not self._config.announce_deaths:
            return
        channel = self.get_channel(self._config.broadcaster_login)
        if channel is None:
            log.warning("Cannot announce death: not joined to #%s yet.", self._config.broadcaster_login)
            return
        if witnesses > 0:
            text = (
                f"Death #{count} recorded — {witnesses} witness"
                f"{'es' if witnesses != 1 else ''} credited."
            )
        else:
            text = f"Death #{count} recorded."
        try:
            await channel.send(text)
        except Exception:
            log.exception("Failed to send death announcement to chat.")

    @commands.cooldown(rate=1, per=2.0, bucket=commands.Bucket.user)
    @commands.command(name="deaths")
    async def cmd_deaths(self, ctx: commands.Context, target: Optional[str] = None) -> None:
        broadcaster = self._config.broadcaster_login
        if target is None:
            count = await self._service.get_count()
            session = await self._service.get_session_count()
            await ctx.send(
                f"{broadcaster} has died {count} time"
                f"{'' if count == 1 else 's'} ({session} this session)."
            )
            return
        login = target.lstrip("@").strip().lower()
        if not login:
            count = await self._service.get_count()
            await ctx.send(f"{broadcaster} has died {count} times.")
            return
        n = await storage.get_witness_count(self._db, login)
        if n == 0:
            await ctx.send(
                f"@{login} hasn't witnessed any of {broadcaster}'s deaths yet."
            )
        else:
            await ctx.send(
                f"@{login} has witnessed {n} of {broadcaster}'s death"
                f"{'' if n == 1 else 's'}."
            )

    @commands.cooldown(rate=1, per=5.0, bucket=commands.Bucket.channel)
    @commands.command(name="topdeaths", aliases=("topwitnesses",))
    async def cmd_topdeaths(self, ctx: commands.Context) -> None:
        rows = await self._service.top_witnesses(limit=5)
        if not rows:
            await ctx.send("No witnesses recorded yet.")
            return
        parts = [f"{i+1}. {login} ({n})" for i, (login, n) in enumerate(rows)]
        await ctx.send("Top witnesses: " + " · ".join(parts))

    @commands.command(name="death")
    async def cmd_death(self, ctx: commands.Context) -> None:
        """Mod/broadcaster shortcut: increment the death counter from chat."""
        if not _is_privileged(ctx):
            return
        new = await self._service.increment()
        if not self._config.announce_deaths:
            await ctx.send(f"Death #{new} recorded.")

    @commands.command(name="undeath", aliases=("undodeath",))
    async def cmd_undeath(self, ctx: commands.Context) -> None:
        """Mod/broadcaster shortcut: undo the most recent counter action."""
        if not _is_privileged(ctx):
            return
        new = await self._service.undo()
        if new is None:
            await ctx.send("Nothing to undo.")
        else:
            await ctx.send(f"Undid last action — count is now {new}.")
