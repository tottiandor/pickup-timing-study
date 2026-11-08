"""Step 4: value metrics.

    python analysis/metrics.py
    python analysis/metrics.py --label igl

Reads data/processed/pickup_events.csv and baseline_pool.csv, and writes
site/data/metrics.json for the pages to read, plus printed tables.

Value is the player's real Fantrax points, never a fantasy line-up decision:

    gw_fpts      points in the gameweek the add targeted
    started      whether he started a real match that gameweek
    pps          points per start - total points in started gameweeks divided
                 by the number of started gameweeks, per the brief
    next4_fpts   points over the target gameweek and the three after it
    held_fpts    points while on that roster, add to drop

Every table is produced two ways, because they answer different questions:

    per event    every add counts. What a manager's add was worth on average.
    per unique   one row per player-gameweek per bucket. If forty divisions
                 all snipe the same player that is one decision about one
                 player, not forty, and the per-event view would report the
                 crowd's favourite forty times over.

The dedupe key is (bucket, player_id, target_gameweek). Bucket is in the key
deliberately: the same player-gameweek reached by a waiver claim and by a
snipe is two different decisions, and collapsing them would hide exactly the
contrast the study is about.

**A comparability trap in the baseline.** The never-picked-up pool is
conditioned on having started, by construction - it is the players who were
free *and* started. So it compares to the `mean_fpts_if_started` column, never
to `mean_fpts`. Both are reported so the mistake is hard to make by accident.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROCESSED = ROOT / "data" / "processed"
SITE_DATA = ROOT / "site" / "data"

BUCKETS = ("waivers", "lineup_snipe", "late_add")
BUCKET_LABELS = {
    "waivers": "Waivers (>KO-75)",
    "lineup_snipe": "Lineup snipe (KO-75 to KO-70)",
    "late_add": "Late add (KO-70 to KO)",
}
LOCK_MINUTES = 75
SNIPE_FLOOR = 70

# Season phase by target gameweek. Thirds of a 38-week season.
PHASES = (("early", 1, 12), ("mid", 13, 25), ("late", 26, 38))

# Histogram edges in minutes before kickoff. Deliberately fine around the
# 70 and 75 minute marks, where the whole study lives, and coarse out to a week.
HIST_EDGES = [0, 5, 10, 15, 20, 30, 40, 50, 60, 65, 70, 75, 80, 90, 120, 180,
              360, 720, 1440, 2880, 4320, 10080]


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load(name, label=None):
    path = PROCESSED / name
    if not path.exists():
        sys.exit("no %s - run analysis/events.py first" % path)
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [r for r in csv.DictReader(handle)
                if not label or r["league_label"] == label]
    return rows


def prepare(events):
    for event in events:
        event["gw_fpts_f"] = _float(event["gw_fpts"])
        event["next4_f"] = _float(event["next4_fpts"])
        event["held_f"] = _float(event["held_fpts"])
        event["minutes_f"] = _float(event["minutes_before_ko"])
        event["started_b"] = event["gw_started"] == "True"
        event["gameweek_i"] = int(event["target_gameweek"]) if \
            event["target_gameweek"].isdigit() else None
        event["position_s"] = (event["position"] or "").split(",")[0].strip() or "?"
    return [e for e in events if e["gw_fpts_f"] is not None]


def dedupe(events):
    """One event per (bucket, player, target gameweek) - one decision."""
    unique = {}
    for event in events:
        key = (event["bucket"], event["player_id"], event["target_gameweek"])
        unique.setdefault(key, event)
    return list(unique.values())


# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #

def summarise(events):
    """The metric block for one set of events."""
    if not events:
        return None
    points = [e["gw_fpts_f"] for e in events]
    started = [e for e in events if e["started_b"]]
    started_points = [e["gw_fpts_f"] for e in started]
    next4 = [e["next4_f"] for e in events if e["next4_f"] is not None]
    held = [e["held_f"] for e in events if e["held_f"] is not None]

    return {
        "n": len(events),
        "start_rate": round(100.0 * len(started) / len(events), 1),
        "mean_fpts": round(statistics.mean(points), 2),
        "median_fpts": round(statistics.median(points), 2),
        # Points per start, per the brief: points in started gameweeks over the
        # number of started gameweeks. Not the same as mean points per add.
        "pps": round(statistics.mean(started_points), 2) if started_points else None,
        "mean_next4": round(statistics.mean(next4), 2) if next4 else None,
        "mean_held": round(statistics.mean(held), 2) if held else None,
        "mean_fpts_if_started": (round(statistics.mean(started_points), 2)
                                 if started_points else None),
    }


def by_bucket(events):
    return {bucket: summarise([e for e in events if e["bucket"] == bucket])
            for bucket in BUCKETS}


def cut(events, key_of, order=None):
    """{key: {bucket: summary}} for an arbitrary cut."""
    groups = defaultdict(list)
    for event in events:
        key = key_of(event)
        if key is not None:
            groups[key].append(event)
        keys = order or sorted(groups)
    return {str(key): by_bucket(groups[key]) for key in (order or sorted(groups))
            if key in groups}


def phase_of(event):
    gameweek = event["gameweek_i"]
    if gameweek is None:
        return None
    for name, low, high in PHASES:
        if low <= gameweek <= high:
            return name
    return None


def histogram(events):
    """Counts per minutes-before-kickoff band, per bucket."""
    bands = []
    for index in range(len(HIST_EDGES) - 1):
        low, high = HIST_EDGES[index], HIST_EDGES[index + 1]
        band = {"from": low, "to": high, "total": 0}
        for bucket in BUCKETS:
            band[bucket] = 0
        for event in events:
            minutes = event["minutes_f"]
            if minutes is None:
                continue
            if low <= minutes < high:
                band["total"] += 1
                band[event["bucket"]] += 1
        bands.append(band)
    over = [e for e in events if e["minutes_f"] is not None
            and e["minutes_f"] >= HIST_EDGES[-1]]
    if over:
        band = {"from": HIST_EDGES[-1], "to": None, "total": len(over)}
        for bucket in BUCKETS:
            band[bucket] = sum(1 for e in over if e["bucket"] == bucket)
        bands.append(band)
    return bands


def lineup_cliff(events):
    """The study's central behavioural fact, as one number.

    Lineups are announced at KO-75. So the five minutes *after* that
    announcement are 70-75 minutes before kickoff, and the five minutes before
    it are 75-80. Managers who are watching for team news act in the first
    window and cannot act in the second, because there is nothing to react to
    yet. The ratio between the two is the cleanest evidence that sniping is a
    deliberate behaviour rather than an artefact of when people happen to be
    online.
    """
    def count(low, high):
        return sum(1 for e in events if e["minutes_f"] is not None
                   and low <= e["minutes_f"] < high)

    after = count(SNIPE_FLOOR, LOCK_MINUTES)
    before = count(LOCK_MINUTES, LOCK_MINUTES + 5)
    return {
        "after_announcement": after,
        "before_announcement": before,
        "ratio": round(float(after) / before, 1) if before else None,
    }


def baseline_summary(baseline):
    if not baseline:
        return None
    points = [_float(b["fpts"]) or 0.0 for b in baseline]
    per_gameweek = Counter(int(b["gameweek"]) for b in baseline
                           if b["gameweek"].isdigit())
    return {
        "n": len(baseline),
        "mean_fpts": round(statistics.mean(points), 2),
        "median_fpts": round(statistics.median(points), 2),
        # Conditioned on having started, so this is the number that compares to
        # a bucket's mean_fpts_if_started.
        "conditioned_on_start": True,
        "mean_per_gameweek": round(
            statistics.mean(list(per_gameweek.values())), 1) if per_gameweek else None,
    }


def best_pickups(events, limit=60):
    """The most valuable adds, one row per player-gameweek.

    Deduped deliberately. Across 18 divisions the same player-gameweek is
    picked up by dozens of teams, so a straight top-40 by points is the same
    handful of players repeated over and over - one haul filled twelve rows
    with the same name. Each row here is one player-gameweek, carrying how
    many teams took him and the earliest bucket anyone got him in.
    """
    grouped = {}
    for event in events:
        key = (event["player_id"], event["target_gameweek"])
        row = grouped.get(key)
        if row is None:
            grouped[key] = row = {
                "player": event["player_name"],
                "position": event["position_s"],
                "club": event["club"],
                "gameweek": event["gameweek_i"],
                "gw_fpts": event["gw_fpts_f"],
                "next4_fpts": event["next4_f"],
                "started": event["started_b"],
                "teams": 0,
                "bucket": event["bucket"],
                "minutes_before_ko": event["minutes_f"],
            }
        row["teams"] += 1
        # Keep the latest add, which is the one closest to kickoff: it is the
        # most informed route anybody took to him.
        if (event["minutes_f"] is not None
                and (row["minutes_before_ko"] is None
                     or event["minutes_f"] < row["minutes_before_ko"])):
            row["minutes_before_ko"] = event["minutes_f"]
            row["bucket"] = event["bucket"]
    ranked = sorted(grouped.values(), key=lambda r: r["gw_fpts"], reverse=True)
    return ranked[:limit]


# --------------------------------------------------------------------------- #
# Printing
# --------------------------------------------------------------------------- #

def print_block(title, block):
    print("\n%s" % title)
    print("  %-14s %6s %8s %9s %8s %7s %8s %8s"
          % ("bucket", "n", "start%", "mean", "median", "PPS", "next4", "held"))
    for bucket in BUCKETS:
        summary = block.get(bucket)
        if not summary:
            print("  %-14s %6d %8s %9s %8s %7s %8s %8s"
                  % (bucket, 0, "-", "-", "-", "-", "-", "-"))
            continue
        print("  %-14s %6d %7.1f%% %9.2f %8.2f %7.2f %8s %8s" % (
            bucket, summary["n"], summary["start_rate"], summary["mean_fpts"],
            summary["median_fpts"], summary["pps"] or 0.0,
            "%.2f" % summary["mean_next4"] if summary["mean_next4"] else "-",
            "%.2f" % summary["mean_held"] if summary["mean_held"] else "-"))


def print_cut(title, cut_block, min_n=1):
    print("\n%s" % title)
    print("  %-16s %-14s %6s %8s %9s %8s" % ("", "bucket", "n", "start%",
                                             "mean", "median"))
    for key, block in cut_block.items():
        for bucket in BUCKETS:
            summary = block.get(bucket)
            if not summary or summary["n"] < min_n:
                continue
            flag = "  <- thin" if summary["n"] < 30 else ""
            print("  %-16s %-14s %6d %7.1f%% %9.2f %8.2f%s" % (
                key, bucket, summary["n"], summary["start_rate"],
                summary["mean_fpts"], summary["median_fpts"], flag))


def print_histogram(bands):
    print("\nwhen adds happen (minutes before kickoff)")
    peak = max((b["total"] for b in bands), default=0) or 1
    for band in bands:
        if not band["total"]:
            continue
        span = ("%d+" % band["from"] if band["to"] is None
                else "%d-%d" % (band["from"], band["to"]))
        mark = ""
        if band["from"] == SNIPE_FLOOR:
            mark = "  <- THE SNIPE WINDOW: lineups just dropped"
        elif band["from"] == LOCK_MINUTES:
            mark = "  <- the five minutes BEFORE lineups drop"
        bar = "#" * max(1, int(40.0 * band["total"] / peak))
        print("  %9s min %5d %-40s%s" % (span, band["total"], bar, mark))


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def build(events, baseline):
    per_event = events
    per_unique = dedupe(events)

    payload = {
        "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "bucket_labels": BUCKET_LABELS,
        "lock_minutes": LOCK_MINUTES,
        "snipe_floor": SNIPE_FLOOR,
        "counts": {
            "events": len(per_event),
            "unique_player_gameweeks": len(per_unique),
            "baseline": len(baseline),
        },
        "per_event": by_bucket(per_event),
        "per_unique": by_bucket(per_unique),
        "baseline": baseline_summary(baseline),
        "by_position": cut(per_event, lambda e: e["position_s"]),
        "by_phase": cut(per_event, phase_of, order=[p[0] for p in PHASES]),
        "by_gameweek": cut(per_event, lambda e: e["gameweek_i"]),
        "by_league": cut(per_event, lambda e: e["league_label"]),
        "by_claim_type": cut(per_event, lambda e: e["claim_type"] or "?"),
        "missed_live_period": by_bucket(
            [e for e in per_event if e["missed_live_period"] == "True"]),
        "histogram": histogram(per_event),
        "lineup_cliff": lineup_cliff(per_event),
        "best_pickups": best_pickups(per_event),
    }
    return payload, per_unique


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", help="one league only")
    args = parser.parse_args()

    events = prepare(load("pickup_events.csv", args.label))
    baseline = load("baseline_pool.csv", args.label)
    if not events:
        sys.exit("no scored pickup events found")

    payload, per_unique = build(events, baseline)

    print("pickup events: %d, unique player-gameweeks: %d, baseline: %d"
          % (len(events), len(per_unique), len(baseline)))

    print_block("per event (every add counts)", payload["per_event"])
    print_block("per unique player-gameweek (one decision each)",
                payload["per_unique"])

    base = payload["baseline"]
    if base:
        print("\nnever picked up, free at kickoff and started: n=%d mean=%.2f "
              "median=%.2f" % (base["n"], base["mean_fpts"], base["median_fpts"]))
        print("  compares to mean-if-started, not to mean:")
        for bucket in BUCKETS:
            summary = payload["per_event"].get(bucket)
            if summary and summary["mean_fpts_if_started"]:
                delta = summary["mean_fpts_if_started"] - base["mean_fpts"]
                print("    %-14s %6.2f  (%+.2f vs baseline)"
                      % (bucket, summary["mean_fpts_if_started"], delta))

    print_cut("by position", payload["by_position"])
    print_cut("by season phase", payload["by_phase"])
    print_cut("by claim type (WW waiver claim, FA free agent add)",
              payload["by_claim_type"])
    if len(payload["by_league"]) > 1:
        print_cut("by league", payload["by_league"])

    print_histogram(payload["histogram"])

    cliff = payload["lineup_cliff"]
    print("\nthe lineup cliff (lineups are announced at KO-75):")
    print("  adds in the 5 min AFTER lineups drop (KO-75 to KO-70): %d"
          % cliff["after_announcement"])
    print("  adds in the 5 min BEFORE they drop   (KO-80 to KO-75): %d"
          % cliff["before_announcement"])
    if cliff["ratio"]:
        print("  ratio: %.1fx more adds once the team news is out" % cliff["ratio"])

    print("\nbest single pickups by points in the target gameweek:")
    for row in payload["best_pickups"][:10]:
        print("  %-22s %-3s GW%-3s %-13s %6.2f  (%.0f min before KO)"
              % (row["player"], row["position"], row["gameweek"], row["bucket"],
                 row["gw_fpts"], row["minutes_before_ko"]))

    thin = [(key, bucket, block[bucket]["n"])
            for key, block in payload["by_position"].items()
            for bucket in BUCKETS
            if block.get(bucket) and block[bucket]["n"] < 30]
    if thin:
        print("\n%d position/bucket cells under 30 events - too thin to quote "
              "on their own:" % len(thin))
        for key, bucket, count in thin:
            print("   %-6s %-14s %d" % (key, bucket, count))

    SITE_DATA.mkdir(parents=True, exist_ok=True)
    out = SITE_DATA / "metrics.json"
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print("\nwrote %s" % out)

    # The same payload as a script that assigns a global, which is what the
    # pages actually load. `fetch()` is blocked on file:// URLs, so a
    # fetch-based page breaks when opened directly or pasted into a Wix HTML
    # block; a <script src> works in both. The .json stays for anything
    # external that wants to read it.
    script = SITE_DATA / "metrics.js"
    script.write_text(
        "/* Generated by analysis/metrics.py - do not edit by hand. */\n"
        "window.PICKUP_METRICS = %s;\n" % json.dumps(payload, indent=1),
        encoding="utf-8")
    print("wrote %s" % script)


if __name__ == "__main__":
    main()
