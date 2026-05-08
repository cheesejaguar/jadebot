from __future__ import annotations

import asyncio
import logging
import signal
import sys

from aiohttp import web

from .config import Config
from .helix import HelixClient
from .services import KillCounterService
from . import storage
from .twitch_bot import JadeBot
from .web import make_app

log = logging.getLogger("jadebot")


def _configure_logging() -> None:
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    # Force UTF-8 even on Windows / non-UTF-8 terminals so display names with emoji
    # don't crash the bot.
    try:
        handler.stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


async def amain() -> int:
    _configure_logging()

    try:
        config = Config.from_env()
    except RuntimeError as e:
        log.error("Configuration error: %s", e)
        return 2

    try:
        db = await storage.open_db(config.db_path)
    except Exception as e:
        log.error("Failed to open database at %s: %s", config.db_path, e)
        return 3

    helix = HelixClient(
        client_id=config.client_id,
        token=config.helix_token,
        broadcaster_id=config.broadcaster_user_id,
        moderator_id=config.bot_user_id,
    )
    service = KillCounterService(db, helix, config)
    await service.load()

    runner = web.AppRunner(make_app(service))
    await runner.setup()
    site = web.TCPSite(runner, host=config.web_host, port=config.web_port)
    try:
        await site.start()
    except OSError as e:
        log.error(
            "Failed to bind web server on %s:%s — %s. "
            "Is another instance already running?",
            config.web_host,
            config.web_port,
            e,
        )
        await runner.cleanup()
        await helix.close()
        await db.close()
        return 4
    log.info("Web server listening on http://%s:%s", config.web_host, config.web_port)

    bot = JadeBot(config, db, service)
    service.set_announce_callback(bot.announce_death)

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

    exit_code = 0
    if bot_task in done and not stop.is_set():
        exc = bot_task.exception()
        if exc is not None:
            log.error("Twitch bot task crashed: %s", exc, exc_info=exc)
            exit_code = 1

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
    return exit_code


def run() -> None:
    try:
        rc = asyncio.run(amain())
    except KeyboardInterrupt:
        rc = 0
    sys.exit(rc)


if __name__ == "__main__":
    run()
