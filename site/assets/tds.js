/* Charts and tables for the pickup timing study.
 *
 * No chart library, and no SVG for the plots. Two reasons, both about where
 * these end up:
 *
 *  - A responsive SVG scaled by its viewBox scales its own text with it, so a
 *    940px chart squeezed into a 360px phone renders 11px labels at about 4px.
 *    HTML and CSS bars keep every label at a real font size at every width.
 *  - A dependency-free page cannot be broken by a CDN being blocked inside a
 *    sandboxed Wix iframe.
 *
 * Everything reads window.PICKUP_METRICS, written by analysis/metrics.py as a
 * script rather than JSON because fetch() is blocked on file:// URLs.
 */
(function (root) {
  'use strict';

  var TDS = root.TDS = {};

  var BUCKETS = ['waivers', 'lineup_snipe', 'late_add'];
  var COLOUR = {
    waivers: 'var(--waivers)',
    lineup_snipe: 'var(--snipe)',
    late_add: 'var(--late)',
    baseline: 'var(--baseline)',
    // Non-bucket hues, for cuts where the three bucket colours would be a
    // lie - positions, season phases, anything categorical of its own.
    teal: 'var(--teal)',
    violet: 'var(--violet)',
    rose: 'var(--rose)',
    accent: 'var(--accent)'
  };
  var SHORT = {
    waivers: 'Waivers',
    lineup_snipe: 'Snipe',
    late_add: 'Late add',
    baseline: 'Never added'
  };

  TDS.BUCKETS = BUCKETS;
  TDS.COLOUR = COLOUR;
  TDS.SHORT = SHORT;

  // ------------------------------------------------------------- plumbing --
  TDS.data = function () {
    var payload = root.PICKUP_METRICS;
    if (!payload) {
      throw new Error('no data: site/data/metrics.js is missing. ' +
        'Run `python analysis/metrics.py` first.');
    }
    return payload;
  };

  TDS.fail = function (node, message) {
    if (!node) { return; }
    node.innerHTML = '';
    var box = el('div', 'caveat');
    box.textContent = message;
    node.appendChild(box);
  };

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }
  TDS.el = el;

  TDS.num = function (value, places) {
    if (value === null || value === undefined || value === '') { return '--'; }
    var fixed = Number(value).toFixed(places === undefined ? 2 : places);
    return fixed;
  };

  TDS.pct = function (value) {
    if (value === null || value === undefined) { return '--'; }
    return Number(value).toFixed(1) + '%';
  };

  TDS.thousands = function (value) {
    return String(value).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  };

  // ------------------------------------------------------------- bar rows --
  /* Horizontal grouped bars. Horizontal because the category labels are words
   * ("Lineup snipe", "Goalkeeper") and vertical bars would either clip them or
   * rotate them to unreadability on a phone. */
  TDS.bars = function (node, options) {
    if (!node) { return; }
    var rows = options.rows || [];
    var max = options.max;
    if (max === undefined) {
      max = 0;
      rows.forEach(function (row) {
        (row.bars || []).forEach(function (bar) {
          if (bar.value !== null && bar.value > max) { max = bar.value; }
        });
      });
    }
    if (!max) { max = 1; }

    node.innerHTML = '';
    var chart = el('div', 'bars');

    rows.forEach(function (row) {
      var group = el('div', 'bars-group');
      var head = el('div', 'bars-label');
      head.appendChild(el('span', null, row.label));
      if (row.note) { head.appendChild(el('em', 'bars-note', row.note)); }
      group.appendChild(head);

      (row.bars || []).forEach(function (bar) {
        var line = el('div', 'bars-line');

        if (options.showKeys) {
          // `key` doubles as the colour, so a bar coloured by hue rather than
          // by bucket needs its own label - otherwise the row reads "teal".
          line.appendChild(el('span', 'bars-key',
            bar.name || SHORT[bar.key] || bar.key));
        }

        var track = el('div', 'bars-track');
        var fill = el('div', 'bars-fill');
        var share = bar.value === null ? 0 : Math.max(0, bar.value) / max * 100;
        fill.style.width = share.toFixed(2) + '%';
        fill.style.background = bar.colour || COLOUR[bar.key] || 'var(--gold)';
        if (bar.faint) { fill.style.opacity = '0.45'; }
        track.appendChild(fill);
        line.appendChild(track);

        var value = el('span', 'bars-value', bar.display !== undefined
          ? bar.display : TDS.num(bar.value));
        if (bar.thin) { value.appendChild(el('em', 'thin', ' thin')); }
        line.appendChild(value);

        group.appendChild(line);
      });

      chart.appendChild(group);
    });

    node.appendChild(chart);
  };

  // ------------------------------------------------------------- columns ---
  /* Vertical columns, for the add-timing distribution. The x axis is ordered
   * time-to-kickoff, so it has to run left to right. Labels are printed only
   * on bands that matter, because 22 bands will not carry 22 labels on a
   * phone. */
  TDS.columns = function (node, options) {
    if (!node) { return; }
    var bands = options.bands || [];
    var max = 0;
    bands.forEach(function (band) { if (band.value > max) { max = band.value; } });
    if (!max) { max = 1; }

    node.innerHTML = '';
    var chart = el('div', 'cols');

    // Past about twenty columns the per-column numbers collide into an
    // unreadable smear, so they are dropped and the hover title carries them
    // instead. Explicit `showValues` overrides either way.
    var showValues = options.showValues;
    if (showValues === undefined) { showValues = bands.length <= 20; }

    bands.forEach(function (band) {
      var column = el('div', 'cols-col' + (band.highlight ? ' is-hot' : ''));
      if (band.title) { column.title = band.title; }

      if (showValues) {
        column.appendChild(el('div', 'cols-value', band.value || ''));
      }

      var track = el('div', 'cols-track');
      var fill = el('div', 'cols-fill');
      fill.style.height = (band.value / max * 100).toFixed(2) + '%';
      if (band.colour) { fill.style.background = band.colour; }
      track.appendChild(fill);
      column.appendChild(track);

      var label = el('div', 'cols-label', band.label || '');
      column.appendChild(label);

      chart.appendChild(column);
    });

    node.appendChild(chart);
  };

  // --------------------------------------------------------------- tables --
  /* Click-to-sort. Numeric columns are marked with data-type="num" so that
   * "10" sorts above "9" instead of below it, which a string sort would do. */
  TDS.sortable = function (table) {
    if (!table) { return; }
    var headers = table.querySelectorAll('thead th');
    Array.prototype.forEach.call(headers, function (header, index) {
      if (header.dataset.sort === 'off') { return; }
      header.classList.add('sortable');
      header.addEventListener('click', function () {
        var numeric = header.dataset.type === 'num';
        var descending = header.dataset.dir !== 'desc';

        Array.prototype.forEach.call(headers, function (other) {
          if (other !== header) { delete other.dataset.dir; }
        });
        header.dataset.dir = descending ? 'desc' : 'asc';

        var body = table.tBodies[0];
        var rows = Array.prototype.slice.call(body.rows);
        rows.sort(function (a, b) {
          var left = cellValue(a, index, numeric);
          var right = cellValue(b, index, numeric);
          if (left < right) { return descending ? 1 : -1; }
          if (left > right) { return descending ? -1 : 1; }
          return 0;
        });
        rows.forEach(function (row) { body.appendChild(row); });
      });
    });
  };

  function cellValue(row, index, numeric) {
    var cell = row.cells[index];
    if (!cell) { return numeric ? -Infinity : ''; }
    var raw = cell.dataset.value !== undefined ? cell.dataset.value : cell.textContent;
    if (!numeric) { return raw.trim().toLowerCase(); }
    var parsed = parseFloat(String(raw).replace(/[^0-9.\-]/g, ''));
    return isNaN(parsed) ? -Infinity : parsed;
  }

  // --------------------------------------------------------------- legend --
  TDS.legend = function (node, keys) {
    if (!node) { return; }
    node.innerHTML = '';
    keys.forEach(function (key) {
      var item = el('span');
      var swatch = el('i');
      swatch.style.background = COLOUR[key] || key;
      item.appendChild(swatch);
      item.appendChild(el('span', null, SHORT[key] || key));
      node.appendChild(item);
    });
  };

  // ------------------------------------------------------- shared renders --
  /* The bucket comparison, used on the front page and as its own embed. */
  TDS.renderBuckets = function (node, payload, metric) {
    var block = payload.per_event;
    var baseline = payload.baseline;
    var spec = {
      start_rate: { label: 'Start rate', format: TDS.pct, max: 100 },
      mean_fpts: { label: 'Mean FPts per add', format: function (v) { return TDS.num(v); } },
      mean_fpts_if_started: {
        label: 'Mean FPts when he started',
        format: function (v) { return TDS.num(v); }
      },
      mean_next4: { label: 'FPts over the next 4 gameweeks',
        format: function (v) { return TDS.num(v); } }
    }[metric];

    var rows = BUCKETS.map(function (bucket) {
      var summary = block[bucket];
      return {
        label: SHORT[bucket],
        note: summary ? 'n=' + TDS.thousands(summary.n) : 'no data',
        bars: [{
          key: bucket,
          value: summary ? summary[metric] : null,
          display: summary ? spec.format(summary[metric]) : '--',
          thin: summary && summary.n < 30
        }]
      };
    });

    // The never-picked-up pool only belongs on the conditional metric: it is
    // built from players who started, so comparing it to an unconditional
    // mean would compare two different things.
    if (baseline && metric === 'mean_fpts_if_started') {
      rows.push({
        label: SHORT.baseline,
        note: 'n=' + TDS.thousands(baseline.n) + ', free at kickoff and started',
        bars: [{
          key: 'baseline',
          value: baseline.mean_fpts,
          display: TDS.num(baseline.mean_fpts),
          faint: true
        }]
      });
    }

    TDS.bars(node, { rows: rows, max: spec.max });
  };

  /* The add-timing distribution, with the lineup announcement marked.
   *
   * `options.upTo` clips to the final N minutes. Worth having: 47% of all
   * adds land one to three days out, and on the full range those columns are
   * five times the height of the snipe window, which buries the very thing
   * the chart exists to show. The front page clips to three hours; the timing
   * page shows everything. */
  TDS.renderTiming = function (node, payload, options) {
    var lock = payload.lock_minutes;
    var floor = payload.snipe_floor;
    var upTo = (options || {}).upTo;

    var source = payload.histogram.filter(function (band) {
      return !upTo || (band.to !== null && band.to <= upTo);
    });

    var bands = source.map(function (band) {
      var isSnipe = band.from === floor;
      var label = '';
      if (band.to === null) { label = '7d+'; }
      else if (band.from === 0) { label = '0'; }
      else if (band.from === floor) { label = String(floor); }
      else if (band.from === lock) { label = String(lock); }
      else if (band.from === 180) { label = '3h'; }
      else if (band.from === 1440) { label = '1d'; }
      else if (band.from === 4320) { label = '3d'; }
      else if (upTo) { label = String(band.from); }

      var span = band.to === null
        ? band.from + ' minutes or more'
        : band.from + ' to ' + band.to + ' minutes before kickoff';

      return {
        value: band.total,
        label: label,
        highlight: isSnipe,
        colour: isSnipe ? 'var(--snipe)'
          : (band.from < floor ? 'var(--late)' : 'var(--waivers)'),
        title: span + ': ' + band.total + ' adds'
      };
    });

    TDS.columns(node, { bands: bands });
  };

  root.addEventListener('error', function () { /* keep a chart error local */ });
}(window));
