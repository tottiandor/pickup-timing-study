"""Step 3: the event table.

Rebuilds each league's rosters over time, then buckets every pickup by how
long before kickoff it happened and attaches what the player went on to score.

    python analysis/events.py                      # every league pulled
    python analysis/events.py --label igl

Writes data/processed/pickup_events.csv (one row per executed add) and
data/processed/baseline_pool.csv (the never-picked-up comparison pool).

The buckets, measured against the kickoff of the player's next real fixture,
with lineups announced at KO-75:

    waivers        added more than 75 minutes before kickoff
    lineup_snipe   added between KO-75 and KO-70
    late_add       added between KO-70 and kickoff

**On the brief's fourth bucket, `post_lock`.** The brief asks for adds "made
after the player's game kicked off" to be held apart from bucket 1. Taken
literally against the league's own scoring periods that turns out to collide
with bucket 1 rather than carve anything out of it: periods run Friday to
Thursday, so the weekend's matches sit at the *start* of a period and every
ordinary mid-week waiver add lands after them. On the pilot that rule claimed
569 adds - 46% of all of them - of which 553 were more than 75 minutes from
the next kickoff, 396 made on a Thursday, median just under two days of lead
time. Those are the archetypal bucket 1 waiver adds, not a separate
phenomenon.

**Settled by András on 2026-11-07: these stay in bucket 1.** The bucket is
assigned by time to the next kickoff, which is the brief's own primary
definition. The other property is kept as its own column,
`missed_live_period` - true when the player had already played in the scoring
period the add landed in and had no fixture left in it - so the cut is still
available to anyone who wants it, and `report` still prints the split both
ways. It is a column, not a bucket.

Two things read from the data rather than assumed:

- Bucket 1 splits by Fantrax's own `claim_type`: `WW` waiver claim, `FA` free
  agent add.
- A player's club comes from the **scoring data**, per gameweek, not from the
  transaction row. Fantrax reports a transacted player's club as their club
  *history* - values like `TOT/CRY` and `BHA/CHE/LEE` - which silently dropped
  158 claims when used as a fixture key.

Only `EXECUTED` claims are pickups. A `CLAIM_FAILED` row moved no player - 29%
of claims in the pilot - and is counted separately rather than mixed in.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scraper"))

import fantrax  # noqa: E402
import reference as ref  # noqa: E402

RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

LOCK_MINUTES = 75
SNIPE_FLOOR = 70
BUCKETS = ("waivers", "lineup_snipe", "late_add")

EVENT_FIELDS = [
    "league_label", "division", "team_id", "team_name",
    "player_id", "player_name", "position", "club",
    "claim_type", "bucket", "missed_live_period", "bid",
    "added_utc", "kickoff_utc", "minutes_before_ko",
    "league_period", "target_gameweek",
    "gw_fpts", "gw_started", "gw_starts", "gw_matches", "gw_minutes",
    "next4_fpts", "next4_gameweeks",
    "removed_utc", "held_gameweeks", "held_fpts",
    # Points per start, tenure removed: held_fpts rewards simply owning
    # somebody for a long time, however badly he played.
    "held_starts", "held_start_fpts", "held_pps", "gw_to_first_start",
]

BASELINE_FIELDS = [
    "league_label", "division", "gameweek", "player_id", "player_name",
    "position", "club", "kickoff_utc", "fpts", "started", "starts", "matches",
    "minutes",
]


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def load_transactions(label=None):
    path = PROCESSED / "transactions.csv"
    if not path.exists():
        sys.exit("no %s - run scraper/pull.py first" % path)
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [r for r in csv.DictReader(handle)
                if not label or r["league_label"] == label]
    for row in rows:
        row["added"] = (datetime.fromisoformat(row["stamp_utc"])
                        if row["stamp_utc"] else None)
        row["executed"] = row["executed"] == "True"
        row["is_player_move"] = row["is_player_move"] == "True"
    return rows


def draft_rosters(league_id):
    """{team_id: {player_id, ...}} as drafted, across every division.

    A multi-division league has one draft file per division, because Fantrax
    only serves one division's picks per call. All of them are merged here;
    reading a single file would leave every team outside one division with an
    empty opening roster, so their drafted players would look like free agents
    all season and the baseline pool would be badly wrong.
    """
    rosters = defaultdict(set)
    files = sorted(RAW.glob("draft_%s*.json" % league_id))
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for pick in payload.get("draftPicksOrdered") or []:
            team, player = pick.get("teamId"), pick.get("scorerId")
            if team and player:
                rosters[team].add(player)
    return dict(rosters)


def league_meta(league_id):
    path = RAW / ("league_info_%s.json" % league_id)
    info = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return fantrax.teams(info), fantrax.scoring_periods(info)


# --------------------------------------------------------------------------- #
# Per-player fixture list, from the scoring data
# --------------------------------------------------------------------------- #

def player_fixtures(player_gameweeks, fixtures):
    """{player_id: [{kickoff, gameweek, club}, ...]} in kickoff order.

    Built from the scoring rows, so a player who moved club mid-season gets
    the fixtures of whichever club he was actually at in each gameweek.
    """
    fixture_of = {}
    for fixture in fixtures:
        for club in (fixture["home"], fixture["away"]):
            fixture_of[(club, fixture["togga_gameweek"])] = fixture

    by_player = defaultdict(dict)
    for row in player_gameweeks:
        fixture = fixture_of.get((row["club"], row["gameweek"]))
        if not fixture:
            continue
        # keyed so a player's two matches in one gameweek both survive
        by_player[row["player_id"]][(fixture["home"], fixture["away"])] = {
            "kickoff": fixture["kickoff_utc"],
            "gameweek": fixture["togga_gameweek"],
            "club": row["club"],
        }

    out = {}
    for player_id, matches in by_player.items():
        ordered = sorted(matches.values(), key=lambda m: m["kickoff"])
        out[player_id] = ordered
    return out


def next_fixture(matches, after):
    for match in matches:
        if match["kickoff"] > after:
            return match
    return None


# --------------------------------------------------------------------------- #
# Roster timeline
# --------------------------------------------------------------------------- #

def roster_moves(rows, teams_wanted=None):
    """Every roster-changing move, oldest first.

    Sorted so a removal at a given minute is applied before an addition at
    that same minute: timestamps carry only minutes, and a player dropped and
    re-claimed inside one minute must not look doubly owned.
    """
    moves = []

    def keep(team):
        return team and (teams_wanted is None or team in teams_wanted)

    for row in rows:
        if not row["added"] or not row["player_id"]:
            continue
        code = row["transaction_code"]
        if code == "CLAIM":
            if (row["executed"] and row["result_code"] == "EXECUTED"
                    and keep(row["team_id"])):
                moves.append((row["added"], 1, "add", row["player_id"],
                              row["team_id"]))
        elif code == "DROP":
            if keep(row["team_id"]):
                moves.append((row["added"], 0, "remove", row["player_id"],
                              row["team_id"]))
        elif code == "TRADE":
            if row["is_player_move"] and row["result_code"] == "EXECUTED":
                if keep(row["from_team_id"]):
                    moves.append((row["added"], 0, "remove", row["player_id"],
                                  row["from_team_id"]))
                if keep(row["to_team_id"]):
                    moves.append((row["added"], 1, "add", row["player_id"],
                                  row["to_team_id"]))
    moves.sort(key=lambda m: (m[0], m[1]))
    return moves


def ownership_at_kickoffs(rows, rosters, kickoffs, teams_wanted):
    """{kickoff: {player_id: team_id}} - who owned whom at each kickoff.

    Ownership is **per division**: a player rostered in one division is still a
    free agent in every other, so this is only ever called with one division's
    teams at a time.
    """
    owner = {}
    for team, players in rosters.items():
        if team in teams_wanted:
            for player in players:
                owner[player] = team

    moves = roster_moves(rows, teams_wanted)
    snapshots, cursor = {}, 0
    for kickoff in kickoffs:
        while cursor < len(moves) and moves[cursor][0] <= kickoff:
            _, _, action, player, team = moves[cursor]
            if action == "add":
                owner[player] = team
            elif owner.get(player) == team:
                owner.pop(player, None)
            cursor += 1
        snapshots[kickoff] = dict(owner)
    return snapshots


def removal_index(rows):
    """{(player_id, team_id): [times]} - when a team let a player go.

    Indexed once rather than scanned per event; a 143-team league has far too
    many rows for the quadratic version.
    """
    index = defaultdict(list)
    for row in rows:
        if not row["added"] or not row["player_id"]:
            continue
        code = row["transaction_code"]
        if code == "DROP":
            index[(row["player_id"], row["team_id"])].append(row["added"])
        elif (code == "TRADE" and row["is_player_move"]
              and row["result_code"] == "EXECUTED" and row["from_team_id"]):
            index[(row["player_id"], row["from_team_id"])].append(row["added"])
    for times in index.values():
        times.sort()
    return index


def first_removal(index, player_id, team_id, after):
    times = index.get((player_id, team_id))
    if not times:
        return None
    position = bisect.bisect_right(times, after)
    return times[position] if position < len(times) else None


# --------------------------------------------------------------------------- #
# Bucketing
# --------------------------------------------------------------------------- #

def period_of(periods, when):
    for period in periods:
        start, end = period["start"], period["end"]
        if start and end and start <= when <= end:
            return period["number"]
    return None


def bucket_for(minutes):
    if minutes > LOCK_MINUTES:
        return "waivers"
    if minutes > SNIPE_FLOOR:
        return "lineup_snipe"
    return "late_add"


def held_window(points, matches, player_id, start, end):
    """What the spell on this roster was worth.

    Returns (gameweeks, points, started_gameweeks, points_in_started,
    first_started_gameweek).

    `first_started_gameweek` is there for the stash case: a big early bid on
    an injured player or a new signing returns nothing for weeks, then starts.
    Judging that claim on the gameweek after it was made says nothing at all
    about whether it was a good buy.

    The last two carry PPS - points per start, as the brief defines it: points
    in started rows over the number of started rows. It matters because
    `held_fpts` alone conflates two very different players, one who started
    every week and one who was carried on the bench and happened to be owned
    for a long time. Counted per gameweek rather than per match, so a double
    gameweek where he started once counts as one started gameweek carrying
    that whole gameweek's points.
    """
    gameweeks, total = set(), 0.0
    started, started_points = 0, 0.0
    first_start = None
    for match in sorted(matches, key=lambda m: m["kickoff"]):
        kickoff = match["kickoff"]
        if kickoff <= start or (end and kickoff >= end):
            continue
        if match["gameweek"] in gameweeks:
            continue
        scored = points.get((player_id, match["gameweek"]))
        if scored:
            gameweeks.add(match["gameweek"])
            total += scored["fpts"]
            if scored["started"]:
                started += 1
                started_points += scored["fpts"]
                if first_start is None:
                    first_start = match["gameweek"]
    return (len(gameweeks), round(total, 2), started,
            round(started_points, 2), first_start)


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #

def build(label, rows, player_gameweeks, fixtures, points, by_player):
    league_id = rows[0]["league_id"]
    team_map, periods = league_meta(league_id)
    removals = removal_index(rows)

    # period_of scans every period, and it is called once per fixture per
    # claim. At 24k claims across 38 periods that is tens of millions of
    # comparisons, so each distinct timestamp is resolved once and reused.
    period_cache = {}

    def period_at(when):
        hit = period_cache.get(when)
        if hit is None:
            hit = period_cache[when] = period_of(periods, when) or 0
        return hit or None

    events, skipped = [], Counter()
    for row in rows:
        if row["transaction_code"] != "CLAIM":
            continue
        if not (row["executed"] and row["result_code"] == "EXECUTED"):
            skipped[row["result_code"] or "no result"] += 1
            continue
        added = row["added"]
        if not added:
            skipped["no timestamp"] += 1
            continue

        matches = by_player.get(row["player_id"])
        if not matches:
            skipped["player has no fixtures in the scoring data"] += 1
            continue

        target = next_fixture(matches, added)
        if target is None:
            skipped["no fixture left in the season"] += 1
            continue

        this_period = period_at(added)
        # Had the player already played in the period this add landed in, with
        # nothing left in it? Then the add could not affect that period.
        in_period = [m for m in matches
                     if period_at(m["kickoff"]) == this_period]
        missed_live_period = bool(
            this_period
            and any(m["kickoff"] <= added for m in in_period)
            and not any(m["kickoff"] > added for m in in_period))

        minutes = (target["kickoff"] - added).total_seconds() / 60.0
        gameweek = target["gameweek"]
        scored = points.get((row["player_id"], gameweek))

        next4 = [points.get((row["player_id"], gameweek + step))
                 for step in range(4)]
        next4_rows = [s for s in next4 if s]

        removed = first_removal(removals, row["player_id"], row["team_id"], added)
        (held_gameweeks, held_points, held_starts, held_start_points,
         first_start_gw) = held_window(
            points, matches, row["player_id"], added, removed)

        events.append({
            "league_label": label,
            "division": row["division"] or "",
            "team_id": row["team_id"],
            "team_name": row["team_name"],
            "player_id": row["player_id"],
            "player_name": row["player_name"],
            "position": (scored or {}).get("position") or row["player_pos"],
            "club": target["club"],
            "claim_type": row["claim_type"],
            "bucket": bucket_for(minutes),
            "missed_live_period": missed_live_period,
            "bid": row.get("bid") or "",
            "added_utc": added.isoformat(),
            "kickoff_utc": target["kickoff"].isoformat(),
            "minutes_before_ko": round(minutes, 1),
            "league_period": this_period,
            "target_gameweek": gameweek,
            "gw_fpts": round(scored["fpts"], 2) if scored else "",
            "gw_started": scored["started"] if scored else "",
            "gw_starts": scored["starts"] if scored else "",
            "gw_matches": scored["matches"] if scored else "",
            "gw_minutes": scored["minutes"] if scored else "",
            "next4_fpts": round(sum(s["fpts"] for s in next4_rows), 2)
                          if next4_rows else "",
            "next4_gameweeks": len(next4_rows),
            "removed_utc": removed.isoformat() if removed else "",
            "held_gameweeks": held_gameweeks,
            "held_fpts": held_points,
            "held_starts": held_starts,
            "held_start_fpts": held_start_points,
            "held_pps": (round(held_start_points / held_starts, 2)
                         if held_starts else ""),
            # Gameweeks between the claim and the first time he actually
            # started for this team. 0 means he started straight away.
            "gw_to_first_start": (first_start_gw - gameweek
                                  if first_start_gw is not None else ""),
        })

    baseline, wide = build_baseline(label, rows, league_id, player_gameweeks,
                                    fixtures, points, team_map, by_player)
    return events, baseline, wide, skipped


def build_baseline(label, rows, league_id, player_gameweeks, fixtures,
                   points, team_map, by_player):
    """Players who were free at their own kickoff and started anyway.

    The fair comparison for a pickup: not every unowned player, but the ones a
    manager could have had who went on to start a real match. Computed once per
    division, because a player rostered in one division is still free in all
    the others.
    """
    rosters = draft_rosters(league_id)
    kickoffs = sorted({f["kickoff_utc"] for f in fixtures})

    by_division = defaultdict(set)
    for team_id, team in team_map.items():
        by_division[team["division"]].add(team_id)
    if not by_division:
        by_division[""] = {r["team_id"] for r in rows if r["team_id"]}

    # Earliest kickoff per player-gameweek: a double gameweek has two, and
    # ownership is tested at the first chance the manager had to use him.
    first_kickoff = {}
    for player_id, matches in by_player.items():
        for match in matches:
            key = (player_id, match["gameweek"])
            current = first_kickoff.get(key)
            if current is None or match["kickoff"] < current[0]:
                first_kickoff[key] = (match["kickoff"], match["club"])

    started_pool, wide_pool = [], []
    for division, teams_wanted in sorted(by_division.items()):
        snapshots = ownership_at_kickoffs(rows, rosters, kickoffs, teams_wanted)
        for (player_id, gameweek), (kickoff, club) in sorted(first_kickoff.items()):
            scored = points.get((player_id, gameweek))
            if not scored:
                continue
            if player_id in snapshots.get(kickoff, {}):
                continue
            entry = {
                "league_label": label,
                "division": division,
                "gameweek": gameweek,
                "player_id": player_id,
                "player_name": scored["player_name"],
                "position": scored["position"],
                "club": club,
                "kickoff_utc": kickoff.isoformat(),
                "fpts": round(scored["fpts"], 2),
                "started": scored["started"],
                "starts": scored["starts"],
                "matches": scored["matches"],
                "minutes": scored["minutes"],
            }
            # Both pools, because they answer different questions and mixing
            # them up is the easiest mistake to make here.
            #
            #   started_pool  free at kickoff AND started. The brief's "fair
            #                 comparison pool" - what a good pickup looked
            #                 like.
            #   wide_pool     free at kickoff, started or not. The honest
            #                 denominator for "could the manager have done
            #                 better", because a manager choosing a free agent
            #                 does not know yet whether he will start. Comparing
            #                 an unconditional add against the started-only
            #                 pool stacks the deck against the add.
            wide_pool.append(entry)
            if scored["started"]:
                started_pool.append(entry)
    return started_pool, wide_pool


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def _summarise(rowset):
    scored = [e for e in rowset if e["gw_fpts"] != ""]
    if not scored:
        return None
    starts = sum(1 for e in scored if e["gw_started"] is True)
    mean = sum(float(e["gw_fpts"]) for e in scored) / len(scored)
    with_next4 = [e for e in scored if e["next4_fpts"] != ""]
    mean4 = (sum(float(e["next4_fpts"]) for e in with_next4) / len(with_next4)
             if with_next4 else 0.0)
    return len(scored), 100.0 * starts / len(scored), mean, mean4


def report(events, baseline, wide, skipped):
    total = len(events)
    print("\n--- buckets, by time to the next kickoff ------------------------")
    buckets = Counter(e["bucket"] for e in events)
    for name in BUCKETS:
        count = buckets.get(name, 0)
        print("  %-13s %5d  %5.1f%%" % (name, count,
                                        100.0 * count / total if total else 0))
    print("  %-13s %5d" % ("TOTAL", total))

    print("\nby Fantrax claim_type (WW = waiver claim, FA = free agent add):")
    for name in BUCKETS:
        kinds = Counter(e["claim_type"] for e in events if e["bucket"] == name)
        if kinds:
            print("  %-13s %s" % (name, dict(kinds)))

    print("\nstart rate and points, by bucket:")
    print("  %-13s %6s %8s %10s %9s" % ("bucket", "n", "start%", "mean FPts",
                                        "mean n4"))
    for name in BUCKETS:
        summary = _summarise([e for e in events if e["bucket"] == name])
        if summary:
            n, start_pct, mean, mean4 = summary
            print("  %-13s %6d %7.1f%% %10.2f %9.2f"
                  % (name, n, start_pct, mean, mean4))
        else:
            print("  %-13s %6d %8s %10s %9s" % (name, 0, "-", "-", "-"))

    # The brief's post_lock question, shown as a cut rather than a bucket.
    print("\nthe brief's `post_lock` idea, as a flag instead of a bucket:")
    missed = [e for e in events if e["missed_live_period"] is True]
    print("  adds that could not affect the period they landed in: %d of %d (%.1f%%)"
          % (len(missed), total, 100.0 * len(missed) / total if total else 0))
    print("  of those, more than 75 min from the next kickoff: %d"
          % sum(1 for e in missed if float(e["minutes_before_ko"]) > LOCK_MINUTES))
    print("  their bucket split:", dict(Counter(e["bucket"] for e in missed)))
    summary = _summarise(missed)
    if summary:
        n, start_pct, mean, mean4 = summary
        print("  they score: n=%d start%%=%.1f mean FPts=%.2f mean n4=%.2f"
              % (n, start_pct, mean, mean4))
    print("  if they were split out of bucket 1, waivers would fall from "
          "%d to %d" % (buckets.get("waivers", 0),
                        buckets.get("waivers", 0)
                        - sum(1 for e in missed if e["bucket"] == "waivers")))

    print("\nadds with no scoring row for the target gameweek: %d of %d"
          % (sum(1 for e in events if e["gw_fpts"] == ""), total))

    if skipped:
        print("\nclaims not counted as pickups:")
        for reason, count in skipped.most_common(10):
            print("   %-44s %d" % (reason, count))

    print("\nbaseline pool (free at kickoff, started in real life): %d "
          "player-gameweeks" % len(baseline))
    if baseline:
        mean = sum(b["fpts"] for b in baseline) / len(baseline)
        print("  mean FPts: %.2f" % mean)

    print("\nsample size checks:")
    per = Counter((e["bucket"], e["target_gameweek"]) for e in events)
    thin = [1 for (bucket, _), n in per.items()
            if bucket in ("lineup_snipe", "late_add") and n <= 2]
    print("  %d bucket/gameweek cells with 2 or fewer events" % len(thin))
    snipes = buckets.get("lineup_snipe", 0)
    if snipes < 100:
        print("  WARNING: %d lineup snipes in total. Too thin to cut by both "
              "position and season phase on this league alone." % snipes)


def write(path, fields, rows):
    PROCESSED.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print("wrote %s (%d rows)" % (path, len(rows)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", help="one league only")
    args = parser.parse_args()

    rows = load_transactions(args.label)
    if not rows:
        sys.exit("no transactions for label %r" % args.label)

    player_gameweeks = ref.load_player_gameweeks()
    fixtures = ref.load_fixtures(player_gameweeks)
    points = ref.points_index(player_gameweeks)
    by_player = player_fixtures(player_gameweeks, fixtures)
    print("reference: %d fixtures, %d player-gameweeks, %d scoring keys, "
          "%d players with fixtures"
          % (len(fixtures), len(player_gameweeks), len(points), len(by_player)))

    all_events, all_baseline, all_wide = [], [], []
    for label in sorted({r["league_label"] for r in rows}):
        subset = [r for r in rows if r["league_label"] == label]
        print("\n=== %s: %d transaction rows ===" % (label, len(subset)))
        events, baseline, wide, skipped = build(
            label, subset, player_gameweeks, fixtures, points, by_player)
        report(events, baseline, wide, skipped)
        all_events.extend(events)
        all_baseline.extend(baseline)
        all_wide.extend(wide)

    print()
    write(PROCESSED / "pickup_events.csv", EVENT_FIELDS, all_events)
    write(PROCESSED / "baseline_pool.csv", BASELINE_FIELDS, all_baseline)
    write(PROCESSED / "unowned_pool.csv", BASELINE_FIELDS, all_wide)


if __name__ == "__main__":
    main()
