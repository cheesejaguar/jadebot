from __future__ import annotations

import pytest
from aiohttp import web as aiohttp_web

from jadebot import helix
from jadebot.helix import HelixClient


pytestmark = pytest.mark.asyncio


@pytest.fixture
async def fake_twitch(monkeypatch):
    """A local HTTP server standing in for api.twitch.tv's Get Chatters endpoint.

    Tests push responses onto `responses` (status, json_body) and inspect `requests`.
    The real HelixClient talks to it over HTTP, so request building, pagination,
    and status handling are all exercised for real.
    """
    state = {"responses": [], "requests": []}

    async def handler(request: aiohttp_web.Request) -> aiohttp_web.Response:
        state["requests"].append(
            {"query": dict(request.query), "headers": dict(request.headers)}
        )
        status, body = state["responses"].pop(0)
        return aiohttp_web.json_response(body, status=status)

    app = aiohttp_web.Application()
    app.router.add_get("/helix/chat/chatters", handler)
    runner = aiohttp_web.AppRunner(app)
    await runner.setup()
    site = aiohttp_web.TCPSite(runner, host="127.0.0.1", port=0)
    await site.start()
    port = list(runner.sites)[0]._server.sockets[0].getsockname()[1]  # type: ignore[attr-defined]
    monkeypatch.setattr(helix, "CHATTERS_URL", f"http://127.0.0.1:{port}/helix/chat/chatters")
    try:
        yield state
    finally:
        await runner.cleanup()


@pytest.fixture
async def client():
    c = HelixClient(client_id="cid", token="oauth:secret", broadcaster_id="111", moderator_id="222")
    try:
        yield c
    finally:
        await c.close()


async def test_get_chatters_builds_request_and_follows_pagination(fake_twitch, client):
    fake_twitch["responses"] = [
        (200, {"data": [{"user_login": "alice"}], "pagination": {"cursor": "abc"}}),
        (200, {"data": [{"user_login": "bob"}], "pagination": {}}),
    ]
    chatters = await client.get_chatters()
    assert chatters == [{"user_login": "alice"}, {"user_login": "bob"}]

    first, second = fake_twitch["requests"]
    assert first["query"] == {"broadcaster_id": "111", "moderator_id": "222", "first": "1000"}
    assert second["query"]["after"] == "abc"
    # The "oauth:" IRC prefix must be stripped for the Helix bearer token.
    assert first["headers"]["Authorization"] == "Bearer secret"
    assert first["headers"]["Client-Id"] == "cid"


@pytest.mark.parametrize("status", [401, 403])
async def test_get_chatters_auth_failure_returns_empty_without_retry(fake_twitch, client, status):
    fake_twitch["responses"] = [(status, {"message": "missing scope"})]
    assert await client.get_chatters() == []
    assert len(fake_twitch["requests"]) == 1


async def test_get_chatters_retries_server_errors(fake_twitch, client):
    fake_twitch["responses"] = [
        (503, {"message": "unavailable"}),
        (200, {"data": [{"user_login": "alice"}]}),
    ]
    assert await client.get_chatters() == [{"user_login": "alice"}]
    assert len(fake_twitch["requests"]) == 2
