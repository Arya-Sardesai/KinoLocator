/* Kinolocator tonight view. Plain JS, no build step.
 *
 * Reads showtimes.json (copied next to this file at deploy time, see
 * netlify.toml), venues.json and memberships.json.
 *
 * Times: every showtime's `start` carries its Vancouver offset
 * ("2026-10-19T19:00:00-07:00"), so wall-clock times and dates are read from
 * the string itself rather than through the browser's time-zone data, which
 * may predate BC's move to permanent UTC-7 on 2026-11-01. "Now" in Vancouver
 * uses the offset of the showtime nearest to now for the same reason.
 *
 * Testing: ?at=2026-10-19T18:10 pretends it is that Vancouver time.
 */
(function () {
  "use strict";

  var SCRUB_START = 17 * 60; // 5 PM, minutes after midnight
  var SCRUB_END = 24 * 60; // midnight
  var CARRY_ON_BELOW = 8; // fewer than this left tonight: carry on into the next day
  var STORAGE_KEY = "kinolocator.memberships";
  var DAY_MS = 86400000;
  // Shown as tags only when they say something: DCP is the everyday format.
  var EVERYDAY_FORMATS = { DCP: true, Digital: true };
  var LANGUAGE_NAMES = { cn: "Cantonese", zh: "Mandarin", xx: "No dialogue" };
  // Film admission in BC carries GST only (no PST). Used to compare a venue
  // that lists prices before GST with venues whose prices include it.
  var GST_RATE = 0.05;

  var state = {
    shows: [], // parsed showtimes
    venues: {}, // id -> venue (venues.json, plus any id only seen in the data)
    venueOrder: [],
    status: {}, // id -> showtimes.json venues[id]
    memberships: [],
    selected: {}, // membership id -> true
    sort: "time",
    venueFilter: null,
    hotVenue: null,
    scrub: null, // minutes after midnight chosen on the scrubber; null = now
    offset: null, // Vancouver UTC offset in minutes, e.g. -420
    now: null, // { ms, date: "YYYY-MM-DD", minutes }
    pinnedNow: null, // ?at= override, in ms
    generatedAt: null,
  };

  var els = {};
  var map = null;
  var markers = {}; // venue id -> Leaflet marker
  var groups = {}; // "id+id" -> Leaflet marker standing in for venues whose dots overlap
  var mergedInto = {}; // venue id -> its group's key, while merged
  var displayNames = null;

  // --- small helpers -------------------------------------------------------

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function parseWall(iso) {
    var m = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})?$/.exec(iso || "");
    if (!m) return null;
    var offset = null;
    if (m[4] === "Z") offset = 0;
    else if (m[4]) offset = (m[4][0] === "-" ? -1 : 1) * (parseInt(m[4].slice(1, 3), 10) * 60 + parseInt(m[4].slice(4), 10));
    return { date: m[1], minutes: parseInt(m[2], 10) * 60 + parseInt(m[3], 10), offset: offset, ms: Date.parse(iso) };
  }

  // Wall-clock date and minutes in Vancouver for an instant.
  function wallAt(ms) {
    var d = new Date(ms + state.offset * 60000);
    return { ms: ms, date: d.toISOString().slice(0, 10), minutes: d.getUTCHours() * 60 + d.getUTCMinutes() };
  }

  function addDays(date, n) {
    return new Date(Date.parse(date + "T00:00:00Z") + n * DAY_MS).toISOString().slice(0, 10);
  }

  function clockParts(minutes) {
    var h = Math.floor(minutes / 60) % 24;
    var m = minutes % 60;
    return { hm: (h % 12 || 12) + ":" + (m < 10 ? "0" : "") + m, ampm: h < 12 ? "AM" : "PM" };
  }

  function clock(minutes) {
    var p = clockParts(minutes);
    return p.hm + " " + p.ampm;
  }

  // "Mon 19 Oct"
  function dayLabel(date) {
    var d = new Date(date + "T12:00:00Z");
    var part = function (opts) { return d.toLocaleDateString("en-GB", Object.assign({ timeZone: "UTC" }, opts)); };
    return part({ weekday: "short" }) + " " + part({ day: "numeric" }) + " " + part({ month: "short" });
  }

  function whenLabel(ms) {
    var w = wallAt(ms);
    var today = state.now.date;
    var day = w.date === today ? "today" : w.date === addDays(today, -1) ? "yesterday" : dayLabel(w.date);
    return day + " at " + clock(w.minutes);
  }

  function agoLabel(ms) {
    var min = Math.max(0, Math.round((state.now.ms - ms) / 60000));
    if (min < 2) return "just now";
    if (min < 60) return min + " min ago";
    var h = Math.round(min / 60);
    if (h < 36) return h + " h ago";
    return Math.round(h / 24) + " days ago";
  }

  function money(x) {
    if (x === 0) return "Free";
    return "$" + (Math.round(x) === x ? String(x) : x.toFixed(2));
  }

  function languageName(code) {
    if (LANGUAGE_NAMES[code]) return LANGUAGE_NAMES[code];
    try {
      displayNames = displayNames || new Intl.DisplayNames(["en"], { type: "language" });
      return displayNames.of(code) || code;
    } catch (e) {
      return code;
    }
  }

  // The one language a listing is tagged with: original_language where the
  // scraper set it, otherwise the first screening language listed.
  function mainLanguage(show) {
    return show.original_language || (show.screening_language || [])[0] || null;
  }

  function storageGet() {
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      var ids = raw ? JSON.parse(raw) : [];
      return Array.isArray(ids) ? ids : [];
    } catch (e) {
      return [];
    }
  }

  function storageSet(ids) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
    } catch (e) {
      /* private mode or storage blocked: ticks just won't persist */
    }
  }

  function fetchJSON(url) {
    return fetch(url, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error(url + ": HTTP " + r.status);
      return r.json();
    });
  }

  // --- time -------------------------------------------------------------------

  function updateNow() {
    var ms = state.pinnedNow != null ? state.pinnedNow : Date.now();
    state.now = wallAt(ms);
  }

  // Vancouver's offset now, taken from the showtime nearest to now.
  function pickOffset(nowMs) {
    var best = null;
    state.shows.forEach(function (s) {
      if (s.offset == null) return;
      var d = Math.abs(s.ms - nowMs);
      if (!best || d < best.d) best = { d: d, offset: s.offset };
    });
    if (best) return best.offset;
    return -new Date(nowMs).getTimezoneOffset(); // last resort: the viewer's own zone
  }

  // Effective "from" time tonight, in minutes after midnight.
  function cutoff() {
    var now = state.now.minutes;
    return state.scrub == null ? now : Math.max(now, state.scrub);
  }

  // --- prices -------------------------------------------------------------------

  function discountApplies(appliesTo, confidence) {
    var kinds = Array.isArray(appliesTo) ? appliesTo : [appliesTo || "rate_card"];
    return kinds.some(function (k) {
      return k === "all" || (k === "rate_card" && confidence === "estimated") || (k === "venue_stated" && confidence === "scraped");
    });
  }

  function priceFor(show) {
    var regular = show.regular_price;
    if (regular == null) return null;
    var best = 0;
    var by = null;
    state.memberships.forEach(function (m) {
      if (!state.selected[m.id]) return;
      (m.discounts || []).forEach(function (d) {
        if ((d.venues || []).indexOf(show.venue_id) < 0) return;
        if (!discountApplies(d.applies_to, show.price_confidence)) return;
        var off = d.amount_off != null ? Number(d.amount_off) : d.percent_off != null ? (regular * Number(d.percent_off)) / 100 : 0;
        if (off > best) { best = off; by = m.name; }
      });
    });
    var yours = Math.max(0, Math.round((regular - best) * 100) / 100);
    return { yours: yours, regular: regular, discounted: yours < regular, by: by, estimated: show.price_confidence === "estimated" };
  }

  // --- data -----------------------------------------------------------------------

  function venueFor(id, show) {
    if (!state.venues[id]) {
      // In the data but not in venues.json: list it, but it has no map dot.
      state.venues[id] = {
        id: id, name: show ? show.venue_name : id, short_name: show ? show.venue_name : id,
        neighbourhood: show ? show.municipality : "", lat: null, lng: null, url: null,
      };
      state.venueOrder.push(id);
      if (window.console) console.warn("Kinolocator: venue " + id + " is missing from venues.json");
    }
    return state.venues[id];
  }

  function ingest(showtimes, venues, memberships) {
    (venues.venues || []).forEach(function (v) {
      state.venues[v.id] = v;
      state.venueOrder.push(v.id);
    });
    state.memberships = (memberships && memberships.memberships) || [];
    state.status = showtimes.venues || {};
    state.generatedAt = showtimes.generated_at ? Date.parse(showtimes.generated_at) : null;
    state.shows = (showtimes.showtimes || []).map(function (s) {
      var w = parseWall(s.start);
      if (!w) return null;
      venueFor(s.venue_id, s);
      return Object.assign({}, s, w);
    }).filter(Boolean).sort(function (a, b) { return a.ms - b.ms || a.title.localeCompare(b.title); });
    var known = {};
    state.memberships.forEach(function (m) { known[m.id] = true; });
    storageGet().forEach(function (id) { if (known[id]) state.selected[id] = true; });
  }

  // What the page shows right now.
  function view() {
    var today = state.now.date;
    var from = cutoff();
    var tonightAll = state.shows.filter(function (s) { return s.date === today; });
    var remaining = tonightAll.filter(function (s) { return s.minutes >= from; });
    var byVenue = function (s) { return !state.venueFilter || s.venue_id === state.venueFilter; };
    var listed = remaining.filter(byVenue);

    var nextDate = null;
    state.shows.forEach(function (s) {
      if (s.date > today && byVenue(s) && (nextDate == null || s.date < nextDate)) nextDate = s.date;
    });
    var next = nextDate ? state.shows.filter(function (s) { return s.date === nextDate && byVenue(s); }) : [];

    var counts = {};
    remaining.forEach(function (s) { counts[s.venue_id] = (counts[s.venue_id] || 0) + 1; });
    var nextFirst = {};
    state.shows.forEach(function (s) {
      if (s.date > today && !nextFirst[s.venue_id]) nextFirst[s.venue_id] = s;
    });

    return {
      today: today, from: from, tonightAll: tonightAll, remaining: remaining, listed: listed,
      nextDate: nextDate, next: next, carryOn: listed.length < CARRY_ON_BELOW, counts: counts, nextFirst: nextFirst,
    };
  }

  // Your price as you'd pay it at the till, for sorting only: the page still
  // shows each price as the venue lists it.
  function priceWithTax(show) {
    var p = priceFor(show);
    if (!p) return Infinity;
    var v = state.venues[show.venue_id];
    return v && v.prices_exclude_gst ? p.yours * (1 + GST_RATE) : p.yours;
  }

  function sorted(list) {
    var out = list.slice();
    if (state.sort === "price") {
      out.sort(function (a, b) {
        return priceWithTax(a) - priceWithTax(b) || a.ms - b.ms;
      });
    }
    return out;
  }

  // --- rendering: listings ----------------------------------------------------------

  function showHTML(s) {
    var v = state.venues[s.venue_id];
    var t = clockParts(s.minutes);
    var title;
    if (s.original_title && s.original_title !== s.title) {
      var lang = s.original_language && s.original_language !== "xx" ? ' lang="' + esc(s.original_language) + '"' : "";
      title = '<h3 class="show-title"' + lang + ">" + esc(s.original_title) + "</h3>" +
        '<p class="show-latin">' + esc(s.title) + "</p>";
    } else {
      title = '<h3 class="show-title">' + esc(s.title) + "</h3>";
    }

    var tags = [];
    if (s.format && !EVERYDAY_FORMATS[s.format]) tags.push('<span class="tag tag-format">' + esc(s.format) + "</span>");
    var lang = mainLanguage(s);
    if (lang && lang !== "en") tags.push('<span class="tag tag-lang">' + esc(languageName(lang)) + "</span>");
    if (s.subtitles && s.subtitles.length) {
      tags.push('<span class="tag tag-lang">' + esc(s.subtitles.map(languageName).join(" · ")) + " subtitles</span>");
    }

    var p = priceFor(s);
    var price;
    if (!p) {
      price = '<span class="price-none">Price on the venue\'s site</span>';
    } else {
      var est = p.estimated && p.yours > 0 ? ' <span class="est">est.</span>' : "";
      // venues.json flags venues whose listed prices exclude GST.
      var tax = v.prices_exclude_gst ? ' <span class="tax">+ GST</span>' : "";
      price = '<span class="price">' + money(p.yours) + (p.yours > 0 ? tax : "") + est + "</span>";
      if (p.discounted) {
        price += '<span class="price-regular" title="' + esc("With " + p.by) + '">' + money(p.regular) + " regular" + (p.regular > 0 ? tax : "") + "</span>";
      }
    }

    var book = s.ticket_url
      ? '<a class="book" href="' + esc(s.ticket_url) + '" target="_blank" rel="noopener">Book at ' + esc(v.short_name || v.name) + " ↗</a>"
      : v.url
        ? '<a class="book" href="' + esc(v.url) + '" target="_blank" rel="noopener">Book at ' + esc(v.short_name || v.name) + ' ↗ <small>no ticket link listed, opens the venue\'s site</small></a>'
        : "";

    return '<article class="show" data-venue="' + esc(s.venue_id) + '">' +
      '<div class="show-time"><time datetime="' + esc(s.start) + '">' + t.hm + "</time><small>" + t.ampm + "</small></div>" +
      '<div class="show-main">' + title +
        '<p class="show-venue"><button type="button" data-venue-pick="' + esc(s.venue_id) + '">' + esc(v.short_name || v.name) + "</button>" +
        (v.neighbourhood ? " · " + esc(v.neighbourhood) : "") + "</p>" +
        '<div class="tags">' + tags.join("") + "</div>" + book +
      "</div>" +
      '<div class="show-price">' + price + "</div>" +
      "</article>";
  }

  function nextHeading(v) {
    return v.nextDate === addDays(v.today, 1) ? "Tomorrow" : dayLabel(v.nextDate);
  }

  function renderListings(v) {
    var html = "";
    var venueName = state.venueFilter ? (state.venues[state.venueFilter].short_name || state.venues[state.venueFilter].name) : null;

    if (v.listed.length) {
      html += sorted(v.listed).map(showHTML).join("");
    } else {
      var last = v.tonightAll.filter(function (s) { return !state.venueFilter || s.venue_id === state.venueFilter; }).pop();
      var why;
      if (state.scrub != null && state.scrub > state.now.minutes) why = "Nothing " + (venueName ? "at " + esc(venueName) + " " : "") + "starts after " + clock(v.from) + " tonight.";
      else if (last) why = "The last film " + (venueName ? "at " + esc(venueName) + " " : "") + "tonight started at " + clock(last.minutes) + ".";
      else why = "There are no films " + (venueName ? "at " + esc(venueName) + " " : "") + "tonight.";
      html += '<div class="done-state"><h2>That\'s everything for tonight</h2><p>' + why +
        (v.nextDate ? " " + (v.nextDate === addDays(v.today, 1) ? "Tomorrow's" : esc(dayLabel(v.nextDate)) + "'s") + " first films are below." : "") + "</p></div>";
    }

    if (v.carryOn && v.next.length) {
      html += '<h2 class="section-head">' + esc(nextHeading(v)) + " <small>" + esc(dayLabel(v.nextDate)) + "</small></h2>";
      html += sorted(v.next).map(showHTML).join("");
    } else if (!v.listed.length && !v.next.length) {
      html += '<p class="empty">No upcoming films are listed yet.</p>';
    }
    els.listings.innerHTML = html;
  }

  function renderHead(v) {
    var title = state.now.minutes < SCRUB_START ? "Today" : "Tonight";
    els.shelfTitle.textContent = title;
    els.dateChip.textContent = title + " · " + dayLabel(v.today);

    var n = v.listed.length;
    var venues = {};
    v.listed.forEach(function (s) { venues[s.venue_id] = true; });
    var parts = [n ? n + (n === 1 ? " film" : " films") + " still to start" : "Nothing left " + title.toLowerCase()];
    var nv = Object.keys(venues).length;
    if (n && !state.venueFilter) parts.push(nv + (nv === 1 ? " venue" : " venues"));
    var confirmed = oldestConfirmed();
    if (confirmed != null) parts.push("confirmed " + agoLabel(confirmed));
    els.shelfMeta.textContent = parts.join(" · ");
    els.rail.textContent = n + (n === 1 ? " film " : " films ") + title.toLowerCase();

    if (state.venueFilter) {
      var fv = state.venues[state.venueFilter];
      els.venueFilter.hidden = false;
      els.venueFilter.textContent = "Only " + (fv.short_name || fv.name);
      els.venueFilter.setAttribute("aria-label", "Showing only " + fv.name + ". Show all venues");
    } else {
      els.venueFilter.hidden = true;
    }

    var count = Object.keys(state.selected).length;
    els.membershipCount.textContent = count ? String(count) : "";
  }

  function oldestConfirmed() {
    var oldest = null;
    Object.keys(state.status).forEach(function (id) {
      var st = state.status[id];
      if (st.stale || !st.last_confirmed_at) return;
      var t = Date.parse(st.last_confirmed_at);
      if (oldest == null || t < oldest) oldest = t;
    });
    return oldest != null ? oldest : state.generatedAt;
  }

  function staleWords(id) {
    var st = state.status[id];
    var v = state.venues[id];
    var name = v ? v.name : id;
    var why = st.failure === "no_showtimes" ? name + "'s site listed no films on our last check" : name + "'s site couldn't be read on our last check";
    var when = st.last_confirmed_at ? ", so these are the times we last confirmed " + whenLabel(Date.parse(st.last_confirmed_at)) : "";
    return why + when + ". Changes since then won't show here.";
  }

  function renderStatus() {
    var notices = "";
    var items = "";
    state.venueOrder.forEach(function (id) {
      var st = state.status[id];
      var v = state.venues[id];
      if (!st) {
        items += "<li>" + esc(v.name) + ": no recent check</li>";
        return;
      }
      if (st.stale) {
        notices += '<p class="notice"><strong>' + esc(v.name) + " may be out of date.</strong> " + esc(staleWords(id)) + "</p>";
        items += '<li class="is-stale">' + esc(v.name) + ": " + (st.last_confirmed_at ? "last confirmed " + esc(whenLabel(Date.parse(st.last_confirmed_at))) : "not confirmed yet") + " (out of date)</li>";
      } else if (st.status !== "ok") {
        notices += '<p class="notice"><strong>No listings from ' + esc(v.name) + ".</strong> Its site couldn't be read on our last check, and there are no earlier times to show.</p>";
        items += '<li class="is-stale">' + esc(v.name) + ": not confirmed</li>";
      } else {
        items += "<li>" + esc(v.name) + ": confirmed " + esc(whenLabel(Date.parse(st.last_confirmed_at))) + "</li>";
      }
    });
    els.notices.innerHTML = notices;
    els.venueStatus.innerHTML = items;
  }

  // --- rendering: scrubber ---------------------------------------------------------------

  function renderScrubber(v) {
    var now = state.now.minutes;
    var span = SCRUB_END - SCRUB_START;
    var nowPos = Math.min(span, Math.max(0, now - SCRUB_START));
    var pos = state.scrub == null ? nowPos : Math.max(nowPos, state.scrub - SCRUB_START);
    els.scrub.value = String(pos);
    els.scrubPast.style.width = (nowPos / span) * 100 + "%";
    var label = state.scrub == null || state.scrub <= now ? "Now · " + clock(now) : "From " + clock(v.from);
    els.scrubLabel.textContent = label;
    els.scrub.setAttribute("aria-valuetext", label);

    var byMinute = {};
    v.tonightAll.forEach(function (s) {
      if (s.minutes < SCRUB_START) return;
      byMinute[s.minutes] = (byMinute[s.minutes] || 0) + 1;
    });
    els.scrubTicks.innerHTML = Object.keys(byMinute).map(function (m) {
      var x = ((m - SCRUB_START) / span) * 100;
      var h = Math.min(18, 7 + byMinute[m] * 4);
      return '<i class="' + (m < v.from ? "is-past" : "") + '" style="left:' + x + "%;--h:" + h + 'px"></i>';
    }).join("");
  }

  // --- rendering: map ------------------------------------------------------------------------

  function dotSize(n) {
    return n > 0 ? Math.min(30, 10 + 5 * Math.sqrt(n)) : 8;
  }

  function labelHTML(id, v) {
    var venue = state.venues[id];
    var n = v.counts[id] || 0;
    var sub;
    if (n) sub = n + (n === 1 ? " film left" : " films left");
    else if (!v.remaining.length && v.nextFirst[id]) {
      var f = v.nextFirst[id];
      sub = (f.date === addDays(v.today, 1) ? "Tomorrow " : dayLabel(f.date) + " ") + clock(f.minutes);
    } else sub = "";
    return '<span class="vl-name">' + esc(venue.short_name || venue.name) + "</span>" + (sub ? '<span class="vl-sub">' + esc(sub) + "</span>" : "");
  }

  // Venue dots that would overlap at the current zoom (VIFF Centre and the
  // Cinematheque are a block apart) are merged into one dot labelled with
  // their area; zooming in separates them again. Merged groups are keyed by
  // their venue ids joined with "+".
  var MERGE_GAP = 8; // px of clear space wanted between two dots' edges

  function areaName(ids) {
    var shared = function (f) {
      var values = ids.map(function (id) { return f(state.venues[id]) || ""; });
      return values.every(function (x) { return x && x === values[0]; }) ? values[0] : "";
    };
    return shared(function (venue) { return venue.neighbourhood; }) ||
      shared(function (venue) { return (venue.address || "").split(",").pop().trim(); });
  }

  // Single-link grouping: two venues join when their dots would touch.
  function regroup(v) {
    var ids = Object.keys(markers);
    var pt = {}, root = {};
    ids.forEach(function (id) { pt[id] = map.latLngToLayerPoint(markers[id].getLatLng()); root[id] = id; });
    var find = function (id) { while (root[id] !== id) id = root[id] = root[root[id]]; return id; };
    for (var i = 0; i < ids.length; i++) {
      for (var j = i + 1; j < ids.length; j++) {
        var a = ids[i], b = ids[j];
        var reach = (dotSize(v.counts[a] || 0) + dotSize(v.counts[b] || 0)) / 2 + MERGE_GAP;
        if (pt[a].distanceTo(pt[b]) < reach) root[find(a)] = find(b);
      }
    }
    var members = {};
    ids.forEach(function (id) { (members[find(id)] = members[find(id)] || []).push(id); });
    var keys = {};
    mergedInto = {};
    Object.keys(members).forEach(function (r) {
      var group = members[r];
      if (group.length < 2) return;
      var key = group.sort().join("+");
      keys[key] = true;
      group.forEach(function (id) { mergedInto[id] = key; });
      if (!groups[key]) groups[key] = groupMarker(group);
    });
    Object.keys(groups).forEach(function (key) {
      if (keys[key]) return;
      groups[key].remove();
      delete groups[key];
    });
  }

  function groupMarker(ids) {
    var lat = 0, lng = 0;
    ids.forEach(function (id) { lat += state.venues[id].lat / ids.length; lng += state.venues[id].lng / ids.length; });
    var marker = venueMarker([lat, lng], areaName(ids) || ids.length + " venues", "venue-group");
    var key = ids.join("+");
    var zoomIn = function () { separate(ids); };
    marker.on("click", zoomIn);
    marker.getTooltip().on("click", zoomIn);
    marker.on("mouseover", function () { setHot(key, true); });
    marker.on("mouseout", function () { setHot(null, true); });
    return marker;
  }

  // Zoom in just far enough for the group's dots to stand apart, even at
  // the largest dot size (30px).
  function separate(ids) {
    var latlngs = ids.map(function (id) { return markers[id].getLatLng(); });
    var pts = latlngs.map(function (ll) { return map.latLngToLayerPoint(ll); });
    var near = Infinity;
    pts.forEach(function (a, i) {
      pts.slice(i + 1).forEach(function (b) { near = Math.min(near, a.distanceTo(b)); });
    });
    var steps = Math.max(1, Math.ceil(Math.log2((dotSize(Infinity) + MERGE_GAP) / Math.max(near, 1))));
    fitMap(latlngs, Math.min(map.getZoom() + steps, map.getMaxZoom()));
  }

  function groupLabelHTML(ids, v) {
    var n = ids.reduce(function (sum, id) { return sum + (v.counts[id] || 0); }, 0);
    var area = areaName(ids);
    var sub = [];
    if (area) sub.push(ids.length + " venues");
    if (n) sub.push(n + (n === 1 ? " film left" : " films left"));
    return '<span class="vl-name">' + esc(area || ids.length + " venues") + "</span>" +
      (sub.length ? '<span class="vl-sub">' + esc(sub.join(" · ")) + "</span>" : "");
  }

  function paintDot(m, ids, key, v, merged) {
    var n = ids.reduce(function (sum, id) { return sum + (v.counts[id] || 0); }, 0);
    var hot = state.hotVenue === key || ids.indexOf(state.hotVenue) >= 0;
    var selected = ids.indexOf(state.venueFilter) >= 0;
    var el = m.getElement();
    var tip = m.getTooltip();
    if (el) {
      el.classList.toggle("is-lit", n > 0);
      el.classList.toggle("is-done", n === 0);
      el.classList.toggle("is-hot", hot);
      el.classList.toggle("is-selected", selected);
      el.classList.toggle("is-merged", merged);
      el.querySelector(".dot").style.setProperty("--size", dotSize(n) + "px");
      var films = n ? n + (n === 1 ? " film" : " films") + " still to start" : "done for tonight";
      if (ids.length === 1) {
        el.setAttribute("aria-label", state.venues[ids[0]].name + ": " + films);
        el.setAttribute("aria-pressed", selected ? "true" : "false");
      } else {
        var names = ids.map(function (id) { return state.venues[id].name; });
        el.setAttribute("aria-label", (areaName(ids) ? areaName(ids) + ", " : "") + names.slice(0, -1).join(", ") + " and " +
          names[names.length - 1] + ": " + films + ". Zoom in to separate them");
      }
    }
    tip.setContent(ids.length === 1 ? labelHTML(ids[0], v) : groupLabelHTML(ids, v));
    var tipEl = tip.getElement();
    if (tipEl) {
      tipEl.classList.toggle("is-done", n === 0);
      tipEl.classList.toggle("is-hot", hot);
      tipEl.classList.toggle("is-selected", selected);
      tipEl.classList.toggle("is-merged", merged);
    }
    m.setZIndexOffset(hot || selected ? 1000 : n * 10);
  }

  function renderMap(v) {
    if (!map) return;
    regroup(v);
    Object.keys(markers).forEach(function (id) { paintDot(markers[id], [id], id, v, !!mergedInto[id]); });
    Object.keys(groups).forEach(function (key) { paintDot(groups[key], key.split("+"), key, v, false); });
    untangleLabels();
  }

  // Dots close together but not merged would print their labels on top of
  // each other: when two labels overlap, the western dot's label goes to the
  // left of it.
  function setLabelSide(marker, side) {
    var tip = marker.getTooltip();
    if (tip.options.direction === side) return;
    tip.options.direction = side;
    tip.options.offset = [side === "left" ? -marker.labelGap : marker.labelGap, 0];
    tip.update();
  }

  function untangleLabels() {
    if (!map) return;
    var shown = Object.keys(markers).filter(function (id) { return !mergedInto[id]; }).map(function (id) { return markers[id]; })
      .concat(Object.keys(groups).map(function (key) { return groups[key]; }));
    shown.forEach(function (m) { setLabelSide(m, "right"); });
    var box = function (m) {
      var el = m.getTooltip().getElement();
      return el ? el.getBoundingClientRect() : null;
    };
    for (var i = 0; i < shown.length; i++) {
      for (var j = i + 1; j < shown.length; j++) {
        var a = box(shown[i]), b = box(shown[j]);
        if (!a || !b || a.right < b.left || b.right < a.left || a.bottom < b.top || b.bottom < a.top) continue;
        setLabelSide(shown[i].getLatLng().lng <= shown[j].getLatLng().lng ? shown[i] : shown[j], "left");
      }
    }
  }

  // A refused tile still arrives as a picture: Stadia answers a missing or
  // wrong authorisation with HTTP 401 and a "401 Error" image, which a plain
  // <img> shows as if it were the map, so Leaflet never reports an error.
  // Fetching each tile first exposes the status; anything but 2xx is a
  // failed tile.
  var CheckedTileLayer = window.L && L.TileLayer.extend({
    initialize: function (url, options) {
      L.TileLayer.prototype.initialize.call(this, url, options);
      this.on("tileunload tileabort", function (e) {
        if (e.tile._abort) e.tile._abort.abort();
      });
    },
    createTile: function (coords, done) {
      var tile = document.createElement("img");
      tile.alt = "";
      var abort = window.AbortController ? new AbortController() : null;
      tile._abort = abort;
      fetch(this.getTileUrl(coords), { credentials: "omit", signal: abort ? abort.signal : undefined })
        .then(function (r) {
          if (!r.ok) throw new Error("tile: HTTP " + r.status);
          return r.blob();
        })
        .then(function (blob) {
          var src = URL.createObjectURL(blob);
          tile.onload = function () { URL.revokeObjectURL(src); done(null, tile); };
          tile.onerror = function () { URL.revokeObjectURL(src); done(new Error("tile: not an image"), tile); };
          tile.src = src;
        })
        .catch(function (err) {
          if (err.name !== "AbortError") done(err, tile);
        });
      return tile;
    },
  });

  function mapNote(html) {
    els.mapNote.innerHTML = html;
    els.mapNote.hidden = !html;
  }

  function initMap() {
    if (!window.L) {
      mapNote("<strong>The map couldn't load.</strong> All of tonight's listings are still in the list.");
      return;
    }
    map = L.map(els.map, {
      zoomControl: true,
      minZoom: 9,
      maxBounds: [[48.95, -123.6], [49.5, -122.2]],
      maxBoundsViscosity: 0.8,
    });
    map.zoomControl.setPosition("topright");

    // Stadia authorises the page by its domain (the Stadia property for
    // kinolocator.netlify.app; localhost needs nothing), so there is no key.
    var tiles = new CheckedTileLayer("https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.png", {
      maxZoom: 20,
      attribution: '&copy; <a href="https://stadiamaps.com/">Stadia Maps</a> &copy; <a href="https://openmaptiles.org/">OpenMapTiles</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    });
    // Counted per batch of tiles Leaflet asks for, so tiles that start failing
    // after the first view (a lapsed authorisation, a quota) still raise the note.
    var everLoaded = false, ok = 0, bad = 0, noteShown = false;
    function tilesNote(show) {
      if (show === noteShown) return;
      noteShown = show;
      mapNote(show ? "<strong>The map tiles didn't load.</strong> Venue dots and all listings still work." : "");
    }
    tiles.on("loading", function () { ok = 0; bad = 0; });
    tiles.on("tileload", function () { ok++; everLoaded = true; tilesNote(false); });
    tiles.on("tileerror", function () { bad++; if (!ok && bad >= 4) tilesNote(true); });
    tiles.on("load", function () { if (!ok && bad) tilesNote(true); });
    tiles.addTo(map);
    setTimeout(function () { if (!everLoaded) tilesNote(true); }, 8000);

    var bounds = [];
    state.venueOrder.forEach(function (id) {
      var venue = state.venues[id];
      if (venue.lat == null || venue.lng == null) return;
      var marker = venueMarker([venue.lat, venue.lng], venue.name, "");
      marker.on("click", function () { toggleVenue(id); });
      marker.getTooltip().on("click", function () { toggleVenue(id); });
      marker.on("mouseover", function () { setHot(id, true); });
      marker.on("mouseout", function () { setHot(null, true); });
      markers[id] = marker;
      bounds.push([venue.lat, venue.lng]);
    });
    fitMap(bounds);
    map.on("zoomend", function () { renderMap(view()); });
  }

  function venueMarker(latlng, title, extraClass) {
    var marker = L.marker(latlng, {
      icon: L.divIcon({ className: "venue-marker " + extraClass, html: '<span class="dot"></span>', iconSize: [32, 32], iconAnchor: [16, 16] }),
      keyboard: true,
      title: title,
      riseOnHover: true,
    });
    marker.labelGap = extraClass ? 20 : 12; // a group's dot has a ring around it
    marker.bindTooltip("", { permanent: true, direction: "right", offset: [marker.labelGap, 0], className: "venue-label", interactive: true });
    marker.addTo(map);
    // keyboard: true makes the dot focusable; Leaflet turns Enter into a click.
    if (marker.getElement()) marker.getElement().setAttribute("role", "button");
    return marker;
  }

  function fitMap(bounds, maxZoom) {
    if (!map) return;
    bounds = bounds || Object.keys(markers).map(function (id) { return markers[id].getLatLng(); });
    if (!bounds.length) { map.setView([49.25, -123.1], 11); return; }
    var phone = isPhone();
    var bottom = phone ? sheetHeight() + els.scrubber.offsetHeight + 24 : 60;
    map.fitBounds(bounds, {
      paddingTopLeft: [60, phone ? 40 : 60],
      paddingBottomRight: [phone ? 110 : 170, bottom],
      maxZoom: maxZoom || 13,
    });
  }

  // --- interaction -------------------------------------------------------------------------------

  function render() {
    var v = view();
    renderHead(v);
    renderListings(v);
    renderScrubber(v);
    renderMap(v);
  }

  function setHot(id, fromMap) {
    if (state.hotVenue === id) return;
    state.hotVenue = id;
    renderMap(view());
    if (fromMap) {
      Array.prototype.forEach.call(els.listings.querySelectorAll(".show"), function (row) {
        row.classList.toggle("is-hot", id != null && id.split("+").indexOf(row.getAttribute("data-venue")) >= 0);
      });
    }
  }

  function toggleVenue(id) {
    state.venueFilter = state.venueFilter === id ? null : id;
    render();
    els.shelfBody.scrollTop = 0;
    if (isPhone() && sheetHeight() < snaps()[1]) setSheet(snaps()[1]);
    if (els.stage.classList.contains("is-collapsed")) setCollapsed(false);
  }

  function renderMemberships() {
    if (!state.memberships.length) {
      els.membershipList.innerHTML = '<p class="menu-hint">No memberships are set up.</p>';
      return;
    }
    els.membershipList.innerHTML = state.memberships.map(function (m) {
      var venues = {};
      (m.discounts || []).forEach(function (d) { (d.venues || []).forEach(function (id) { venues[id] = true; }); });
      var names = Object.keys(venues).map(function (id) { return state.venues[id] ? state.venues[id].short_name || state.venues[id].name : null; }).filter(Boolean);
      var note = names.length ? "Applies at " + names.join(", ") : "No venue with listings yet";
      return '<label><input type="checkbox" value="' + esc(m.id) + '"' + (state.selected[m.id] ? " checked" : "") + ">" +
        "<span>" + esc(m.name) + "<small>" + esc(note) + "</small></span></label>";
    }).join("");
  }

  function setCollapsed(collapsed) {
    els.stage.classList.toggle("is-collapsed", collapsed);
    els.shelfToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    els.shelfToggle.setAttribute("aria-label", collapsed ? "Show listings" : "Hide listings");
    setTimeout(function () { if (map) map.invalidateSize(); }, 260);
  }

  // Phone bottom sheet ---------------------------------------------------------------

  var phoneQuery = window.matchMedia("(max-width: 720px)");

  function isPhone() { return phoneQuery.matches; }

  function snaps() {
    var h = window.innerHeight;
    var top = els.stage.getBoundingClientRect().top;
    return [150, Math.round(h * 0.46), Math.round(h - top - 12)];
  }

  function sheetHeight() { return isPhone() ? els.shelf.getBoundingClientRect().height : 0; }

  function setSheet(h) {
    var s = snaps();
    h = Math.max(s[0], Math.min(s[2], h));
    document.documentElement.style.setProperty("--sheet-h", h + "px");
    document.body.classList.toggle("sheet-full", h >= s[2] - 4);
  }

  function initSheet() {
    var startY = null, startH = 0, moved = false;
    function down(e) {
      if (!isPhone()) return;
      if (e.target.closest && e.target.closest("button:not(.sheet-handle), a, input")) return;
      startY = e.clientY;
      startH = sheetHeight();
      moved = false;
      document.body.classList.add("sheet-dragging");
      e.currentTarget.setPointerCapture(e.pointerId);
    }
    function move(e) {
      if (startY == null) return;
      var dy = startY - e.clientY;
      if (Math.abs(dy) > 4) moved = true;
      setSheet(startH + dy);
    }
    function up() {
      if (startY == null) return;
      startY = null;
      document.body.classList.remove("sheet-dragging");
      var h = sheetHeight();
      var s = snaps();
      var nearest = s.reduce(function (a, b) { return Math.abs(b - h) < Math.abs(a - h) ? b : a; });
      setSheet(nearest);
    }
    [els.sheetHandle, els.shelfHead].forEach(function (el) {
      el.addEventListener("pointerdown", down);
      el.addEventListener("pointermove", move);
      el.addEventListener("pointerup", up);
      el.addEventListener("pointercancel", up);
    });
    els.sheetHandle.addEventListener("click", function () {
      if (moved) return;
      var s = snaps();
      var h = sheetHeight();
      var i = s.findIndex(function (x) { return Math.abs(x - h) < 8; });
      setSheet(s[(i + 1) % s.length]);
    });
    if (isPhone()) setSheet(snaps()[1]);
  }

  function bind() {
    els.scrub.addEventListener("input", function () {
      var minutes = SCRUB_START + Number(els.scrub.value);
      state.scrub = minutes <= state.now.minutes ? null : minutes;
      render();
    });

    Array.prototype.forEach.call(document.querySelectorAll("[data-sort]"), function (b) {
      b.addEventListener("click", function () {
        state.sort = b.getAttribute("data-sort");
        Array.prototype.forEach.call(document.querySelectorAll("[data-sort]"), function (o) {
          o.setAttribute("aria-pressed", o === b ? "true" : "false");
        });
        render();
      });
    });

    els.venueFilter.addEventListener("click", function () { toggleVenue(state.venueFilter); });

    els.listings.addEventListener("click", function (e) {
      var pick = e.target.closest("[data-venue-pick]");
      if (pick) toggleVenue(pick.getAttribute("data-venue-pick"));
    });
    els.listings.addEventListener("mouseover", function (e) {
      var row = e.target.closest(".show");
      setHot(row ? row.getAttribute("data-venue") : null);
    });
    els.listings.addEventListener("mouseleave", function () { setHot(null); });
    els.listings.addEventListener("focusin", function (e) {
      var row = e.target.closest(".show");
      setHot(row ? row.getAttribute("data-venue") : null);
    });
    els.listings.addEventListener("focusout", function () { setHot(null); });

    els.membershipList.addEventListener("change", function (e) {
      if (!e.target.matches("input[type=checkbox]")) return;
      if (e.target.checked) state.selected[e.target.value] = true;
      else delete state.selected[e.target.value];
      storageSet(Object.keys(state.selected));
      render();
    });
    document.addEventListener("click", function (e) {
      if (els.membershipMenu.open && !els.membershipMenu.contains(e.target)) els.membershipMenu.open = false;
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && els.membershipMenu.open) els.membershipMenu.open = false;
    });

    els.shelfToggle.addEventListener("click", function () {
      setCollapsed(!els.stage.classList.contains("is-collapsed"));
    });

    var resizeTimer = null;
    window.addEventListener("resize", function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        if (isPhone()) setSheet(sheetHeight() || snaps()[1]);
        if (map) map.invalidateSize();
      }, 150);
    });

    // Keep "still to start" honest while the page stays open.
    setInterval(function () {
      if (state.pinnedNow != null) return;
      var before = state.now.date;
      updateNow();
      if (state.now.date !== before) state.scrub = null;
      render();
    }, 60000);
  }

  function fail(message) {
    els.shelfMeta.textContent = "";
    els.listings.innerHTML = '<div class="done-state"><h2>Listings didn\'t load</h2><p>' + esc(message) + "</p></div>";
    mapNote("<strong>Listings didn't load.</strong> Try reloading the page.");
  }

  function init() {
    ["stage", "map", "mapNote", "scrubber", "scrub", "scrubPast", "scrubTicks", "scrubLabel", "shelf", "shelfBody",
      "shelfTitle", "shelfMeta", "shelfToggle", "sheetHandle", "venueFilter", "notices", "listings", "venueStatus",
      "membershipMenu", "membershipList", "membershipCount", "dateChip"].forEach(function (k) {
      els[k] = $(k.replace(/[A-Z]/g, function (c) { return "-" + c.toLowerCase(); }));
    });
    els.rail = $("shelf-rail");
    els.shelfHead = document.querySelector(".shelf-head");

    var at = new URLSearchParams(window.location.search).get("at");

    Promise.all([
      fetchJSON("showtimes.json"),
      fetchJSON("venues.json"),
      fetchJSON("memberships.json").catch(function () { return { memberships: [] }; }),
    ]).then(function (res) {
      ingest(res[0], res[1], res[2]);
      if (at) {
        var w = parseWall(at.length === 16 ? at + ":00" : at);
        if (w) {
          var guessMs = Date.parse(w.date + "T00:00:00Z") + w.minutes * 60000;
          state.offset = pickOffset(guessMs);
          state.pinnedNow = guessMs - state.offset * 60000;
        }
      }
      if (state.offset == null) state.offset = pickOffset(Date.now());
      updateNow();
      renderMemberships();
      renderStatus();
      initSheet();
      initMap();
      bind();
      render();
    }).catch(function (err) {
      if (window.console) console.error(err);
      fail("The listings file couldn't be fetched or read. Try reloading in a minute.");
    });
  }

  init();
})();
