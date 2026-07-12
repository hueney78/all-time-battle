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
    triggers: list[str] = ["crit", "ko"]
    slowmo_factor: float = 2.0


class AudioConfig(BaseModel):
    """Host-page Web Audio manager knobs. `events_sfx` maps engine event types
    to stinger clips; per-move clips are the `sfx` keys in moves.yaml."""

    enabled: bool = True
    volume: float = 0.8
    pitch_variation: float = 0.10
    sfx_dir: str = "/static/host/assets/sfx"
    events_sfx: dict[str, str] = {
        "crit": "crowd_roar",
        "fumble": "sad_trombone",
        "ko": "ko_bell",
        "combo": "air_horn",
        "sudden_death": "drumroll",
        "replay": "replay",
    }


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
    # splash_text keys: draw_characters / draw_action / montage / gremlin
    # ("{round}" is substituted); phones show `gremlin` to KO'd players.
    phase_splash_seconds: float = 2.0
    splash_text: dict[str, str] = {
        "draw_characters": "Draw your Character!",
        "draw_action": "Round {round} — Draw your Move!",
        "montage": "🎵 Upgrade your Character! 🎵",
        "gremlin": "Draw a Hazard, Gremlin! 😈",
    }
    reveal_action_zoom_scale: float = 1.8
    reveal_action_zoom_seconds: float = 2.5
    reveal_beat_seconds: float = 3.2   # per-beat auto-advance pace; 0 = manual (host clicks Next ▶)
    float_number_seconds: float = 1.5
    audience_recent_rounds: int = 3
    combo_splash_seconds: float = 2.0
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
    # HP formula: HP = hp_base + hp_per_power * Power
    hp_base: int = 20
    hp_per_power: int = 2
    # AC formula: AC = ac_base + Speed
    ac_base: int = 10
    # Stat budget — AI distributes stats summing to this
    stat_budget: int = 9
    stat_min: int = 0
    stat_max: int = 6
    # Degrees of success (2d6): crit on natural 12 or beating AC by crit_margin;
    # fumble on natural 2 (a move's fumble_on_roll_lte can widen the band).
    crit_margin: int = 5
    crit_damage_mult: float = 2.0
    fumble_self_damage: int = 3
    # Creativity bonuses (added to the 2d6 roll)
    creativity_tier_0: int = 0
    creativity_tier_1: int = 1
    creativity_tier_2: int = 2
    creativity_tier_3: int = 4
    # Combo rules — both partners gain this on their own rolls (no fusion)
    combo_bonus: int = 2
    # Rubber-banding
    underdog_enabled: bool = True
    underdog_hp_share_threshold: int = 2
    underdog_attack_bonus: int = 1
    # Sudden death
    max_rounds: int = 12
    sudden_death_attack_bonus: int = 2


def load_balance(config_dir: Path | None = None) -> Balance:
    data = _load_yaml("balance.yaml", config_dir)
    return _parse(Balance, data, "balance.yaml")


# ---------------------------------------------------------------------------
# zones.yaml
# ---------------------------------------------------------------------------


class ZoneModifiers(BaseModel):
    model_config = {"extra": "allow"}
    attack_bonus: int = 0
    ac_bonus: int = 0
    ranged_ac_bonus: int = 0
    damage_bonus: int = 0
    speed_penalty: int = 0


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
    # Movement is tapped, absolute (◀/▶ match the TV), edge-disabled.
    move_buttons: list[str] = ["move_l", "move_r"]


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
    """One COMBAT V2 move (GAME_DESIGN §4.1). The catalog owns all math;
    formulas may reference POW/SPD/WRD (see engine/dice.py)."""

    model_config = {"extra": "allow"}
    stat: str = "none"       # attack-roll stat: "power" | "speed" | "weird" | "none"
    range: str = "same_zone"  # "same_zone" | "any"
    # "single_enemy" | "zone_all" | "ally_or_self" | "zone_allies" | "self"
    target: str = "single_enemy"
    damage: str | None = None     # formula, e.g. "(1 + ceil(POW/2))d4 + 2"
    heal: str | None = None       # formula, may reference CRE (creativity bonus)
    same_zone_penalty: str | None = None  # "half" → point-blank damage halved (round up)
    # Round-scoped defensive buff: SHIELD's +4 to its zone_allies, movement's
    # +1 dodge. Lasts from when the move resolves until end of round.
    ac_bonus: int = 0
    # SHIELD reflect: attacks missing a shielded target by reflect_miss_margin+
    # deal reflect_damage back to the attacker (0/"" disables).
    reflect_miss_margin: int = 0
    reflect_damage: str = ""
    friendly_fire: bool = False
    auto_step: bool = False       # SMASH: no enemy in zone → step toward target
    fumble_on_roll_lte: int | None = None     # WILD: natural 2d6 <= this fumbles
    move: int = 0                 # absolute zone steps (◀ = -1, ▶ = +1)
    button: str = ""              # phone button label
    desc: str = ""
    sfx: str = ""                 # host sound clip (web/host/assets/sfx/<sfx>.wav)

    @property
    def is_movement(self) -> bool:
        return self.move != 0


class MovesConfig(BaseModel):
    moves: dict[str, MoveDef]


def load_moves(config_dir: Path | None = None) -> MovesConfig:
    data = _load_yaml("moves.yaml", config_dir)
    return _parse(MovesConfig, data, "moves.yaml")


# ---------------------------------------------------------------------------
# hazards.yaml — the Arena Gremlin hazard palette (GAME_DESIGN §10)
# ---------------------------------------------------------------------------


class HazardDef(BaseModel):
    model_config = {"extra": "allow"}
    # A hazard damages a zone's occupants or forces them to move (v2.1: no
    # status effects). Adding a hazard is YAML-only.
    damage: str = ""              # dice spec rolled once for the whole zone
    forces_move: bool = False
    emoji: str = ""
    sfx: str = ""
    desc: str = ""


class HazardsConfig(BaseModel):
    hazards: dict[str, HazardDef]


def load_hazards(config_dir: Path | None = None) -> HazardsConfig:
    data = _load_yaml("hazards.yaml", config_dir)
    return _parse(HazardsConfig, data, "hazards.yaml")


# ---------------------------------------------------------------------------
# Bundle — passed to resolver and AI layer
# ---------------------------------------------------------------------------


class GameRules(BaseModel):
    settings: Settings
    balance: Balance
    zones: ZonesConfig
    moves: MovesConfig
    hazards: HazardsConfig = HazardsConfig(hazards={})


def load_game_rules(config_dir: Path | None = None) -> GameRules:
    return GameRules(
        settings=load_settings(config_dir),
        balance=load_balance(config_dir),
        zones=load_zones(config_dir),
        moves=load_moves(config_dir),
        hazards=load_hazards(config_dir),
    )
