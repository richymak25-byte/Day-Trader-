/* MINDLOCK Terminal: front end. Talks only to the local server (server.py). */
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const autoDigits = (x) => (Math.abs(x) < 10 ? 5 : 2);
const fmt = (x, d) => (x == null || isNaN(x) ? "—" : Number(x).toLocaleString("fr-FR", { minimumFractionDigits: d ?? autoDigits(x), maximumFractionDigits: d ?? autoDigits(x) }));
const usd = (x) => (x == null ? "—" : fmt(x, 2) + " $");
const pct = (x) => (x == null ? "—" : (x >= 0 ? "+" : "") + fmt(x, 2) + " %");
const cls = (x) => (x == null ? "" : x >= 0 ? "up" : "down");
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};

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
function confirmBox(html) {
  return new Promise((res) => {
    $("mbody").innerHTML = html; $("modal").classList.remove("hidden");
    const done = (v) => { $("modal").classList.add("hidden"); $("m-yes").onclick = $("m-no").onclick = null; res(v); };
    $("m-yes").onclick = () => done(true); $("m-no").onclick = () => done(false);
  });
}

/* ------------------------------------------------------------ state */
const S = {
  sym: store.get("dt.sym", "GC=F"),
  watch: store.get("dt.watch", ["GC=F", "AAPL", "TSLA", "NVDA", "MSFT", "AMZN", "META", "AMD", "SPY", "QQQ"]),
  period: "M30", plan: null, order: null, risk: null, bias: null, chartData: null,
};

/* ------------------------------------------------------------ chart */
const LW = window.LightweightCharts;
let chart, candles, volume, e9, e21, e100, vw, planLines = [];
function initChart() {
  const el = $("chart");
  chart = LW.createChart(el, {
    autoSize: true,
    layout: { background: { color: "transparent" }, textColor: "#7c8896", fontFamily: "system-ui" },
    grid: { vertLines: { color: "#141a22" }, horzLines: { color: "#141a22" } },
    rightPriceScale: { borderColor: "#1f2731" },
    timeScale: { borderColor: "#1f2731", timeVisible: true, secondsVisible: false },
    crosshair: { mode: 0 },
  });
  candles = chart.addCandlestickSeries({ upColor: "#22c58b", downColor: "#ef5a5a", borderVisible: false, wickUpColor: "#22c58b", wickDownColor: "#ef5a5a" });
  volume = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  e9 = chart.addLineSeries({ color: "#ffd400", lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
  e21 = chart.addLineSeries({ color: "#22c55e", lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
  e100 = chart.addLineSeries({ color: "#ef4444", lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
  vw = chart.addLineSeries({ color: "#f2b43c", lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false });
}
function emaSeries(bars, n) {
  const k = 2 / (n + 1); let e = bars.length ? bars[0].c : 0;
  return bars.map((b) => ({ time: b.time, value: (e = b.c * k + e * (1 - k)) }));
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
  add(S.plan.entry, "#dfe6ee", "Entrée"); add(S.plan.stop, "#ef5a5a", "Stop"); add(S.plan.target, "#22c58b", "Cible");
}
async function loadChart() {
  try {
    const d = await api(`/api/chart?symbol=${encodeURIComponent(S.sym)}&period=${S.period}`);
    S.chartData = d;
    const off = d.gmtoffset || 0;
    const bars = d.bars.map((b) => ({ ...b, time: b.t + off }));
    const prec = autoDigits(d.price || (bars.length ? bars[bars.length - 1].c : 100));
    candles.applyOptions({ priceFormat: { type: "price", precision: prec, minMove: Math.pow(10, -prec) } });
    candles.setData(bars.map((b) => ({ time: b.time, open: b.o, high: b.h, low: b.l, close: b.c })));
    volume.setData(bars.map((b) => ({ time: b.time, value: b.v, color: b.c >= b.o ? "rgba(34,197,139,.35)" : "rgba(239,90,90,.35)" })));
    e9.setData(emaSeries(bars, 20)); e21.setData(emaSeries(bars, 50)); e100.setData(emaSeries(bars, 100));
    const intraday = ["1D", "5D"].includes(S.period);
    vw.setData(intraday ? vwapSeries(bars) : []);
    chart.timeScale().fitContent();
    drawPlan(); drawMarkers();
    renderHeader(d);
    if (!$("t-entry").value || $("t-entry").dataset.sym !== S.sym) { $("t-entry").value = d.price?.toFixed(autoDigits(d.price)) ?? ""; $("t-entry").dataset.sym = S.sym; }
  } catch (e) { toast(e.message, true); }
}
function renderHeader(d) {
  $("c-sym").textContent = d.symbol; $("c-name").textContent = d.name || "";
  $("c-price").textContent = fmt(d.price);
  const ch = d.price - d.prev_close, cp = (ch / d.prev_close) * 100;
  $("c-chg").innerHTML = d.prev_close ? `<span class="${cls(ch)}">${ch >= 0 ? "+" : ""}${fmt(ch, autoDigits(d.price))} (${pct(cp)})</span>` : "";
  $("c-stats").innerHTML = [
    ["Plus haut", d.day_high], ["Plus bas", d.day_low], ["Clôture préc.", d.prev_close],
    ["Volume", d.volume != null ? d.volume.toLocaleString("fr-FR") : null], ["52 sem. haut", d.high52], ["52 sem. bas", d.low52],
  ].map(([k, v]) => `<span>${k} <b>${typeof v === "number" ? fmt(v) : v ?? "—"}</b></span>`).join("") + `<span>${esc(d.exchange || "")} · ${esc(d.currency || "")}</span>`;
  document.title = `${d.symbol} ${fmt(d.price)} · MINDLOCK Terminal`;
}

/* ------------------------------------------------------------ watchlist */
function spark(vals, up) {
  if (!vals || vals.length < 2) return "";
  const mn = Math.min(...vals), mx = Math.max(...vals), w = 56, h = 20;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1)) * w},${h - ((v - mn) / (mx - mn || 1)) * h}`).join(" ");
  return `<svg width="${w}" height="${h}"><polyline fill="none" stroke="${up ? "#22c58b" : "#ef5a5a"}" stroke-width="1.2" points="${pts}"/></svg>`;
}
async function loadWatch() {
  if (!S.watch.length) { $("watchlist").innerHTML = `<div class="empty">Ajoutez un symbole.</div>`; return; }
  try {
    const { quotes } = await api(`/api/quotes?symbols=${S.watch.map(encodeURIComponent).join(",")}`);
    $("watchlist").innerHTML = quotes.map((q, i) => {
      const s = S.watch[i];
      if (q.error) return `<div class="wl" data-s="${esc(s)}"><div><div class="s">${esc(s)}</div><div class="n err">indisponible</div></div><div></div><div></div><button class="x" data-x="${esc(s)}">×</button></div>`;
      return `<div class="wl ${s === S.sym ? "sel" : ""}" data-s="${esc(s)}">
        <div><div class="s">${esc(s)}</div><div class="n">${esc(q.name)}</div></div>
        <div>${spark(q.spark, q.change >= 0)}</div>
        <div class="p mono"><div>${fmt(q.price)}</div><div class="${cls(q.change_pct)}" style="font-size:11px">${pct(q.change_pct)}</div></div>
        <button class="x" data-x="${esc(s)}" title="Retirer">×</button></div>`;
    }).join("");
  } catch (e) { toast(e.message, true); }
}
$("watchlist").addEventListener("click", (ev) => {
  const x = ev.target.closest("[data-x]");
  if (x) { S.watch = S.watch.filter((s) => s !== x.dataset.x); store.set("dt.watch", S.watch); loadWatch(); return; }
  const row = ev.target.closest(".wl"); if (row) select(row.dataset.s);
});
$("add-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const s = $("add-sym").value.trim().toUpperCase(); if (!s) return;
  if (!S.watch.includes(s)) { S.watch.push(s); store.set("dt.watch", S.watch); }
  $("add-sym").value = ""; loadWatch(); select(s);
});

/* ------------------------------------------------------------ selection */
function select(sym) {
  S.sym = sym; store.set("dt.sym", sym);
  S.plan = S.order = S.risk = null; renderTicket();
  document.querySelectorAll(".wl").forEach((r) => r.classList.toggle("sel", r.dataset.s === sym));
  loadChart(); loadBias(); loadNews(); loadML();
}

/* ------------------------------------------------------------ search */
let searchTimer;
$("search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = $("search").value.trim();
  if (!q) { $("search-results").classList.add("hidden"); return; }
  searchTimer = setTimeout(async () => {
    try {
      const { results } = await api(`/api/search?q=${encodeURIComponent(q)}`);
      $("search-results").innerHTML = results.length ? results.map((r) => `<div data-s="${esc(r.symbol)}"><b>${esc(r.symbol)}</b><span class="muted">${esc(r.name || "")} · ${esc(r.exchange || "")}</span></div>`).join("") : `<div class="muted">Aucun résultat</div>`;
      $("search-results").classList.remove("hidden");
    } catch {}
  }, 250);
});
$("search").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { const s = $("search").value.trim().toUpperCase(); if (s) { $("search-results").classList.add("hidden"); $("search").value = ""; select(s); } } });
$("search-results").addEventListener("click", (ev) => { const d = ev.target.closest("[data-s]"); if (!d) return; $("search-results").classList.add("hidden"); $("search").value = ""; select(d.dataset.s); });
document.addEventListener("click", (ev) => { if (!ev.target.closest(".search")) $("search-results").classList.add("hidden"); });

/* ------------------------------------------------------------ periods / tabs */
$("periods").addEventListener("click", (ev) => {
  const b = ev.target.closest("button"); if (!b) return;
  S.period = b.dataset.p; document.querySelectorAll("#periods button").forEach((x) => x.classList.toggle("on", x === b)); loadChart();
});
$("tabbar").addEventListener("click", (ev) => {
  const b = ev.target.closest("button"); if (!b) return;
  document.querySelectorAll("#tabbar button").forEach((x) => x.classList.toggle("on", x === b));
  document.querySelectorAll(".tabbody").forEach((x) => x.classList.toggle("hidden", x.id !== "tab-" + b.dataset.t));
  if (b.dataset.t === "macro") loadMacro();
});

/* ------------------------------------------------------------ bias */
async function loadBias() {
  $("bias").innerHTML = `<div class="muted">Analyse de ${esc(S.sym)}…</div>`;
  try {
    const b = await api(`/api/bias?symbol=${encodeURIComponent(S.sym)}`); S.bias = b;
    const col = b.label === "HAUSSIER" ? "up" : b.label === "BAISSIER" ? "down" : "muted";
    $("bias").innerHTML = `
      <div class="gauge"><div class="big ${col}">${b.label}</div><div class="muted">score <b class="mono ${cls(b.score)}">${b.score > 0 ? "+" : ""}${b.score}</b> / 100<br>confiance ${b.confidence} %</div></div>
      <div class="meter"><i style="left:${(b.score + 100) / 2}%"></i></div>
      <ul class="reasons">${b.reasons.map((r) => `<li><span>${esc(r.text)}</span><span class="mono ${cls(r.points)}">${r.points > 0 ? "+" : ""}${r.points}</span></li>`).join("")}</ul>
      ${b.macro_tags.length ? `<div style="margin-top:8px">${b.macro_tags.map((t) => `<span class="tag y">${esc(t)}</span>`).join("")}</div>` : ""}
      <div class="note">${esc(b.note)}</div>`;
  } catch (e) { $("bias").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}
$("bias-refresh").onclick = loadBias;

/* ------------------------------------------------------------ news */
function ago(t) {
  const d = new Date(String(t).replace(" ", "T")); if (isNaN(d)) return "";
  const m = Math.round((Date.now() - d) / 60000);
  return m < 60 ? `il y a ${m} min` : m < 1440 ? `il y a ${Math.round(m / 60)} h` : d.toLocaleDateString("fr-FR");
}
async function loadNews() {
  $("news-sym").textContent = S.sym; $("news").innerHTML = `<div class="muted">Chargement…</div>`;
  try {
    const { items } = await api(`/api/news?symbol=${encodeURIComponent(S.sym)}`);
    $("news").innerHTML = items.length ? items.map((n) => `<a href="${esc(n.link)}" target="_blank" rel="noopener noreferrer">
      <div class="t">${esc(n.title)}</div>
      <div class="m">${ago(n.time)} ${n.sentiment > 0 ? '<span class="tag g">positif</span>' : n.sentiment < 0 ? '<span class="tag r">négatif</span>' : ""}${n.tags.map((t) => `<span class="tag y">${esc(t)}</span>`).join("")}</div></a>`).join("") : `<div class="empty">Aucune news.</div>`;
  } catch (e) { $("news").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}

/* ------------------------------------------------------------ ticket: plan -> size -> risk -> send */
function steps(n, bad) {
  for (let i = 1; i <= 4; i++) $("st" + i).className = "step" + (i < n ? " done" : i === n && bad ? " bad" : i === n && !bad && n === 5 ? " done" : "");
}
$("t-rule").addEventListener("change", () => {
  const r = $("t-rule").value;
  $("t-param").value = r === "pct" ? 1 : r === "atr" ? 0.5 : (Number($("t-entry").value || 0) * 0.99).toFixed(2);
});
$("t-plan").onclick = async () => {
  S.plan = S.order = S.risk = null;
  const rule = $("t-rule").value;
  const body = { entry: $("t-entry").value, stop_rule: rule, stop_param: $("t-param").value, target_r: $("t-r").value };
  if (rule === "atr") { if (!S.bias?.atr_daily) { toast("ATR indisponible", true); return; } body.atr = S.bias.atr_daily; }
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
    <div class="kv"><span>Stop <span class="muted">(${esc(p.stop_label)})</span></span><b class="mono down">${fmt(p.stop)}</b></div>
    <div class="kv"><span>Cible <span class="muted">(${esc(p.target_label)})</span></span><b class="mono up">${fmt(p.target)}</b></div>
    <div class="kv"><span>Risque / action</span><b class="mono">${fmt(p.risk, 2)} $ · R:R ${fmt(p.reward_risk, 2)}</b></div></div>`;
  if (s && s.refusal) { $("t-out").innerHTML = h + `<div class="box block err">${esc(s.refusal)}</div>`; steps(2, true); return; }
  if (s) h += `<div class="box"><div class="kv"><span>Quantité</span><b class="mono">${s.qty} actions</b></div>
    <div class="kv"><span>Montant</span><b class="mono">${usd(s.cost)}</b></div>
    <div class="kv"><span>Perte max au stop</span><b class="mono down">${usd(s.max_loss)}</b></div>
    <div class="kv"><span>Limite décisive</span><b>${esc(s.bound_by)}</b></div></div>`;
  const r = S.risk;
  if (r) {
    const blockers = r.reasons.filter((x) => !(S.anytime && x.startsWith("window:")));
    const ok = r.verdict === "PASS", canSend = blockers.length === 0;
    h += `<div class="box ${ok ? "pass" : "block"}"><b class="${ok ? "up" : "down"}">${ok ? "PASS" : "BLOCK"}</b> · Risk Manager
      ${r.reasons.map((x) => `<div class="err">• ${esc(x)}</div>`).join("")}${r.warnings.map((x) => `<div class="muted">~ ${esc(x)}</div>`).join("")}</div>`;
    if (!ok && canSend) h += `<div class="muted" style="margin-top:6px">Mode --anytime : seule la fenêtre horaire bloque, l'entraînement paper est autorisé.</div>`;
    h += `<button id="t-send" class="wide buy" ${canSend ? "" : "disabled"}>Acheter ${s.qty} ${esc(p.symbol)} (paper, bracket)</button>`;
    steps(ok ? 4 : 3, !ok);
  }
  $("t-out").innerHTML = h;
  const btn = $("t-send");
  if (btn) btn.onclick = sendOrder;
}
async function sendOrder() {
  const p = S.plan, s = S.sizing;
  const ok = await confirmBox(`<h3 style="margin-top:0">Confirmer l'ordre paper</h3>
    <p>Acheter <b>${s.qty} ${esc(p.symbol)}</b> au marché (~${fmt(p.entry)}), stop <b class="down">${fmt(p.stop)}</b>, cible <b class="up">${fmt(p.target)}</b>.</p>
    <p class="muted">Perte max au stop ≈ ${usd(s.max_loss)}. Compte simulé : aucun ordre réel n'est envoyé.</p>`);
  if (!ok) return;
  try {
    const acct = await api("/api/paper/order", { symbol: p.symbol, side: "buy", qty: s.qty, stop: p.stop, target: p.target, confirm: true, why: "ticket" });
    renderAccount(acct); toast(`Achat de ${s.qty} ${p.symbol} exécuté (paper)`); steps(5);
  } catch (e) { toast(e.message, true); }
}

/* ------------------------------------------------------------ account */
function renderAccount(a) {
  $("account").innerHTML = [["Équité", usd(a.equity)], ["Cash", usd(a.cash)],
    ["P&L total", `<span class="${cls(a.total_pnl)}">${usd(a.total_pnl)}</span>`], ["Réalisé aujourd'hui", `<span class="${cls(a.realized_today)}">${usd(a.realized_today)}</span>`]]
    .map(([k, v]) => `<div class="kv"><span class="muted">${k}</span><b class="mono">${v}</b></div>`).join("") + rulesBox(a.day);
  S.lock = a.day && a.day.lock ? a.day.message : null;
  $("t-plan").disabled = !!S.lock;
  $("t-plan").textContent = S.lock ? "Verrou journalier actif" : "Calculer le plan";
  $("lockbar").classList.toggle("hidden", !S.lock);
  $("lockbar").className = "lockbar" + (S.lock ? (a.day.lock.kind === "win" ? " win" : " loss") : " hidden");
  $("lockbar").textContent = S.lock || "";
  $("tab-positions").innerHTML = a.positions.length ? `<table><tr><th>Symbole</th><th>Qté</th><th>PRU</th><th>Prix</th><th>Valeur</th><th>P&L</th><th></th></tr>
    ${a.positions.map((p) => `<tr><td class="l"><a href="#" data-s="${esc(p.symbol)}" style="color:inherit">${esc(p.symbol)}</a></td><td>${p.qty}</td><td>${fmt(p.avg)}</td><td>${fmt(p.price)}</td><td>${fmt(p.value, 2)}</td>
      <td class="${cls(p.pnl)}">${fmt(p.pnl, 2)} (${pct(p.pnl_pct)})</td><td><button class="sm sell" data-close="${esc(p.symbol)}" data-q="${p.qty}">Clôturer</button></td></tr>`).join("")}</table>` : `<div class="empty">Aucune position ouverte.</div>`;
  $("tab-orders").innerHTML = a.orders.length ? `<table><tr><th>Symbole</th><th>Qté</th><th>Stop</th><th>Cible</th><th>Ouvert</th></tr>
    ${a.orders.map((o) => `<tr><td class="l">${esc(o.symbol)}</td><td>${o.qty}</td><td class="down">${fmt(o.stop)}</td><td class="up">${fmt(o.target)}</td><td>${esc(o.at.slice(0, 16).replace("T", " "))} UTC</td></tr>`).join("")}</table>
    <p class="muted">Les stops et cibles sont vérifiés à chaque rafraîchissement (toutes les 15 s) contre le dernier prix.</p>` : `<div class="empty">Aucun ordre bracket actif.</div>`;
  $("tab-fills").innerHTML = a.fills.length ? `<table><tr><th>Heure (UTC)</th><th>Symbole</th><th>Sens</th><th>Qté</th><th>Prix</th><th>P&L</th><th>Origine</th></tr>
    ${a.fills.map((f) => `<tr><td>${esc(f.at.slice(0, 16).replace("T", " "))}</td><td class="l">${esc(f.symbol)}</td><td class="${f.side === "buy" ? "up" : "down"}">${f.side === "buy" ? "Achat" : "Vente"}</td><td>${f.qty}</td><td>${fmt(f.price)}</td><td class="${cls(f.pnl)}">${f.side === "sell" ? fmt(f.pnl, 2) : ""}</td><td class="l muted">${esc(f.why)}</td></tr>`).join("")}</table>` : `<div class="empty">Aucune exécution.</div>`;
}
$("tab-positions").addEventListener("click", async (ev) => {
  const a = ev.target.closest("[data-s]"); if (a) { ev.preventDefault(); select(a.dataset.s); return; }
  const b = ev.target.closest("[data-close]"); if (!b) return;
  if (!(await confirmBox(`<h3 style="margin-top:0">Clôturer ${esc(b.dataset.close)} ?</h3><p>Vendre ${b.dataset.q} actions au marché (paper).</p>`))) return;
  try { renderAccount(await api("/api/paper/order", { symbol: b.dataset.close, side: "sell", qty: Number(b.dataset.q), confirm: true, why: "clôture manuelle" })); toast("Position clôturée"); }
  catch (e) { toast(e.message, true); }
});
function rulesBox(d) {
  if (!d) return "";
  const lo = -d.loss_limit, hi = d.win_limit, span = hi - lo || 1;
  const pos = Math.max(0, Math.min(100, ((d.pnl - lo) / span) * 100));
  const lock = d.lock ? `<span class="tag ${d.lock.kind === "win" ? "g" : "r"}">VERROUILLÉ ${d.lock.kind === "win" ? "GAIN" : "PERTE"}</span>` : `<span class="tag">ouvert</span>`;
  return `<div class="rules">
    <div class="kv"><span class="muted">Risque / trade (${fmt(d.risk_pct, 1)} %)</span><b class="mono">${usd(d.risk_per_trade)}</b></div>
    <div class="kv"><span class="muted">P&L du jour</span><b class="mono ${cls(d.pnl)}">${usd(d.pnl)}</b></div>
    <div class="lockmeter"><i style="left:${pos}%"></i></div>
    <div class="kv sm"><span class="down">-${usd(d.loss_limit)}</span>${lock}<span class="up">+${usd(d.win_limit)}</span></div></div>`;
}
async function loadAccount() { try { renderAccount(await api("/api/paper")); } catch (e) { toast(e.message, true); } }
$("reset").onclick = async () => {
  if (!(await confirmBox(`<h3 style="margin-top:0">Réinitialiser le compte paper ?</h3><p>Positions, ordres et historique simulés seront effacés.</p>`))) return;
  renderAccount(await api("/api/paper/reset", {})); toast("Compte paper réinitialisé");
};

/* ------------------------------------------------------------ scanner */
const SETUP_FR = { breakout_20d: ["Cassure 20 j", "g"], bull_flag: ["Bull flag", "g"], reclaim_ma20: ["Reprise MM20", "g"], oversold: ["Survendu", "r"], oversold_bounce: ["Rebond survente", "y"] };
$("scan-run").onclick = async () => {
  $("scan-out").innerHTML = `<div class="muted">Scan de ${S.watch.length} graphiques…</div>`;
  try {
    const { rows } = await api(`/api/scan?symbols=${S.watch.map(encodeURIComponent).join(",")}`);
    $("scan-out").innerHTML = `<table><tr><th>Symbole</th><th>Prix</th><th>Jour</th><th>6 mois</th><th>RSI 14</th><th>Setups</th></tr>
      ${rows.map((r) => r.error ? `<tr><td class="l">${esc(r.symbol)}</td><td colspan="5" class="l err">${esc(r.error)}</td></tr>` :
        `<tr><td class="l"><a href="#" data-s="${esc(r.symbol)}" style="color:inherit">${esc(r.symbol)}</a></td><td>${fmt(r.price)}</td><td class="${cls(r.change_pct)}">${pct(r.change_pct)}</td><td class="${cls(r.change_6m)}">${pct(r.change_6m)}</td><td>${fmt(r.rsi, 0)}</td>
        <td class="l">${r.setups.length ? r.setups.map((k) => `<span class="tag ${(SETUP_FR[k] || [k, ""])[1]}">${(SETUP_FR[k] || [k])[0]}</span>`).join("") : '<span class="muted">—</span>'}</td></tr>`).join("")}</table>`;
  } catch (e) { $("scan-out").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
};
$("scan-out").addEventListener("click", (ev) => { const a = ev.target.closest("[data-s]"); if (a) { ev.preventDefault(); select(a.dataset.s); } });

/* ------------------------------------------------------------ time machine */
$("tm-run").onclick = async () => {
  if (!S.plan || S.plan.symbol !== S.sym) { toast("Calculez d'abord un plan dans le ticket d'ordre", true); return; }
  $("tm-out").innerHTML = `<div class="muted">10 000 séances simulées pour ${esc(S.sym)}…</div>`;
  try {
    const d = await api("/api/timemachine", { symbol: S.sym, entry: S.plan.entry, stop: S.plan.stop, target: S.plan.target, entry_time: $("tm-time").value });
    const m = d.mc, h = d.history || {};
    const w = (x) => Math.max(0, x * 100).toFixed(1);
    $("tm-out").innerHTML = `
      <div class="bar3"><div style="width:${w(m.p_target)}%;background:var(--up)">${w(m.p_target)}%</div><div style="width:${w(m.p_neither)}%;background:#5b6573">${w(m.p_neither)}%</div><div style="width:${w(m.p_stop)}%;background:var(--down)">${w(m.p_stop)}%</div></div>
      <div class="muted" style="font-size:11px">vert : cible d'abord · gris : ni l'un ni l'autre à ${esc(m.until)} · rouge : stop d'abord</div>
      <div class="box"><div class="kv"><span>R moyen</span><b class="mono ${cls(m.avg_R)}">${fmt(m.avg_R, 2)}</b></div>
        <div class="kv"><span>Clôture médiane (5 %–95 %)</span><b class="mono">${fmt(m.median_close)} (${fmt(m.p5_close)} – ${fmt(m.p95_close)})</b></div>
        <div class="kv"><span>Séances utilisées</span><b class="mono">${m.pool_sessions} (${esc(m.pool_from)} → ${esc(m.pool_to)})</b></div>
        ${h.rule ? `<div class="kv"><span>Historique (même règle)</span><b class="mono">${h.triggered} déclench. · ${h.target ?? "?"} cible · ${h.stop ?? "?"} stop</b></div>` : ""}
      </div>
      <div class="box"><b>Verdict :</b> ${esc(d.verdict)}${(d.verdict_notes || []).map((n) => `<div class="muted">${esc(n)}</div>`).join("")}</div>
      ${(d.warnings || []).map((x) => `<div class="muted">~ ${esc(x)}</div>`).join("")}
      <div class="note">${esc(d.note)}</div>`;
  } catch (e) { $("tm-out").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
};

/* ------------------------------------------------------------ macro + market */
async function loadMacro() {
  try {
    const m = await api("/api/macro");
    $("regime").textContent = "MACRO " + m.regime; $("regime").className = "chip " + (m.regime === "RISK-ON" ? "riskon" : m.regime === "RISK-OFF" ? "riskoff" : "");
    $("regime").title = m.notes.join(" · ");
    $("tape").innerHTML = m.quotes.filter((q) => q.price != null).map((q) => `<span><b>${esc(q.label)}</b><span class="mono">${fmt(q.price)}</span> <span class="mono ${cls(q.change_pct)}">${pct(q.change_pct)}</span></span>`).join("");
    $("macro-out").innerHTML = `<p>Régime : <b class="${m.regime === "RISK-ON" ? "up" : m.regime === "RISK-OFF" ? "down" : ""}">${m.regime}</b> <span class="muted">— ${esc(m.notes.join(" · "))}</span></p>
      <div class="macro-grid">${m.quotes.map((q) => `<div class="mcard" data-s="${esc(q.symbol)}"><div class="n">${esc(q.label)}</div><div class="mono">${fmt(q.price)}</div><div class="mono ${cls(q.change_pct)}">${pct(q.change_pct)}</div>${spark(q.spark, q.change >= 0)}</div>`).join("")}</div>`;
  } catch (e) { $("tape").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
}
$("macro-out").addEventListener("click", (ev) => { const c = ev.target.closest("[data-s]"); if (c) select(c.dataset.s); });
async function loadMarket() {
  try {
    const m = await api("/api/market");
    $("market").textContent = m.open ? "MARCHÉ OUVERT" : "MARCHÉ FERMÉ"; $("market").className = "chip " + (m.open ? "on" : "off");
    $("market").title = `${m.reason}${m.next_open ? " · prochaine ouverture " + m.next_open + " NY" : ""}`;
  } catch {}
}
function tick() {
  const ny = new Date().toLocaleTimeString("fr-FR", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  $("clock").textContent = "New York " + ny;
}

/* ------------------------------------------------------------ MINDLOCK EMA CORE TREND V.1 */
const ML = { data: null, tf: store.get("dt.mltf", "M30"), lines: [] };
const SIDE_FR = { buy: "BUY", sell: "SELL" };
const px = (x, d) => (x == null ? "—" : Number(x).toFixed(d ?? 2));
function setTf(tf) {
  ML.tf = tf; store.set("dt.mltf", tf);
  document.querySelectorAll("#ml-tf button").forEach((x) => x.classList.toggle("on", x.dataset.tf === tf));
}
$("ml-tf").addEventListener("click", (ev) => { const b = ev.target.closest("button"); if (!b) return; setTf(b.dataset.tf); loadML(); });
function drawMarkers() {
  const d = ML.data, cd = S.chartData;
  ML.lines.forEach((l) => candles.removePriceLine(l)); ML.lines = [];
  if (!d || !cd || S.sym !== d.symbol || S.period !== d.tf) { candles.setMarkers([]); return; }
  const have = new Set(cd.bars.map((b) => b.t)), off = cd.gmtoffset || 0;
  candles.setMarkers(d.markers.filter((m) => have.has(m.t)).map((m) => ({
    time: m.t + off, position: m.side === "buy" ? "belowBar" : "aboveBar", shape: m.side === "buy" ? "arrowUp" : "arrowDown",
    color: m.side === "buy" ? "#7CFC00" : "#ef5a5a", text: m.side === "buy" ? "BUY" : "SELL" })));
  const line = (price, color, title, style = 2) => price != null && ML.lines.push(candles.createPriceLine({ price, color, lineWidth: 2, lineStyle: style, axisLabelVisible: true, title }));
  if (d.position) { line(d.position.entry, "#dfe6ee", "Entrée", 0); line(d.position.sl, "#ef5a5a", d.position.be ? "BE" : "SL"); line(d.position.tp, "#22c58b", "TP"); }
  else if (d.pending) { line(d.pending.price, "#00e5ff", "Limit " + SIDE_FR[d.pending.side]); line(d.pending.sl, "rgba(239,90,90,.6)", "SL"); line(d.pending.tp, "rgba(34,197,139,.6)", "TP"); }
}
function posList(d) {
  if (!d.positions.length && !d.pendings.length && !d.autos.length) return "";
  const rows = d.positions.map((p) => `<div class="kv"><span><b class="${p.side === "buy" ? "up" : "down"}">${SIDE_FR[p.side]}</b> ${esc(p.key.replace("|", " "))} · ${fmt(p.lots, 2)} lot${p.be ? ' <span class="tag">BE</span>' : ""}</span>
    <span><b class="mono ${cls(p.pnl)}">${usd(p.pnl)}</b> <button class="ghost sm" data-mlclose="${esc(p.key)}" title="Clôturer">×</button></span></div>`).join("")
    + d.pendings.map((p) => `<div class="kv"><span class="muted">Limit ${SIDE_FR[p.side]} ${esc(p.key.replace("|", " "))} @ ${px(p.price, d.key === p.key ? digitsOf(d) : 5)}</span>
    <button class="ghost sm" data-mlcancel="${esc(p.key)}" title="Annuler l'ordre">×</button></div>`).join("");
  return `<div class="box plist"><b>Positions et ordres MINDLOCK</b>${rows || '<div class="muted">aucun</div>'}
    ${d.autos.length ? `<div class="muted sm" style="margin-top:6px">Auto : ${d.autos.map((k) => esc(k.replace("|", " "))).join(", ")}</div>` : ""}</div>`;
}
const digitsOf = (d) => { const t = d.params.MinTick || 0.01; return t < 1 ? Math.max(2, Math.round(-Math.log10(t))) : 2; };
async function loadML() {
  $("ml-title").textContent = `${S.sym} ${ML.tf}`;
  if ($("bt-target")) $("bt-target").textContent = `${S.sym} ${ML.tf}`;
  try {
    const d = await api(`/api/mindlock/signal?symbol=${encodeURIComponent(S.sym)}&tf=${ML.tf}`);
    if (d.symbol !== S.sym || d.tf !== ML.tf) return;
    ML.data = d;
    if (!ML.formShown) { renderParams(d.base_params); ML.formShown = true; }
    const nd = digitsOf(d), P = d.params, unit = P.Unit;
    const time = new Date((d.bar.t + d.bar_seconds) * 1000).toLocaleString("fr-FR", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" });
    const col = d.signal === "buy" ? "up" : d.signal === "sell" ? "down" : "muted";
    const ckList = (side) => d.sides[side].checks.map((c) => `<div class="ck" title="${esc(c.detail)}"><span class="${c.ok ? "ok" : "no"}">${c.ok ? "✓" : "✗"}</span>${esc(c.name)}</div>`).join("");
    const pos = d.position, pend = d.pending;
    let h = `<div class="mlsig"><div><div class="big ${col}">${d.signal ? "SIGNAL " + SIDE_FR[d.signal] : "PAS DE SIGNAL"}</div>
        <div class="muted sm">bougie ${d.tf} clôturée à ${time} NY · ${px(d.bar.c, nd)} · tendance ${d.trend === "up" ? '<span class="up">▲</span>' : '<span class="down">▼</span>'}${d.in_session ? "" : ' · <span class="tag">hors session</span>'}</div></div>
        <button class="ghost sm" id="ml-chart">Graphique ${d.tf}</button></div>
      <div class="mlcols"><div class="mlcol"><h3 class="up">BUY</h3>${ckList("buy")}</div><div class="mlcol"><h3 class="down">SELL</h3>${ckList("sell")}</div></div>
      <div class="box"><div class="kv"><span>Entrée</span><b>${esc(d.entry_labels[P.EntryMethod])}${P.EntryMethod !== "MARKET" ? ` · expire ${P.PendingExpiryBars} bougies` : ""}</b></div>
        <div class="kv"><span>SL structurel</span><b class="mono">${P.StructLookback} bougies ± ${P.StructBuffer} u, max ${P.StopLossMax} u (${px(P.StopLossMax * unit, nd)})</b></div>
        <div class="kv"><span>TP · break-even</span><b class="mono">${P.TakeProfit} u (${px(P.TakeProfit * unit, nd)}) · ${P.UseBreakEven ? `BE à ${P.BreakEvenTrigger} u` : "BE off"}</b></div>
        <div class="kv"><span>Unité · contrat</span><b class="mono">${unit} · ${P.ContractSize}${P.Currency !== "USD" ? " · " + esc(P.Currency) : ""}</b></div>
        <div class="kv"><span>Risque · daily lock</span><b class="mono">${fmt(P.RiskPercent, 1)} % · ${P.UseDailyLock ? `-${fmt(P.DailyLossPercent, 1)} % / +${fmt(P.DailyProfitPercent, 1)} %` : "off"}${d.day.lock ? ' <span class="tag r">ACTIF</span>' : ""}</b></div>
        <div class="kv"><span>Session (NY)</span><b class="mono">${P.UseSession ? esc(P.Session.slice(0, 2) + ":" + P.Session.slice(2, 4) + "–" + P.Session.slice(5, 7) + ":" + P.Session.slice(7)) : "24 h"}</b></div></div>`;
    if (pos) h += `<div class="box ${pos.pnl >= 0 ? "pass" : "block"}"><div class="kv"><span>Position <b class="${pos.side === "buy" ? "up" : "down"}">${SIDE_FR[pos.side]}</b> ${fmt(pos.lots, 2)} lot</span><b class="mono ${cls(pos.pnl)}">${usd(pos.pnl)}</b></div>
        <div class="kv"><span>Entrée ${px(pos.entry, nd)}</span><span class="mono"><span class="down">${pos.be ? "BE" : "SL"} ${px(pos.sl, nd)}</span> · <span class="up">TP ${px(pos.tp, nd)}</span></span></div></div>`;
    else if (pend) h += `<div class="box"><div class="kv"><span>Ordre limit <b class="${pend.side === "buy" ? "up" : "down"}">${SIDE_FR[pend.side]}</b> @ <b class="mono">${px(pend.price, nd)}</b></span><span class="muted">${pend.age}/${P.PendingExpiryBars} bougies</span></div>
        <div class="kv"><span class="mono"><span class="down">SL ${px(pend.sl, nd)}</span> · <span class="up">TP ${px(pend.tp, nd)}</span></span><button class="ghost sm" data-mlcancel="${esc(d.key)}">Annuler</button></div></div>`;
    else if (d.signal) {
      const sd = d.sides[d.signal];
      h += `<div class="box"><div class="kv"><span>${P.EntryMethod === "MARKET" ? "Entrée au marché" : "Limit"} ${px(sd.entry, nd)}</span><span class="mono"><span class="down">SL ${px(sd.sl, nd)}</span> · <span class="up">TP ${px(sd.tp, nd)}</span></span></div>
        <div class="kv"><span>Taille</span><b class="mono">${fmt(sd.lots, 2)} lot · ${usd(sd.risk_usd)} au SL</b></div></div>
        <button id="ml-exec" class="wide ${d.signal}">${P.EntryMethod === "MARKET" ? (d.signal === "buy" ? "Acheter" : "Vendre") + " au marché" : "Placer le limit " + SIDE_FR[d.signal] + " @ " + px(sd.entry, nd)} (paper)</button>`;
    }
    h += `<label class="switch" style="margin-top:10px"><input type="checkbox" id="ml-auto" ${d.auto ? "checked" : ""}> Mode auto (paper) sur ${esc(d.symbol)} ${d.tf}</label>`;
    h += posList(d);
    if (d.events.length) h += `<div class="events">${d.events.slice(0, 6).map((e) => `<div><span class="muted">${new Date(e.t * 1000).toLocaleString("fr-FR", { timeZone: "America/New_York", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</span> ${esc(e.text)}</div>`).join("")}</div>`;
    h += `<div class="note">Port Python de ton Pine Script « MINDLOCK EMA CORE TREND V.1 ». Données Yahoo gratuites (or = GC=F), proches de ton courtier, pas identiques. Compte simulé.</div>`;
    $("ml").innerHTML = h;
    $("ml-chart").onclick = () => { S.period = d.tf; document.querySelectorAll("#periods button").forEach((x) => x.classList.toggle("on", x.dataset.p === d.tf)); loadChart(); };
    if ($("ml-exec")) $("ml-exec").onclick = async () => {
      const sd = d.sides[d.signal];
      const what = P.EntryMethod === "MARKET" ? `au marché ~${px(sd.entry, nd)}` : `ordre limit @ ${px(sd.entry, nd)} (expire après ${P.PendingExpiryBars} bougies)`;
      if (!(await confirmBox(`<h3 style="margin-top:0">${SIDE_FR[d.signal]} ${esc(d.symbol)} ${d.tf} (paper)</h3><p>${fmt(sd.lots, 2)} lot, ${what}.<br>SL structurel <b class="down">${px(sd.sl, nd)}</b>, TP <b class="up">${px(sd.tp, nd)}</b>.</p><p class="muted">Perte max au SL ≈ ${usd(sd.risk_usd)}. Le SL est recalculé au remplissage, comme dans ton script. Aucun ordre réel.</p>`))) return;
      try { await api("/api/mindlock/execute", { symbol: d.symbol, tf: d.tf, side: d.signal, confirm: true }); toast(P.EntryMethod === "MARKET" ? "Position ouverte (paper)" : "Ordre limit placé (paper)"); loadML(); loadAccount(); } catch (e) { toast(e.message, true); }
    };
    $("ml-auto").onchange = async (ev) => {
      const on = ev.target.checked;
      if (on && !(await confirmBox(`<h3 style="margin-top:0">Mode auto sur ${esc(d.symbol)} ${d.tf} ?</h3><p>Sur le compte <b>paper</b>, la stratégie prendra chaque signal à la clôture des bougies ${d.tf} : ordres limit, SL structurel, break-even, TP, fermeture et inversion sur signal opposé, session et daily lock.</p><p class="muted">Le serveur doit rester allumé. Aucun ordre réel.</p>`))) { ev.target.checked = false; return; }
      try { await api("/api/mindlock/auto", { symbol: d.symbol, tf: d.tf, on }); toast(on ? `Auto activé sur ${d.symbol} ${d.tf} (paper)` : "Mode auto désactivé"); loadML(); } catch (e) { toast(e.message, true); ev.target.checked = !on; }
    };
    drawMarkers();
  } catch (e) { $("ml").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}
$("ml").addEventListener("click", async (ev) => {
  const c = ev.target.closest("[data-mlcancel]");
  if (c) {
    try { await api("/api/mindlock/close", { key: c.dataset.mlcancel, what: "pending" }); toast("Ordre annulé"); loadML(); } catch (e) { toast(e.message, true); }
    return;
  }
  const b = ev.target.closest("[data-mlclose]"); if (!b) return;
  if (!(await confirmBox(`<h3 style="margin-top:0">Clôturer ${esc(b.dataset.mlclose.replace("|", " "))} ?</h3><p>Au dernier prix (paper).</p>`))) return;
  try { await api("/api/mindlock/close", { key: b.dataset.mlclose }); toast("Position clôturée"); loadML(); loadAccount(); } catch (e) { toast(e.message, true); }
});
$("ml-refresh").onclick = loadML;

$("mlscan-run").onclick = async () => {
  const syms = S.watch.slice(0, 25);
  $("scan-out").innerHTML = `<div class="muted">MINDLOCK sur ${syms.length} actifs × 5 timeframes…</div>`;
  try {
    const r = await api(`/api/mindlock/scan?symbols=${syms.map(encodeURIComponent).join(",")}`);
    const at = {}; r.cells.forEach((c) => { at[c.symbol + "|" + c.tf] = c; });
    const cell = (c) => {
      if (!c || c.error) return `<td class="muted" title="${esc(c && c.error)}">—</td>`;
      const tr = c.aligned === "up" ? '<span class="up">▲▲</span>' : c.aligned === "down" ? '<span class="down">▼▼</span>' : c.trend === "up" ? '<span class="up">▲</span>' : '<span class="down">▼</span>';
      const sig = c.signal ? `<span class="sig ${c.signal === "buy" ? "up" : "down"}">${SIDE_FR[c.signal]}</span>`
        : c.last_signal ? `<span class="muted">${SIDE_FR[c.last_signal]} −${c.bars_ago}</span>` : "";
      return `<td data-s="${esc(c.symbol)}" data-tf="${c.tf}" title="RSI ${fmt(c.rsi, 0)}">${tr} ${sig}</td>`;
    };
    $("scan-out").innerHTML = `<table class="mx"><tr><th>Actif</th>${r.tfs.map((t) => `<th style="text-align:center">${t}</th>`).join("")}</tr>
      ${r.symbols.map((s) => `<tr><td class="l"><b>${esc(s)}</b></td>${r.tfs.map((t) => cell(at[s + "|" + t])).join("")}</tr>`).join("")}</table>
      <p class="note"><b>BUY / SELL</b> en gras : signal sur la dernière bougie clôturée (croisement EMA 20/50 + EMA 100 + RSI). « SELL −12 » : dernier signal il y a 12 bougies.
      ▲▲ / ▼▼ : EMA 20/50 et clôture/EMA 100 alignées ; ▲ / ▼ : EMA 20 vs EMA 50 seulement. Clique une case pour l'ouvrir.</p>`;
  } catch (e) { $("scan-out").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
};
$("scan-out").addEventListener("click", (ev) => {
  const c = ev.target.closest("[data-tf]"); if (!c) return;
  setTf(c.dataset.tf); S.period = c.dataset.tf;
  document.querySelectorAll("#periods button").forEach((x) => x.classList.toggle("on", x.dataset.p === c.dataset.tf));
  select(c.dataset.s);
});

const ENTRY_OPTS = [["MARKET", "MARKET — clôture du signal"], ["BODY", "BODY LIMIT — corps + tolérance"], ["EMA20", "EMA 20 — limit"], ["EMA50", "EMA 50 — limit"], ["CORE", "EMA CORE 20/50 — milieu"], ["OTE", "OTE Fibonacci — limit"]];
const PARAM_GROUPS = [
  ["1. Moteur EMA", [["EMA20Length", "Période EMA 20"], ["EMA50Length", "Période EMA 50"], ["EMA100Length", "Période EMA 100"]]],
  ["2. Filtre RSI", [["UseRSI", "Activer le filtre RSI"], ["RSILength", "Période RSI"], ["RSIMid", "Niveau de validation"]]],
  ["3. Méthode d'entrée", [["EntryMethod", "Mode d'entrée", ENTRY_OPTS], ["Tolerance", "Tolérance (unités)"], ["PendingExpiryBars", "Expiration du pending (bougies)"],
    ["CancelOnOppositeSignal", "Remplacer le pending sur signal opposé"], ["CloseOnOppositeSignal", "Fermer sur signal opposé"], ["EnterAfterClose", "Entrer dans le nouveau sens"],
    ["OTELevel", "OTE — niveau", [["0.618", "0.618"], ["0.705", "0.705"], ["0.789", "0.789"]]], ["OTELookback", "OTE — recherche de l'impulsion"]]],
  ["4. Gestion du risque", [["UnitMode", "Unité", [["Auto", "Auto"], ["Pips", "Pips"], ["Ticks", "Ticks"], ["Points", "Points"]]], ["UnitOverride", "Taille d'une unité en prix (0 = auto)"],
    ["StopLossMax", "SL max (unités)"], ["TakeProfit", "Take Profit (unités)"], ["StructLookback", "SL structurel — bougies"], ["StructBuffer", "SL structurel — marge (unités)"],
    ["CapStructSL", "Plafonner le SL au SL max"], ["UseBreakEven", "Break-even"], ["BreakEvenTrigger", "Activation BE (unités)"]]],
  ["5. Daily Lock et session", [["UseDailyLock", "Activer le Daily Lock"], ["DailyLossPercent", "Daily Loss Lock %"], ["DailyProfitPercent", "Daily Profit Lock %"],
    ["UseSession", "Filtre de session"], ["Session", "Session (New York)"], ["CloseAtSessionEnd", "Fermer en fin de session"]]],
  ["Taille (terminal)", [["UseRiskPercent", "Risque en % du compte"], ["RiskPercent", "Risque %"], ["FixedLot", "Lot fixe"], ["MaxLeverage", "Levier max"], ["ContractSize", "Taille du contrat (0 = auto)"]]],
];
function renderParams(p) {
  $("ml-params").innerHTML = PARAM_GROUPS.map(([g, items]) => `<div class="pgroup"><h3>${g}</h3><div class="pgrid">${items.map(([k, label, opts]) => {
    if (opts) return `<label>${label}<select data-k="${k}">${opts.map(([v, t]) => `<option value="${v}" ${String(p[k]) === v ? "selected" : ""}>${t}</option>`).join("")}</select></label>`;
    if (typeof p[k] === "boolean") return `<label>${label}<select data-k="${k}" data-b="1"><option value="true" ${p[k] ? "selected" : ""}>oui</option><option value="false" ${p[k] ? "" : "selected"}>non</option></select></label>`;
    if (typeof p[k] === "string") return `<label>${label}<input data-k="${k}" value="${esc(p[k])}"></label>`;
    return `<label>${label}<input data-k="${k}" type="number" step="any" value="${p[k]}"></label>`;
  }).join("")}</div></div>`).join("");
}
function readParams() {
  const o = {}; document.querySelectorAll("#ml-params [data-k]").forEach((el) => { o[el.dataset.k] = el.dataset.b ? el.value === "true" : el.value; }); return o;
}
const REASON_FR = { cible: "TP", stop: "SL", "break-even": "BE", "signal opposé": "signal opposé", "fin de session": "fin de session", "clôture manuelle": "manuelle" };
let eqChart;
$("bt-run").onclick = async () => {
  $("bt-out").innerHTML = `<div class="muted">Backtest en cours…</div>`;
  try {
    const body = { params: readParams(), balance: $("bt-bal").value, symbol: S.sym, tf: ML.tf };
    const f = $("bt-csv").files[0]; if (f) { body.csv = await f.text(); body.csv_name = f.name; }
    const r = await api("/api/mindlock/backtest", body);
    const nd = (() => { const t = r.params.MinTick || 0.01; return t < 1 ? Math.max(2, Math.round(-Math.log10(t))) : 2; })();
    const day = (t) => new Date(t * 1000).toLocaleString("fr-FR", { timeZone: "America/New_York", year: "2-digit", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
    $("bt-out").innerHTML = `<div class="muted sm">${esc(r.source)} · ${day(r.from)} → ${day(r.to)} NY · unité ${r.params.Unit} · ${r.signals} signaux</div>
      <div class="kpis">${[["Résultat net", `<span class="${cls(r.net)}">${usd(r.net)}</span>`], ["En %", `<span class="${cls(r.net_pct)}">${pct(r.net_pct)}</span>`], ["En unités", `<span class="${cls(r.net_units)}">${fmt(r.net_units, 0)}</span>`],
        ["Trades", r.trades], ["Taux de réussite", fmt(r.win_rate, 1) + " %"], ["Profit factor", r.profit_factor == null ? "—" : fmt(r.profit_factor, 2)],
        ["Gain moyen", usd(r.avg_win)], ["Perte moyenne", usd(r.avg_loss)], ["Drawdown max", `${usd(r.max_drawdown)} (${fmt(r.max_drawdown_pct, 2)} %)`], ["Jours verrouillés", r.locked_days]]
        .map(([n, v]) => `<div class="kpi"><div class="n">${n}</div><div class="v">${v}</div></div>`).join("")}</div>
      <div class="muted sm">Sorties : ${Object.entries(r.exits).map(([k, v]) => `${esc(REASON_FR[k] || k)} ${v}`).join(" · ") || "—"}</div>
      <div id="eq" class="eqchart"></div>
      <table><tr><th>Entrée (NY)</th><th>Sens</th><th>Lot</th><th>Entrée</th><th>SL</th><th>Sortie</th><th>Raison</th><th>Unités</th><th>P&L</th></tr>
      ${r.trade_list.slice(0, 80).map((t) => `<tr><td>${day(t.t)}</td><td class="${t.side === "buy" ? "up" : "down"}">${SIDE_FR[t.side]}</td><td>${fmt(t.lots, 2)}</td><td>${px(t.entry, nd)}</td><td>${px(t.sl0, nd)}</td><td>${px(t.exit, nd)}</td><td class="l">${esc(REASON_FR[t.reason] || t.reason)}</td><td class="${cls(t.units)}">${fmt(t.units, 0)}</td><td class="${cls(t.pnl)}">${fmt(t.pnl, 2)}</td></tr>`).join("")}</table>
      <p class="note">Port de ton Pine Script, bougie par bougie : signal et ordres à la clôture, limit rempli au prix ou à l'ouverture en cas de gap, SL / TP actifs dès la bougie suivante, et si une bougie touche les deux, l'extrême le plus proche de l'ouverture passe en premier (comme le simulateur TradingView). Sans spread, commission ni slippage. Un résultat passé ne garantit rien.</p>`;
    eqChart = LW.createChart($("eq"), { autoSize: true, layout: { background: { color: "transparent" }, textColor: "#7c8896" }, grid: { vertLines: { visible: false }, horzLines: { color: "#141a22" } }, timeScale: { timeVisible: true } });
    eqChart.addAreaSeries({ lineColor: "#f2b43c", topColor: "rgba(242,180,60,.25)", bottomColor: "rgba(242,180,60,0)", lineWidth: 2 })
      .setData(r.curve.map((c) => ({ time: c.t, value: c.equity })));
    eqChart.timeScale().fitContent();
  } catch (e) { $("bt-out").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
};
$("ml-save").onclick = async () => {
  if (!(await confirmBox(`<h3 style="margin-top:0">Appliquer ces réglages en live ?</h3><p>Le signal, le mode auto, le risque par trade et le daily lock du terminal utiliseront ces paramètres.</p>`))) return;
  try { const r = await api("/api/mindlock/params", { params: readParams() }); renderParams(r.params); toast("Réglages MINDLOCK enregistrés"); loadML(); loadAccount(); } catch (e) { toast(e.message, true); }
};

/* ------------------------------------------------------------ boot */
initChart();
api("/api/config").then((c) => { S.anytime = c.anytime; }).catch(() => {});
setTf(ML.tf);

loadWatch(); select(S.sym); loadAccount(); loadMacro(); loadMarket(); tick();
setInterval(tick, 1000);
setInterval(() => { if (document.hidden) return; loadWatch(); loadAccount(); if (["1D", "M5", "M15", "M30", "H1", "H4"].includes(S.period)) loadChart(); }, 15000);
setInterval(() => { if (!document.hidden) { loadMacro(); loadMarket(); loadML(); } }, 60000);
