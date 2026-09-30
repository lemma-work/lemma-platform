/* Lemma widget kit v1, the drawing half: the shapes most answers take,
 * drawn to the widget visual standard so a widget does not have to hand-write
 * them. Each takes an element (or its id) and plain rows, and returns the element.
 *
 *   lemma.stats(el, [{ label, value, previous?, format?, better? }])
 *   lemma.bars(el, rows, { label, value, format?, top? })
 *   lemma.line(el, rows, { x, y, format?, xFormat?, names? })   y: a key, or several
 *   lemma.scatter(el, rows, { x, y, color?, label?, xFormat?, yFormat?, xLabel?, yLabel? })
 *   lemma.table(el, rows, { columns: [{ key, label, format? }], filter? })
 *   lemma.record(el, row, { fields: [{ key, label, format? }] })
 *
 * `format` names one of lemma.num / money / pct / date. `better` is "up" or
 * "down": which direction of change is good news, so it wears the right colour.
 * Every chart carries its numbers as a visually hidden table and answers the
 * pointer; text stays in ink and only the marks wear colour.
 */
(function () {
  var L = window.lemma;
  if (!L || L.bars) return;

  function node(target) { return typeof target === "string" ? document.getElementById(target) : target; }
  function fmt(format) { return (format && L[format]) || L.num; }
  function hidden(rows, columns) {
    // Wrapped: a table ignores height and overflow, so a clipped table still
    // stretches the frame by its full height.
    return '<div class="lk-sr"><table><thead><tr>' + columns.map(function (c) { return "<th>" + L.esc(c.label) + "</th>"; }).join("") +
      "</tr></thead><tbody>" + rows.map(function (r) {
        return "<tr>" + columns.map(function (c) { return "<td>" + L.esc(fmt(c.format)(r[c.key])) + "</td>"; }).join("") + "</tr>";
      }).join("") + "</tbody></table></div>";
  }
  // An empty result is a normal answer, not an error: say so instead of drawing axes around nothing.
  function empty(el) { el.innerHTML = '<p class="lk-empty">Nothing to show.</p>'; return el; }
  function text(format, value) { return typeof value === "number" || !isNaN(Number(value)) ? fmt(format)(value) : L.esc(value); }

  L.stats = function (target, items) {
    var el = node(target);
    el.classList.add("lk-stats");
    el.innerHTML = items.map(function (s) {
      var delta = "";
      if (s.previous != null && isFinite(Number(s.previous))) {
        var change = Number(s.value) - Number(s.previous);
        var ratio = Number(s.previous) ? change / Math.abs(Number(s.previous)) : null;
        var good = s.better === "down" ? change < 0 : change > 0;
        var kind = change === 0 ? "flat" : good ? "good" : "bad";
        delta = '<span class="lk-delta" data-kind="' + kind + '">' + (change > 0 ? "▲ " : change < 0 ? "▼ " : "") +
          (ratio == null ? fmt(s.format)(change) : L.pct(Math.abs(ratio))) + " vs " + fmt(s.format)(s.previous) + "</span>";
      }
      return '<div class="lk-stat"><span class="lk-label">' + L.esc(s.label) + '</span><span class="lk-value">' +
        text(s.format, s.value) + "</span>" + delta + "</div>";
    }).join("");
    return el;
  };

  L.bars = function (target, rows, o) {
    var el = node(target);
    if (!rows || !rows.length) return empty(el);
    var top = o.top || 8;
    var shown = rows.slice(0, top);
    var rest = rows.slice(top).reduce(function (a, r) { return a + (Number(r[o.value]) || 0); }, 0);
    if (rest) { var other = {}; other[o.label] = "Other (" + (rows.length - top) + ")"; other[o.value] = rest; other.__rest = true; shown.push(other); }
    var max = Math.max.apply(null, shown.map(function (r) { return Number(r[o.value]) || 0; })) || 1;
    el.classList.add("lk-bars");
    el.innerHTML = shown.map(function (r) {
      var v = Number(r[o.value]) || 0;
      return '<div class="lk-bar-row" title="' + L.esc(r[o.label]) + ": " + L.esc(fmt(o.format)(v)) + '"><span class="lk-name">' +
        L.esc(r[o.label] == null ? "Unspecified" : r[o.label]) + '</span><span class="lk-track"><span class="lk-bar"' +
        (r.__rest ? " data-rest" : "") + ' style="width:' + (v / max) * 100 + '%"></span></span><span class="lk-num">' +
        fmt(o.format)(v) + "</span></div>";
    }).join("") + hidden(rows, [{ key: o.label, label: o.label }, { key: o.value, label: o.value, format: o.format }]);
    return el;
  };

  var SERIES = ["var(--lemma-widget-chart-1, #3d6be0)", "var(--lemma-widget-chart-2, #d9822b)", "var(--lemma-widget-chart-3, #2f9e8f)",
    "var(--lemma-widget-chart-4, #9b5de5)", "var(--lemma-widget-chart-5, #c2528b)"];
  function legend(names) {
    return names.length < 2 ? "" : '<div class="lk-legend">' + names.map(function (n, i) {
      return '<span><i style="background:' + SERIES[i % SERIES.length] + '"></i>' + L.esc(n) + "</span>";
    }).join("") + "</div>";
  }

  L.line = function (target, rows, o) {
    var el = node(target);
    if (!rows || !rows.length) return empty(el);
    var keys = [].concat(o.y);
    var names = keys.map(function (k, i) { return (o.names && o.names[i]) || k; });
    var W = 600, H = 180, P = { l: 8, r: 64, t: 12, b: 22 };
    var series = keys.map(function (k) { return rows.map(function (r) { return Number(r[k]) || 0; }); });
    var all = [].concat.apply([], series);
    var lo = Math.min.apply(null, all.concat([0])), hi = Math.max.apply(null, all) || 1;
    var x = function (i) { return P.l + (rows.length < 2 ? 0 : (i / (rows.length - 1)) * (W - P.l - P.r)); };
    var y = function (v) { return P.t + (1 - (v - lo) / (hi - lo || 1)) * (H - P.t - P.b); };
    var xf = fmt(o.xFormat || "date");
    var last = rows.length - 1;
    var marks = series.map(function (ys, s) {
      var path = ys.map(function (v, i) { return (i ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1); }).join(" ");
      var colour = SERIES[s % SERIES.length];
      var area = keys.length > 1 ? "" : '<path class="lk-area" d="' + path + " L" + x(last).toFixed(1) + " " + (H - P.b) + " L" + x(0).toFixed(1) + " " + (H - P.b) + ' Z"/>';
      return area + '<path class="lk-path" style="stroke:' + colour + '" d="' + path + '"/>' +
        '<circle class="lk-dot" style="fill:' + colour + '" r="3.5" cx="' + x(last) + '" cy="' + y(ys[last]) + '"/>' +
        '<text class="lk-end" x="' + (x(last) + 8) + '" y="' + (y(ys[last]) + 4) + '">' + L.esc(fmt(o.format)(ys[last])) + "</text>";
    }).join("");
    el.classList.add("lk-line");
    el.innerHTML = legend(names) + '<div class="lk-plot"><svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + L.esc(names.join(", ")) + ' over time">' +
      [0.25, 0.5, 0.75].map(function (f) { var gy = P.t + f * (H - P.t - P.b); return '<line class="lk-grid" x1="' + P.l + '" x2="' + (W - P.r) + '" y1="' + gy + '" y2="' + gy + '"/>'; }).join("") +
      marks +
      '<text class="lk-axis" x="' + P.l + '" y="' + (H - 4) + '">' + L.esc(xf(rows[0] && rows[0][o.x])) + "</text>" +
      '<text class="lk-axis" x="' + (W - P.r) + '" y="' + (H - 4) + '" text-anchor="end">' + L.esc(xf(rows[last] && rows[last][o.x])) + "</text>" +
      '<line class="lk-cross" y1="' + P.t + '" y2="' + (H - P.b) + '" hidden/></svg><div class="lk-tip" hidden></div></div>' +
      hidden(rows, [{ key: o.x, label: o.x, format: o.xFormat || "date" }].concat(keys.map(function (k, i) { return { key: k, label: names[i], format: o.format }; })));
    var svg = el.querySelector("svg"), cross = el.querySelector(".lk-cross"), tip = el.querySelector(".lk-tip");
    svg.addEventListener("mousemove", function (e) {
      var box = svg.getBoundingClientRect();
      var px = ((e.clientX - box.left) / box.width) * W;
      var i = Math.max(0, Math.min(last, Math.round(((px - P.l) / (W - P.l - P.r)) * last)));
      cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.removeAttribute("hidden");
      tip.hidden = false;
      tip.textContent = xf(rows[i][o.x]) + " · " + series.map(function (ys, s) {
        return (keys.length > 1 ? names[s] + " " : "") + fmt(o.format)(ys[i]);
      }).join(" · ");
      tip.style.left = Math.min(box.width - 160, Math.max(0, (x(i) / W) * box.width - 60)) + "px";
    });
    svg.addEventListener("mouseleave", function () { cross.setAttribute("hidden", ""); tip.hidden = true; });
    return el;
  };

  L.scatter = function (target, rows, o) {
    var el = node(target);
    if (!rows || !rows.length) return empty(el);
    var W = 600, H = 260, P = { l: 8, r: 16, t: 24, b: 22 };
    var xs = rows.map(function (r) { return Number(r[o.x]) || 0; }), ys = rows.map(function (r) { return Number(r[o.y]) || 0; });
    var span = function (v) { var a = Math.min.apply(null, v.concat([0])), b = Math.max.apply(null, v) || 1; return [a, b - a || 1]; };
    var sx = span(xs), sy = span(ys);
    var px = function (v) { return P.l + ((v - sx[0]) / sx[1]) * (W - P.l - P.r); };
    var py = function (v) { return P.t + (1 - (v - sy[0]) / sy[1]) * (H - P.t - P.b); };
    // Groups in order of size, so the biggest wears chart-1; a sixth and later share one ink.
    var counts = {};
    rows.forEach(function (r) { var g = o.color ? String(r[o.color]) : ""; counts[g] = (counts[g] || 0) + 1; });
    var groups = Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; });
    var colour = function (g) { var i = groups.indexOf(g); return i < SERIES.length ? SERIES[i] : "var(--lemma-widget-faint, #b9b3a6)"; };
    el.classList.add("lk-scatter");
    el.innerHTML = (o.color ? legend(groups.map(function (g) { return g + " (" + counts[g] + ")"; })) : "") +
      '<div class="lk-plot"><svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + L.esc(o.y) + " against " + L.esc(o.x) + '">' +
      [0.25, 0.5, 0.75].map(function (f) { var gy = P.t + f * (H - P.t - P.b); return '<line class="lk-grid" x1="' + P.l + '" x2="' + (W - P.r) + '" y1="' + gy + '" y2="' + gy + '"/>'; }).join("") +
      rows.map(function (r, i) {
        return '<circle class="lk-pt" r="3.5" cx="' + px(xs[i]).toFixed(1) + '" cy="' + py(ys[i]).toFixed(1) + '" style="fill:' + colour(o.color ? String(r[o.color]) : "") + '"/>';
      }).join("") +
      '<text class="lk-axis" x="' + P.l + '" y="' + (H - 4) + '">' + L.esc(o.xLabel || o.x) + " →</text>" +
      '<text class="lk-axis" x="' + P.l + '" y="12">↑ ' + L.esc(o.yLabel || o.y) + "</text>" +
      '</svg><div class="lk-tip" hidden></div></div>' +
      hidden(rows, [o.label ? { key: o.label, label: o.label } : null, { key: o.x, label: o.xLabel || o.x, format: o.xFormat },
        { key: o.y, label: o.yLabel || o.y, format: o.yFormat }, o.color ? { key: o.color, label: o.color } : null].filter(Boolean));
    var svg = el.querySelector("svg"), tip = el.querySelector(".lk-tip");
    svg.addEventListener("mousemove", function (e) {
      var box = svg.getBoundingClientRect();
      var mx = ((e.clientX - box.left) / box.width) * W, my = ((e.clientY - box.top) / box.height) * H;
      var best = -1, dist = 400;
      for (var i = 0; i < rows.length; i++) {
        var d = Math.pow(px(xs[i]) - mx, 2) + Math.pow(py(ys[i]) - my, 2);
        if (d < dist) { dist = d; best = i; }
      }
      if (best < 0) { tip.hidden = true; return; }
      var r = rows[best];
      tip.hidden = false;
      tip.textContent = (o.label ? r[o.label] + " · " : "") + fmt(o.xFormat)(xs[best]) + ", " + fmt(o.yFormat)(ys[best]) + (o.color ? " · " + r[o.color] : "");
      tip.style.left = Math.min(box.width - 200, Math.max(0, (px(xs[best]) / W) * box.width - 60)) + "px";
      tip.style.top = Math.max(0, (py(ys[best]) / H) * box.height - 34) + "px";
    });
    svg.addEventListener("mouseleave", function () { tip.hidden = true; });
    return el;
  };

  L.table = function (target, rows, o) {
    var el = node(target);
    var cols = o.columns;
    el.classList.add("lk-table");
    function draw(list) {
      el.querySelector("tbody").innerHTML = list.map(function (r) {
        return "<tr>" + cols.map(function (c) { return '<td data-kind="' + (c.format ? "num" : "text") + '">' + text(c.format, r[c.key]) + "</td>"; }).join("") + "</tr>";
      }).join("") || '<tr><td colspan="' + cols.length + '" class="lk-empty">Nothing matches.</td></tr>';
      el.querySelector(".lk-count").textContent = list.length + " of " + rows.length;
    }
    var options = o.filter ? Array.from(new Set(rows.map(function (r) { return r[o.filter]; }))).sort() : [];
    el.innerHTML = '<div class="lk-tools">' + (o.filter ? '<select aria-label="Filter by ' + L.esc(o.filter) + '"><option value="">All</option>' +
      options.map(function (v) { return "<option>" + L.esc(v) + "</option>"; }).join("") + "</select>" : "") +
      '<span class="lk-count"></span></div><table><thead><tr>' + cols.map(function (c) { return "<th>" + L.esc(c.label) + "</th>"; }).join("") +
      "</tr></thead><tbody></tbody></table>";
    if (o.filter) el.querySelector("select").onchange = function (e) {
      var v = e.target.value;
      draw(v ? rows.filter(function (r) { return String(r[o.filter]) === v; }) : rows);
    };
    draw(rows);
    return el;
  };

  L.record = function (target, row, o) {
    var el = node(target);
    el.classList.add("lk-record");
    el.innerHTML = "<dl>" + o.fields.map(function (f) {
      return "<dt>" + L.esc(f.label) + '</dt><dd data-kind="' + (f.format ? "num" : "text") + '">' + text(f.format, row[f.key]) + "</dd>";
    }).join("") + "</dl>";
    return el;
  };
})();
