"""Engine demo — Phase 2 checkpoint.

Runs 3 scripted rounds of a 4-player game using the golden-test fixture
and prints a play-by-play of events so a human can sanity-check the math.

Run with: python -m server.engine.demo
"""

from __future__ import annotations

from server.config import load_balance, load_game_rules
from server.engine.dice import Dice
from server.engine.models import Character, ClassifiedAction, Event, GameState, Stats, Team
from server.engine.resolver import resolve_round

_CFG = load_balance()


def _char(pid: str, name: str, power: int, speed: int, weird: int, hp: int, zone: str,
          conds: dict | None = None) -> Character:
    return Character(
        player_id=pid, name=name,
        stats=Stats(power=power, speed=speed, weird=weird),
        hp=hp, max_hp=_CFG.hp_base + _CFG.hp_per_power * power,
        ac=_CFG.ac_base + speed, zone_id=zone,
        conditions=conds or {},
    )


_TEAMS = [
    Team(id="team_a", name="Glitter Crew", color="pink", player_ids=["p1", "p4"]),
    Team(id="team_b", name="Thunder Squad", color="blue", player_ids=["p2", "p3"]),
]

_SCRIPTS: list[list[ClassifiedAction]] = [
    # ---- Round 1 ----
    [
        # Stabby ray vs Lawnmower
        ClassifiedAction(player_id="p1", catalog_id="ray",   action_cost=2, targets=["p3"]),
        # Blob grapple Stabby
        ClassifiedAction(player_id="p2", catalog_id="grapple", action_cost=3, targets=["p1"],
                         adaptation_note="devour attempt"),
        # Lawnmower strike Stabby
        ClassifiedAction(player_id="p3", catalog_id="strike", action_cost=2, targets=["p1"]),
        # Gerald heal himself (cost 1)
        ClassifiedAction(player_id="p4", catalog_id="heal",   action_cost=1, targets=["p4"]),
    ],
    # ---- Round 2 (golden fixture round) ----
    [
        ClassifiedAction(player_id="p3", catalog_id="demoralize", action_cost=2, targets=["p1"]),
        ClassifiedAction(player_id="p1", catalog_id="ray",         action_cost=2, targets=["p2"]),
        ClassifiedAction(player_id="p2", catalog_id="ray",         action_cost=3, targets=["p1"],
                         suggested_conditions=["sticky"],
                         adaptation_note="The Blob envelops Stabby in a blob-ray"),
        ClassifiedAction(player_id="p4", catalog_id="ray",         action_cost=2, targets=["p3"],
                         creativity_tier=2, adaptation_note="Precision water throw"),
    ],
    # ---- Round 3 ----
    [
        # Blob charges Stabby (if alive)
        ClassifiedAction(player_id="p2", catalog_id="ray",    action_cost=2, targets=["p1"]),
        # Stabby defends
        ClassifiedAction(player_id="p1", catalog_id="defend", action_cost=2),
        # Lawnmower charge Gerald
        ClassifiedAction(player_id="p3", catalog_id="ray",    action_cost=3, targets=["p4"]),
        # Gerald ray Lawnmower creativity 3
        ClassifiedAction(player_id="p4", catalog_id="ray",    action_cost=2, targets=["p3"],
                         creativity_tier=3, adaptation_note="table-losing-it water tornado"),
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
            print(f"  [{pid}->{tid}] {d.get('catalog_id','')} {result.upper()}"
                  f"  d20={d.get('d20')}  dmg={d.get('damage')}  ac={d.get('ac')}")
        elif result == "fumble":
            print(f"  [{pid}] FUMBLE d20=1  self_dmg={d.get('self_damage')}")
        elif result == "miss":
            print(f"  [{pid}->{tid}] MISS  d20={d.get('d20')}  total={d.get('total_atk')}  ac={d.get('ac')}")
        elif result == "no_target":
            print(f"  [{pid}] no valid target for {d.get('catalog_id')}")
    elif t == "condition_applied":
        print(f"  [{pid}] + {d.get('condition')} ({d.get('duration')} rounds)")
    elif t == "condition_expired":
        print(f"  [{pid}] {d.get('condition')} expired")
    elif t == "condition_ticked":
        print(f"  [{pid}] {d.get('condition')} tick -{d.get('damage', 0)} HP")
    elif t == "healed":
        src = d.get("source", "heal")
        print(f"  [{pid}] healed +{d.get('amount')}  ({src})")
    elif t == "ko":
        print(f"  *** {pid} IS KO'd -> becomes Arena Gremlin! ***")
    elif t == "banked":
        pass  # quiet
    elif t == "stumble":
        print(f"  [{pid}] stumbles dramatically")
    elif t == "victory":
        print(f"\n  ====== VICTORY: {d.get('winner_team_id')} wins! ======")
    elif t == "sudden_death":
        print("\n  ** SUDDEN DEATH -- all attacks +2, healing disabled! **")


def main() -> None:
    rules = load_game_rules()
    print("=== Doodle Brawl — Engine Demo (Phase 2) ===\n")
    print(f"Zones:      {[z.id for z in rules.zones.zones]}")
    print(f"Conditions: {sorted(rules.conditions.conditions)}")
    print(f"Moves:      {len(rules.moves.moves)} catalog entries")
    print(f"HP formula: {rules.balance.hp_base} + {rules.balance.hp_per_power} x Power")
    print(f"AC formula: {rules.balance.ac_base} + Speed")
    print()

    # Build starting state from golden fixture
    chars = [
        _char("p1", "Princess Stabby", power=3, speed=2, weird=3, hp=10,  zone="frontline"),
        _char("p2", "The Blob",        power=2, speed=2, weird=4, hp=22,  zone="frontline",
              conds={"sticky": 2}),
        _char("p3", "Sir Lawnmower",   power=4, speed=3, weird=1, hp=26,  zone="frontline"),
        _char("p4", "Gerald",          power=3, speed=1, weird=4, hp=24,  zone="glitter_back"),
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
            print(f"  {ch.name:20s}  HP={ch.hp}/{ch.max_hp}{status}{conds}")

        if result.new_state.winner_team_id:
            break
        print()

    print()
    print("Demo complete. Run pytest to verify golden test numbers.")


if __name__ == "__main__":
    main()
