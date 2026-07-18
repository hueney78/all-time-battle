"""Typed config loaders — all values loaded from config/*.yaml.

Bad YAML raises a clear error naming the file. Bad field names/types raise
a ValidationError that pydantic formats with the offending key path.
Hot-reload per room: call load_game_rules() at room creation time.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

CONFIG_DIR = Path(__file__).parent.parent / "config"


def _load_yaml(filename: str, config_dir: Path | None = None) -> dict:
    path = (config_dir or CONFIG_DIR) / filename
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        raise FileNotFoundError(f"Config file missing: {path}")
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in {path}: {e}") from e


def _parse(model_cls, data: dict, source_file: str):
    try:
        return model_cls(**data)
    except ValidationError as e:
        raise ValueError(f"Config error in {source_file}:\n{e}") from e


# ---------------------------------------------------------------------------
# settings.yaml
# ---------------------------------------------------------------------------


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000


class GameConfig(BaseModel):
    max_players: int = 6
    min_players: int = 2
    room_code_length: int = 4
    montage_every_rounds: int = 3   # 0 disables the Power-Up Montage


class TimerConfig(BaseModel):
    draw_characters_seconds: int = 90
    draw_action_seconds: int = 75
    montage_seconds: int = 20
    warning_seconds: int = 10
    beat_seconds: int = 6


class AIConfig(BaseModel):
    classify_model: str = "claude-haiku-4-5"
    narrate_model: str = "claude-sonnet-4-6"
    timeout_seconds: int = 20
    max_retries: int = 1
    max_image_size_bytes: int = 200_000
    image_px: int = 512


class SnapshotConfig(BaseModel):
    enabled: bool = True
    dir: str = "snapshots"


class GalleryConfig(BaseModel):
    """The Doodle Crowd (GAME_DESIGN §15) — persistent past characters."""

    enabled: bool = True
    dir: str = "gallery"
    cap: int = 60          # max characters kept as spectators (oldest pruned)
    cameo_count: int = 3   # gallery names injected into the narrate prompt each round


class InstantReplayConfig(BaseModel):
    enabled: bool = True
    # Attack results worth replaying. "devastating" = a hit at creativity tier 3
    # (v4's spike moment; there are no crits).
    triggers: list[str] = ["devastating", "ko"]
    slowmo_factor: float = 2.0


class AudioConfig(BaseModel):
    """Host-page Web Audio manager knobs. `events_sfx` maps engine event types
    to stinger clips; per-move clips are the `sfx` keys in moves.yaml."""

    enabled: bool = True
    volume: float = 0.8
    pitch_variation: float = 0.10
    sfx_dir: str = "/static/host/assets/sfx"
    events_sfx: dict[str, str] = {
        "devastating": "crowd_roar",
        "reflect": "boing",
        "trap": "comic_snap",
        "ko": "ko_bell",
        "combo": "air_horn",
        "sudden_death": "drumroll",
        "replay": "replay",
    }


class ReadoutConfig(BaseModel):
    """The host's plain-language damage readout (GAME_DESIGN §13):

        🎯 SHOOT → 🎲 3 + ⚡ Speed 5 + ⭐⭐ Creative 3 = 11 damage

    Rules baked into the server-side builder: one addition and one total per
    line, zero terms omitted, reductions on their own line (never a rewrite of
    the first), and star count == the creativity tier.
    """

    enabled: bool = True
    dice_icon: str = "🎲"
    star_icon: str = "⭐"
    creative_label: str = "Creative"
    stat_icons: dict[str, str] = {"power": "💪", "speed": "⚡", "weird": "🌀"}
    stat_labels: dict[str, str] = {"power": "Power", "speed": "Speed", "weird": "Weird"}
    # Tier 3 swaps the star chip for a flourish.
    devastating_chip: str = "⭐⭐⭐ DEVASTATING!"
    damage_line: str = "{icon} {move} → {terms} = {total} damage"
    heal_line: str = "{icon} {move} → {terms} = {total} healed"
    # The one reduction line (second line): PROTECT's shield reflecting a share.
    reflect_line: str = "🛡️ {target}'s shield reflects {total} back at {attacker}!"


class HowToPlayConfig(BaseModel):
    """Lobby rules copy (GAME_DESIGN §13). The host lobby shows the full panel
    (steps + tips) beside the QR/room code; the player waiting screen shows the
    same numbered steps condensed under "You're in!". Steps carry their own
    number emoji so the copy is fully editable here with no numbering in code."""

    title: str = "How to Play"
    steps: list[str] = [
        "1️⃣ Draw your fighter — the AI sizes it up, names it, and gives it stats.",
        "2️⃣ Every round: TAP a move, PICK a target, then DRAW how your character does it.",
        "3️⃣ Your drawing is your power — creative, funny drawings earn big bonuses.",
        "4️⃣ Scheme with your teammate: drawings that work together trigger a COMBO.",
        "5️⃣ Knock out the other team to win — and if you're KO'd, "
        "you become a Gremlin and draw hazards!",
    ]
    tips: list[str] = [
        "Weirder is better",
        "Watch the Initiative Order — fast fighters act first.",
    ]


class StandsConfig(BaseModel):
    """The Doodle Crowd stands (GAME_DESIGN §15). The host receives the full
    gallery roster (up to gallery.cap) and shows a rotating handful of them as
    tiny spectators in the colosseum stands. These knobs are pure presentation
    (how many at once, how often the visible set rotates)."""

    max: int = 14              # spectators visible at once (0 disables the stands)
    rotate_seconds: float = 12.0   # how often the visible handful is reshuffled (0 = never)


class UIConfig(BaseModel):
    """Presentation knobs handed to the browser as window.DOODLE_CONFIG.

    Defaults let older settings.yaml files (without a `ui:` block) still load.
    """

    canvas_background_color: str = "#E8D5A8"
    arena_background: str = ""
    action_canvas_character_scale: float = 0.5
    montage_canvas_character_scale: float = 0.88
    deliberation_filler_seconds: float = 3.5
    # Phase splash (GAME_DESIGN §13): a full-screen announcement on all phones
    # + the TV before each drawing phase; the draw timer starts after it.
    # splash_text keys: draw_characters / intros / draw_action / montage /
    # gremlin ("{round}" is substituted); phones show `gremlin` to KO'd players.
    phase_splash_seconds: float = 2.0
    splash_text: dict[str, str] = {
        "draw_characters": "Draw your Character!",
        "intros": "🥁 Meet the Fighters! 🥁",
        "draw_action": "Round {round} — Draw your Move!",
        "montage": "🎵 Upgrade your Character! 🎵",
        "gremlin": "Draw a Hazard, Gremlin! 😈",
    }
    reveal_action_zoom_scale: float = 1.8
    reveal_action_zoom_seconds: float = 2.5
    reveal_move_seconds: float = 0.7   # CHARGE/ESCAPE sprite travel time between zones (v6)
    reveal_beat_seconds: float = 3.2   # per-beat auto-advance pace; 0 = manual (host clicks Next ▶)
    float_number_seconds: float = 1.5
    audience_recent_rounds: int = 3
    combo_splash_seconds: float = 2.0
    how_to_play: HowToPlayConfig = HowToPlayConfig()
    stands: StandsConfig = StandsConfig()
    readout: ReadoutConfig = ReadoutConfig()
    instant_replay: InstantReplayConfig = InstantReplayConfig()
    audio: AudioConfig = AudioConfig()


class Settings(BaseModel):
    server: ServerConfig
    game: GameConfig
    timers: TimerConfig
    ai: AIConfig
    snapshots: SnapshotConfig
    gallery: GalleryConfig = GalleryConfig()
    ui: UIConfig = UIConfig()


def load_settings(config_dir: Path | None = None) -> Settings:
    data = _load_yaml("settings.yaml", config_dir)
    return _parse(Settings, data, "settings.yaml")


# ---------------------------------------------------------------------------
# balance.yaml
# ---------------------------------------------------------------------------


class Balance(BaseModel):
    # HP = hp_base + hp_per_power*Power + hp_per_weird*Weird + Speed//hp_speed_divisor
    hp_base: int = 27
    hp_per_power: int = 2
    hp_per_weird: int = 1
    hp_speed_divisor: int = 2   # Speed buys a little HP now that dodge is gone (v5)
    # Stat budget — AI distributes stats summing to this
    stat_budget: int = 9
    stat_min: int = 0
    stat_max: int = 6
    # COMBAT V5: no AC, no attack roll, no dodge — every selected move lands. The
    # ONLY thing that reduces a hit is PROTECT's reflect shield: a shielded ally
    # absorbs reflect_per_weird * caster's Weird (capped at reflect_cap) and
    # bounces exactly that much back at the attacker.
    reflect_per_weird: float = 0.05
    reflect_cap: float = 0.30
    # Arena Gremlin traps (GAME_DESIGN §10): trap_damage + creativity to one enemy
    trap_damage: str = "1d4"
    # Creativity bonuses (added directly to the effect — there is no roll)
    creativity_tier_0: int = 0
    creativity_tier_1: int = 1
    creativity_tier_2: int = 3
    creativity_tier_3: int = 5
    # Combo rules — both partners gain this many creativity TIERS (no fusion)
    combo_tier_bonus: int = 1
    # Rubber-banding
    underdog_enabled: bool = True
    underdog_hp_share_threshold: int = 2
    underdog_damage_bonus: int = 1
    # Sudden death
    max_rounds: int = 12
    sudden_death_damage_bonus: int = 3


def load_balance(config_dir: Path | None = None) -> Balance:
    data = _load_yaml("balance.yaml", config_dir)
    return _parse(Balance, data, "balance.yaml")


# ---------------------------------------------------------------------------
# zones.yaml
# ---------------------------------------------------------------------------


class ZoneModifiers(BaseModel):
    """Zone riders the resolver reads generically (GAME_DESIGN §6). COMBAT V5
    has no AC and no dodge, so a zone's edge is flat damage or healing now."""

    model_config = {"extra": "allow"}
    damage_bonus: int = 0            # flat damage on hits made FROM this zone
    incoming_damage_bonus: int = 0   # flat damage on hits landing IN this zone
    heal_bonus: int = 0              # flat healing on heals landing IN this zone


class ZoneDef(BaseModel):
    model_config = {"extra": "allow"}
    id: str
    name: str
    adjacent: list[str]
    tags: list[str] = []
    capacity: int | None = None
    entry_cost: int = 1
    modifiers: ZoneModifiers = ZoneModifiers()


class ZoneRules(BaseModel):
    melee_requires_same_zone: bool = True
    ranged_any_zone: bool = True
    # v5: movement is not a button — CHARGE rushes into a target's zone and
    # ESCAPE slips one zone (player picks ◀/▶) along the zones.yaml order.


class ZonesConfig(BaseModel):
    zones: list[ZoneDef]
    rules: ZoneRules


def load_zones(config_dir: Path | None = None) -> ZonesConfig:
    data = _load_yaml("zones.yaml", config_dir)
    return _parse(ZonesConfig, data, "zones.yaml")


# ---------------------------------------------------------------------------
# moves.yaml
# ---------------------------------------------------------------------------


class MoveDef(BaseModel):
    """One COMBAT V5 move (GAME_DESIGN §4.1). The catalog owns all math;
    formulas may reference POW/SPD/WRD (see engine/dice.py) but never spell out
    creativity — the resolver adds that to every damage/heal as a system rule."""

    model_config = {"extra": "allow"}
    # Headline stat — the term the host readout prints and the phone keys off.
    # "power" | "speed" | "weird" | "avg(power,speed)" | "none". Not a roll:
    # COMBAT V5 has no attack roll.
    stat: str = "none"
    range: str = "same_zone"  # "same_zone" | "any_zone"
    target: str = "single_enemy"  # "single_enemy" | "ally"
    damage: str | None = None     # formula, e.g. "2d4 + avg(POW,SPD)"
    heal: str | None = None       # formula, e.g. "1d6 + WRD"
    same_zone_penalty: str | None = None  # "half" → point-blank damage halved (round up)
    # v5 riders (GAME_DESIGN §4.1):
    always_legal: bool = False    # BLAST/CHARGE: the button is never greyed out
    moves_to_target: bool = False # CHARGE: rush into the target's zone, then hit
    moves_one_zone: bool = False  # ESCAPE: slip one zone (◀/▶ from the tap), then hit
    acts_first: bool = False      # PROTECT: resolves before every other move this round
    applies_shield: bool = False  # PROTECT: cloak the target in a reflecting shield
    button: str = ""              # phone button label
    icon: str = ""                # emoji shown on the host readout line (§13)
    desc: str = ""
    sfx: str = ""                 # host sound clip (web/host/assets/sfx/<sfx>.wav)


class MovesConfig(BaseModel):
    moves: dict[str, MoveDef]


def load_moves(config_dir: Path | None = None) -> MovesConfig:
    data = _load_yaml("moves.yaml", config_dir)
    return _parse(MovesConfig, data, "moves.yaml")


# ---------------------------------------------------------------------------
# Bundle — passed to resolver and AI layer
# ---------------------------------------------------------------------------


class GameRules(BaseModel):
    settings: Settings
    balance: Balance
    zones: ZonesConfig
    moves: MovesConfig


def load_game_rules(config_dir: Path | None = None) -> GameRules:
    return GameRules(
        settings=load_settings(config_dir),
        balance=load_balance(config_dir),
        zones=load_zones(config_dir),
        moves=load_moves(config_dir),
    )
