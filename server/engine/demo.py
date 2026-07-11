"""Engine demo — scripted COMBAT V2 rounds.

Runs 3 scripted rounds of the GAME_DESIGN §12 2v2 fixture and prints a
play-by-play of events so a human can sanity-check the math.

Run with: python -m server.engine.demo
"""

from __future__ import annotations

from server.config import load_balance, load_game_rules
from server.engine.dice import Dice
from server.engine.models import Character, ClassifiedAction, Event, GameState, Stats, Team
from server.engine.resolver import resolve_round

_CFG = load_balance()


def _char(pid: str, name: str, power: int, speed: int, weird: int, zone: str,
          conds: dict | None = None) -> Character:
    hp = _CFG.hp_base + _CFG.hp_per_power * power
    return Character(
        player_id=pid, name=name,
        stats=Stats(power=power, speed=speed, weird=weird),
        hp=hp, max_hp=hp,
        ac=_CFG.ac_base + speed, zone_id=zone,
        conditions=conds or {},
    )


_TEAMS = [
    Team(id="team_a", name="Team A", color="pink", player_ids=["p1", "p4"]),
    Team(id="team_b", name="Team B", color="blue", player_ids=["p2", "p3"]),
]

_SCRIPTS: list[list[ClassifiedAction]] = [
    # ---- Round 1 (the §12 worked round) ----
    [
        # Stabby TRICK on Blob — glitter hypnosis, creativity 2
        ClassifiedAction(player_id="p1", move_id="trick", target_id="p2",
                         creativity_tier=2, trick_condition="confused",
                         flavor_summary="glitter hypnosis"),
        # Blob BLAST on the front zone (creativity 1)
        ClassifiedAction(player_id="p2", move_id="blast", target_id="p1",
                         creativity_tier=1),
        # Lawnmower SMASH on Gerald (auto-step, creativity 0)
        ClassifiedAction(player_id="p3", move_id="smash", target_id="p4"),
        # Gerald SHIELDs himself — one round too late
        ClassifiedAction(player_id="p4", move_id="shield", target_id="p4"),
    ],
    # ---- Round 2 ----
    [
        # Stabby can't repeat TRICK — WILD CARD gamble on Lawnmower
        ClassifiedAction(player_id="p1", move_id="wild", target_id="p3",
                         creativity_tier=1),
        # Blob TRICKs Stabby
        ClassifiedAction(player_id="p2", move_id="trick", target_id="p1",
                         trick_condition="sticky"),
        # Lawnmower steps back toward its backline, dodging
        ClassifiedAction(player_id="p3", move_id="move_r"),
        # Gerald RALLIES himself with a table-losing-it drawing
        ClassifiedAction(player_id="p4", move_id="rally", target_id="p4",
                         creativity_tier=3),
    ],
    # ---- Round 3 ----
    [
        ClassifiedAction(player_id="p1", move_id="smash", target_id="p3"),
        ClassifiedAction(player_id="p2", move_id="blast", target_id="p4",
                         creativity_tier=2),
        ClassifiedAction(player_id="p3", move_id="blast", target_id="p1"),
        ClassifiedAction(player_id="p4", move_id="trick", target_id="p2",
                         trick_condition="burning", creativity_tier=1),
    ],
]


def _print_event(ev: Event) -> None:
    t = ev.type.value
    pid = ev.player_id or "—"
    tid = ev.target_id or ""
    d = ev.data
    if t == "attack_resolved":
        result = d.get("result", "?")
        if result in ("hit", "crit"):
            print(f"  [{pid}->{tid}] {d.get('move_id','')} {result.upper()}"
                  f"  2d6={d.get('natural')}  total={d.get('total_atk')}"
                  f"  vs AC {d.get('ac')}  dmg={d.get('damage')}")
        elif result == "fumble":
            print(f"  [{pid}] FUMBLE 2d6={d.get('natural')}  self_dmg={d.get('self_damage')}")
        elif result == "miss":
            print(f"  [{pid}->{tid}] MISS  2d6={d.get('natural')}"
                  f"  total={d.get('total_atk')}  vs AC {d.get('ac')}")
        elif result == "reflect":
            print(f"  [{pid}] SHIELD REFLECT -> {tid} for {d.get('damage')}")
        elif result == "out_of_reach":
            print(f"  [{pid}] can't reach {tid} — the swing hits air")
        elif result == "no_target":
            print(f"  [{pid}] no valid target for {d.get('move_id')}")
    elif t == "moved":
        print(f"  [{pid}] moves {d.get('from')} -> {d.get('to')}")
    elif t == "combo":
        print(f"  ** COMBO! {d.get('combo_name')} ({', '.join(d.get('partners', []))}) **")
    elif t == "condition_applied":
        print(f"  [{pid}] + {d.get('condition')} ({d.get('duration')} rounds)")
    elif t == "condition_expired":
        print(f"  [{pid}] {d.get('condition')} expired")
    elif t == "condition_ticked":
        print(f"  [{pid}] {d.get('condition')} tick -{d.get('damage', 0)} HP")
    elif t == "healed":
        print(f"  [{pid}] heals {tid or pid} +{d.get('amount')}")
    elif t == "ko":
        print(f"  *** {pid} IS KO'd -> becomes Arena Gremlin! ***")
    elif t == "stumble":
        print(f"  [{pid}] stumbles dramatically")
    elif t == "victory":
        print(f"\n  ====== VICTORY: {d.get('winner_team_id')} wins! ======")
    elif t == "sudden_death":
        print("\n  ** SUDDEN DEATH -- all attacks +2, healing disabled! **")


def main() -> None:
    rules = load_game_rules()
    print("=== Doodle Brawl — Engine Demo (COMBAT V2) ===\n")
    print(f"Zones:      {[z.id for z in rules.zones.zones]}")
    print(f"Conditions: {sorted(rules.conditions.conditions)}")
    print(f"Moves:      {list(rules.moves.moves)}")
    print(f"HP formula: {rules.balance.hp_base} + {rules.balance.hp_per_power} x Power")
    print(f"AC formula: {rules.balance.ac_base} + Speed")
    print()

    # GAME_DESIGN §12 fixture: 2v2, seed 42.
    chars = [
        _char("p1", "Princess Stabby", power=1, speed=5, weird=3, zone="frontline"),
        _char("p2", "The Blob",        power=0, speed=3, weird=6, zone="thunder_back"),
        _char("p3", "Sir Lawnmower",   power=6, speed=2, weird=1, zone="frontline"),
        _char("p4", "Gerald",          power=3, speed=1, weird=5, zone="glitter_back"),
    ]

    print("Starting lineup:")
    for ch in chars:
        cond_str = f"  [{', '.join(ch.conditions)}]" if ch.conditions else ""
        print(f"  {ch.name:20s}  HP={ch.hp}/{ch.max_hp}  AC={ch.ac}"
              f"  POW={ch.stats.power} SPD={ch.stats.speed} WRD={ch.stats.weird}"
              f"  zone={ch.zone_id}{cond_str}")
    print()

    state = GameState(
        room_id="DEMO",
        characters={c.player_id: c for c in chars},
        teams=_TEAMS,
    )

    rng = Dice(seed=42)

    for round_num, actions in enumerate(_SCRIPTS, start=1):
        state = state.model_copy(update={"round": round_num})
        sep = "-" * 60
        print(sep)
        print(f"ROUND {round_num}")
        print(sep)

        result = resolve_round(state, actions, rng, _CFG)

        for ev in result.events:
            _print_event(ev)

        state = result.new_state
        print()
        print("HP after round:")
        for pid in ["p1", "p2", "p3", "p4"]:
            ch = state.characters[pid]
            status = " [KO->GREMLIN]" if ch.is_ko else ""
            conds = f"  {ch.conditions}" if ch.conditions else ""
            print(f"  {ch.name:20s}  HP={ch.hp}/{ch.max_hp}  zone={ch.zone_id}{status}{conds}")

        if result.new_state.winner_team_id:
            break
        print()

    print()
    print("Demo complete. Run pytest to verify golden test numbers.")


if __name__ == "__main__":
    main()
