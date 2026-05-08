# jadebot

A Python Twitch bot + kill-counter overlay for streamer **`jade_infinite`**.

It does three things:

1. **Live death counter** — a green-screen `/chroma` page for OBS Browser
   Sources and a localhost `/admin` page with hotkeys, undo, session counter,
   and a witness leaderboard.
2. **Witness tracking** — every time the streamer logs a death, every viewer
   currently in chat is credited with one "witnessed death." Chat can ask
   `!deaths @user` to see how many they've seen.
3. **Chat logging** — all messages in the broadcaster's channel are persisted
   to SQLite for grep-able history.

Single Python process. Single asyncio loop. SQLite (WAL). No external services
required beyond Twitch itself.

---

## Quick start

The project is managed with [`uv`](https://docs.astral.sh/uv/). Install it with
`curl -LsSf https://astral.sh/uv/install.sh | sh` (or `pipx install uv`), then:

```bash
git clone <this repo> jadebot && cd jadebot
uv sync                # creates .venv and installs locked deps + test extras

cp .env.example .env
# Fill in TWITCH_BOT_USERNAME, TWITCH_OAUTH_TOKEN, TWITCH_CLIENT_ID,
# BROADCASTER_USER_ID, BOT_USER_ID. See "Configuration" below.

uv run jadebot         # or: uv run python -m jadebot
```

Then open:

- `http://<host>:8080/admin` — control surface
- `http://<host>:8080/chroma` — OBS Browser Source URL
- `http://<host>:8080/leaderboard` — witness leaderboard

By default the web server binds to `0.0.0.0` so any device on your LAN can
reach these pages. Set `WEB_HOST=127.0.0.1` to restrict to this machine.

Stop with `Ctrl-C`.

---

## OBS setup

1. Add → **Browser** source.
2. URL: `http://127.0.0.1:8080/chroma`
3. Width 1920, Height 1080 (or whatever your canvas is).
4. **Custom CSS**: leave empty.
5. Apply a **Chroma Key** filter to the source, key colour green (#00FF00).
   The default page renders pure `#00FF00` for clean keying.

The page connects to the bot over Server-Sent Events and updates with no
refresh — increment in `/admin` and OBS will animate the counter instantly.

### Customising the chroma overlay

The `/chroma` page accepts these query parameters:

| Param   | Example                  | Default   | Notes                                  |
| ------- | ------------------------ | --------- | -------------------------------------- |
| `label` | `?label=Skill+Issues`    | `Deaths`  | Up to 32 chars                         |
| `bg`    | `?bg=000000`             | `00FF00`  | Hex colour (3 or 6 digits, optional #) |
| `fg`    | `?fg=FFFF00`             | `FF0000`  | Same                                   |
| `size`  | `?size=18vh` or `?size=15` | `22vh`   | Number → vh, otherwise CSS length      |

Combine them: `/chroma?label=Yikes&bg=000000&fg=00FFFF&size=18vh`.

---

## Admin page features

- **Increment / Decrement / Set total** buttons
- **Undo** — reverses the most recent increment, decrement, or set, including
  rolling back witness credits when undoing an increment
- **Session counter** that ticks alongside the total; reset it without losing
  the lifetime count
- **Confirmation prompt** when the streamer would set the count to `0` or
  change it by more than 5 in a single edit
- **CSV export** of every witness (`/admin/export.csv`)
- **Live connection indicator** — turns red and reconnects with backoff if the
  server restarts
- **Keyboard shortcuts** (when no input is focused):
  - `+` or `=` — increment
  - `-` — decrement
  - `Z` — undo
  - `R` — reset session

The web server has no built-in authentication. With the default
`WEB_HOST=0.0.0.0`, anyone on the same LAN can reach `/admin` and bump or
reset the counter. Run on a trusted network, or set `WEB_HOST=127.0.0.1` to
restrict to the local machine only.

---

## Chat commands

| Command              | Who      | What                                             |
| -------------------- | -------- | ------------------------------------------------ |
| `!deaths`            | anyone   | Posts the current total + this-session count     |
| `!deaths @user`      | anyone   | Reports how many of jade's deaths `@user` has witnessed |
| `!topdeaths`         | anyone   | Top 5 witnesses by count (alias: `!topwitnesses`) |
| `!death`             | mods + broadcaster | Increment from chat (handy on phone) |
| `!undeath`           | mods + broadcaster | Undo the last counter action            |

Set `ANNOUNCE_DEATHS=true` in `.env` to have the bot post
`Death #N recorded — N witnesses credited.` after each increment.

---

## Configuration

All settings come from environment variables (or a `.env` file in the project
directory).

| Variable               | Required | Default              | Notes |
| ---------------------- | -------- | -------------------- | ----- |
| `TWITCH_BOT_USERNAME`  | yes      | —                    | Bot account login (lowercase) |
| `TWITCH_OAUTH_TOKEN`   | yes      | —                    | IRC token, must include `oauth:` prefix |
| `TWITCH_CLIENT_ID`     | yes      | —                    | Helix client ID for the same token |
| `BROADCASTER_LOGIN`    | no       | `jade_infinite`      | Channel to join |
| `BROADCASTER_USER_ID`  | yes      | —                    | Numeric Twitch user ID |
| `BOT_USER_ID`          | yes      | —                    | Numeric Twitch user ID |
| `WEB_HOST`             | no       | `0.0.0.0`            | Bind address (set to `127.0.0.1` for loopback only) |
| `WEB_PORT`             | no       | `8080`               | Bind port |
| `DB_PATH`              | no       | `logs/chat.db`       | SQLite file (WAL) |
| `PRESENCE_WINDOW_SEC`  | no       | `600`                | Fallback presence window when Helix is unavailable |
| `ANNOUNCE_DEATHS`      | no       | `false`              | Post to chat on every death |
| `UNDO_HISTORY`         | no       | `20`                 | How many actions to keep for undo |

**Generating tokens.** The simplest path:

1. Sign in to https://twitchtokengenerator.com as the bot account.
2. Pick scopes: `chat:read`, `chat:edit`. If the bot will be a moderator and
   you want the real viewer list (vs. the recent-speakers fallback), also pick
   `moderator:read:chatters`.
3. Copy the **Access Token** (with `oauth:` prefix) and the **Client ID**.

**User IDs.** Look them up at
https://www.streamweasels.com/tools/convert-twitch-username-to-user-id/.

---

## Witness model

When the streamer clicks **Increment**:

1. The total count is bumped (atomically — `BEGIN IMMEDIATE`).
2. The bot fetches the live viewer list via the Helix
   [Get Chatters](https://dev.twitch.tv/docs/api/reference/#get-chatters)
   endpoint.
3. Every login in that list (minus the bot itself) gets `+1` in the
   `witnesses` table.
4. SSE pushes the new state to every connected `/chroma` and `/admin` page.

If Helix Get Chatters returns 401/403 (the bot isn't a mod, or the scope is
missing), the bot falls back to "everyone who has chatted in the last
`PRESENCE_WINDOW_SEC` seconds." This degrades gracefully — the counter still
works, the leaderboard still grows, just from a smaller cohort.

Undoing an increment **also** rolls back the witness credits for that death.

---

## Development

```bash
uv sync          # installs locked deps + test extras
uv run pytest    # 28 tests, ~16s
```

To upgrade dependencies and refresh the lock:

```bash
uv lock --upgrade
uv sync
```

Layout:

```
jadebot/
├── __main__.py    # entrypoint: starts bot + web on one event loop
├── config.py      # @dataclass Config, env validation
├── storage.py     # aiosqlite helpers + schema
├── helix.py       # Get Chatters with auth/transient distinction + retry
├── services.py    # KillCounterService: counter, undo, session, SSE pub/sub
├── twitch_bot.py  # JadeBot: chat logging + commands
└── web.py         # aiohttp routes, inline HTML templates
tests/
├── conftest.py
├── test_storage.py
├── test_service.py
└── test_web.py
```

---

## Troubleshooting

- **"Helix Get Chatters denied (401/403)"** — the bot account is not a
  moderator in the channel, or its OAuth token is missing
  `moderator:read:chatters`. The fallback presence window will be used. Add
  the scope or `/mod` the bot account in chat to enable real presence.
- **"Failed to bind web server"** — another instance is already running, or
  another app is on the port. `lsof -i :8080` to find it.
- **Counter doesn't update on `/chroma`** — OBS Browser Sources cache
  aggressively; right-click the source → **Refresh** once. Subsequent updates
  arrive via SSE without further refreshes.
- **"Configuration error: BROADCASTER_USER_ID must be a numeric Twitch user
  ID"** — you put a username instead of the numeric ID. Convert it with the
  link above.
