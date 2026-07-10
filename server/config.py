"""Typed config loaders — all values loaded from config/*.yaml.

Bad YAML raises a clear error naming the file. Bad field names/types raise
a ValidationError that pydantic formats with the offending key path.
Hot-reload per room: call load_game_rules() at room creation time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
    reveal_action_zoom_scale: float = 1.8
    reveal_action_zoom_seconds: float = 2.5
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


class CostScalingEntry(BaseModel):
    damage_mult: float
    bank: int
    hit_bonus: int


class Balance(BaseModel):
    # HP formula: HP = hp_base + hp_per_power * Power
    hp_base: int = 18
    hp_per_power: int = 2
    # AC formula: AC = ac_base + Speed
    ac_base: int = 11
    # Stat budget — AI distributes stats summing to this
    stat_budget: int = 8
    stat_min: int = 1
    stat_max: int = 4
    # Action cost scaling (keys 1/2/3)
    cost_scaling: dict[int, CostScalingEntry]
    # Banked action economy
    banked_ac_per_action: int = 1
    banked_free_step_threshold: int = 2
    # Degrees of success
    crit_margin: int = 10
    fumble_margin: int = 10
    crit_damage_mult: float = 2.0
    # Creativity bonuses (added to attack roll)
    creativity_tier_0: int = 0
    creativity_tier_1: int = 1
    creativity_tier_2: int = 2
    creativity_tier_3: int = 4
    # Stale move penalty
    stale_penalty: int = -2
    # Combo rules
    combo_bonus: int = 3
    combo_creativity_escalate: int = 1
    # Rubber-banding
    underdog_enabled: bool = True
    underdog_hp_share_threshold: int = 2
    underdog_attack_bonus: int = 1
    # Fumble self-damage (flat HP; embarrassed condition is separately auto-applied)
    fumble_self_damage: int = 2
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
    fumble_extra: str | None = None


class ZoneDef(BaseModel):
    model_config = {"extra": "allow"}
    id: str
    name: str
    adjacent: list[str]
    tags: list[str] = []
    capacity: int | None = None
    entry_cost: int = 1
    modifiers: ZoneModifiers = ZoneModifiers()


class FreeStepsRule(BaseModel):
    threshold: int = 3
    steps: int = 1


class ZoneRules(BaseModel):
    melee_requires_same_zone: bool = True
    ranged_any_zone: bool = True
    move_cost_per_step: int = 1
    free_steps_from_speed: FreeStepsRule = FreeStepsRule()


class ZonesConfig(BaseModel):
    zones: list[ZoneDef]
    rules: ZoneRules


def load_zones(config_dir: Path | None = None) -> ZonesConfig:
    data = _load_yaml("zones.yaml", config_dir)
    return _parse(ZonesConfig, data, "zones.yaml")


# ---------------------------------------------------------------------------
# conditions.yaml
# ---------------------------------------------------------------------------


class ConditionModifiers(BaseModel):
    model_config = {"extra": "allow"}
    power: int = 0
    speed: int = 0
    attack: int = 0
    ac: int = 0


class ConditionDef(BaseModel):
    model_config = {"extra": "allow"}
    duration: int
    tick_damage: int = 0
    cure_tags: list[str] = []
    immunities: list[str] = []
    modifiers: ConditionModifiers = ConditionModifiers()
    blocks_free_step: bool = False
    stand_cost: int = 0
    trigger: str | None = None
    emoji: str = ""
    untargetable_melee: bool = False
    ac_bonus_vs_ranged: int = 0
    ac_penalty_vs_feinter_team: int = 0
    incoming_attack_bonus: int = 0
    randomize_targets: bool = False
    # Negative status? Only debuffs can be stripped by `cleanse`; buffs and
    # markers (pumped, shielded, transformed, …) are left alone.
    debuff: bool = False


class ConditionsConfig(BaseModel):
    conditions: dict[str, ConditionDef]


def load_conditions(config_dir: Path | None = None) -> ConditionsConfig:
    data = _load_yaml("conditions.yaml", config_dir)
    return _parse(ConditionsConfig, data, "conditions.yaml")


# ---------------------------------------------------------------------------
# moves.yaml
# ---------------------------------------------------------------------------


class MoveDef(BaseModel):
    model_config = {"extra": "allow"}
    pf2e: str = ""
    roll: str = "none"      # "power" | "weird" | "none"
    range: str = "same_zone"
    target: str = "single_enemy"
    damage: str | None = None
    desc: str = ""
    sfx: str = ""           # host sound clip name (web/host/assets/sfx/<sfx>.wav)
    min_cost: int = 1
    includes_move: bool = False
    friendly_fire: bool = False
    on_hit_condition: str | None = None
    on_hit_push_zones: int = 0
    on_hit_steal_banked: bool = False
    heal: str | None = None
    heal_self_ratio: float = 0.0
    creates_hazard: bool = False
    hidden_hazard: bool = False
    counters_next_attack: bool = False
    redirect_attacks_to_self: bool = False
    zone_modifier: dict[str, Any] = {}
    removes_conditions: int = 0
    grants_roll_bonus: int = 0
    applies_condition: str | None = None
    stat_swap: int = 0
    duration: int | None = None
    move_zones_per_cost: int = 0
    fixed_cost: int | None = None
    ac_bonus: int = 0


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
    # A hazard applies a condition to a zone's occupants, forces them to move, or
    # both. Effects reuse existing registries so adding a hazard is YAML-only.
    applies_condition: str | None = None
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
    conditions: ConditionsConfig
    moves: MovesConfig
    hazards: HazardsConfig = HazardsConfig(hazards={})


def load_game_rules(config_dir: Path | None = None) -> GameRules:
    return GameRules(
        settings=load_settings(config_dir),
        balance=load_balance(config_dir),
        zones=load_zones(config_dir),
        conditions=load_conditions(config_dir),
        moves=load_moves(config_dir),
        hazards=load_hazards(config_dir),
    )
