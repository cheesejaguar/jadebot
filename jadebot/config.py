from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


def _required_numeric(name: str) -> str:
    val = _required(name)
    if not val.isdigit():
        raise RuntimeError(
            f"{name} must be a numeric Twitch user ID (got {val!r}). "
            f"Look up at https://www.streamweasels.com/tools/convert-twitch-username-to-user-id/"
        )
    return val


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    bot_username: str
    oauth_token: str
    client_id: str
    broadcaster_login: str
    broadcaster_user_id: str
    bot_user_id: str
    web_host: str
    web_port: int
    db_path: str
    presence_window_sec: int
    announce_deaths: bool
    history_size: int

    @property
    def helix_token(self) -> str:
        tok = self.oauth_token
        return tok[6:] if tok.lower().startswith("oauth:") else tok

    @property
    def irc_token(self) -> str:
        tok = self.oauth_token
        return tok if tok.lower().startswith("oauth:") else f"oauth:{tok}"

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            bot_username=_required("TWITCH_BOT_USERNAME").lower(),
            oauth_token=_required("TWITCH_OAUTH_TOKEN"),
            client_id=_required("TWITCH_CLIENT_ID"),
            broadcaster_login=os.environ.get("BROADCASTER_LOGIN", "jade_infinite").lower(),
            broadcaster_user_id=_required_numeric("BROADCASTER_USER_ID"),
            bot_user_id=_required_numeric("BOT_USER_ID"),
            web_host=os.environ.get("WEB_HOST", "127.0.0.1"),
            web_port=int(os.environ.get("WEB_PORT", "8080")),
            db_path=os.environ.get("DB_PATH", "logs/chat.db"),
            presence_window_sec=int(os.environ.get("PRESENCE_WINDOW_SEC", "600")),
            announce_deaths=_bool("ANNOUNCE_DEATHS", default=False),
            history_size=int(os.environ.get("UNDO_HISTORY", "20")),
        )
