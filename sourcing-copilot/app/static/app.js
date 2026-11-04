/* Sourcing Copilot — mobile-first PWA (no build step). */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const view = $("#view");
const S = {
  currency: localGet("currency", "TMT"),
  rules: { yuan_to_tmt: 3.1, usd_cny_rate: 7.1 },
  explain: {},  // id -> breakdown payload for score sheets
};

function localGet(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } }
function localSet(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } }
function esc(v) { return String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function cap(s) { s = String(s || ""); return s.charAt(0).toUpperCase() + s.slice(1); }
function n0(v) { return v == null || isNaN(v) ? "—" : Math.round(v).toLocaleString("en-GB"); }
function fmtDate(s) { if (!s) return "—"; const d = new Date(s); return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" }); }
function money(tmt, opts = {}) {
  if (tmt == null || isNaN(tmt)) return "—";
  const c = opts.currency || S.currency;
  let v = tmt, sym = " TMT", dec = 0;
  if (c === "CNY") { v = tmt / S.rules.yuan_to_tmt; sym = ""; dec = 1; return "¥" + v.toFixed(dec); }
  if (c === "USD") { v = tmt / S.rules.yuan_to_tmt / (S.rules.usd_cny_rate || 7.1); return "$" + v.toFixed(2); }
  return Math.round(v).toLocaleString("en-GB") + sym;
}
function cny(v) { return v == null ? "—" : "¥" + Number(v).toFixed(2); }
function colour(s) { return s >= 70 ? "green" : s >= 45 ? "amber" : "red"; }
const STATE = { rising: ["↗ Rising", "green"], peaking: ["→ Peaking", "amber"], fading: ["↘ Fading", "red"], unknown: ["· New", ""] };
function stateChip(st) { const [t, c] = STATE[st] || STATE.unknown; return `<span class="chip ${c}">${t}</span>`; }

async function api(path, opts = {}) {
  const o = { headers: {}, ...opts };
  if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers["Content-Type"] = "application/json"; o.method = o.method || "POST"; }
  const r = await fetch(path, o);
  if (r.status === 401 && !path.startsWith("/api/auth")) { renderLogin(); throw new Error("login required"); }
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) { const e = new Error((data && data.detail) || r.statusText); e.data = data; e.status = r.status; throw e; }
  return data;
}
function toast(msg, ms = 2600) {
  const t = document.createElement("div"); t.className = "toast"; t.textContent = msg; document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}
function sheet(html, onMount) {
  const bg = document.createElement("div"); bg.className = "sheet-bg";
  bg.innerHTML = `<div class="sheet" role="dialog"><div class="grab"></div>${html}</div>`;
  bg.addEventListener("click", e => { if (e.target === bg) bg.remove(); });
  document.body.appendChild(bg);
  const close = () => bg.remove();
  $$("[data-close]", bg).forEach(b => b.onclick = close);
  onMount && onMount(bg.firstElementChild, close);
  return close;
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied ✓"); }
  catch { sheet(`<h3>Copy this</h3><textarea style="min-height:260px">${esc(text)}</textarea>`); }
}
function setTitle(t, back) {
  $("#title").textContent = t; document.title = t + " · Sourcing Copilot";
  $("#back").hidden = !back;
}
function setTab(tab) { $$(".nav a").forEach(a => a.classList.toggle("on", a.dataset.tab === tab)); }
async function refreshBadge() {
  try { const b = await api("/api/basket"); const n = b.totals.units; const el = $("#basket-badge"); el.hidden = !n; el.textContent = n; } catch { }
}

/* ---------- score explain sheets ---------- */
function scoreBtn(score, label, key, extraClass = "") {
  const c = colour(score);
  return `<button class="score ${c} ${extraClass}" data-explain="${esc(key)}" aria-label="${esc(label)} ${score}, tap for details">${n0(score)}<small>${esc(label)}</small></button>`;
}
function showBreakdown(key) {
  const b = S.explain[key]; if (!b) return;
  const comps = (b.components || []).map(c => {
    const pct = c.max ? Math.max(0, Math.min(100, Math.abs(c.points) / c.max * 100)) : 0;
    const links = (c.links || []).filter(l => l.url).map(l =>
      `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.platform || l.source)} · ${l.views ? n0(l.views) + " views · " : ""}${esc(l.title || l.url)}</a>`).join("");
    return `<div class="comp"><div class="row"><b>${esc(c.label)}</b><span class="spacer"></span><b>${c.points > 0 ? "+" : ""}${c.points}</b><small>/ ${c.max}</small></div>
      <div class="bar ${c.points < 0 ? "neg" : ""}"><i style="width:${pct}%"></i></div><small>${esc(c.reason)}</small>${links ? `<div class="links">${links}</div>` : ""}</div>`;
  }).join("");
  sheet(`<div class="row"><h3 style="margin:0">${esc(b.title)}</h3><span class="spacer"></span>${`<span class="score ${colour(b.score)}">${n0(b.score)}</span>`}</div>
    ${b.subtitle ? `<p class="muted">${esc(b.subtitle)}</p>` : ""}
    ${b.cap_reason ? `<div class="note">${esc(b.cap_reason)}</div>` : ""}
    ${b.state_reason ? `<p><b>${(STATE[b.state] || STATE.unknown)[0]}</b> — <small>${esc(b.state_reason)}</small></p>` : ""}
    <div>${comps}</div><button class="btn block" data-close style="margin-top:12px">Close</button>`);
}
document.addEventListener("click", e => {
  const b = e.target.closest("[data-explain]"); if (b) { e.preventDefault(); e.stopPropagation(); showBreakdown(b.dataset.explain); }
});

/* ---------- routing ---------- */
const routes = [
  [/^\/?$/, viewHome, "home"], [/^\/trends$/, viewTrends, "trends"], [/^\/product\/(\d+)$/, viewProduct, "trends"],
  [/^\/offer\/new\/(\d+)$/, viewOfferForm, "trends"], [/^\/offer\/(\d+)\/edit$/, viewOfferEdit, "trends"],
  [/^\/order$/, viewOrder, "order"], [/^\/sales$/, viewSales, "sales"], [/^\/more$/, viewMore, "more"],
  [/^\/shipments$/, viewShipments, "more"], [/^\/sellers$/, viewSellers, "more"], [/^\/seller\/(\d+)$/, viewSeller, "more"],
  [/^\/report$/, viewReport, "more"], [/^\/import$/, viewImport, "more"], [/^\/settings$/, viewSettings, "more"],
  [/^\/setup$/, viewSetup, "more"], [/^\/filter$/, viewFilter, "more"], [/^\/calc$/, viewCalc, "more"],
];
async function router() {
  const hash = location.hash.replace(/^#/, "") || "/";
  for (const [re, fn, tab] of routes) {
    const m = hash.match(re);
    if (m) {
      setTab(tab); window.scrollTo(0, 0);
      try { await fn(...m.slice(1)); }
      catch (e) { if (e.message !== "login required") view.innerHTML = `<div class="empty"><div class="big">⚠️</div><p>${esc(e.message)}</p><button class="btn" onclick="router()">Retry</button></div>`; }
      return;
    }
  }
  location.hash = "#/";
}
window.addEventListener("hashchange", router);
$("#back").onclick = () => history.length > 1 ? history.back() : (location.hash = "#/");

/* ---------- currency & theme ---------- */
function paintCurrency() { $$("#currency button").forEach(b => b.classList.toggle("on", b.dataset.c === S.currency)); }
$$("#currency button").forEach(b => b.onclick = () => { S.currency = b.dataset.c; localSet("currency", S.currency); paintCurrency(); router(); });
function applyTheme() { const t = localGet("theme", ""); if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; }
$("#theme").onclick = () => {
  const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  localSet("theme", cur === "dark" ? "light" : "dark"); applyTheme();
};

/* ---------- product card ---------- */
function img(c) { return c.image_url ? esc(c.image_url) : `/api/placeholder/${c.product_id}.svg`; }
function recCard(c, i) {
  const key = "trend-" + c.product_id;
  S.explain[key] = { title: `Trend Score · ${cap(c.name)}`, score: c.trend_score, ...(c.trend_breakdown || {}) };
  const nums = c.landing_tmt != null ? `
    <div class="nums"><div class="num"><span>Landing</span><b>${money(c.landing_tmt)}</b></div>
    <div class="num"><span>Sell for</span><b>${money(c.sell_tmt)}</b></div>
    <div class="num good"><span>Profit</span><b>${money(c.profit_tmt)}</b></div></div>` :
    `<div class="note" style="margin-top:10px">No offer yet: find one on Taobao / 1688.</div>`;
  return `<article class="card" data-pid="${c.product_id}">
    <div class="pcard"><img class="thumb" src="${img(c)}" alt="" loading="lazy">
      <div><div class="row" style="align-items:flex-start"><div style="flex:1;min-width:0"><span class="rank">#${i + 1}</span>
        <div class="pname"><a href="#/product/${c.product_id}" style="color:inherit">${esc(c.name)}</a></div>
        <div class="meta">${stateChip(c.trend_state)}${c.seller_score != null ? `<span class="chip ${colour(c.seller_score)}">Seller ${c.seller_score}</span>` : ""}
          <span class="chip">🚚 ${esc(c.delivery?.label || "")}</span>${c.stock ? `<span class="chip blue">${c.stock} in stock</span>` : ""}</div></div>
        ${scoreBtn(c.trend_score, "trend", key)}</div></div></div>
    ${nums}
    ${c.weight_is_estimate && c.landing_tmt != null ? `<small>⚖️ weight estimated</small>` : ""}
    ${c.counterfeit_risk ? `<div class="warn">⚠️ ${esc(c.counterfeit_reason || "Branded/licensed: counterfeit risk, customs can seize.")}</div>` : ""}
    ${c.local_check && ["above_market", "above_median"].includes(c.local_check.status) ? `<div class="note" style="margin-top:8px">🏪 ${esc(c.local_check.message)}</div>` : ""}
    ${c.explanation ? `<p class="explain">💡 ${esc(c.explanation)}</p>` : ""}
    <div class="actions">
      <button class="btn primary small" data-act="add" ${c.best_offer ? "" : "disabled"}>＋ Add to order</button>
      <a class="btn small" href="#/product/${c.product_id}">🔎 Find offers</a>
      <button class="btn small" data-act="watch">${c.watched ? "★ Watching" : "☆ Watch"}</button>
      <button class="btn small ghost" data-act="dismiss">✕ Dismiss</button>
    </div></article>`;
}
function bindCards(root, reload) {
  $$("[data-pid]", root).forEach(card => {
    const pid = +card.dataset.pid;
    $$("[data-act]", card).forEach(b => b.onclick = async () => {
      const act = b.dataset.act;
      if (act === "add") return addToOrderSheet(pid);
      if (act === "watch") { const w = b.textContent.includes("☆"); await api(`/api/products/${pid}`, { method: "PATCH", json: { watched: w } }); toast(w ? "Watching: you'll get alerts" : "Unwatched"); }
      if (act === "dismiss") { await api(`/api/products/${pid}`, { method: "PATCH", json: { dismissed: true } }); toast("Dismissed"); }
      reload();
    });
  });
}
async function addToOrderSheet(pid, offerId) {
  const d = await api(`/api/products/${pid}`);
  const offers = d.offers;
  if (!offers.length) { toast("Add an offer first"); location.hash = `#/product/${pid}`; return; }
  const sel = offerId || offers[0].offer_id;
  sheet(`<h3>Add to order · ${esc(cap(d.product.name))}</h3>
    <label>Offer</label><select id="o">${offers.map(o => `<option value="${o.offer_id}" ${o.offer_id === sel ? "selected" : ""}>${esc((o.title || "").slice(0, 50))} · ¥${o.offer.price_cny} · value ${o.value.score}</option>`).join("")}</select>
    <div class="fields"><div><label>Quantity</label><input id="q" type="number" inputmode="numeric" value="20" min="1"></div>
    <div><label>Variant / colour</label><input id="v" placeholder="e.g. beige"></div></div>
    <div id="pv" class="info" style="margin-top:10px"></div>
    <button class="btn primary block" id="go" style="margin-top:12px">Add to basket</button>`, (el, close) => {
    const upd = () => {
      const o = offers.find(x => x.offer_id == $("#o", el).value); const q = +$("#q", el).value || 1; const p = o.pricing;
      $("#pv", el).innerHTML = `${q} × ${money(p.landing_tmt)} landing = <b>${money(p.landing_tmt * q)}</b> · expected profit <b>${money(p.profit_tmt * q)}</b>`;
    };
    $("#o", el).onchange = upd; $("#q", el).oninput = upd; upd();
    $("#go", el).onclick = async () => {
      await api("/api/basket/items", { json: { offer_id: +$("#o", el).value, qty: +$("#q", el).value, variant: $("#v", el).value || null } });
      close(); toast("Added to order ✓"); refreshBadge();
    };
  });
}

/* ---------- HOME ---------- */
async function viewHome() {
  setTitle("This week", false);
  view.innerHTML = `<div class="skel"></div><div class="skel"></div>`;
  const d = await api("/api/home");
  const p = d.profit_30d;
  view.innerHTML = `
    <section class="hero"><h1>What should I order this week?</h1><p>Ranked by trend, profit, seller and delivery time.</p></section>
    ${d.mock_data ? `<div class="info" style="margin-top:10px">🧪 Showing demo data. Clear it in <a href="#/settings">Settings</a> when you go live.</div>` : ""}
    <div class="kpis"><div class="kpi"><b>${money(p.profit_tmt)}</b><span>profit · 30d</span></div>
      <div class="kpi"><b>${p.margin_pct}%</b><span>margin</span></div>
      <div class="kpi"><b>${d.shipments_active}</b><span>on the way</span></div></div>
    <div class="stack" id="recs" style="margin-top:12px">${d.recommendations.map(recCard).join("") ||
      `<div class="empty"><div class="big">📭</div><p>No product ideas yet.<br>Import trends to get started.</p><a class="btn primary" href="#/import">Import trends</a></div>`}</div>`;
  bindCards(view, viewHome);
  refreshBadge();
}
/* ---------- TRENDS ---------- */
async function viewTrends() {
  setTitle("Trends", false);
  const show = localGet("trend-show", "active");
  const list = await api(`/api/products?show=${show}`);
  view.innerHTML = `
    <section class="hero"><h1>Product ideas</h1><p>Trend Score from TR/RU videos, search, Ashgabat channels and your sales.</p></section>
    <div class="tabs" style="margin-top:10px">${["active", "watched", "dismissed", "all"].map(s => `<button class="chip ${s === show ? "blue" : ""}" data-show="${s}">${cap(s)}</button>`).join("")}
      <button class="chip" id="newp">＋ New idea</button><a class="chip" href="#/import">⬇ Import</a></div>
    <div class="stack">${list.map(c => {
      const key = "trend-" + c.product_id;
      S.explain[key] = { title: `Trend Score · ${cap(c.name)}`, score: c.trend_score, ...(c.trend_breakdown || {}) };
      return `<a class="card row" href="#/product/${c.product_id}" style="color:inherit">
        <img class="thumb" src="${img(c)}" alt="" style="width:56px;height:56px" loading="lazy">
        <div class="grow" style="flex:1;min-width:0"><div class="pname ellipsis">${esc(c.name)}</div>
          <div class="meta">${stateChip(c.trend_state)}<span class="chip">${c.signals} signals</span>
          ${c.offers_count ? `<span class="chip">${c.offers_count} offers</span>` : `<span class="chip amber">no offers</span>`}
          ${c.counterfeit_risk ? `<span class="chip red">⚠ brand</span>` : ""}${!c.is_trend ? `<span class="chip">single signal</span>` : ""}</div></div>
        ${scoreBtn(c.trend_score, "trend", key)}</a>`;
    }).join("") || `<div class="empty"><div class="big">📈</div><p>Nothing here.</p></div>`}</div>`;
  $$("[data-show]").forEach(b => b.onclick = () => { localSet("trend-show", b.dataset.show); viewTrends(); });
  $("#newp").onclick = () => sheet(`<h3>New product idea</h3><label>Product name (English)</label><input id="nm" placeholder="e.g. capybara plush">
    <button class="btn primary block" id="go" style="margin-top:12px">Create</button>`, (el, close) => {
    $("#go", el).onclick = async () => { const r = await api("/api/products", { json: { name: $("#nm", el).value } }); close(); location.hash = `#/product/${r.id}`; };
  });
}

/* ---------- PRODUCT DETAIL ---------- */
function sparkline(points) {
  if (!points || points.length < 2) return "";
  const w = 300, h = 60, max = 100;
  const xs = points.map((_, i) => i / (points.length - 1) * w);
  const ys = points.map(p => h - (p.score / max) * (h - 6) - 3);
  const d = xs.map((x, i) => `${i ? "L" : "M"}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(" ");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-label="Trend score history">
    <path d="${d}" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
    ${xs.map((x, i) => `<circle cx="${x}" cy="${ys[i]}" r="3" fill="var(--accent)"><title>${points[i].day}: ${points[i].score}</title></circle>`).join("")}</svg>`;
}
function offerCompareCard(o, i) {
  const p = o.pricing, s = o.seller;
  const vkey = "value-" + o.offer_id, skey = "seller-" + o.offer_id;
  S.explain[vkey] = { title: "Value Score", subtitle: o.title, ...o.value };
  if (s) S.explain[skey] = { title: "Seller reliability · " + s.name, ...s };
  return `<div class="card ${i === 0 ? "win" : ""}">
    <div class="row"><span class="chip ${i === 0 ? "green" : ""}">#${i + 1}${i === 0 ? " best" : ""}</span><span class="chip">${esc(o.offer.platform)}</span><span class="spacer"></span>
      ${scoreBtn(o.value.score, "value", vkey)}</div>
    <h3 style="margin-top:8px">${esc((o.title || "").slice(0, 70))}</h3>
    <div class="kv"><span>Price</span><b>${cny(o.offer.price_cny)}${o.offer.price_tiers?.length ? ` <small>(${o.offer.price_tiers.map(t => `${t.min_qty}+ ¥${t.price_cny}`).join(", ")})</small>` : ""}</b></div>
    <div class="kv"><span>Landing cost</span><b>${money(p.landing_tmt)}</b></div>
    <div class="kv"><span>Sell for</span><b>${money(p.sell_tmt)}</b></div>
    <div class="kv"><span>Profit · margin</span><b style="color:var(--green)">${money(p.profit_tmt)} · ${p.margin_pct.toFixed(0)}%</b></div>
    <div class="kv"><span>Cargo share</span><span>${p.cargo_pct_of_cost.toFixed(0)}% · ${p.weight_kg} kg${p.weight_is_estimate ? " <span class='chip amber'>estimate</span>" : ""}</span></div>
    <div class="kv"><span>Rating · reviews</span><span>${o.offer.rating ?? "—"}★ · ${n0(o.offer.review_count)}</span></div>
    <div class="kv"><span>Sold</span><span>${n0(o.offer.sales_count)}</span></div>
    <div class="kv"><span>Delivery</span><span>${esc(o.delivery.label)}</span></div>
    ${s ? `<div class="kv"><span>Seller</span><button class="chip ${colour(s.score)}" data-explain="${skey}">${esc(s.name.slice(0, 22))} · ${s.score}</button></div>` : ""}
    ${o.offer.praises?.length ? `<p style="margin:8px 0 0;font-size:13px">👍 ${o.offer.praises.map(esc).join(" · ")}</p>` : ""}
    ${o.offer.complaints?.length ? `<p style="margin:4px 0 0;font-size:13px;color:var(--red)">👎 ${o.offer.complaints.map(esc).join(" · ")}</p>` : ""}
    ${o.local_check.status !== "no_data" ? `<div class="${["above_market", "above_median"].includes(o.local_check.status) ? "note" : o.local_check.status === "well_below" ? "info" : "ok"}" style="margin-top:8px">🏪 ${esc(o.local_check.message)}</div>` : ""}
    <div class="actions"><button class="btn primary small" data-addoffer="${o.offer_id}">＋ Order</button>
      ${o.offer.url ? `<a class="btn small" href="${esc(o.offer.url)}" target="_blank" rel="noopener">Open ↗</a>` : ""}
      <a class="btn small ghost" href="#/offer/${o.offer_id}/edit">Edit</a></div></div>`;
}
async function viewProduct(pid) {
  setTitle("Product", true);
  const d = await api(`/api/products/${pid}`);
  const p = d.product, c = d.card, t = d.trend;
  setTitle(cap(p.name), true);
  S.explain["trend-" + pid] = { title: "Trend Score · " + cap(p.name), ...t };
  const social = d.signals.filter(s => ["vidiq", "youtube", "paste"].includes(s.source));
  const local = d.signals.filter(s => s.source === "telegram");
  view.innerHTML = `
    <section class="hero row"><img class="thumb" src="${img(c)}" alt="">
      <div style="flex:1;min-width:0"><h1 style="text-transform:capitalize">${esc(p.name)}</h1>
        <div class="meta">${stateChip(c.trend_state)}<span class="chip">${esc(p.category)}</span><span class="chip">${c.signals} independent signals</span>
          ${d.stock ? `<span class="chip blue">${d.stock} in stock</span>` : ""}</div></div>
      ${scoreBtn(c.trend_score, "trend", "trend-" + pid)}</section>
    ${p.counterfeit_risk ? `<div class="warn">⚠️ ${esc(p.counterfeit_reason || "Branded / licensed item: counterfeits can be seized by customs.")}</div>` : ""}
    ${t.cap_reason ? `<div class="note" style="margin-top:8px">${esc(t.cap_reason)}</div>` : ""}
    ${t.state_reason ? `<p class="muted" style="margin:8px 0 0"><b>${(STATE[t.state] || STATE.unknown)[0]}:</b> ${esc(t.state_reason)}</p>` : ""}
    ${d.history.length > 1 ? `<div class="card" style="margin-top:12px"><small>Trend Score history</small>${sparkline(d.history)}</div>` : ""}
    <div class="actions"><button class="btn small" id="watch">${p.watched ? "★ Watching" : "☆ Watch"}</button>
      <button class="btn small ghost" id="dismiss">${p.dismissed ? "↺ Restore" : "✕ Dismiss"}</button></div>

    <h2>Best offers</h2>
    ${d.top3.length ? `<p class="ok">🏆 ${esc(d.why_winner)}</p><div class="compare">${d.top3.map(offerCompareCard).join("")}</div>` :
      `<div class="card empty"><div class="big">🔎</div><p>No offers yet. Search Taobao / 1688 below, then add the best listings.</p></div>`}
    ${d.offers.length > 3 ? `<details style="margin-top:10px"><summary>All ${d.offers.length} offers</summary><div class="compare" style="margin-top:8px">${d.offers.slice(3).map((o, i) => offerCompareCard(o, i + 3)).join("")}</div></details>` : ""}
    <a class="btn primary block" href="#/offer/new/${pid}" style="margin-top:12px">＋ Add offer (link + screenshots)</a>

    <h2>Find it in China</h2>
    <div class="card"><div class="stack">${d.search_links.map(l => `<div><div class="row"><b style="font-size:16px">${esc(l.term)}</b><span class="spacer"></span>
        <button class="btn small ghost" data-copy="${esc(l.term)}">Copy</button></div>
        <div class="actions" style="margin-top:6px"><a class="btn small" href="${esc(l.taobao)}" target="_blank" rel="noopener">淘宝 Taobao ↗</a>
        <a class="btn small" href="${esc(l["1688"])}" target="_blank" rel="noopener">1688 ↗</a></div></div>`).join("")}
      <p class="info">📸 Tip: also try <b>photo search</b>: save a screenshot of the TikTok/Reel, open the Taobao or 1688 app and tap the camera icon in the search bar. Often finds the factory listing.</p>
      ${d.search_terms.fallback ? `<p class="note">Add the Claude API key to generate Chinese search terms automatically. For now, edit them below.</p>` : ""}
      <small>TR: ${esc(d.search_terms.tr || "—")} · RU: ${esc(d.search_terms.ru || "—")} · TK: ${esc(d.search_terms.tk || "—")}</small>
      <button class="btn small ghost" id="editterms">Edit search terms</button></div></div>

    <h2>Ashgabat market</h2>
    <div class="card">${d.local_prices.length ? `<ul class="list">${d.local_prices.map(l => `<li><b>${money(l.price_tmt, { currency: "TMT" })}</b>
        <div class="grow"><div class="ellipsis">${esc(l.text || l.channel)}</div><small>${esc(l.channel || l.source)} · ${fmtDate(l.seen_at)}</small></div>
        ${l.url ? `<a href="${esc(l.url)}" target="_blank" rel="noopener">↗</a>` : ""}</li>`).join("")}</ul>` : `<p class="muted">No local prices yet. Add channels in Settings, or enter a price you saw.</p>`}
      <div class="quick" style="margin-top:8px"><input id="lp" type="number" inputmode="decimal" placeholder="Price seen (TMT)"><input id="lpw" placeholder="Where"><button class="btn" id="addlp">Add</button></div></div>

    <h2>Posts for customers</h2>
    <div class="card" id="caps">${d.captions ? captionsHtml(d.captions) : `<p class="muted">Instagram / Telegram post text in Turkmen and Russian.</p>`}
      <button class="btn small" id="gencap">${d.captions ? "↻ Regenerate" : "✍ Write posts (TK + RU)"}</button></div>

    <h2>Signals (${d.signals.length})</h2>
    <div class="card"><ul class="list">${[...social, ...local, ...d.signals.filter(s => s.source === "google_trends")].map(s => `<li>
      <span class="chip">${esc(s.platform || s.source)}</span><div class="grow"><div class="ellipsis">${esc(s.title)}</div>
      <small>${s.views ? n0(s.views) + " views · " : ""}${s.outlier_score ? s.outlier_score + "× outlier · " : ""}${s.value != null ? "value " + n0(s.value) + " · " : ""}${esc(s.language || "")} · ${fmtDate(s.published_at || s.collected_at)}${s.creator ? " · " + esc(s.creator) : ""}</small></div>
      ${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">↗</a>` : ""}</li>`).join("")}</ul></div>

    <h2>Details</h2>
    <div class="card"><div class="fields"><div><label>Estimated weight (kg)</label><input id="w" type="number" step="0.01" value="${p.est_weight_kg ?? ""}"></div>
      <div><label>Category</label><input id="cat" value="${esc(p.category)}"></div>
      <div class="full"><label>Notes</label><textarea id="notes" style="min-height:70px">${esc(p.notes || "")}</textarea></div></div>
      <div class="actions"><button class="btn small" id="savep">Save</button><span class="spacer"></span><button class="btn small danger" id="delp">Delete product</button></div></div>`;
  $$("[data-addoffer]").forEach(b => b.onclick = () => addToOrderSheet(pid, +b.dataset.addoffer));
  $$("[data-copy]").forEach(b => b.onclick = () => copy(b.dataset.copy));
  $("#watch").onclick = async () => { await api(`/api/products/${pid}`, { method: "PATCH", json: { watched: !p.watched } }); viewProduct(pid); };
  $("#dismiss").onclick = async () => { await api(`/api/products/${pid}`, { method: "PATCH", json: { dismissed: !p.dismissed } }); viewProduct(pid); };
  $("#addlp").onclick = async () => { const v = $("#lp").value; if (!v) return; await api(`/api/products/${pid}/local-prices`, { json: { price_tmt: v, where: $("#lpw").value } }); toast("Local price saved"); viewProduct(pid); };
  $("#gencap").onclick = async () => { $("#gencap").disabled = true; const caps = await api(`/api/products/${pid}/captions`, { method: "POST" }); $("#caps").innerHTML = captionsHtml(caps); bindCopy($("#caps")); };
  bindCopy($("#caps"));
  $("#savep").onclick = async () => { await api(`/api/products/${pid}`, { method: "PATCH", json: { est_weight_kg: parseFloat($("#w").value) || null, category: $("#cat").value, notes: $("#notes").value } }); toast("Saved"); viewProduct(pid); };
  $("#delp").onclick = async () => { if (!confirm("Delete this product and its offers?")) return; await api(`/api/products/${pid}`, { method: "DELETE" }); location.hash = "#/trends"; };
  $("#editterms").onclick = () => sheet(`<h3>Search terms</h3><label>Chinese (one per line)</label><textarea id="zh">${esc((d.search_terms.zh || []).join("\n"))}</textarea>
    <div class="fields"><div><label>Turkish</label><input id="tr" value="${esc(d.search_terms.tr || "")}"></div><div><label>Russian</label><input id="ru" value="${esc(d.search_terms.ru || "")}"></div>
    <div><label>Turkmen</label><input id="tk" value="${esc(d.search_terms.tk || "")}"></div></div><button class="btn primary block" id="go" style="margin-top:12px">Save</button>`, (el, close) => {
    $("#go", el).onclick = async () => {
      const st = { zh: $("#zh", el).value.split("\n").map(s => s.trim()).filter(Boolean), tr: $("#tr", el).value, ru: $("#ru", el).value, tk: $("#tk", el).value };
      await api(`/api/products/${pid}`, { method: "PATCH", json: { search_terms: st, keywords: [...new Set([...(p.keywords || []), st.tr, st.ru, st.tk].filter(Boolean).map(s => s.toLowerCase()))] } });
      close(); viewProduct(pid);
    };
  });
}
function captionsHtml(c) {
  return ["tk", "ru"].map(l => c[l] ? `<div style="margin-bottom:10px"><div class="row"><b>${l === "tk" ? "🇹🇲 Türkmençe" : "🇷🇺 Русский"}</b><span class="spacer"></span>
    <button class="btn small ghost" data-copy="${esc(c[l].caption)}">Copy</button></div><div class="pre">${esc(c[l].title)}\n\n${esc(c[l].caption)}</div></div>` : "").join("") +
    (c.tk?.template ? `<small>Template text: add the Claude key for natural, product-specific posts.</small>` : "");
}
function bindCopy(root) { $$("[data-copy]", root).forEach(b => b.onclick = () => copy(b.dataset.copy)); }

/* ---------- OFFER FORM (link + screenshots → AI) ---------- */
const OFFER_FIELDS = [
  ["url", "Listing link", "full"], ["title_en", "Title (English)", "full"], ["title", "Original title", "full"],
  ["price_cny", "Price (¥)", "", "number"], ["domestic_shipping_cny", "China shipping (¥)", "", "number"],
  ["sales_count", "Sales count", "", "number"], ["rating", "Rating (0-5)", "", "number"],
  ["review_count", "Reviews", "", "number"], ["buyer_photos", "Buyer photos", "", "number"],
  ["repeat_purchase_rate", "Repeat buyers %", "", "number"], ["weight_kg", "Weight (kg)", "", "number"],
  ["size", "Size", ""], ["variants", "Variants / colours", ""],
];
const SELLER_INPUTS = [["store_name", "Store name", "full"], ["store_age_years", "Store age (years)", "", "number"],
  ["store_rating", "Store rating (0-5)", "", "number"], ["store_total_sales", "Store total sales", "", "number"],
  ["response_rate", "Response rate %", "", "number"], ["dispute_rate", "Refund/dispute %", "", "number"]];
function fieldHtml([k, label, cls, type], val) {
  return `<div class="${cls}"><label for="f_${k}">${label}</label><input id="f_${k}" data-k="${k}" ${type === "number" ? 'type="number" step="any" inputmode="decimal"' : ""} value="${esc(val ?? "")}"></div>`;
}
async function viewOfferForm(pid, existing) {
  const prod = await api(`/api/products/${pid}`);
  setTitle(existing ? "Edit offer" : "Add offer", true);
  const o = existing || {};
  const sellers = await api("/api/sellers");
  view.innerHTML = `<section class="hero"><h1>${existing ? "Edit offer" : "Add an offer"}</h1><p style="text-transform:capitalize">${esc(prod.product.name)}</p></section>
    ${existing ? "" : `<div class="card" style="margin-top:12px"><h3>1. Link + screenshots</h3>
      <p class="muted" style="margin:0">Paste the Taobao/1688 link and add screenshots of the listing page and the reviews page. AI fills the form; you can edit everything.</p>
      <label>Link</label><input id="url" placeholder="https://item.taobao.com/..." value="${esc(o.url || "")}">
      <label>Screenshots</label><input id="shots" type="file" accept="image/*" multiple>
      <div class="imgs" id="prev"></div>
      <button class="btn primary block" id="read" style="margin-top:10px">✨ Read screenshots</button><div id="aimsg" style="margin-top:8px"></div></div>`}
    <div class="card" style="margin-top:12px"><h3>${existing ? "Offer" : "2. Check the details"}</h3>
      <div class="fields">${OFFER_FIELDS.map(f => fieldHtml(f, o[f[0]])).join("")}
        <div class="full"><label>Price tiers (qty:price per line, e.g. 10:13.5)</label><textarea id="tiers" style="min-height:60px">${esc((o.price_tiers || []).map(t => `${t.min_qty}:${t.price_cny}`).join("\n"))}</textarea></div>
        <div class="full"><label>Top praises (one per line)</label><textarea id="praises" style="min-height:60px">${esc((o.praises || []).join("\n"))}</textarea></div>
        <div class="full"><label>Top complaints (one per line)</label><textarea id="complaints" style="min-height:60px">${esc((o.complaints || []).join("\n"))}</textarea></div>
      </div>
      <div class="info" id="calc" style="margin-top:10px">Enter a price to see landing cost.</div></div>
    <div class="card" style="margin-top:12px"><h3>Seller</h3>
      <label>Existing seller</label><select id="seller"><option value="">— new / from screenshot —</option>${sellers.map(s => `<option value="${s.id}" ${o.seller_id === s.id ? "selected" : ""}>${esc(s.name)} (${s.score.score})</option>`).join("")}</select>
      <div class="fields" id="newseller">${SELLER_INPUTS.map(f => fieldHtml(f, "")).join("")}
        <div class="full"><label>Factory or reseller?</label><select id="f_is_factory" data-k="is_factory"><option value="">Unknown</option><option value="true">Factory / source</option><option value="false">Reseller</option></select></div></div></div>
    <button class="btn primary block" id="save" style="margin-top:12px">Save offer</button>
    ${existing ? `<button class="btn block danger" id="del" style="margin-top:8px">Delete offer</button>` : ""}`;
  let screenshots = o.screenshots || [];
  const recalc = async () => {
    const price = $("#f_price_cny").value; if (!price) return;
    const q = new URLSearchParams({ price_cny: price, shipping_cny: $("#f_domestic_shipping_cny").value || 0, name: prod.product.name });
    if ($("#f_weight_kg").value) q.set("weight_kg", $("#f_weight_kg").value);
    const r = await api("/api/pricing/calc?" + q);
    $("#calc").innerHTML = `Landing <b>${money(r.landing_tmt)}</b> → sell <b>${money(r.sell_tmt)}</b> · profit <b>${money(r.profit_tmt)}</b> (${r.margin_pct.toFixed(0)}%) · cargo ${r.cargo_pct_of_cost.toFixed(0)}%${r.weight_is_estimate ? ` · <span class="chip amber">weight estimated ${r.weight_kg} kg: ${esc(r.weight_note)}</span>` : ""}`;
  };
  ["#f_price_cny", "#f_domestic_shipping_cny", "#f_weight_kg"].forEach(s => $(s).addEventListener("input", recalc));
  recalc();
  const toggleSeller = () => $("#newseller").hidden = !!$("#seller").value; $("#seller").onchange = toggleSeller; toggleSeller();
  if (!existing) {
    $("#shots").onchange = () => { $("#prev").innerHTML = [...$("#shots").files].map(f => `<img src="${URL.createObjectURL(f)}" alt="">`).join(""); };
    $("#read").onclick = async () => {
      const fd = new FormData(); fd.append("url", $("#url").value); [...$("#shots").files].forEach(f => fd.append("files", f));
      $("#read").disabled = true; $("#read").textContent = "Reading…";
      try {
        const r = await api("/api/offers/extract", { method: "POST", body: fd });
        screenshots = r.screenshots || [];
        $("#f_url").value = $("#url").value;
        if (r.ai && r.fields) {
          const f = r.fields;
          Object.entries(f).forEach(([k, v]) => { const el = $(`[data-k="${k}"]`); if (el && v != null && v !== "") { el.value = v === true ? "true" : v === false ? "false" : v; el.classList.add("ai-filled"); } });
          if (f.price_tiers?.length) $("#tiers").value = f.price_tiers.map(t => `${t.min_qty}:${t.price_cny}`).join("\n");
          if (f.praises?.length) $("#praises").value = f.praises.join("\n");
          if (f.complaints?.length) $("#complaints").value = f.complaints.join("\n");
          const match = sellers.find(s => f.store_name && s.name === f.store_name); if (match) { $("#seller").value = match.id; toggleSeller(); }
          $("#aimsg").innerHTML = `<div class="ok">✨ Filled from screenshots${r.cached ? " (cached)" : ""}. Blue fields came from AI: check them.</div>`;
          recalc();
        } else {
          $("#aimsg").innerHTML = `<div class="note">${esc(r.message)}</div>`;
        }
      } catch (e) { $("#aimsg").innerHTML = `<div class="warn">${esc(e.message)}</div>`; }
      $("#read").disabled = false; $("#read").textContent = "✨ Read screenshots";
    };
  }
  const lines = id => $(id).value.split("\n").map(s => s.trim()).filter(Boolean);
  $("#save").onclick = async () => {
    const body = { product_id: +pid, screenshots, ai_filled: !!$(".ai-filled") };
    $$("[data-k]").forEach(el => { if (el.closest("#newseller") && $("#seller").value) return; body[el.dataset.k] = el.value; });
    body.is_factory = body.is_factory === "" || body.is_factory === undefined ? null : body.is_factory === "true";
    body.price_tiers = lines("#tiers").map(l => { const [q, p] = l.split(/[:=]/); return { min_qty: +q, price_cny: +p }; }).filter(t => t.min_qty && t.price_cny);
    body.praises = lines("#praises"); body.complaints = lines("#complaints");
    if ($("#seller").value) body.seller_id = +$("#seller").value;
    if (!body.price_cny) { toast("Enter a price"); return; }
    if (body.url) body.platform = body.url.includes("1688") ? "1688" : body.url.includes("tmall") ? "tmall" : "taobao";
    if (existing) await api(`/api/offers/${existing.id}`, { method: "PATCH", json: body }); else await api("/api/offers", { json: body });
    toast("Offer saved ✓"); location.hash = `#/product/${pid}`;
  };
  if (existing) $("#del").onclick = async () => { if (!confirm("Delete this offer?")) return; await api(`/api/offers/${existing.id}`, { method: "DELETE" }); location.hash = `#/product/${pid}`; };
}
async function viewOfferEdit(oid) { const o = await api(`/api/offers/${oid}`); return viewOfferForm(o.product_id, o); }

/* ---------- ORDER ---------- */
async function viewOrder() {
  setTitle("Order", false);
  const b = await api("/api/basket");
  const t = b.totals;
  view.innerHTML = `<section class="hero"><h1>Order basket</h1><p>Draft #${b.order.id}: ${t.units} pcs</p></section>
    ${b.lines.length ? `
    <div class="kpis"><div class="kpi"><b>${cny(t.goods_cny)}</b><span>goods · ${money(t.goods_tmt, { currency: "TMT" })}</span></div>
      <div class="kpi"><b>${t.weight_kg.toFixed(1)} kg</b><span>cargo ${money(t.cargo_tmt)}</span></div>
      <div class="kpi"><b style="color:var(--green)">${money(t.profit_tmt)}</b><span>expected profit</span></div></div>
    <div class="card" style="margin-top:4px"><div class="kv"><span>Total landing cost</span><b>${money(t.landing_tmt)}</b></div>
      <div class="kv"><span>Expected revenue</span><b>${money(t.sell_tmt)}</b></div>
      <div class="kv"><span>Arrives</span><b>${fmtDate(b.arrival.earliest)} – ${fmtDate(b.arrival.latest)}</b></div>
      <small>${esc(b.delivery.basis)}${t.weight_has_estimates ? " · some weights are estimates" : ""}</small></div>
    <div class="stack" style="margin-top:12px">${b.lines.map(l => `<div class="card"><div class="row">
      <img class="thumb" style="width:52px;height:52px" src="${l.image_url ? esc(l.image_url) : `/api/placeholder/${l.product_id}.svg`}" alt="">
      <div class="grow" style="flex:1;min-width:0"><div class="pname ellipsis">${esc(l.product)}</div><small class="ellipsis" style="display:block">${esc(l.title || "")}${l.seller ? " · " + esc(l.seller) : ""}</small></div>
      <button class="iconbtn" data-rm="${l.item_id}" aria-label="Remove">✕</button></div>
      <div class="fields"><div><label>Qty</label><input type="number" inputmode="numeric" min="1" value="${l.qty}" data-qty="${l.item_id}"></div>
        <div><label>Variant</label><input value="${esc(l.variant || "")}" data-var="${l.item_id}"></div></div>
      <div class="kv" style="margin-top:6px"><span>${cny(l.unit_cny)} × ${l.qty} · ${l.weight_kg} kg each${l.weight_is_estimate ? " (est.)" : ""}</span><b>${money(l.line_landing_tmt)}</b></div>
      <div class="kv"><span>Profit if sold at ${money(l.unit_sell_tmt)}</span><b style="color:var(--green)">${money(l.line_profit_tmt)}</b></div></div>`).join("")}</div>
    <h2>Send it</h2>
    <div class="card"><div class="row wrapr"><span>Language:</span><div class="seg" id="lang"><button data-l="en" class="on">EN</button><button data-l="ru">RU</button><button data-l="tk">TK</button></div></div>
      <div class="actions"><button class="btn" id="copy">📋 Copy list</button><button class="btn" id="share">↗ Share (WhatsApp/Telegram)</button>
        <button class="btn" id="bot">🤖 Send via bot</button></div>
      <button class="btn primary block" id="place" style="margin-top:12px">✓ Mark as ordered (creates shipment)</button></div>` :
    `<div class="empty"><div class="big">🛒</div><p>Basket is empty. Add products from the home screen.</p><a class="btn primary" href="#/">See this week's picks</a></div>`}
    <h2>Past orders</h2><div id="past" class="card"><small>Loading…</small></div>`;
  let lang = "en";
  $$("#lang button").forEach(btn => btn.onclick = () => { lang = btn.dataset.l; $$("#lang button").forEach(x => x.classList.toggle("on", x === btn)); });
  $$("[data-rm]").forEach(btn => btn.onclick = async () => { await api(`/api/basket/items/${btn.dataset.rm}`, { method: "DELETE" }); viewOrder(); refreshBadge(); });
  $$("[data-qty]").forEach(inp => inp.onchange = async () => { await api(`/api/basket/items/${inp.dataset.qty}`, { method: "PATCH", json: { qty: +inp.value } }); viewOrder(); refreshBadge(); });
  $$("[data-var]").forEach(inp => inp.onchange = () => api(`/api/basket/items/${inp.dataset.var}`, { method: "PATCH", json: { variant: inp.value } }));
  const text = () => api(`/api/orders/${b.order.id}/export?lang=${lang}`);
  if (b.lines.length) {
    $("#copy").onclick = async () => copy(await text());
    $("#share").onclick = async () => { const tx = await text(); if (navigator.share) navigator.share({ text: tx }).catch(() => { }); else copy(tx); };
    $("#bot").onclick = async () => { try { await api(`/api/orders/${b.order.id}/send`, { json: { lang } }); toast("Sent to your person ✓"); } catch (e) { toast(e.message, 4000); } };
    $("#place").onclick = async () => {
      if (!confirm("Mark this basket as ordered? It becomes a shipment you can track.")) return;
      const r = await api("/api/basket/place", { method: "POST" }); toast(`Shipment ${r.code} created`); refreshBadge(); location.hash = "#/shipments";
    };
  }
  const past = await api("/api/orders");
  $("#past").innerHTML = past.length ? `<ul class="list">${past.map(o => `<li><div class="grow"><b>#${o.order.id}</b> · ${o.totals.units} pcs · ${cny(o.totals.goods_cny)}
      <br><small>placed ${fmtDate(o.order.placed_at)} · landing ${money(o.totals.landing_tmt)}</small></div>
      <button class="btn small ghost" data-exp="${o.order.id}">📋</button></li>`).join("")}</ul>` : `<small class="muted">None yet.</small>`;
  $$("[data-exp]").forEach(b2 => b2.onclick = async () => copy(await api(`/api/orders/${b2.dataset.exp}/export?lang=${lang}`)));
}

/* ---------- SHIPMENTS ---------- */
const SHIP_ST = ["ordered", "china_warehouse", "in_transit", "arrived", "sold_out"];
const SHIP_LABEL = { ordered: "Ordered", china_warehouse: "China warehouse", in_transit: "In transit", arrived: "Arrived", sold_out: "Sold out" };
async function viewShipments() {
  setTitle("Shipments", true);
  const d = await api("/api/shipments");
  view.innerHTML = `<section class="hero"><h1>Shipments</h1><p>Typical delivery: ${esc(d.delivery.label)} · ${esc(d.delivery.basis)}</p></section>
    <div class="stack" style="margin-top:12px">${d.shipments.map(s => {
      const idx = SHIP_ST.indexOf(s.status);
      return `<div class="card"><div class="row"><b>${esc(s.code || "#" + s.id)}</b><span class="chip ${s.status === "arrived" || s.status === "sold_out" ? "green" : "blue"}">${SHIP_LABEL[s.status] || s.status}</span>
        <span class="spacer"></span><small>${s.seller_name ? esc(s.seller_name.slice(0, 24)) : ""}</small></div>
        <div class="steps">${SHIP_ST.map((st, i) => `<div class="${i < idx ? "done" : i === idx ? "cur" : ""}" title="${SHIP_LABEL[st]}"></div>`).join("")}</div>
        <small>${s.items.map(i => `${i.emoji} ${esc(i.product)} ×${i.qty}`).join(" · ")}</small>
        <div class="kv" style="margin-top:6px"><span>Ordered</span><span>${fmtDate(s.ordered_at)}</span></div>
        ${s.eta ? `<div class="kv"><span>ETA</span><b>${fmtDate(s.eta.earliest)} – ${fmtDate(s.eta.latest)}</b></div><div class="kv"><span>Days so far</span><span>${s.days_in_transit}</span></div>` : ""}
        ${s.actual_days ? `<div class="kv"><span>Took</span><b>${s.actual_days} days</b></div>` : ""}
        <div class="kv"><span>Weight · cargo</span><span>${s.weight_kg ? s.weight_kg.toFixed(1) + " kg" : "—"} · ${money(s.cargo_cost_tmt)}</span></div>
        <div class="kv"><span>Delivered · cash</span><span>${s.delivered_at ? "✓ " + fmtDate(s.delivered_at) : "—"} · ${s.cash_received_at ? "✓ " + fmtDate(s.cash_received_at) : "—"}</span></div>
        <div class="actions">${idx < SHIP_ST.length - 1 ? `<button class="btn small primary" data-adv="${s.id}" data-to="${SHIP_ST[idx + 1]}">→ ${SHIP_LABEL[SHIP_ST[idx + 1]]}</button>` : ""}
          ${!s.delivered_at ? `<button class="btn small" data-adv="${s.id}" data-to="delivered">🚚 Delivered</button>` : ""}
          ${!s.cash_received_at ? `<button class="btn small" data-adv="${s.id}" data-to="cash_received">💵 Cash received</button>` : ""}
          <button class="btn small ghost" data-edit="${s.id}">Edit</button></div></div>`;
    }).join("") || `<div class="empty"><div class="big">🚚</div><p>No shipments. Place an order to create one.</p></div>`}</div>`;
  $$("[data-adv]").forEach(b => b.onclick = async () => { await api(`/api/shipments/${b.dataset.adv}/status`, { json: { status: b.dataset.to } }); toast(b.dataset.to === "arrived" ? "Arrived: stock updated ✓" : "Updated ✓"); viewShipments(); });
  $$("[data-edit]").forEach(b => b.onclick = () => {
    const s = d.shipments.find(x => x.id == b.dataset.edit);
    const dt = v => v ? v.slice(0, 10) : "";
    sheet(`<h3>Edit ${esc(s.code)}</h3><div class="fields"><div><label>Code</label><input id="code" value="${esc(s.code)}"></div>
      <div><label>Real weight (kg)</label><input id="w" type="number" step="0.01" value="${s.weight_kg ?? ""}"></div>
      <div><label>Cargo cost (TMT)</label><input id="cc" type="number" value="${s.cargo_cost_tmt ?? ""}"></div>
      <div><label>Ordered</label><input id="oa" type="date" value="${dt(s.ordered_at)}"></div>
      <div><label>Arrived</label><input id="aa" type="date" value="${dt(s.arrived_at)}"></div>
      <div class="full"><label>Notes</label><input id="nt" value="${esc(s.notes || "")}"></div></div>
      <button class="btn primary block" id="go" style="margin-top:12px">Save</button>
      ${s.seller_id ? `<button class="btn block" id="ev" style="margin-top:8px">Log a problem with the seller…</button>` : ""}`, (el, close) => {
      $("#go", el).onclick = async () => {
        await api(`/api/shipments/${s.id}`, { method: "PATCH", json: { code: $("#code", el).value, weight_kg: $("#w", el).value, cargo_cost_tmt: $("#cc", el).value,
          ordered_at: $("#oa", el).value || null, arrived_at: $("#aa", el).value || null, notes: $("#nt", el).value } });
        close(); viewShipments();
      };
      const ev = $("#ev", el); if (ev) ev.onclick = () => { close(); sellerEventSheet(s.seller_id, s.id); };
    });
  });
}

/* ---------- SALES ---------- */
async function viewSales() {
  setTitle("Sales", false);
  const [sales, stock, prods] = await Promise.all([api("/api/sales?days=30"), api("/api/stock"), api("/api/products?show=all")]);
  const sm = sales.summary;
  view.innerHTML = `<section class="hero"><h1>Sales</h1><p>Type it like a message: <b>2 hamster 250</b></p></section>
    <div class="card" style="margin-top:12px"><div class="quick"><input id="qs" placeholder="2 hamster 250" autocomplete="off" enterkeyhint="send"><button class="btn primary" id="qsgo">Log</button></div>
      <div id="qsmsg" style="margin-top:8px"></div>
      <details style="margin-top:6px"><summary class="muted">Or pick a product</summary>
        <div class="fields"><div class="full"><label>Product</label><select id="sp">${prods.map(p => `<option value="${p.product_id}">${esc(p.emoji)} ${esc(p.name)}</option>`).join("")}</select></div>
          <div><label>Qty</label><input id="sq" type="number" inputmode="numeric" value="1" min="1"></div><div><label>Price each (TMT)</label><input id="spr" type="number" inputmode="decimal"></div></div>
        <button class="btn block" id="sgo" style="margin-top:10px">Log sale</button></details></div>
    <div class="kpis"><div class="kpi"><b>${sm.units}</b><span>units · 30d</span></div><div class="kpi"><b>${money(sm.revenue_tmt)}</b><span>revenue</span></div>
      <div class="kpi"><b style="color:var(--green)">${money(sm.profit_tmt)}</b><span>profit · ${sm.margin_pct}%</span></div></div>
    <div class="actions"><a class="btn small" href="#/report">📊 Weekly report</a></div>
    <h2>Stock</h2><div class="card">${stock.length ? `<table class="t"><tr><th>Product</th><th class="r">Stock</th><th class="r">Coming</th><th class="r">Sold 30d</th></tr>
      ${stock.map(s => `<tr><td>${esc(s.emoji)} ${esc(cap(s.name))}</td><td class="r"><b style="color:${s.stock < 0 ? "var(--red)" : "inherit"}">${s.stock}</b></td><td class="r">${s.incoming || "—"}</td><td class="r">${s.sold_30d}</td></tr>`).join("")}</table>` : `<small class="muted">No stock yet. It updates automatically when a shipment arrives.</small>`}
      <button class="btn small ghost" id="adj" style="margin-top:8px">Adjust stock…</button></div>
    <h2>Recent sales</h2><div class="card"><ul class="list">${sales.sales.slice(0, 40).map(s => `<li><span>${esc(s.emoji)}</span><div class="grow"><div class="ellipsis">${s.qty} × ${esc(s.product)} @ ${money(s.unit_price_tmt, { currency: "TMT" })}</div>
      <small>${fmtDate(s.sold_at)} · ${s.unit_cost_tmt ? "profit " + money((s.unit_price_tmt - s.unit_cost_tmt) * s.qty) : "cost unknown"}${s.source === "bot" ? " · via bot" : ""}</small></div>
      <button class="iconbtn" data-del="${s.id}" aria-label="Delete">✕</button></li>`).join("") || `<li class="muted">No sales yet.</li>`}</ul></div>`;
  const logQuick = async () => {
    const text = $("#qs").value.trim(); if (!text) return;
    try {
      const r = await api("/api/sales/quick", { json: { text } });
      $("#qsmsg").innerHTML = `<div class="ok">✓ ${r.parsed.qty} × ${esc(r.product)} at ${r.parsed.unit_price} TMT · ${r.profit_tmt != null ? "profit " + money(r.profit_tmt) : ""} · stock ${r.stock}</div>`;
      $("#qs").value = ""; setTimeout(viewSales, 1200);
    } catch (e) {
      if (e.status === 409 && e.data?.candidates) {
        $("#qsmsg").innerHTML = `<div class="note">${esc(e.data.detail)} ${e.data.candidates.map(c => `<button class="chip blue" data-c="${esc(c.name)}">${esc(c.name)}</button>`).join(" ")}</div>`;
        $$("[data-c]").forEach(b => b.onclick = () => { $("#qs").value = `${e.data.parsed.qty} ${b.dataset.c} ${e.data.parsed.unit_price}`; logQuick(); });
      } else $("#qsmsg").innerHTML = `<div class="warn">${esc(e.message)}</div>`;
    }
  };
  $("#qsgo").onclick = logQuick; $("#qs").onkeydown = e => { if (e.key === "Enter") logQuick(); };
  $("#sgo").onclick = async () => { const r = await api("/api/sales", { json: { product_id: +$("#sp").value, qty: +$("#sq").value, unit_price_tmt: $("#spr").value } }); toast(`Logged · stock ${r.stock}`); viewSales(); };
  $$("[data-del]").forEach(b => b.onclick = async () => { if (!confirm("Delete this sale?")) return; await api(`/api/sales/${b.dataset.del}`, { method: "DELETE" }); viewSales(); });
  $("#adj").onclick = () => sheet(`<h3>Adjust stock</h3><label>Product</label><select id="p">${prods.map(p => `<option value="${p.product_id}">${esc(p.name)}</option>`).join("")}</select>
    <div class="fields"><div><label>Change (+/-)</label><input id="q" type="number" value="0"></div><div><label>Unit cost TMT (for +)</label><input id="c" type="number"></div></div>
    <button class="btn primary block" id="go" style="margin-top:12px">Save</button>`, (el, close) => {
    $("#go", el).onclick = async () => { await api("/api/stock/adjust", { json: { product_id: +$("#p", el).value, qty: +$("#q", el).value, unit_cost_tmt: $("#c", el).value } }); close(); viewSales(); };
  });
}

/* ---------- REPORT ---------- */
async function viewReport() {
  setTitle("Weekly report", true);
  const r = await api("/api/report/weekly");
  view.innerHTML = `<section class="hero"><h1>Weekly report</h1><p>${fmtDate(r.from)} – ${fmtDate(r.to)}</p></section>
    <div class="kpis"><div class="kpi"><b>${r.totals.units}</b><span>units sold</span></div><div class="kpi"><b>${money(r.totals.revenue)}</b><span>revenue</span></div>
      <div class="kpi"><b style="color:var(--green)">${money(r.totals.profit)}</b><span>profit · ${r.totals.margin_pct}%</span></div></div>
    <h2>What sold</h2><div class="card">${r.sold.length ? `<table class="t"><tr><th>Product</th><th class="r">Units</th><th class="r">Profit</th><th class="r">Real margin</th></tr>
      ${r.sold.map(s => `<tr><td>${esc(s.emoji)} ${esc(cap(s.name))}</td><td class="r">${s.units}</td><td class="r">${money(s.profit)}</td><td class="r"><span class="chip ${s.margin_pct >= 40 ? "green" : s.margin_pct >= 20 ? "amber" : "red"}">${s.margin_pct}%</span></td></tr>`).join("")}</table>` : `<small class="muted">No sales this week.</small>`}</div>
    <h2>🔁 Reorder</h2><div class="card">${r.reorder.length ? `<ul class="list">${r.reorder.map(x => `<li><span>${esc(x.emoji)}</span><div class="grow"><b style="text-transform:capitalize">${esc(x.name)}</b> · order ~${x.suggested_qty}<br><small>${esc(x.reason)}</small></div>
      <button class="btn small primary" data-re="${x.product_id}">＋</button></li>`).join("")}</ul>` : `<small class="muted">Nothing urgent.</small>`}</div>
    <h2>⛔ Stop buying</h2><div class="card">${r.stop.length ? `<ul class="list">${r.stop.map(x => `<li><span>${esc(x.emoji)}</span><div class="grow"><b style="text-transform:capitalize">${esc(x.name)}</b><br><small>${esc(x.reason)} · ${x.stock} in stock</small></div></li>`).join("")}</ul>` : `<small class="muted">Nothing to stop.</small>`}</div>`;
  $$("[data-re]").forEach(b => b.onclick = () => addToOrderSheet(+b.dataset.re));
}

/* ---------- SELLERS ---------- */
async function viewSellers() {
  setTitle("Sellers", true);
  const list = await api("/api/sellers");
  view.innerHTML = `<section class="hero"><h1>Sellers</h1><p>Reliability from store data + your own experience.</p></section>
    <div class="stack" style="margin-top:12px">${list.map(s => {
      const key = "sel-" + s.id; S.explain[key] = { title: "Seller reliability · " + s.name, ...s.score };
      return `<a class="card row" href="#/seller/${s.id}" style="color:inherit"><div class="grow" style="flex:1;min-width:0"><b class="ellipsis" style="display:block">${esc(s.name)}</b>
        <div class="meta"><span class="chip">${esc(s.platform)}</span>${s.is_factory ? `<span class="chip green">factory</span>` : s.is_factory === false ? `<span class="chip">reseller</span>` : ""}
        <span class="chip">${s.offers} offers</span>${s.events ? `<span class="chip amber">${s.events} logged</span>` : ""}</div></div>${scoreBtn(s.score.score, "seller", key)}</a>`;
    }).join("") || `<div class="empty">No sellers yet: they're created when you add offers.</div>`}</div>`;
}
const EVENT_LABEL = { delay: "⏰ Delay", damage: "💥 Damage", wrong_item: "❌ Wrong item", missing: "📭 Missing", good: "👍 Good experience" };
function sellerEventSheet(sid, shipmentId) {
  sheet(`<h3>Log experience</h3><label>What happened?</label><select id="k">${Object.entries(EVENT_LABEL).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
    <label>Days late (for delays)</label><input id="dl" type="number" inputmode="numeric"><label>Note</label><input id="nt">
    <button class="btn primary block" id="go" style="margin-top:12px">Save</button>`, (el, close) => {
    $("#go", el).onclick = async () => { await api(`/api/sellers/${sid}/events`, { json: { kind: $("#k", el).value, days_late: $("#dl", el).value, note: $("#nt", el).value, shipment_id: shipmentId } }); close(); toast("Logged: seller score updated"); router(); };
  });
}
async function viewSeller(sid) {
  const s = await api(`/api/sellers/${sid}`);
  setTitle(s.name, true);
  const key = "sel-" + sid; S.explain[key] = { title: "Seller reliability · " + s.name, ...s.score };
  view.innerHTML = `<section class="hero row"><div style="flex:1;min-width:0"><h1>${esc(s.name)}</h1><p>${esc(s.platform)} · ${esc(s.location || "")}</p></div>${scoreBtn(s.score.score, "seller", key)}</section>
    <div class="card" style="margin-top:12px">${s.score.components.map(c => `<div class="comp"><div class="row"><b>${esc(c.label)}</b><span class="spacer"></span><b>${c.points}</b><small>/ ${c.max}</small></div><small>${esc(c.reason)}</small></div>`).join("")}</div>
    <h2>My experience</h2><div class="card"><ul class="list">${s.events.map(e => `<li><span>${EVENT_LABEL[e.kind] || e.kind}</span><div class="grow"><small>${esc(e.note || "")}${e.days_late ? ` · ${e.days_late} days late` : ""} · ${fmtDate(e.created_at)}</small></div>
      <button class="iconbtn" data-dev="${e.id}">✕</button></li>`).join("") || `<li class="muted">Nothing logged.</li>`}</ul><button class="btn small" id="addev">＋ Log experience</button></div>
    <h2>Store data</h2><div class="card"><div class="fields">
      ${[["store_age_years", "Store age (years)"], ["total_sales", "Total sales"], ["rating", "Rating (0-5)"], ["response_rate", "Response %"], ["dispute_rate", "Refund/dispute %"]].map(([k, l]) => `<div><label>${l}</label><input data-sk="${k}" type="number" step="any" value="${s[k] ?? ""}"></div>`).join("")}
      <div><label>Type</label><select data-sk="is_factory"><option value="">Unknown</option><option value="true" ${s.is_factory === true ? "selected" : ""}>Factory</option><option value="false" ${s.is_factory === false ? "selected" : ""}>Reseller</option></select></div>
      <div class="full"><label>Notes</label><input data-sk="notes" value="${esc(s.notes || "")}"></div></div>
      <button class="btn small" id="saves" style="margin-top:10px">Save</button></div>
    <h2>Offers</h2><div class="card"><ul class="list">${s.offers.map(o => `<li><div class="grow"><a href="#/product/${o.product_id}" style="text-transform:capitalize">${esc(o.product)}</a><br><small>${esc((o.title_en || o.title || "").slice(0, 60))} · ${cny(o.price_cny)}</small></div></li>`).join("")}</ul>
      <small>Delivery estimate: ${esc(s.delivery.label)} (${esc(s.delivery.basis)})</small></div>`;
  $("#addev").onclick = () => sellerEventSheet(sid);
  $$("[data-dev]").forEach(b => b.onclick = async () => { await api(`/api/seller-events/${b.dataset.dev}`, { method: "DELETE" }); viewSeller(sid); });
  $("#saves").onclick = async () => {
    const body = {}; $$("[data-sk]").forEach(el => body[el.dataset.sk] = el.value);
    body.is_factory = body.is_factory === "" ? null : body.is_factory === "true";
    await api(`/api/sellers/${sid}`, { method: "PATCH", json: body }); toast("Saved"); viewSeller(sid);
  };
}

/* ---------- MORE ---------- */
function viewMore() {
  setTitle("More", false);
  const item = (href, ic, t, s) => `<a class="card row" href="${href}" style="color:inherit"><span style="font-size:24px">${ic}</span><div class="grow"><b>${t}</b><br><small>${s}</small></div><span class="muted">›</span></a>`;
  view.innerHTML = `<section class="hero"><h1>More</h1></section><div class="stack" style="margin-top:12px">
    ${item("#/shipments", "🚚", "Shipments", "Status, ETA, arrivals → stock")}
    ${item("#/report", "📊", "Weekly report", "What sold, real margins, reorder / stop")}
    ${item("#/sellers", "🏭", "Sellers", "Reliability scores + your experience")}
    ${item("#/import", "⬇️", "Import trends", "vidIQ JSON, paste box, run sources")}
    ${item("#/filter", "🧹", "Filter review", "See what was thrown away as noise")}
    ${item("#/calc", "🧮", "Price calculator", "Quick landing cost / selling price")}
    ${item("#/settings", "⚙️", "Settings", "Pricing rules, delivery, Telegram, demo data")}
    ${item("#/setup", "🔌", "Setup & integrations", "Keys, status, step-by-step instructions")}</div>`;
}

/* ---------- IMPORT ---------- */
async function viewImport() {
  setTitle("Import trends", true);
  const setup = await api("/api/setup");
  const ad = Object.fromEntries(setup.adapters.map(a => [a.name, a]));
  const v = ad.vidiq_import;
  view.innerHTML = `<section class="hero"><h1>Import trends</h1><p>vidIQ results, TikTok Creative Center / Pinterest Trends pastes, and live sources.</p></section>
    <div class="card" style="margin-top:12px"><h3>vidIQ JSON (from the weekly Claude task)</h3>
      <p class="muted" style="margin:0 0 6px">vidIQ credits this month: <b>${v.credits_used}</b> used / ${v.credits_left} left (150 free, 5 per call).</p>
      <textarea id="vj" placeholder='{"source":"vidiq","items":[{"platform":"tiktok","url":"...","title":"...","views":2200000,"language":"tr"}]}'></textarea>
      <button class="btn primary block" id="vgo" style="margin-top:8px">Import JSON</button><div id="vmsg"></div></div>
    <div class="card" style="margin-top:12px"><h3>Paste box</h3>
      <p class="muted" style="margin:0 0 6px">Paste hashtags, product names or links (one per line) from TikTok Creative Center or Pinterest Trends. Views like "1.2M" are picked up.</p>
      <textarea id="pb" placeholder="#squishy 1.2M&#10;crochet hamster plush&#10;https://www.pinterest.com/pin/..."></textarea>
      <div class="row" style="margin-top:8px"><select id="pl" style="width:auto"><option value="tr">Turkish</option><option value="ru">Russian</option><option value="en">English / other</option></select>
        <button class="btn primary" id="pgo" style="flex:1">Import</button></div><div id="pmsg"></div></div>
    <h2>Live sources</h2><div class="stack">${setup.adapters.filter(a => a.automatic).map(a => `<div class="card"><div class="row">
      <span class="status-dot ${a.last_status === "ok" ? "on" : a.last_status === "error" ? "err" : a.configured ? "" : "off"}"></span><b class="grow">${esc(a.label)}</b>
      <button class="btn small" data-run="${a.name}" ${a.configured ? "" : "disabled"}>Run now</button></div>
      <small>${esc(a.message || (a.configured ? "Ready" : "Not configured"))}${a.last_run ? " · " + fmtDate(a.last_run) : ""}</small></div>`).join("")}</div>`;
  const report = (el, r) => el.innerHTML = `<div class="ok" style="margin-top:8px">✓ ${r.received} items: ${r.product} products, ${r.noise} noise, ${r.unknown} unclear.
    ${r.products?.length ? "<br>Products: " + r.products.map(esc).join(", ") : ""}${r.alerts?.length ? "<br>🔔 " + r.alerts.map(a => esc(a.message)).join("<br>") : ""}</div>`;
  $("#vgo").onclick = async () => { try { report($("#vmsg"), await api("/api/import/trends/manual", { json: { json: $("#vj").value } })); } catch (e) { $("#vmsg").innerHTML = `<div class="warn">${esc(e.message)}</div>`; } };
  $("#pgo").onclick = async () => { try { report($("#pmsg"), await api("/api/import/paste", { json: { text: $("#pb").value, language: $("#pl").value } })); } catch (e) { $("#pmsg").innerHTML = `<div class="warn">${esc(e.message)}</div>`; } };
  $$("[data-run]").forEach(b => b.onclick = async () => { b.disabled = true; b.textContent = "Running…"; const r = await api(`/api/adapters/${b.dataset.run}/run`, { method: "POST" }); toast(`${r.status}: ${r.message || "done"}`, 4000); viewImport(); });
}

/* ---------- FILTER REVIEW ---------- */
async function viewFilter() {
  setTitle("Filter review", true);
  const tab = localGet("filter-tab", "noise");
  const list = await api(`/api/signals?classification=${tab}&limit=150`);
  view.innerHTML = `<section class="hero"><h1>Product / noise filter</h1><p>Lip syncs, dances, skits and food jokes are thrown away. Fix any mistakes here.</p></section>
    <div class="tabs" style="margin-top:10px">${["noise", "unknown", "product"].map(t => `<button class="chip ${t === tab ? "blue" : ""}" data-t="${t}">${cap(t)}</button>`).join("")}</div>
    <div class="card"><ul class="list">${list.map(s => `<li><div class="grow"><div>${esc(s.title.slice(0, 140))}</div>
      <small>${esc(s.platform)} · ${n0(s.views)} views · ${esc(s.class_reason || "")}${s.extracted_product ? " → " + esc(s.extracted_product) : ""}</small></div>
      ${tab !== "product" ? `<button class="btn small" data-prod="${s.id}">Product</button>` : `<button class="btn small ghost" data-noise="${s.id}">Noise</button>`}</li>`).join("") || `<li class="muted">Nothing here.</li>`}</ul></div>`;
  $$("[data-t]").forEach(b => b.onclick = () => { localSet("filter-tab", b.dataset.t); viewFilter(); });
  $$("[data-noise]").forEach(b => b.onclick = async () => { await api(`/api/signals/${b.dataset.noise}`, { method: "PATCH", json: { classification: "noise" } }); viewFilter(); });
  $$("[data-prod]").forEach(b => b.onclick = () => sheet(`<h3>Which product is it?</h3><label>Product name (English)</label><input id="nm" placeholder="e.g. squishy toys">
    <button class="btn primary block" id="go" style="margin-top:12px">Save</button>`, (el, close) => {
    $("#go", el).onclick = async () => { await api(`/api/signals/${b.dataset.prod}`, { method: "PATCH", json: { classification: "product", product_name: $("#nm", el).value } }); close(); viewFilter(); };
  }));
}

/* ---------- CALCULATOR ---------- */
function viewCalc() {
  setTitle("Price calculator", true);
  view.innerHTML = `<section class="hero"><h1>Price calculator</h1><p>Your rules: ${S.rules.yuan_to_tmt} TMT/¥, ${S.rules.cargo_tmt_per_kg} TMT/kg, ×${S.rules.markup}</p></section>
    <div class="card" style="margin-top:12px"><div class="fields"><div><label>Item price ¥</label><input id="p" type="number" step="any" inputmode="decimal" value="15"></div>
      <div><label>China shipping ¥</label><input id="s" type="number" step="any" inputmode="decimal" value="0"></div>
      <div><label>Weight kg (blank = estimate)</label><input id="w" type="number" step="any" inputmode="decimal"></div>
      <div><label>Markup ×</label><input id="m" type="number" step="0.1" value="${S.rules.markup}"></div>
      <div class="full"><label>Product name (for weight estimate)</label><input id="n" value="plush toy"></div></div></div>
    <div class="card" id="out" style="margin-top:12px"></div>`;
  const go = async () => {
    const q = new URLSearchParams({ price_cny: $("#p").value || 0, shipping_cny: $("#s").value || 0, name: $("#n").value, markup: $("#m").value || S.rules.markup });
    if ($("#w").value) q.set("weight_kg", $("#w").value);
    const r = await api("/api/pricing/calc?" + q);
    $("#out").innerHTML = [["Goods", r.goods_tmt], ["Cargo", r.cargo_tmt], ["Fee", r.fee_tmt], ["Loss buffer", r.buffer_tmt]].map(([k, v]) => `<div class="kv"><span>${k}</span><span>${money(v)}</span></div>`).join("") +
      `<div class="kv"><span>Landing cost</span><b>${money(r.landing_tmt)}</b></div><div class="kv"><span>Raw sell (×${r.rules.markup})</span><span>${money(r.raw_sell_tmt)}</span></div>
      <div class="kv"><span>Suggested price</span><b style="font-size:18px">${money(r.sell_tmt)}</b></div>
      <div class="kv"><span>Profit · margin</span><b style="color:var(--green)">${money(r.profit_tmt)} · ${r.margin_pct.toFixed(0)}%</b></div>
      <div class="kv"><span>Cargo share of cost</span><span>${r.cargo_pct_of_cost.toFixed(0)}%</span></div>
      ${r.weight_is_estimate ? `<div class="note">⚖️ Weight estimated at ${r.weight_kg} kg: ${esc(r.weight_note)}</div>` : ""}`;
  };
  $$("input", view).forEach(i => i.addEventListener("input", go)); go();
}

/* ---------- SETTINGS ---------- */
async function viewSettings() {
  setTitle("Settings", true);
  const d = await api("/api/settings");
  const st = d.settings, pr = st.pricing, dl = st.delivery, tr = st.trends, tg = st.telegram, fx = d.fx;
  const num = (id, label, val, step = "any") => `<div><label for="${id}">${label}</label><input id="${id}" type="number" step="${step}" value="${val}"></div>`;
  const trip = (k, label) => `<div class="full"><label>${label} (min / typical / max days)</label><div class="row">${dl[k].map((v, i) => `<input data-dl="${k}" data-i="${i}" type="number" value="${v}">`).join("")}</div></div>`;
  view.innerHTML = `<section class="hero"><h1>Settings</h1></section>
    <h2>Pricing rules</h2><div class="card"><div class="fields">
      ${num("yuan_to_tmt", "TMT per 1 CNY (my person)", pr.yuan_to_tmt)}${num("cargo_tmt_per_kg", "Cargo TMT per kg (real weight)", pr.cargo_tmt_per_kg)}
      ${num("fee_per_item_tmt", "Fee per item to my person (TMT)", pr.fee_per_item_tmt)}${num("loss_buffer", "Loss buffer (0.05 = 5%)", pr.loss_buffer)}
      ${num("markup", "Default markup ×", pr.markup)}${num("round_to_small", "Round to (under 100 TMT)", pr.round_to_small, 1)}
      ${num("round_to_large", "Round to (100 TMT and up)", pr.round_to_large, 1)}${num("usd_cny_rate", "CNY per USD (for USD view)", pr.usd_cny_rate)}</div>
      ${fx.cny_per_usd ? `<div class="info" style="margin-top:10px">Market today: 1 USD = ¥${fx.cny_per_usd} · official ${fx.tmt_per_usd_official ?? "—"} TMT${fx.note ? "<br>" + esc(fx.note) : ""}</div>` :
        `<small class="muted">Reference exchange rate loads daily from a free API (Setup → Run).</small>`}
      <button class="btn primary" id="savepr" style="margin-top:10px">Save pricing</button></div>
    <h2>Delivery defaults</h2><div class="card"><div class="fields">${trip("seller_dispatch_days", "Seller dispatch")}${trip("china_domestic_days", "China domestic transit")}${trip("cargo_transit_days", "Cargo to Ashgabat")}</div>
      <small class="muted">Once 2+ shipments have real dates, estimates learn from your history.</small><br><button class="btn primary" id="savedl" style="margin-top:10px">Save delivery</button></div>
    <h2>Trends</h2><div class="card"><div class="fields">${num("alert_threshold", "Alert when watched product crosses", tr.alert_threshold, 1)}
      ${num("min_independent_signals", "Independent signals needed for a trend", tr.min_independent_signals, 1)}
      <div class="full"><label>YouTube queries: Turkish (one per line)</label><textarea id="yq_tr" style="min-height:80px">${esc(tr.youtube_queries_tr.join("\n"))}</textarea></div>
      <div class="full"><label>YouTube queries: Russian</label><textarea id="yq_ru" style="min-height:70px">${esc(tr.youtube_queries_ru.join("\n"))}</textarea></div></div>
      <button class="btn primary" id="savetr" style="margin-top:10px">Save trends</button></div>
    <h2>Telegram channels (Ashgabat market)</h2><div class="card"><label>Public channel usernames, one per line (e.g. ashgabat_oyunjak)</label>
      <textarea id="chs" style="min-height:80px">${esc(tg.channels.join("\n"))}</textarea>
      <div class="fields">${num("digest_weekday", "Digest day (0=Mon … 6=Sun)", tg.digest_weekday, 1)}${num("digest_hour_utc", "Digest hour (UTC)", tg.digest_hour_utc, 1)}</div>
      <button class="btn primary" id="savetg" style="margin-top:10px">Save channels</button> <a class="btn" href="#/setup">Telegram bot setup →</a></div>
    <h2>AI budget</h2><div class="card"><div class="fields">${num("budget", "Monthly Claude budget (USD)", st.ai.monthly_budget_usd)}</div><button class="btn primary" id="saveai" style="margin-top:10px">Save</button></div>
    <h2>Demo data</h2><div class="card"><p class="muted" style="margin-top:0">Remove all demo products, offers, sellers, shipments and sales. Anything you entered yourself is kept.</p>
      <button class="btn danger" id="clear">🧹 Clear demo data (go live)</button> <button class="btn ghost" id="seed">Re-load demo data</button></div>
    <h2>Account</h2><div class="card"><button class="btn" id="logout">Log out</button></div>`;
  const val = id => parseFloat($("#" + id).value);
  $("#savepr").onclick = async () => { const body = {}; ["yuan_to_tmt", "cargo_tmt_per_kg", "fee_per_item_tmt", "loss_buffer", "markup", "round_to_small", "round_to_large", "usd_cny_rate"].forEach(k => body[k] = val(k)); S.rules = await api("/api/settings/pricing", { method: "PUT", json: body }); toast("Pricing saved: all prices recalculated"); };
  $("#savedl").onclick = async () => { const body = {}; $$("[data-dl]").forEach(i => { (body[i.dataset.dl] ||= [0, 0, 0])[+i.dataset.i] = +i.value; }); await api("/api/settings/delivery", { method: "PUT", json: body }); toast("Delivery saved"); };
  $("#savetr").onclick = async () => { await api("/api/settings/trends", { method: "PUT", json: { alert_threshold: val("alert_threshold"), min_independent_signals: val("min_independent_signals"), youtube_queries_tr: $("#yq_tr").value.split("\n").map(s => s.trim()).filter(Boolean), youtube_queries_ru: $("#yq_ru").value.split("\n").map(s => s.trim()).filter(Boolean) } }); toast("Saved"); };
  $("#savetg").onclick = async () => { await api("/api/settings/telegram", { method: "PUT", json: { channels: $("#chs").value.split("\n").map(s => s.trim().replace(/^@/, "").replace("https://t.me/", "")).filter(Boolean), digest_weekday: val("digest_weekday"), digest_hour_utc: val("digest_hour_utc") } }); toast("Saved (digest time applies after restart)"); };
  $("#saveai").onclick = async () => { await api("/api/settings/ai", { method: "PUT", json: { monthly_budget_usd: val("budget") } }); toast("Saved"); };
  $("#clear").onclick = async () => { if (!confirm("Delete all demo data?")) return; const r = await api("/api/mock/clear", { method: "POST" }); toast("Demo data cleared ✓"); location.hash = "#/"; };
  $("#seed").onclick = async () => { const r = await api("/api/mock/seed", { method: "POST" }); toast(r.seeded ? "Demo data loaded" : "Only works on an empty database"); };
  $("#logout").onclick = async () => { await api("/api/auth/logout", { method: "POST" }); location.reload(); };
}

/* ---------- SETUP ---------- */
async function viewSetup() {
  setTitle("Setup", true);
  const d = await api("/api/setup");
  const dot = s => `<span class="status-dot ${s === "connected" ? "on" : s === "error" ? "err" : "off"}"></span>`;
  const tg = d.telegram;
  const pending = tg.chats.filter(c => c.role === "pending");
  view.innerHTML = `<section class="hero"><h1>Setup & integrations</h1><p>Every key is optional and free. Paste keys into <code>.env</code> and restart: the feature turns on by itself.</p></section>
    <h2>Telegram bot</h2><div class="card">
      <div class="row">${dot(tg.bot.running ? "connected" : tg.bot.token_set ? "error" : "")}<b class="grow">${tg.bot.running ? "Connected as @" + esc(tg.bot.username) : tg.bot.token_set ? "Token set but not running" : "Telegram: not connected"}</b>
        <button class="btn small" id="restart">↻ Reconnect</button></div>
      ${tg.bot.error ? `<div class="warn">${esc(tg.bot.error)}</div>` : ""}
      ${tg.bot.running ? `<p class="info">Send <b>/start</b> to <a href="https://t.me/${esc(tg.bot.username)}" target="_blank" rel="noopener">@${esc(tg.bot.username)}</a>, then tap Approve below. Ask your person to do the same.</p>` : ""}
      ${pending.length ? `<h3 style="margin-top:10px">Waiting for approval</h3><ul class="list">${pending.map(c => `<li><div class="grow"><b>${esc(c.name || c.username || c.chat_id)}</b><br><small>chat ${c.chat_id}${c.username ? " · @" + esc(c.username) : ""}</small></div>
        <button class="btn small primary" data-ap="${c.chat_id}" data-role="owner">Approve as me</button><button class="btn small" data-ap="${c.chat_id}" data-role="person">My person</button></li>`).join("")}</ul>` : ""}
      <h3 style="margin-top:10px">Approved</h3><ul class="list">${tg.chats.filter(c => ["owner", "person"].includes(c.role)).map(c => `<li><div class="grow">${esc(c.name || c.chat_id)} <span class="chip ${c.role === "owner" ? "blue" : "green"}">${c.role === "owner" ? "me" : "my person"}</span></div>
        <button class="btn small ghost" data-rmc="${c.chat_id}">Remove</button></li>`).join("")}${tg.env_allowed.map(id => `<li><div class="grow">${id} <span class="chip">from .env</span></div></li>`).join("") || ""}</ul>
      ${tg.bot.running ? `<button class="btn small" id="digest">Send digest now</button>` : ""}<button class="btn small ghost" id="refresh">↻ Check for new chats</button></div>
    <h2>Integrations</h2><div class="stack">${d.integrations.map(i => `<details class="card" ${i.status !== "connected" && ["telegram_bot", "claude"].includes(i.key) ? "open" : ""}>
      <summary class="row" style="cursor:pointer;list-style:none">${dot(i.status)}<b class="grow">${esc(i.label)}</b><span class="chip ${i.status === "connected" ? "green" : i.status === "error" ? "red" : ""}">${i.status}</span></summary>
      <p><b>Unlocks:</b> ${esc(i.unlocks)}</p><p class="muted"><b>Without it:</b> ${esc(i.without)}</p>
      ${i.key === "claude" && i.connected ? `<p class="info">This month: $${i.month_spend_usd} of $${i.budget_usd} budget · ${esc(i.model_small)} / ${esc(i.model_vision)}</p>` : ""}
      <ol>${i.steps.map(s => `<li>${esc(s)}</li>`).join("")}</ol>
      <small>.env: ${i.env.map(e => `<code>${e}</code>${i.missing.includes(e) ? " ✗" : " ✓"}`).join(" · ")}</small></details>`).join("")}</div>
    <h2>Data sources</h2><div class="card"><ul class="list">${d.adapters.map(a => `<li>${dot(a.last_status === "ok" ? "connected" : a.last_status === "error" ? "error" : "")}<div class="grow"><b>${esc(a.label)}</b><br>
      <small>${esc(a.free_note)}</small><br><small>${esc(a.message || "")}${a.last_run ? " · last " + fmtDate(a.last_run) : ""}${a.retry_after ? " · retry " + fmtDate(a.retry_after) : ""}</small></div></li>`).join("")}</ul></div>`;
  $("#restart").onclick = async () => { const r = await api("/api/telegram/restart", { method: "POST" }); toast(r.running ? "Bot connected ✓" : (r.error || "No token in .env"), 4000); viewSetup(); };
  $("#refresh").onclick = viewSetup;
  const dg = $("#digest"); if (dg) dg.onclick = async () => { const r = await api("/api/telegram/digest", { method: "POST" }); toast(`Digest sent to ${r.sent} chat(s)`); };
  $$("[data-ap]").forEach(b => b.onclick = async () => { await api(`/api/telegram/chats/${b.dataset.ap}/approve`, { json: { role: b.dataset.role } }); toast("Approved ✓"); viewSetup(); });
  $$("[data-rmc]").forEach(b => b.onclick = async () => { await api(`/api/telegram/chats/${b.dataset.rmc}`, { method: "DELETE" }); viewSetup(); });
}

/* ---------- LOGIN ---------- */
function renderLogin() {
  setTitle("Sourcing Copilot", false);
  view.innerHTML = `<div class="login card"><h1>🔒 Sign in</h1><p class="muted">Enter your app password.</p>
    <input id="pw" type="password" autocomplete="current-password"><button class="btn primary block" id="go" style="margin-top:10px">Sign in</button><div id="err"></div></div>`;
  const go = async () => { try { await api("/api/auth/login", { json: { password: $("#pw").value } }); boot(); } catch { $("#err").innerHTML = `<div class="warn">Wrong password</div>`; } };
  $("#go").onclick = go; $("#pw").onkeydown = e => { if (e.key === "Enter") go(); };
}

/* ---------- boot ---------- */
async function boot() {
  applyTheme(); paintCurrency();
  const a = await api("/api/auth/status");
  if (!a.logged_in) return renderLogin();
  try { const s = await api("/api/settings"); S.rules = s.settings.pricing; } catch { }
  router(); refreshBadge();
}
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => { });
boot();
