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


def test_host_lobby_has_how_to_play_panel():
    """§13: the host lobby shows a "How to Play" panel beside the QR/room code,
    populated at boot from CFG.how_to_play (shipped via DOODLE_CONFIG)."""
    with TestClient(app) as client:
        body = client.get("/host").text
    assert 'id="howto"' in body
    assert 'id="howtoSteps"' in body
    assert 'id="howtoTips"' in body
    # the copy itself travels in DOODLE_CONFIG, not hardcoded markup
    assert "how_to_play" in body
    assert "Weirder is better" in body


def test_player_status_card_hidden_until_character_exists():
    """§13: the phone status card is hidden until the character exists; the
    condensed lobby rules render on the waiting screen."""
    with TestClient(app) as client:
        body = client.get("/play").text
    # the status card ships hidden (revealed on the first player_state)
    assert re.search(r'id="statusCard"[^>]*class="[^"]*hidden', body) or \
        re.search(r'class="[^"]*hidden[^"]*"[^>]*id="statusCard"', body), \
        "status card must start hidden"
    assert 'id="statusCard"' in body
    assert 'id="waitRules"' in body


def test_host_renders_the_doodle_crowd_stands():
    """§15/S4: the host consumes the gallery_roster message and renders past
    characters as tiny spectators in a .stands band above the battlefield."""
    with TestClient(app) as client:
        host = client.get("/host").text
        arena = client.get("/static/host/arena.js").text
    assert "gallery_roster" in host, "host must handle the gallery roster message"
    assert "setSpectators" in host and "setSpectators" in arena
    # the stands live in their own band, above the zones (never obscuring play)
    assert ".stands" in host and ".spectator" in host


def test_v6_move_buttons_show_stat_icons():
    """v6 §13: each move button is prefixed with the stat icon(s) that power it
    (💪 SMASH / 🌀 BLAST / 💪⚡ CHARGE / ⚡ ESCAPE / 🌀 PROTECT)."""
    with TestClient(app) as client:
        body = client.get("/play").text
    assert "stat_icon" in body, "move buttons must render the move's stat icon(s)"


def test_v6_host_reveal_features_present():
    """v6 §13: move-name badge under a fighter, PROTECT's round-long blue glow,
    and CHARGE/ESCAPE sprite travel between zones during the reveal."""
    with TestClient(app) as client:
        host = client.get("/host").text
        arena = client.get("/static/host/arena.js").text
    # CSS + sequencer hooks live on the host page
    assert ".actionbadge" in host and ".shielded" in host
    for field in ("move_name", "shield_on", "to_zone"):
        assert field in host, f"host sequencer must consume {field}"
    # the arena renderer exposes the helpers the sequencer drives
    for fn in ("setActionBadge", "clearBadges", "setShield", "clearShields", "moveTo"):
        assert fn in arena, fn


def test_pages_cache_bust_scripts_and_are_no_store():
    """A freshly-served page must never pair with a stale cached script: local
    /static/*.js|css refs carry a content hash and the HTML itself is no-store.
    (This is what made a mid-deploy arena.js crash the reveal sequencer.)"""
    with TestClient(app) as client:
        for path in ["/host", "/play"]:
            r = client.get(path)
            assert "no-store" in r.headers.get("cache-control", ""), path
            # every local script/style ref is versioned
            refs = re.findall(r'(?:src|href)="(/static/[^"]+\.(?:js|css))(\?v=[0-9a-f]+)?"', r.text)
            assert refs, f"{path} has no local static refs?"
            for base, ver in refs:
                assert ver, f"{path} ref not cache-busted: {base}"
        # arena.js gets a hash on the host page specifically
        host = client.get("/host").text
        assert re.search(r"/static/host/arena\.js\?v=[0-9a-f]+", host)


def test_host_offers_new_match_and_both_pages_guard_navigation():
    """§10.2/§13: the victory screen offers a Start-a-New-Match button that
    reloads the host into a fresh room, and both pages guard against an
    accidental refresh once a match is underway."""
    with TestClient(app) as client:
        host = client.get("/host").text
        play = client.get("/play").text
    # New Match button + the reload-into-a-fresh-room handler (clears stored room)
    assert 'id="newMatchBtn"' in host
    assert "startNewMatch" in host
    assert "removeItem('hostroom')" in host
    # accidental-navigation guard registered on both pages
    for label, body in (("host", host), ("play", play)):
        assert "beforeunload" in body, label
        assert "onBeforeUnload" in body, label


def test_host_battlefield_nametag_is_team_colored():
    """Task 4/§13: the battlefield name bubble is a team-colored oval (var(--team))
    with white text — not a plain white box."""
    with TestClient(app) as client:
        body = client.get("/host").text
    nametag = _css_block(body, ".fighter .nametag")
    assert nametag, "host page missing a .fighter .nametag rule"
    assert "var(--team" in nametag
    assert "color:#fff" in nametag.replace(" ", "")


def test_host_rail_stat_stacks_icon_over_number():
    """Task 5/§13: rail stat chips put a line break after the icon so the three
    stats read as evenly aligned columns."""
    with TestClient(app) as client:
        body = client.get("/host").text
    assert "icon + '<br>'" in body, "rail setStat must stack the icon over the number"


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


def test_poster_route_serves_composed_png(tmp_path, monkeypatch):
    """GET /poster/<room> serves snapshots/room-<CODE>/poster.png (the victory
    screen's download link, S3); unknown rooms and sneaky paths 404."""
    # Point the snapshots base (a cwd-relative path) at a temp dir holding
    # one composed poster.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "snapshots" / "room-ABCD").mkdir(parents=True)
    png = bytes.fromhex("89504e470d0a1a0a") + b"0" * 128   # PNG magic + filler
    (tmp_path / "snapshots" / "room-ABCD" / "poster.png").write_bytes(png)

    with TestClient(app) as client:
        ok = client.get("/poster/abcd")            # case-insensitive room code
        assert ok.status_code == 200
        assert ok.headers["content-type"] == "image/png"
        assert ok.content == png

        assert client.get("/poster/ZZZZ").status_code == 404      # no such room
        assert client.get("/poster/..%2Fsecret").status_code == 404   # traversal
