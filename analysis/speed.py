#!/usr/bin/env python3
"""Is there real value in being fast when the lineups drop?

    python analysis/speed.py --label igl

The question this answers, in András's words: how much better is a quick pickup
than logging in later, past the five-minute window, and taking a starter who is
still available?

Why that question needs its own analysis. Lineups are announced at KO-75, and
from that moment **everyone** can see them. So a manager adding at KO-50 knows
exactly as much as one adding at KO-72. The snipe window therefore cannot be
an information advantage - it can only be a *queue* advantage: being first to
the best confirmed starter before someone else takes him.

A queue advantage is only worth something if the thing being queued for is
scarce. So the test is not "what did snipers score" but:

    at the exact moment of each add, what else could this manager have taken,
    and how much of that available upside did they actually capture?

That means reconstructing ownership at every add's own timestamp, not at
kickoff, and then asking which *still-unowned players with a fixture still to
come in that gameweek* went on to start. That set is the real choice.

Two metrics come out of it:

    captured    the add's points as a percentile of the pool available at that
                moment. 50 means they did no better than picking at random
                from what was there.
    gap to best how far short of the best available starter they fell, in
                points. If speed matters, this grows the later you log in.
"""
from __future__ import annotations

import argparse
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
RAW = ROOT / "data" / "raw"
RULE = "-" * 78

# Windows in minutes before kickoff. The first is before the team sheets land,
# so it is the only one where the manager is guessing; the rest all know.
WINDOWS = [
    ("blind, >75 min", 75, 1e9, False),
    ("SNIPE, 70-75", 70, 75, True),
    ("60-70 min", 60, 70, True),
    ("45-60 min", 45, 60, True),
    ("30-45 min", 30, 45, True),
    ("15-30 min", 15, 30, True),
    ("under 15 min", 0, 15, True),
]


def window_of(minutes):
    for name, low, high, informed in WINDOWS:
        if low <= minutes < high:
            return name, informed
    return None, None


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def percentile_of(value, pool):
    if not pool:
        return None
    below = sum(1 for p in pool if p < value)
    equal = sum(1 for p in pool if p == value)
    return 100.0 * (below + 0.5 * equal) / len(pool)


# --------------------------------------------------------------------------- #

def build(label):
    player_gameweeks = ref.load_player_gameweeks()
    fixtures = ref.load_fixtures(player_gameweeks)
    points = ref.points_index(player_gameweeks)
    by_player = ev.player_fixtures(player_gameweeks, fixtures)

    path = PROCESSED / "transactions.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [r for r in csv.DictReader(handle)
                if not label or r["league_label"] == label]
    for row in rows:
        row["added"] = (datetime.fromisoformat(row["stamp_utc"])
                        if row["stamp_utc"] else None)
        row["executed"] = row["executed"] == "True"
        row["is_player_move"] = row["is_player_move"] == "True"
    league_id = rows[0]["league_id"]

    # Players with a fixture in a given gameweek, and when it kicks off.
    fixture_by_player_gameweek = defaultdict(list)
    for player_id, matches in by_player.items():
        for match in matches:
            fixture_by_player_gameweek[match["gameweek"]].append(
                (player_id, match["kickoff"]))

    # Ownership is per DIVISION: a player rostered in one division is still a
    # free agent in every other. Walking a single league-wide ownership map
    # over a 12-division league would mark ~2,100 players owned who were in
    # fact available, and shrink every choice set accordingly.
    rosters = ev.draft_rosters(league_id)
    team_map, _ = ev.league_meta(league_id)

    by_division = defaultdict(set)
    for team_id, team in team_map.items():
        by_division[team["division"]].add(team_id)
    if not by_division:
        by_division[""] = {r["team_id"] for r in rows if r["team_id"]}

    results = []
    for division, teams_wanted in sorted(by_division.items()):
        owner = {}
        for team, players in rosters.items():
            if team in teams_wanted:
                for player in players:
                    owner[player] = team
        moves = ev.roster_moves(rows, teams_wanted)

        adds = []
        for row in rows:
            if row["transaction_code"] != "CLAIM":
                continue
            if not (row["executed"] and row["result_code"] == "EXECUTED"):
                continue
            if not row["added"] or not row["player_id"]:
                continue
            if row["team_id"] not in teams_wanted:
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
            minutes = (target["kickoff"] - added).total_seconds() / 60.0
            name, informed = window_of(minutes)
            if name is None:
                continue

            scored = points.get((row["player_id"], gameweek))
            if not scored:
                continue

            # The real choice set: still unowned in THIS division at this
            # instant, still has a fixture to come in this gameweek, and went
            # on to start it.
            available = []
            for player_id, kickoff in fixture_by_player_gameweek.get(gameweek, ()):
                if kickoff <= added:
                    continue
                if player_id in owner:
                    continue
                other = points.get((player_id, gameweek))
                if other and other["started"]:
                    available.append(other["fpts"])

            results.append({
                "league": row["league_label"],
                "division": division,
                "player_id": row["player_id"],
                "window": name,
                "informed": informed,
                "minutes": minutes,
                "pts": scored["fpts"],
                "started": scored["started"],
                "gameweek": gameweek,
                "position": (scored["position"] or "?").split(",")[0],
                "available": len(available),
                "best_available": max(available) if available else None,
                "captured": percentile_of(scored["fpts"], available),
                "pool_mean": statistics.mean(available) if available else None,
            })
    return results


# --------------------------------------------------------------------------- #

def supply(results):
    print(RULE)
    print("FIRST: is a confirmed starter even scarce?")
    print(RULE)
    print("A queue advantage is worth nothing if there is no queue. This is how")
    print("many unowned players who went on to START were still sitting there,")
    print("unclaimed, at the moment each manager made their add.\n")
    print("%-16s %6s %12s %14s" % ("window", "n", "mean avail", "median avail"))
    for name, _, _, _ in WINDOWS:
        rows = [r for r in results if r["window"] == name]
        if not rows:
            continue
        counts = [r["available"] for r in rows]
        print("%-16s %6d %12.1f %14.0f"
              % (name, len(rows), statistics.mean(counts),
                 statistics.median(counts)))
    print("\nIf these numbers are large, nobody is fighting over scraps and")
    print("speed cannot be worth much in a league this size.")


def capture(results):
    print("\n" + RULE)
    print("DID THE FAST MANAGERS CAPTURE MORE OF WHAT WAS THERE?")
    print(RULE)
    print("'captured' is the add's points as a percentile of the starters")
    print("actually available at that moment. 50 = no better than random from")
    print("what was on the shelf. 'gap to best' is points left on the table.\n")
    print("%-16s %6s %8s %9s %11s %12s %11s"
          % ("window", "n", "start%", "mean pts", "captured", "gap to best",
             "pool mean"))
    for name, _, _, _ in WINDOWS:
        rows = [r for r in results if r["window"] == name]
        if not rows:
            continue
        captured = [r["captured"] for r in rows if r["captured"] is not None]
        gaps = [r["best_available"] - r["pts"] for r in rows
                if r["best_available"] is not None]
        pool = [r["pool_mean"] for r in rows if r["pool_mean"] is not None]
        starts = sum(1 for r in rows if r["started"])
        print("%-16s %6d %7.1f%% %9.2f %10.1f%% %12.2f %11.2f"
              % (name, len(rows), 100.0 * starts / len(rows),
                 statistics.mean([r["pts"] for r in rows]),
                 statistics.mean(captured) if captured else 0.0,
                 statistics.mean(gaps) if gaps else 0.0,
                 statistics.mean(pool) if pool else 0.0))


def starters_only(results):
    print("\n" + RULE)
    print("ANDRAS'S QUESTION, STRAIGHT: among adds who DID start, does being")
    print("fast get you a better one?")
    print(RULE)
    print("This holds 'I picked up a starter' fixed and varies only the clock.\n")
    print("%-16s %6s %9s %9s %11s" % ("window", "n", "mean pts", "median",
                                      "captured"))
    for name, _, _, _ in WINDOWS:
        rows = [r for r in results if r["window"] == name and r["started"]]
        if not rows:
            continue
        captured = [r["captured"] for r in rows if r["captured"] is not None]
        print("%-16s %6d %9.2f %9.2f %10.1f%%"
              % (name, len(rows), statistics.mean([r["pts"] for r in rows]),
                 statistics.median([r["pts"] for r in rows]),
                 statistics.mean(captured) if captured else 0.0))

    informed = [r for r in results if r["informed"] and r["started"]]
    snipe = [r for r in informed if r["window"] == "SNIPE, 70-75"]
    later = [r for r in informed if r["window"] != "SNIPE, 70-75"]
    if snipe and later:
        print("\nthe five-minute window against everyone who logged in later,")
        print("both groups knowing the lineups, both having got a starter:")
        print("  snipe window  n=%-4d mean %.2f  median %.2f"
              % (len(snipe), statistics.mean([r["pts"] for r in snipe]),
                 statistics.median([r["pts"] for r in snipe])))
        print("  logged in later n=%-3d mean %.2f  median %.2f"
              % (len(later), statistics.mean([r["pts"] for r in later]),
                 statistics.median([r["pts"] for r in later])))
        delta = statistics.mean([r["pts"] for r in snipe]) - \
            statistics.mean([r["pts"] for r in later])
        print("  being fast is worth %+.2f FPts per add" % delta)


def verdict(results):
    print("\n" + RULE)
    print("VERDICT")
    print(RULE)
    informed = [r for r in results if r["informed"]]
    blind = [r for r in results if not r["informed"]]
    if blind and informed:
        print("adding BEFORE the team sheets  n=%-4d start%%=%.1f  mean %.2f"
              % (len(blind),
                 100.0 * sum(1 for r in blind if r["started"]) / len(blind),
                 statistics.mean([r["pts"] for r in blind])))
        print("adding AFTER the team sheets   n=%-4d start%%=%.1f  mean %.2f"
              % (len(informed),
                 100.0 * sum(1 for r in informed if r["started"]) / len(informed),
                 statistics.mean([r["pts"] for r in informed])))
        print("\nThat gap is the value of the INFORMATION.")
    captured = [r["captured"] for r in informed if r["captured"] is not None]
    if captured:
        print("\nAmong informed adds, mean captured percentile: %.1f%%"
              % statistics.mean(captured))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label")
    args = parser.parse_args()
    results = build(args.label)
    if not results:
        sys.exit("no adds resolved")
    print("%d adds resolved against the pool available at their own moment\n"
          % len(results))
    supply(results)
    capture(results)
    starters_only(results)
    verdict(results)


if __name__ == "__main__":
    main()
