# Pickup Timing Study, 25/26 (IGL + Community League)

Brief for Claude Code. Read this whole file before writing any code.

## Goal

Measure the fantasy value of player pickups in 25/26 Fantrax EPL draft leagues, split by **when** the pickup happened. If the findings hold up, they become a The Draft Society article, with the visuals hosted on a GitHub Pages site and embedded into Wix.

## The four buckets

All times are relative to the kickoff (KO) of the player's next real fixture. Lineups are announced at KO minus 75 minutes.

| Bucket | Definition |
|---|---|
| 1. Waivers | Added before KO-75 (see open question below) |
| 2. Lineup snipe | Added between KO-75 and KO-70 |
| 3. Late add | Added between KO-70 and KO |
| 4. Never picked up | Free agent at KO in that division, not added |

Leagues use **per-game lineup locks**, so a player added in buckets 2 and 3 can still be started that gameweek.

**Open question to confirm with András before finalising:** Fantrax has both waiver claims and free agent adds made earlier in the week, before lineups are out. Default: bucket 1 includes both, with a `claim_type` column (`waiver` / `fa_early`) so they can be split later. Adds made after the player's game kicked off go into a separate `post_lock` bucket and are reported apart, not mixed into bucket 1.

## Step 1: Scraper

Reuse the existing scraper from the **Rock You Like a Harry Kane history site**. Ask András for the folder path if it is not already open. Adapt it rather than writing from scratch.

What it needs to pull for each 25/26 league (IGL and Community League, every division):

- Full transaction history: player ID, player name, team ID, team name, division, type (claim / FA add / drop / trade), and the exact timestamp.
- Draft results (needed to reconstruct rosters from GW1).
- Team-to-division mapping.

Notes from earlier Fantrax work:

- Internal API is `fxpa/req`, cookie-authenticated. External `fxea` is public GET but returns thinner data.
- `STALE_CLIENT` errors were fixed by stripping app version fields from the payload.
- Save every raw JSON response to `data/raw/` before parsing, so parsing bugs never force a re-pull.
- Rate limit politely (sleep between calls) and make the scraper resumable.
- In Fantrax, divisions may sit inside a single league ID (IGL came through as one league with 143 teams). Confirm this for both leagues.
- Credentials (cookies, any user secret) go in a `.env` file that is in `.gitignore`. Nothing secret gets committed, since this repo will be public.

**Timezone check:** confirm what timezone Fantrax returns timestamps in. Sanity test against a known event (for example, a burst of adds just after a team's lineup dropped) before bucketing anything.

## Step 2: Reference data

- **Kickoff times, 25/26:** pull from the vaastav Fantasy-Premier-League repo on GitHub (`data/2025-26/fixtures.csv`, `kickoff_time` in UTC). Map FPL team IDs to club names.
- **Player gameweek points:** András will copy these from the TDS project into `data/reference/`:
  - `togga_2526_outfielders_clean.csv` (per-gameweek outfielders, `GS==1` flags a real start)
  - `togga_2526_goalies_final.csv`
  - `players_gameweek.csv` (multi-season, filter `season` to 25/26) as a cross-check
- Join on Fantrax player ID wherever possible. If a name join is ever needed: Unicode NFKD to ASCII, strip non-letters, lowercase. Report the join rate and list every unmatched player.
- Fantrax CSVs have duplicate column names (CF, CS). Pandas renames them CF.1 / CS.1. That is expected.

## Step 3: Build the event table

1. Reconstruct each division's rosters over time: draft, then apply adds, drops and trades in timestamp order.
2. For every add, find the player's next fixture after the add timestamp and compute `minutes_before_ko`. Assign the bucket.
3. Double gameweeks: match to the next fixture, but measure gameweek points across the whole gameweek.
4. For bucket 4, for every division and gameweek, list players who were free agents at their kickoff, were not added, and started in real life (`GS==1`). This is the fair comparison pool. Also keep the wider "all unowned" pool as a secondary view.

Output: `data/processed/pickup_events.csv` with one row per add, plus `data/processed/baseline_pool.csv`.

## Step 4: Value metrics

We are **not** using fantasy lineup history. Value is the player's real Fantrax points:

- Points in the target gameweek.
- Whether the player actually started in real life that gameweek (start rate per bucket).
- Points over the next 4 gameweeks.
- Points while on that team's roster (add to drop, using drop timestamps).

Report each two ways:

- **Per event** (every add counts).
- **Per unique player-gameweek** (if 40 divisions all snipe the same player, that is really one decision, so dedupe and weight).

Useful cuts: by position, by gameweek / season phase, by league (IGL vs Community), and a histogram of add timing in minutes before KO.

Scoring reminders: PPS is points in `GS==1` rows divided by number of `GS==1` rows. Any category breakdown must reconcile exactly to FPts.

## Step 5: GitHub Pages site

Static site, no backend. Python builds JSON into `site/data/`, the pages read it.

**Style (TDS house look):** black and gold floodlit-matchday palette, Anton for headings, IBM Plex Mono for body and numbers. Charts must work as embeds at **940px max width** for Wix iframes, and stay readable on mobile.

Suggested pages:

- `index.html`: headline findings, one chart per bucket comparison.
- `timing.html`: distribution of adds by minutes before KO, with the 75/70 minute marks.
- `positions.html`: bucket results by position.
- `season.html`: how pickup value changes across the season.
- `best-pickups.html`: sortable table of the most valuable pickups per bucket.
- `method.html`: definitions, data sources, caveats.

Each chart should also exist as a standalone page so it can be iframed into Wix on its own.

Charting: Chart.js or Observable Plot from a pinned CDN version.

## Repo layout

```
pickup-study/
  .env              (gitignored)
  scraper/
  analysis/
  data/raw/         (gitignored if large)
  data/reference/
  data/processed/
  site/
  README.md
```

## Working rules

- Check in with András after Step 1 (show transaction counts per division and the timezone check) and after Step 3 (show bucket counts) before building the site.
- Never invent numbers. If a figure does not reconcile, find the cause instead of adding a caveat.
- Flag anything that looks like a sample size problem (for example, bucket 2 being tiny in some gameweeks).
