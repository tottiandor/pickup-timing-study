#!/usr/bin/env python3
"""Everything the site needs that metrics.py does not already emit.

    python analysis/build_site_data.py

Writes site/data/extra.js (window.PICKUP_EXTRA) with the FAAB tiers, the
cross-division consensus bands, the post-announcement timing windows and the
outcome distributions.

Kept separate from metrics.py because the speed block has to walk ownership
forward to every add's own timestamp across 18 divisions, which is slow, and
there is no reason to pay for that every time the headline tables are rebuilt.

Emitted as a .js assigning a global rather than .json, for the same reason
metrics.py does: fetch() is blocked on file:// URLs, so a fetch-based page
cannot be opened locally or pasted into a Wix HTML block.
"""
from __future__ import annotations

import csv
import json
import math
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
import faab  # noqa: E402
import speed  # noqa: E402

PROCESSED = ROOT / "data" / "processed"
SITE_DATA = ROOT / "site" / "data"
BUCKETS = ("waivers", "lineup_snipe", "late_add")


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def wilson(hits, total):
    if not total:
        return (0.0, 0.0)
    z = 1.96
    phat = hits / total
    denom = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denom
    spread = z * math.sqrt(phat * (1 - phat) / total
                           + z * z / (4 * total * total)) / denom
    return (round(max(0.0, centre - spread) * 100, 1),
            round(min(1.0, centre + spread) * 100, 1))


def quantile(values, fraction):
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    low = int(math.floor(position))
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


# --------------------------------------------------------------------------- #

def distributions():
    """Spread of outcomes per bucket, which the means hide."""
    rows = []
    with (PROCESSED / "pickup_events.csv").open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            points = number(row["gw_fpts"])
            if points is None:
                continue
            rows.append((row["bucket"], points, row["gw_started"] == "True"))

    out = {}
    for bucket in BUCKETS:
        picked = [r for r in rows if r[0] == bucket]
        if not picked:
            continue
        points = [r[1] for r in picked]
        starts = sum(1 for r in picked if r[2])
        low, high = wilson(starts, len(picked))
        out[bucket] = {
            "n": len(picked),
            "start_rate": round(100.0 * starts / len(picked), 1),
            "start_ci": [low, high],
            "p25": round(quantile(points, 0.25), 2),
            "median": round(statistics.median(points), 2),
            "mean": round(statistics.mean(points), 2),
            "p75": round(quantile(points, 0.75), 2),
            "p90": round(quantile(points, 0.90), 2),
            "bust_rate": round(100.0 * sum(1 for p in points if p < 2) / len(points), 1),
            "ten_plus": round(100.0 * sum(1 for p in points if p >= 10) / len(points), 1),
            "twenty_plus": round(100.0 * sum(1 for p in points if p >= 20) / len(points), 1),
            "mean_if_started": round(
                statistics.mean([r[1] for r in picked if r[2]]), 2)
            if starts else None,
        }
    return out


def faab_blocks():
    player_gameweeks = ref.load_player_gameweeks()
    fixtures = ref.load_fixtures(player_gameweeks)
    points = ref.points_index(player_gameweeks)
    by_player = ev.player_fixtures(player_gameweeks, fixtures)

    claims = faab.load_claims(None, points, by_player, None)
    faab.attach_holds(claims, None)

    tiers = []
    for name, _, _ in faab.TIERS:
        rows = [c for c in claims if c["tier"] == name]
        if not rows:
            continue
        won = [c for c in rows if c["won"] and c["pts"] is not None]
        rates = [c["held_fpts"] / c["held_gameweeks"] for c in won
                 if c.get("held_gameweeks") and c.get("held_fpts") is not None]
        # The stash case: an expensive claim is often a player who will not
        # play for weeks. Judging it on the gameweek after the claim says
        # nothing, so the lag, the eventual-dud rate and PPS are carried too.
        lags = [c["gw_to_first_start"] for c in won
                if c.get("gw_to_first_start") is not None]
        pps = [c["held_start_fpts"] / c["held_starts"] for c in won
               if c.get("held_starts") and c.get("held_start_fpts") is not None]
        with_holds = [c for c in won if c.get("held_starts") is not None]
        never = sum(1 for c in with_holds if not c["held_starts"])
        tiers.append({
            "tier": name,
            "claims": len(rows),
            "win_rate": round(100.0 * sum(1 for c in rows if c["won"]) / len(rows), 1),
            "mean_bid": round(statistics.mean([c["bid"] for c in rows]), 2),
            "n_won": len(won),
            "start_rate": round(100.0 * sum(1 for c in won if c["started"])
                                / len(won), 1) if won else None,
            "mean_fpts": round(statistics.mean([c["pts"] for c in won]), 2)
            if won else None,
            "held_gameweeks": round(statistics.mean(
                [c["held_gameweeks"] for c in won
                 if c.get("held_gameweeks") is not None]), 1) if won else None,
            "held_fpts": round(statistics.mean(
                [c["held_fpts"] for c in won
                 if c.get("held_fpts") is not None]), 1) if won else None,
            "per_gameweek_held": round(statistics.mean(rates), 2) if rates else None,
            "lag": round(statistics.mean(lags), 2) if lags else None,
            "never_started": round(100.0 * never / len(with_holds), 1)
            if with_holds else None,
            "pps": round(statistics.mean(pps), 2) if pps else None,
        })

    # Consensus: how many independent divisions chased the same player-gameweek.
    wanted, outcome = defaultdict(set), {}
    for claim in claims:
        key = (claim["player_id"], claim["gameweek"])
        wanted[key].add(claim["division"])
        if claim["pts"] is not None:
            outcome[key] = (claim["pts"], claim["started"])

    consensus = []
    for name, low, high in (("1", 1, 1), ("2-3", 2, 3), ("4-6", 4, 6),
                            ("7-11", 7, 11), ("12+", 12, 99)):
        rows = [outcome[k] for k, divs in wanted.items()
                if low <= len(divs) <= high and k in outcome]
        if len(rows) < 5:
            continue
        consensus.append({
            "band": name,
            "n": len(rows),
            "mean_fpts": round(statistics.mean([r[0] for r in rows]), 2),
            "median_fpts": round(statistics.median([r[0] for r in rows]), 2),
            "start_rate": round(100.0 * sum(1 for r in rows if r[1]) / len(rows), 1),
        })

    # What a dollar buys, and when. Budgets drain over a season, so the same
    # dollar meets less competition in April than in August.
    value = []
    for name, low, high in (("GW1-6", 1, 6), ("GW7-12", 7, 12),
                            ("GW13-19", 13, 19), ("GW20-25", 20, 25),
                            ("GW26-32", 26, 32), ("GW33-38", 33, 38)):
        sub = [c for c in claims if low <= c["gameweek"] <= high]
        won = [c for c in sub if c["won"]]
        spend = sum(c["bid"] for c in won)
        if not (sub and spend):
            continue
        gw_points = sum(c["pts"] for c in won if c["pts"] is not None)
        held = sum(c["held_fpts"] for c in won
                   if c.get("held_fpts") is not None)
        # PPS decomposition. held_per_dollar rewards simply owning somebody a
        # long time, however badly he played - a late claim has fewer weeks
        # left to accumulate, so the measure punishes it for the calendar
        # rather than for the player. Splitting it into starts bought per
        # dollar and points per start separates "more football" from "better
        # football".
        starts = sum(c["held_starts"] for c in won
                     if c.get("held_starts") is not None)
        start_points = sum(c["held_start_fpts"] for c in won
                           if c.get("held_start_fpts") is not None)
        value.append({
            "phase": name,
            "claims": len(sub),
            "mean_bid": round(statistics.mean([c["bid"] for c in sub]), 2),
            "win_rate": round(100.0 * len(won) / len(sub), 1),
            "spent": round(spend),
            "fpts_per_dollar": round(gw_points / spend, 2),
            "held_per_dollar": round(held / spend, 2),
            "starts_per_dollar": round(starts / spend, 2),
            "pps": round(start_points / starts, 2) if starts else None,
            "mean_held_gameweeks": round(statistics.mean(
                [c["held_gameweeks"] for c in won
                 if c.get("held_gameweeks") is not None]), 1) if won else None,
        })

    # The price of a contested player, within a division, over the season.
    groups = defaultdict(list)
    for claim in claims:
        groups[(claim["division"], claim["player_id"],
                claim["gameweek"])].append(claim)
    price = []
    for name, low, high in (("GW1-12", 1, 12), ("GW13-25", 13, 25),
                            ("GW26-38", 26, 38)):
        picked = [v for v in groups.values()
                  if len(v) > 1 and low <= v[0]["gameweek"] <= high]
        winners = [c for rows in picked for c in rows if c["won"]]
        if not winners:
            continue
        price.append({
            "phase": name,
            "auctions": len(picked),
            "winning_bid": round(statistics.mean([c["bid"] for c in winners]), 2),
            "won_free": round(100.0 * sum(1 for c in winners if c["bid"] <= 0)
                              / len(winners), 1),
        })

    return {"tiers": tiers, "consensus": consensus, "value": value,
            "price": price,
            "claims_with_bid": sum(1 for c in claims if c["bid"] > 0),
            "claims_total": len(claims)}


def speed_rows():
    """The per-add records, walked once. Both the speed and edge blocks read
    them, and rebuilding ownership across 18 divisions twice would double the
    slowest part of this script for nothing."""
    rows = []
    for label in ("igl", "community"):
        rows += [dict(r, league=label) for r in speed.build(label)]
    return rows


def speed_blocks(rows):
    windows = []
    for name, _, _, informed in speed.WINDOWS:
        picked = [r for r in rows if r["window"] == name and r["started"]
                  and r["captured"] is not None]
        if not picked:
            continue
        windows.append({
            "window": name,
            "informed": informed,
            "n": len(picked),
            "mean_fpts": round(statistics.mean([r["pts"] for r in picked]), 2),
            "captured": round(statistics.mean([r["captured"] for r in picked]), 1),
            "available": round(statistics.mean([r["available"] for r in picked]), 1),
            "best_available": round(statistics.mean(
                [r["best_available"] for r in picked
                 if r["best_available"] is not None]), 2),
        })

    per_league = []
    for label in ("igl", "community", "ALL"):
        sub = [r for r in rows if r["informed"] and r["started"]
               and (label == "ALL" or r["league"] == label)]
        fast = [r["pts"] for r in sub if r["window"] == "SNIPE, 70-75"]
        slow = [r["pts"] for r in sub if r["window"] != "SNIPE, 70-75"]
        if len(fast) < 3:
            continue
        delta = statistics.mean(fast) - statistics.mean(slow)
        error = math.sqrt(statistics.variance(fast) / len(fast)
                          + statistics.variance(slow) / len(slow))
        per_league.append({
            "league": label,
            "n_snipe": len(fast),
            "n_later": len(slow),
            "snipe": round(statistics.mean(fast), 2),
            "later": round(statistics.mean(slow), 2),
            "delta": round(delta, 2),
            "error": round(error, 2),
            "sigma": round(abs(delta / error), 1) if error else 0,
        })

    informed = [r for r in rows if r["informed"]]
    blind = [r for r in rows if not r["informed"]]
    information = {
        "blind": {"n": len(blind),
                  "start_rate": round(100.0 * sum(1 for r in blind if r["started"])
                                      / len(blind), 1),
                  "mean_fpts": round(statistics.mean([r["pts"] for r in blind]), 2)},
        "informed": {"n": len(informed),
                     "start_rate": round(100.0 * sum(1 for r in informed if r["started"])
                                         / len(informed), 1),
                     "mean_fpts": round(statistics.mean([r["pts"] for r in informed]), 2)},
    }
    return {"windows": windows, "per_league": per_league,
            "information": information}


def edge_blocks(rows, player_gameweeks):
    """What is left to decide once the team sheet has answered 'is he playing'.

    The study's first framing leaned on two non-findings: that pickups spike
    when lineups drop (everybody knows they do) and that sniped players start
    98% of the time (tautological - you make the pickup *because* you have
    seen he is starting). Strip both out and this is what remains.
    """
    picked = [r for r in rows if r["informed"] and r["started"]
              and r["pool_mean"] is not None]
    if not picked:
        return None

    actual = statistics.mean([r["pts"] for r in picked])
    coin_flip = statistics.mean([r["pool_mean"] for r in picked])

    positions = []
    for code in ("D", "G", "F", "M"):
        sub = [r for r in picked if r["position"] == code]
        if len(sub) < 20:
            continue
        captured = [r["captured"] for r in sub if r["captured"] is not None]
        positions.append({
            "position": code,
            "n": len(sub),
            "mean_fpts": round(statistics.mean([r["pts"] for r in sub]), 2),
            "median_fpts": round(statistics.median([r["pts"] for r in sub]), 2),
            "captured": round(statistics.mean(captured), 1) if captured else None,
        })
    ranked = sorted(positions, key=lambda p: p["mean_fpts"])
    position_gap = (ranked[-1]["mean_fpts"] - ranked[0]["mean_fpts"]
                    if len(ranked) > 1 else None)

    fast = [r["pts"] for r in picked if r["window"] == "SNIPE, 70-75"]
    slow = [r["pts"] for r in picked if r["window"] != "SNIPE, 70-75"]
    speed_gap = (statistics.mean(fast) - statistics.mean(slow)
                 if fast and slow else None)

    home_of = {(r["player_id"], r["gameweek"]): r["home"]
               for r in player_gameweeks}
    venues = {"home": [], "away": []}
    for row in picked:
        flag = home_of.get((row.get("player_id"), row["gameweek"]))
        if flag is not None:
            venues["home" if flag else "away"].append(row["pts"])

    starters = sorted(r["fpts"] for r in player_gameweeks if r["started"])

    def at(fraction):
        return round(starters[int(fraction * (len(starters) - 1))], 2)

    return {
        "n": len(picked),
        "pool_size": round(statistics.mean([r["available"] for r in picked]), 0),
        "actual": round(actual, 2),
        "coin_flip": round(coin_flip, 2),
        "skill": round(actual - coin_flip, 2),
        "captured": round(statistics.mean(
            [r["captured"] for r in picked if r["captured"] is not None]), 1),
        "best_available": round(statistics.mean(
            [r["best_available"] for r in picked
             if r["best_available"] is not None]), 2),
        "positions": positions,
        "position_gap": round(position_gap, 2) if position_gap else None,
        "best_position": ranked[-1]["position"] if ranked else None,
        "worst_position": ranked[0]["position"] if ranked else None,
        "speed_gap": round(speed_gap, 2) if speed_gap else None,
        "home": round(statistics.mean(venues["home"]), 2) if venues["home"] else None,
        "away": round(statistics.mean(venues["away"]), 2) if venues["away"] else None,
        "starter_spread": {"median": at(0.5), "p75": at(0.75),
                           "p90": at(0.90), "p99": at(0.99)},
    }


def main():
    print("distributions...", flush=True)
    payload = {"distributions": distributions()}
    print("faab and consensus...", flush=True)
    payload["faab"] = faab_blocks()
    print("speed (walks ownership to every add, slow)...", flush=True)
    rows = speed_rows()
    payload["speed"] = speed_blocks(rows)
    print("the edge...", flush=True)
    payload["edge"] = edge_blocks(rows, ref.load_player_gameweeks())

    # Positional scarcity, which decides whether the position effect is real.
    print("positional scarcity (second ownership walk)...", flush=True)
    import scarcity
    scarcity_rows = scarcity.build()
    positions = []
    for code in ("G", "D", "M", "F"):
        sub = [r for r in scarcity_rows if r["position"] == code
               and r["pool_start_rate"] is not None]
        started = [r for r in scarcity_rows if r["position"] == code
                   and r["started"] and r["within_pos_pctile"] is not None]
        if not sub:
            continue
        add_rate = 100.0 * sum(1 for r in sub if r["started"]) / len(sub)
        pool_rate = statistics.mean([r["pool_start_rate"] for r in sub])
        positions.append({
            "position": code,
            "n": len(sub),
            "saturation": round(statistics.mean(
                [r["saturation"] for r in sub if r["saturation"] is not None]), 1),
            "free_players": round(statistics.mean(
                [r["free_at_pos"] for r in sub]), 0),
            "pool_start_rate": round(pool_rate, 1),
            "add_start_rate": round(add_rate, 1),
            "lift": round(add_rate - pool_rate, 1),
            "mean_fpts": round(statistics.mean([r["pts"] for r in started]), 2)
            if started else None,
            "pool_mean": round(statistics.mean(
                [r["pool_mean_started"] for r in started
                 if r["pool_mean_started"] is not None]), 2) if started else None,
            "within_pos_pctile": round(statistics.mean(
                [r["within_pos_pctile"] for r in started]), 1) if started else None,
        })
    payload["scarcity"] = {"positions": positions}
    payload["generated"] = datetime.now().astimezone().isoformat(timespec="seconds")

    SITE_DATA.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, indent=1)
    (SITE_DATA / "extra.json").write_text(body, encoding="utf-8")
    (SITE_DATA / "extra.js").write_text(
        "/* Generated by analysis/build_site_data.py - do not edit by hand. */\n"
        "window.PICKUP_EXTRA = %s;\n" % body, encoding="utf-8")
    print("wrote %s" % (SITE_DATA / "extra.js"))


if __name__ == "__main__":
    main()
