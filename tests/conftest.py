from __future__ import annotations

import os
import tempfile

import pytest_asyncio

from jadebot import storage
from jadebot.config import Config
from jadebot.services import KillCounterService


class StubHelix:
    """In-memory stand-in for HelixClient. Tests can preload `chatters`."""

    def __init__(self, chatters: list[dict] | None = None) -> None:
        self.chatters = chatters or []
        self.calls = 0

    async def get_chatters(self) -> list[dict]:
        self.calls += 1
        return list(self.chatters)

    async def close(self) -> None:
        pass


def make_config(**overrides) -> Config:
    base = dict(
        bot_username="bot",
        oauth_token="oauth:xxxx",
        client_id="cid",
        broadcaster_login="jade_infinite",
        broadcaster_user_id="111",
        bot_user_id="222",
        web_host="127.0.0.1",
        web_port=18080,
        db_path=":memory:",
        presence_window_sec=600,
        announce_deaths=False,
        history_size=10,
    )
    base.update(overrides)
    return Config(**base)


@pytest_asyncio.fixture
async def db():
    fd, path = tempfile.mkstemp(prefix="jb-test-", suffix=".db")
    os.close(fd)
    conn = await storage.open_db(path)
    try:
        yield conn
    finally:
        await conn.close()
        for ext in ("", "-wal", "-shm"):
            p = path + ext
            if os.path.exists(p):
                os.remove(p)


@pytest_asyncio.fixture
async def service(db):
    helix = StubHelix()
    cfg = make_config()
    svc = KillCounterService(db, helix, cfg)
    await svc.load()
    svc.helix_stub = helix  # type: ignore[attr-defined]
    return svc
