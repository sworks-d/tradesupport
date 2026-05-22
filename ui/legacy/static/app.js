"use strict";

const yen = (n) =>
  "¥" + Math.round(n || 0).toLocaleString("ja-JP");
const pct = (n) => (n >= 0 ? "+" : "") + (n * 100).toFixed(1) + "%";

async function load() {
  let data;
  try {
    const res = await fetch("/api/dashboard");
    data = await res.json();
  } catch (e) {
    document.getElementById("total-assets").textContent = "API エラー";
    return;
  }
  renderSummary(data.summary, data.snapshots);
  renderSparkline(data.snapshots);
  renderSell(data.sell_recommendations);
  renderBuy(data.buy_recommendations);
  renderHoldings(data.holdings);
  renderTopics(data.topics);
  document.getElementById("updated").textContent =
    "更新: " + new Date().toLocaleString("ja-JP");
}

function renderSummary(s, snaps) {
  s = s || {};
  document.getElementById("total-assets").textContent = yen(s.total_assets_jpy);
  const pnlEl = document.getElementById("daily-pnl");
  const pnl = s.daily_pnl_jpy || 0;
  pnlEl.textContent = "前日比 " + (pnl >= 0 ? "+" : "") + yen(pnl);
  pnlEl.className = "pnl " + (pnl >= 0 ? "pos" : "neg");
  document.getElementById("core-val").textContent = yen(s.core_value_jpy);
  document.getElementById("sat-val").textContent = yen(s.satellite_value_jpy);
  document.getElementById("cash-val").textContent = yen(s.cash_jpy);
  document.getElementById("holding-count").textContent = (s.holding_count || 0) + " 銘柄";
}

function renderSparkline(snaps) {
  const svg = document.getElementById("sparkline");
  if (!snaps || snaps.length < 2) return;
  const vals = snaps.map((s) => s.total_assets_jpy);
  const min = Math.min(...vals), max = Math.max(...vals);
  const range = max - min || 1;
  const pts = vals
    .map((v, i) => {
      const x = (i / (vals.length - 1)) * 240;
      const y = 58 - ((v - min) / range) * 54;
      return x.toFixed(1) + "," + y.toFixed(1);
    })
    .join(" ");
  const up = vals[vals.length - 1] >= vals[0];
  svg.innerHTML =
    `<polyline fill="none" stroke="${up ? "#3fb950" : "#e5484d"}" stroke-width="2" points="${pts}" />`;
}

function card(html) {
  const d = document.createElement("div");
  d.className = "card";
  d.innerHTML = html;
  return d;
}

function renderSell(items) {
  const el = document.getElementById("sell-cards");
  el.innerHTML = "";
  if (!items || !items.length) return (el.innerHTML = '<div class="empty">売り推奨はありません</div>');
  for (const s of items) {
    const label = s.signal_type === "stop_loss" ? "損切り" : "利確";
    const reasons = (s.reasons || []).map((r) => `<li>${r.text || r}</li>`).join("");
    const act = s.recommended_action || {};
    el.appendChild(
      card(
        `<div class="row1"><span class="ticker">${s.ticker}</span><span class="score">${s.score}</span></div>
         <div class="tag">${label}${act.note ? " ・ " + act.note : ""}</div>
         <ul>${reasons}</ul>`
      )
    );
  }
}

function renderBuy(items) {
  const el = document.getElementById("buy-cards");
  el.innerHTML = "";
  if (!items || !items.length) return (el.innerHTML = '<div class="empty">買い推奨はありません</div>');
  for (const b of items) {
    el.appendChild(
      card(
        `<div class="row1"><span class="ticker">${b.ticker}</span><span class="score">${b.score}</span></div>
         <div class="tag">${b.strategy_category} ・ 目標 ${b.target_period_days}日 ・ 期待 ${pct(b.expected_return)}</div>
         <div class="meta">エントリー ${b.entry_price} → 目標 ${b.target_price} ・ 推奨 ${yen(b.recommended_amount_jpy)}</div>`
      )
    );
  }
}

function renderHoldings(items) {
  const el = document.getElementById("holdings-grid");
  el.innerHTML = "";
  if (!items || !items.length) return (el.innerHTML = '<div class="empty">保有銘柄はありません</div>');
  for (const h of items) {
    const up = (h.pnl_pct || 0) >= 0;
    const d = document.createElement("div");
    d.className = "hcard " + (up ? "up" : "down");
    d.innerHTML =
      `<div class="ht"><span class="ticker">${h.ticker}</span>
        <span class="pnl ${up ? "pos" : "neg"}">${pct(h.pnl_pct)}</span></div>
       <div class="sub">${h.strategy_category} ・ ${h.qty}株 @ ${h.buy_price} → ${h.current_price}</div>
       <div class="sub">${h.thesis || ""}</div>`;
    el.appendChild(d);
  }
}

function renderTopics(items) {
  const el = document.getElementById("topic-list");
  el.innerHTML = "";
  if (!items || !items.length) return (el.innerHTML = '<div class="empty">トピックスはありません</div>');
  for (const t of items) {
    const tickers = (t.affected_tickers || []).join(", ");
    const d = document.createElement("div");
    d.className = "topic";
    d.innerHTML =
      `<div class="th"><span class="imp ${t.importance}">${t.importance}</span>
        <span class="cat">${t.category}${tickers ? " ・ " + tickers : ""}</span></div>
       <div class="headline">${t.headline}</div>
       <div class="summary">${t.summary || ""}</div>
       ${t.source_url ? `<a href="${t.source_url}" target="_blank" rel="noopener">${t.source || "ソース"} ↗</a>` : ""}`;
    el.appendChild(d);
  }
}

load();
