/* MINDLOCK Terminal: front end. Talks only to the local server (server.py). */
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const autoDigits = (x) => (Math.abs(x) < 10 ? 5 : 2);
const digitsFor = (sym, x) => (/JPY/.test(sym || "") && /=X$/.test(sym || "") ? 3 : autoDigits(x));
const fmtP = (sym, x) => fmt(x, x == null ? undefined : digitsFor(sym, x));
const fmt = (x, d) => (x == null || isNaN(x) ? "—" : Number(x).toLocaleString("fr-FR", { minimumFractionDigits: d ?? autoDigits(x), maximumFractionDigits: d ?? autoDigits(x) }));
const usd = (x) => (x == null ? "—" : (x < 0 ? "-" : "") + Math.abs(x).toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " $");
const sgnUsd = (x) => (x == null ? "—" : (x > 0 ? "+" : "") + usd(x));
const pct = (x) => (x == null ? "—" : (x >= 0 ? "+" : "") + fmt(x, 2) + " %");
const cls = (x) => (x == null ? "" : x >= 0 ? "up" : "down");
const chg = (x) => `<span class="chg ${cls(x)}">${pct(x)}</span>`;
const icon = (id) => `<svg><use href="#i-${id}"/></svg>`;
const SIDE = { buy: "BUY", sell: "SELL" };
const side = (s) => `<span class="side ${s}">${SIDE[s] || s}</span>`;
const REASON = { TP: "TP", SL: "SL", BE: "BE", cible: "TP", stop: "SL", "break-even": "BE", "signal opposé": "Signal opposé", "fin de session": "Fin de session", "clôture manuelle": "Manuelle", manuel: "Manuelle" };
const reason = (r) => REASON[r] || r;
const nyTime = (t, withDay = true) => new Date(t * 1000).toLocaleString("fr-FR", { timeZone: "America/New_York", ...(withDay ? { day: "2-digit", month: "2-digit" } : {}), hour: "2-digit", minute: "2-digit" });
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};
const empty = (text, ic = "layers") => `<div class="empty">${icon(ic)}${text}</div>`;
const table = (head, rows, extra = "") => `<div class="tw"><table ${extra}><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;

async function api(path, body) {
  const opt = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({ error: "réponse invalide" }));
  if (!r.ok || j.error) throw new Error(j.error || "HTTP " + r.status);
  return j;
}
let toastTimer;
function toast(msg, err) {
  const t = $("toast"); t.textContent = msg; t.className = "toast" + (err ? " err" : "");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), err ? 7000 : 3500);
}
function confirmBox(html, yes = "Confirmer", kind = "gold") {
  return new Promise((res) => {
    $("mbody").innerHTML = html; $("m-yes").textContent = yes; $("m-yes").className = "btn " + kind;
    $("modal").classList.remove("hidden"); $("m-yes").focus();
    const done = (v) => { $("modal").classList.add("hidden"); $("m-yes").onclick = $("m-no").onclick = null; document.removeEventListener("keydown", key); res(v); };
    const key = (e) => { if (e.key === "Escape") done(false); };
    document.addEventListener("keydown", key);
    $("m-yes").onclick = () => done(true); $("m-no").onclick = () => done(false);
  });
}

/* ------------------------------------------------------------ state */
const TFS = ["M5", "M15", "M30", "H1", "H4"];
const S = {
  sym: store.get("dt.sym", "GC=F"),
  watch: store.get("dt.watch", ["GC=F", "NQ=F", "YM=F", "ES=F", "EURUSD=X", "GBPUSD=X", "USDJPY=X", "BTC-USD", "AAPL", "NVDA"]),
  period: store.get("dt.mltf", "M30"), plan: null, order: null, risk: null, bias: null, chartData: null,
};

/* ------------------------------------------------------------ chart */
const LW = window.LightweightCharts;
let chart, candles, volume, e20, e50, e100, vw, planLines = [];
function initChart() {
  chart = LW.createChart($("chart"), {
    autoSize: true,
    layout: { background: { color: "transparent" }, textColor: "#8b92a1", fontFamily: "Inter, system-ui", fontSize: 11 },
    grid: { vertLines: { color: "rgba(255,255,255,0.035)" }, horzLines: { color: "rgba(255,255,255,0.035)" } },
    rightPriceScale: { borderColor: "rgba(255,255,255,0.08)", scaleMargins: { top: 0.08, bottom: 0.2 } },
    timeScale: { borderColor: "rgba(255,255,255,0.08)", timeVisible: true, secondsVisible: false, rightOffset: 6 },
    crosshair: { mode: 0, vertLine: { color: "rgba(217,181,106,.45)", labelBackgroundColor: "#b8893c" }, horzLine: { color: "rgba(217,181,106,.45)", labelBackgroundColor: "#b8893c" } },
    watermark: { visible: true, text: "MINDLOCK", color: "rgba(217,181,106,0.035)", fontSize: 56, fontFamily: "Inter", fontStyle: "800" },
  });
  candles = chart.addCandlestickSeries({ upColor: "#2fd8a1", downColor: "#ff5c7c", borderVisible: false, wickUpColor: "#2fd8a1", wickDownColor: "#ff5c7c" });
  volume = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.86, bottom: 0 } });
  const line = (color) => chart.addLineSeries({ color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  e20 = line("#ffd23f"); e50 = line("#2ecc71"); e100 = line("#ff4d4d");
  vw = chart.addLineSeries({ color: "rgba(217,181,106,.6)", lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });
}
function emaSeries(bars, n) {           // ta.ema: seeded with the SMA of the first n closes
  if (bars.length < n) return [];
  const k = 2 / (n + 1); let e = bars.slice(0, n).reduce((a, b) => a + b.c, 0) / n;
  const out = [{ time: bars[n - 1].time, value: e }];
  for (let i = n; i < bars.length; i++) { e = bars[i].c * k + e * (1 - k); out.push({ time: bars[i].time, value: e }); }
  return out;
}
function vwapSeries(bars) {
  let pv = 0, v = 0, day = null;
  return bars.map((b) => {
    const d = new Date(b.time * 1000).toISOString().slice(0, 10);
    if (d !== day) { pv = 0; v = 0; day = d; }
    pv += ((b.h + b.l + b.c) / 3) * b.v; v += b.v;
    return { time: b.time, value: v ? pv / v : b.c };
  });
}
function drawPlan() {
  planLines.forEach((l) => candles.removePriceLine(l)); planLines = [];
  if (!S.plan || S.plan.symbol !== S.sym) return;
  const add = (price, color, title) => planLines.push(candles.createPriceLine({ price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title }));
  add(S.plan.entry, "#eceef3", "Entrée"); add(S.plan.stop, "#ff5c7c", "SL"); add(S.plan.target, "#2fd8a1", "TP");
}
let chartReq = 0;
async function loadChart() {
  const req = ++chartReq;
  try {
    const d = await api(`/api/chart?symbol=${encodeURIComponent(S.sym)}&period=${S.period}`);
    if (req !== chartReq) return;
    S.chartData = d;
    const off = d.gmtoffset || 0;
    const bars = d.bars.map((b) => ({ ...b, time: b.t + off }));
    const prec = digitsFor(d.symbol, d.price || (bars.length ? bars[bars.length - 1].c : 100));
    candles.applyOptions({ priceFormat: { type: "price", precision: prec, minMove: Math.pow(10, -prec) } });
    candles.setData(bars.map((b) => ({ time: b.time, open: b.o, high: b.h, low: b.l, close: b.c })));
    volume.setData(bars.map((b) => ({ time: b.time, value: b.v, color: b.c >= b.o ? "rgba(47,216,161,.22)" : "rgba(255,92,124,.22)" })));
    e20.setData(emaSeries(bars, 20)); e50.setData(emaSeries(bars, 50)); e100.setData(emaSeries(bars, 100));
    vw.setData(["1D", "5D"].includes(S.period) ? vwapSeries(bars) : []);
    chart.applyOptions({ watermark: { text: d.symbol } });
    chart.timeScale().fitContent();
    drawPlan(); drawMarkers();
    renderHeader(d);
    if (!$("t-entry").value || $("t-entry").dataset.sym !== S.sym) { $("t-entry").value = d.price?.toFixed(autoDigits(d.price)) ?? ""; $("t-entry").dataset.sym = S.sym; }
  } catch (e) { toast(e.message, true); }
}
function renderHeader(d) {
  $("c-sym").textContent = d.symbol; $("c-name").textContent = d.name || "";
  $("c-price").textContent = fmtP(d.symbol, d.price);
  const ch = d.price - d.prev_close, cp = (ch / d.prev_close) * 100;
  $("c-chg").innerHTML = d.prev_close ? `<span class="chg ${cls(ch)}">${ch >= 0 ? "+" : ""}${fmt(ch, autoDigits(d.price))} · ${pct(cp)}</span>` : "";
  $("c-stats").innerHTML = [
    ["Haut", d.day_high], ["Bas", d.day_low], ["Clôture préc.", d.prev_close],
    ["Volume", d.volume ? d.volume.toLocaleString("fr-FR") : null], ["52 sem.", d.low52 != null ? `${fmt(d.low52)} – ${fmt(d.high52)}` : null],
  ].filter(([, v]) => v != null).map(([k, v]) => `<span>${k}<b>${typeof v === "number" ? fmt(v) : v}</b></span>`).join("") + `<span>${esc(d.exchange || "")}${d.currency ? " · " + esc(d.currency) : ""}</span>`;
  document.title = `${d.symbol} ${fmt(d.price)} · MINDLOCK`;
}

/* ------------------------------------------------------------ watchlist */
function spark(vals, up, w = 54, h = 22) {
  if (!vals || vals.length < 2) return "";
  const mn = Math.min(...vals), mx = Math.max(...vals);
  const pts = vals.map((v, i) => `${((i / (vals.length - 1)) * w).toFixed(1)},${(h - 2 - ((v - mn) / (mx - mn || 1)) * (h - 4)).toFixed(1)}`).join(" ");
  const c = up ? "#2fd8a1" : "#ff5c7c", id = "g" + Math.random().toString(36).slice(2, 8);
  return `<svg width="${w}" height="${h}" style="width:${w}px;height:${h}px"><defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${c}" stop-opacity=".25"/><stop offset="1" stop-color="${c}" stop-opacity="0"/></linearGradient></defs>
    <polygon fill="url(#${id})" points="0,${h} ${pts} ${w},${h}"/><polyline fill="none" stroke="${c}" stroke-width="1.4" stroke-linejoin="round" points="${pts}"/></svg>`;
}
async function loadWatch() {
  if (!S.watch.length) { $("watchlist").innerHTML = empty("Ajoute un actif pour commencer.", "plus"); return; }
  try {
    const { quotes } = await api(`/api/quotes?symbols=${S.watch.map(encodeURIComponent).join(",")}`);
    $("watchlist").innerHTML = quotes.map((q, i) => {
      const s = S.watch[i];
      const x = `<button class="x" data-x="${esc(s)}" title="Retirer">${icon("x")}</button>`;
      if (q.error) return `<div class="wl" data-s="${esc(s)}"><div><div class="s">${esc(s)}</div><div class="n err">indisponible</div></div><div></div><div></div>${x}</div>`;
      return `<div class="wl ${s === S.sym ? "sel" : ""}" data-s="${esc(s)}">
        <div style="min-width:0"><div class="s">${esc(s)}</div><div class="n">${esc(q.name)}</div></div>
        <div>${spark(q.spark, q.change >= 0)}</div>
        <div class="p"><div>${fmtP(s, q.price)}</div>${chg(q.change_pct)}</div>${x}</div>`;
    }).join("");
  } catch (e) { toast(e.message, true); }
}
$("watchlist").addEventListener("click", (ev) => {
  const x = ev.target.closest("[data-x]");
  if (x) { ev.stopPropagation(); S.watch = S.watch.filter((s) => s !== x.dataset.x); store.set("dt.watch", S.watch); loadWatch(); return; }
  const row = ev.target.closest(".wl"); if (row) select(row.dataset.s);
});
$("add-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const s = $("add-sym").value.trim().toUpperCase(); if (!s) return;
  if (!S.watch.includes(s)) { S.watch.push(s); store.set("dt.watch", S.watch); }
  $("add-sym").value = ""; loadWatch(); select(s);
});

/* ------------------------------------------------------------ selection, timeframes, search */
function select(sym) {
  S.sym = sym; store.set("dt.sym", sym);
  S.plan = S.order = S.risk = null; renderTicket();
  document.querySelectorAll(".wl").forEach((r) => r.classList.toggle("sel", r.dataset.s === sym));
  $("bt-target").textContent = `${sym} ${ML.tf}`;
  loadChart(); loadNews(); loadML();
  if (!$("sp-bias").classList.contains("hidden")) loadBias();
}
function setPeriod(p) {
  S.period = p;
  document.querySelectorAll("#periods button").forEach((x) => x.classList.toggle("on", x.dataset.p === p));
}
$("periods").addEventListener("click", (ev) => {
  const b = ev.target.closest("button"); if (!b) return;
  setPeriod(b.dataset.p); loadChart();
  if (TFS.includes(b.dataset.p) && b.dataset.p !== ML.tf) { setTf(b.dataset.p); loadML(); }
});
let searchTimer;
$("search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = $("search").value.trim();
  if (!q) { $("search-results").classList.add("hidden"); return; }
  searchTimer = setTimeout(async () => {
    try {
      const { results } = await api(`/api/search?q=${encodeURIComponent(q)}`);
      $("search-results").innerHTML = results.length ? results.map((r) => `<div data-s="${esc(r.symbol)}"><b>${esc(r.symbol)}</b><span class="muted">${esc(r.name || "")}</span><span class="faint sm" style="margin-left:auto">${esc(r.type || "")} · ${esc(r.exchange || "")}</span></div>`).join("") : `<div class="muted">Aucun résultat</div>`;
      $("search-results").classList.remove("hidden");
    } catch {}
  }, 220);
});
$("search").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") { const first = $("search-results").querySelector("[data-s]"); const s = first ? first.dataset.s : $("search").value.trim().toUpperCase(); if (s) { $("search-results").classList.add("hidden"); $("search").value = ""; $("search").blur(); select(s); } }
  if (ev.key === "Escape") { $("search").blur(); $("search-results").classList.add("hidden"); }
});
$("search-results").addEventListener("click", (ev) => { const d = ev.target.closest("[data-s]"); if (!d) return; $("search-results").classList.add("hidden"); $("search").value = ""; select(d.dataset.s); });
document.addEventListener("click", (ev) => { if (!ev.target.closest(".search")) $("search-results").classList.add("hidden"); });
document.addEventListener("keydown", (ev) => { if (ev.key === "/" && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) { ev.preventDefault(); $("search").focus(); } });

/* ------------------------------------------------------------ tabs */
function openTab(t) {
  document.querySelectorAll("#tabbar button").forEach((x) => x.classList.toggle("on", x.dataset.t === t));
  document.querySelectorAll(".pane").forEach((x) => x.classList.toggle("hidden", x.id !== `tab-${t}-pane`));
  if (t === "tools" && !$("sp-macro").classList.contains("hidden")) loadMacro();
}
$("tabbar").addEventListener("click", (ev) => { const b = ev.target.closest("button"); if (b) openTab(b.dataset.t); });
$("subtabs").addEventListener("click", (ev) => {
  const b = ev.target.closest("button"); if (!b) return;
  document.querySelectorAll("#subtabs button").forEach((x) => x.classList.toggle("on", x === b));
  document.querySelectorAll(".subpane").forEach((x) => x.classList.toggle("hidden", x.id !== "sp-" + b.dataset.s));
  if (b.dataset.s === "macro") loadMacro();
  if (b.dataset.s === "bias") loadBias();
});
$("bottomnav").addEventListener("click", (ev) => { const a = ev.target.closest("a[data-tab]"); if (a) openTab(a.dataset.tab); });

/* ------------------------------------------------------------ bias */
async function loadBias() {
  $("bias").innerHTML = `<div class="loading">Analyse de ${esc(S.sym)}…</div>`;
  try {
    const b = await api(`/api/bias?symbol=${encodeURIComponent(S.sym)}`); S.bias = b;
    const col = b.label === "HAUSSIER" ? "up" : b.label === "BAISSIER" ? "down" : "muted";
    $("bias").innerHTML = `
      <div class="gauge"><div class="big ${col}">${b.label}</div><div class="muted">score <b class="mono ${cls(b.score)}">${b.score > 0 ? "+" : ""}${b.score}</b> / 100 · confiance ${b.confidence} %</div></div>
      <div class="meter"><i style="left:${(b.score + 100) / 2}%"></i></div>
      <ul class="reasons">${b.reasons.map((r) => `<li><span>${esc(r.text)}</span><span class="mono ${cls(r.points)}">${r.points > 0 ? "+" : ""}${r.points}</span></li>`).join("")}</ul>
      ${b.macro_tags.length ? `<div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">${b.macro_tags.map((t) => `<span class="tag y">${esc(t)}</span>`).join("")}</div>` : ""}
      <div class="note">${esc(b.note)}</div>`;
  } catch (e) { $("bias").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}
$("bias-refresh").onclick = loadBias;

/* ------------------------------------------------------------ news */
function ago(t) {
  const d = new Date(String(t).replace(" ", "T")); if (isNaN(d)) return "";
  const m = Math.round((Date.now() - d) / 60000);
  return m < 60 ? `il y a ${Math.max(m, 1)} min` : m < 1440 ? `il y a ${Math.round(m / 60)} h` : d.toLocaleDateString("fr-FR");
}
async function loadNews() {
  $("news-sym").textContent = S.sym; $("news").innerHTML = `<div class="skel" style="height:120px"></div>`;
  try {
    const { items } = await api(`/api/news?symbol=${encodeURIComponent(S.sym)}`);
    $("news").innerHTML = items.length ? items.slice(0, 14).map((n) => `<a href="${esc(n.link)}" target="_blank" rel="noopener noreferrer">
      <div class="t">${esc(n.title)}</div>
      <div class="m">${ago(n.time)}${n.sentiment > 0 ? '<span class="tag g">positif</span>' : n.sentiment < 0 ? '<span class="tag r">négatif</span>' : ""}${n.tags.map((t) => `<span class="tag y">${esc(t)}</span>`).join("")}</div></a>`).join("") : empty("Aucune news pour cet actif.", "list");
  } catch (e) { $("news").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}

/* ------------------------------------------------------------ stock ticket: plan -> size -> risk -> send */
function steps(n, bad) {
  for (let i = 1; i <= 4; i++) $("st" + i).className = "step" + (i < n || n === 5 ? " done" : i === n && bad ? " bad" : "");
}
$("t-rule").addEventListener("change", () => {
  const r = $("t-rule").value;
  $("t-param").value = r === "pct" ? 1 : r === "atr" ? 0.5 : (Number($("t-entry").value || 0) * 0.99).toFixed(2);
});
$("t-plan").onclick = async () => {
  S.plan = S.order = S.risk = null;
  const rule = $("t-rule").value;
  const body = { entry: $("t-entry").value, stop_rule: rule, stop_param: $("t-param").value, target_r: $("t-r").value };
  if (rule === "atr") { if (!S.bias?.atr_daily) await loadBias(); if (!S.bias?.atr_daily) { toast("ATR indisponible", true); return; } body.atr = S.bias.atr_daily; }
  try {
    const p = await api("/api/plan", body);
    S.plan = { ...p, symbol: S.sym }; drawPlan();
    const s = await api("/api/size", { symbol: S.sym, entry: p.entry, stop: p.stop, target: p.target });
    S.sizing = s.sizing; S.order = s.order;
    if (S.order) S.risk = (await api("/api/risk", { order: S.order })).result.results[0];
  } catch (e) { $("t-out").innerHTML = `<div class="box block err">${esc(e.message)}</div>`; steps(1, true); return; }
  renderTicket();
};
function renderTicket() {
  if (!S.plan) { $("t-out").innerHTML = ""; steps(1); drawPlan(); return; }
  const p = S.plan, s = S.sizing;
  let h = `<div class="box"><div class="kv"><span>Entrée</span><b class="mono">${fmt(p.entry)}</b></div>
    <div class="kv"><span>SL <span class="faint">· ${esc(p.stop_label)}</span></span><b class="mono down">${fmt(p.stop)}</b></div>
    <div class="kv"><span>TP <span class="faint">· ${esc(p.target_label)}</span></span><b class="mono up">${fmt(p.target)}</b></div>
    <div class="kv"><span>Risque par action</span><b class="mono">${usd(p.risk)} · R:R ${fmt(p.reward_risk, 2)}</b></div></div>`;
  if (s && s.refusal) { $("t-out").innerHTML = h + `<div class="box block err">${esc(s.refusal)}</div>`; steps(2, true); return; }
  if (s) h += `<div class="box"><div class="kv"><span>Quantité</span><b class="mono">${s.qty} actions</b></div>
    <div class="kv"><span>Montant</span><b class="mono">${usd(s.cost)}</b></div>
    <div class="kv"><span>Perte max au SL</span><b class="mono down">${usd(s.max_loss)}</b></div>
    <div class="kv"><span>Limite décisive</span><b>${esc(s.bound_by)}</b></div></div>`;
  const r = S.risk;
  if (r) {
    const blockers = r.reasons.filter((x) => !(S.anytime && x.startsWith("window:")));
    const ok = r.verdict === "PASS", canSend = blockers.length === 0;
    h += `<div class="box ${ok ? "pass" : "block"}"><b class="${ok ? "up" : "down"}">${ok ? "PASS" : "BLOCK"}</b> <span class="muted">· Risk Manager</span>
      ${r.reasons.map((x) => `<div class="err sm">• ${esc(x)}</div>`).join("")}${r.warnings.map((x) => `<div class="muted sm">~ ${esc(x)}</div>`).join("")}</div>`;
    if (!ok && canSend) h += `<div class="hint" style="margin-top:8px">Mode --anytime : seule la fenêtre horaire bloque, l'entraînement paper est autorisé.</div>`;
    h += `<button id="t-send" class="btn buy wide" ${canSend ? "" : "disabled"}>BUY ${s.qty} ${esc(p.symbol)} · paper, bracket</button>`;
    steps(ok ? 4 : 3, !ok);
  }
  $("t-out").innerHTML = h;
  if ($("t-send")) $("t-send").onclick = sendOrder;
}
async function sendOrder() {
  const p = S.plan, s = S.sizing;
  if (!(await confirmBox(`<div class="kicker">Ordre paper</div><h3>BUY ${s.qty} ${esc(p.symbol)}</h3>
    <p>Au marché (~${fmt(p.entry)}), SL <b class="down">${fmt(p.stop)}</b>, TP <b class="up">${fmt(p.target)}</b>.</p>
    <p>Perte max au SL ≈ <b>${usd(s.max_loss)}</b>. Compte simulé : aucun ordre réel n'est envoyé.</p>`, "Envoyer l'ordre", "buy"))) return;
  try {
    const acct = await api("/api/paper/order", { symbol: p.symbol, side: "buy", qty: s.qty, stop: p.stop, target: p.target, confirm: true, why: "ticket" });
    renderAccount(acct); toast(`BUY ${s.qty} ${p.symbol} exécuté (paper)`); steps(5);
  } catch (e) { toast(e.message, true); }
}

/* ------------------------------------------------------------ account */
function renderAccount(a) {
  const d = a.day;
  let h = `<div class="equity">${usd(a.equity)}</div>
    <div class="equity-sub"><span class="chg ${cls(a.total_pnl)}">${sgnUsd(a.total_pnl)}</span><span class="hint">depuis le départ</span></div>
    <div class="kv"><span>Cash</span><b class="mono">${usd(a.cash)}</b></div>
    <div class="kv"><span>Réalisé aujourd'hui</span><b class="mono ${cls(a.realized_today)}">${sgnUsd(a.realized_today)}</b></div>`;
  if (d) {
    const lo = -d.loss_limit, hi = d.win_limit, span = hi - lo || 1;
    const posi = Math.max(0, Math.min(100, ((d.pnl - lo) / span) * 100));
    const st = d.lock ? `<span class="tag ${d.lock.kind === "win" ? "g" : "r"}">${icon("lock")}${d.lock.kind === "win" ? "Gain verrouillé" : "Perte verrouillée"}</span>` : (d.loss_limit || d.win_limit) ? `<span class="tag">Ouvert</span>` : `<span class="tag">Désactivé</span>`;
    h += `<div class="lockbox"><div class="row"><span class="muted">Daily lock · P&L réalisé</span>${st}</div>
      <div class="row" style="margin-top:6px"><b class="mono ${cls(d.pnl)}" style="font-size:15px">${sgnUsd(d.pnl)}</b><span class="hint">risque / trade ${usd(d.risk_per_trade)}</span></div>
      ${d.loss_limit || d.win_limit ? `<div class="meter"><i style="left:${posi}%"></i></div><div class="row"><span class="down mono">-${usd(d.loss_limit)}</span><span class="up mono">+${usd(d.win_limit)}</span></div>` : ""}</div>`;
    $("daypill").textContent = `Jour ${sgnUsd(d.pnl)}`;
    $("daypill").className = "pill mono " + (d.lock ? "bad" : d.pnl > 0 ? "good" : d.pnl < 0 ? "bad" : "");
  }
  $("account").innerHTML = h;
  S.lock = d && d.lock ? d.message : null;
  $("t-plan").disabled = !!S.lock;
  $("t-plan").textContent = S.lock ? "Daily lock actif" : "Calculer le plan";
  $("lockbar").className = "lockbar" + (S.lock ? (d.lock.kind === "win" ? " win" : " loss") : " hidden");
  $("lockbar").textContent = S.lock || "";
  $("tab-positions").innerHTML = a.positions.length ? table(["Actif", "Qté", "PRU", "Prix", "Valeur", "P&L", ""],
    a.positions.map((p) => `<tr><td class="l"><a href="#" data-s="${esc(p.symbol)}">${esc(p.symbol)}</a></td><td>${p.qty}</td><td>${fmt(p.avg)}</td><td>${fmt(p.price)}</td><td>${usd(p.value)}</td>
      <td class="${cls(p.pnl)}">${sgnUsd(p.pnl)} · ${pct(p.pnl_pct)}</td><td><button class="btn ghost sm" data-close="${esc(p.symbol)}" data-q="${p.qty}">Clôturer</button></td></tr>`)) : empty("Aucune position actions ouverte.");
  $("tab-orders").innerHTML = a.orders.length ? `<h3 class="sub">Ordres bracket actions</h3>` + table(["Actif", "Qté", "SL", "TP", "Ouvert (UTC)"],
    a.orders.map((o) => `<tr><td class="l">${esc(o.symbol)}</td><td>${o.qty}</td><td class="down">${fmt(o.stop)}</td><td class="up">${fmt(o.target)}</td><td>${esc(o.at.slice(0, 16).replace("T", " "))}</td></tr>`)) : "";
  $("tab-fills").innerHTML = a.fills.length ? table(["Heure (UTC)", "Actif", "Sens", "Qté", "Prix", "P&L", "Origine"],
    a.fills.map((f) => `<tr><td>${esc(f.at.slice(5, 16).replace("T", " "))}</td><td class="l">${esc(f.symbol)}</td><td>${side(f.side)}</td><td>${f.qty}</td><td>${fmt(f.price)}</td><td class="${cls(f.pnl)}">${f.pnl ? sgnUsd(f.pnl) : ""}</td><td class="l muted">${esc(f.why)}</td></tr>`)) : empty("Aucune exécution pour l'instant.", "list");
}
$("tab-positions").addEventListener("click", async (ev) => {
  const a = ev.target.closest("[data-s]"); if (a) { ev.preventDefault(); select(a.dataset.s); return; }
  const b = ev.target.closest("[data-close]"); if (!b) return;
  if (!(await confirmBox(`<div class="kicker">Ordre paper</div><h3>Clôturer ${esc(b.dataset.close)}</h3><p>Vendre ${b.dataset.q} actions au marché.</p>`, "Clôturer", "sell"))) return;
  try { renderAccount(await api("/api/paper/order", { symbol: b.dataset.close, side: "sell", qty: Number(b.dataset.q), confirm: true, why: "clôture manuelle" })); toast("Position clôturée"); }
  catch (e) { toast(e.message, true); }
});
async function loadAccount() { try { renderAccount(await api("/api/paper")); } catch (e) { toast(e.message, true); } }
$("reset").onclick = async () => {
  if (!(await confirmBox(`<div class="kicker">Compte paper</div><h3>Réinitialiser le compte ?</h3><p>Positions, ordres et historique simulés seront effacés.</p>`, "Réinitialiser", "sell"))) return;
  renderAccount(await api("/api/paper/reset", {})); toast("Compte paper réinitialisé"); loadML();
};

/* ------------------------------------------------------------ daily setups (stocks) */
const SETUP_FR = { breakout_20d: ["Cassure 20 j", "g"], bull_flag: ["Bull flag", "g"], reclaim_ma20: ["Reprise MM20", "g"], oversold: ["Survendu", "r"], oversold_bounce: ["Rebond survente", "y"] };
$("scan-run").onclick = async () => {
  $("setups-out").innerHTML = `<div class="loading">Scan de ${S.watch.length} graphiques…</div>`;
  try {
    const { rows } = await api(`/api/scan?symbols=${S.watch.map(encodeURIComponent).join(",")}`);
    $("setups-out").innerHTML = table(["Actif", "Prix", "Jour", "6 mois", "RSI 14", "Setups"], rows.map((r) => r.error ? `<tr><td class="l">${esc(r.symbol)}</td><td colspan="5" class="l err">${esc(r.error)}</td></tr>` :
      `<tr><td class="l"><a href="#" data-s="${esc(r.symbol)}">${esc(r.symbol)}</a></td><td>${fmt(r.price)}</td><td class="${cls(r.change_pct)}">${pct(r.change_pct)}</td><td class="${cls(r.change_6m)}">${pct(r.change_6m)}</td><td>${fmt(r.rsi, 0)}</td>
      <td class="l">${r.setups.length ? r.setups.map((k) => `<span class="tag ${(SETUP_FR[k] || [k, ""])[1]}">${(SETUP_FR[k] || [k])[0]}</span>`).join(" ") : '<span class="faint">—</span>'}</td></tr>`));
  } catch (e) { $("setups-out").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
};
$("setups-out").addEventListener("click", (ev) => { const a = ev.target.closest("[data-s]"); if (a) { ev.preventDefault(); select(a.dataset.s); } });

/* ------------------------------------------------------------ time machine */
$("tm-run").onclick = async () => {
  if (!S.plan || S.plan.symbol !== S.sym) { toast("Calcule d'abord un plan dans le ticket actions", true); return; }
  $("tm-out").innerHTML = `<div class="loading">10 000 séances simulées pour ${esc(S.sym)}…</div>`;
  try {
    const d = await api("/api/timemachine", { symbol: S.sym, entry: S.plan.entry, stop: S.plan.stop, target: S.plan.target, entry_time: $("tm-time").value });
    const m = d.mc, h = d.history || {};
    const w = (x) => Math.max(0, x * 100).toFixed(1);
    $("tm-out").innerHTML = `
      <div class="bar3"><div style="width:${w(m.p_target)}%;background:var(--buy)">${w(m.p_target)}%</div><div style="width:${w(m.p_neither)}%;background:#4a5060;color:#dfe3ea">${w(m.p_neither)}%</div><div style="width:${w(m.p_stop)}%;background:var(--sell)">${w(m.p_stop)}%</div></div>
      <div class="hint">vert : TP d'abord · gris : ni l'un ni l'autre à ${esc(m.until)} · rouge : SL d'abord</div>
      <div class="box"><div class="kv"><span>R moyen</span><b class="mono ${cls(m.avg_R)}">${fmt(m.avg_R, 2)}</b></div>
        <div class="kv"><span>Clôture médiane (5 %–95 %)</span><b class="mono">${fmt(m.median_close)} (${fmt(m.p5_close)} – ${fmt(m.p95_close)})</b></div>
        <div class="kv"><span>Séances utilisées</span><b class="mono">${m.pool_sessions} (${esc(m.pool_from)} → ${esc(m.pool_to)})</b></div>
        ${h.rule ? `<div class="kv"><span>Historique, même règle</span><b class="mono">${h.triggered} fois · ${h.target ?? "?"} TP · ${h.stop ?? "?"} SL</b></div>` : ""}</div>
      <div class="box"><b>Verdict :</b> ${esc(d.verdict)}${(d.verdict_notes || []).map((n) => `<div class="muted sm">${esc(n)}</div>`).join("")}</div>
      <div class="note">${esc(d.note)}</div>`;
  } catch (e) { $("tm-out").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
};

/* ------------------------------------------------------------ macro + market */
async function loadMacro() {
  try {
    const m = await api("/api/macro");
    $("regime").textContent = "Macro " + m.regime.toLowerCase();
    $("regime").className = "pill " + (m.regime === "RISK-ON" ? "good" : m.regime === "RISK-OFF" ? "bad" : "");
    $("regime").title = m.notes.join(" · ");
    $("tape").innerHTML = m.quotes.filter((q) => q.price != null).map((q) => `<span class="it" data-s="${esc(q.symbol)}"><b>${esc(q.label)}</b><span class="mono">${fmtP(q.symbol, q.price)}</span><span class="mono ${cls(q.change_pct)}">${pct(q.change_pct)}</span></span>`).join("");
    $("macro-out").innerHTML = `<div class="bt-meta">Régime <span class="pill ${m.regime === "RISK-ON" ? "good" : m.regime === "RISK-OFF" ? "bad" : ""}">${m.regime}</span>${esc(m.notes.join(" · "))}</div>
      <div class="macro-grid">${m.quotes.map((q) => `<div class="mcard" data-s="${esc(q.symbol)}"><div class="n">${esc(q.label)}</div><div class="v">${fmt(q.price)}</div>${chg(q.change_pct)}<div style="margin-top:8px">${spark(q.spark, q.change >= 0, 140, 34)}</div></div>`).join("")}</div>`;
  } catch (e) { $("tape").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
}
$("macro-out").addEventListener("click", (ev) => { const c = ev.target.closest("[data-s]"); if (c) select(c.dataset.s); });
$("tape").addEventListener("click", (ev) => { const c = ev.target.closest("[data-s]"); if (c) select(c.dataset.s); });
async function loadMarket() {
  try {
    const m = await api("/api/market");
    $("market").className = "pill" + (m.open ? " on" : "");
    $("market").querySelector("span").textContent = m.open ? "NYSE ouvert" : "NYSE fermé";
    $("market").title = `${m.reason}${m.next_open ? " · prochaine ouverture " + m.next_open + " NY" : ""}`;
  } catch {}
}
function tick() {
  $("clock").textContent = "NY " + new Date().toLocaleTimeString("fr-FR", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/* ------------------------------------------------------------ MINDLOCK EMA CORE TREND V.1 */
const ML = { data: null, tf: store.get("dt.mltf", "M30"), lines: [] };
const px = (x, d) => (x == null ? "—" : Number(x).toLocaleString("fr-FR", { minimumFractionDigits: d ?? 2, maximumFractionDigits: d ?? 2 }));
const digitsOf = (p) => { const t = (p && p.MinTick) || 0.01; return t < 1 ? Math.max(2, Math.round(-Math.log10(t))) : 2; };
function setTf(tf) {
  ML.tf = tf; store.set("dt.mltf", tf);
  document.querySelectorAll("#ml-tf button").forEach((x) => x.classList.toggle("on", x.dataset.tf === tf));
  $("ml-title").textContent = `${S.sym} ${tf}`; $("bt-target").textContent = `${S.sym} ${tf}`;
}
$("ml-tf").addEventListener("click", (ev) => {
  const b = ev.target.closest("button"); if (!b) return;
  setTf(b.dataset.tf); loadML();
  if (S.period !== b.dataset.tf) { setPeriod(b.dataset.tf); loadChart(); }
});
function drawMarkers() {
  const d = ML.data, cd = S.chartData;
  ML.lines.forEach((l) => candles.removePriceLine(l)); ML.lines = [];
  if (!d || !cd || S.sym !== d.symbol || S.period !== d.tf) { candles.setMarkers([]); return; }
  const have = new Set(cd.bars.map((b) => b.t)), off = cd.gmtoffset || 0;
  candles.setMarkers(d.markers.filter((m) => have.has(m.t)).map((m) => ({
    time: m.t + off, position: m.side === "buy" ? "belowBar" : "aboveBar", shape: m.side === "buy" ? "arrowUp" : "arrowDown",
    color: m.side === "buy" ? "#7CFC9A" : "#ff5c7c", text: SIDE[m.side], size: 1.2 })));
  const line = (price, color, title, style = 2) => price != null && ML.lines.push(candles.createPriceLine({ price, color, lineWidth: 2, lineStyle: style, axisLabelVisible: true, title }));
  if (d.position) { line(d.position.entry, "#eceef3", "Entrée", 0); line(d.position.sl, "#ff5c7c", d.position.be ? "BE" : "SL"); line(d.position.tp, "#2fd8a1", "TP"); }
  else if (d.pending) { line(d.pending.price, "#7dd3fc", "Limit " + SIDE[d.pending.side]); line(d.pending.sl, "rgba(255,92,124,.65)", "SL"); line(d.pending.tp, "rgba(47,216,161,.65)", "TP"); }
}
function ladder(entry, sl, tp, nd, label) {
  const up = tp > entry;
  const risk = Math.abs(entry - sl), rew = Math.abs(tp - entry), r = risk ? rew / risk : 0;
  const pr = risk + rew ? (rew / (risk + rew)) * 100 : 50;
  const bar = up ? `linear-gradient(180deg, var(--buy) 0 ${pr}%, var(--sell) ${pr}% 100%)` : `linear-gradient(180deg, var(--sell) 0 ${100 - pr}%, var(--buy) ${100 - pr}% 100%)`;
  const rows = up ? [["TP", tp, "up"], [label, entry, ""], ["SL", sl, "down"]] : [["SL", sl, "down"], [label, entry, ""], ["TP", tp, "up"]];
  return `<div class="ladder"><div class="bar" style="background:${bar}"></div>${rows.map(([k, v, c], i) => `${i ? "" : ""}<span class="k">${k}</span><span class="v ${c}">${px(v, nd)}</span>`).join("")}</div>
    <div class="hint" style="margin-top:6px">Risque ${px(risk, nd)} · gain ${px(rew, nd)} · <b class="mono" style="color:var(--gold-2)">${fmt(r, 1)}R</b></div>`;
}
let mlReq = 0;
async function loadML() {
  setTf(ML.tf);
  const req = ++mlReq;
  try {
    const d = await api(`/api/mindlock/signal?symbol=${encodeURIComponent(S.sym)}&tf=${ML.tf}`);
    if (req !== mlReq || d.symbol !== S.sym || d.tf !== ML.tf) return;
    ML.data = d;
    if (!ML.formShown) { renderParams(d.base_params); ML.formShown = true; }
    renderStrategy(d); renderMLPositions(d); renderMLJournal(d); drawMarkers();
  } catch (e) { if (req === mlReq) $("ml").innerHTML = `<div class="box block err">${esc(e.message)}</div>`; }
}
function renderStrategy(d) {
  const nd = digitsOf(d.params), P = d.params, unit = P.Unit, E = d.engine;
  const cond = (c) => `<div class="cond ${c.ok ? "ok" : "no"}" title="${esc(c.detail)}"><span class="b">${icon(c.ok ? "check" : "x")}</span><span>${esc(c.name)} <span class="faint">· ${esc(c.detail)}</span></span></div>`;
  const pos = d.position, pend = d.pending;
  let h = "";
  if (E) {
    const nxt = E.next_side, fOk = E.filters.filter((f) => f.ok).length, fN = E.filters.length;
    const blocked = E.crossed_now && !d.signal;
    const color = d.signal === "buy" ? "#2fd8a1" : d.signal === "sell" ? "#ff5c7c" : blocked ? "#5c6272" : fOk === fN ? "#d9b56a" : "#5c6272";
    const state = d.signal ? `SIGNAL ${SIDE[d.signal]}` : blocked ? `${SIDE[E.last_cross.side]} BLOQUÉ` : "EN ATTENTE";
    const sub = d.signal ? "Croisement EMA 20/50 à la clôture, filtres validés"
      : blocked ? "Croisement à la clôture, mais les filtres ne valident pas"
      : `du croisement EMA 20/50 · prochain : <b class="${nxt === "buy" ? "up" : "down"}">${SIDE[nxt]}</b>`;
    const lc = E.last_cross;
    const r = 31, c = 2 * Math.PI * r, frac = fN ? fOk / fN : 0;
    h += `<div class="hero"><div class="ring"><svg viewBox="0 0 74 74"><circle cx="37" cy="37" r="${r}" fill="none" stroke="rgba(255,255,255,.07)" stroke-width="6"/>
        <circle cx="37" cy="37" r="${r}" fill="none" stroke="${color}" stroke-width="6" stroke-linecap="round" stroke-dasharray="${(c * frac).toFixed(1)} ${c.toFixed(1)}"/></svg>
        <div class="lbl"><div>${fOk}/${fN}<small>FILTRES</small></div></div></div>
      <div class="hero-t"><div class="state ${d.signal || "idle"}">${state}</div>
        <div class="meta"><span>${sub}</span></div>
        <div class="meta"><span>Bougie ${d.tf} close à ${nyTime(d.bar.t + d.bar_seconds, false)} NY</span>${d.in_session ? "" : '<span class="tag">Hors session</span>'}${d.day.lock ? `<span class="tag r">${icon("lock")}Daily lock</span>` : ""}</div></div></div>
      <div class="engine">
        <div class="eh"><span class="kicker">Moteur · croisement EMA 20 / 50</span></div>
        <div class="emas"><div><i style="background:#ffd23f"></i>EMA 20<b class="mono">${px(E.e20, nd)}</b></div><div><i style="background:#2ecc71"></i>EMA 50<b class="mono">${px(E.e50, nd)}</b></div></div>
        <div class="kv"><span>Position</span><b class="${E.above ? "up" : "down"}">EMA 20 ${E.above ? "au-dessus" : "en dessous"} ${E.above ? "▲" : "▼"}</b></div>
        <div class="kv"><span>Écart 20 / 50</span><b class="mono">${fmt(Math.abs(E.gap_units), 1)} u <span class="tag ${E.converging ? "y" : ""}">${E.converging ? "se resserre" : "s'écarte"}</span></b></div>
        <div class="kv"><span>Dernier croisement</span><b>${lc ? `${side(lc.side)} il y a ${lc.bars_ago} bougie${lc.bars_ago > 1 ? "s" : ""} <span class="tag ${lc.validated ? "g" : ""}">${lc.validated ? "signal validé" : "filtré"}</span>` : "—"}</b></div>
      </div>
      <div class="filters"><div class="kicker" style="color:var(--muted)">Filtres du prochain croisement <span class="${nxt === "buy" ? "up" : "down"}">${SIDE[nxt]}</span></div>${E.filters.map(cond).join("")}</div>`;
  } else {
    h += `<div class="hint" style="margin:14px 0">Pas encore assez de bougies pour les EMA 20 / 50 / 100.</div>`;
  }
  if (pos) {
    h += `<div class="order"><div class="oh"><span>${side(pos.side)} <b>${fmt(pos.lots, 2)} lot</b> ${pos.be ? '<span class="tag y">BE actif</span>' : ""}</span><b class="mono ${cls(pos.pnl)}" style="font-size:16px">${sgnUsd(pos.pnl)}</b></div>
      ${ladder(pos.entry, pos.sl0 ?? pos.sl, pos.tp, nd, "Entrée")}
      <div class="foot2"><span class="muted">SL actuel ${px(pos.sl, nd)}</span><button class="btn ghost sm" data-mlclose="${esc(d.key)}">Clôturer</button></div></div>`;
  } else if (pend) {
    h += `<div class="order"><div class="oh"><span>${side(pend.side)} <b>Ordre limit</b></span><span class="hint">${pend.age}/${P.PendingExpiryBars} bougies</span></div>
      ${ladder(pend.price, pend.sl, pend.tp, nd, "Limit")}
      <div class="foot2"><span class="muted">Le SL est recalculé au remplissage</span><button class="btn ghost sm" data-mlcancel="${esc(d.key)}">Annuler</button></div></div>`;
  } else if (d.signal) {
    const sd = d.sides[d.signal];
    h += `<div class="order"><div class="oh"><span>${side(d.signal)} <b>${P.EntryMethod === "MARKET" ? "Au marché" : "Ordre limit"}</b></span><span class="hint">${esc(d.entry_labels[P.EntryMethod].split(" - ")[0])}</span></div>
      ${ladder(sd.entry, sd.sl, sd.tp, nd, P.EntryMethod === "MARKET" ? "Entrée" : "Limit")}
      <div class="foot2"><span class="muted">Taille</span><b class="mono">${fmt(sd.lots, 2)} lot · ${usd(sd.risk_usd)} au SL</b></div></div>
      <button id="ml-exec" class="btn ${d.signal} wide">${P.EntryMethod === "MARKET" ? `${SIDE[d.signal]} au marché` : `Placer le limit ${SIDE[d.signal]} · ${px(sd.entry, nd)}`}</button>`;
  } else {
    h += `<div class="order"><div class="oh"><b>Aucun ordre</b><span class="tag">en attente</span></div>
      <div class="hint">Le signal naît du croisement EMA 20 / 50 à la clôture d'une bougie ${d.tf}, s'il est validé par l'EMA 100 et le RSI. L'ordre proposé apparaîtra alors ici${d.auto ? ", et le mode auto le placera." : "."}</div></div>`;
  }
  h += `<label class="switch"><span class="t"><b>Mode auto · paper</b><span>Prend chaque signal ${esc(d.symbol)} ${d.tf} tout seul</span></span><input type="checkbox" id="ml-auto" ${d.auto ? "checked" : ""}><span class="knob"></span></label>
    <details class="more"><summary>Réglages actifs ${icon("chev")}</summary><div class="in">
      <div class="kv"><span>Entrée</span><b>${esc(d.entry_labels[P.EntryMethod])}</b></div>
      <div class="kv"><span>SL structurel</span><b class="mono">${P.StructLookback} bougies ± ${P.StructBuffer} u · max ${P.StopLossMax} u (${px(P.StopLossMax * unit, nd)})</b></div>
      <div class="kv"><span>TP · BE</span><b class="mono">${P.TakeProfit} u (${px(P.TakeProfit * unit, nd)}) · ${P.UseBreakEven ? `BE à ${P.BreakEvenTrigger} u` : "BE off"}</b></div>
      <div class="kv"><span>Unité · contrat</span><b class="mono">${unit} · ${P.ContractSize}${P.Currency && P.Currency !== "USD" ? " · " + esc(P.Currency) : ""}</b></div>
      <div class="kv"><span>Risque · daily lock</span><b class="mono">${fmt(P.RiskPercent, 1)} % · ${P.UseDailyLock ? `-${fmt(P.DailyLossPercent, 1)} / +${fmt(P.DailyProfitPercent, 1)} %` : "off"}</b></div>
      <div class="kv"><span>Session (NY)</span><b class="mono">${P.UseSession ? esc(P.Session.slice(0, 2) + ":" + P.Session.slice(2, 4) + " – " + P.Session.slice(5, 7) + ":" + P.Session.slice(7)) : "24 h"}</b></div>
    </div></details>`;
  if (d.events.length) h += `<details class="more" open><summary>Journal ${d.tf} ${icon("chev")}</summary><div class="in timeline">${d.events.slice(0, 6).map((e) => `<div class="ev"><span class="w">${nyTime(e.t)}</span><span>${esc(e.text)}</span></div>`).join("")}</div></details>`;
  $("ml").innerHTML = h;
  if ($("ml-exec")) $("ml-exec").onclick = async () => {
    const sd = d.sides[d.signal];
    const what = P.EntryMethod === "MARKET" ? `au marché, ~${px(sd.entry, nd)}` : `ordre limit à ${px(sd.entry, nd)}, valable ${P.PendingExpiryBars} bougies`;
    if (!(await confirmBox(`<div class="kicker">MINDLOCK · paper</div><h3>${SIDE[d.signal]} ${esc(d.symbol)} ${d.tf}</h3>
      <p>${fmt(sd.lots, 2)} lot, ${what}.</p><p>SL structurel <b class="down">${px(sd.sl, nd)}</b> · TP <b class="up">${px(sd.tp, nd)}</b></p>
      <p>Perte max au SL ≈ <b>${usd(sd.risk_usd)}</b>. Le SL est recalculé au remplissage, comme dans ton script. Aucun ordre réel.</p>`, P.EntryMethod === "MARKET" ? `${SIDE[d.signal]} maintenant` : "Placer l'ordre", d.signal))) return;
    try { await api("/api/mindlock/execute", { symbol: d.symbol, tf: d.tf, side: d.signal, confirm: true }); toast(P.EntryMethod === "MARKET" ? "Position ouverte (paper)" : "Ordre limit placé (paper)"); loadML(); loadAccount(); } catch (e) { toast(e.message, true); }
  };
  $("ml-auto").onchange = async (ev) => {
    const on = ev.target.checked;
    if (on && !(await confirmBox(`<div class="kicker">MINDLOCK · paper</div><h3>Mode auto sur ${esc(d.symbol)} ${d.tf}</h3>
      <p>La stratégie prendra chaque signal à la clôture des bougies ${d.tf} : ordres limit, SL structurel, BE, TP, fermeture et inversion sur signal opposé, session et daily lock.</p>
      <p>Le serveur doit rester allumé. Aucun ordre réel.</p>`, "Activer"))) { ev.target.checked = false; return; }
    try { await api("/api/mindlock/auto", { symbol: d.symbol, tf: d.tf, on }); toast(on ? `Auto activé sur ${d.symbol} ${d.tf}` : "Mode auto désactivé"); loadML(); } catch (e) { toast(e.message, true); ev.target.checked = !on; }
  };
}
function renderMLPositions(d) {
  const rows = d.positions.map((p) => `<tr><td class="l"><a href="#" data-key="${esc(p.key)}">${esc(p.key.replace("|", " · "))}</a></td><td>${side(p.side)}</td><td>${fmt(p.lots, 2)}</td><td>${fmt(p.entry)}</td>
      <td class="down">${fmt(p.sl)}${p.be ? ' <span class="tag y">BE</span>' : ""}</td><td class="up">${fmt(p.tp)}</td><td class="${cls(p.pnl)}">${sgnUsd(p.pnl)}</td><td><button class="btn ghost sm" data-mlclose="${esc(p.key)}">Clôturer</button></td></tr>`)
    .concat(d.pendings.map((p) => `<tr><td class="l"><a href="#" data-key="${esc(p.key)}">${esc(p.key.replace("|", " · "))}</a></td><td>${side(p.side)}</td><td class="l muted">limit</td><td>${fmt(p.price)}</td>
      <td class="down">${fmt(p.sl)}</td><td class="up">${fmt(p.tp)}</td><td class="muted">${p.age} bougie${p.age > 1 ? "s" : ""}</td><td><button class="btn ghost sm" data-mlcancel="${esc(p.key)}">Annuler</button></td></tr>`));
  $("ml-positions").innerHTML = (rows.length ? table(["Marché", "Sens", "Lot", "Entrée", "SL", "TP", "P&L", ""], rows) : empty("Aucune position ni ordre MINDLOCK. Prends un signal depuis le panneau stratégie ou active le mode auto.", "bolt"))
    + (d.autos.length ? `<div class="bt-meta" style="margin-top:10px">Mode auto actif sur ${d.autos.map((k) => `<span class="tag y">${esc(k.replace("|", " · "))}</span>`).join(" ")}</div>` : "");
}
function renderMLJournal(d) {
  $("ml-journal").innerHTML = d.trades.length ? table(["Fermé", "Marché", "Sens", "Lot", "Entrée", "Sortie", "Raison", "P&L"],
    d.trades.map((t) => `<tr><td>${nyTime(t.exit_t)}</td><td class="l">${esc((t.key || "").replace("|", " · "))}</td><td>${side(t.side)}</td><td>${fmt(t.lots, 2)}</td><td>${fmt(t.entry)}</td><td>${fmt(t.exit)}</td><td class="l">${esc(reason(t.reason))}</td><td class="${cls(t.pnl)}">${sgnUsd(t.pnl)}</td></tr>`))
    : empty("Aucun trade MINDLOCK clôturé pour l'instant.", "list");
}
async function mlAction(ev) {
  const k = ev.target.closest("[data-key]");
  if (k) { ev.preventDefault(); const [sym, tf] = k.dataset.key.split("|"); setTf(tf); setPeriod(tf); select(sym); window.scrollTo({ top: 0, behavior: "smooth" }); return; }
  const c = ev.target.closest("[data-mlcancel]");
  if (c) { try { await api("/api/mindlock/close", { key: c.dataset.mlcancel, what: "pending" }); toast("Ordre annulé"); loadML(); } catch (e) { toast(e.message, true); } return; }
  const b = ev.target.closest("[data-mlclose]"); if (!b) return;
  if (!(await confirmBox(`<div class="kicker">MINDLOCK · paper</div><h3>Clôturer ${esc(b.dataset.mlclose.replace("|", " "))}</h3><p>Au dernier prix.</p>`, "Clôturer", "sell"))) return;
  try { await api("/api/mindlock/close", { key: b.dataset.mlclose }); toast("Position clôturée"); loadML(); loadAccount(); } catch (e) { toast(e.message, true); }
}
$("ml").addEventListener("click", mlAction);
$("ml-positions").addEventListener("click", mlAction);
$("ml-refresh").onclick = loadML;

/* ------------------------------------------------------------ MINDLOCK scanner */
$("mlscan-run").onclick = async () => {
  const syms = S.watch.slice(0, 25);
  $("scan-out").innerHTML = `<div class="loading">MINDLOCK sur ${syms.length} actifs × 5 timeframes…</div>`;
  try {
    const r = await api(`/api/mindlock/scan?symbols=${syms.map(encodeURIComponent).join(",")}`);
    const at = {}; r.cells.forEach((c) => { at[c.symbol + "|" + c.tf] = c; });
    const cell = (c) => {
      if (!c || c.error) return `<td title="${esc(c && c.error)}"><span class="faint">—</span></td>`;
      const tr = c.aligned === "up" ? '<span class="up">▲▲</span>' : c.aligned === "down" ? '<span class="down">▼▼</span>' : c.trend === "up" ? '<span class="up">▲</span>' : '<span class="down">▼</span>';
      const main = c.signal ? `<span class="t ${c.signal === "buy" ? "up" : "down"}">${SIDE[c.signal]}</span>` : `<span class="t">${tr}</span>`;
      const subl = c.signal ? "maintenant" : c.last_signal ? `${SIDE[c.last_signal]} il y a ${c.bars_ago}` : "aucun signal";
      return `<td data-s="${esc(c.symbol)}" data-tf="${c.tf}" title="RSI ${fmt(c.rsi, 0)}"><span class="cell ${c.signal ? "sig-" + c.signal : ""}">${main}<span class="s">${subl}</span></span></td>`;
    };
    $("scan-out").innerHTML = table(["Actif"].concat(r.tfs), r.symbols.map((s) => `<tr><td class="l"><b>${esc(s)}</b></td>${r.tfs.map((t) => cell(at[s + "|" + t])).join("")}</tr>`), 'class="mx"')
      + `<p class="note">BUY / SELL : l'EMA 20 croise l'EMA 50 sur la dernière bougie clôturée et l'EMA 100 et le RSI valident. ▲▲ / ▼▼ : EMA 20/50 et EMA 100 alignées ; ▲ / ▼ : EMA 20 vs EMA 50 seulement. Clique une case pour ouvrir l'actif sur ce timeframe.</p>`;
  } catch (e) { $("scan-out").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
};
$("scan-out").addEventListener("click", (ev) => {
  const c = ev.target.closest("[data-tf]"); if (!c) return;
  setTf(c.dataset.tf); setPeriod(c.dataset.tf); select(c.dataset.s); window.scrollTo({ top: 0, behavior: "smooth" });
});

/* ------------------------------------------------------------ MINDLOCK backtest + settings */
const ENTRY_OPTS = [["MARKET", "MARKET · clôture du signal"], ["BODY", "BODY LIMIT · corps + tolérance"], ["EMA20", "EMA 20 · limit"], ["EMA50", "EMA 50 · limit"], ["CORE", "EMA CORE 20/50 · milieu"], ["OTE", "OTE Fibonacci · limit"]];
const PARAM_GROUPS = [
  ["Moteur EMA", "20 · 50 · 100", [["EMA20Length", "Période EMA 20"], ["EMA50Length", "Période EMA 50"], ["EMA100Length", "Période EMA 100"]]],
  ["Filtre RSI", "RSI 14 vs 50", [["UseRSI", "Activer le filtre"], ["RSILength", "Période"], ["RSIMid", "Niveau de validation"]]],
  ["Méthode d'entrée", "limit, expiration, inversion", [["EntryMethod", "Mode d'entrée", ENTRY_OPTS], ["Tolerance", "Tolérance (unités)"], ["PendingExpiryBars", "Expiration (bougies)"],
    ["CancelOnOppositeSignal", "Remplacer sur signal opposé"], ["CloseOnOppositeSignal", "Fermer sur signal opposé"], ["EnterAfterClose", "Entrer dans le nouveau sens"],
    ["OTELevel", "OTE · niveau", [["0.618", "0.618"], ["0.705", "0.705"], ["0.789", "0.789"]]], ["OTELookback", "OTE · impulsion (bougies)"]]],
  ["Gestion du risque", "SL structurel, TP, BE", [["UnitMode", "Unité", [["Auto", "Auto"], ["Pips", "Pips"], ["Ticks", "Ticks"], ["Points", "Points"]]], ["UnitOverride", "Unité en prix (0 = auto)"],
    ["StopLossMax", "SL max (unités)"], ["TakeProfit", "TP (unités)"], ["StructLookback", "SL structurel · bougies"], ["StructBuffer", "SL structurel · marge"],
    ["CapStructSL", "Plafonner au SL max"], ["UseBreakEven", "Break-even"], ["BreakEvenTrigger", "Activation BE (unités)"]]],
  ["Daily lock & session", "réalisé, New York", [["UseDailyLock", "Daily lock"], ["DailyLossPercent", "Perte max jour %"], ["DailyProfitPercent", "Gain max jour %"],
    ["UseSession", "Filtre de session"], ["Session", "Session NY (HHMM-HHMM)"], ["CloseAtSessionEnd", "Fermer en fin de session"]]],
  ["Taille", "règle du terminal", [["UseRiskPercent", "Risque en % du compte"], ["RiskPercent", "Risque %"], ["FixedLot", "Lot fixe"], ["MaxLeverage", "Levier max"], ["ContractSize", "Contrat (0 = auto)"]]],
];
function renderParams(p) {
  $("ml-params").innerHTML = PARAM_GROUPS.map(([g, sub, items], gi) => `<details ${gi < 2 ? "" : ""}><summary><span>${g} <span class="s">· ${sub}</span></span>${icon("chev")}</summary><div class="pg">${items.map(([k, label, opts]) => {
    if (opts) return `<label class="field">${label}<select data-k="${k}">${opts.map(([v, t]) => `<option value="${v}" ${String(p[k]) === v ? "selected" : ""}>${t}</option>`).join("")}</select></label>`;
    if (typeof p[k] === "boolean") return `<label class="field">${label}<select data-k="${k}" data-b="1"><option value="true" ${p[k] ? "selected" : ""}>Oui</option><option value="false" ${p[k] ? "" : "selected"}>Non</option></select></label>`;
    if (typeof p[k] === "string") return `<label class="field">${label}<input data-k="${k}" value="${esc(p[k])}"></label>`;
    return `<label class="field">${label}<input data-k="${k}" type="number" step="any" value="${p[k]}"></label>`;
  }).join("")}</div></details>`).join("");
}
function readParams() {
  const o = {}; document.querySelectorAll("#ml-params [data-k]").forEach((el) => { o[el.dataset.k] = el.dataset.b ? el.value === "true" : el.value; }); return o;
}
$("bt-csv").addEventListener("change", () => { const f = $("bt-csv").files[0]; $("bt-csv-name").textContent = f ? f.name : "Importer un CSV"; });
let eqChart;
$("bt-run").onclick = async () => {
  $("bt-out").innerHTML = `<div class="loading">Backtest de ${esc(S.sym)} ${ML.tf}…</div>`;
  $("bt-run").disabled = true;
  try {
    const body = { params: readParams(), balance: $("bt-bal").value, symbol: S.sym, tf: ML.tf };
    const f = $("bt-csv").files[0]; if (f) { body.csv = await f.text(); body.csv_name = f.name; }
    const r = await api("/api/mindlock/backtest", body);
    const nd = digitsOf(r.params);
    const day = (t) => new Date(t * 1000).toLocaleDateString("fr-FR", { timeZone: "America/New_York", day: "2-digit", month: "short", year: "numeric" });
    const kpi = (n, v, hl) => `<div class="kpi ${hl ? "hl" : ""}"><div class="n">${n}</div><div class="v">${v}</div></div>`;
    $("bt-out").innerHTML = `<div class="bt-meta"><span class="tag y">${esc(r.source)}</span><span>${day(r.from)} → ${day(r.to)}</span><span>· unité ${r.params.Unit}</span><span>· ${r.signals} signaux</span></div>
      <div class="kpis">${[
        kpi("Résultat net", `<span class="${cls(r.net)}">${sgnUsd(r.net)}</span>`, true), kpi("Rendement", `<span class="${cls(r.net_pct)}">${pct(r.net_pct)}</span>`, true),
        kpi("Profit factor", r.profit_factor == null ? "—" : fmt(r.profit_factor, 2)), kpi("Trades", r.trades), kpi("Réussite", fmt(r.win_rate, 1) + " %"),
        kpi("Drawdown max", `${fmt(r.max_drawdown_pct, 1)} %`), kpi("Gain moyen", usd(r.avg_win)), kpi("Perte moyenne", usd(r.avg_loss)),
        kpi("En unités", `<span class="${cls(r.net_units)}">${fmt(r.net_units, 0)}</span>`), kpi("Jours verrouillés", r.locked_days)].join("")}</div>
      <div class="bt-meta">Sorties ${Object.entries(r.exits).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<span class="tag">${esc(reason(k))} · ${v}</span>`).join(" ") || "—"}</div>
      <div id="eq" class="eqchart" style="margin-top:12px"></div>
      ${r.trade_list.length ? table(["Entrée (NY)", "Sens", "Lot", "Entrée", "SL", "Sortie", "Raison", "Unités", "P&L"],
        r.trade_list.slice(0, 200).map((t, i) => `<tr><td>${nyTime(t.t)}</td><td>${side(t.side)}</td><td>${fmt(t.lots, 2)}</td><td>${px(t.entry, nd)}</td><td>${px(t.sl0, nd)}</td><td>${px(t.exit, nd)}</td><td class="l">${esc(reason(t.reason))}</td><td class="${cls(t.units)}">${fmt(t.units, 0)}</td><td class="${cls(t.pnl)}">${sgnUsd(t.pnl)}</td></tr>`.replace("<tr>", i >= 20 ? '<tr class="more-row hidden">' : "<tr>")), 'id="bt-trades"') : empty("Aucun trade sur cette période.", "flask")}
      ${r.trade_list.length > 20 ? `<div class="toolbar end" style="margin:10px 0 0"><button id="bt-more" class="btn ghost sm">Voir les ${Math.min(r.trade_list.length, 200)} trades</button></div>` : ""}
      <p class="note">Port de ton Pine Script bougie par bougie. Signal et ordres à la clôture, limit rempli au prix ou à l'ouverture en cas de gap. SL et TP actifs dès la bougie suivante ; si une bougie touche les deux, l'extrême le plus proche de l'ouverture passe en premier, comme le simulateur TradingView. Sans spread, commission ni slippage. Un résultat passé ne garantit rien.</p>`;
    if ($("bt-more")) $("bt-more").onclick = (ev) => { document.querySelectorAll("#bt-trades .more-row").forEach((x) => x.classList.remove("hidden")); ev.target.remove(); };
    eqChart = LW.createChart($("eq"), { autoSize: true, layout: { background: { color: "transparent" }, textColor: "#8b92a1", fontFamily: "Inter" },
      grid: { vertLines: { visible: false }, horzLines: { color: "rgba(255,255,255,.035)" } }, rightPriceScale: { borderVisible: false }, timeScale: { borderVisible: false, timeVisible: true } });
    eqChart.addAreaSeries({ lineColor: "#d9b56a", topColor: "rgba(217,181,106,.28)", bottomColor: "rgba(217,181,106,0)", lineWidth: 2 }).setData(r.curve.map((c) => ({ time: c.t, value: c.equity })));
    eqChart.timeScale().fitContent();
  } catch (e) { $("bt-out").innerHTML = `<div class="box block err">${esc(e.message)}</div>`; }
  $("bt-run").disabled = false;
};
$("ml-save").onclick = async () => {
  if (!(await confirmBox(`<div class="kicker">Réglages live</div><h3>Appliquer ces réglages ?</h3><p>Le signal, le mode auto, le risque par trade et le daily lock du terminal utiliseront ces paramètres.</p>`, "Appliquer"))) return;
  try { const r = await api("/api/mindlock/params", { params: readParams() }); renderParams(r.params); toast("Réglages MINDLOCK enregistrés"); loadML(); loadAccount(); } catch (e) { toast(e.message, true); }
};

/* ------------------------------------------------------------ boot */
initChart();
api("/api/config").then((c) => { S.anytime = c.anytime; }).catch(() => {});
setTf(ML.tf); setPeriod(S.period);
loadWatch(); select(S.sym); loadAccount(); loadMacro(); loadMarket(); tick();
setInterval(tick, 1000);
setInterval(() => { if (document.hidden) return; loadWatch(); loadAccount(); if (["1D", ...TFS].includes(S.period)) loadChart(); }, 15000);
setInterval(() => { if (!document.hidden) { loadMacro(); loadMarket(); loadML(); } }, 60000);
