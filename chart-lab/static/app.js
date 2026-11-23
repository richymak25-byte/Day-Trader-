/* Chart Lab: free local charts for the Day Trader skills. KLineChart (Apache 2.0) draws the charts. */
(function () {
  "use strict";
  var kc = window.klinecharts;

  // ------------------------------------------------------------ helpers
  function $(s, root) { return (root || document).querySelector(s); }
  function $$(s, root) { return Array.prototype.slice.call((root || document).querySelectorAll(s)); }
  function el(tag, attrs, html) {
    var e = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "class") e.className = attrs[k]; else if (k === "text") e.textContent = attrs[k]; else e.setAttribute(k, attrs[k]);
    });
    if (html != null) e.innerHTML = html;
    return e;
  }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function fmt(n, d) { if (n == null || isNaN(n)) return "-"; return Number(n).toLocaleString("fr-FR", { minimumFractionDigits: d == null ? 2 : d, maximumFractionDigits: d == null ? 2 : d }); }
  function pct(p) { return p == null ? "-" : fmt(p * 100, 1) + " %"; }
  function store(k, v) { try { if (v === undefined) return JSON.parse(localStorage.getItem("chartlab." + k)); localStorage.setItem("chartlab." + k, JSON.stringify(v)); } catch (e) { return null; } }
  var toastTimer;
  function toast(msg) { var t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.classList.remove("show"); }, 3200); }
  function api(method, url, body) {
    return fetch(url, { method: method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(j.error || ("HTTP " + r.status)); return j; }); });
  }
  function debounce(fn, ms) { var t; return function () { var a = arguments, self = this; clearTimeout(t); t = setTimeout(function () { fn.apply(self, a); }, ms); }; }

  // ------------------------------------------------------------ custom indicators
  kc.registerIndicator({
    name: "VWAP", shortName: "VWAP", series: "price", precision: 2, calcParams: [],
    figures: [{ key: "vwap", title: "VWAP: ", type: "line" }],
    calc: function (list) {
      var pv = 0, vol = 0, day = null;
      return list.map(function (k) {
        var d = new Date(k.timestamp).toISOString().slice(0, 10);
        if (d !== day) { day = d; pv = 0; vol = 0; } // resets each session (UTC date is the same day for US hours)
        var tp = (k.high + k.low + k.close) / 3, v = k.volume || 0;
        pv += tp * v; vol += v;
        return { vwap: vol > 0 ? pv / vol : tp };
      });
    }
  });
  kc.registerIndicator({
    name: "ATR", shortName: "ATR", series: "normal", precision: 2, calcParams: [14],
    figures: [{ key: "atr", title: "ATR: ", type: "line" }],
    calc: function (list, ind) {
      var n = ind.calcParams[0] || 14, atr = null;
      return list.map(function (k, i) {
        var pc = i > 0 ? list[i - 1].close : k.close;
        var tr = Math.max(k.high - k.low, Math.abs(k.high - pc), Math.abs(k.low - pc));
        atr = atr == null ? tr : (atr * (n - 1) + tr) / n; // Wilder
        return { atr: i >= n - 1 ? atr : null };
      });
    }
  });

  var MAIN_IND = [
    ["MA", "Moyennes mobiles", "5,10,30,60"], ["EMA", "Moyennes exponentielles", "9,21,50"],
    ["VWAP", "VWAP de la séance", ""], ["BOLL", "Bandes de Bollinger", "20,2"],
    ["SAR", "SAR parabolique", "2,2,20"], ["BBI", "BBI", "3,6,12,24"]
  ];
  var SUB_IND = [
    ["VOL", "Volume", "5,10,20"], ["MACD", "MACD", "12,26,9"], ["RSI", "RSI", "6,12,24"],
    ["ATR", "ATR (Wilder)", "14"], ["KDJ", "Stochastique KDJ", "9,3,3"], ["OBV", "OBV", "30"],
    ["CCI", "CCI", "20"], ["WR", "Williams %R", "6,10,14"], ["DMI", "DMI / ADX", "14,6"],
    ["ROC", "ROC", "12,6"], ["MTM", "Momentum", "12,6"], ["TRIX", "TRIX", "12,9"],
    ["AO", "Awesome Oscillator", "5,34"], ["BIAS", "BIAS", "6,12,24"], ["PVT", "PVT", ""]
  ];

  var TOOLS = [
    ["segment", "Segment de tendance", '<path d="M3 13L13 3"/><circle cx="3" cy="13" r="1.3"/><circle cx="13" cy="3" r="1.3"/>'],
    ["rayLine", "Demi-droite", '<path d="M3 13L14 2"/><circle cx="3" cy="13" r="1.3"/>'],
    ["straightLine", "Droite", '<path d="M1 15L15 1"/>'],
    ["horizontalStraightLine", "Ligne horizontale", '<path d="M1 8h14"/>'],
    ["horizontalRayLine", "Demi-droite horizontale", '<path d="M4 8h11"/><circle cx="4" cy="8" r="1.3"/>'],
    ["verticalStraightLine", "Ligne verticale", '<path d="M8 1v14"/>'],
    ["priceLine", "Ligne de prix", '<path d="M3 10h11"/><path d="M3 10V6h5"/>'],
    ["parallelStraightLine", "Canal parallèle", '<path d="M1 11L11 1M5 15L15 5"/>'],
    ["priceChannelLine", "Canal de prix", '<path d="M1 12L12 3M1 15L15 5"/><path d="M4 9.5L14 1.5" stroke-dasharray="2 2"/>'],
    ["fibonacciLine", "Retracement de Fibonacci", '<path d="M2 3h12M2 6.5h12M2 9.5h12M2 13h12"/>'],
    ["simpleAnnotation", "Annotation", '<path d="M3 3h10v7H8l-3 3v-3H3z"/>'],
    ["simpleTag", "Étiquette de prix", '<path d="M2 8h7l3-3h2v6h-2l-3-3"/>']
  ];

  // ------------------------------------------------------------ state
  var INTERVALS = [["1m", "1m"], ["5m", "5m"], ["15m", "15m"], ["1h", "1h"], ["1d", "J"], ["1wk", "S"]];
  var saved = store("state") || {};
  var state = {
    interval: saved.interval || "5m",
    grid: saved.grid || 1,
    symbols: saved.symbols || ["TSLA", "NVDA", "AAPL", "SPY"],
    active: 0,
    indicators: saved.indicators || { VOL: "5,10,20", EMA: "9,21,50", VWAP: "" },
    watch: saved.watch || ["TSLA", "NVDA", "AMD", "AAPL", "SPY", "QQQ"],
    magnet: !!saved.magnet,
    theme: saved.theme || null
  };
  function persist() {
    store("state", { interval: state.interval, grid: state.grid, symbols: state.symbols, indicators: state.indicators, watch: state.watch, magnet: state.magnet, theme: state.theme });
    saveLayout();
  }
  var saveLayout = debounce(function () {
    api("PUT", "/api/layout", { interval: state.interval, grid: state.grid, symbols: state.symbols, indicators: state.indicators, watch: state.watch }).catch(function () {});
  }, 800);

  var cells = [];
  var pick = null; // {inputId} while choosing a price on the chart
  var replay = null;

  // ------------------------------------------------------------ theme
  function isDark() {
    var t = document.documentElement.getAttribute("data-theme");
    if (t) return t === "dark";
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  function css(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }
  function chartStyles() {
    var text = css("--muted"), grid = css("--grid"), border = css("--border"), up = css("--up"), down = css("--down"), fg = css("--text");
    return {
      grid: { horizontal: { color: grid }, vertical: { color: grid } },
      candle: {
        bar: { upColor: up, downColor: down, noChangeColor: text, upBorderColor: up, downBorderColor: down, noChangeBorderColor: text, upWickColor: up, downWickColor: down, noChangeWickColor: text },
        priceMark: { last: { upColor: up, downColor: down, noChangeColor: text }, high: { color: text }, low: { color: text } },
        tooltip: { text: { color: text } }
      },
      indicator: { tooltip: { text: { color: text } } },
      xAxis: { axisLine: { color: border }, tickLine: { color: border }, tickText: { color: text } },
      yAxis: { axisLine: { color: border }, tickLine: { color: border }, tickText: { color: text } },
      separator: { color: border },
      crosshair: {
        horizontal: { line: { color: text }, text: { backgroundColor: fg, color: css("--surface"), borderColor: fg } },
        vertical: { line: { color: text }, text: { backgroundColor: fg, color: css("--surface"), borderColor: fg } }
      },
      overlay: { line: { color: css("--accent") }, point: { color: css("--accent"), borderColor: css("--accent"), activeColor: css("--accent"), activeBorderColor: css("--accent") } }
    };
  }
  function applyTheme() {
    if (state.theme) document.documentElement.setAttribute("data-theme", state.theme); else document.documentElement.removeAttribute("data-theme");
    cells.forEach(function (c) { c.chart.setStyles(chartStyles()); });
  }

  // ------------------------------------------------------------ cells
  function makeCell(i) {
    var root = el("div", { class: "cell" });
    var head = el("div", { class: "cell-head" }, '<span class="sym"></span><span class="last mono"></span><span class="chg mono"></span><span class="name"></span><span class="src"></span>');
    var box = el("div", { class: "chart" });
    var msg = el("div", { class: "cell-msg" });
    root.appendChild(head); root.appendChild(box); root.appendChild(msg);
    $("#grid").appendChild(root);
    var chart = kc.init(box, { locale: "en-US" });
    chart.setStyles(chartStyles());
    var cell = { i: i, root: root, head: head, box: box, msg: msg, chart: chart, symbol: null, bars: [], meta: {}, drawIds: [], planIds: [], crosshair: null, indPanes: {}, loadSeq: 0 };
    chart.subscribeAction("onCrosshairChange", function (c) { cell.crosshair = c; });
    root.addEventListener("mousedown", function () { setActive(cell.i); }, true);
    box.addEventListener("click", function () { onChartClick(cell); });
    cells.push(cell);
    applyIndicators(cell);
    return cell;
  }

  function buildGrid() {
    stopReplay();
    cells.forEach(function (c) { kc.dispose(c.box); });
    cells = [];
    $("#grid").innerHTML = "";
    $("#grid").className = "grid g" + state.grid;
    for (var i = 0; i < state.grid; i++) {
      var c = makeCell(i);
      loadSymbol(c, state.symbols[i] || state.symbols[0] || "SPY");
    }
    setActive(Math.min(state.active, state.grid - 1));
  }

  function setActive(i) {
    state.active = i;
    cells.forEach(function (c) { c.root.classList.toggle("active", c.i === i); });
    var c = cells[i];
    if (c && document.activeElement !== $("#symbol-input")) $("#symbol-input").value = c.symbol || "";
  }
  function activeCell() { return cells[state.active] || cells[0]; }

  function loadSymbol(cell, sym) {
    sym = String(sym || "").trim().toUpperCase();
    if (!sym) return;
    if (replay && replay.cell === cell) stopReplay();
    var seq = ++cell.loadSeq;
    var sameSymbol = cell.symbol === sym;
    cell.symbol = sym;
    state.symbols[cell.i] = sym;
    persist();
    $(".sym", cell.head).textContent = sym;
    $(".name", cell.head).textContent = "";
    cell.msg.textContent = "Chargement de " + sym + "...";
    cell.msg.hidden = false;
    if (cell.i === state.active) $("#symbol-input").value = sym;
    api("GET", "/api/bars?symbol=" + encodeURIComponent(sym) + "&interval=" + state.interval).then(function (d) {
      if (seq !== cell.loadSeq) return;
      cell.bars = d.bars || [];
      cell.meta = d.meta || {};
      if (cell.meta.timezone) cell.chart.setTimezone(cell.meta.timezone);
      var last = cell.bars[cell.bars.length - 1];
      var prec = last && last.close < 1 ? 4 : 2;
      cell.chart.setPriceVolumePrecision(prec, 0);
      cell.chart.applyNewData(cell.bars);
      cell.msg.hidden = cell.bars.length > 0;
      if (!cell.bars.length) cell.msg.textContent = "Aucune donnée pour " + sym + ".";
      updateHead(cell);
      if (!sameSymbol || !cell.drawIds.length) loadDrawings(cell);
      setStatus();
    }).catch(function (e) {
      if (seq !== cell.loadSeq) return;
      cell.chart.clearData();
      cell.msg.textContent = sym + " : " + e.message;
      cell.msg.hidden = false;
    });
  }

  function updateHead(cell) {
    var b = cell.bars, n = b.length;
    $(".name", cell.head).textContent = cell.meta.name || "";
    $(".src", cell.head).textContent = (cell.meta.source === "offline" ? "hors ligne" : "Yahoo, différé") + (cell.meta.exchange ? " · " + cell.meta.exchange : "");
    if (!n) return;
    var last = b[n - 1].close, ref = null;
    // change versus the previous session's close for intraday, the previous bar otherwise
    var lastDay = new Date(b[n - 1].timestamp).toISOString().slice(0, 10);
    for (var i = n - 2; i >= 0; i--) { if (new Date(b[i].timestamp).toISOString().slice(0, 10) !== lastDay) { ref = b[i].close; break; } }
    if (ref == null || ["1d", "1wk"].indexOf(state.interval) >= 0) ref = n > 1 ? b[n - 2].close : null;
    $(".last", cell.head).textContent = fmt(last, last < 1 ? 4 : 2);
    var chg = $(".chg", cell.head);
    if (ref) { var p = (last / ref - 1) * 100; chg.textContent = (p >= 0 ? "+" : "") + fmt(p, 2) + " %"; chg.className = "chg mono " + (p >= 0 ? "up" : "down"); }
  }

  function setStatus() {
    var c = activeCell();
    $("#status").textContent = c && c.bars.length ? (c.bars.length + " bougies · " + state.interval) : "";
  }

  // ------------------------------------------------------------ indicators
  function parseParams(s) {
    var p = String(s || "").split(/[,; ]+/).filter(Boolean).map(Number).filter(function (x) { return !isNaN(x); });
    return p.length ? p : null;
  }
  function isMain(name) { return MAIN_IND.some(function (x) { return x[0] === name; }); }
  function applyIndicators(cell) {
    var chart = cell.chart;
    // remove what is no longer wanted
    Object.keys(cell.indPanes).forEach(function (name) {
      if (!(name in state.indicators)) { chart.removeIndicator(cell.indPanes[name], name); delete cell.indPanes[name]; }
    });
    Object.keys(state.indicators).forEach(function (name) {
      var params = parseParams(state.indicators[name]);
      if (cell.indPanes[name]) {
        if (params) chart.overrideIndicator({ name: name, calcParams: params }, cell.indPanes[name]);
        return;
      }
      var create = { name: name };
      if (params) create.calcParams = params;
      var paneId = isMain(name) ? chart.createIndicator(create, true, { id: "candle_pane" }) : chart.createIndicator(create, false, { height: 90 });
      if (paneId) cell.indPanes[name] = paneId;
    });
  }
  function renderIndicatorPanel() {
    function block(list, root) {
      root.innerHTML = "";
      list.forEach(function (it) {
        var name = it[0], on = name in state.indicators;
        var row = el("div", { class: "ind" });
        var id = "ind-" + name;
        row.innerHTML = '<input type="checkbox" id="' + id + '"' + (on ? " checked" : "") + '><label for="' + id + '"><b>' + esc(name) + "</b>" + esc(it[1]) + "</label>" +
          (it[2] ? '<input type="text" class="mono" value="' + esc(on ? state.indicators[name] || it[2] : it[2]) + '" aria-label="Paramètres ' + esc(name) + '">' : "<span></span>");
        var cb = row.querySelector("input[type=checkbox]"), tx = row.querySelector("input[type=text]");
        cb.addEventListener("change", function () {
          if (cb.checked) state.indicators[name] = tx ? tx.value : ""; else delete state.indicators[name];
          cells.forEach(applyIndicators); persist();
        });
        if (tx) tx.addEventListener("change", function () {
          if (!(name in state.indicators)) return;
          state.indicators[name] = tx.value; cells.forEach(applyIndicators); persist();
        });
        root.appendChild(row);
      });
    }
    block(MAIN_IND, $("#ind-main"));
    block(SUB_IND, $("#ind-sub"));
  }

  // ------------------------------------------------------------ drawings
  function overlayEvents(cell) {
    var save = function () { scheduleSave(cell); return false; };
    return {
      onDrawEnd: save, onPressedMoveEnd: save,
      onRemoved: function (e) {
        var id = e && e.overlay && e.overlay.id;
        cell.drawIds = cell.drawIds.filter(function (x) { return x !== id; });
        scheduleSave(cell); return false;
      }
    };
  }
  function startTool(name) {
    var cell = activeCell();
    if (!cell) return;
    var o = { name: name, mode: state.magnet ? "weak_magnet" : "normal" };
    if (name === "simpleAnnotation" || name === "simpleTag") {
      var text = window.prompt(name === "simpleAnnotation" ? "Texte de l'annotation" : "Texte de l'étiquette", "");
      if (text == null) return;
      o.extendData = text;
    }
    var ev = overlayEvents(cell);
    Object.keys(ev).forEach(function (k) { o[k] = ev[k]; });
    var id = cell.chart.createOverlay(o, "candle_pane");
    if (id) cell.drawIds.push(id);
    toast("Clique sur le graphique pour placer les points. Clic droit sur un dessin pour l'effacer.");
  }
  function serialize(cell) {
    return cell.drawIds.map(function (id) { return cell.chart.getOverlayById(id); }).filter(function (o) {
      return o && o.points && o.points.length && o.currentStep === -1; // -1 = finished drawing
    }).map(function (o) {
      return { name: o.name, points: o.points.map(function (p) { return { timestamp: p.timestamp, value: p.value }; }), extendData: o.extendData == null ? null : o.extendData, lock: !!o.lock, visible: o.visible !== false };
    });
  }
  function scheduleSave(cell) {
    clearTimeout(cell.saveTimer);
    var sym = cell.symbol;
    cell.saveTimer = setTimeout(function () {
      if (sym !== cell.symbol) return;
      api("PUT", "/api/drawings/" + encodeURIComponent(sym), { drawings: serialize(cell) })
        .catch(function (e) { toast("Dessins non sauvegardés : " + e.message); });
    }, 400);
  }
  function clearDrawings(cell) {
    cell.drawIds.forEach(function (id) { cell.chart.removeOverlay({ id: id }); });
    cell.drawIds = [];
  }
  function loadDrawings(cell) {
    var sym = cell.symbol;
    clearDrawings(cell);
    api("GET", "/api/drawings/" + encodeURIComponent(sym)).then(function (d) {
      if (sym !== cell.symbol) return;
      restoreDrawings(cell, d.drawings || []);
    }).catch(function () {});
  }
  function restoreDrawings(cell, list) {
    var ev = overlayEvents(cell);
    list.forEach(function (d) {
      if (!d || !d.name || !d.points) return;
      var o = { name: d.name, points: d.points, lock: !!d.lock, visible: d.visible !== false };
      if (d.extendData != null) o.extendData = d.extendData;
      Object.keys(ev).forEach(function (k) { o[k] = ev[k]; });
      var id = cell.chart.createOverlay(o, "candle_pane");
      if (id) cell.drawIds.push(id);
    });
  }

  function renderTools() {
    var nav = $("#tools");
    TOOLS.forEach(function (t) {
      var b = el("button", { title: t[1], "aria-label": t[1] }, "<svg viewBox='0 0 16 16'>" + t[2] + "</svg>");
      b.addEventListener("click", function () { startTool(t[0]); });
      nav.appendChild(b);
    });
    nav.appendChild(el("hr"));
    var magnet = el("button", { title: "Aimant : accroche les points aux prix OHLC", "aria-label": "Aimant" }, "<svg viewBox='0 0 16 16'><path d='M4 2v6a4 4 0 0 0 8 0V2'/><path d='M4 5h3M9 5h3'/></svg>");
    magnet.classList.toggle("on", state.magnet);
    magnet.addEventListener("click", function () { state.magnet = !state.magnet; magnet.classList.toggle("on", state.magnet); persist(); toast(state.magnet ? "Aimant activé" : "Aimant désactivé"); });
    nav.appendChild(magnet);
    var lock = el("button", { title: "Verrouiller / déverrouiller les dessins", "aria-label": "Verrouiller" }, "<svg viewBox='0 0 16 16'><rect x='3' y='7' width='10' height='7' rx='1.5'/><path d='M5.5 7V5a2.5 2.5 0 0 1 5 0v2'/></svg>");
    lock.addEventListener("click", function () {
      var c = activeCell(), any = c.drawIds.some(function (id) { var o = c.chart.getOverlayById(id); return o && !o.lock; });
      c.drawIds.forEach(function (id) { c.chart.overrideOverlay({ id: id, lock: any }); });
      lock.classList.toggle("on", any); scheduleSave(c); toast(any ? "Dessins verrouillés" : "Dessins déverrouillés");
    });
    nav.appendChild(lock);
    var eye = el("button", { title: "Masquer / afficher les dessins", "aria-label": "Masquer" }, "<svg viewBox='0 0 16 16'><path d='M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z'/><circle cx='8' cy='8' r='2'/></svg>");
    eye.addEventListener("click", function () {
      var c = activeCell(), anyVisible = c.drawIds.some(function (id) { var o = c.chart.getOverlayById(id); return o && o.visible !== false; });
      c.drawIds.forEach(function (id) { c.chart.overrideOverlay({ id: id, visible: !anyVisible }); });
      eye.classList.toggle("on", anyVisible); scheduleSave(c);
    });
    nav.appendChild(eye);
    var trash = el("button", { title: "Effacer tous les dessins de ce symbole", "aria-label": "Tout effacer" }, "<svg viewBox='0 0 16 16'><path d='M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.7 9h5.6l.7-9'/></svg>");
    trash.addEventListener("click", function () {
      var c = activeCell();
      if (!c.drawIds.length || !window.confirm("Effacer les " + c.drawIds.length + " dessins de " + c.symbol + " ?")) return;
      clearDrawings(c); scheduleSave(c);
    });
    nav.appendChild(trash);
    nav.appendChild(el("hr"));
    var shot = el("button", { title: "Exporter une image du graphique", "aria-label": "Capture" }, "<svg viewBox='0 0 16 16'><rect x='1.5' y='4' width='13' height='9.5' rx='1.5'/><circle cx='8' cy='8.7' r='2.5'/><path d='M5.5 4l1-1.5h3l1 1.5'/></svg>");
    shot.addEventListener("click", function () {
      var c = activeCell(), a = document.createElement("a");
      a.href = c.chart.getConvertPictureUrl(true, "png", css("--surface"));
      a.download = (c.symbol || "chart") + "-" + state.interval + ".png"; a.click();
    });
    nav.appendChild(shot);
  }

  // ------------------------------------------------------------ chart clicks: price pick and replay start
  function priceAt(cell) {
    var c = cell.crosshair;
    if (!c || c.paneId !== "candle_pane" || c.y == null) return null;
    var p = cell.chart.convertFromPixel([{ x: c.x, y: c.y }], { paneId: "candle_pane" });
    p = Array.isArray(p) ? p[0] : p;
    return p && p.value != null ? p.value : null;
  }
  function onChartClick(cell) {
    if (pick) {
      var v = priceAt(cell);
      if (v == null) return;
      var dec = v < 1 ? 4 : 2;
      $("#" + pick).value = v.toFixed(dec);
      endPick();
      if (cell.symbol) drawPlanLines(cell);
      return;
    }
    if (replay && replay.waiting && replay.cell === cell) {
      var idx = cell.crosshair && cell.crosshair.dataIndex;
      if (idx == null || idx < 1 || idx >= cell.bars.length - 1) { toast("Choisis une bougie déjà passée."); return; }
      beginReplay(cell, idx);
    }
  }
  function startPick(id) {
    endPick();
    pick = id;
    $$(".pick").forEach(function (b) { b.classList.toggle("on", b.dataset.pick === id); });
    cells.forEach(function (c) { c.box.classList.add("picking"); });
    toast("Clique sur le graphique pour choisir le prix.");
  }
  function endPick() {
    pick = null;
    $$(".pick").forEach(function (b) { b.classList.remove("on"); });
    cells.forEach(function (c) { c.box.classList.remove("picking"); });
  }

  // ------------------------------------------------------------ replay
  function startReplay() {
    var cell = activeCell();
    if (!cell || cell.bars.length < 3) { toast("Pas assez de bougies pour une relecture."); return; }
    stopReplay();
    replay = { cell: cell, waiting: true, idx: 0, timer: null, full: cell.bars.slice() };
    $("#replay-bar").hidden = false; $("#replay-hint").hidden = false; $("#replay-ctrl").hidden = true;
    $("#replay-btn").classList.add("on");
    resizeAll();
  }
  function beginReplay(cell, idx) {
    replay.waiting = false; replay.idx = idx + 1;
    cell.chart.applyNewData(replay.full.slice(0, replay.idx));
    $("#replay-hint").hidden = true; $("#replay-ctrl").hidden = false;
    replayPos();
  }
  function replayStep() {
    if (!replay || replay.waiting) return;
    if (replay.idx >= replay.full.length) { replayPause(); toast("Fin des données : relecture terminée."); return; }
    replay.cell.chart.updateData(replay.full[replay.idx]);
    replay.idx++;
    replayPos();
  }
  function replayPos() {
    var b = replay.full[replay.idx - 1];
    $("#rp-pos").textContent = b ? (new Date(b.timestamp).toLocaleString("fr-FR", { timeZone: replay.cell.meta.timezone || undefined, dateStyle: "short", timeStyle: "short" }) + " · " + fmt(b.close) + " · " + replay.idx + "/" + replay.full.length) : "";
  }
  function replayPlay() {
    if (!replay || replay.waiting) return;
    if (replay.timer) return replayPause();
    replay.timer = setInterval(replayStep, Number($("#rp-speed").value));
    $("#rp-play").textContent = "Pause";
  }
  function replayPause() { if (replay && replay.timer) { clearInterval(replay.timer); replay.timer = null; } $("#rp-play").textContent = "Lecture"; }
  function stopReplay() {
    if (!replay) return;
    replayPause();
    var cell = replay.cell;
    if (cells.indexOf(cell) >= 0 && !replay.waiting) cell.chart.applyNewData(replay.full);
    replay = null;
    $("#replay-bar").hidden = true; $("#replay-btn").classList.remove("on");
    resizeAll();
  }
  function resizeAll() { setTimeout(function () { cells.forEach(function (c) { c.chart.resize(); }); }, 30); }

  // ------------------------------------------------------------ plan
  function val(id) { var v = parseFloat(String($("#" + id).value).replace(",", ".")); return isNaN(v) ? null : v; }
  function drawPlanLines(cell) {
    cell.planIds.forEach(function (id) { cell.chart.removeOverlay({ id: id }); });
    cell.planIds = [];
    var last = cell.bars[cell.bars.length - 1];
    if (!last) return;
    var e = val("pl-entry"), s = val("pl-stop"), t = val("pl-target");
    if (t == null && e != null && s != null && s < e) t = e + 2 * (e - s);
    [[e, css("--accent")], [s, css("--down")], [t, css("--up")]].forEach(function (x) {
      if (x[0] == null) return;
      var id = cell.chart.createOverlay({ name: "horizontalStraightLine", groupId: "plan", lock: true, points: [{ timestamp: last.timestamp, value: x[0] }], styles: { line: { color: x[1], style: "dashed", dashedValue: [6, 4] } } }, "candle_pane");
      if (id) cell.planIds.push(id);
    });
  }
  function runPlan() {
    var cell = activeCell(), out = $("#pl-out"), btn = $("#pl-run");
    var body = { symbol: cell.symbol, entry: val("pl-entry"), stop: val("pl-stop"), target: val("pl-target") };
    if (body.entry == null || body.stop == null) { out.innerHTML = '<p class="err">Il faut au moins une entrée et un stop.</p>'; return; }
    if (body.stop >= body.entry) { out.innerHTML = '<p class="err">Le stop doit être sous l\'entrée (achat seulement).</p>'; return; }
    drawPlanLines(cell);
    btn.disabled = true; btn.textContent = "Simulation en cours...";
    out.innerHTML = '<p class="muted small">trader-exit, puis 10 000 séances simulées, puis la taille de position...</p>';
    api("POST", "/api/plan", body).then(function (r) { renderPlan(r, cell); })
      .catch(function (e) { out.innerHTML = '<p class="err">' + esc(e.message) + "</p>"; })
      .then(function () { btn.disabled = false; btn.textContent = "Planifier"; });
  }
  function renderPlan(r, cell) {
    var out = $("#pl-out"), h = "";
    if (r.error) { out.innerHTML = '<p class="err">' + esc(r.error) + "</p>"; return; }
    var p = r.plan || {};
    if (p.target) { $("#pl-target").value = p.target; drawPlanLines(cell); }
    h += '<div class="kv"><span>Entrée</span><span>' + fmt(p.entry) + "</span><span>Stop (" + esc(p.stop_label || "") + ")</span><span>" + fmt(p.stop) +
      "</span><span>Objectif</span><span>" + fmt(p.target) + "</span><span>Risque par action</span><span>" + fmt(p.risk_per_share) +
      "</span><span>Gain / risque</span><span>" + fmt(p.reward_risk) + " R</span><span>Être à plat avant</span><span>" + esc(p.flat_by || "-") + "</span></div>";
    (p.warnings || []).forEach(function (w) { h += '<p class="warn">' + esc(w) + "</p>"; });
    var tm = r.timemachine;
    if (tm && tm.mc) {
      var m = tm.mc;
      h += "<h3>Time Machine · " + fmt(m.replays, 0) + " séances</h3>";
      h += '<div class="odds"><div class="t" style="flex:' + m.p_target + '"></div><div class="s" style="flex:' + m.p_stop + '"></div><div class="n" style="flex:' + m.p_neither + '"></div></div>';
      h += '<div class="legend"><span><i style="background:var(--up)"></i>Objectif d\'abord ' + pct(m.p_target) + '</span><span><i style="background:var(--down)"></i>Stop d\'abord ' + pct(m.p_stop) + '</span><span><i style="background:var(--muted)"></i>Ni l\'un ni l\'autre ' + pct(m.p_neither) + "</span></div>";
      h += '<div class="kv"><span>R moyen</span><span>' + (m.avg_R >= 0 ? "+" : "") + fmt(m.avg_R, 3) + "</span><span>Clôture médiane</span><span>" + fmt(m.median_close) + "</span><span>Clôture 5 % / 95 %</span><span>" + fmt(m.p5_close) + " / " + fmt(m.p95_close) + "</span></div>";
      if (tm.verdict) h += '<div class="verdict">' + esc(tm.verdict) + "</div>";
      (tm.warnings || []).forEach(function (w) { h += '<p class="warn">' + esc(w) + "</p>"; });
    } else if (r.timemachine_error) {
      h += '<p class="err">Time Machine : ' + esc(r.timemachine_error) + "</p>";
    }
    var b = r.budget;
    if (b && b.sizing) {
      var s = b.sizing;
      h += "<h3>Taille de position</h3>";
      if (s.refusal) h += '<p class="err">' + esc(s.refusal) + "</p>";
      else {
        h += '<div class="kv"><span>Actions</span><span>' + fmt(s.qty, 0) + "</span><span>Coût</span><span>" + fmt(s.cost) + " $</span><span>Perte max au stop</span><span>" + fmt(s.max_loss) + " $</span><span>Limite qui décide</span><span>" + esc(s.bound_by || "-") + "</span></div>";
        if (b.orders && b.orders[0]) h += '<div class="order mono">' + esc(b.orders[0].why || "") + "</div>";
      }
      (b.notes || []).forEach(function (n) { h += '<p class="warn">' + esc(n) + "</p>"; });
    } else if (r.budget_error) {
      h += '<p class="err">Budget : ' + esc(r.budget_error) + "</p>";
    }
    h += '<div class="row"><button class="btn" id="pl-alerts">Créer les alertes du plan</button></div>';
    h += '<p class="muted small">Plan écrit dans trader/plan.json. Aucun ordre n\'est passé. Pour aller plus loin, demande à Claude /trader-risk puis /trader-broker (papier par défaut, ton « oui » est toujours demandé).</p>';
    out.innerHTML = h;
    $("#pl-alerts").addEventListener("click", function () {
      api("POST", "/api/alerts/from-plan").then(function (x) { toast(x.ok ? "Alertes stop, objectif et « flat by » créées" : "Alertes refusées"); if (!x.ok) out.insertAdjacentHTML("beforeend", '<p class="err">' + esc(x.output) + "</p>"); loadAlerts(); })
        .catch(function (e) { toast(e.message); });
    });
  }

  // ------------------------------------------------------------ alerts
  function loadAlerts() {
    api("GET", "/api/alerts").then(function (d) {
      var ul = $("#al-list"); ul.innerHTML = "";
      if (!d.rules.length) { ul.appendChild(el("li", { class: "static muted" }, "Aucune règle pour l'instant.")); return; }
      d.rules.slice().reverse().forEach(function (r) {
        var what = r.type === "time" ? ("à " + (r.at || "")) : r.type === "pct_move" ? (r.pct + " %") : r.price != null ? fmt(r.price) : "";
        var li = el("li", { class: "static" }, '<span class="s">' + esc(r.symbol || "NYSE") + '</span><span class="tags">' + esc(r.type) + " " + esc(what) + (r.date ? " · " + esc(r.date) : "") + (r.note ? " · " + esc(r.note) : "") + "</span>");
        ul.appendChild(li);
      });
    }).catch(function () {});
  }
  function addAlert(kind) {
    var cell = activeCell(), price = val("al-price");
    if (price == null) { toast("Choisis un prix d'abord."); return; }
    api("POST", "/api/alerts", { symbol: cell.symbol, kind: kind, price: price, note: "Chart Lab" }).then(function (x) {
      toast(x.ok ? "Alerte " + cell.symbol + (kind === "above" ? " au-dessus de " : " en dessous de ") + fmt(price) : "Alerte refusée");
      loadAlerts();
    }).catch(function (e) { toast(e.message); });
  }

  // ------------------------------------------------------------ watchlists
  function renderWatch() {
    var ul = $("#watch-mine"); ul.innerHTML = "";
    state.watch.forEach(function (sym) {
      var li = el("li", null, '<span class="s">' + esc(sym) + '</span><span class="tags"></span><button class="x" title="Retirer" aria-label="Retirer ' + esc(sym) + '">×</button>');
      li.addEventListener("click", function (e) {
        if (e.target.classList.contains("x")) { state.watch = state.watch.filter(function (x) { return x !== sym; }); persist(); renderWatch(); return; }
        loadSymbol(activeCell(), sym);
      });
      ul.appendChild(li);
    });
    api("GET", "/api/watchlist").then(function (d) {
      var ul2 = $("#watch-scan"); ul2.innerHTML = "";
      $("#scan-empty").hidden = d.scanner.length > 0;
      $("#scan-asof").textContent = d.as_of ? "· " + (d.mode || "") + " " + d.as_of : "";
      d.scanner.forEach(function (h) {
        var li = el("li", null, '<span class="s">' + esc(h.symbol) + '</span><span class="tags">' + esc((h.setups || []).join(", ")) + "</span>" + (h.price != null ? '<span class="mono muted">' + fmt(h.price) + "</span>" : ""));
        li.addEventListener("click", function () { loadSymbol(activeCell(), h.symbol); });
        ul2.appendChild(li);
      });
    }).catch(function () {});
  }

  // ------------------------------------------------------------ top bar
  function renderSeg(root, items, current, onPick) {
    root.innerHTML = "";
    items.forEach(function (it) {
      var b = el("button", { type: "button", title: it[2] || it[1] }, esc(it[1]));
      b.classList.toggle("on", String(it[0]) === String(current));
      b.addEventListener("click", function () { onPick(it[0]); });
      root.appendChild(b);
    });
  }
  function renderTop() {
    renderSeg($("#interval-seg"), INTERVALS, state.interval, function (v) {
      state.interval = v; persist(); renderTop();
      stopReplay();
      cells.forEach(function (c) { loadSymbol(c, c.symbol); });
    });
    renderSeg($("#grid-seg"), [[1, "1", "Un graphique"], [2, "2", "Deux graphiques"], [4, "4", "Quatre graphiques"]], state.grid, function (v) {
      state.grid = v; persist(); renderTop(); buildGrid();
    });
  }

  function wire() {
    $("#symbol-form").addEventListener("submit", function (e) {
      e.preventDefault();
      var v = $("#symbol-input").value.trim().toUpperCase();
      if (v) { loadSymbol(activeCell(), v); $("#symbol-input").blur(); }
    });
    var suggest = debounce(function () {
      var q = $("#symbol-input").value.trim();
      if (q.length < 2) return;
      api("GET", "/api/search?q=" + encodeURIComponent(q)).then(function (d) {
        var dl = $("#symbol-suggest"); dl.innerHTML = "";
        d.results.forEach(function (r) { var o = el("option", { value: r.symbol }); o.textContent = r.name + " · " + r.exchange; dl.appendChild(o); });
      }).catch(function () {});
    }, 300);
    $("#symbol-input").addEventListener("input", suggest);

    $$(".tabs button").forEach(function (b) {
      b.addEventListener("click", function () {
        $$(".tabs button").forEach(function (x) { x.classList.toggle("on", x === b); });
        $$(".tab").forEach(function (t) { t.hidden = t.dataset.tab !== b.dataset.tab; });
        if (b.dataset.tab === "watch") renderWatch();
        if (b.dataset.tab === "alerts") loadAlerts();
        store("tab", b.dataset.tab);
      });
    });
    $("#panel-btn").addEventListener("click", function () {
      var p = $("#panel");
      if (window.matchMedia("(max-width: 860px)").matches) p.classList.toggle("open"); else p.classList.toggle("closed");
      resizeAll();
    });
    $("#theme-btn").addEventListener("click", function () { state.theme = isDark() ? "light" : "dark"; persist(); applyTheme(); });
    if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () { if (!state.theme) applyTheme(); });

    $("#watch-form").addEventListener("submit", function (e) {
      e.preventDefault();
      var v = $("#watch-input").value.trim().toUpperCase();
      if (v && state.watch.indexOf(v) < 0) { state.watch.push(v); persist(); renderWatch(); }
      $("#watch-input").value = "";
    });

    $$(".pick").forEach(function (b) { b.addEventListener("click", function () { if (pick === b.dataset.pick) endPick(); else startPick(b.dataset.pick); }); });
    $("#pl-last").addEventListener("click", function () {
      var c = activeCell(), last = c.bars[c.bars.length - 1];
      if (last) { $("#pl-entry").value = last.close; drawPlanLines(c); }
    });
    ["pl-entry", "pl-stop", "pl-target"].forEach(function (id) { $("#" + id).addEventListener("change", function () { drawPlanLines(activeCell()); }); });
    $("#pl-run").addEventListener("click", runPlan);
    $$("[data-alert]").forEach(function (b) { b.addEventListener("click", function () { addAlert(b.dataset.alert); }); });

    $("#replay-btn").addEventListener("click", function () { if (replay) stopReplay(); else startReplay(); });
    $("#rp-play").addEventListener("click", replayPlay);
    $("#rp-step").addEventListener("click", function () { replayPause(); replayStep(); });
    $("#rp-exit").addEventListener("click", stopReplay);
    $("#rp-speed").addEventListener("change", function () { if (replay && replay.timer) { replayPause(); replayPlay(); } });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { endPick(); }
      if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
      if (replay && !replay.waiting && (e.key === "ArrowRight")) { e.preventDefault(); replayPause(); replayStep(); }
      if (replay && !replay.waiting && e.key === " ") { e.preventDefault(); replayPlay(); }
    });
    window.addEventListener("resize", debounce(function () { cells.forEach(function (c) { c.chart.resize(); }); }, 100));
    // refresh the last bars every minute on intraday intervals, except during a replay
    setInterval(function () {
      if (["1m", "5m", "15m"].indexOf(state.interval) < 0 || document.hidden) return;
      cells.forEach(function (c) { if (!(replay && replay.cell === c) && c.symbol) refreshBars(c); });
    }, 60000);
  }

  function refreshBars(cell) {
    var sym = cell.symbol, seq = cell.loadSeq;
    api("GET", "/api/bars?symbol=" + encodeURIComponent(sym) + "&interval=" + state.interval).then(function (d) {
      if (seq !== cell.loadSeq || sym !== cell.symbol || !d.bars || !d.bars.length) return;
      var lastTs = cell.bars.length ? cell.bars[cell.bars.length - 1].timestamp : 0;
      d.bars.forEach(function (b) { if (b.timestamp >= lastTs) cell.chart.updateData(b); });
      cell.bars = d.bars;
      updateHead(cell);
    }).catch(function () {});
  }

  // ------------------------------------------------------------ boot
  function boot() {
    if (!kc) { document.body.innerHTML = "<p style='padding:24px'>La librairie de graphiques n'a pas pu se charger.</p>"; return; }
    if (state.theme) document.documentElement.setAttribute("data-theme", state.theme);
    renderTools();
    renderTop();
    renderIndicatorPanel();
    wire();
    var tab = store("tab");
    if (tab && $('.tabs button[data-tab="' + tab + '"]')) $('.tabs button[data-tab="' + tab + '"]').click();
    api("GET", "/api/status").then(function (s) { if (s.offline) toast("Mode hors ligne : données lues sur le disque"); }).catch(function () {});
    if (!saved.symbols) {
      // first visit on this browser: take the layout saved on disk, if any
      api("GET", "/api/layout").then(function (l) {
        ["interval", "grid", "symbols", "indicators", "watch"].forEach(function (k) { if (l[k] != null) state[k] = l[k]; });
      }).catch(function () {}).then(function () { renderTop(); renderIndicatorPanel(); buildGrid(); });
    } else {
      buildGrid();
    }
  }
  window.chartLab = { cells: function () { return cells; }, state: state };
  boot();
})();
