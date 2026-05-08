from __future__ import annotations

import asyncio
import logging
import signal

from aiohttp import web

from .config import Config
from .helix import HelixClient
from .services import KillCounterService
from . import storage
from .twitch_bot import JadeBot
from .web import make_app

log = logging.getLogger("jadebot")


async def amain() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = Config.from_env()

    db = await storage.open_db(config.db_path)
    helix = HelixClient(
        client_id=config.client_id,
        token=config.helix_token,
        broadcaster_id=config.broadcaster_user_id,
        moderator_id=config.bot_user_id,
    )
    service = KillCounterService(db, helix, config)
    await service.load()

    app = make_app(service)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=config.web_host, port=config.web_port)
    await site.start()
    log.info("Web server listening on http://%s:%s", config.web_host, config.web_port)

    bot = JadeBot(config, db, service)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        if not stop.is_set():
            log.info("Shutdown signal received.")
            stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, RuntimeError):
            pass

    bot_task = asyncio.create_task(bot.start(), name="twitch-bot")
    stop_task = asyncio.create_task(stop.wait(), name="stop-signal")

    done, _pending = await asyncio.wait(
        {bot_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
    )

    if bot_task in done and not stop.is_set():
        exc = bot_task.exception()
        if exc:
            log.error("Twitch bot task crashed: %s", exc, exc_info=exc)

    log.info("Shutting down...")
    try:
        await bot.close()
    except Exception:
        log.exception("Error closing bot.")
    if not bot_task.done():
        bot_task.cancel()
        try:
            await bot_task
        except (asyncio.CancelledError, Exception):
            pass

    await service.aclose()
    try:
        await runner.cleanup()
    except Exception:
        log.exception("Error cleaning up web runner.")
    try:
        await helix.close()
    except Exception:
        log.exception("Error closing helix client.")
    try:
        await db.close()
    except Exception:
        log.exception("Error closing database.")


def run() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
