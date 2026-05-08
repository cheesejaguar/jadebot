from __future__ import annotations

import asyncio
import json
import time

import pytest

from jadebot import storage


pytestmark = pytest.mark.asyncio


async def test_increment_credits_helix_chatters(service, db):
    service.helix_stub.chatters = [
        {"user_login": "alice", "user_id": "1"},
        {"user_login": "Bob", "user_id": "2"},  # mixed case
        {"user_login": "bot"},  # bot account is filtered out
    ]
    await service.increment()
    assert await service.get_count() == 1
    assert await storage.get_witness_count(db, "alice") == 1
    assert await storage.get_witness_count(db, "bob") == 1
    assert await storage.get_witness_count(db, "bot") == 0


async def test_increment_falls_back_to_recent_speakers(service, db):
    # No chatters from Helix -> should use recent_speakers.
    now = int(time.time())
    await storage.upsert_speaker(db, user_login="recent", user_id="r", last_seen=now)
    await service.increment()
    assert await storage.get_witness_count(db, "recent") == 1


async def test_decrement_does_not_change_witnesses(service, db):
    service.helix_stub.chatters = [{"user_login": "alice", "user_id": "1"}]
    await service.increment()
    assert await storage.get_witness_count(db, "alice") == 1
    await service.decrement()
    assert await service.get_count() == 0
    # Decrement is intentionally not a witness rollback.
    assert await storage.get_witness_count(db, "alice") == 1


async def test_undo_restores_count_and_witnesses(service, db):
    service.helix_stub.chatters = [{"user_login": "alice", "user_id": "1"}]
    await service.increment()
    await service.increment()
    assert await service.get_count() == 2
    assert await storage.get_witness_count(db, "alice") == 2

    new = await service.undo()
    assert new == 1
    assert await storage.get_witness_count(db, "alice") == 1

    new = await service.undo()
    assert new == 0
    assert await storage.get_witness_count(db, "alice") == 0

    assert await service.undo() is None  # nothing left to undo


async def test_undo_set_restores_previous_value(service):
    await service.set_count(42)
    await service.set_count(7)
    new = await service.undo()
    assert new == 42
    new = await service.undo()
    assert new == 0


async def test_session_count_tracks_increments(service):
    assert await service.get_session_count() == 0
    await service.increment()
    await service.increment()
    assert await service.get_session_count() == 2
    await service.decrement()
    assert await service.get_session_count() == 1
    # Set is session-neutral.
    await service.set_count(99)
    assert await service.get_session_count() == 1
    await service.reset_session()
    assert await service.get_session_count() == 0


async def test_announcement_callback_invoked(service):
    seen: list[tuple[int, int]] = []

    async def cb(count: int, witnesses: int) -> None:
        seen.append((count, witnesses))

    service.set_announce_callback(cb)
    service.helix_stub.chatters = [{"user_login": "alice"}]
    await service.increment()
    await service.increment()
    assert seen == [(1, 1), (2, 1)]


async def test_announcement_callback_failure_does_not_break_increment(service):
    async def boom(*_args):
        raise RuntimeError("no")

    service.set_announce_callback(boom)
    await service.increment()  # must not raise
    assert await service.get_count() == 1


async def test_top_witnesses_orders_by_count_desc(service, db):
    now = int(time.time())
    for login in ("a", "b", "c"):
        await storage.upsert_speaker(db, user_login=login, user_id=login, last_seen=now)
    await storage.bump_witnesses(db, ["a", "b", "c"])
    await storage.bump_witnesses(db, ["a", "b"])
    await storage.bump_witnesses(db, ["a"])
    rows = await service.top_witnesses(limit=3)
    assert rows == [("a", 3), ("b", 2), ("c", 1)]


async def test_sse_subscribers_receive_broadcasts(service):
    q1 = service.subscribe()
    q2 = service.subscribe()
    await service.increment()
    msg1 = await asyncio.wait_for(q1.get(), timeout=1)
    msg2 = await asyncio.wait_for(q2.get(), timeout=1)
    payload1 = json.loads(msg1)
    payload2 = json.loads(msg2)
    assert payload1["count"] == 1 and payload2["count"] == 1
    assert payload1["session"] == 1
    service.unsubscribe(q1)
    service.unsubscribe(q2)
