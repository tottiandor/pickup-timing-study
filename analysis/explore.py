#!/usr/bin/env python3
"""An analyst's pass over the pickup events, before any write-up exists.

    python analysis/explore.py --label igl

This is the "is there actually a finding here" script. It goes past means to
the things that would sink an article: whether the gaps survive their own
sample sizes, whether the snipe beats the thing it is actually competing
against, what the distribution looks like rather than its average, and what
the failed claims say.

Nothing here is written for publication. It prints.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "data" / "processed"
BUCKETS = ("waivers", "lineup_snipe", "late_add")
RULE = "-" * 74


def load(name, label=None):
    path = PROCESSED / name
    if not path.exists():
        sys.exit("missing %s - run the pipeline first" % path)
    with path.open(encoding="utf-8", newline="") as handle:
        return [r for r in csv.DictReader(handle)
                if not label or r["league_label"] == label]


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def wilson(hits, total):
    """95% confidence interval for a proportion.

    Wilson rather than the textbook normal interval, because the normal one
    misbehaves badly at the extremes - and a 98% start rate on 100 events is
    exactly an extreme.
    """
    if not total:
        return (0.0, 0.0)
    z = 1.96
    phat = hits / total
    denominator = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denominator
    spread = (z * math.sqrt(phat * (1 - phat) / total
                            + z * z / (4 * total * total)) / denominator)
    return (max(0.0, centre - spread) * 100, min(1.0, centre + spread) * 100)


def quantile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    low = int(math.floor(position))
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def prepare(events):
    out = []
    for event in events:
        points = number(event["gw_fpts"])
        if points is None:
            continue
        event["pts"] = points
        event["next4"] = number(event["next4_fpts"])
        event["held"] = number(event["held_fpts"])
        event["mins"] = number(event["minutes_before_ko"])
        event["started"] = event["gw_started"] == "True"
        event["bid_f"] = number(event["bid"])
        event["gw"] = int(event["target_gameweek"]) if \
            event["target_gameweek"].isdigit() else None
        event["pos"] = (event["position"] or "?").split(",")[0].strip() or "?"
        out.append(event)
    return out


# --------------------------------------------------------------------------- #

def distributions(events):
    print(RULE)
    print("DISTRIBUTION OF OUTCOMES, not just the average")
    print(RULE)
    print("A mean of 6 points can be one 30-pointer and nine blanks, or ten")
    print("sixes. For a decision rule it matters which.\n")
    print("%-13s %5s %6s %6s %6s %6s %6s | %7s %7s %7s"
          % ("bucket", "n", "p25", "med", "mean", "p75", "p90",
             "bust%", ">=10", ">=20"))
    for bucket in BUCKETS:
        rows = [e for e in events if e["bucket"] == bucket]
        if not rows:
            continue
        points = [e["pts"] for e in rows]
        bust = sum(1 for p in points if p < 2.0)
        ten = sum(1 for p in points if p >= 10.0)
        twenty = sum(1 for p in points if p >= 20.0)
        print("%-13s %5d %6.2f %6.2f %6.2f %6.2f %6.2f | %6.1f%% %6.1f%% %6.1f%%"
              % (bucket, len(rows), quantile(points, 0.25),
                 statistics.median(points), statistics.mean(points),
                 quantile(points, 0.75), quantile(points, 0.90),
                 100.0 * bust / len(rows), 100.0 * ten / len(rows),
                 100.0 * twenty / len(rows)))
    print("\nbust = under 2 FPts, which is roughly 'he did not play or did")
    print("nothing'. The interesting column is bust%, not the mean.")


def confidence(events):
    print("\n" + RULE)
    print("DOES THE START-RATE GAP SURVIVE ITS OWN SAMPLE SIZE?")
    print(RULE)
    print("95%% Wilson intervals. If two intervals do not overlap, the gap is")
    print("real at this sample size; if they do, it is not yet established.\n")
    print("%-13s %5s %8s   %s" % ("bucket", "n", "start%", "95% interval"))
    spans = {}
    for bucket in BUCKETS:
        rows = [e for e in events if e["bucket"] == bucket]
        if not rows:
            continue
        hits = sum(1 for e in rows if e["started"])
        low, high = wilson(hits, len(rows))
        spans[bucket] = (low, high)
        print("%-13s %5d %7.1f%%   %.1f%% to %.1f%%"
              % (bucket, len(rows), 100.0 * hits / len(rows), low, high))

    if "waivers" in spans and "lineup_snipe" in spans:
        waiver_high = spans["waivers"][1]
        snipe_low = spans["lineup_snipe"][0]
        print("\nwaivers top out at %.1f%%, snipes bottom out at %.1f%%."
              % (waiver_high, snipe_low))
        print("VERDICT: %s" % (
            "the start-rate gap is real, and not close."
            if snipe_low > waiver_high else
            "the intervals overlap - not established yet."))


def counterfactual(events, wide, baseline):
    print("\n" + RULE)
    print("THE COUNTERFACTUAL: where did the add rank among what was there?")
    print(RULE)
    print("A pickup is not competing with 'nothing'. It is competing with")
    print("every free agent available in that gameweek. The trap is which")
    print("pool you compare against:")
    print("")
    print("  free and started   what a perfect start-predictor would have got.")
    print("                     Comparing an *unconditional* add against this")
    print("                     stacks the deck against the add, because the")
    print("                     manager does not know who will start.")
    print("  free, any outcome  the honest denominator. This is the choice the")
    print("                     manager actually faced.")

    def by_gameweek(rows):
        grouped = defaultdict(list)
        for row in rows:
            points = number(row["fpts"])
            if points is not None and row["gameweek"].isdigit():
                grouped[int(row["gameweek"])].append(points)
        return grouped

    wide_pool = by_gameweek(wide)
    started_pool = by_gameweek(baseline)

    wide_points = [number(r["fpts"]) for r in wide if number(r["fpts"]) is not None]
    wide_started = sum(1 for r in wide if r["started"] == "True")
    print("\nthe pool, over the season: %d free player-gameweeks, mean %.2f "
          "FPts, %.1f%% of them started"
          % (len(wide_points), statistics.mean(wide_points),
             100.0 * wide_started / len(wide)))

    # Percentile rank is the fair summary: both sides unconditional, and it
    # answers "out of everything available, how good was this pick?" where 50
    # means the add carried no information at all.
    #
    # Each gameweek's pool is sorted and averaged ONCE. Scanning the pool per
    # event was fine on the pilot and is not at this scale: 24k events against
    # ~14k pool entries each is a third of a billion comparisons.
    prepared = {}
    for gameweek, values in wide_pool.items():
        ordered = sorted(values)
        prepared[gameweek] = (ordered, statistics.mean(ordered))

    def percentile_of(value, ordered):
        if not ordered:
            return None
        below = bisect.bisect_left(ordered, value)
        equal = bisect.bisect_right(ordered, value) - below
        return 100.0 * (below + 0.5 * equal) / len(ordered)

    print("\n%-13s %5s %9s %11s %12s %11s"
          % ("bucket", "n", "add FPts", "pool mean", "pctile in pool",
             "beat pool%"))
    for bucket in BUCKETS:
        rows = [e for e in events
                if e["bucket"] == bucket and e["gw"] in wide_pool]
        if not rows:
            continue
        percentiles, beats, means = [], 0, []
        for event in rows:
            ordered, pool_mean = prepared[event["gw"]]
            means.append(pool_mean)
            rank = percentile_of(event["pts"], ordered)
            if rank is not None:
                percentiles.append(rank)
            if event["pts"] > pool_mean:
                beats += 1
        print("%-13s %5d %9.2f %11.2f %11.1f%% %10.1f%%"
              % (bucket, len(rows), statistics.mean([e["pts"] for e in rows]),
                 statistics.mean(means), statistics.mean(percentiles),
                 100.0 * beats / len(rows)))

    print("\n50%% percentile would mean picking at random. Anything well above")
    print("it is genuine selection skill.")

    print("\nand against the started-only pool, for completeness - remember")
    print("this one is biased against the add:")
    print("%-13s %9s %11s %11s" % ("bucket", "add FPts", "pool mean",
                                   "beat pool%"))
    for bucket in BUCKETS:
        rows = [e for e in events
                if e["bucket"] == bucket and e["gw"] in started_pool]
        if not rows:
            continue
        started_means = {g: statistics.mean(v) for g, v in started_pool.items()}
        beats = sum(1 for e in rows if e["pts"] > started_means[e["gw"]])
        started = [e for e in rows if e["started"]]
        print("%-13s %9.2f %11.2f %10.1f%%%s"
              % (bucket, statistics.mean([e["pts"] for e in rows]),
                 statistics.mean([started_means[e["gw"]] for e in rows]),
                 100.0 * beats / len(rows),
                 "   (if he started: %.2f)"
                 % statistics.mean([e["pts"] for e in started]) if started else ""))


def same_player(events):
    print("\n" + RULE)
    print("THE CLEANEST TEST: the same player, reached two different ways")
    print(RULE)
    print("Player quality is the obvious confounder - maybe snipers simply")
    print("chase better players. This holds the player-gameweek fixed and")
    print("asks whether the route to him mattered.\n")

    groups = defaultdict(dict)
    for event in events:
        groups[(event["player_id"], event["target_gameweek"])].setdefault(
            event["bucket"], event)

    pairs = Counter()
    for key, routes in groups.items():
        present = tuple(sorted(r for r in routes if r in BUCKETS))
        if len(present) > 1:
            pairs[present] += 1
    if not pairs:
        print("No player-gameweek was reached by more than one bucket in this")
        print("league, so this test needs the big leagues to run at all.")
        print("It is the test that will matter most once IGL is in.")
        return
    for combination, count in pairs.most_common():
        print("  %-34s %d player-gameweeks" % (" + ".join(combination), count))


def positions(events):
    print("\n" + RULE)
    print("BY POSITION, with the sample size shown so nothing gets quoted thin")
    print(RULE)
    print("%-4s %-13s %5s %8s %8s %8s %8s"
          % ("pos", "bucket", "n", "start%", "mean", "median", "bust%"))
    for position in sorted({e["pos"] for e in events}):
        for bucket in BUCKETS:
            rows = [e for e in events
                    if e["pos"] == position and e["bucket"] == bucket]
            if not rows:
                continue
            points = [e["pts"] for e in rows]
            starts = sum(1 for e in rows if e["started"])
            bust = sum(1 for p in points if p < 2.0)
            flag = "  <- too thin" if len(rows) < 30 else ""
            print("%-4s %-13s %5d %7.1f%% %8.2f %8.2f %7.1f%%%s"
                  % (position, bucket, len(rows), 100.0 * starts / len(rows),
                     statistics.mean(points), statistics.median(points),
                     100.0 * bust / len(rows), flag))


def faab(events):
    print("\n" + RULE)
    print("DOES PAYING MORE GET YOU MORE? (FAAB bids on waiver claims)")
    print(RULE)
    bids = [e for e in events if e["bid_f"] is not None and e["bid_f"] > 0]
    if not bids:
        print("No bid amounts in this league's data.")
        return
    print("%d claims carry a bid. Range %.0f to %.0f.\n"
          % (len(bids), min(e["bid_f"] for e in bids),
             max(e["bid_f"] for e in bids)))
    ordered = sorted(bids, key=lambda e: e["bid_f"])
    size = max(1, len(ordered) // 4)
    print("%-16s %5s %9s %8s %8s" % ("bid quartile", "n", "mean bid",
                                     "mean pts", "start%"))
    for index in range(0, len(ordered), size):
        chunk = ordered[index:index + size]
        if len(chunk) < 5:
            continue
        starts = sum(1 for e in chunk if e["started"])
        print("%-16s %5d %9.2f %8.2f %7.1f%%"
              % ("Q%d" % (index // size + 1), len(chunk),
                 statistics.mean([e["bid_f"] for e in chunk]),
                 statistics.mean([e["pts"] for e in chunk]),
                 100.0 * starts / len(chunk)))

    paid = [e["bid_f"] for e in bids]
    got = [e["pts"] for e in bids]
    if len(paid) > 2:
        mean_paid, mean_got = statistics.mean(paid), statistics.mean(got)
        top = sum((p - mean_paid) * (g - mean_got) for p, g in zip(paid, got))
        bottom = math.sqrt(sum((p - mean_paid) ** 2 for p in paid)
                           * sum((g - mean_got) ** 2 for g in got))
        if bottom:
            print("\ncorrelation between bid and points that gameweek: %+.3f"
                  % (top / bottom))
            print("(near zero would mean the market cannot price a week ahead.)")


def failed_claims(label):
    print("\n" + RULE)
    print("THE FAILED CLAIMS - a free second dataset")
    print(RULE)
    rows = load("transactions.csv", label)
    claims = [r for r in rows if r["transaction_code"] == "CLAIM"]
    results = Counter(r["result_code"] for r in claims)
    print("claim outcomes:", dict(results))

    failed = [r for r in claims if r["result_code"] == "CLAIM_FAILED"]
    if not failed:
        return
    contested = Counter(r["player_id"] for r in failed)
    print("\n%d failed claims over %d distinct players."
          % (len(failed), len(contested)))
    print("The most fought-over players of the season:")
    names = {r["player_id"]: r["player_name"] for r in failed}
    for player_id, count in contested.most_common(10):
        print("   %-24s %d failed claims" % (names.get(player_id, player_id), count))
    print("\nThis is a record of demand, independent of outcome, and it is the")
    print("only thing here that measures what managers *wanted* rather than")
    print("what they got.")


def timing(events):
    print("\n" + RULE)
    print("THE LINEUP CLIFF")
    print(RULE)
    def count(low, high):
        return sum(1 for e in events if e["mins"] is not None
                   and low <= e["mins"] < high)
    after, before = count(70, 75), count(75, 80)
    print("adds 70-75 min before kickoff (just AFTER team news):  %d" % after)
    print("adds 75-80 min before kickoff (just BEFORE it):        %d" % before)
    if before:
        print("ratio: %.1fx" % (after / before))
    print("\nThe five minutes either side of the announcement are otherwise")
    print("identical - same day, same hour, same managers. The only thing that")
    print("changes at KO-75 is that the team sheets land.")

    print("\nhow the adds split overall:")
    windows = [("inside 70 min of kickoff", 0, 70),
               ("the snipe window, 70-75", 70, 75),
               ("75 min to 3 hours", 75, 180),
               ("3 to 24 hours", 180, 1440),
               ("1 to 3 days", 1440, 4320),
               ("more than 3 days", 4320, float("inf"))]
    total = len(events)
    for name, low, high in windows:
        found = sum(1 for e in events if e["mins"] is not None
                    and low <= e["mins"] < high)
        print("  %-26s %5d  %5.1f%%" % (name, found, 100.0 * found / total))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label")
    args = parser.parse_args()

    events = prepare(load("pickup_events.csv", args.label))
    baseline = load("baseline_pool.csv", args.label)
    wide = load("unowned_pool.csv", args.label)
    print("%d scored pickups, %d free-and-started, %d free player-gameweeks\n"
          % (len(events), len(baseline), len(wide)))

    distributions(events)
    confidence(events)
    counterfactual(events, wide, baseline)
    same_player(events)
    positions(events)
    faab(events)
    timing(events)
    failed_claims(args.label)


if __name__ == "__main__":
    main()
