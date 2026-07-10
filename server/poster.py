"""Match poster composition (Pillow) — GAME_DESIGN §10.2.

Composes a shareable end-of-match poster: arena-colored background, a winner
banner, each fighter's final drawing + name, the round titles, and the match's
best narrated line. Saved to snapshots/<room>/poster.png.

Pure image work — no game logic. The caller runs it off the event loop and
treats any failure as "no poster," so the game never blocks or crashes on it.
Robust to missing/invalid drawings (a placeholder box stands in).
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from server.ai.provider import MatchSummary
from server.engine.models import GameState, Team

_INK = "#2b2b3a"


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)   # Pillow >= 10.1 scales the default
    except TypeError:                              # older Pillow — fixed-size bitmap
        return ImageFont.load_default()


def _decode_png(b64: str) -> Image.Image | None:
    data = (b64 or "").strip()
    if not data:
        return None
    if data.startswith("data:"):
        data = data.split(",", 1)[1] if "," in data else ""
    try:
        return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGBA")
    except Exception:
        return None


def compose_poster(
    path: Path,
    state: GameState,
    teams: list[Team],
    summary: MatchSummary,
    bg_color: str = "#E8D5A8",
) -> Path:
    w, h = 1000, 720
    img = Image.new("RGB", (w, h), bg_color)
    draw = ImageDraw.Draw(img)
    title_font, head_font, body_font = _font(48), _font(28), _font(18)

    # Title bar + champions banner
    draw.rectangle([0, 0, w, 90], fill=_INK)
    draw.text((w // 2, 45), "DOODLE BRAWL", font=title_font, fill="#ffd76b", anchor="mm")
    win_name = next((t.name for t in teams if t.id == summary.winner_team_id), None)
    banner = f"CHAMPIONS: {win_name}" if win_name else "A DRAW FOR THE AGES"
    draw.text((w // 2, 118), banner, font=head_font, fill=_INK, anchor="mm")

    # Fighters row — each final drawing with name + HP
    chars = list(state.characters.values()) if state else []
    slots = max(1, len(chars))
    slot_w = w // slots
    box = min(180, slot_w - 20)
    top = 160
    for i, ch in enumerate(chars):
        cx = i * slot_w + slot_w // 2
        sprite = _decode_png(ch.character_png_b64)
        if sprite is not None:
            sprite.thumbnail((box, box))
            img.paste(sprite, (cx - sprite.width // 2, top), sprite)
        else:
            draw.rectangle([cx - box // 2, top, cx + box // 2, top + box],
                           outline=_INK, width=3)
            draw.text((cx, top + box // 2), "?", font=title_font, fill=_INK, anchor="mm")
        team = next((t for t in teams if ch.player_id in t.player_ids), None)
        draw.text((cx, top + box + 16), ch.name[:18], font=body_font,
                  fill=(team.color if team else _INK), anchor="mm")
        draw.text((cx, top + box + 38), f"{max(0, ch.hp)} HP", font=body_font,
                  fill="#555", anchor="mm")

    # Round titles + the best line of the night
    ty = top + box + 78
    draw.text((40, ty), "THE STORY SO FAR", font=head_font, fill=_INK)
    ty += 40
    for rt in summary.round_titles[-6:]:
        draw.text((60, ty), f"• {rt}", font=body_font, fill="#333")
        ty += 26
    if summary.best_line:
        draw.text((40, h - 74), "Best line of the night:", font=body_font, fill="#555")
        line = summary.best_line if len(summary.best_line) <= 96 else summary.best_line[:95] + "…"
        draw.text((40, h - 50), line, font=body_font, fill="#111")

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    return path
