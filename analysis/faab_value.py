#!/usr/bin/env python3
"""How many fantasy points does one FAAB dollar actually buy, and when?

    python analysis/faab_value.py

The hypothesis: value per dollar should rise as the season goes on. Budgets
drain, so there is less money chasing each player, bids fall and the same
dollar wins more often. If that holds, holding budget back is worth real
points and spending early is a tax.

Two returns are measured, because they answer different questions:

    gameweek points   what the claim delivered in the week it was made - the
                      return on a one-week rental
    points while held everything he scored for that team before being
                      dropped - the return on the acquisition

Only winning claims cost anything, so the spend is the sum of winning bids.
Losing bids are free, which is exactly why the market behaves as it does.
"""
from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scraper"))

import reference as ref  # noqa: E402
import events as ev  # noqa: E402
import faab  # noqa: E402

RULE = "-" * 78
PHASES = (("GW1-12", 1, 12), ("GW13-25", 13, 25), ("GW26-38", 26, 38))
BLOCKS = (("GW1-6", 1, 6), ("GW7-12", 7, 12), ("GW13-19", 13, 19),
          ("GW20-25", 20, 25), ("GW26-32", 26, 32), ("GW33-38", 33, 38))


def load():
    player_gameweeks = ref.load_player_gameweeks()
    fixtures = ref.load_fixtures(player_gameweeks)
    points = ref.points_index(player_gameweeks)
    by_player = ev.player_fixtures(player_gameweeks, fixtures)
    claims = faab.load_claims(None, points, by_player, None)
    faab.attach_holds(claims, None)
    return claims


def table(claims, bands, title):
    print("\n%s" % title)
    print("%-10s %8s %9s %9s %10s %12s %13s"
          % ("phase", "claims", "mean bid", "win rate", "spent",
             "FPts/$ (GW)", "FPts/$ (held)"))
    rows = []
    for name, low, high in bands:
        sub = [c for c in claims if low <= c["gameweek"] <= high]
        if not sub:
            continue
        won = [c for c in sub if c["won"]]
        spend = sum(c["bid"] for c in won)
        gw_points = sum(c["pts"] for c in won if c["pts"] is not None)
        held_points = sum(c["held_fpts"] for c in won
                          if c.get("held_fpts") is not None)
        per_gw = gw_points / spend if spend else None
        per_held = held_points / spend if spend else None
        rows.append((name, per_gw, per_held))
        print("%-10s %8d %9.2f %8.1f%% %10.0f %12s %13s"
              % (name, len(sub), statistics.mean([c["bid"] for c in sub]),
                 100.0 * len(won) / len(sub), spend,
                 "%.2f" % per_gw if per_gw else "--",
                 "%.2f" % per_held if per_held else "--"))
    return rows


def paid_only(claims):
    print("\n" + RULE)
    print("AMONG CLAIMS THAT ACTUALLY COST SOMETHING")
    print(RULE)
    print("Three quarters of claims are $0, and they drag every average")
    print("towards infinity. This is the paying market on its own.\n")
    paid = [c for c in claims if c["bid"] > 0]
    print("%-10s %8s %9s %9s %12s %13s"
          % ("phase", "claims", "mean bid", "win rate", "FPts/$ (GW)",
             "FPts/$ (held)"))
    for name, low, high in PHASES:
        sub = [c for c in paid if low <= c["gameweek"] <= high]
        if not sub:
            continue
        won = [c for c in sub if c["won"]]
        spend = sum(c["bid"] for c in won)
        if not spend:
            continue
        gw_points = sum(c["pts"] for c in won if c["pts"] is not None)
        held = sum(c["held_fpts"] for c in won
                   if c.get("held_fpts") is not None)
        print("%-10s %8d %9.2f %8.1f%% %12.2f %13.2f"
              % (name, len(sub), statistics.mean([c["bid"] for c in sub]),
                 100.0 * len(won) / len(sub), gw_points / spend, held / spend))


def cost_of_winning(claims):
    print("\n" + RULE)
    print("WHAT IT COSTS TO WIN A CONTESTED CLAIM, OVER THE SEASON")
    print(RULE)
    print("Within a division, among player-gameweeks that drew more than one")
    print("claim - the only place money is actually competing.\n")
    groups = defaultdict(list)
    for claim in claims:
        groups[(claim["division"], claim["player_id"],
                claim["gameweek"])].append(claim)
    print("%-10s %12s %14s %15s"
          % ("phase", "auctions", "winning bid", "won on $0"))
    for name, low, high in PHASES:
        picked = [v for k, v in groups.items()
                  if len(v) > 1 and low <= v[0]["gameweek"] <= high]
        winners = [c for rows in picked for c in rows if c["won"]]
        if not winners:
            continue
        free = sum(1 for c in winners if c["bid"] <= 0)
        print("%-10s %12d %14.2f %14.1f%%"
              % (name, len(picked), statistics.mean([c["bid"] for c in winners]),
                 100.0 * free / len(winners)))


def main():
    claims = load()
    print("%d waiver claims\n" % len(claims))
    print(RULE)
    print("HOW MUCH DOES ONE FAAB DOLLAR BUY, AND WHEN?")
    print(RULE)
    print("Spend is the sum of winning bids; losing bids cost nothing.")
    table(claims, PHASES, "by third of the season")
    table(claims, BLOCKS, "in finer blocks")
    paid_only(claims)
    cost_of_winning(claims)
    print("\n" + RULE)
    print("Read the FPts/$ columns as a price. A rising number means the same")
    print("dollar is buying more as the season goes on, which would make")
    print("holding budget back worth real points.")


if __name__ == "__main__":
    main()
