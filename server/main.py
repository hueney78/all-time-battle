"""FastAPI application entry point.

Run with: uvicorn server.main:app --reload
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from server.config import load_game_rules

app = FastAPI(title="Doodle Brawl", version="0.1.0")

_WEB_DIR = Path(__file__).parent.parent / "web"
if _WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR)), name="static")


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
  <p class="sub">Server is running. Open the host screen on your TV, then scan the QR code with your phone.</p>
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


@app.get("/host", response_class=HTMLResponse)
async def host_page():
    return """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Doodle Brawl — Host</title></head>
<body><h1>Host Screen</h1><p>Phase 4 — TODO</p></body>
</html>"""


@app.get("/play", response_class=HTMLResponse)
async def player_page():
    return """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Doodle Brawl — Join</title></head>
<body><h1>Join Game</h1><p>Phase 4 — TODO</p></body>
</html>"""
