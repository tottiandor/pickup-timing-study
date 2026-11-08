#!/usr/bin/env python3
"""Does the "speed is worthless" result depend on supply being deep?

    python analysis/supply.py --label igl

`speed.py` found that being fast when the lineups drop is worth nothing, and
offered one obvious get-out: the pilot is a 12-team league with 44.6 spare
starters sitting unowned at any moment, so of course the queue is worthless.
This asks whether thin supply would change the answer.

Three tests, in order of how much they can be trusted:

1. **The natural experiment already in the data.** Pool depth is not constant.
   An add before Saturday lunchtime has the whole weekend's fixtures still to
   come and a deep pool; an add before the last match of a gameweek has almost
   nothing left. So the adds can be split by the depth that actually faced
   them, and speed re-tested inside each stratum. This is real data, not a
   model.

2. **Depletion.** If there were a queue, the best available starter would get
   worse through the announcement window as rivals took the good ones. If the
   best option is as good at KO-20 as at KO-75, there was never a queue.

3. **A simulation of bigger divisions**, clearly labelled as one. Extra teams
   are added, each taking the best players still free, and the surviving
   starter pool is counted. It says at what division size supply would start
   to bind - which is the number that decides whether this result travels.

A structural note that corrects an earlier assumption of mine: free agency is
per **division**, not per league. A 143-team league split into twelve 12-team
divisions has exactly the pilot's supply depth. League size is irrelevant;
division size is everything.
"""
from __future__ import annotations

import argparse
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scraper"))

import reference as ref  # noqa: E402
import speed  # noqa: E402

RULE = "-" * 78
SNIPE = "SNIPE, 70-75"
ROSTER_SLOTS = 16


def depth_strata(results):
    print(RULE)
    print("TEST 1: the natural experiment - speed, re-tested at each depth")
    print(RULE)
    print("Pool depth is not constant. An add before Saturday lunchtime has the")
    print("weekend ahead of it; an add before a gameweek's last kickoff has")
    print("almost nothing left. So we can ask the question again inside each")
    print("level of depth, on real data.\n")

    informed = [r for r in results if r["informed"] and r["started"]
                and r["captured"] is not None]
    if not informed:
        print("no informed adds with a pool to compare against")
        return

    counts = sorted(r["available"] for r in informed)
    cuts = (counts[len(counts) // 3], counts[2 * len(counts) // 3])
    print("depth terciles by starters still available: thin <=%d, "
          "mid <=%d, deep >%d\n" % (cuts[0], cuts[1], cuts[1]))

    def band(row):
        if row["available"] <= cuts[0]:
            return "thin"
        if row["available"] <= cuts[1]:
            return "mid"
        return "deep"

    print("%-6s %-16s %5s %9s %10s %12s"
          % ("depth", "window", "n", "mean pts", "captured", "mean avail"))
    for name in ("thin", "mid", "deep"):
        rows = [r for r in informed if band(r) == name]
        snipes = [r for r in rows if r["window"] == SNIPE]
        later = [r for r in rows if r["window"] != SNIPE]
        for label, group in (("snipe window", snipes), ("logged in later", later)):
            if not group:
                continue
            print("%-6s %-16s %5d %9.2f %9.1f%% %12.1f"
                  % (name, label, len(group),
                     statistics.mean([r["pts"] for r in group]),
                     statistics.mean([r["captured"] for r in group]),
                     statistics.mean([r["available"] for r in group])))
        if len(snipes) > 2 and len(later) > 2:
            fast = [r["pts"] for r in snipes]
            slow = [r["pts"] for r in later]
            delta = statistics.mean(fast) - statistics.mean(slow)
            # Welch standard error. Without it a +0.94 on 30 events reads as a
            # finding, when it is half a standard error of nothing.
            error = math.sqrt(statistics.variance(fast) / len(fast)
                              + statistics.variance(slow) / len(slow))
            ratio = delta / error if error else 0.0
            if abs(ratio) < 2:
                verdict = "  <- within noise (%.1f SE)" % abs(ratio)
            elif delta > 0:
                verdict = "  <- speed PAYS, %.1f SE" % ratio
            else:
                verdict = "  <- speed HURTS, %.1f SE" % abs(ratio)
            print("%-6s %-16s %5s %+9.2f  +/-%.2f%s"
                  % (name, "difference", "", delta, error, verdict))
        print()

    print("Read the 'difference' line against its standard error, not its")
    print("sign. The question is whether speed's value grows as the shelf")
    print("empties - which is what a queue advantage would look like - or")
    print("whether it is flat across depth, which would mean scarcity is not")
    print("what drives it.")


def depletion(results):
    print("\n" + RULE)
    print("TEST 2: does the top of the pool actually get taken?")
    print(RULE)
    print("With a real queue, the best available starter degrades as the window")
    print("runs on. Flat means nobody was competing for him.\n")
    print("%-16s %5s %14s %14s %12s"
          % ("window", "n", "best avail", "pool mean", "mean avail"))
    for name, _, _, _ in speed.WINDOWS:
        rows = [r for r in results if r["window"] == name
                and r["best_available"] is not None]
        if not rows:
            continue
        print("%-16s %5d %14.2f %14.2f %12.1f"
              % (name, len(rows),
                 statistics.mean([r["best_available"] for r in rows]),
                 statistics.mean([r["pool_mean"] for r in rows]),
                 statistics.mean([r["available"] for r in rows])))
    print("\nA flat 'best avail' column across the windows means the good")
    print("options were still there, untouched, right up to kickoff.")


def simulate(label):
    print("\n" + RULE)
    print("TEST 3: SIMULATION - how big must a division be before starters")
    print("actually run out?")
    print(RULE)
    print("Assumptions, stated plainly: every team carries %d players; extra"
          % ROSTER_SLOTS)
    print("teams draft the best players still free by season points; the real")
    print("25/26 starting elevens are held fixed. This is a model, not data -")
    print("it answers 'where is the cliff', not 'what happened'.\n")

    player_gameweeks = ref.load_player_gameweeks()
    points = ref.points_index(player_gameweeks)

    season = defaultdict(float)
    started_in = defaultdict(set)
    for (player_id, gameweek), row in points.items():
        season[player_id] += row["fpts"]
        if row["started"]:
            started_in[gameweek].add(player_id)

    ranked = sorted(season, key=lambda p: season[p], reverse=True)
    gameweeks = sorted(started_in)
    total_starters = statistics.mean([len(started_in[g]) for g in gameweeks])
    print("real starters per gameweek, on average: %.0f" % total_starters)
    print("players in the Fantrax pool: %d\n" % len(ranked))

    print("%-14s %12s %18s %s" % ("division size", "players owned",
                                  "free starters/GW", ""))
    for teams in (8, 10, 12, 14, 16, 18, 20, 24, 30, 40):
        owned = set(ranked[:teams * ROSTER_SLOTS])
        free = [len(started_in[g] - owned) for g in gameweeks]
        mean_free = statistics.mean(free)
        note = ""
        if mean_free < 5:
            note = "  <- starters have run out"
        elif mean_free < 15:
            note = "  <- getting tight; speed should start to matter"
        print("%-14d %12d %18.1f%s"
              % (teams, len(owned), mean_free, note))

    print("\nThe pilot is 12 teams. Read off where it sits, and where a league")
    print("would have to get to before being first actually mattered.")


def structure():
    print("\n" + RULE)
    print("WHAT THIS MEANS FOR IGL, and a correction")
    print(RULE)
    print("Free agency in Fantrax is per DIVISION. A player rostered in one")
    print("division is still a free agent in every other one - which is exactly")
    print("how baseline_pool.csv is built.")
    print("")
    print("So a 143-team league does NOT have a thinner pool than a 12-team")
    print("one. If IGL is twelve divisions of twelve, its supply depth is the")
    print("pilot's, and the 'speed is worthless' result should carry over")
    print("unchanged. What IGL buys the study is **statistical power** - tens")
    print("of thousands of adds, and the same-player test that needs dozens of")
    print("teams chasing one man - not a different supply regime.")
    print("")
    print("The thing to check the moment the IGL data lands is therefore its")
    print("DIVISION SIZE and roster size, not its team count. That, and")
    print("nothing else, decides whether the pool is deep enough for this")
    print("finding to hold.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label")
    args = parser.parse_args()
    results = speed.build(args.label)
    if not results:
        sys.exit("no adds resolved")
    print("%d adds, each measured against the pool facing it\n" % len(results))
    depth_strata(results)
    depletion(results)
    simulate(args.label)
    structure()


if __name__ == "__main__":
    main()
