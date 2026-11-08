#!/usr/bin/env python3
"""Given that everyone can see the team sheet, where is the actual edge?

    python analysis/edge.py

Two things this study kept reporting are not findings at all, and this script
exists because of that:

  "Pickups spike at KO-75."   Everyone knows lineups drop then. Observing
                              that managers react to them is a description of
                              the hobby, not a result.
  "Sniped players start 98%   Tautological. You make the pickup *because* you
   of the time."              have seen he is starting. The start rate is the
                              definition of the act, not a discovery.

Strip both out and the real decision is what remains. At the moment a manager
acts on the team sheet there are dozens of **confirmed starters** sitting
unowned. Choosing among them is the entire skill, because the "is he playing"
question has already been answered for free.

So: of the confirmed starters available at that exact moment, how good a one
did they take, how much did they leave behind, and what actually separates a
good informed pickup from a bad one?
"""
from __future__ import annotations

import csv
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

PROCESSED = ROOT / "data" / "processed"
RULE = "-" * 78
POS = {"G": "Goalkeepers", "D": "Defenders", "M": "Midfielders", "F": "Forwards"}


def informed(rows):
    """Adds made after the team sheet landed, that produced a starter."""
    return [r for r in rows if r["informed"] and r["started"]]


def left_on_table(rows):
    print(RULE)
    print("THE ONLY DECISION LEFT: which confirmed starter did they take?")
    print(RULE)
    picked = [r for r in informed(rows) if r["captured"] is not None]
    if not picked:
        print("no informed adds")
        return
    actual = statistics.mean([r["pts"] for r in picked])
    best = statistics.mean([r["best_available"] for r in picked
                            if r["best_available"] is not None])
    captured = statistics.mean([r["captured"] for r in picked])
    pool = statistics.mean([r["available"] for r in picked])

    print("%d informed pickups that produced a starter.\n" % len(picked))
    print("  confirmed starters free at that moment : %.0f" % pool)
    print("  what they took                         : %.2f FPts" % actual)
    print("  the best one available                 : %.2f FPts" % best)
    print("  left on the table                      : %.2f FPts" % (best - actual))
    print("  percentile of the pool they captured   : %.1f%%" % captured)
    print("")
    print("50%% is picking at random from the confirmed starters. The team")
    print("sheet answers 'is he playing'. It does not answer 'which of these")
    print("forty-odd players is best', and the evidence is that managers")
    print("barely try to.")


def by_position(rows):
    print("\n" + RULE)
    print("THE LEVER THAT ACTUALLY MOVES: which position you take")
    print(RULE)
    picked = informed(rows)
    print("Among informed pickups that started - so 'he is playing' is held")
    print("constant and only the choice of player varies.\n")
    print("%-14s %7s %10s %10s %11s"
          % ("position", "n", "mean FPts", "median", "captured"))
    means = {}
    for code in ("D", "F", "M", "G"):
        sub = [r for r in picked if r["position"] == code]
        if len(sub) < 20:
            continue
        means[code] = statistics.mean([r["pts"] for r in sub])
        captured = [r["captured"] for r in sub if r["captured"] is not None]
        print("%-14s %7d %10.2f %10.2f %10.1f%%"
              % (POS[code], len(sub), means[code],
                 statistics.median([r["pts"] for r in sub]),
                 statistics.mean(captured) if captured else 0.0))
    if len(means) > 1:
        best = max(means, key=means.get)
        worst = min(means, key=means.get)
        print("\n  %s over %s: %+.2f FPts per pickup"
              % (POS[best], POS[worst], means[best] - means[worst]))
    return means


def versus_speed(rows, means):
    print("\n" + RULE)
    print("HOW THAT COMPARES TO BEING QUICK")
    print(RULE)
    picked = informed(rows)
    fast = [r["pts"] for r in picked if r["window"] == "SNIPE, 70-75"]
    slow = [r["pts"] for r in picked if r["window"] != "SNIPE, 70-75"]
    if not (fast and slow and len(means) > 1):
        return
    speed_gap = statistics.mean(fast) - statistics.mean(slow)
    best, worst = max(means, key=means.get), min(means, key=means.get)
    position_gap = means[best] - means[worst]
    print("  being first after the team sheet   : %+.2f FPts per pickup"
          % speed_gap)
    print("  taking a %s rather than a %s : %+.2f FPts per pickup"
          % (POS[best].lower()[:-1], POS[worst].lower()[:-1], position_gap))
    if speed_gap:
        print("\n  The position choice is worth %.0f times the speed."
              % abs(position_gap / speed_gap))
    print("  Both are available to anyone looking at the same team sheet.")


def home_away(rows, player_gameweeks):
    print("\n" + RULE)
    print("AND WHETHER HE IS AT HOME")
    print(RULE)
    home_of = {}
    for row in player_gameweeks:
        home_of[(row["player_id"], row["gameweek"])] = row["home"]
    picked = informed(rows)
    buckets = defaultdict(list)
    for r in picked:
        flag = home_of.get((r.get("player_id"), r["gameweek"]))
        if flag is not None:
            buckets["home" if flag else "away"].append(r["pts"])
    if not buckets:
        print("  (player ids not carried through; skipped)")
        return
    print("%-10s %8s %11s" % ("venue", "n", "mean FPts"))
    for name in ("home", "away"):
        if buckets[name]:
            print("%-10s %8d %11.2f"
                  % (name, len(buckets[name]), statistics.mean(buckets[name])))


def the_prize(rows, player_gameweeks):
    """How much selection skill is actually on display, and how much is luck.

    The tempting line here is "they left 15 points on the table", taken from
    the best starter available each week. That is **ex-post regret**, not
    achievable alpha: nobody picks the 23-point haul every Saturday, and
    quoting it as a missed opportunity would be dishonest. What can be said
    is how far above a coin-flip pick they landed, and how that compares to
    the one systematic signal in the data.
    """
    print("\n" + RULE)
    print("HOW MUCH SKILL IS ACTUALLY ON DISPLAY?")
    print(RULE)

    picked = [r for r in informed(rows) if r["pool_mean"] is not None]
    if not picked:
        return
    actual = statistics.mean([r["pts"] for r in picked])
    random_pick = statistics.mean([r["pool_mean"] for r in picked])

    # What the whole pool of confirmed starters looked like, to show how much
    # of the outcome is simply variance nobody can forecast.
    spread = []
    for row in player_gameweeks:
        if row["started"]:
            spread.append(row["fpts"])
    spread.sort()

    def pct(fraction):
        return spread[int(fraction * (len(spread) - 1))]

    print("  what informed managers took        : %.2f FPts" % actual)
    print("  a coin flip among confirmed starters: %.2f FPts" % random_pick)
    print("  demonstrated selection skill       : %+.2f FPts per pickup"
          % (actual - random_pick))
    print("")
    print("  for scale, every confirmed starter in the season:")
    print("    median %.2f, p75 %.2f, p90 %.2f, p99 %.2f"
          % (pct(0.5), pct(0.75), pct(0.90), pct(0.99)))
    print("")
    print("The pool is wildly right-skewed, so the gap between a median")
    print("starter and a great one is mostly variance nobody can forecast.")
    print("Which is the point: with the 'is he playing' question answered for")
    print("free, there is very little skill left to exercise - and what little")
    print("edge exists is systematic, not a read on the week.")


def main():
    player_gameweeks = ref.load_player_gameweeks()
    rows = []
    for label in ("igl", "community"):
        rows += [dict(r, league=label) for r in speed.build(label)]
    print("%d pickups resolved against the pool facing them\n" % len(rows))

    left_on_table(rows)
    means = by_position(rows)
    versus_speed(rows, means)
    home_away(rows, player_gameweeks)
    the_prize(rows, player_gameweeks)


if __name__ == "__main__":
    main()
