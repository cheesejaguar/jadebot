from __future__ import annotations

import time

import pytest

from jadebot import storage


pytestmark = pytest.mark.asyncio


async def test_initial_count_is_zero(db):
    assert await storage.get_count(db) == 0


async def test_bump_and_set_count(db):
    assert await storage.bump_count(db, +1) == 1
    assert await storage.bump_count(db, +1) == 2
    assert await storage.set_count(db, 10) == 10
    # Can't go below zero.
    assert await storage.bump_count(db, -100) == 0


async def test_set_count_clamps_negative(db):
    assert await storage.set_count(db, -5) == 0


async def test_witnesses_lifecycle(db):
    now = int(time.time())
    await storage.upsert_speaker(db, user_login="Alice", user_id="1", last_seen=now)
    await storage.upsert_speaker(db, user_login="bob", user_id="2", last_seen=now)
    await storage.bump_witnesses(db, ["Alice", "bob", "Alice"])
    # Logins are case-insensitive; deduped.
    assert await storage.get_witness_count(db, "alice") == 1
    assert await storage.get_witness_count(db, "bob") == 1
    assert await storage.get_witness_count(db, "carol") == 0

    await storage.bump_witnesses(db, ["alice", "carol"])
    assert await storage.get_witness_count(db, "alice") == 2
    assert await storage.get_witness_count(db, "carol") == 1

    top = await storage.top_witnesses(db, limit=10)
    assert top[0] == ("alice", 2)
    assert ("bob", 1) in top
    assert ("carol", 1) in top


async def test_decrement_witnesses_clamps(db):
    now = int(time.time())
    await storage.upsert_speaker(db, user_login="alice", user_id="1", last_seen=now)
    await storage.bump_witnesses(db, ["alice"])
    assert await storage.get_witness_count(db, "alice") == 1
    await storage.decrement_witnesses(db, ["alice", "alice"])  # dedup -> single decrement
    assert await storage.get_witness_count(db, "alice") == 0
    # Should not go negative.
    await storage.decrement_witnesses(db, ["alice"])
    assert await storage.get_witness_count(db, "alice") == 0


async def test_recent_speakers_window(db):
    now = int(time.time())
    await storage.upsert_speaker(db, user_login="recent", user_id="1", last_seen=now)
    await storage.upsert_speaker(db, user_login="ancient", user_id="2", last_seen=now - 3600)
    speakers = await storage.recent_speakers(db, now - 60)
    assert "recent" in speakers
    assert "ancient" not in speakers


async def test_chat_log_insertion(db):
    now = int(time.time())
    await storage.log_chat(
        db,
        ts=now,
        user_id="1",
        user_login="alice",
        display_name="Alice",
        message="hello world",
    )
    async with db.execute("SELECT user_login, message FROM chat_log") as cur:
        rows = await cur.fetchall()
    assert rows == [("alice", "hello world")]


async def test_all_witnesses_export_shape(db):
    now = int(time.time())
    await storage.upsert_speaker(db, user_login="alice", user_id="1", last_seen=now)
    await storage.bump_witnesses(db, ["alice"])
    rows = await storage.all_witnesses(db)
    assert len(rows) == 1
    login, user_id, count, last_seen = rows[0]
    assert login == "alice" and user_id == "1" and count == 1 and isinstance(last_seen, int)
