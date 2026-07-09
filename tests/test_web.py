"""Phase 4 — the host/player pages and their static assets are served, and the
host page gets its LAN IP injected for a phone-reachable join URL."""

from __future__ import annotations

from fastapi.testclient import TestClient

from server.main import app


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
