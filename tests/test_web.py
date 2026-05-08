from __future__ import annotations

import json

import aiohttp
import pytest
from aiohttp import web as aiohttp_web

from jadebot.web import make_app


pytestmark = pytest.mark.asyncio


@pytest.fixture
async def server(service):
    app = make_app(service)
    runner = aiohttp_web.AppRunner(app)
    await runner.setup()
    site = aiohttp_web.TCPSite(runner, host="127.0.0.1", port=0)
    await site.start()
    # aiohttp >= 3.9 exposes the bound port via the runner.
    sockets = list(runner.sites)[0]._server.sockets  # type: ignore[attr-defined]
    port = sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    async with aiohttp.ClientSession() as session:
        yield session, base
    await runner.cleanup()


async def test_chroma_default_render(server):
    session, base = server
    async with session.get(f"{base}/chroma") as r:
        assert r.status == 200
        body = await r.text()
    assert "Deaths" in body
    assert "#00FF00" in body  # default green chroma
    assert "#FF0000" in body  # default red text


async def test_chroma_query_overrides(server):
    session, base = server
    async with session.get(f"{base}/chroma?label=Skill+Issues&bg=000000&fg=FFFF00&size=15vh") as r:
        body = await r.text()
    assert "Skill Issues" in body
    assert "#000000" in body
    assert "#FFFF00" in body
    assert "15vh" in body


async def test_chroma_rejects_invalid_color(server):
    session, base = server
    async with session.get(f"{base}/chroma?bg=javascript:alert(1)") as r:
        body = await r.text()
    # Falls back to default when the color is invalid.
    assert "#00FF00" in body
    assert "javascript:" not in body


async def test_increment_decrement_undo_roundtrip(server):
    session, base = server
    async with session.post(f"{base}/admin/increment") as r:
        s1 = await r.json()
    async with session.post(f"{base}/admin/increment") as r:
        s2 = await r.json()
    assert s1["count"] == 1 and s2["count"] == 2
    assert s2["session"] == 2
    assert s2["can_undo"] is True

    async with session.post(f"{base}/admin/undo") as r:
        s3 = await r.json()
    assert s3["count"] == 1 and s3["undone"] is True

    async with session.post(f"{base}/admin/decrement") as r:
        s4 = await r.json()
    assert s4["count"] == 0


async def test_set_validates_input(server):
    session, base = server
    async with session.post(f"{base}/admin/set", data={"count": "nope"}) as r:
        assert r.status == 400
    async with session.post(f"{base}/admin/set", data={"count": "-1"}) as r:
        assert r.status == 400
    async with session.post(f"{base}/admin/set", data={"count": "42"}) as r:
        s = await r.json()
    assert s["count"] == 42


async def test_session_reset(server):
    session, base = server
    await session.post(f"{base}/admin/increment")
    await session.post(f"{base}/admin/increment")
    async with session.post(f"{base}/admin/session/reset") as r:
        s = await r.json()
    assert s["session"] == 0
    assert s["count"] == 2  # total unchanged


async def test_leaderboard_html_and_json(server, service, db):
    session, base = server
    # Empty leaderboard.
    async with session.get(f"{base}/leaderboard") as r:
        body = await r.text()
    assert "No witnesses" in body

    service.helix_stub.chatters = [{"user_login": "alice"}]
    await service.increment()

    async with session.get(f"{base}/leaderboard") as r:
        body = await r.text()
    assert "alice" in body
    async with session.get(f"{base}/leaderboard?format=json") as r:
        data = await r.json()
    assert data == [{"user_login": "alice", "count": 1}]


async def test_export_csv(server, service):
    session, base = server
    service.helix_stub.chatters = [{"user_login": "alice", "user_id": "1"}]
    await service.increment()
    async with session.get(f"{base}/admin/export.csv") as r:
        assert r.status == 200
        assert r.headers["Content-Type"].startswith("text/csv")
        body = await r.text()
    lines = body.strip().splitlines()
    assert lines[0] == "user_login,user_id,witnessed_deaths,last_seen_unix"
    assert lines[1].startswith("alice,1,1,")


async def test_healthz(server):
    session, base = server
    async with session.get(f"{base}/healthz") as r:
        data = await r.json()
    assert data["status"] == "ok"
    assert "count" in data and "session" in data


async def test_sse_emits_initial_and_updates(server):
    session, base = server
    async with session.get(f"{base}/events") as resp:
        # First three lines: retry preamble (with blank), then initial data, then blank.
        line1 = await resp.content.readuntil(b"\n\n")
        assert b"retry: 5000" in line1
        line2 = await resp.content.readuntil(b"\n\n")
        assert line2.startswith(b"data: ")
        initial = json.loads(line2[len(b"data: ") : -2])
        assert initial["count"] == 0

        # Trigger an update from a parallel request.
        await session.post(f"{base}/admin/increment")
        line3 = await resp.content.readuntil(b"\n\n")
        # A keepalive can race in front of the data line; if so, read the next.
        if line3.startswith(b": keepalive"):
            line3 = await resp.content.readuntil(b"\n\n")
        assert line3.startswith(b"data: ")
        update = json.loads(line3[len(b"data: ") : -2])
        assert update["count"] == 1
