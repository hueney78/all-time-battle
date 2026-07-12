# Doodle Brawl

Couch party game where your family's terrible drawings come to life and beat each other up. Sketch heroes on phones; AI assigns stats and writes narration; a deterministic engine resolves all mechanics.

## Quick start

```bash
# 1. Install dependencies (uv recommended)
uv sync --extra dev
# or: pip install -e ".[dev]"

# 2. Configure
cp .env.example .env
# Phases 1–4 work without an API key (AI_MODE=mock is the default)

# 3. Run the server
uv run uvicorn server.main:app --reload
# Then open http://localhost:8000

# 4. On the same LAN: players open http://<your-ip>:8000/play?room=XXXX
```

## Development

```bash
# Run all tests
pytest

# Run a specific test
pytest tests/test_resolver.py::test_v2_golden

# Lint / format
ruff check .
ruff format .

# Full offline game (no API key)
AI_MODE=mock uv run uvicorn server.main:app --reload

# Live AI smoke test (requires ANTHROPIC_API_KEY in .env)
python scripts/ai_smoke.py

# Engine demo (3 scripted rounds, printed events)
python -m server.engine.demo
```

## Configuration

All tunable values live in `config/*.yaml` — edit and start a new room (no server restart needed).

| File | Contents |
|---|---|
| `config/settings.yaml` | Timers, ports, player limits, model IDs |
| `config/balance.yaml` | HP formulas, stat budgets, crit/fumble thresholds, creativity caps |
| `config/zones.yaml` | Zone graph — add High Ground here with zero code changes |
| `config/conditions.yaml` | Condition registry (duration, effects, emojis) |
| `config/moves.yaml` | COMBAT V2 catalog: eight tapped moves owning all action math |
| `config/prompts/` | Jinja2 prompt templates for the AI layer |

See `ARCHITECTURE.md` and `GAME_DESIGN.md` for full documentation.
