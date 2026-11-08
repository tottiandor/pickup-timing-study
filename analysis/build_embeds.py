#!/usr/bin/env python3
"""Generate the standalone chart embeds in site/embed/.

    python analysis/build_embeds.py

Each output is a **single self-contained HTML file**: the stylesheet, the
chart code and the data are all inlined, and the only thing it fetches is the
two Google Fonts. That is deliberate, and it is what the house embed spec
asks for - these get iframed into Wix, or pasted straight into a Wix HTML
block, and a file that reaches out for `assets/tds.css` or `data/metrics.js`
works in neither case. `fetch()` is blocked on file:// URLs too, so inlining
is the only approach that works everywhere.

The duplication between embeds is the point: one file, no dependencies, drop
it anywhere. They are generated rather than hand-maintained so the site and
the embeds can never drift apart - edit assets/tds.css or assets/tds.js and
re-run this.
"""
from __future__ import annotations

import io
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
OUT = SITE / "embed"

FONTS = ("https://fonts.googleapis.com/css2?family=Anton&"
         "family=IBM+Plex+Mono:wght@400;500;600&"
         "family=IBM+Plex+Sans:wght@400;500;600&display=swap")

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="%(fonts)s" rel="stylesheet">
<style>
%(css)s
</style>
</head>
<body class="embed">
<div class="wrap">
  <div class="panel">
    <div class="panel-head">
      <h3>%(heading)s</h3>
      <span class="note">%(subhead)s</span>
    </div>
    <div id="chart"></div>
    %(footnote)s
  </div>
</div>
<script>%(metrics)s</script>
<script>%(extra)s</script>
<script>%(js)s</script>
<script>
(function () {
  var m = window.PICKUP_METRICS, x = window.PICKUP_EXTRA;
  try {
%(render)s
  } catch (e) { TDS.fail(document.getElementById('chart'), e.message); }
}());
</script>
</body>
</html>
"""

EMBEDS = [
    {
        "file": "cliff.html",
        "title": "The lineup cliff",
        "heading": "When pickups happen",
        "subhead": "minutes before kickoff, final three hours",
        "footnote": "The gold column is the five minutes after team sheets "
                    "are published at KO-75.",
        "render": "    TDS.renderTiming(document.getElementById('chart'), m, "
                  "{ upTo: 180 });",
    },
    {
        "file": "buckets.html",
        "title": "What sniping buys",
        "heading": "Start rate by when you picked him up",
        "subhead": "did he start a real match?",
        "footnote": "A sniped player starts almost every time. A waiver add "
                    "is closer to two in three.",
        "render": """    var d = x.distributions;
    TDS.bars(document.getElementById('chart'), {
      max: 100,
      rows: TDS.BUCKETS.map(function (b) {
        return { label: TDS.SHORT[b], note: 'n=' + TDS.thousands(d[b].n),
          bars: [{ key: b, value: d[b].start_rate,
            display: TDS.pct(d[b].start_rate) }] };
      })
    });""",
    },
    {
        "file": "quality.html",
        "title": "Points once he started",
        "heading": "Points, once he actually started",
        "subhead": "the quality you got, not the odds he played",
        "footnote": "Waiver adds are the better footballers. Sniping buys "
                    "certainty, not quality. The never-picked-up line is "
                    "every player free at kickoff who started anyway.",
        "render": """    var d = x.distributions;
    var rows = TDS.BUCKETS.map(function (b) {
      return { label: TDS.SHORT[b], note: 'n=' + TDS.thousands(d[b].n),
        bars: [{ key: b, value: d[b].mean_if_started,
          display: TDS.num(d[b].mean_if_started) }] };
    });
    rows.push({ label: TDS.SHORT.baseline,
      note: 'n=' + TDS.thousands(m.baseline.n) + ', free and started',
      bars: [{ key: 'baseline', value: m.baseline.mean_fpts,
        display: TDS.num(m.baseline.mean_fpts), faint: true }] });
    TDS.bars(document.getElementById('chart'), { rows: rows });""",
    },
    {
        "file": "positions.html",
        "title": "Sniping by position",
        "heading": "Start rate by position",
        "subhead": "blue waivers, gold snipe, red late add",
        "footnote": "Goalkeepers barely rotate, so there is nothing to wait "
                    "for. Forwards are close to a coin flip, which is where "
                    "the team sheet is worth most.",
        "render": """    var NAME = { G: 'Goalkeepers', D: 'Defenders', M: 'Midfielders',
      F: 'Forwards' };
    TDS.bars(document.getElementById('chart'), {
      max: 100, showKeys: true,
      rows: ['G', 'D', 'M', 'F'].filter(function (p) {
        return m.by_position[p];
      }).map(function (p) {
        return { label: NAME[p], bars: TDS.BUCKETS.map(function (b) {
          var c = m.by_position[p][b];
          return { key: b, value: c ? c.start_rate : null,
            display: c ? TDS.pct(c.start_rate) : '--',
            thin: c && c.n < 30 };
        }) };
      })
    });""",
    },
    {
        "file": "faab.html",
        "title": "What a FAAB bid buys",
        "heading": "Share of waiver claims won, by bid",
        "subhead": "all claims, won and lost",
        "footnote": "Money works up to about $9 and then goes into reverse. "
                    "A big bid is a signal you are in a fight, and most "
                    "people in a fight lose it.",
        "render": """    var best = x.faab.tiers.reduce(function (a, b) {
      return b.win_rate > a.win_rate ? b : a;
    });
    TDS.bars(document.getElementById('chart'), {
      max: 70,
      rows: x.faab.tiers.map(function (t) {
        return { label: t.tier, note: 'n=' + TDS.thousands(t.claims),
          bars: [{ key: t === best ? 'lineup_snipe' : 'waivers',
            value: t.win_rate, display: TDS.pct(t.win_rate) }] };
      })
    });""",
    },
    {
        "file": "consensus.html",
        "title": "What the crowd knows",
        "heading": "Divisions chasing the same player",
        "subhead": "mean FPts that gameweek",
        "footnote": "Eighteen divisions pick independently. When several "
                    "converge on one player he tends to deliver - though it "
                    "flattens at the very top.",
        "render": """    TDS.bars(document.getElementById('chart'), {
      rows: x.faab.consensus.map(function (c) {
        return { label: c.band + (c.band === '1' ? ' division' : ' divisions'),
          note: 'n=' + TDS.thousands(c.n) + ' \\u00b7 ' +
            TDS.pct(c.start_rate) + ' started',
          bars: [{ key: 'lineup_snipe', value: c.mean_fpts,
            display: TDS.num(c.mean_fpts) }] };
      })
    });""",
    },
]


def read(path):
    return io.open(path, encoding="utf-8").read()


def main():
    css = read(SITE / "assets" / "tds.css")
    js = read(SITE / "assets" / "tds.js")
    metrics = read(SITE / "data" / "metrics.js")
    extra = read(SITE / "data" / "extra.js")

    OUT.mkdir(parents=True, exist_ok=True)
    for spec in EMBEDS:
        footnote = ('<p class="note" style="margin-top:14px">%s</p>'
                    % spec["footnote"]) if spec.get("footnote") else ""
        body = PAGE % {
            "title": spec["title"],
            "heading": spec["heading"],
            "subhead": spec["subhead"],
            "footnote": footnote,
            "render": spec["render"],
            "fonts": FONTS,
            "css": css, "js": js, "metrics": metrics, "extra": extra,
        }
        path = OUT / spec["file"]
        io.open(path, "w", encoding="utf-8").write(body)
        print("%-18s %6.1f KB" % (spec["file"], len(body.encode("utf-8")) / 1024))

    index = ["<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
             "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
             "<title>Embeds</title>",
             "<link href=\"%s\" rel=\"stylesheet\">" % FONTS,
             "<link rel=\"stylesheet\" href=\"../assets/tds.css\"></head><body>",
             "<div class=\"wrap\"><h1>Embeds</h1>",
             "<p class=\"lede\">Each one is a single self-contained file. "
             "Iframe it, or paste the whole file into a Wix HTML block.</p>",
             "<div class=\"scroll\"><table><thead><tr><th>Chart</th>"
             "<th>File</th></tr></thead><tbody>"]
    for spec in EMBEDS:
        index.append("<tr><td><a href=\"%s\">%s</a></td><td>embed/%s</td></tr>"
                     % (spec["file"], spec["title"], spec["file"]))
    index.append("</tbody></table></div>")
    index.append("<footer><p><a href=\"../index.html\">Back to the study</a>"
                 "</p></footer></div></body></html>")
    io.open(OUT / "index.html", "w", encoding="utf-8").write("\n".join(index))
    print("%-18s (listing)" % "index.html")


if __name__ == "__main__":
    main()
