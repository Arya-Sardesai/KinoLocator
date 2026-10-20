/* Kinolocator stats page. Plain JS, no build step.
 *
 * Reads stats.json (built from every archive snapshot by
 * `python -m kinolocator stats`, see kinolocator/stats.py; copied next to
 * this file at deploy time) and draws each chart as inline SVG at the
 * width of its panel, redrawn when the window is resized.
 */
(function () {
  "use strict";

  var LANGUAGE_NAMES = { cn: "Cantonese", zh: "Mandarin", xx: "No dialogue", unknown: "Not known", other: "Other languages" };
  var FORMAT_NAMES = { unlisted: "Not stated" };
  var displayNames = null;
  var data = null;

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function num(n) { return Number(n).toLocaleString("en-CA"); }

  function languageName(code) {
    if (LANGUAGE_NAMES[code]) return LANGUAGE_NAMES[code];
    try {
      displayNames = displayNames || new Intl.DisplayNames(["en"], { type: "language" });
      return displayNames.of(code) || code;
    } catch (e) {
      return code;
    }
  }

  // "12 Oct 2026"
  function dateLabel(date) {
    return new Date(date + "T12:00:00Z").toLocaleDateString("en-GB", { timeZone: "UTC", day: "numeric", month: "short", year: "numeric" });
  }

  // Labelled horizontal bars: label on the left, the bar, its value at the end.
  function hbars(rows, width, opts) {
    opts = opts || {};
    var row = 26, labelW = Math.min(opts.labelW || 150, Math.round(width * 0.45)), valueW = 44;
    var barW = Math.max(40, width - labelW - valueW - 10);
    var max = Math.max.apply(null, rows.map(function (r) { return r.value; }).concat([1]));
    var h = rows.length * row;
    var out = '<svg width="' + width + '" height="' + h + '" viewBox="0 0 ' + width + " " + h + '" role="img" aria-label="' + esc(opts.title || "") + '">';
    rows.forEach(function (r, i) {
      var y = i * row;
      var w = Math.max(2, (r.value / max) * barW);
      out += '<text class="chart-label" x="0" y="' + (y + 16) + '">' + esc(clip(r.label, labelW)) + "</text>" +
        '<rect class="chart-track" x="' + labelW + '" y="' + (y + 6) + '" width="' + barW + '" height="12" rx="2"></rect>' +
        '<rect class="chart-bar' + (r.quiet ? " is-quiet" : "") + '" x="' + labelW + '" y="' + (y + 6) + '" width="' + w.toFixed(1) + '" height="12" rx="2"><title>' +
          esc(r.label + ": " + num(r.value)) + "</title></rect>" +
        '<text class="chart-value" x="' + (labelW + barW + 8) + '" y="' + (y + 16) + '">' + num(r.value) + "</text>";
    });
    return out + "</svg>";
  }

  // Cut a label to roughly fit `px` at 12.5px type.
  function clip(text, px) {
    var max = Math.floor(px / 7);
    return text.length > max ? text.slice(0, max - 1) + "…" : text;
  }

  // Films per decade of release: columns, value above, decade below.
  function decades(rows, width) {
    var h = 190, base = 160, top = 22;
    var max = Math.max.apply(null, rows.map(function (r) { return r.films; }).concat([1]));
    var slot = width / Math.max(rows.length, 1);
    var barW = Math.min(44, slot * 0.66);
    var out = '<svg width="' + width + '" height="' + h + '" viewBox="0 0 ' + width + " " + h + '" role="img" aria-label="Films by decade of release">';
    rows.forEach(function (r, i) {
      var bh = Math.max(2, (r.films / max) * (base - top));
      var x = i * slot + (slot - barW) / 2;
      var label = slot < 46 ? "’" + String(r.decade).slice(2) : r.decade + "s";
      out += '<rect class="chart-bar" x="' + x.toFixed(1) + '" y="' + (base - bh).toFixed(1) + '" width="' + barW.toFixed(1) + '" height="' + bh.toFixed(1) + '" rx="2">' +
        "<title>" + esc(r.decade + "s: " + r.films + (r.films === 1 ? " film" : " films")) + "</title></rect>" +
        '<text class="chart-value" text-anchor="middle" x="' + (x + barW / 2).toFixed(1) + '" y="' + (base - bh - 6).toFixed(1) + '">' + r.films + "</text>" +
        '<text class="chart-axis" text-anchor="middle" x="' + (x + barW / 2).toFixed(1) + '" y="' + (base + 18) + '">' + label + "</text>";
    });
    out += '<line class="chart-base" x1="0" x2="' + width + '" y1="' + (base + 0.5) + '" y2="' + (base + 0.5) + '"></line>';
    return out + "</svg>";
  }

  function panel(id, title, note, wide) {
    return '<section class="panel' + (wide ? " is-wide" : "") + '" aria-labelledby="' + id + '-h"><h2 id="' + id + '-h">' + esc(title) + "</h2>" +
      (note ? '<p class="note">' + esc(note) + "</p>" : "") + '<div data-chart="' + id + '"></div></section>';
  }

  function repertoryHTML(r) {
    var rep = r.repertory.films, nw = r.new.films, total = Math.max(rep + nw, 1);
    return '<div class="split" role="img" aria-label="' + esc(rep + " repertory films, " + nw + " new films") + '">' +
      '<i class="is-rep" style="width:' + (rep / total) * 100 + '%"></i><i class="is-new" style="width:' + (nw / total) * 100 + '%"></i></div>' +
      '<ul class="split-legend">' +
        '<li><span class="sw" style="background:var(--amber)"></span>Repertory <b>' + num(rep) + "</b> films · " + num(r.repertory.screenings) + " screenings</li>" +
        '<li><span class="sw" style="background:var(--ice)"></span>New <b>' + num(nw) + "</b> films · " + num(r.new.screenings) + " screenings</li>" +
      "</ul>";
  }

  function layout() {
    var d = data;
    var t = d.totals;
    var days = d.days + (d.days === 1 ? " day" : " days");
    $("basis").textContent = "Based on " + days + " of listings, " + dateLabel(d.first_day) + " to " + dateLabel(d.last_day) +
      ". Every scrape adds to it, twice a day, so these numbers grow as the archive does. A screening is counted once, however many days it was listed.";
    var range = d.screening_dates ? dateLabel(d.screening_dates.first) + " – " + dateLabel(d.screening_dates.last) : "–";
    var cut = d.repertory.cutoff_year;
    var unknown = d.repertory.unknown_year.films;
    $("stats-body").innerHTML =
      '<div class="totals">' +
        '<div class="total"><b>' + num(t.screenings) + "</b><span>screenings</span></div>" +
        '<div class="total"><b>' + num(t.films) + "</b><span>films</span></div>" +
        '<div class="total"><b>' + num(t.cinemas) + "</b><span>cinemas with listings</span></div>" +
        '<div class="total is-range"><b>' + esc(range) + "</b><span>screening dates covered</span></div>" +
      "</div>" +
      '<div class="panels">' +
        panel("decades", "Decade of release", "Films, by the year TMDB gives." + (unknown ? " " + unknown + " without a known year are left out." : ""), true) +
        '<section class="panel is-wide" aria-labelledby="rep-h"><h2 id="rep-h">Repertory and new</h2><p class="note">' +
          esc("Repertory: released before " + cut + ". New: released in " + cut + " or since." +
            (unknown ? " " + unknown + " films without a known year aren't counted." : "")) + "</p>" + repertoryHTML(d.repertory) + "</section>" +
        panel("languages", "Languages", "Screenings, by the language each is tagged with.") +
        panel("formats", "Formats", "Screenings, by projection format where the venue states it.") +
        panel("films", "Most-screened films", "Screenings of each film.") +
        panel("cinemas", "Busiest cinemas", "Screenings at each cinema.") +
      "</div>" +
      '<p class="stats-foot">Film details come from TMDB. This product uses the TMDB API but is not endorsed or certified by TMDB.</p>';
    draw();
  }

  function draw() {
    var d = data;
    var charts = {
      decades: function (w) { return decades(d.decades, w); },
      languages: function (w) {
        return hbars(d.languages.map(function (l) { return { label: languageName(l.code), value: l.screenings, quiet: l.code === "other" || l.code === "unknown" }; }), w, { labelW: 130, title: "Screenings by language" });
      },
      formats: function (w) {
        return hbars(d.formats.map(function (f) { return { label: FORMAT_NAMES[f.format] || f.format, value: f.screenings, quiet: f.format === "unlisted" }; }), w, { labelW: 110, title: "Screenings by format" });
      },
      films: function (w) {
        return hbars(d.top_films.map(function (f) { return { label: f.title + (f.year ? " (" + f.year + ")" : ""), value: f.screenings }; }), w, { labelW: 220, title: "Most-screened films" });
      },
      cinemas: function (w) {
        return hbars(d.cinemas.map(function (c) { return { label: c.name, value: c.screenings }; }), w, { labelW: 150, title: "Screenings by cinema" });
      },
    };
    Array.prototype.forEach.call(document.querySelectorAll("[data-chart]"), function (el) {
      el.innerHTML = charts[el.getAttribute("data-chart")](Math.floor(el.clientWidth));
    });
  }

  fetch("stats.json", { cache: "no-cache" }).then(function (r) {
    if (!r.ok) throw new Error("stats.json: HTTP " + r.status);
    return r.json();
  }).then(function (json) {
    data = json;
    layout();
    var timer = null;
    window.addEventListener("resize", function () { clearTimeout(timer); timer = setTimeout(draw, 120); });
  }).catch(function (err) {
    if (window.console) console.error(err);
    $("basis").textContent = "The stats couldn't be loaded. Try reloading in a minute.";
  });
})();
