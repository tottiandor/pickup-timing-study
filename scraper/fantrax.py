"""Fantrax read-only data access for the pickup timing study.

READ-ONLY. Every call here is a read. Nothing sets a line-up, makes a claim,
touches a roster or changes a league setting, and nothing ever should.

What we learned probing the API (2026-11-07), so nobody has to re-learn it:

  fxea/general/getLeagueInfo   public GET. Division membership, team names, and
                               the league's scoring periods with *explicit UTC
                               offsets*.
  fxpa/req                     the site's own JSON-RPC endpoint. It answers
                               these leagues **unauthenticated** (guest role),
                               transaction history included. No cookie, no
                               browser console, no sink helper.

Three traps, all confirmed by experiment:

1. Timestamps are US Eastern *wall clock*, and DST-aware. The transaction table
   header says "Date (EST)" but the scoring periods come back -0400 in
   September and -0500 in January, so it is America/New_York, not a fixed
   UTC-5. Passing `tz` in the payload changes nothing - the server ignores it.
   Getting this wrong shifts every add by an hour, which would wreck a
   five-minute bucket, so `to_utc` below goes through zoneinfo.

2. Transaction timestamps carry minutes, never seconds.

3. A rate-limited request comes back **empty rather than as an error**, so a
   response is only accepted once `want` confirms it holds what was asked for.
   `getScores` is worse: it ignores `seasonId` and silently serves the current
   season, so it is useless for a past season and is not used here.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

FXEA = "https://www.fantrax.com/fxea/general/"
FXPA = "https://www.fantrax.com/fxpa/req"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://www.fantrax.com/",
}
TIMEOUT = 45
EASTERN = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #

def parse_tx_datetime(text):
    """'Sun May 24, 2026, 10:29AM' -> aware datetime in US Eastern.

    This is the format of the transaction table's date cell. Minute precision
    is all Fantrax gives.
    """
    if not text:
        return None
    try:
        naive = datetime.strptime(text.strip(), "%a %b %d, %Y, %I:%M%p")
    except ValueError:
        return None
    return naive.replace(tzinfo=EASTERN)


def to_utc(dt):
    return dt.astimezone(UTC) if dt else None


def parse_iso(value):
    """'2025-09-12T06:00:00.0-0400' -> aware datetime."""
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #

class Fantrax:
    def __init__(self, sleep_seconds=0.4, raw_dir=None):
        self.sleep = sleep_seconds
        self.raw_dir = Path(raw_dir) if raw_dir else None
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.errors = []

    # -- raw cache ---------------------------------------------------------- #
    def _raw_path(self, name):
        return (self.raw_dir / (name + ".json")) if self.raw_dir else None

    def _cached(self, name):
        path = self._raw_path(name)
        if path and path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return None
        return None

    def _store(self, name, body):
        path = self._raw_path(name)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(body), encoding="utf-8")

    # -- calls -------------------------------------------------------------- #
    def call(self, method, data=None, league_id=None, want=None,
             cache_as=None, refresh=False, tries=4):
        """One fxpa method. Returns the response `data`, or None.

        `want(d)` guards against the empty body a rate-limited request gets.
        """
        if cache_as and not refresh:
            hit = self._cached(cache_as)
            if hit is not None:
                return hit

        payload_data = dict(data or {})
        if league_id:
            payload_data["leagueId"] = league_id
        body = {
            "msgs": [{"method": method, "data": payload_data}],
            # no client version 'v': a pinned one goes stale and Fantrax
            # refuses the call outright (STALE_CLIENT)
            "uiv": 3, "refUrl": "https://www.fantrax.com/", "dt": 0, "at": 0,
        }
        params = {"leagueId": league_id} if league_id else {}

        for attempt in range(tries):
            try:
                response = self.session.post(FXPA, params=params, json=body,
                                             timeout=TIMEOUT)
                response.raise_for_status()
                parsed = response.json()
                page_error = parsed.get("pageError")
                if page_error:
                    raise RuntimeError("pageError %s" % page_error.get("code"))
                got = (parsed.get("responses") or [{}])[0].get("data")
                if got and (want is None or want(got)):
                    time.sleep(self.sleep)
                    if cache_as:
                        self._store(cache_as, got)
                    return got
            except Exception as exc:
                if "NOT_LOGGED_IN" in str(exc):
                    self.errors.append("%s: not logged in" % method)
                    return None
            time.sleep(self.sleep * (attempt + 2) * 2)

        self.errors.append("%s %s gave nothing back"
                           % (method, json.dumps(data or {})[:100]))
        return None

    def league_info(self, league_id, refresh=False):
        """Public GET: divisions, team names, scoring periods with offsets."""
        name = "league_info_%s" % league_id
        if not refresh:
            hit = self._cached(name)
            if hit is not None:
                return hit
        response = self.session.get(FXEA + "getLeagueInfo",
                                    params={"leagueId": league_id}, timeout=TIMEOUT)
        response.raise_for_status()
        info = response.json()
        self._store(name, info)
        time.sleep(self.sleep)
        return info

    # -- transaction history ------------------------------------------------ #
    def transactions(self, league_id, view="CLAIM_DROP", per_page=500,
                     refresh=False, max_pages=200):
        """Every transaction row for a league, newest first.

        `team: 'ALL'` is required - without it the history comes back for a
        single team only.

        Row shape quirk: a claim and the drop that paid for it share a
        `txSetId`, and only the *first* row of the group carries the Team and
        Date cells. The paired row's cells hold the gameweek alone. So team and
        timestamp are carried across the group here, rather than read per row.
        """
        rows, page, pages = [], 1, 1
        while page <= pages and page <= max_pages:
            got = self.call(
                "getTransactionDetailsHistory",
                {"view": view, "team": "ALL", "executedOnly": False,
                 "maxResultsPerPage": per_page, "pageNumber": page},
                league_id=league_id,
                want=lambda d: "table" in d or "paginatedResultSet" in d,
                cache_as="tx_%s_%s_p%03d" % (league_id, view.lower(), page),
                refresh=refresh,
            )
            if not got or "table" not in got:
                break
            pages = (got.get("paginatedResultSet") or {}).get("totalNumPages") or 1
            header = [cell.get("shortName") or cell.get("name")
                      for cell in ((got["table"].get("header") or {}).get("cells") or [])]
            rows.extend(self._shape(got["table"].get("rows") or [], header, view))
            page += 1
        return rows

    @staticmethod
    def _shape(raw_rows, header, view):
        """Flatten table rows, carrying team/date across a transaction group.

        The column layout is **not the same in every league**, which is the
        trap here. A league with FAAB waivers has five columns - Team, Bid, Pr,
        Date, Gameweek - and one with simple waivers has three. So every lookup
        goes through the header rather than a fixed position.

        Worse, only a group's *lead* row has cells that line up with the header
        at all. A continuation row (the drop that paid for a claim) carries a
        misaligned subset: `['', '1', '35']` against a five-column header. So
        nothing positional is read from a continuation row. Fantrax marks the
        lead row by putting `numInGroup` on it and leaving it off the rest,
        which is the signal used below; a full-width row counts as a lead too.

        A trade is the one exception: its From and To cells are present on
        every row of the group, left-aligned, because a multi-team trade moves
        different assets between different pairs of teams.
        """
        index = {name: i for i, name in enumerate(header) if name}
        i_team = index.get("Team")
        i_from, i_to = index.get("From"), index.get("To")
        i_date = next((i for name, i in index.items() if name.startswith("Date")), None)
        i_gw = index.get("Gameweek")
        i_bid = index.get("Bid")

        def cell(cells, position):
            if position is None or position >= len(cells):
                return None
            return cells[position]

        shaped, group = [], {}
        for raw in raw_rows:
            raw_cells = raw.get("cells") or []
            cells = [c.get("content") for c in raw_cells]
            team_ids = [c.get("teamId") for c in raw_cells if c.get("teamId")]
            set_id = raw.get("txSetId")

            is_lead = raw.get("numInGroup") is not None or len(cells) == len(header)
            if is_lead:
                group[set_id] = {
                    "team_name": cell(cells, i_team),
                    "team_id": team_ids[0] if team_ids else None,
                    "stamp": cell(cells, i_date),
                    "gameweek": cell(cells, i_gw),
                    "bid": cell(cells, i_bid),
                }
            lead = group.get(set_id, {})

            # From/To are per-row on a trade, and left-aligned even when the
            # Date column is omitted.
            from_name = cell(cells, i_from) if i_from is not None else None
            to_name = cell(cells, i_to) if i_to is not None else None
            from_id = raw_cells[i_from].get("teamId") if (
                i_from is not None and i_from < len(raw_cells)) else None
            to_id = raw_cells[i_to].get("teamId") if (
                i_to is not None and i_to < len(raw_cells)) else None

            scorer = raw.get("scorer") or {}
            stamp = lead.get("stamp")
            eastern = parse_tx_datetime(stamp)

            # A trade row has no transactionCode of its own.
            code = raw.get("transactionCode") or ("TRADE" if view == "TRADE" else None)
            # The acquiring team is the one a traded player ends up on.
            team_id = to_id or lead.get("team_id") or (team_ids[0] if team_ids else None)

            shaped.append({
                "view": view,
                "tx_set_id": set_id,
                "transaction_code": code,
                "transaction_type": raw.get("transactionType") or (
                    "Trade" if view == "TRADE" else None),
                "claim_type": raw.get("claimType") or "",
                "result_code": raw.get("resultCode"),
                "executed": bool(raw.get("executed")),
                "deleted": bool(raw.get("deleted")),
                "num_in_group": raw.get("numInGroup"),
                "bid": lead.get("bid") or "",
                "team_name": to_name or lead.get("team_name"),
                "team_id": team_id,
                "from_team_name": from_name,
                "from_team_id": from_id,
                "to_team_name": to_name,
                "to_team_id": to_id,
                "stamp_raw": stamp,
                "stamp_eastern": eastern.isoformat() if eastern else None,
                "stamp_utc": to_utc(eastern).isoformat() if eastern else None,
                "league_gameweek": lead.get("gameweek"),
                # A trade can move a budget amount rather than a player; such a
                # row has no scorer and must not become a roster move.
                "is_player_move": bool(scorer.get("scorerId")),
                "player_id": scorer.get("scorerId"),
                "player_name": scorer.get("name"),
                "player_pos": scorer.get("posShortNames"),
                "club_id": scorer.get("teamId"),
                "club": scorer.get("teamShortName"),
            })
        return shaped

    def draft_results(self, league_id, division_id=None, refresh=False):
        """One division's draft, or the league's default division.

        A multi-division league does **not** return every pick at once: the
        call answers with a single division's 192 and nothing says so. Without
        `divisionId` the other divisions' drafts are silently missing, which
        would leave every drafted player outside one division looking like a
        free agent from gameweek one.
        """
        data = {"divisionId": division_id} if division_id else {}
        name = ("draft_%s_%s" % (league_id, division_id) if division_id
                else "draft_%s" % league_id)
        return self.call("getDraftResults", data, league_id=league_id,
                         cache_as=name, refresh=refresh)

    def divisions(self, league_id, refresh=False):
        """[{id, name}] for the league. Empty list for a single-division one.

        The division list is only exposed inside the draft payload, not by
        getLeagueInfo, which gives division names on teams but no ids.
        """
        payload = self.draft_results(league_id, refresh=refresh) or {}
        return [d for d in (payload.get("divisions") or []) if d.get("id")]

    def standings(self, league_id, refresh=False):
        return self.call("getStandings", {"view": "ALL"}, league_id=league_id,
                         want=lambda d: d.get("tableList"),
                         cache_as="standings_%s" % league_id, refresh=refresh)


# --------------------------------------------------------------------------- #
# Shaping league metadata
# --------------------------------------------------------------------------- #

def scoring_periods(info):
    """[{number, start, end}] with aware datetimes, from getLeagueInfo.

    These are the league's own periods. They are NOT EPL gameweeks: a league
    can start late or run a custom calendar (the pilot has 35 periods opening
    on 12 September), so the 'Gameweek' column in the transaction table is a
    league period number and must never be assumed to equal an FPL gameweek.
    """
    periods = []
    for period in info.get("scoringPeriods") or []:
        periods.append({
            "number": period.get("number"),
            "start": parse_iso(period.get("startDate")),
            "end": parse_iso(period.get("endDate")),
        })
    return sorted((p for p in periods if p["number"]), key=lambda p: p["number"])


def teams(info):
    """{team_id: {name, division}}"""
    return {
        tid: {"name": team.get("teamName") or team.get("name"),
              "division": team.get("division") or ""}
        for tid, team in (info.get("teamInfo") or {}).items()
        if isinstance(team, dict)
    }
