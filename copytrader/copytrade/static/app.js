"use strict";
// CopyTrade dashboard: vanilla JS, no external library, works offline.

const $ = (s, el) => (el || document).querySelector(s);
const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const nf2 = new Intl.NumberFormat("fr-FR", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const nf0 = new Intl.NumberFormat("fr-FR", {maximumFractionDigits: 0});
const nfq = new Intl.NumberFormat("fr-FR", {maximumFractionDigits: 2});
const money = (v) => v == null ? "-" : nf2.format(v);
const smoney = (v) => v == null ? "-" : (v > 0 ? "+" : v < 0 ? "-" : "") + nf2.format(Math.abs(v));
const cls = (v) => v > 0 ? "pos" : v < 0 ? "neg" : "";
const pct = (v) => v == null ? "-" : nfq.format(v) + " %";
const fmtTime = (ts) => ts ? new Date(ts * 1000).toLocaleString("fr-FR", {day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit"}) : "-";
const fmtDur = (s) => s == null ? "-" : s < 60 ? Math.round(s) + " s" : s < 3600 ? Math.round(s / 60) + " min" : nfq.format(s / 3600) + " h";

let META = null, STATE = null, VIEW = "dash", editing = null;

async function api(path, body) {
  const opt = body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove("show"), 2500); }
function table(cols, rows, empty) {
  if (!rows.length) return `<div class="empty">${esc(empty || "Rien pour l'instant")}</div>`;
  return `<table><thead><tr>${cols.map((c) => `<th class="${c.n ? "n" : ""}">${esc(c.h)}</th>`).join("")}</tr></thead><tbody>` +
    rows.map((r) => `<tr>${cols.map((c) => `<td class="${c.n ? "n " : ""}${c.cls ? c.cls(r) : ""}">${c.f(r)}</td>`).join("")}</tr>`).join("") + "</tbody></table>";
}
const acctName = (id) => { const a = STATE && STATE.accounts.find((x) => x.id === id); return a ? a.name : (id == null ? "-" : "#" + id); };

// ---------------------------------------------------------------- tooltip
const tip = $("#tooltip");
function showTip(e, html) { tip.innerHTML = html; tip.style.display = "block"; const w = tip.offsetWidth, h = tip.offsetHeight; let x = e.clientX + 14, y = e.clientY + 14; if (x + w > innerWidth - 8) x = e.clientX - w - 14; if (y + h > innerHeight - 8) y = e.clientY - h - 14; tip.style.left = x + "px"; tip.style.top = y + "px"; }
function hideTip() { tip.style.display = "none"; }

// ---------------------------------------------------------------- charts (SVG)
const NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs, parent) { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; }
function niceTicks(min, max, n) {
  if (min === max) { min -= 1; max += 1; }
  const span = max - min, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0) || mag * 10;
  const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step, out = [];
  for (let v = lo; v <= hi + step / 2; v += step) out.push(+v.toFixed(10));
  return out;
}
function shortNum(v) { const a = Math.abs(v); return a >= 1e6 ? nfq.format(v / 1e6) + "M" : a >= 1e4 ? nfq.format(v / 1e3) + "k" : nf0.format(v); }

function lineChart(el, pts, opt) {
  el.innerHTML = "";
  if (pts.length < 2) { el.innerHTML = `<div class="empty">Pas encore assez de trades</div>`; return; }
  const W = el.clientWidth || 600, H = el.clientHeight || 240, m = {l: 54, r: 12, t: 10, b: 24};
  const svg = svgEl("svg", {viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opt.label || "courbe"}, el);
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const ticks = niceTicks(Math.min(...ys), Math.max(...ys), 4);
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const X = (v) => m.l + (x1 === x0 ? 0 : (v - x0) / (x1 - x0)) * (W - m.l - m.r);
  const Y = (v) => H - m.b - (v - y0) / (y1 - y0 || 1) * (H - m.t - m.b);
  const g = svgEl("g", {class: "axis"}, svg);
  ticks.forEach((t) => { svgEl("line", {x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t), class: "gridline"}, g); const tx = svgEl("text", {x: m.l - 6, y: Y(t) + 3, "text-anchor": "end"}, g); tx.textContent = shortNum(t); });
  for (let i = 0; i <= 4; i++) { const v = x0 + (x1 - x0) * i / 4; const tx = svgEl("text", {x: X(v), y: H - 6, "text-anchor": i === 0 ? "start" : i === 4 ? "end" : "middle"}, g); const dt = new Date(v * 1000); tx.textContent = x1 - x0 < 2 * 86400 ? dt.toLocaleTimeString("fr-FR", {hour: "2-digit", minute: "2-digit"}) : dt.toLocaleDateString("fr-FR", {day: "2-digit", month: "2-digit"}); }
  if (opt.baseline != null && opt.baseline >= y0 && opt.baseline <= y1) svgEl("line", {x1: m.l, x2: W - m.r, y1: Y(opt.baseline), y2: Y(opt.baseline), stroke: "var(--muted)", "stroke-dasharray": "3 3"}, svg);
  const d = pts.map((p, i) => (i ? "L" : "M") + X(p.x).toFixed(1) + " " + Y(p.y).toFixed(1)).join(" ");
  svgEl("path", {d: d + ` L${X(x1)} ${H - m.b} L${X(x0)} ${H - m.b} Z`, fill: "var(--pos)", opacity: 0.08}, svg);
  svgEl("path", {d, fill: "none", stroke: "var(--pos)", "stroke-width": 2, "stroke-linejoin": "round"}, svg);
  const cross = svgEl("line", {y1: m.t, y2: H - m.b, stroke: "var(--muted)", visibility: "hidden"}, svg);
  const dot = svgEl("circle", {r: 4, fill: "var(--pos)", stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden"}, svg);
  const hit = svgEl("rect", {x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent"}, svg);
  hit.addEventListener("mousemove", (e) => {
    const r = svg.getBoundingClientRect(), mx = (e.clientX - r.left) * W / r.width;
    let best = pts[0]; for (const p of pts) if (Math.abs(X(p.x) - mx) < Math.abs(X(best.x) - mx)) best = p;
    cross.setAttribute("x1", X(best.x)); cross.setAttribute("x2", X(best.x)); cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", X(best.x)); dot.setAttribute("cy", Y(best.y)); dot.setAttribute("visibility", "visible");
    showTip(e, `<b>${money(best.y)}</b>${fmtTime(best.x)}${best.extra ? "<br>" + best.extra : ""}`);
  });
  hit.addEventListener("mouseleave", () => { hideTip(); cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); });
}

function barChart(el, items, opt) {
  el.innerHTML = "";
  opt = opt || {};
  if (!items.length || items.every((i) => !i.value && !i.count)) { el.innerHTML = `<div class="empty">Pas encore de donnees</div>`; return; }
  const W = el.clientWidth || 600, H = el.clientHeight || 240, m = {l: 54, r: 8, t: 10, b: 24};
  const svg = svgEl("svg", {viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opt.label || "barres"}, el);
  const vals = items.map((i) => i.value);
  const ticks = niceTicks(Math.min(0, ...vals), Math.max(0, ...vals), 4);
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  const Y = (v) => H - m.b - (v - y0) / (y1 - y0 || 1) * (H - m.t - m.b);
  const g = svgEl("g", {class: "axis"}, svg);
  ticks.forEach((t) => { svgEl("line", {x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t), class: "gridline"}, g); const tx = svgEl("text", {x: m.l - 6, y: Y(t) + 3, "text-anchor": "end"}, g); tx.textContent = shortNum(t); });
  const slot = (W - m.l - m.r) / items.length, bw = Math.max(2, Math.min(28, slot - 2));
  const every = Math.ceil(items.length / Math.max(1, Math.floor((W - m.l) / 46)));
  items.forEach((it, i) => {
    const cx = m.l + slot * i + slot / 2, top = Y(Math.max(0, it.value)), bot = Y(Math.min(0, it.value));
    const h = Math.max(1, bot - top);
    const r = Math.min(4, bw / 2, h);
    const up = it.value >= 0;
    // rounded data end, square at the baseline
    const x = cx - bw / 2, y = top;
    const path = up
      ? `M${x} ${y + h} V${y + r} Q${x} ${y} ${x + r} ${y} H${x + bw - r} Q${x + bw} ${y} ${x + bw} ${y + r} V${y + h} Z`
      : `M${x} ${y} V${y + h - r} Q${x} ${y + h} ${x + r} ${y + h} H${x + bw - r} Q${x + bw} ${y + h} ${x + bw} ${y + h - r} V${y} Z`;
    svgEl("path", {d: path, fill: it.value > 0 ? "var(--pos)" : it.value < 0 ? "var(--neg)" : "var(--zero)"}, svg);
    if (i % every === 0) { const tx = svgEl("text", {x: cx, y: H - 6, "text-anchor": "middle"}, g); tx.textContent = it.label; }
    const hit = svgEl("rect", {x: m.l + slot * i, y: m.t, width: slot, height: H - m.t - m.b, fill: "transparent"}, svg);
    hit.addEventListener("mousemove", (e) => showTip(e, `<b class="${cls(it.value)}">${smoney(it.value)}</b>${esc(it.tip || it.label)}`));
    hit.addEventListener("mouseleave", hideTip);
  });
  svgEl("line", {x1: m.l, x2: W - m.r, y1: Y(0), y2: Y(0), stroke: "var(--muted)"}, svg);
}

function hbars(el, rows) {
  if (!rows.length) { el.innerHTML = `<div class="empty">Pas encore de donnees</div>`; return; }
  const max = Math.max(...rows.map((r) => Math.abs(r.pnl)), 1);
  el.innerHTML = rows.map((r) => {
    const w = Math.abs(r.pnl) / max * 50;
    const left = r.pnl >= 0 ? 50 : 50 - w;
    return `<div class="hbar" title="${esc(r.key)}: ${r.trades} trades, ${r.win_rate} % gagnants"><span>${esc(r.key)} <span class="hint">(${r.trades})</span></span>
      <div class="track"><div class="b" style="left:${left}%;width:${w}%;background:${r.pnl >= 0 ? "var(--pos)" : "var(--neg)"}"></div>
      <div style="position:absolute;left:50%;top:-2px;bottom:-2px;border-left:1px solid var(--muted)"></div></div>
      <span class="val ${cls(r.pnl)}">${smoney(r.pnl)}</span></div>`;
  }).join("");
}

let calMonth = null;
function calendar(el, daily) {
  const byDay = {}; daily.forEach((d) => byDay[d.date] = d);
  if (!calMonth) { const last = daily.length ? daily[daily.length - 1].date : new Date().toISOString().slice(0, 10); calMonth = last.slice(0, 7); }
  const [y, mo] = calMonth.split("-").map(Number);
  const first = new Date(y, mo - 1, 1), days = new Date(y, mo, 0).getDate();
  const offset = (first.getDay() + 6) % 7;
  const max = Math.max(1, ...daily.map((d) => Math.abs(d.pnl)));
  let html = `<div class="calnav"><button class="btn small" data-cal="-1">&lsaquo;</button><b>${first.toLocaleDateString("fr-FR", {month: "long", year: "numeric"})}</b><button class="btn small" data-cal="1">&rsaquo;</button></div><div class="cal">`;
  html += ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"].map((d) => `<div class="h">${d}</div>`).join("");
  for (let i = 0; i < offset; i++) html += `<div class="d blank"></div>`;
  let monthPnl = 0;
  for (let d = 1; d <= days; d++) {
    const key = `${y}-${String(mo).padStart(2, "0")}-${String(d).padStart(2, "0")}`, r = byDay[key];
    let style = "";
    if (r) { monthPnl += r.pnl; const k = Math.round(15 + 55 * Math.abs(r.pnl) / max); style = `background:color-mix(in srgb, ${r.pnl >= 0 ? "var(--pos)" : "var(--neg)"} ${k}%, var(--surface-2))`; }
    html += `<div class="d" style="${style}" ${r ? `title="${r.trades} trades, ${r.win_rate} % gagnants"` : ""}>${d}${r ? `<div class="p">${smoney(r.pnl)}</div><div>${r.trades} tr.</div>` : ""}</div>`;
  }
  html += `</div><p class="hint">Total du mois: <b class="${cls(monthPnl)}">${smoney(monthPnl)}</b></p>`;
  el.innerHTML = html;
  $$("[data-cal]", el).forEach((b) => b.onclick = () => { const dt = new Date(y, mo - 1 + Number(b.dataset.cal), 1); calMonth = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}`; calendar(el, daily); });
}

// ---------------------------------------------------------------- KPIs
function kpi(label, value, sub, klass) { return `<div class="kpi"><div class="l">${esc(label)}</div><div class="v ${klass || ""}">${value}</div>${sub ? `<div class="s">${sub}</div>` : ""}</div>`; }

function meter(label, used, limit, leftText, invert) {
  if (limit == null) return "";
  const p = Math.max(0, Math.min(100, used / limit * 100));
  const c = invert ? (p >= 100 ? "good" : "") : (p > 80 ? "bad" : p > 50 ? "warn" : "");
  return `<div class="meter"><div class="row"><span>${esc(label)}</span><span>${leftText}</span></div><div class="bar"><div class="fill ${c}" style="width:${p}%"></div></div></div>`;
}

// ---------------------------------------------------------------- renders
function renderTop() {
  const b = $("#copyToggle");
  b.textContent = STATE.copying ? "COPIE ACTIVE" : "COPIE ARRETEE";
  b.className = "btn big " + (STATE.copying ? "on" : "off");
  b.title = STATE.copying ? "Cliquer pour arreter la copie" : "Cliquer pour demarrer la copie";
  const l = STATE.latency;
  $("#latency").textContent = "Latence: " + (l.avg != null ? nfq.format(l.avg) + " ms" : "-") + (l.p95 != null ? " (p95 " + nfq.format(l.p95) + ")" : "");
}

function renderDash() {
  const fol = STATE.accounts.filter((a) => a.role === "follower");
  const sum = (arr, f) => arr.reduce((s, a) => s + (f(a) || 0), 0);
  const bal = sum(fol, (a) => a.info && a.info.balance), eq = sum(fol, (a) => a.info && a.info.equity);
  const day = sum(fol, (a) => a.risk && a.risk.daily_pnl);
  const open = sum(STATE.accounts, (a) => a.positions.length);
  const locked = fol.filter((a) => a.risk && a.risk.lock).length;
  const conn = STATE.accounts.filter((a) => a.connected).length;
  $("#dashKpis").innerHTML =
    kpi("Solde copieurs", money(bal), fol.length + " compte(s)") +
    kpi("Equity copieurs", money(eq), "P&L latent " + smoney(eq - bal), cls(eq - bal)) +
    kpi("P&L du jour (copieurs)", smoney(day), "depuis l'ouverture de session", cls(day)) +
    kpi("Positions ouvertes", nf0.format(open), "tous comptes") +
    kpi("Connectes", conn + " / " + STATE.accounts.length, STATE.accounts.length - conn ? "verifier le journal" : "tout va bien") +
    kpi("Comptes bloques", nf0.format(locked), locked ? "regle de risque atteinte" : "aucun", locked ? "neg" : "") +
    (DASH_STATS ? kpi("Win rate 30 j", pct(DASH_STATS.summary.win_rate), DASH_STATS.summary.trades + " trades") +
      kpi("Profit factor 30 j", DASH_STATS.summary.profit_factor == null ? "-" : nfq.format(DASH_STATS.summary.profit_factor), "P&L net " + smoney(DASH_STATS.summary.net_pnl)) : "");

  $("#acctCards").innerHTML = STATE.accounts.length ? STATE.accounts.map((a) => {
    const r = a.risk || {}, i = a.info, rs = (a.settings && a.settings.risk) || {};
    const dll = Number(rs.daily_loss_limit) || 0, dd = Number(rs.max_drawdown) || 0, tgt = Number(rs.profit_target) || 0;
    const status = a.connected ? (a.error ? `<span class="badge warn">${esc("Erreur")}</span>` : `<span class="badge ok">Connecte</span>`) : `<span class="badge bad">Deconnecte</span>`;
    const copyCfg = a.role === "follower" ? (META.edition === "futures"
      ? `x${nfq.format(a.settings.multiplier || 1)}${a.settings.invert ? " inverse" : ""}`
      : `${esc(a.settings.sizing || "multiplier")} ${a.settings.sizing === "fixed" ? a.settings.fixed_lot + " lot" : "x" + nfq.format(a.settings.multiplier || 1)}`) : "";
    return `<div class="card ${r.lock ? "locked" : ""}">
      <div class="hd"><div><div class="nm">${esc(a.name)}</div><div class="sub">${a.role === "leader" ? "Maitre" : "Copieur de " + esc(acctName(a.leader_id))} | ${esc(a.connector)} ${copyCfg ? "| " + copyCfg : ""}</div></div>
      <div>${a.role === "leader" ? '<span class="badge leader">MAITRE</span> ' : ""}${status}</div></div>
      <div class="nums">
        <div><div class="l">Solde</div><div class="v">${i ? money(i.balance) : "-"}</div></div>
        <div><div class="l">Equity</div><div class="v">${i ? money(i.equity) : "-"}</div></div>
        <div><div class="l">P&amp;L jour</div><div class="v ${cls(r.daily_pnl)}">${smoney(r.daily_pnl)}</div></div>
      </div>
      ${dll ? meter("Perte journaliere", Math.max(0, -(r.daily_pnl || 0)), dll, "reste " + money(r.dll_left)) : ""}
      ${dd && r.floor != null ? meter("Drawdown (plancher " + money(r.floor) + ")", Math.max(0, dd - (r.dd_left || 0)), dd, "reste " + money(r.dd_left)) : ""}
      ${tgt ? meter("Objectif de profit", Math.max(0, r.profit || 0), tgt, pct(r.target_pct), true) : ""}
      <div class="sub">${a.positions.length ? a.positions.map((p) => `${esc(p.symbol)} <b class="${cls(p.qty)}">${p.qty > 0 ? "+" : ""}${nfq.format(p.qty)}</b> <span class="${cls(p.unrealized)}">${smoney(p.unrealized)}</span>`).join(" | ") : "Aucune position"}</div>
      ${r.lock ? `<div class="lockmsg">Bloque: ${esc(r.lock_reason || r.lock)}</div>` : ""}
      ${a.error ? `<div class="lockmsg">${esc(a.error)}</div>` : ""}
      <div class="acts">
        <button class="btn small" data-act="enable" data-id="${a.id}" data-on="${a.enabled ? 0 : 1}">${a.enabled ? "Desactiver" : "Activer"}</button>
        <button class="btn small danger" data-act="flatten" data-id="${a.id}">Aplatir</button>
        ${r.lock ? `<button class="btn small" data-act="unlock" data-id="${a.id}">Debloquer</button>` : ""}
        <button class="btn small ghost" data-act="edit" data-id="${a.id}">Modifier</button>
      </div></div>`;
  }).join("") : `<div class="empty">Aucun compte. Ajoutez un compte maitre puis vos comptes copieurs dans l'onglet Comptes (ou lancez avec --demo).</div>`;

  $("#dashExecs").innerHTML = execTable(STATE.executions.slice(0, 10));
}

function execTable(rows) {
  return table([
    {h: "Heure", f: (r) => fmtTime(r.ts)},
    {h: "Maitre", f: (r) => esc(acctName(r.leader_id))},
    {h: "Copieur", f: (r) => esc(acctName(r.follower_id))},
    {h: "Symbole", f: (r) => esc(r.symbol)},
    {h: "Action", f: (r) => esc(r.action)},
    {h: "Qte", n: 1, f: (r) => nfq.format(r.qty)},
    {h: "Statut", f: (r) => esc(r.status), cls: (r) => "st-" + r.status},
    {h: "Latence", n: 1, f: (r) => r.latency_ms ? nfq.format(r.latency_ms) + " ms" : "-"},
    {h: "Detail", f: (r) => esc(r.message)},
  ], rows, "Aucune copie pour l'instant");
}

function renderAccounts() {
  $("#acctTable").innerHTML = table([
    {h: "Nom", f: (a) => `<b>${esc(a.name)}</b>`},
    {h: "Role", f: (a) => a.role === "leader" ? '<span class="badge leader">Maitre</span>' : "Copieur"},
    {h: "Maitre", f: (a) => a.role === "follower" ? esc(acctName(a.leader_id)) : "-"},
    {h: "Connecteur", f: (a) => esc(a.connector)},
    {h: "Etat", f: (a) => `<span class="dot ${a.connected ? "ok" : "bad"}"></span>${a.connected ? "connecte" : "deconnecte"}`},
    {h: "Copie", f: (a) => a.enabled ? "active" : "desactivee"},
    {h: "Solde", n: 1, f: (a) => a.info ? money(a.info.balance) : "-"},
    {h: "Equity", n: 1, f: (a) => a.info ? money(a.info.equity) : "-"},
    {h: "Risque", f: (a) => a.risk && a.risk.lock ? `<span class="neg">${esc(a.risk.lock)}</span>` : "ok"},
    {h: "", f: (a) => `<button class="btn small" data-act="edit" data-id="${a.id}">Modifier</button>
      <button class="btn small" data-act="reconnect" data-id="${a.id}">Reconnecter</button>
      <button class="btn small" data-act="reset_risk" data-id="${a.id}" title="Repart des soldes actuels">Reset risque</button>
      <button class="btn small danger" data-act="delete" data-id="${a.id}">Supprimer</button>`},
  ], STATE.accounts, "Aucun compte configure");
}

function renderPositions() {
  const rows = [];
  STATE.accounts.forEach((a) => a.positions.forEach((p) => rows.push(Object.assign({acct: a.name, role: a.role}, p))));
  $("#posTable").innerHTML = table([
    {h: "Compte", f: (r) => esc(r.acct) + (r.role === "leader" ? ' <span class="badge leader">M</span>' : "")},
    {h: "Symbole", f: (r) => esc(r.symbol)},
    {h: "Sens", f: (r) => r.qty > 0 ? "Long" : "Short", cls: (r) => cls(r.qty)},
    {h: "Qte (" + META.unit + ")", n: 1, f: (r) => nfq.format(Math.abs(r.qty))},
    {h: "Prix moyen", n: 1, f: (r) => r.avg_price},
    {h: "SL", n: 1, f: (r) => r.sl || "-"},
    {h: "TP", n: 1, f: (r) => r.tp || "-"},
    {h: "Ticket", f: (r) => esc(r.ticket || "-")},
    {h: "P&L latent", n: 1, f: (r) => smoney(r.unrealized), cls: (r) => cls(r.unrealized)},
  ], rows, "Aucune position ouverte");
}

function fillAccountSelects() {
  for (const sel of [$("#fAccount"), $("#tAccount")]) {
    const v = sel.value;
    sel.innerHTML = `<option value="followers">Tous les copieurs</option><option value="leaders">Comptes maitres</option><option value="all">Tous les comptes</option>` +
      STATE.accounts.map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join("");
    if (v && $$("option", sel).some((o) => o.value === v)) sel.value = v;
  }
}
function statQuery(v, days) {
  return ["followers", "leaders", "all"].includes(v) ? `account=all&role=${v}&days=${days}` : `account=${v}&days=${days}`;
}

let DASH_STATS = null;
async function loadDashStats() {
  try {
    DASH_STATS = await api("/api/stats?account=all&role=followers&days=30");
    lineChart($("#dashEquity"), DASH_STATS.equity.map((p) => ({x: p.t, y: p.equity, extra: p.symbol ? `${esc(p.symbol)} <span class="${cls(p.pnl)}">${smoney(p.pnl)}</span>` : ""})), {baseline: DASH_STATS.start_balance, label: "Equity des copieurs"});
    barChart($("#dashDaily"), DASH_STATS.daily.map((d) => ({label: d.date.slice(5), value: d.pnl, tip: `${d.date}: ${d.trades} trades`})), {label: "P&L journalier"});
  } catch (e) { /* first start */ }
}

async function renderStats() {
  const q = statQuery($("#fAccount").value || "followers", $("#fDays").value);
  const s = await api("/api/stats?" + q), m = s.summary;
  $("#statKpis").innerHTML =
    kpi("P&L net", smoney(m.net_pnl), "frais " + money(m.fees), cls(m.net_pnl)) +
    kpi("Trades", nf0.format(m.trades), `${m.wins} G / ${m.losses} P / ${m.breakeven} BE`) +
    kpi("Win rate", pct(m.win_rate)) +
    kpi("Profit factor", m.profit_factor == null ? "-" : nfq.format(m.profit_factor)) +
    kpi("Esperance / trade", smoney(m.expectancy), "", cls(m.expectancy)) +
    kpi("Gain moyen / perte moy.", money(m.avg_win) + " / " + money(m.avg_loss), "ratio " + (m.payoff == null ? "-" : nfq.format(m.payoff))) +
    kpi("Drawdown max", money(m.max_drawdown), pct(m.max_drawdown_pct), m.max_drawdown ? "neg" : "") +
    kpi("Jours gagnants", pct(m.green_days_pct), m.green_days + " / " + m.trading_days + " jours") +
    kpi("Meilleur / pire jour", smoney(m.best_day) + " / " + smoney(m.worst_day)) +
    kpi("Consistance", m.consistency_pct == null ? "-" : pct(m.consistency_pct), "meilleur jour / profits totaux") +
    kpi("Sharpe (jour)", nfq.format(m.sharpe), "Sortino " + nfq.format(m.sortino));
  lineChart($("#stEquity"), s.equity.map((p) => ({x: p.t, y: p.equity, extra: p.symbol ? `${esc(p.symbol)} <span class="${cls(p.pnl)}">${smoney(p.pnl)}</span>` : ""})), {baseline: s.start_balance, label: "Equity"});
  barChart($("#stDaily"), s.daily.map((d) => ({label: d.date.slice(5), value: d.pnl, tip: `${d.date}: ${d.trades} trades, ${d.win_rate} % gagnants`})));
  calMonth = null; calendar($("#stCalendar"), s.daily);
  hbars($("#stSides"), [Object.assign({key: "Long"}, s.long), Object.assign({key: "Short"}, s.short)]);
  hbars($("#stSymbol"), s.by_symbol);
  barChart($("#stHour"), s.by_hour.map((h) => ({label: h.key, value: h.pnl, count: h.trades, tip: `${h.key}: ${h.trades} trades, ${h.win_rate} % gagnants`})));
  barChart($("#stWeekday"), s.by_weekday.map((h) => ({label: h.key, value: h.pnl, count: h.trades, tip: `${h.key}: ${h.trades} trades, ${h.win_rate} % gagnants`})));
  const rows = [
    ["P&L net", smoney(m.net_pnl)], ["Profits bruts", money(m.gross_profit)], ["Pertes brutes", money(m.gross_loss)], ["Frais", money(m.fees)],
    ["Nombre de trades", m.trades], ["Volume (" + META.unit + ")", nfq.format(m.volume)], ["Win rate", pct(m.win_rate)], ["Profit factor", m.profit_factor == null ? "-" : m.profit_factor],
    ["Esperance", smoney(m.expectancy)], ["Gain moyen", money(m.avg_win)], ["Perte moyenne", money(m.avg_loss)], ["Ratio gain/perte", m.payoff == null ? "-" : m.payoff],
    ["Plus gros gain", smoney(m.largest_win)], ["Plus grosse perte", smoney(m.largest_loss)], ["Gains consecutifs max", m.max_consec_wins], ["Pertes consecutives max", m.max_consec_losses],
    ["Duree moyenne", fmtDur(m.avg_duration_s)], ["Drawdown max", money(m.max_drawdown) + " (" + pct(m.max_drawdown_pct) + ")"], ["Facteur de recuperation", m.recovery_factor == null ? "-" : m.recovery_factor],
    ["Sharpe annualise", m.sharpe], ["Sortino annualise", m.sortino], ["Jours de trading", m.trading_days], ["Jours gagnants", m.green_days + " (" + pct(m.green_days_pct) + ")"],
    ["P&L moyen par jour", smoney(m.avg_day)], ["Meilleur jour", smoney(m.best_day)], ["Pire jour", smoney(m.worst_day)], ["Consistance (meilleur jour / total)", m.consistency_pct == null ? "-" : pct(m.consistency_pct)],
    ["Long", `${s.long.trades} trades, ${pct(s.long.win_rate)}, ${smoney(s.long.pnl)}`], ["Short", `${s.short.trades} trades, ${pct(s.short.win_rate)}, ${smoney(s.short.pnl)}`],
  ];
  $("#stTable").innerHTML = table([{h: "Mesure", f: (r) => esc(r[0])}, {h: "Valeur", n: 1, f: (r) => esc(r[1])}], rows);
}

async function renderTrades() {
  const v = $("#tAccount").value || "followers";
  const q = statQuery(v, 0);
  $("#csvLink").href = "/api/trades.csv?" + q;
  let rows = await api("/api/trades?" + q + "&limit=1000");
  if (["followers", "leaders"].includes(v)) {
    const ids = new Set(STATE.accounts.filter((a) => a.role === (v === "leaders" ? "leader" : "follower")).map((a) => a.id));
    rows = rows.filter((r) => ids.has(r.account_id));
  }
  $("#tradeTable").innerHTML = table([
    {h: "Cloture", f: (r) => fmtTime(r.close_time)},
    {h: "Compte", f: (r) => esc(acctName(r.account_id))},
    {h: "Symbole", f: (r) => esc(r.symbol)},
    {h: "Sens", f: (r) => r.side === "long" ? "Long" : "Short"},
    {h: "Qte", n: 1, f: (r) => nfq.format(r.qty)},
    {h: "Entree", n: 1, f: (r) => r.entry_price},
    {h: "Sortie", n: 1, f: (r) => r.exit_price},
    {h: "Duree", n: 1, f: (r) => fmtDur(r.close_time - r.open_time)},
    {h: "Frais", n: 1, f: (r) => money(r.fees)},
    {h: "P&L", n: 1, f: (r) => smoney(r.pnl), cls: (r) => cls(r.pnl)},
  ], rows, "Aucun trade cloture");
}

async function renderExecs() { $("#execTable").innerHTML = execTable(await api("/api/executions?limit=300")); }
async function renderJournal() {
  const rows = await api("/api/events?limit=400");
  $("#eventTable").innerHTML = table([
    {h: "Heure", f: (r) => fmtTime(r.ts)},
    {h: "Niveau", f: (r) => esc(r.level), cls: (r) => "lv-" + r.level},
    {h: "Compte", f: (r) => r.account_id ? esc(acctName(r.account_id)) : "-"},
    {h: "Message", f: (r) => esc(r.message)},
  ], rows, "Journal vide");
}
function renderSettings() {
  $("#syncOnStart").checked = !!STATE.sync_on_start;
  $("#aboutPanel").innerHTML = `<h3>${esc(META.title)} v${esc(META.version)}</h3>
    <p class="hint">Serveur local uniquement (127.0.0.1). Les identifiants sont stockes dans le fichier SQLite du dossier data/ de votre machine: protegez ce dossier.
    Logiciel fourni tel quel, sans garantie. Ce n'est pas un conseil financier. Verifiez que votre prop firm autorise la copie de trades entre vos comptes.</p>`;
}

// ---------------------------------------------------------------- account form
function fieldHtml(f, value, prefix) {
  const name = (prefix || "") + f.key, v = value == null ? "" : value;
  if (f.type === "bool") return `<label class="check"><input type="checkbox" name="${name}" ${v === true || v === "true" ? "checked" : ""}> ${esc(f.label)}</label>`;
  if (f.type === "select") return `<label>${esc(f.label)}<select name="${name}">${f.options.map((o) => `<option ${o === v ? "selected" : ""}>${esc(o)}</option>`).join("")}</select></label>`;
  const t = f.type === "password" ? "password" : f.type === "number" ? "number" : "text";
  return `<label>${esc(f.label)}<input type="${t}" ${t === "number" ? 'step="any"' : ""} name="${name}" value="${esc(v)}" placeholder="${esc(f.placeholder || "")}" autocomplete="off"></label>`;
}
const mapToText = (m) => typeof m === "string" ? m : Object.entries(m || {}).map(([k, v]) => k + "=" + v).join(", ");

function copyFields(role, s) {
  const fut = META.edition === "futures";
  if (role === "leader") return fut ? `<p class="hint">Le compte maitre est la source: aucun reglage de copie.</p>` :
    fieldHtml({key: "symbol_suffix", label: "Suffixe des symboles du maitre (ex .r)", type: "text"}, s.symbol_suffix, "s.");
  if (fut) return [
    fieldHtml({key: "multiplier", label: "Multiplicateur (contrats copieur / maitre)", type: "number"}, s.multiplier, "s."),
    fieldHtml({key: "max_qty", label: "Contrats max (0 = illimite)", type: "number"}, s.max_qty, "s."),
    fieldHtml({key: "symbol_map", label: "Mapping de racines (NQ=MNQ, ES=MES)", type: "text"}, mapToText(s.symbol_map), "s."),
    fieldHtml({key: "invert", label: "Inverser le sens (hedge)", type: "bool"}, s.invert, "s."),
  ].join("");
  return [
    `<label>Calcul des lots<select name="s.sizing">${[["multiplier", "Multiplicateur"], ["equity", "Proportionnel a l'equity"], ["fixed", "Lot fixe"]].map(([v, l]) => `<option value="${v}" ${s.sizing === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>`,
    fieldHtml({key: "multiplier", label: "Multiplicateur", type: "number"}, s.multiplier, "s."),
    fieldHtml({key: "fixed_lot", label: "Lot fixe", type: "number"}, s.fixed_lot, "s."),
    fieldHtml({key: "min_lot", label: "Lot minimum", type: "number"}, s.min_lot, "s."),
    fieldHtml({key: "lot_step", label: "Pas de lot", type: "number"}, s.lot_step, "s."),
    fieldHtml({key: "max_lot", label: "Lot max par ordre (0 = off)", type: "number"}, s.max_lot, "s."),
    fieldHtml({key: "symbol_suffix", label: "Suffixe symboles du copieur (ex .r)", type: "text"}, s.symbol_suffix, "s."),
    fieldHtml({key: "symbol_map", label: "Mapping de symboles (US30=DJ30, NAS100=USTEC)", type: "text"}, mapToText(s.symbol_map), "s."),
    fieldHtml({key: "copy_sltp", label: "Copier SL / TP", type: "bool"}, s.copy_sltp !== false, "s."),
    fieldHtml({key: "invert", label: "Inverser le sens (hedge)", type: "bool"}, s.invert, "s."),
  ].join("");
}

function openForm(acct) {
  editing = acct ? acct.id : null;
  const f = $("#acctForm"); f.reset(); $("#formError").textContent = "";
  $("#dlgTitle").textContent = acct ? "Modifier " + acct.name : "Ajouter un compte";
  const s = Object.assign({}, META.defaults, acct ? acct.settings : {});
  f.name.value = acct ? acct.name : "";
  f.role.value = acct ? acct.role : (STATE.accounts.some((a) => a.role === "leader") ? "follower" : "leader");
  f.enabled.checked = acct ? acct.enabled : true;
  f.connector.innerHTML = META.connectors.map((c) => `<option value="${c.id}">${esc(c.label)}</option>`).join("");
  f.connector.value = acct ? acct.connector : META.connectors[0].id;
  const leaders = STATE.accounts.filter((a) => a.role === "leader" && (!acct || a.id !== acct.id));
  f.leader_id.innerHTML = leaders.map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join("") || `<option value="">(creez d'abord un maitre)</option>`;
  if (acct && acct.leader_id) f.leader_id.value = acct.leader_id;
  const params = acct ? acct.params : {};
  const drawConn = () => { const c = META.connectors.find((x) => x.id === f.connector.value); $("#connFields").innerHTML = c.fields.map((fl) => fieldHtml(fl, params[fl.key], "p.")).join(""); };
  const drawCopy = () => { $("#copyFields").innerHTML = copyFields(f.role.value, s); $$(".only-follower").forEach((e) => e.style.display = f.role.value === "follower" ? "" : "none"); };
  f.connector.onchange = drawConn; f.role.onchange = drawCopy; drawConn(); drawCopy();
  $("#presetSel").innerHTML = Object.entries(META.presets).map(([k, p]) => `<option value="${k}">${esc(p.label)}</option>`).join("");
  const r = Object.assign({}, META.presets.custom, s.risk || {});
  const setRisk = (r) => { for (const k in r) { const el = f.elements["r." + k]; if (!el) continue; if (el.type === "checkbox") el.checked = !!r[k]; else el.value = r[k] == null ? "" : r[k]; } };
  setRisk(Object.assign({stop_on_target: false, safety_buffer: 0, flatten_time: ""}, r));
  $("#presetSel").value = "custom";
  $("#presetSel").onchange = () => { const p = META.presets[$("#presetSel").value]; setRisk(Object.assign({}, p)); };
  $("#acctDialog").showModal();
}

function readForm() {
  const f = $("#acctForm"), out = {name: f.name.value, role: f.role.value, connector: f.connector.value, enabled: f.enabled.checked, params: {}, settings: {}};
  if (out.role === "follower") out.leader_id = f.leader_id.value; else out.leader_id = null;
  const risk = {};
  for (const el of f.elements) {
    if (!el.name) continue;
    const v = el.type === "checkbox" ? el.checked : el.type === "number" ? (el.value === "" ? 0 : Number(el.value)) : el.value;
    if (el.name.startsWith("p.")) out.params[el.name.slice(2)] = v;
    else if (el.name.startsWith("s.")) out.settings[el.name.slice(2)] = v;
    else if (el.name.startsWith("r.")) risk[el.name.slice(2)] = v;
  }
  out.settings.risk = risk;
  return out;
}

// ---------------------------------------------------------------- wiring
async function refresh() {
  try { STATE = await api("/api/state"); } catch (e) { $("#latency").textContent = "Serveur injoignable"; return; }
  renderTop();
  if (VIEW === "dash") renderDash();
  if (VIEW === "accounts") renderAccounts();
  if (VIEW === "positions") renderPositions();
}

function show(view) {
  VIEW = view;
  $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + view));
  if (!STATE) return;
  fillAccountSelects();
  ({dash: () => { renderDash(); loadDashStats(); }, accounts: renderAccounts, positions: renderPositions, stats: renderStats,
    trades: renderTrades, execs: renderExecs, journal: renderJournal, settings: renderSettings}[view])();
}

document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-act]");
  if (!b) return;
  const id = Number(b.dataset.id), act = b.dataset.act;
  try {
    if (act === "edit") return openForm(STATE.accounts.find((a) => a.id === id));
    if (act === "delete" && !confirm("Supprimer ce compte ? Ses trades restent dans l'historique.")) return;
    if (act === "flatten" && !confirm("Fermer toutes les positions de ce compte ?")) return;
    await api(`/api/accounts/${id}/${act}`, act === "enable" ? {enabled: b.dataset.on === "1"} : {});
    toast("OK"); refresh();
    if (VIEW === "accounts") setTimeout(() => show("accounts"), 300);
  } catch (err) { toast(err.message); }
});

$("#tabs").addEventListener("click", (e) => { const b = e.target.closest("button[data-view]"); if (b) show(b.dataset.view); });
$("#copyToggle").onclick = async () => { await api("/api/engine", {copying: !STATE.copying}); refresh(); };
$("#flattenAll").onclick = async () => {
  if (!confirm("Fermer toutes les positions de TOUS les comptes copieurs ?")) return;
  const lead = confirm("Fermer aussi les comptes maitres ?");
  await api("/api/flatten_all", {include_leaders: lead}); toast("Aplatissement envoye");
};
$("#addAcct").onclick = () => openForm(null);
$("#dlgCancel").onclick = () => $("#acctDialog").close();
$("#acctForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const body = readForm();
    await api(editing ? `/api/accounts/${editing}` : "/api/accounts", body);
    $("#acctDialog").close(); toast("Compte enregistre"); await refresh(); show(VIEW);
  } catch (err) { $("#formError").textContent = err.message; }
});
$("#fRefresh").onclick = renderStats; $("#fAccount").onchange = renderStats; $("#fDays").onchange = renderStats;
$("#tAccount").onchange = renderTrades;
$("#syncOnStart").onchange = (e) => api("/api/engine", {sync_on_start: e.target.checked});
$("#themeToggle").onclick = () => {
  const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("ct-theme", next); } catch (_) {}
  show(VIEW);
};
try { const t = localStorage.getItem("ct-theme"); if (t) document.documentElement.dataset.theme = t; } catch (_) {}
let resizeT; addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => { if (VIEW === "stats") renderStats(); if (VIEW === "dash") loadDashStats(); }, 250); });

(async function init() {
  META = await api("/api/meta");
  document.title = META.title;
  $("#title").textContent = META.title;
  $("#edition").textContent = META.edition === "futures" ? "Prop firms futures" : "CFD / Forex";
  await refresh();
  show("dash");
  setInterval(refresh, 1000);
  setInterval(() => { if (VIEW === "dash") loadDashStats(); }, 15000);
})();
