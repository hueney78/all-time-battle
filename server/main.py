"""FastAPI application entry point.

Run with: uvicorn server.main:app --reload
Serves the host (TV) and player (phone) pages from web/ and the /ws endpoint.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from server.config import load_game_rules
from server.room import RoomManager, SocketDisconnect

app = FastAPI(title="Doodle Brawl", version="0.1.0")

_WEB_DIR = Path(__file__).parent.parent / "web"
if _WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR)), name="static")

# One in-memory room manager for the process (LAN party, single host machine).
room_manager = RoomManager(load_game_rules())


def _lan_ip() -> str:
    """Best-effort LAN IP so the host page can show a phone-reachable join URL."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))  # no packets sent; just picks the outbound iface
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


class _StarletteSocket:
    """Adapts a starlette WebSocket to the room layer's Socket protocol,
    surfacing disconnects as SocketDisconnect."""

    def __init__(self, ws: WebSocket):
        self._ws = ws

    async def send_text(self, data: str) -> None:
        await self._ws.send_text(data)

    async def receive_text(self) -> str:
        try:
            return await self._ws.receive_text()
        except WebSocketDisconnect as exc:
            raise SocketDisconnect from exc

    async def close(self, code: int = 1000) -> None:
        try:
            await self._ws.close(code)
        except Exception:
            pass


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    await room_manager.handle_socket(_StarletteSocket(ws))


@app.get("/", response_class=HTMLResponse)
async def root():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Doodle Brawl</title>
  <style>
    body { font-family: sans-serif; max-width: 600px; margin: 4rem auto; padding: 0 1rem; }
    h1   { font-size: 2.5rem; margin-bottom: 0.5rem; }
    nav  { display: flex; gap: 1rem; margin-top: 1.5rem; }
    a    { padding: 0.6rem 1.2rem; background: #4f46e5; color: #fff;
           border-radius: 6px; text-decoration: none; font-weight: 600; }
    a:hover { background: #3730a3; }
    .sub { color: #6b7280; margin-top: 0.25rem; }
  </style>
</head>
<body>
  <h1>Doodle Brawl</h1>
  <p class="sub">Open the Host Screen on your TV, then join from phones with the room code.</p>
  <nav>
    <a href="/host">Host Screen</a>
    <a href="/play">Join Game</a>
    <a href="/health">Health</a>
    <a href="/docs">API Docs</a>
  </nav>
</body>
</html>"""


@app.get("/health")
async def health():
    rules = load_game_rules()
    return {
        "status": "ok",
        "zones": [z.id for z in rules.zones.zones],
        "conditions": sorted(rules.conditions.conditions.keys()),
        "moves": sorted(rules.moves.moves.keys()),
        "ai": {
            "classify_model": rules.settings.ai.classify_model,
            "narrate_model": rules.settings.ai.narrate_model,
        },
    }


def _inject_config(html: str) -> str:
    """Ship the UI tuning knobs to the browser as window.DOODLE_CONFIG so the
    client is a dumb renderer of server-owned config (canvas/floor color, prefill
    scale, reveal zoom, float timing, …). Injected before </head> so it runs
    ahead of the body scripts that read it."""
    ui = load_game_rules().settings.ui
    tag = f"<script>window.DOODLE_CONFIG = {json.dumps(ui.model_dump())};</script>"
    if "</head>" in html:
        return html.replace("</head>", tag + "\n</head>", 1)
    return tag + html


@app.get("/host", response_class=HTMLResponse)
async def host_page():
    html = (_WEB_DIR / "host" / "index.html").read_text(encoding="utf-8")
    return _inject_config(html.replace("__SERVER_LAN_IP__", _lan_ip()))


@app.get("/play", response_class=HTMLResponse)
async def player_page():
    html = (_WEB_DIR / "player" / "index.html").read_text(encoding="utf-8")
    return _inject_config(html)
