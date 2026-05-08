from __future__ import annotations

import os
import time
from typing import Iterable, Optional

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS deaths (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  count INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO deaths(id, count) VALUES (1, 0);

CREATE TABLE IF NOT EXISTS witnesses (
  user_login TEXT PRIMARY KEY,
  user_id    TEXT,
  count      INTEGER NOT NULL DEFAULT 0,
  last_seen  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_witnesses_last_seen ON witnesses(last_seen);

CREATE TABLE IF NOT EXISTS chat_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  user_id TEXT,
  user_login TEXT,
  display_name TEXT,
  message TEXT NOT NULL,
  is_action INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_chat_log_ts ON chat_log(ts);
CREATE INDEX IF NOT EXISTS idx_chat_log_user ON chat_log(user_login);
"""


async def open_db(path: str) -> aiosqlite.Connection:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    db = await aiosqlite.connect(path)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.executescript(SCHEMA)
    await db.commit()
    return db


async def get_count(db: aiosqlite.Connection) -> int:
    async with db.execute("SELECT count FROM deaths WHERE id = 1") as cur:
        row = await cur.fetchone()
        return int(row[0]) if row else 0


async def set_count(db: aiosqlite.Connection, n: int) -> int:
    n = max(0, int(n))
    await db.execute("UPDATE deaths SET count = ? WHERE id = 1", (n,))
    await db.commit()
    return n


async def bump_count(db: aiosqlite.Connection, delta: int) -> int:
    """Atomically adjust the death count and return the new value (clamped at >= 0)."""
    await db.execute("BEGIN IMMEDIATE")
    try:
        async with db.execute("SELECT count FROM deaths WHERE id = 1") as cur:
            row = await cur.fetchone()
        current = int(row[0]) if row else 0
        new = max(0, current + int(delta))
        await db.execute("UPDATE deaths SET count = ? WHERE id = 1", (new,))
        await db.commit()
        return new
    except Exception:
        await db.rollback()
        raise


async def log_chat(
    db: aiosqlite.Connection,
    *,
    ts: int,
    user_id: Optional[str],
    user_login: Optional[str],
    display_name: Optional[str],
    message: str,
    is_action: bool = False,
) -> None:
    await db.execute(
        "INSERT INTO chat_log(ts, user_id, user_login, display_name, message, is_action)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (ts, user_id, user_login, display_name, message, 1 if is_action else 0),
    )
    await db.commit()


async def upsert_speaker(
    db: aiosqlite.Connection,
    *,
    user_login: str,
    user_id: Optional[str],
    last_seen: Optional[int] = None,
) -> None:
    ts = int(last_seen if last_seen is not None else time.time())
    await db.execute(
        "INSERT INTO witnesses(user_login, user_id, count, last_seen)"
        " VALUES (?, ?, 0, ?)"
        " ON CONFLICT(user_login) DO UPDATE SET"
        "   user_id = COALESCE(excluded.user_id, witnesses.user_id),"
        "   last_seen = excluded.last_seen",
        (user_login.lower(), user_id, ts),
    )
    await db.commit()


async def bump_witnesses(db: aiosqlite.Connection, logins: Iterable[str]) -> int:
    """Increment witness count for each login. Creates rows that don't exist. Returns rows touched."""
    now = int(time.time())
    seen = {l.lower() for l in logins if l}
    if not seen:
        return 0
    await db.executemany(
        "INSERT INTO witnesses(user_login, user_id, count, last_seen)"
        " VALUES (?, NULL, 1, ?)"
        " ON CONFLICT(user_login) DO UPDATE SET"
        "   count = witnesses.count + 1,"
        "   last_seen = excluded.last_seen",
        [(login, now) for login in seen],
    )
    await db.commit()
    return len(seen)


async def get_witness_count(db: aiosqlite.Connection, user_login: str) -> int:
    async with db.execute(
        "SELECT count FROM witnesses WHERE user_login = ?", (user_login.lower(),)
    ) as cur:
        row = await cur.fetchone()
        return int(row[0]) if row else 0


async def recent_speakers(db: aiosqlite.Connection, since_ts: int) -> list[str]:
    async with db.execute(
        "SELECT user_login FROM witnesses WHERE last_seen >= ? AND user_login IS NOT NULL",
        (int(since_ts),),
    ) as cur:
        rows = await cur.fetchall()
    return [r[0] for r in rows if r[0]]
