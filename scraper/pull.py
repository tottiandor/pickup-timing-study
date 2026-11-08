#!/usr/bin/env python3
"""Pull every league's transaction history, draft and metadata into data/raw/.

    python scraper/pull.py                 # every league in config.json
    python scraper/pull.py --label igl
    python scraper/pull.py --refresh       # ignore the raw cache and re-fetch

Runs entirely on this computer. Fantrax answers these leagues without a login
(see scraper/fantrax.py for what that cost to work out), so there is no cookie
to capture and no browser step. Every raw response is written to data/raw/
before anything is parsed, so a parsing bug never forces a re-pull, and a
re-run reuses what is already on disk unless --refresh says otherwise.

It prints a summary table and writes data/processed/transactions.csv. Nothing
is written to Fantrax at any point - every call is a read.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import fantrax  # noqa: E402

RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

FIELDS = [
    "league_label", "league_id", "division", "view", "tx_set_id",
    "transaction_code", "transaction_type", "claim_type", "result_code",
    "executed", "deleted", "num_in_group", "bid", "team_id", "team_name",
    "from_team_id", "from_team_name", "to_team_id", "to_team_name",
    "stamp_raw", "stamp_eastern", "stamp_utc", "league_gameweek",
    "is_player_move", "player_id", "player_name", "player_pos",
    "club_id", "club",
]


def pull_league(api, league, refresh=False):
    """Everything for one league. Returns (rows, report)."""
    label, league_id = league["label"], league["leagueId"]
    print("\n=== %s (%s) ===" % (label, league_id), flush=True)

    info = api.league_info(league_id, refresh=refresh)
    team_map = fantrax.teams(info)
    periods = fantrax.scoring_periods(info)
    divisions = sorted({t["division"] for t in team_map.values() if t["division"]})

    print("  league name      : %s" % info.get("leagueName"))
    print("  teams            : %d" % len(team_map))
    print("  divisions        : %d %s" % (len(divisions), divisions[:6] or "(none)"))
    if periods:
        print("  scoring periods  : %d, GW1 opens %s, last closes %s"
              % (len(periods), periods[0]["start"], periods[-1]["end"]))

    rows = []
    for view in ("CLAIM_DROP", "TRADE"):
        got = api.transactions(league_id, view=view,
                               per_page=league.get("perPage", 500), refresh=refresh)
        print("  %-11s rows : %d" % (view, len(got)), flush=True)
        rows.extend(got)

    # Stamp each row with its league and the team's division.
    for row in rows:
        row["league_label"] = label
        row["league_id"] = league_id
        row["division"] = (team_map.get(row.get("team_id")) or {}).get("division", "")

    # Drafts come back one division at a time, so every division is asked for
    # by id. Without this a 12-division league keeps one division's draft and
    # treats 2,112 drafted players as free agents.
    division_list = api.divisions(league_id, refresh=refresh)
    picks_total, drafts_got = 0, 0
    if division_list:
        for division in division_list:
            payload = api.draft_results(league_id, division_id=division["id"],
                                        refresh=refresh)
            picks = len((payload or {}).get("draftPicksOrdered") or [])
            picks_total += picks
            drafts_got += 1 if picks else 0
        print("  drafts           : %d of %d divisions, %d picks"
              % (drafts_got, len(division_list), picks_total))
        if drafts_got != len(division_list):
            print("  WARNING: %d division draft(s) missing"
                  % (len(division_list) - drafts_got))
    else:
        payload = api.draft_results(league_id, refresh=refresh)
        picks_total = len((payload or {}).get("draftPicksOrdered") or [])
        print("  draft results    : %s (%d picks)"
              % ("yes" if payload else "MISSING", picks_total))

    report = {
        "label": label,
        "league_id": league_id,
        "name": info.get("leagueName"),
        "teams": len(team_map),
        "divisions": divisions,
        "periods": len(periods),
        "rows": len(rows),
        "division_count": len(division_list),
        "draft_picks": picks_total,
    }
    return rows, report


def census(rows):
    """The counts worth eyeballing before any bucketing happens."""
    print("\n--- transaction census -----------------------------------------")
    by_code = Counter(r["transaction_code"] for r in rows)
    print("by transaction_code :", dict(by_code))

    claims = [r for r in rows if r["transaction_code"] == "CLAIM"]
    print("claim_type on claims:", dict(Counter(r["claim_type"] for r in claims)))
    print("result_code on claims:", dict(Counter(r["result_code"] for r in claims)))

    trades = [r for r in rows if r["view"] == "TRADE"]
    if trades:
        movers = [r for r in trades if r["is_player_move"]]
        print("trade rows: %d, of which player moves: %d (rest are budget)"
              % (len(trades), len(movers)))

    # Every row must end up with a team and a timestamp, or the roster
    # reconstruction is quietly wrong. The parser carries both across a
    # transaction group, so a gap here means the lead-row detection failed.
    missing = [r for r in rows if not r["stamp_utc"]]
    print("rows with no usable timestamp: %d of %d" % (len(missing), len(rows)))
    if missing:
        sample = Counter((r["transaction_code"], r["view"]) for r in missing)
        print("  (by code/view):", dict(sample))

    no_team = [r for r in rows if not r["team_id"]]
    print("rows with no team: %d" % len(no_team))
    if no_team:
        print("  (by code/view):",
              dict(Counter((r["transaction_code"], r["view"]) for r in no_team)))

    stamped = [r for r in rows if r["stamp_utc"]]
    if stamped:
        order = sorted(r["stamp_utc"] for r in stamped)
        print("timestamp range (UTC): %s  ->  %s" % (order[0], order[-1]))

    # Minute precision means a tie-break is sometimes needed; worth knowing how often.
    per_minute = Counter(r["stamp_utc"] for r in stamped)
    ties = sum(1 for n in per_minute.values() if n > 1)
    print("distinct minutes: %d, minutes holding >1 row: %d" % (len(per_minute), ties))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", help="pull one league only")
    parser.add_argument("--refresh", action="store_true",
                        help="ignore the raw cache and re-fetch")
    args = parser.parse_args()

    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    leagues = config["leagues"]
    if args.label:
        leagues = [lg for lg in leagues if lg["label"] == args.label]
        if not leagues:
            sys.exit("no league labelled %r in config.json" % args.label)

    api = fantrax.Fantrax(sleep_seconds=config.get("sleepSeconds", 0.4), raw_dir=RAW)

    all_rows, reports = [], []
    for league in leagues:
        rows, report = pull_league(api, league, refresh=args.refresh)
        all_rows.extend(rows)
        reports.append(report)

    census(all_rows)

    PROCESSED.mkdir(parents=True, exist_ok=True)
    out = PROCESSED / "transactions.csv"
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
    print("\nwrote %s (%d rows)" % (out, len(all_rows)))

    (PROCESSED / "pull_report.json").write_text(
        json.dumps(reports, indent=1), encoding="utf-8")

    if api.errors:
        print("\n%d errors:" % len(api.errors))
        for err in api.errors[:20]:
            print("  ", err)


if __name__ == "__main__":
    main()
