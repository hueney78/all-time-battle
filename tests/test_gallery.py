"""The Doodle Crowd — persistent character gallery (GAME_DESIGN §15 / S4)."""

from __future__ import annotations

import base64
from pathlib import Path

from server.gallery import GalleryStore

_FIXTURE = Path(__file__).parent / "fixtures" / "character.png"


def _png() -> str:
    return "data:image/png;base64," + base64.b64encode(_FIXTURE.read_bytes()).decode()


def _entry(name: str, won: bool = False, png: str | None = None) -> dict:
    return {"name": name, "stats": {"power": 2, "speed": 2, "weird": 2},
            "team_id": "team_a", "team_name": "Glitter", "won": won,
            "room": "ABCD", "png": _png() if png is None else png}


def test_save_writes_png_and_json(tmp_path):
    store = GalleryStore(tmp_path, enabled=True)
    ids = store.save_match([_entry("Princess Stabby", won=True)])
    assert len(ids) == 1
    uid = ids[0]
    assert (tmp_path / f"{uid}.json").exists()
    assert (tmp_path / f"{uid}.png").exists()        # valid png decoded to disk
    assert store.all_names() == ["Princess Stabby"]


def test_invalid_png_still_saves_json(tmp_path):
    store = GalleryStore(tmp_path, enabled=True)
    uid = store.save_match([_entry("The Blob", png="not-a-real-png")])[0]
    assert (tmp_path / f"{uid}.json").exists()
    assert not (tmp_path / f"{uid}.png").exists()    # undecodable → no png file, JSON kept
    assert "The Blob" in store.all_names()


def test_cap_prunes_oldest(tmp_path):
    store = GalleryStore(tmp_path, enabled=True, cap=3)
    for i in range(5):
        store.save_match([_entry(f"Fighter{i}")])
    assert len(store.all_names()) == 3               # capped; oldest pruned


def test_roster_returns_name_png_and_result(tmp_path):
    store = GalleryStore(tmp_path, enabled=True)
    store.save_match([_entry("Stabby", won=True), _entry("Blob", won=False)])
    roster = store.roster()
    assert {r["name"] for r in roster} == {"Stabby", "Blob"}
    assert all({"png", "won", "team_id"} <= set(r) for r in roster)


def test_disabled_store_is_a_noop(tmp_path):
    store = GalleryStore(tmp_path, enabled=False)
    assert store.save_match([_entry("Ghost")]) == []
    assert store.all_names() == [] and store.roster() == []
    assert not any(tmp_path.iterdir())               # nothing written to disk
