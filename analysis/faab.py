#!/usr/bin/env python3
"""Does paying more on waivers get you more?

    python analysis/faab.py --label igl

The first pass at this said no - bid and points that gameweek correlated at
-0.079, and the top bid quartile returned 5.10 points against 6.99 for the
cheapest. Before that becomes an article, it has to survive four objections,
and the first two are serious enough that the first pass was simply wrong:

1. **The $0 bids were missing.** Every waiver claim carries a bid, and 626 of
   996 of them are $0.00 - claims made on waiver priority rather than money.
   Quartiles computed over bids above zero threw away the biggest group in the
   data, and the cheapest tier with it.

2. **One gameweek is the wrong horizon.** If you pay real money for a player
   you intend to keep him. Judging a $14 claim on the points he scored in the
   single gameweek after the claim is close to the least fair test available.
   So every tier is measured on four horizons, including how long he was
   actually held.

3. **Position mix could explain all of it.** Forwards start 43% of the time and
   goalkeepers 93%. If expensive bids skew to forwards, the start-rate gap is
   a position artefact and nothing to do with money.

4. **Only winners have outcomes.** 556 claims failed. Ignoring them measures
   what the market got, never what it paid, and the price of *winning* is the
   whole question - a bid only costs you anything if it succeeds.

Everything here is within waiver claims (`WW`) only, so the waiver-versus-snipe
information gap cannot contaminate the bid gradient.
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scraper"))

import reference as ref  # noqa: E402
import events as ev  # noqa: E402

PROCESSED = ROOT / "data" / "processed"
RULE = "-" * 76

# Tiers rather than quartiles: $0 is a different act from a $1 bid, and the
# lumpy distribution (626 zeros, then a long tail to $101) does not split into
# four equal groups that mean anything.
TIERS = [("$0 (priority)", 0, 0.001), ("$1", 0.001, 1.01), ("$2-3", 1.01, 3.01),
         ("$4-9", 3.01, 9.01), ("$10-24", 9.01, 24.01), ("$25+", 24.01, 1e9)]


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def tier_of(bid):
    for name, low, high in TIERS:
        if low <= bid < high:
            return name
    return TIERS[-1][0]


def correlation(xs, ys):
    if len(xs) < 3:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    top = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    bottom = math.sqrt(sum((x - mx) ** 2 for x in xs)
                       * sum((y - my) ** 2 for y in ys))
    return top / bottom if bottom else None


def spearman(xs, ys):
    """Rank correlation - the bid distribution is far too skewed for Pearson
    to mean much on its own ($0 to $101 with 626 zeros)."""
    def ranks(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        index = 0
        while index < len(order):
            stop = index
            while (stop + 1 < len(order)
                   and values[order[stop + 1]] == values[order[index]]):
                stop += 1
            mean_rank = (index + stop) / 2.0 + 1
            for position in range(index, stop + 1):
                out[order[position]] = mean_rank
            index = stop + 1
        return out
    return correlation(ranks(xs), ranks(ys))


# --------------------------------------------------------------------------- #

def load_claims(label, points, by_player, periods):
    """Every waiver claim, won or lost, with the gameweek it was aimed at."""
    path = PROCESSED / "transactions.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [r for r in csv.DictReader(handle)
                if (not label or r["league_label"] == label)
                and r["transaction_code"] == "CLAIM"
                and r["claim_type"] == "WW"]

    claims = []
    for row in rows:
        bid = number(row["bid"])
        if bid is None or not row["stamp_utc"] or not row["player_id"]:
            continue
        added = datetime.fromisoformat(row["stamp_utc"])
        matches = by_player.get(row["player_id"])
        if not matches:
            continue
        target = ev.next_fixture(matches, added)
        if not target:
            continue
        gameweek = target["gameweek"]
        scored = points.get((row["player_id"], gameweek))

        nxt = [points.get((row["player_id"], gameweek + step)) for step in range(4)]
        nxt = [s for s in nxt if s]

        claims.append({
            "league": row["league_label"],
            "division": row["division"],
            "player_id": row["player_id"],
            "player": row["player_name"],
            "team_id": row["team_id"],
            "bid": bid,
            "tier": tier_of(bid),
            "won": row["result_code"] == "EXECUTED",
            "added": added,
            "gameweek": gameweek,
            "position": (scored or {}).get("position") or row["player_pos"] or "?",
            "pts": scored["fpts"] if scored else None,
            "started": bool(scored and scored["started"]),
            "next4": sum(s["fpts"] for s in nxt) if nxt else None,
        })
    return claims


def attach_holds(claims, label):
    """How long the winner actually kept him, from the event table."""
    path = PROCESSED / "pickup_events.csv"
    if not path.exists():
        return
    held = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if label and row["league_label"] != label:
                continue
            key = (row["player_id"], row["team_id"], row["target_gameweek"])
            held[key] = (number(row["held_gameweeks"]), number(row["held_fpts"]),
                         number(row.get("held_starts")),
                         number(row.get("held_start_fpts")),
                         number(row.get("gw_to_first_start")))
    for claim in claims:
        key = (claim["player_id"], claim["team_id"], str(claim["gameweek"]))
        (claim["held_gameweeks"], claim["held_fpts"], claim["held_starts"],
         claim["held_start_fpts"], claim["gw_to_first_start"]) = held.get(
            key, (None, None, None, None, None))


# --------------------------------------------------------------------------- #

def market(claims):
    print(RULE)
    print("THE MARKET: what a bid buys you is a CHANCE, and that is the cost")
    print(RULE)
    print("%d waiver claims. Every one carries a bid; most carry $0.\n" % len(claims))
    print("%-15s %6s %7s %9s | %8s" % ("tier", "n", "won", "win rate", "mean bid"))
    for name, _, _ in TIERS:
        rows = [c for c in claims if c["tier"] == name]
        if not rows:
            continue
        won = sum(1 for c in rows if c["won"])
        print("%-15s %6d %7d %8.1f%% | %8.2f"
              % (name, len(rows), won, 100.0 * won / len(rows),
                 statistics.mean([c["bid"] for c in rows])))
    print("\nThis is the part the first pass missed entirely. A bid is a price")
    print("paid for probability, so the gradient here is the real product.")


def horizons(claims):
    print("\n" + RULE)
    print("WHAT THE WINNERS GOT, on four horizons")
    print(RULE)
    print("The one-gameweek column is the one that looked damning. The point of")
    print("a big bid is usually not one gameweek.\n")
    print("%-15s %6s %8s %9s %9s %8s %9s %9s"
          % ("tier", "n", "start%", "GW FPts", "next4", "held GW", "held FPts",
             "per GW held"))
    for name, _, _ in TIERS:
        rows = [c for c in claims
                if c["tier"] == name and c["won"] and c["pts"] is not None]
        if not rows:
            continue
        starts = sum(1 for c in rows if c["started"])
        next4 = [c["next4"] for c in rows if c["next4"] is not None]
        held_gw = [c.get("held_gameweeks") for c in rows
                   if c.get("held_gameweeks") is not None]
        held_pts = [c.get("held_fpts") for c in rows
                    if c.get("held_fpts") is not None]
        # Points while held grows mechanically with how long he was held, so
        # the per-gameweek rate is the only version of it that says anything
        # about the player rather than about the length of the spell.
        rates = [c["held_fpts"] / c["held_gameweeks"] for c in rows
                 if c.get("held_gameweeks") and c.get("held_fpts") is not None]
        print("%-15s %6d %7.1f%% %9.2f %9.2f %8.1f %9.2f %9.2f"
              % (name, len(rows), 100.0 * starts / len(rows),
                 statistics.mean([c["pts"] for c in rows]),
                 statistics.mean(next4) if next4 else 0.0,
                 statistics.mean(held_gw) if held_gw else 0.0,
                 statistics.mean(held_pts) if held_pts else 0.0,
                 statistics.mean(rates) if rates else 0.0))

    winners = [c for c in claims if c["won"] and c["pts"] is not None]
    print()
    for claim in winners:
        if claim.get("held_gameweeks") and claim.get("held_fpts") is not None:
            claim["per_gw"] = claim["held_fpts"] / claim["held_gameweeks"]
    for label, key in (("points that gameweek", "pts"),
                       ("points over 4 gameweeks", "next4"),
                       ("gameweeks held", "held_gameweeks"),
                       ("points while held (grows with length!)", "held_fpts"),
                       ("points PER gameweek held", "per_gw")):
        pairs = [(c["bid"], c.get(key)) for c in winners if c.get(key) is not None]
        if len(pairs) < 10:
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        print("  bid vs %-38s Pearson %+.3f  Spearman %+.3f  (n=%d)"
              % (label, correlation(xs, ys) or 0.0, spearman(xs, ys) or 0.0,
                 len(pairs)))
    print("\nNote on 'gameweeks held': it is partly endogenous. A manager who")
    print("paid $25 is committed to the player by the price, so hold length")
    print("measures sunk cost as much as merit. The per-gameweek rate is the")
    print("honest column.")


def confounds(claims):
    print("\n" + RULE)
    print("OBJECTION: is it just position mix?")
    print(RULE)
    print("Forwards start 43% of the time, keepers 93%. If the money chases")
    print("forwards, the start-rate gap is a position artefact.\n")
    winners = [c for c in claims if c["won"]]
    print("%-15s %6s %s" % ("tier", "n", "position mix of winning claims"))
    for name, _, _ in TIERS:
        rows = [c for c in winners if c["tier"] == name]
        if not rows:
            continue
        mix = Counter(c["position"].split(",")[0] for c in rows)
        share = ", ".join("%s %.0f%%" % (pos, 100.0 * count / len(rows))
                          for pos, count in mix.most_common())
        print("%-15s %6d %s" % (name, len(rows), share))

    print("\nand the same gradient held WITHIN each position:")
    print("%-4s %-15s %6s %8s %9s" % ("pos", "tier", "n", "start%", "GW FPts"))
    for position in ("D", "M", "F", "G"):
        rows_all = [c for c in winners
                    if c["position"].split(",")[0] == position
                    and c["pts"] is not None]
        if len(rows_all) < 20:
            continue
        cheap = [c for c in rows_all if c["bid"] <= 1.0]
        dear = [c for c in rows_all if c["bid"] > 3.0]
        for name, rows in (("cheap, $0-1", cheap), ("dear, $4+", dear)):
            if len(rows) < 5:
                continue
            starts = sum(1 for c in rows if c["started"])
            print("%-4s %-15s %6d %7.1f%% %9.2f"
                  % (position, name, len(rows), 100.0 * starts / len(rows),
                     statistics.mean([c["pts"] for c in rows])))

    print("\nOBJECTION: is it budget depletion over the season?")
    thirds = (("GW1-12", 1, 12), ("GW13-25", 13, 25), ("GW26-38", 26, 38))
    print("%-10s %6s %9s %9s %8s" % ("phase", "n", "mean bid", "win rate", "GW FPts"))
    for name, low, high in thirds:
        rows = [c for c in claims if low <= c["gameweek"] <= high]
        if not rows:
            continue
        won = [c for c in rows if c["won"] and c["pts"] is not None]
        print("%-10s %6d %9.2f %8.1f%% %8.2f"
              % (name, len(rows), statistics.mean([c["bid"] for c in rows]),
                 100.0 * sum(1 for c in rows if c["won"]) / len(rows),
                 statistics.mean([c["pts"] for c in won]) if won else 0.0))


def auctions(claims):
    print("\n" + RULE)
    print("CONTESTED AUCTIONS - within a division, which is the only place")
    print("two claims actually compete")
    print(RULE)
    print("Free agency is per division, so two managers in different")
    print("divisions bidding for the same player are not in the same auction")
    print("at all. Grouping on player-gameweek alone pools 18 separate")
    print("auctions into one and invents competition that never happened.")
    print("The key here is (division, player, gameweek).\n")

    groups = defaultdict(list)
    for claim in claims:
        groups[(claim["division"], claim["player_id"],
                claim["gameweek"])].append(claim)

    contested = {k: v for k, v in groups.items() if len(v) > 1}
    print("%d division-player-gameweeks drew more than one claim, out of %d."
          % (len(contested), len(groups)))

    margins, upsets = [], 0
    for rows in contested.values():
        winners = [c for c in rows if c["won"]]
        losers = [c for c in rows if not c["won"]]
        if len(winners) != 1 or not losers:
            continue
        winner = winners[0]
        best_loser = max(c["bid"] for c in losers)
        margins.append(winner["bid"] - best_loser)
        if winner["bid"] < best_loser:
            upsets += 1
    if margins:
        print("\nwinning margin over the best losing bid:")
        print("  mean %.2f, median %.2f" % (statistics.mean(margins),
                                            statistics.median(margins)))
        print("  won with a $0 bid:        %.1f%%"
              % (100.0 * sum(1 for m in margins if m <= 0) / len(margins)))
        print("  won while bidding LESS:   %.1f%%  (%d of %d)"
              % (100.0 * upsets / len(margins), upsets, len(margins)))
        print("\nIf the top bid always won, that last line would be 0%. Where")
        print("it is not, waiver priority is deciding the auction, not money.")


def consensus(claims):
    """How many independent divisions wanted the same player that week.

    This is what 18 divisions buy that one league cannot. Each division is a
    separate market with its own managers, so the number of them chasing one
    player-gameweek is a genuine wisdom-of-crowds measure rather than a count
    of rivals in a single auction.
    """
    print("\n" + RULE)
    print("CROSS-DIVISION CONSENSUS: does the crowd know something?")
    print(RULE)
    print("18 divisions decide independently. Counting how many of them went")
    print("after the same player in the same gameweek is a real consensus")
    print("signal - and one the pilot could not produce at all.\n")

    wanted = defaultdict(set)
    outcome = {}
    for claim in claims:
        key = (claim["player_id"], claim["gameweek"])
        wanted[key].add(claim["division"])
        if claim["pts"] is not None:
            outcome[key] = (claim["pts"], claim["started"])

    bands = (("1 division", 1, 1), ("2-3", 2, 3), ("4-6", 4, 6),
             ("7-11", 7, 11), ("12+", 12, 99))
    print("%-14s %8s %10s %10s %10s"
          % ("divisions", "n", "mean FPts", "median", "start%"))
    for name, low, high in bands:
        rows = [outcome[k] for k, divs in wanted.items()
                if low <= len(divs) <= high and k in outcome]
        if len(rows) < 5:
            continue
        points = [r[0] for r in rows]
        print("%-14s %8d %10.2f %10.2f %9.1f%%"
              % (name, len(rows), statistics.mean(points),
                 statistics.median(points),
                 100.0 * sum(1 for r in rows if r[1]) / len(rows)))
    print("\nA rising column means independent divisions converging on the")
    print("same player is a real signal, not noise.")


def verdict(claims):
    print("\n" + RULE)
    print("VERDICT")
    print(RULE)
    winners = [c for c in claims if c["won"] and c["pts"] is not None]
    free = [c for c in winners if c["bid"] <= 0.001]
    paid = [c for c in winners if c["bid"] > 0.001]
    if free and paid:
        print("free claims   n=%-4d start%%=%.1f  GW FPts=%.2f"
              % (len(free), 100.0 * sum(1 for c in free if c["started"]) / len(free),
                 statistics.mean([c["pts"] for c in free])))
        print("paid claims   n=%-4d start%%=%.1f  GW FPts=%.2f"
              % (len(paid), 100.0 * sum(1 for c in paid if c["started"]) / len(paid),
                 statistics.mean([c["pts"] for c in paid])))
    big = [c for c in winners if c["bid"] >= 10]
    if big:
        print("$10+ claims    n=%-4d start%%=%.1f  GW FPts=%.2f  held=%.1f GW"
              % (len(big), 100.0 * sum(1 for c in big if c["started"]) / len(big),
                 statistics.mean([c["pts"] for c in big]),
                 statistics.mean([c["held_gameweeks"] for c in big
                                  if c.get("held_gameweeks") is not None] or [0])))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label")
    args = parser.parse_args()

    player_gameweeks = ref.load_player_gameweeks()
    fixtures = ref.load_fixtures(player_gameweeks)
    points = ref.points_index(player_gameweeks)
    by_player = ev.player_fixtures(player_gameweeks, fixtures)

    claims = load_claims(args.label, points, by_player, None)
    attach_holds(claims, args.label)
    if not claims:
        sys.exit("no waiver claims with bids found")

    market(claims)
    horizons(claims)
    confounds(claims)
    auctions(claims)
    consensus(claims)
    verdict(claims)


if __name__ == "__main__":
    main()
