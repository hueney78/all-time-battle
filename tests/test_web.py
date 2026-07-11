"""Phase 4 — the host/player pages and their static assets are served, and the
host page gets its LAN IP injected for a phone-reachable join URL."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from server.main import app


def _css_block(body: str, selector: str) -> str:
    """Return the declarations inside the first `selector { ... }` rule."""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", body)
    return m.group(1) if m else ""


def test_host_page_served_with_lan_ip_injected():
    with TestClient(app) as client:
        r = client.get("/host")
    assert r.status_code == 200
    body = r.text
    assert "__SERVER_LAN_IP__" not in body, "LAN IP placeholder must be substituted"
    assert "/static/host/arena.js" in body
    assert "/static/shared/ws.js" in body
    assert 'id="arena"' in body


def test_player_page_served_with_canvas_assets():
    with TestClient(app) as client:
        r = client.get("/play")
    assert r.status_code == 200
    body = r.text
    assert "/static/player/canvas.js" in body
    assert "/static/shared/ws.js" in body
    assert 'id="pad"' in body           # the drawing canvas
    assert 'id="restore"' in body       # restore-character button


def test_pages_inject_client_config():
    """Both pages ship window.DOODLE_CONFIG so the client renders server-owned
    UI tokens (sand color, prefill scale, reveal zoom …)."""
    with TestClient(app) as client:
        for path in ["/host", "/play"]:
            body = client.get(path).text
            assert "window.DOODLE_CONFIG" in body, path
            assert "#E8D5A8" in body, f"{path} missing canvas_background_color"
            assert "__DOODLE_CONFIG__" not in body, f"{path} left a raw placeholder"


def test_host_battlefield_matches_mockup():
    """Playtest visual contract (design/mockup_host_screen.html): the arena
    floor is uniform sand (no spotlight/vignette circle), sprites have no
    drop-shadow/card so their sand PNG background blends into the floor, and the
    name bubble floats ABOVE the character image (HP bar + conds below)."""
    with TestClient(app) as client:
        body = client.get("/host").text

    # 1) uniform floor — the darker-tan spotlight/vignette circle is gone
    arena = _css_block(body, ".arena")
    assert arena, "host page missing an .arena rule"
    assert "radial-gradient" not in arena, f"arena floor must be uniform, no vignette: {arena!r}"

    # 2) no drop shadow behind sprites (PNG sand bg must blend invisibly)
    pic = _css_block(body, ".fighter .pic")
    assert pic, "host page missing a .fighter .pic rule"
    assert "drop-shadow" not in pic, f"sprite must cast no shadow: {pic!r}"

    # 3) name bubble renders above the sprite
    nametag = _css_block(body, ".nametag")
    assert "order:-1" in nametag, f"name bubble must sit above the sprite (order:-1): {nametag!r}"


def test_static_assets_available():
    with TestClient(app) as client:
        for path in [
            "/static/shared/ws.js",
            "/static/shared/common.css",
            "/static/player/canvas.js",
            "/static/host/arena.js",
        ]:
            r = client.get(path)
            assert r.status_code == 200, path
            assert len(r.text) > 100, f"{path} looks empty"
