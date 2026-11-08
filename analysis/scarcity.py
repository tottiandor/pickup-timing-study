#!/usr/bin/env python3
"""Is the position effect real, or is it positional scarcity?

    python analysis/scarcity.py

The objection, and it is a good one: forwards are rostered less than
midfielders and defenders, so the forwards sitting free are a more
speculative pool to begin with. Their lower start rate after a pickup would
then say nothing about forwards being risky picks - it would just be the
pool. The same confound would inflate any position that happens to be
heavily rostered, because what is left free there is thin but safe.

So every position is normalised against **its own** available pool, measured
at the exact moment of each add, in that add's own division:

    saturation      what share of the position's players were rostered
    pool start rate what share of the FREE players at that position went on
                    to start - the base rate a manager is drawing from
    lift            the add's start rate minus that base rate. This is the
                    number that says whether managers pick well at a
                    position, with scarcity divided out.
    within-position where the add ranked among the free *confirmed starters*
    percentile      at its own position, rather than against all positions

If the raw position gap survives the lift and the within-position percentile,
it is a real difference in how well managers choose. If it does not, it was
scarcity all along and the advice "take the defender" is worthless.
"""
from __future__ import annotations

import csv
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scraper"))

import reference as ref  # noqa: E402
import events as ev  # noqa: E402

PROCESSED = ROOT / "data" / "processed"
RULE = "-" * 78
POS = {"G": "Goalkeepers", "D": "Defenders", "M": "Midfielders", "F": "Forwards"}
ORDER = ("G", "D", "M", "F")


def first_position(value):
    return (value or "?").split(",")[0].strip() or "?"


def percentile_of(value, ordered):
    if not ordered:
        return None
    low, high = 0, len(ordered)
    while low < high:
        mid = (low + high) // 2
        if ordered[mid] < value:
            low = mid + 1
        else:
            high = mid
    below = low
    high = len(ordered)
    low = below
    while low < high:
        mid = (low + high) // 2
        if ordered[mid] <= value:
            low = mid + 1
        else:
            high = mid
    equal = low - below
    return 100.0 * (below + 0.5 * equal) / len(ordered)


def build():
    player_gameweeks = ref.load_player_gameweeks()
    fixtures = ref.load_fixtures(player_gameweeks)
    points = ref.points_index(player_gameweeks)
    by_player = ev.player_fixtures(player_gameweeks, fixtures)

    # Position per player, taken from the scoring data.
    position_of = {}
    for row in player_gameweeks:
        position_of[row["player_id"]] = first_position(row["position"])

    # Every player with a fixture in a gameweek, and when it starts.
    in_gameweek = defaultdict(list)
    for player_id, matches in by_player.items():
        for match in matches:
            in_gameweek[match["gameweek"]].append((player_id, match["kickoff"]))

    with (PROCESSED / "transactions.csv").open(encoding="utf-8", newline="") as fh:
        rows = [r for r in csv.DictReader(fh)]
    for row in rows:
        row["added"] = (datetime.fromisoformat(row["stamp_utc"])
                        if row["stamp_utc"] else None)
        row["executed"] = row["executed"] == "True"
        row["is_player_move"] = row["is_player_move"] == "True"

    results = []
    for label in sorted({r["league_label"] for r in rows}):
        subset = [r for r in rows if r["league_label"] == label]
        league_id = subset[0]["league_id"]
        rosters = ev.draft_rosters(league_id)
        team_map, _ = ev.league_meta(league_id)

        by_division = defaultdict(set)
        for team_id, team in team_map.items():
            by_division[team["division"]].add(team_id)
        if not by_division:
            by_division[""] = {r["team_id"] for r in subset if r["team_id"]}

        for division, teams in sorted(by_division.items()):
            owner = {}
            for team, players in rosters.items():
                if team in teams:
                    for player in players:
                        owner[player] = team
            moves = ev.roster_moves(subset, teams)

            adds = []
            for row in subset:
                if row["transaction_code"] != "CLAIM":
                    continue
                if not (row["executed"] and row["result_code"] == "EXECUTED"):
                    continue
                if not row["added"] or row["team_id"] not in teams:
                    continue
                matches = by_player.get(row["player_id"])
                if not matches:
                    continue
                target = ev.next_fixture(matches, row["added"])
                if target:
                    adds.append((row["added"], row, target))
            adds.sort(key=lambda a: a[0])

            cursor = 0
            for added, row, target in adds:
                while cursor < len(moves) and moves[cursor][0] <= added:
                    _, _, action, player, team = moves[cursor]
                    if action == "add":
                        owner[player] = team
                    elif owner.get(player) == team:
                        owner.pop(player, None)
                    cursor += 1

                gameweek = target["gameweek"]
                scored = points.get((row["player_id"], gameweek))
                if not scored:
                    continue
                position = first_position(scored["position"])

                # The pool at this instant, split by position.
                total_at_pos = free_at_pos = started_at_pos = 0
                free_starter_points = []
                for player_id, kickoff in in_gameweek.get(gameweek, ()):
                    if position_of.get(player_id) != position:
                        continue
                    total_at_pos += 1
                    if kickoff <= added or player_id in owner:
                        continue
                    free_at_pos += 1
                    other = points.get((player_id, gameweek))
                    if other and other["started"]:
                        started_at_pos += 1
                        free_starter_points.append(other["fpts"])

                free_starter_points.sort()
                minutes = (target["kickoff"] - added).total_seconds() / 60.0
                results.append({
                    "league": label,
                    "division": division,
                    "position": position,
                    "gameweek": gameweek,
                    "informed": minutes < 75,
                    "pts": scored["fpts"],
                    "started": scored["started"],
                    "saturation": (100.0 * (total_at_pos - free_at_pos)
                                   / total_at_pos) if total_at_pos else None,
                    "free_at_pos": free_at_pos,
                    "pool_start_rate": (100.0 * started_at_pos / free_at_pos)
                                       if free_at_pos else None,
                    "within_pos_pctile": percentile_of(scored["fpts"],
                                                       free_starter_points),
                    "pool_mean_started": (statistics.mean(free_starter_points)
                                          if free_starter_points else None),
                })
    return results


# --------------------------------------------------------------------------- #

def saturation(rows):
    print(RULE)
    print("IS THE POOL DIFFERENT BY POSITION? (the objection, measured)")
    print(RULE)
    print("Taken at the moment of each pickup, inside that add's division.\n")
    print("%-14s %8s %12s %14s %16s"
          % ("position", "n", "% rostered", "free players", "of those, start%"))
    for code in ORDER:
        sub = [r for r in rows if r["position"] == code
               and r["saturation"] is not None]
        if not sub:
            continue
        print("%-14s %8d %11.1f%% %14.0f %15.1f%%"
              % (POS[code], len(sub),
                 statistics.mean([r["saturation"] for r in sub]),
                 statistics.mean([r["free_at_pos"] for r in sub]),
                 statistics.mean([r["pool_start_rate"] for r in sub
                                  if r["pool_start_rate"] is not None])))
    print("\nIf the 'of those, start%' column moves a lot by position, the raw")
    print("start rates in the rest of this study were measuring the pool, not")
    print("the manager.")


def lift(rows):
    print("\n" + RULE)
    print("START RATE, WITH SCARCITY DIVIDED OUT")
    print(RULE)
    print("'Lift' is the manager's start rate minus the base rate of the free")
    print("players at that position. It is what is left after the pool.\n")
    print("%-14s %8s %11s %12s %10s"
          % ("position", "n", "add start%", "pool start%", "lift"))
    lifts = {}
    for code in ORDER:
        sub = [r for r in rows if r["position"] == code
               and r["pool_start_rate"] is not None]
        if not sub:
            continue
        add_rate = 100.0 * sum(1 for r in sub if r["started"]) / len(sub)
        pool_rate = statistics.mean([r["pool_start_rate"] for r in sub])
        lifts[code] = add_rate - pool_rate
        print("%-14s %8d %10.1f%% %11.1f%% %+9.1f"
              % (POS[code], len(sub), add_rate, pool_rate, lifts[code]))
    if len(lifts) > 1:
        best, worst = max(lifts, key=lifts.get), min(lifts, key=lifts.get)
        print("\n  spread in lift: %.1f points, %s best, %s worst"
              % (lifts[best] - lifts[worst], POS[best], POS[worst]))
    return lifts


def within_position(rows):
    print("\n" + RULE)
    print("POINTS, RANKED INSIDE EACH POSITION'S OWN POOL")
    print(RULE)
    print("The raw means said defenders beat midfielders by 1.25 FPts. But")
    print("defenders score more in this scoring system anyway. Ranking each")
    print("add against the free confirmed starters at its OWN position asks")
    print("whether managers choose better there, scarcity removed.\n")
    print("%-14s %8s %11s %14s %16s"
          % ("position", "n", "mean FPts", "pool mean", "within-pos pctile"))
    pctiles = {}
    for code in ORDER:
        sub = [r for r in rows if r["position"] == code and r["started"]
               and r["within_pos_pctile"] is not None]
        if len(sub) < 20:
            continue
        pctiles[code] = statistics.mean([r["within_pos_pctile"] for r in sub])
        print("%-14s %8d %11.2f %14.2f %15.1f%%"
              % (POS[code], len(sub),
                 statistics.mean([r["pts"] for r in sub]),
                 statistics.mean([r["pool_mean_started"] for r in sub
                                  if r["pool_mean_started"] is not None]),
                 pctiles[code]))
    if len(pctiles) > 1:
        best, worst = max(pctiles, key=pctiles.get), min(pctiles, key=pctiles.get)
        print("\n  spread: %.1f percentile points, %s best, %s worst"
              % (pctiles[best] - pctiles[worst], POS[best], POS[worst]))
        print("\n  50%% is a coin flip among the free starters at that")
        print("  position. A flat column here means the apparent position")
        print("  effect was the scoring system and the pool, not the choosing.")
    return pctiles


def verdict(lifts, pctiles):
    print("\n" + RULE)
    print("VERDICT ON 'TAKE THE DEFENDER'")
    print(RULE)
    if not (lifts and pctiles):
        return
    spread_lift = max(lifts.values()) - min(lifts.values())
    spread_pct = max(pctiles.values()) - min(pctiles.values())
    print("  spread in start-rate lift      : %.1f points" % spread_lift)
    print("  spread in within-position rank : %.1f percentile points" % spread_pct)
    print("")
    if spread_pct < 5:
        print("  The within-position ranks are flat. Managers choose about")
        print("  equally well at every position, and the raw 1.25 FPts gap")
        print("  was positional scarcity plus the scoring system - NOT a")
        print("  skill difference. 'Take the defender' does not survive.")
    else:
        print("  The ranks still separate, so some of the position effect is")
        print("  real choosing rather than pool composition.")


def main():
    rows = build()
    print("%d pickups measured against their own position's pool\n" % len(rows))
    saturation(rows)
    lifts = lift(rows)
    pctiles = within_position(rows)
    verdict(lifts, pctiles)


if __name__ == "__main__":
    main()
