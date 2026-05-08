from __future__ import annotations

import asyncio
import json
import logging

from aiohttp import web

from .services import KillCounterService

log = logging.getLogger(__name__)


CHROMA_HTML = """<!doctype html>
<html><head>
<meta charset="utf-8">
<title>Deaths</title>
<style>
  html, body { margin: 0; padding: 0; height: 100%; }
  body {
    background: #00FF00;
    color: #FF0000;
    font-family: "Impact", "Arial Black", sans-serif;
    display: flex; align-items: center; justify-content: center;
    -webkit-text-stroke: 2px #000;
  }
  #counter {
    font-size: 22vh;
    font-weight: 900;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    line-height: 1;
  }
</style>
</head>
<body>
  <div id="counter">Deaths: <span id="n">__COUNT__</span></div>
<script>
(function() {
  function pad(n) { return String(n); }
  function connect() {
    var es = new EventSource('/events');
    es.onmessage = function(e) {
      try {
        var data = JSON.parse(e.data);
        if (typeof data.count === 'number') {
          document.getElementById('n').textContent = pad(data.count);
        }
      } catch (_) {}
    };
    es.onerror = function() {
      es.close();
      setTimeout(connect, 2000);
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
  body { font-family: system-ui, sans-serif; max-width: 520px; margin: 3em auto; padding: 0 1em; color: #eee; background: #1b1b1b; }
  h1 { margin-bottom: 0.2em; }
  .count { font-size: 4em; font-weight: 800; color: #ff5252; margin: 0.2em 0 0.6em; }
  button, input[type=submit] { font-size: 1.2em; padding: 0.6em 1.2em; margin-right: 0.4em; cursor: pointer; border: 0; border-radius: 6px; background: #2d2d2d; color: #eee; }
  button.primary { background: #c62828; color: white; }
  input[type=number] { font-size: 1.1em; padding: 0.5em; width: 7em; background: #2d2d2d; color: #eee; border: 1px solid #444; border-radius: 6px; }
  form { display: inline-block; margin-top: 1em; }
  small { color: #888; }
</style>
</head>
<body>
  <h1>jadebot · admin</h1>
  <div class="count">Deaths: <span id="n">__COUNT__</span></div>
  <button id="inc" class="primary">+1 Death</button>
  <button id="dec">-1</button>
  <form id="setForm">
    <input id="setVal" name="count" type="number" min="0" placeholder="Set total" required>
    <input type="submit" value="Set">
  </form>
  <p><small>Local only (127.0.0.1). Chroma source: <a style="color:#9cf" href="/chroma">/chroma</a>.</small></p>
<script>
(function() {
  var n = document.getElementById('n');
  function update(v) { if (typeof v === 'number') n.textContent = String(v); }
  async function post(url, body) {
    var opts = { method: 'POST' };
    if (body) {
      opts.headers = { 'Content-Type': 'application/x-www-form-urlencoded' };
      opts.body = body;
    }
    var r = await fetch(url, opts);
    if (!r.ok) { alert('Request failed: ' + r.status); return; }
    var data = await r.json();
    update(data.count);
  }
  document.getElementById('inc').onclick = function() { post('/admin/increment'); };
  document.getElementById('dec').onclick = function() { post('/admin/decrement'); };
  document.getElementById('setForm').onsubmit = function(e) {
    e.preventDefault();
    var v = document.getElementById('setVal').value;
    post('/admin/set', 'count=' + encodeURIComponent(v));
  };
  // Live sync from SSE.
  var es = new EventSource('/events');
  es.onmessage = function(e) {
    try { var d = JSON.parse(e.data); update(d.count); } catch (_) {}
  };
})();
</script>
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
</ul>
</body></html>
"""


def _render(template: str, count: int) -> str:
    return template.replace("__COUNT__", str(count))


async def handle_index(request: web.Request) -> web.Response:
    return web.Response(text=INDEX_HTML, content_type="text/html")


async def handle_chroma(request: web.Request) -> web.Response:
    service: KillCounterService = request.app["service"]
    count = await service.get_count()
    return web.Response(text=_render(CHROMA_HTML, count), content_type="text/html")


async def handle_admin(request: web.Request) -> web.Response:
    service: KillCounterService = request.app["service"]
    count = await service.get_count()
    return web.Response(text=_render(ADMIN_HTML, count), content_type="text/html")


async def handle_increment(request: web.Request) -> web.Response:
    service: KillCounterService = request.app["service"]
    new = await service.increment()
    return web.json_response({"count": new})


async def handle_decrement(request: web.Request) -> web.Response:
    service: KillCounterService = request.app["service"]
    new = await service.decrement()
    return web.json_response({"count": new})


async def handle_set(request: web.Request) -> web.Response:
    service: KillCounterService = request.app["service"]
    data = await request.post()
    raw = data.get("count")
    if raw is None:
        try:
            payload = await request.json()
            raw = payload.get("count")
        except Exception:
            raw = None
    if raw is None:
        return web.json_response({"error": "missing 'count'"}, status=400)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return web.json_response({"error": "'count' must be an integer"}, status=400)
    new = await service.set_count(n)
    return web.json_response({"count": new})


async def handle_events(request: web.Request) -> web.StreamResponse:
    service: KillCounterService = request.app["service"]
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
    try:
        initial = json.dumps({"count": await service.get_count()})
        await resp.write(f"data: {initial}\n\n".encode("utf-8"))
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                await resp.write(b": keepalive\n\n")
                continue
            if payload == "__close__":
                break
            await resp.write(f"data: {payload}\n\n".encode("utf-8"))
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        service.unsubscribe(queue)
    return resp


def make_app(service: KillCounterService) -> web.Application:
    app = web.Application()
    app["service"] = service
    app.router.add_get("/", handle_index)
    app.router.add_get("/chroma", handle_chroma)
    app.router.add_get("/admin", handle_admin)
    app.router.add_post("/admin/increment", handle_increment)
    app.router.add_post("/admin/decrement", handle_decrement)
    app.router.add_post("/admin/set", handle_set)
    app.router.add_get("/events", handle_events)
    return app
