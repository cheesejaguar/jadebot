from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from typing import Awaitable, Callable

from aiohttp import web

from .services import KillCounterService

log = logging.getLogger(__name__)

SERVICE_KEY: web.AppKey[KillCounterService] = web.AppKey("service", KillCounterService)

LOOPBACK = ("127.0.0.1", "::1", "localhost")
SSE_WRITE_TIMEOUT = 2.0
SSE_KEEPALIVE_SEC = 15.0


CHROMA_HTML = """<!doctype html>
<html><head>
<meta charset="utf-8">
<title>Deaths</title>
<style>
  html, body { margin: 0; padding: 0; height: 100%; overflow: hidden; }
  body {
    background: __BG__;
    color: __FG__;
    font-family: "Impact", "Arial Black", sans-serif;
    display: flex; align-items: center; justify-content: center;
    -webkit-text-stroke: 2px #000;
  }
  #counter {
    font-size: __SIZE__;
    font-weight: 900;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    line-height: 1;
    will-change: transform, filter;
  }
  @keyframes jb-pulse {
    0%   { transform: scale(1);    filter: brightness(1); }
    20%  { transform: scale(1.18); filter: brightness(1.6); }
    40%  { transform: scale(0.96) translateX(-6px); }
    60%  { transform: scale(1.04) translateX(6px); }
    100% { transform: scale(1);    filter: brightness(1); }
  }
  .pulse { animation: jb-pulse 480ms ease-out; }
</style>
</head>
<body>
  <div id="counter"><span id="label">__LABEL__</span>: <span id="n">__COUNT__</span></div>
<script>
(function() {
  var el = document.getElementById('counter');
  var n = document.getElementById('n');
  var last = null;
  var attempts = 0;
  function setCount(v) {
    if (v === last) return;
    last = v;
    n.textContent = String(v);
    el.classList.remove('pulse');
    // force reflow so the animation restarts on rapid updates
    void el.offsetWidth;
    el.classList.add('pulse');
  }
  function connect() {
    var es = new EventSource('/events');
    es.onopen = function() { attempts = 0; };
    es.onmessage = function(e) {
      try {
        var data = JSON.parse(e.data);
        if (typeof data.count === 'number') setCount(data.count);
      } catch (_) {}
    };
    es.onerror = function() {
      es.close();
      attempts = Math.min(attempts + 1, 6);
      var delay = Math.min(30000, 1000 * Math.pow(2, attempts));
      setTimeout(connect, delay);
    };
  }
  connect();
})();
</script>
</body></html>
"""


ADMIN_HTML = """<!doctype html>
<html><head>
<meta charset="utf-8">
<title>jadebot — admin</title>
<style>
  body { font-family: system-ui, -apple-system, sans-serif; max-width: 560px; margin: 2.4em auto; padding: 0 1em; color: #eee; background: #1b1b1b; }
  h1 { margin-bottom: 0.1em; }
  .sub { color: #888; font-size: 0.9em; margin-bottom: 1em; }
  .count { font-size: 4em; font-weight: 800; color: #ff5252; margin: 0.1em 0 0.2em; line-height: 1; }
  .session { color: #aaa; font-size: 1.1em; margin-bottom: 1em; }
  button, input[type=submit] { font-size: 1.15em; padding: 0.55em 1.1em; margin: 0.2em 0.3em 0.2em 0; cursor: pointer; border: 0; border-radius: 6px; background: #2d2d2d; color: #eee; }
  button:hover { background: #3a3a3a; }
  button.primary { background: #c62828; color: white; }
  button.primary:hover { background: #d63838; }
  button:disabled { opacity: 0.4; cursor: not-allowed; }
  input[type=number] { font-size: 1.1em; padding: 0.5em; width: 7em; background: #2d2d2d; color: #eee; border: 1px solid #444; border-radius: 6px; }
  form { display: inline-block; margin-top: 0.6em; }
  small { color: #888; }
  .row { margin: 0.3em 0; }
  .conn { display: inline-block; padding: 0.15em 0.55em; border-radius: 999px; font-size: 0.8em; margin-left: 0.4em; vertical-align: middle; }
  .conn.live { background: #1b5e20; color: #c8e6c9; }
  .conn.down { background: #5d2222; color: #ffcdd2; }
  kbd { font-family: ui-monospace, monospace; background: #2d2d2d; border: 1px solid #444; border-radius: 4px; padding: 1px 6px; font-size: 0.85em; }
  a { color: #9cf; }
</style>
</head>
<body>
  <h1>jadebot · admin <span id="conn" class="conn down">offline</span></h1>
  <div class="sub">Local control surface for the kill counter.</div>
  <div class="count">Deaths: <span id="n">__COUNT__</span></div>
  <div class="session">Session: <span id="s">__SESSION__</span></div>
  <div class="row">
    <button id="inc" class="primary">+1 Death</button>
    <button id="dec">-1</button>
    <button id="undo">Undo</button>
  </div>
  <div class="row">
    <form id="setForm">
      <input id="setVal" name="count" type="number" min="0" placeholder="Set total" required>
      <input type="submit" value="Set">
    </form>
    <button id="resetSession">Reset session</button>
  </div>
  <p><small>
    Keys: <kbd>+</kbd>/<kbd>=</kbd> increment · <kbd>-</kbd> decrement · <kbd>Z</kbd> undo · <kbd>R</kbd> reset session.<br>
    OBS source: <a href="/chroma">/chroma</a> · Leaderboard: <a href="/leaderboard">/leaderboard</a> · Export: <a href="/admin/export.csv">witnesses.csv</a>
  </small></p>
<script>
(function() {
  var n = document.getElementById('n');
  var s = document.getElementById('s');
  var conn = document.getElementById('conn');
  var undoBtn = document.getElementById('undo');
  function update(state) {
    if (state && typeof state.count === 'number') n.textContent = String(state.count);
    if (state && typeof state.session === 'number') s.textContent = String(state.session);
    if (state && typeof state.can_undo === 'boolean') undoBtn.disabled = !state.can_undo;
  }
  async function post(url, body) {
    var opts = { method: 'POST' };
    if (body) {
      opts.headers = { 'Content-Type': 'application/x-www-form-urlencoded' };
      opts.body = body;
    }
    var r = await fetch(url, opts);
    if (!r.ok) { alert('Request failed: ' + r.status); return; }
    var data = await r.json();
    update(data);
  }
  document.getElementById('inc').onclick = function() { post('/admin/increment'); };
  document.getElementById('dec').onclick = function() { post('/admin/decrement'); };
  undoBtn.onclick = function() { post('/admin/undo'); };
  document.getElementById('resetSession').onclick = function() {
    if (confirm('Reset session counter to 0? (Total stays unchanged.)')) post('/admin/session/reset');
  };
  document.getElementById('setForm').onsubmit = function(e) {
    e.preventDefault();
    var raw = document.getElementById('setVal').value;
    var v = parseInt(raw, 10);
    if (Number.isNaN(v) || v < 0) { alert('Enter a non-negative integer.'); return; }
    var current = parseInt(n.textContent, 10) || 0;
    if (v === 0 && current > 0) {
      if (!confirm('Set the total death count to 0? This will be visible on stream.')) return;
    } else if (Math.abs(v - current) > 5) {
      if (!confirm('Change total from ' + current + ' to ' + v + '?')) return;
    }
    post('/admin/set', 'count=' + encodeURIComponent(String(v)));
  };
  // Keyboard shortcuts: ignore when typing in an input.
  document.addEventListener('keydown', function(e) {
    var t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    var k = e.key;
    if (k === '+' || k === '=') { e.preventDefault(); post('/admin/increment'); }
    else if (k === '-' || k === '_') { e.preventDefault(); post('/admin/decrement'); }
    else if (k === 'z' || k === 'Z') { e.preventDefault(); post('/admin/undo'); }
    else if (k === 'r' || k === 'R') {
      e.preventDefault();
      if (confirm('Reset session counter to 0?')) post('/admin/session/reset');
    }
  });
  // Live SSE.
  var attempts = 0;
  function connect() {
    var es = new EventSource('/events');
    es.onopen = function() { attempts = 0; conn.textContent = 'live'; conn.className = 'conn live'; };
    es.onmessage = function(e) {
      try { update(JSON.parse(e.data)); } catch (_) {}
    };
    es.onerror = function() {
      es.close();
      conn.textContent = 'reconnecting'; conn.className = 'conn down';
      attempts = Math.min(attempts + 1, 6);
      var delay = Math.min(30000, 1000 * Math.pow(2, attempts));
      setTimeout(connect, delay);
    };
  }
  connect();
})();
</script>
</body></html>
"""


LEADERBOARD_HTML = """<!doctype html>
<html><head>
<meta charset="utf-8">
<title>jadebot · leaderboard</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 520px; margin: 3em auto; padding: 0 1em; color: #eee; background: #1b1b1b; }
  h1 { margin-bottom: 0.1em; }
  .sub { color: #888; }
  ol { font-size: 1.4em; line-height: 1.6; padding-left: 1.5em; }
  li b { color: #ff5252; }
  a { color: #9cf; }
  .empty { color: #888; font-style: italic; }
</style>
</head>
<body>
<h1>Witness leaderboard</h1>
<div class="sub">Top viewers by deaths witnessed.</div>
__BODY__
<p><a href="/admin">← admin</a></p>
</body></html>
"""


INDEX_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>jadebot</title>
<style>body{font-family:system-ui,sans-serif;max-width:480px;margin:3em auto;color:#eee;background:#1b1b1b;padding:0 1em;}a{color:#9cf;}</style>
</head><body>
<h1>jadebot</h1>
<ul>
  <li><a href="/chroma">/chroma</a> — OBS Browser Source</li>
  <li><a href="/admin">/admin</a> — Admin controls</li>
  <li><a href="/leaderboard">/leaderboard</a> — Witness leaderboard</li>
</ul>
</body></html>
"""


def _safe_color(value: str, default: str) -> str:
    """Accept either '00FF00' or '#00FF00'; reject anything that isn't a hex color."""
    v = value.strip().lstrip("#")
    if len(v) in (3, 6) and all(c in "0123456789abcdefABCDEF" for c in v):
        return f"#{v}"
    return default


def _safe_size(value: str, default: str) -> str:
    v = value.strip().lower()
    if not v:
        return default
    # Allow a number (interpreted as vh) or a CSS length with a recognised unit.
    if v.replace(".", "", 1).isdigit():
        return f"{v}vh"
    for unit in ("vh", "vw", "px", "em", "rem", "%"):
        if v.endswith(unit):
            num = v[: -len(unit)]
            if num.replace(".", "", 1).isdigit():
                return v
    return default


def _render_chroma(count: int, query) -> str:
    label = (query.get("label") or "Deaths")[:32]
    bg = _safe_color(query.get("bg", ""), "#00FF00")
    fg = _safe_color(query.get("fg", ""), "#FF0000")
    size = _safe_size(query.get("size", ""), "22vh")
    return (
        CHROMA_HTML.replace("__COUNT__", str(count))
        .replace("__LABEL__", label)
        .replace("__BG__", bg)
        .replace("__FG__", fg)
        .replace("__SIZE__", size)
    )


def _render_admin(count: int, session: int) -> str:
    return ADMIN_HTML.replace("__COUNT__", str(count)).replace("__SESSION__", str(session))


def _render_leaderboard(rows: list[tuple[str, int]]) -> str:
    if not rows:
        body = '<p class="empty">No witnesses yet — first death is on the way.</p>'
    else:
        items = "".join(
            f"<li>{i+1}. <code>{login}</code> — <b>{n}</b></li>"
            for i, (login, n) in enumerate(rows)
        )
        body = f"<ol>{items}</ol>"
    return LEADERBOARD_HTML.replace("__BODY__", body)


@web.middleware
async def loopback_only_middleware(request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]):
    """All routes require a loopback peer. Defense in depth on top of the bind address."""
    peer = request.remote or ""
    # IPv6-mapped IPv4 like ::ffff:127.0.0.1
    if peer.startswith("::ffff:"):
        peer = peer[len("::ffff:") :]
    if peer not in LOOPBACK:
        log.warning("Refusing non-loopback request from %s %s %s", request.remote, request.method, request.path)
        return web.Response(status=403, text="forbidden\n")
    return await handler(request)


async def handle_index(request: web.Request) -> web.Response:
    return web.Response(text=INDEX_HTML, content_type="text/html")


async def handle_health(request: web.Request) -> web.Response:
    service: KillCounterService = request.app[SERVICE_KEY]
    state = await service.get_state()
    return web.json_response({"status": "ok", **state})


async def handle_chroma(request: web.Request) -> web.Response:
    service: KillCounterService = request.app[SERVICE_KEY]
    count = await service.get_count()
    return web.Response(text=_render_chroma(count, request.query), content_type="text/html")


async def handle_admin(request: web.Request) -> web.Response:
    service: KillCounterService = request.app[SERVICE_KEY]
    state = await service.get_state()
    return web.Response(
        text=_render_admin(state["count"], state["session"]),
        content_type="text/html",
    )


async def handle_leaderboard(request: web.Request) -> web.Response:
    service: KillCounterService = request.app[SERVICE_KEY]
    if request.query.get("format") == "json" or request.headers.get("Accept", "").startswith("application/json"):
        rows = await service.top_witnesses(limit=20)
        return web.json_response([{"user_login": l, "count": n} for l, n in rows])
    rows = await service.top_witnesses(limit=10)
    return web.Response(text=_render_leaderboard(rows), content_type="text/html")


async def handle_increment(request: web.Request) -> web.Response:
    service: KillCounterService = request.app[SERVICE_KEY]
    await service.increment()
    return web.json_response(await service.get_state())


async def handle_decrement(request: web.Request) -> web.Response:
    service: KillCounterService = request.app[SERVICE_KEY]
    await service.decrement()
    return web.json_response(await service.get_state())


async def handle_undo(request: web.Request) -> web.Response:
    service: KillCounterService = request.app[SERVICE_KEY]
    new = await service.undo()
    state = await service.get_state()
    state["undone"] = new is not None
    return web.json_response(state)


async def handle_session_reset(request: web.Request) -> web.Response:
    service: KillCounterService = request.app[SERVICE_KEY]
    await service.reset_session()
    return web.json_response(await service.get_state())


async def handle_set(request: web.Request) -> web.Response:
    service: KillCounterService = request.app[SERVICE_KEY]
    raw = None
    if request.content_type == "application/json":
        try:
            payload = await request.json()
            raw = payload.get("count")
        except Exception:
            raw = None
    else:
        data = await request.post()
        raw = data.get("count")
    if raw is None:
        return web.json_response({"error": "missing 'count'"}, status=400)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return web.json_response({"error": "'count' must be an integer"}, status=400)
    if n < 0:
        return web.json_response({"error": "'count' must be >= 0"}, status=400)
    await service.set_count(n)
    return web.json_response(await service.get_state())


async def handle_export_csv(request: web.Request) -> web.Response:
    service: KillCounterService = request.app[SERVICE_KEY]
    rows = await service.all_witnesses()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["user_login", "user_id", "witnessed_deaths", "last_seen_unix"])
    for login, user_id, count, last_seen in rows:
        w.writerow([login, user_id or "", count, last_seen])
    return web.Response(
        body=buf.getvalue(),
        content_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="witnesses.csv"'},
    )


async def handle_events(request: web.Request) -> web.StreamResponse:
    service: KillCounterService = request.app[SERVICE_KEY]
    resp = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await resp.prepare(request)
    queue = service.subscribe()

    async def send(payload: bytes) -> bool:
        try:
            await asyncio.wait_for(resp.write(payload), timeout=SSE_WRITE_TIMEOUT)
            return True
        except (asyncio.TimeoutError, ConnectionResetError, asyncio.CancelledError):
            return False
        except Exception:
            log.exception("SSE write failed; closing client.")
            return False

    try:
        # Tell the browser to wait 5s before reconnecting, plus initial state.
        if not await send(b"retry: 5000\n\n"):
            return resp
        initial = json.dumps(await service.get_state())
        if not await send(f"data: {initial}\n\n".encode("utf-8")):
            return resp
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=SSE_KEEPALIVE_SEC)
            except asyncio.TimeoutError:
                if not await send(b": keepalive\n\n"):
                    break
                continue
            if payload == "__close__":
                break
            if not await send(f"data: {payload}\n\n".encode("utf-8")):
                break
    finally:
        service.unsubscribe(queue)
    return resp


def make_app(service: KillCounterService) -> web.Application:
    app = web.Application(middlewares=[loopback_only_middleware])
    app[SERVICE_KEY] = service
    app.router.add_get("/", handle_index)
    app.router.add_get("/healthz", handle_health)
    app.router.add_get("/chroma", handle_chroma)
    app.router.add_get("/admin", handle_admin)
    app.router.add_get("/leaderboard", handle_leaderboard)
    app.router.add_post("/admin/increment", handle_increment)
    app.router.add_post("/admin/decrement", handle_decrement)
    app.router.add_post("/admin/undo", handle_undo)
    app.router.add_post("/admin/session/reset", handle_session_reset)
    app.router.add_post("/admin/set", handle_set)
    app.router.add_get("/admin/export.csv", handle_export_csv)
    app.router.add_get("/events", handle_events)
    return app
