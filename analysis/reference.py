"""Reference data: real kickoff times, and every player's gameweek scoring.

Two sources, joined on things that cannot drift:

  data/reference/fpl_fixtures_2025-26.csv   vaastav's FPL mirror. The only
                                            source of kickoff times - Fantrax's
                                            own getScores ignores seasonId and
                                            always serves the current season.
  data/reference/togga_2526_*.csv           per-gameweek Fantrax scoring, one
                                            row per player per gameweek.

How the fixture join works, and why it is done this way:

Togga already carries `Team`, `OpponentTeam`, `HomeAway` and `Gameweek`, in
Fantrax's own club codes, so the season's 380 fixtures can be rebuilt from it
directly. Joining those to FPL for a kickoff time on (gameweek, home, away)
would be wrong: a postponed match sits in a different gameweek for FPL than
for Togga, and Togga's per-gameweek counts show exactly that (GW33 holds 13
fixtures, GW34 holds 7). An **ordered club pair is unique across a season** -
20 clubs give 380 ordered pairs and the season has 380 matches - so the join
key is (home, away) alone, and the gameweek numbers are carried along as data
rather than used to match. Both numbers are kept, because they disagree.

Club codes agree between FPL and Fantrax for 18 of 20 clubs; the two that
differ are overridden below and the join is asserted to be complete, so a
future season's renaming fails loudly instead of silently dropping fixtures.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / "data" / "reference"

TOGGA_FILES = ("togga_2526_outfielders_clean.csv", "togga_2526_goalies_final.csv")
FIXTURES_FILE = "fpl_fixtures_2025-26.csv"

# FPL short_name -> Fantrax short name, for the two that disagree.
CLUB_OVERRIDES = {"BRE": "BRF", "NFO": "NOT"}


def _clean_id(value):
    """Togga wraps the Fantrax player id in asterisks: '*02lk5*' -> '02lk5'."""
    return (value or "").strip().strip("*")


def _number(value):
    text = (value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Player gameweeks
# --------------------------------------------------------------------------- #

def load_player_gameweeks():
    """[{player_id, name, club, opponent, home, gameweek, fpts, started, minutes, ...}]

    `started` is Togga's GS==1, which is a real start in the actual match - not
    a fantasy line-up decision. That is what the study measures against.
    """
    rows = []
    for filename in TOGGA_FILES:
        path = REFERENCE / filename
        if not path.exists():
            raise FileNotFoundError("missing reference file: %s" % path)
        keeper = "goalie" in filename
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                player_id = _clean_id(raw.get("ID"))
                gameweek = (raw.get("Gameweek") or "").strip()
                if not player_id or not gameweek.isdigit():
                    continue
                home_away = (raw.get("HomeAway") or "").strip().lower()
                rows.append({
                    "player_id": player_id,
                    "player_name": (raw.get("Player") or "").strip(),
                    "position": (raw.get("Position") or "").strip(),
                    "club": (raw.get("Team") or "").strip(),
                    "opponent": (raw.get("OpponentTeam") or "").strip(),
                    "home": home_away.startswith("h"),
                    "gameweek": int(gameweek),
                    "fpts": _number(raw.get("FPts")) or 0.0,
                    "games_played": _number(raw.get("GP")) or 0.0,
                    "started": (raw.get("GS") or "").strip() == "1",
                    "minutes": _number(raw.get("Min")) or 0.0,
                    "keeper": keeper,
                })
    return rows


# --------------------------------------------------------------------------- #
# Fixtures and kickoffs
# --------------------------------------------------------------------------- #

def _togga_fixtures(player_gameweeks):
    """{(home, away): togga_gameweek} rebuilt from the scoring rows."""
    fixtures = {}
    for row in player_gameweeks:
        club, opponent = row["club"], row["opponent"]
        if not club or not opponent:
            continue
        home, away = (club, opponent) if row["home"] else (opponent, club)
        fixtures[(home, away)] = row["gameweek"]
    return fixtures


def _fpl_kickoffs():
    """{(home, away): (kickoff_utc, fpl_event)} from vaastav's fixtures.csv."""
    teams_path = REFERENCE / FIXTURES_FILE
    if not teams_path.exists():
        raise FileNotFoundError("missing reference file: %s" % teams_path)

    # The FPL team ids in fixtures.csv are 1..20 in alphabetical order of the
    # club's FPL name, which is what teams.csv encodes. Rather than depend on a
    # second download, the mapping is pinned here and checked against the
    # fixture file's own id range.
    fpl_short = {
        1: "ARS", 2: "AVL", 3: "BUR", 4: "BOU", 5: "BRE", 6: "BHA", 7: "CHE",
        8: "CRY", 9: "EVE", 10: "FUL", 11: "LEE", 12: "LIV", 13: "MCI",
        14: "MUN", 15: "NEW", 16: "NFO", 17: "SUN", 18: "TOT", 19: "WHU",
        20: "WOL",
    }
    code = {num: CLUB_OVERRIDES.get(short, short) for num, short in fpl_short.items()}

    kickoffs = {}
    with teams_path.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            kickoff = (raw.get("kickoff_time") or "").strip()
            home_id = (raw.get("team_h") or "").strip()
            away_id = (raw.get("team_a") or "").strip()
            if not (kickoff and home_id.isdigit() and away_id.isdigit()):
                continue
            home = code.get(int(home_id))
            away = code.get(int(away_id))
            if not (home and away):
                raise ValueError("unmapped FPL team id in fixtures.csv: %s/%s"
                                 % (home_id, away_id))
            when = datetime.strptime(kickoff, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc)
            event = (raw.get("event") or "").strip()
            kickoffs[(home, away)] = (when, int(event) if event.isdigit() else None)
    return kickoffs


def load_fixtures(player_gameweeks=None):
    """[{home, away, kickoff_utc, togga_gameweek, fpl_event}] for the season.

    Raises if any fixture Togga knows about has no kickoff time, so a club
    renaming can never quietly shrink the fixture list.
    """
    player_gameweeks = player_gameweeks or load_player_gameweeks()
    togga = _togga_fixtures(player_gameweeks)
    kickoffs = _fpl_kickoffs()

    missing = sorted(pair for pair in togga if pair not in kickoffs)
    if missing:
        raise ValueError(
            "%d fixture(s) in the scoring data have no FPL kickoff time, "
            "first few: %s" % (len(missing), missing[:5]))

    fixtures = []
    for (home, away), togga_gameweek in sorted(togga.items()):
        when, event = kickoffs[(home, away)]
        fixtures.append({
            "home": home, "away": away,
            "kickoff_utc": when,
            "togga_gameweek": togga_gameweek,
            "fpl_event": event,
        })
    return fixtures


def club_fixtures(fixtures):
    """{club: [fixture, ...]} in kickoff order, each club's own matches."""
    by_club = {}
    for fixture in fixtures:
        for club in (fixture["home"], fixture["away"]):
            by_club.setdefault(club, []).append(fixture)
    for matches in by_club.values():
        matches.sort(key=lambda f: f["kickoff_utc"])
    return by_club


def points_index(player_gameweeks):
    """{(player_id, gameweek): totals} for scoring lookups.

    Togga holds **one row per match, not per gameweek**. In a rescheduled week
    a player can therefore appear twice under the same gameweek number - 460
    such rows exist in 25/26 - and `GS` is per match, only ever 0 or 1. So the
    rows are summed here rather than indexed, which is also what the brief asks
    for: timing is matched to a single fixture, but value is measured across
    the whole gameweek.
    """
    index = {}
    for row in player_gameweeks:
        key = (row["player_id"], row["gameweek"])
        total = index.get(key)
        if total is None:
            index[key] = {
                "player_id": row["player_id"],
                "player_name": row["player_name"],
                "position": row["position"],
                "gameweek": row["gameweek"],
                "fpts": row["fpts"],
                "games_played": row["games_played"],
                "starts": 1 if row["started"] else 0,
                "started": row["started"],
                "minutes": row["minutes"],
                "matches": 1,
                "clubs": {row["club"]},
            }
        else:
            total["fpts"] += row["fpts"]
            total["games_played"] += row["games_played"]
            total["starts"] += 1 if row["started"] else 0
            total["started"] = total["started"] or row["started"]
            total["minutes"] += row["minutes"]
            total["matches"] += 1
            total["clubs"].add(row["club"])
    return index
