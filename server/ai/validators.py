"""Validate and repair AI JSON responses.

- One repair retry with the validation error appended to the prompt.
- On total failure: neutral classification + template narration fallback.
- Remaps invalid targets/conditions per stale-intent rules (GAME_DESIGN.md §9).

Phase 5 implementation.
"""

# TODO Phase 5
