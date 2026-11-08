# Pickup Timing Study, 25/26

Does it pay to snipe a lineup? This measures what every Fantrax pickup was
worth in 25/26, split by when it was made, across **22,488 pickups in 18
divisions** of two Premier League draft leagues.

| league | teams | divisions | transactions |
|---|---|---|---|
| International Genie League | 144 | 12 | 41,300 |
| DraftPL Community League | 72 | 6 | 27,633 |

Everything runs on a laptop with no credentials: Fantrax serves these
leagues' transaction history without a login.

```
python scraper/pull.py             # transactions, drafts, league metadata
python analysis/events.py          # buckets and the comparison pools
python analysis/metrics.py         # headline tables -> site/data
python analysis/build_site_data.py # the rest of the site's data
python analysis/build_embeds.py    # the standalone embeds
```

Add `--label igl` to any analysis script to work on one league.

## Two things this study does not claim

Worth stating first, because an earlier draft led on both and neither is a
finding.

**That pickups spike when lineups drop at KO−75.** They do — 1,241 in the
five minutes after the announcement against 126 in the five before, a 9.8×
jump. But every manager in these leagues already knows lineups land then,
because they are the ones doing it. Observing the behaviour describes the
hobby; it does not teach anybody anything.

**That sniped players start 97.7% of the time.** Tautological. You make the
pickup *because* you have seen he is starting, so the start rate is the
definition of the act rather than a benefit of it.

Both sit on the timing page as background. Everything below is what survives
once they are removed.

## What the data actually says

### The team sheet is free, and almost nobody converts it

Once lineups are out, "is he playing" is answered for everyone, and there are
on average **53 confirmed starters** sitting unowned. Which one you take is
the entire remaining decision.

Managers land at the **52nd percentile** of those confirmed starters — worth
**+0.72 FPts** over flipping a coin between them. That is the whole of
demonstrated selection skill in the most-watched five minutes of the fantasy
week.

The best starter available each week averaged 22.98 FPts against the 7.31
actually taken, but that gap is **ex-post regret, not achievable alpha**.
Nobody picks the week's 23-point haul every Saturday, and the confirmed
starter pool is wildly right-skewed (median 6.50, p90 16.75, p99 28.75). The
coin flip is the honest comparison.

### FAAB is a timing game, not a bidding game

| | GW1–6 | GW33–38 |
|---|---|---|
| mean bid | $10.56 | $0.30 |
| FPts per dollar | **0.68** | **12.96** |
| win rate | 29.5% | 38.8% |

**68% of the season's entire FAAB spend goes in the first six gameweeks**, at
the worst exchange rate of the year. The price of a contested player falls
from $8.47 to $0.78 across the season, and the share of contested auctions
won on a **$0** bid rises from 39.5% to 77.5%.

This is an equilibrium, not a free lunch: late bids are cheap *because*
everyone else has spent. The edge belongs to a manager who deviates from the
crowd, and exists only while most do not.

Within the bidding itself, win rate climbs to $4–9 and then **collapses** —
56.2% at $4–9, 39.3% at $10–24, 21.8% above $25. Not because a higher bid
loses an auction (the top bid wins by definition) but because a large bid
signals you are in a fight, and most people in a fight lose. Points per
gameweek held fall the whole way: 6.61 at $0 down to 4.63 at $25+.

### Buy early for quality, late for certainty

| bucket | n | start rate | mean FPts | if he started |
|---|---|---|---|---|
| waivers (>KO−75) | 16,555 | 66.7% | 6.12 | **8.38** |
| lineup snipe (KO−75 to −70) | 1,075 | 97.7% | 7.74 | 7.88 |
| late add (KO−70 to KO) | 4,858 | 98.0% | 7.07 | 7.19 |
| never picked up, free and started | 51,511 | — | — | 6.49 |

Sniping does not get a better footballer. It stops the transaction being
wasted — the bust rate (under 2 FPts) nearly halves, 32.5% to 17.4% — but
*conditional on starting*, the waiver add is the better player. Being first
inside the window is worth **+0.58 FPts** (±0.23, 2.6 SE): real, consistently
signed in both leagues, and small.

### Position, once scarcity is divided out

The obvious cut is by position and the obvious reading of it is wrong.

| position | % of **free** players who started | add start rate | lift |
|---|---|---|---|
| Defenders | 15.8% | 79.0% | +63.3 |
| Midfielders | 12.7% | 74.4% | +61.7 |
| Goalkeepers | 8.1% | 91.6% | +83.5 |
| Forwards | **4.9%** | 62.7% | +57.8 |

Only **1 in 20 free forwards started**, against 1 in 6 free defenders — real
teams field four defenders and one or two forwards, so spare forwards are
overwhelmingly bench players. Raw, the defender-forward start-rate gap is 16
points; as lift over each position's own free pool it is under six. **Roughly
three quarters of the apparent position effect was the pool, not the
manager.**

What survives is points, because both sides are confirmed starters so the
pool cannot explain it: a starting defender returns **8.79** against a
starting midfielder's **7.36**. Useful with one limit — if the hole in your
side is a midfielder, you cannot fill it with a defender.

### What eighteen divisions buy

Divisions are separate markets with separate managers, so the number of them
independently chasing one player in one gameweek is a real consensus signal,
and one a single league cannot produce.

| divisions chasing him | n | mean FPts | start rate |
|---|---|---|---|
| 1 | 1,578 | 5.17 | 60.8% |
| 4–6 | 619 | 6.45 | 70.0% |
| 7–11 | 299 | **6.86** | 71.9% |
| 12+ | 93 | 6.77 | 65.6% |

A player chased by seven to eleven divisions returned a third more than one
chased by a single division. It flattens at the top: the very most
fashionable pickups are no better than the merely popular.

## What the Fantrax API turned out to be

Probed live in November 2026. Each of these would have quietly corrupted the
study.

**No login is needed.** `fxpa/req` answers these leagues unauthenticated, in
the guest role, transaction history included. No cookie capture, no browser
collector, no local sink. (The 25/26 league ids themselves are not
recoverable from the API — they came from the League Archive in a logged-in
session, and are in `config.json`.)

**Timestamps are DST-aware US Eastern, not fixed EST.** The transaction table
header says `Date (EST)`, but the same league's scoring periods come back
`-0400` in September and `-0500` in January. Passing `tz` in the payload
changes nothing. Reading the label literally would shift every add by an hour
for most of the season, wrecking a five-minute bucket. Timestamps carry
minutes, never seconds.

**Drafts come one division at a time.** `getDraftResults` returns a single
division's 192 picks and does not say so. Unfixed, 3,264 drafted players
across the other divisions look like free agents from gameweek one. The
division list is inside the draft payload; each is requested by `divisionId`.

**Free agency is per division.** A player rostered in one division is free in
every other, so ownership is reconstructed separately for each of the 18 —
and contested auctions are grouped by division too. Pooling player-gameweeks
across divisions invents competition that never happened.

**A rate-limited request returns an empty body, not an error.** Every call is
guarded by a predicate confirming the response holds what was asked for.

**`getScores` and `getPlayerStats` silently ignore the season or period you
ask for** and serve the current one. Both are useless for a past season, so
kickoff times come from the FPL fixture list instead.

**The transaction column layout differs between leagues.** A FAAB league has
five columns (Team, Bid, Pr, Date, Gameweek), a simple-waivers league three,
and only a group's *lead* row aligns with the header. Lead rows are
identified by Fantrax's own `numInGroup`.

**Scoring rows are per match, not per gameweek.** In rescheduled weeks a
player has two rows under one gameweek number. Timing is matched to a single
fixture; value is summed across the gameweek.

**The player join is free.** Togga's CSVs carry the same Fantrax player ids,
wrapped in asterisks (`*02lk5*`). Strip them and the match is 100% — no name
matching anywhere. Kickoffs join to fixtures on the **ordered club pair**
rather than the gameweek, because a postponed match can sit in a different
gameweek for each source; all 380 matched.

## Two comparisons that are easy to get wrong

**The never-picked-up pool is conditioned on starting.** It is built from
players free at kickoff who *then started*, so it compares to a bucket's
"points once he started", never to its overall mean. Comparing it to the
unconditional figure stacks the deck against the pickup.

**Points while held grows with how long he was held.** It measures the length
of the spell, not the player — and hold length is itself partly sunk cost, a
manager who paid $25 being committed by the price. Only points *per gameweek
held* says anything about quality.

## Limits

- **One season**, two leagues, 18 divisions, every one of them 12 teams on
  broadly similar settings. Nothing is tested against another format.
- **Real Fantrax points, not lineup decisions.** Whether the manager actually
  started the player he picked up is out of scope; a pickup that scored 30 on
  your bench counts here as 30.
- **FAAB findings apply only to leagues using FAAB.** Three quarters of
  claims here were made at $0 on priority.
- **Sniping cannot be separated from the decision to snipe.** Managers who
  watch for team news may simply be better managers. None of this is a
  controlled experiment.
- Cells under 30 events are flagged wherever they appear.

## The site

Static, no backend. Python writes the numbers into `site/data/` and the pages
read them. Serve `site/` over any static server, or open `site/index.html`
straight from disk — it works either way.

**Data is emitted as `.js` assigning a global, not `.json`.** `fetch()` is
blocked on `file://` URLs, so a fetch-based page breaks when opened directly
or pasted into a Wix HTML block; a `<script src>` works everywhere. The
`.json` is written too, for anything external.

**Charts are hand-rolled in HTML and CSS**, not SVG or a charting library. A
responsive SVG scaled by its viewBox scales its own text with it, so an 11px
label on a 940px chart renders at about 4px on a phone. HTML bars keep every
label at a real size at any width, and nothing can be broken by a CDN being
blocked inside a sandboxed iframe.

**`site/embed/` is generated, not hand-written.** Each embed is a single
self-contained file — stylesheet, chart code and data inlined, fetching
nothing but two Google Fonts — so it can be iframed into Wix or pasted
wholesale into an HTML block. Generating them from the shared assets means
the site and the embeds cannot drift; change either and re-run
`build_embeds.py`.

Body copy is IBM Plex Sans and numbers are IBM Plex Mono: the house spec asks
for mono on data and labels, not on running prose, and mono is tiring in
bulk. Each page carries its own accent colour.

## Layout

```
config.json             leagues to pull, season, lock minutes
scraper/fantrax.py      API client; the notes above live in its docstring
scraper/pull.py         transactions + drafts + metadata, and a census
analysis/events.py      buckets, roster reconstruction, comparison pools
analysis/metrics.py     headline tables
analysis/explore.py     distributions, confidence intervals, counterfactuals
analysis/faab.py        the bidding market
analysis/faab_value.py  what a dollar buys, by season phase
analysis/scarcity.py    positional scarcity controls
analysis/speed.py       is being fast worth anything
analysis/supply.py      does the speed answer depend on pool depth
analysis/edge.py        what is left once the tautologies come out
site/                   the static site and the embeds
data/raw/               every raw API response (gitignored)
data/reference/         Togga scoring + FPL fixtures (gitignored, see below)
data/processed/         pickup_events.csv, baseline_pool.csv, analysis dumps
```

`data/reference/` is not committed — Togga's per-gameweek scoring is not ours
to redistribute. It needs `togga_2526_outfielders_clean.csv` and
`togga_2526_goalies_final.csv`, plus the FPL fixture list, which is public:

```
curl -o data/reference/fpl_fixtures_2025-26.csv \
  https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/2025-26/fixtures.csv
```

Nothing here ever writes to Fantrax. Every call is a read.
