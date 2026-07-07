"""Anthropic SDK wrapper — retry, timeout, mock mode.

Three call types:
  generate_characters — Haiku, once per game
  classify_actions    — Haiku, once per round
  narrate_round       — Sonnet, once per round (text-only)

Phase 5 implementation. Set AI_MODE=mock for offline dev.
"""

# TODO Phase 5
