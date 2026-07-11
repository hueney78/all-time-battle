"""Wildcard miner — mine snapshots/*/wildcards.jsonl for new moves.yaml archetypes.

Every drawing the classifier couldn't place lands in a room's `wildcards.jsonl`
as `{round, player_id, adaptation_note}` (see server/snapshots.py). After game
nights, recurring adaptation notes are the signal for a new catalog archetype —
"kids keep drawing themselves growing giant" → add a `grow` move (GAME_DESIGN
§4.1 "wildcard feedback loop" / §14). This aggregates the notes: which shapes
keep showing up, with example notes, so you can decide what to add — always a
YAML-only change.

    python scripts/mine_wildcards.py                    # scans ./snapshots
    python scripts/mine_wildcards.py path/to/snapshots  # scan elsewhere
    python scripts/mine_wildcards.py --top 20           # show more keywords
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

# Words too generic to signal a new archetype (English filler + doodle noise).
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "with", "into", "onto", "for", "from",
    "that", "this", "then", "than", "them", "they", "some", "something", "someone",
    "was", "were", "are", "has", "had", "have", "its", "his", "her", "their",
    "player", "character", "fighter", "drew", "draw", "draws", "drawing", "drawn",
    "canvas", "image", "picture", "looks", "look", "like", "likely", "appears",
    "appear", "seems", "seem", "maybe", "probably", "unclear", "ambiguous",
    "nothing", "fits", "closest", "action", "move", "attack", "enemy", "ally",
    "target", "toward", "around", "wildcard", "adapt", "adapted", "interpret",
    "interpreted", "read", "reading", "just", "some", "kind", "sort",
}


def load_wildcards(snapshots_dir: str | Path) -> list[dict]:
    """Read every snapshots/<room>/wildcards.jsonl row, tagged with its room.
    Missing dirs and malformed lines are skipped, never fatal."""
    rows: list[dict] = []
    for path in sorted(Path(snapshots_dir).glob("*/wildcards.jsonl")):
        room = path.parent.name
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                row.setdefault("room", room)
                rows.append(row)
    return rows


def _keywords(note: str) -> set[str]:
    """Salient lowercase words from a note (stopwords + tiny words dropped).
    A set, so each note counts a word at most once toward its frequency."""
    return {
        w for w in re.findall(r"[a-z][a-z'-]+", (note or "").lower())
        if len(w) > 2 and w not in _STOPWORDS
    }


def mine(rows: list[dict], top: int = 15) -> dict:
    """Aggregate wildcard rows into archetype-candidate signals: how often each
    keyword recurs (with example notes) and the most-repeated exact notes."""
    notes = [(r.get("adaptation_note") or "").strip() for r in rows]
    keyword_counts: Counter[str] = Counter()
    keyword_examples: dict[str, list[str]] = defaultdict(list)
    for note in notes:
        for kw in _keywords(note):
            keyword_counts[kw] += 1
            if note and note not in keyword_examples[kw]:
                keyword_examples[kw].append(note)
    return {
        "total": len(rows),
        "per_room": Counter(r.get("room", "?") for r in rows),
        "top_keywords": keyword_counts.most_common(top),
        "keyword_examples": {
            kw: keyword_examples[kw][:3] for kw, _ in keyword_counts.most_common(top)
        },
        "top_phrases": Counter(n for n in notes if n).most_common(top),
    }


def format_report(result: dict, top: int = 15) -> str:
    lines = [
        f"Wildcard log: {result['total']} unplaced classification(s) across "
        f"{len(result['per_room'])} room(s)."
    ]
    if not result["total"]:
        lines.append("No wildcards logged yet — nothing to mine.")
        return "\n".join(lines)
    lines += ["", f"Recurring keywords (candidate archetypes; top {top}):"]
    for kw, n in result["top_keywords"]:
        egs = result["keyword_examples"].get(kw, [])
        eg = f'   e.g. "{egs[0]}"' if egs else ""
        lines.append(f"  {n:>3}x  {kw}{eg}")
    repeats = [(p, n) for p, n in result["top_phrases"] if n > 1]
    if repeats:
        lines += ["", "Most repeated exact notes:"]
        lines += [f"  {n:>3}x  {p}" for p, n in repeats]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Mine wildcards.jsonl for new moves.yaml archetype candidates."
    )
    ap.add_argument("snapshots_dir", nargs="?", default="snapshots",
                    help="directory holding <room>/wildcards.jsonl (default: snapshots)")
    ap.add_argument("--top", type=int, default=15, help="how many keywords/phrases to show")
    args = ap.parse_args()
    print(format_report(mine(load_wildcards(args.snapshots_dir), top=args.top), top=args.top))


if __name__ == "__main__":
    main()
