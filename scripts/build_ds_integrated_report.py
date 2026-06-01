"""DS 統合レポート（1 日 2 回・朝 / 夕）。

各 DS 機（REI/ASUKA/SHINJI/KAWORU）の状態を統合して 1 つの HTML にまとめる：
  - サマリー: 機別 配分・PnL・保有銘柄数・損益率
  - 進捗: 評価データ蓄積件数、各機の hit_rate（n が貯まったら）、過去 N 日の PnL 推移
  - 展望: 各機の保有銘柄の現状（含み損益・目標日まで・stop 距離）と「次に起こりそうなこと」

出力: autoreport/ds_integrated/YYYY-MM-DD_HHMM.html
       autoreport/ds_integrated/latest.html （シンボリックリンク的に最新を上書き）

実行:
    uv run python scripts/build_ds_integrated_report.py            # 現時刻でレポート
    uv run python scripts/build_ds_integrated_report.py --slot AM  # AM/PM タグ付き
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.db import create_all, get_engine
from trading_agent.models.decisions import Decision
from trading_agent.models.misato_treasury import MisatoTreasury, PilotAllocation
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.universe import Universe
from trading_agent.portfolio.misato import treasury_view
from trading_agent.portfolio.personality import RULE_SUMMARY, all_personalities
from trading_agent.utils.time_utils import utcnow


def _load_pilot_data(engine: Engine) -> list[dict[str, Any]]:
    """各機の保有・配分・PnL を収集（snapshot 経由でも良いが、ここは DB 直接）。"""
    with Session(engine) as s:
        ports = s.exec(select(Portfolio).where(col(Portfolio.status) == "active")).all()
        allocs = {a.pilot_name: a.allocated_jpy for a in s.exec(select(PilotAllocation)).all()}
        uni_rows = s.exec(select(Universe)).all()
    name_by_ticker = {u.ticker: (u.name_ja or u.name or u.ticker) for u in uni_rows}

    # 価格は snapshot.json から取得（最新の yfinance 結果を流用）
    snap_path = Path(__file__).resolve().parent.parent / "ui" / "public" / "data" / "snapshot.json"
    price_by_ticker: dict[str, float] = {}
    if snap_path.exists():
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
        for pilot in snap.get("dummy_system", {}).get("personalities", []):
            for h in pilot.get("holdings", []):
                price_by_ticker[h["ticker"]] = float(h["current_price"])

    out: list[dict[str, Any]] = []
    for pers in all_personalities():
        rows = [p for p in ports if p.personality == pers.name]
        invested = sum(float(r.buy_price or 0) * float(r.qty or 0) for r in rows)
        market_value = 0.0
        for r in rows:
            cur = price_by_ticker.get(r.ticker, float(r.buy_price or 0))
            market_value += cur * float(r.qty or 0)
        overlay = float(allocs.get(pers.name, 0.0))
        cash = overlay - invested
        total = cash + market_value
        pnl = total - overlay
        pnl_pct = (pnl / overlay * 100.0) if overlay else 0.0

        holdings = []
        for r in rows:
            cur = price_by_ticker.get(r.ticker, float(r.buy_price or 0))
            qty = float(r.qty or 0)
            cost = float(r.buy_price or 0) * qty
            mkt = cur * qty
            unrealized_pct = ((cur - r.buy_price) / r.buy_price * 100) if r.buy_price else 0.0
            days_to_exit = (r.target_date - dt.date.today()).days if r.target_date else None
            stop_pct = float(r.stop_loss_pct or 0)
            stop_price = float(r.buy_price or 0) * (1.0 - stop_pct)
            stop_distance_pct = ((cur - stop_price) / cur * 100) if cur else 0.0
            holdings.append({
                "ticker": r.ticker,
                "name": name_by_ticker.get(r.ticker, r.ticker),
                "qty": int(qty),
                "buy_price": float(r.buy_price or 0),
                "current_price": cur,
                "market_value": mkt,
                "unrealized": mkt - cost,
                "unrealized_pct": unrealized_pct,
                "days_to_exit": days_to_exit,
                "stop_distance_pct": stop_distance_pct,
            })
        out.append({
            "name": pers.name,
            "label": pers.label,
            "icon": pers.icon,
            "description": pers.description,
            "rule_summary": RULE_SUMMARY.get(pers.name, ""),
            "horizon_days": pers.horizon_days,
            "stop_loss_pct": pers.stop_loss_pct,
            "overlay_cash_jpy": overlay,
            "holdings_count": len(rows),
            "invested_jpy": invested,
            "cash_jpy": cash,
            "market_value_jpy": market_value,
            "total_value_jpy": total,
            "pnl_jpy": pnl,
            "pnl_pct": pnl_pct,
            "holdings": holdings,
        })
    return out


def _eval_stats(engine: Engine) -> dict[str, Any]:
    """評価データ集計（D-23 ゲート進捗）。"""
    with Session(engine) as s:
        decs = s.exec(select(Decision)).all()
    evaluated = [d for d in decs if d.evaluated_at]
    outcomes = Counter(d.hit_or_miss for d in evaluated)
    return {
        "total_decisions": len(decs),
        "evaluated": len(evaluated),
        "hit": outcomes.get("hit", 0),
        "miss": outcomes.get("miss", 0),
        "neutral": outcomes.get("neutral", 0),
        "pending": outcomes.get("pending", 0),
    }


def _build_outlook(pilots: list[dict[str, Any]], stats: dict[str, Any]) -> list[str]:
    """各機の展望を生成（決定論ベース）。"""
    out: list[str] = []
    # 全体評価
    n_eval = stats["evaluated"]
    if n_eval == 0:
        out.append(
            "📌 評価データはまだ蓄積中（0 件）。評価期日（60-180 日後）の到来を待って "
            "hit_rate / avg_R が分離していく段階。"
        )
    elif n_eval < 10:
        out.append(
            f"📌 評価データ {n_eval} 件蓄積中。D-23 ゲート（n≥30）まで {30 - n_eval} 件不足。"
        )
    elif n_eval < 30:
        hr = stats["hit"] / max(stats["hit"] + stats["miss"], 1) * 100
        out.append(
            f"📌 評価データ {n_eval} 件（暫定 hit_rate {hr:.0f}%）。"
            f"D-23 ゲート（n≥30）まで {30 - n_eval} 件。"
        )
    else:
        hr = stats["hit"] / max(stats["hit"] + stats["miss"], 1) * 100
        out.append(f"🎖 評価データ {n_eval} 件達成・hit_rate {hr:.0f}%。D-23 ゲート判定可能。")

    # 機別展望
    for p in pilots:
        name = p["label"]
        n = p["holdings_count"]
        pnl_pct = p["pnl_pct"]
        if n == 0:
            out.append(f"{p['icon']} {name}: 保有なし。次の MISATO dispatch で候補申請があれば fill 可能。")
            continue
        # stop 接近銘柄
        near_stop = [h for h in p["holdings"] if h["stop_distance_pct"] < 3.0]
        near_target = [h for h in p["holdings"] if (h["days_to_exit"] or 999) < 7]
        if near_stop:
            tickers = ", ".join(h["ticker"] for h in near_stop[:3])
            out.append(f"⚠ {name}: {tickers} が stop ライン至近（3% 以内）。撤退要警戒。")
        if near_target:
            tickers = ", ".join(h["ticker"] for h in near_target[:3])
            out.append(f"⏰ {name}: {tickers} の保有期限が 1 週間以内。time_exit 自動売却が発火する見込み。")
        if pnl_pct > 5.0:
            out.append(f"📈 {name}: PnL {pnl_pct:+.2f}% 好調。次回 dispatch で max_position_pct ボーナス（+20%）適用。")
        elif pnl_pct < -5.0:
            out.append(f"📉 {name}: PnL {pnl_pct:+.2f}% 軟調。次回 dispatch で max_position_pct ディスカウント（-20%）適用。")
    return out


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
  body {{ background: #1a1a1f; color: #ece9de; font-family: -apple-system, "Hiragino Sans", sans-serif; padding: 24px; max-width: 1200px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .subtitle {{ color: #888; font-size: 12px; margin-bottom: 20px; }}
  .card {{ background: #252530; border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; padding: 18px 20px; margin-bottom: 16px; }}
  h2 {{ font-size: 14px; letter-spacing: 1px; color: #a8a89e; margin: 0 0 12px; font-weight: 600; }}
  .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin-bottom: 16px; }}
  .pilot-card {{ background: #2c2c38; border-radius: 4px; padding: 12px; border-top: 3px solid #888; }}
  .pilot-card.REI {{ border-top-color: #4a90e2; }}
  .pilot-card.ASUKA {{ border-top-color: #e74c3c; }}
  .pilot-card.SHINJI {{ border-top-color: #9b59b6; }}
  .pilot-card.KAWORU {{ border-top-color: #5d3fd3; }}
  .pilot-head {{ display: flex; justify-content: space-between; align-items: baseline; }}
  .pilot-name {{ font-weight: 700; font-size: 14px; }}
  .pnl-big {{ font-weight: 700; font-size: 16px; padding: 2px 8px; border-radius: 3px; }}
  .pnl-big.up {{ background: rgba(74,222,128,0.15); color: #4ade80; }}
  .pnl-big.down {{ background: rgba(248,113,113,0.15); color: #f87171; }}
  .pnl-big.flat {{ background: rgba(255,255,255,0.05); color: #a8a89e; }}
  .pilot-stats {{ display: flex; gap: 12px; margin-top: 6px; font-size: 11px; color: #a8a89e; flex-wrap: wrap; }}
  .pilot-stats b {{ color: #ece9de; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 11px; margin-top: 8px; }}
  th, td {{ padding: 5px 8px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.05); }}
  th {{ color: #888; font-weight: 600; font-size: 10px; text-transform: uppercase; }}
  .up {{ color: #4ade80; }}
  .down {{ color: #f87171; }}
  .outlook li {{ margin-bottom: 6px; font-size: 13px; line-height: 1.5; }}
  .total-row {{ background: rgba(255,255,255,0.04); font-weight: 600; }}
  .misato-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }}
  .misato-stat {{ background: rgba(93,63,211,0.08); border-radius: 4px; padding: 10px 12px; border-left: 3px solid #6a5acd; }}
  .misato-stat .lbl {{ font-size: 10px; color: #888; text-transform: uppercase; }}
  .misato-stat .val {{ font-size: 16px; font-weight: 700; color: #ece9de; margin-top: 2px; }}
  .total-pnl-hero {{ display: flex; gap: 24px; align-items: baseline; padding: 16px 20px; background: linear-gradient(135deg, rgba(93,63,211,0.1), rgba(74,222,128,0.05)); border-radius: 6px; margin-bottom: 16px; }}
  .total-pnl-hero .lbl {{ font-size: 11px; color: #888; }}
  .total-pnl-hero .seed-val {{ font-size: 22px; font-weight: 700; color: #b9a3ff; }}
  .total-pnl-hero .pnl-val {{ font-size: 24px; font-weight: 700; }}
  .total-pnl-hero .pnl-val.up {{ color: #4ade80; }}
  .total-pnl-hero .pnl-val.down {{ color: #f87171; }}
</style>
</head>
<body>

<h1>🤖 DS 統合レポート — {slot_label}</h1>
<div class="subtitle">生成: {generated_at}</div>

<!-- HERO: MISATO 投入額 vs DS 全体損益 -->
<div class="total-pnl-hero">
  <div>
    <div class="lbl">💰 MISATO 投入額（seed）</div>
    <div class="seed-val">¥{seed:,}</div>
  </div>
  <div style="font-size:18px;color:#888;">→</div>
  <div>
    <div class="lbl">📊 DS 全体時価</div>
    <div class="seed-val">¥{total_value:,}</div>
  </div>
  <div style="font-size:18px;color:#888;">=</div>
  <div>
    <div class="lbl">📈 損益</div>
    <div class="pnl-val {total_pnl_cls}">{total_pnl_str} ({total_pnl_pct_str})</div>
  </div>
</div>

<!-- MISATO セクション -->
<div class="card" style="border-left: 4px solid #5d3fd3;">
  <h2>🎖 MISATO 司令塔 — 財務状態</h2>
  <div class="misato-grid">
    <div class="misato-stat">
      <div class="lbl">預かり金 (seed)</div>
      <div class="val">¥{seed:,}</div>
    </div>
    <div class="misato-stat">
      <div class="lbl">配分済 (allocated)</div>
      <div class="val">¥{allocated:,}</div>
    </div>
    <div class="misato-stat">
      <div class="lbl">未配分 (available)</div>
      <div class="val">¥{available:,}</div>
    </div>
    <div class="misato-stat">
      <div class="lbl">入金回数</div>
      <div class="val">{deposit_count} 回</div>
    </div>
    <div class="misato-stat">
      <div class="lbl">直近入金</div>
      <div class="val" style="font-size:12px;">{last_deposit_at}</div>
    </div>
    <div class="misato-stat">
      <div class="lbl">自動売買マスター</div>
      <div class="val" style="font-size:13px;">{master_auto_status}</div>
    </div>
  </div>
  <div style="margin-top:12px;font-size:11px;color:#888;">
    安全装置: 1 命令上限 ¥500,000 / 1 機上限 ¥200,000 / HALT ファイル / dry-run→approve 2 段
  </div>

  <h2 style="margin-top:18px;">📤 MISATO の配分内訳（どの機にいくら振ったか）</h2>
  <table>
    <tr>
      <th>機体</th>
      <th>配分額</th>
      <th>配分率</th>
      <th>投入済</th>
      <th>投入率（配分内）</th>
      <th>残現金（未使用）</th>
      <th>状態</th>
    </tr>
    {alloc_breakdown_rows}
    <tr class="total-row">
      <td>合計</td>
      <td>¥{total_overlay:,}</td>
      <td>100%</td>
      <td>¥{total_invested:,}</td>
      <td>{total_invested_pct:.0f}%</td>
      <td>¥{total_cash:,}</td>
      <td></td>
    </tr>
  </table>
  {misato_plan_block}
</div>

<div class="card">
  <h2>📊 DS 機別 配分 + 損益 サマリー</h2>
  <div class="summary-grid">
    {pilot_cards}
  </div>
  <table>
    <tr><th>機体</th><th>配分（元本）</th><th>投入</th><th>残現金</th><th>時価</th><th>合計</th><th>損益 ¥</th><th>損益 %</th></tr>
    {summary_rows}
    <tr class="total-row"><td>合計</td><td>¥{total_overlay:,}</td><td>¥{total_invested:,}</td><td>¥{total_cash:,}</td><td>¥{total_mv:,}</td><td>¥{total_value:,}</td><td class="{total_pnl_cls}">{total_pnl_str}</td><td class="{total_pnl_cls}">{total_pnl_pct_str}</td></tr>
  </table>
</div>

<div class="card">
  <h2>📈 進捗（D-23 評価データ蓄積）</h2>
  <div style="display: flex; gap: 20px; font-size: 13px;">
    <div>📝 総 decision: <b>{total_decisions}</b> 件</div>
    <div>✅ 評価済: <b>{evaluated}</b> 件</div>
    <div>🎯 hit: <b style="color:#4ade80;">{hit}</b></div>
    <div>❌ miss: <b style="color:#f87171;">{miss}</b></div>
    <div>➖ neutral: <b>{neutral}</b></div>
    <div>⏳ pending: <b>{pending}</b></div>
  </div>
  <div style="margin-top:8px;font-size:11px;color:#888;">
    D-23 ゲート進捗: n {evaluated}/30 件・hit_rate ≥50%・avg_R ≥+0.5 で「正式推奨（昇格）」に到達。
  </div>
</div>

<div class="card">
  <h2>🔮 展望（次に起こりそうなこと）</h2>
  <ul class="outlook">
    {outlook_items}
  </ul>
</div>

<div class="card">
  <h2>🎫 DS 所持銘柄 一覧（機別損益サマリー付き）</h2>
  {holdings_tables}
</div>

</body>
</html>
"""


def _render_pilot_card(p: dict[str, Any]) -> str:
    pnl_cls = "up" if p["pnl_jpy"] > 0 else ("down" if p["pnl_jpy"] < 0 else "flat")
    pnl_sign = "+" if p["pnl_jpy"] >= 0 else ""
    return f"""
    <div class="pilot-card {p['name']}">
      <div class="pilot-head">
        <span class="pilot-name">{p['icon']} {p['label']}</span>
        <span class="pnl-big {pnl_cls}">{pnl_sign}¥{p['pnl_jpy']:,.0f} ({pnl_sign}{p['pnl_pct']:.2f}%)</span>
      </div>
      <div class="pilot-stats">
        <span>配分 <b>¥{p['overlay_cash_jpy']:,.0f}</b></span>
        <span>保有 <b>{p['holdings_count']}</b> 銘柄</span>
        <span>投入 <b>¥{p['invested_jpy']:,.0f}</b></span>
        <span>残現金 <b>¥{p['cash_jpy']:,.0f}</b></span>
      </div>
    </div>
    """


def _render_summary_row(p: dict[str, Any]) -> str:
    pnl_cls = "up" if p["pnl_jpy"] > 0 else "down" if p["pnl_jpy"] < 0 else ""
    pnl_sign = "+" if p["pnl_jpy"] >= 0 else ""
    return f"""<tr>
      <td>{p['icon']} {p['label']}</td>
      <td>¥{p['overlay_cash_jpy']:,.0f}</td>
      <td>¥{p['invested_jpy']:,.0f}</td>
      <td>¥{p['cash_jpy']:,.0f}</td>
      <td>¥{p['market_value_jpy']:,.0f}</td>
      <td>¥{p['total_value_jpy']:,.0f}</td>
      <td class="{pnl_cls}">{pnl_sign}¥{p['pnl_jpy']:,.0f}</td>
      <td class="{pnl_cls}">{pnl_sign}{p['pnl_pct']:.2f}%</td>
    </tr>"""


def _render_holdings_table(p: dict[str, Any]) -> str:
    if not p["holdings"]:
        return f"""<div style="margin-bottom:12px;"><b>{p['icon']} {p['label']}</b>: 保有なし</div>"""
    rows = "".join(
        f"""<tr>
          <td>{h['ticker']}</td>
          <td>{h['name']}</td>
          <td>{h['qty']}</td>
          <td>¥{h['buy_price']:,.0f}</td>
          <td>¥{h['current_price']:,.0f}</td>
          <td>¥{h['market_value']:,.0f}</td>
          <td class="{'up' if h['unrealized'] >= 0 else 'down'}">{'+' if h['unrealized'] >= 0 else ''}¥{h['unrealized']:,.0f} ({h['unrealized_pct']:+.2f}%)</td>
          <td>{h['days_to_exit'] if h['days_to_exit'] is not None else '—'} 日</td>
          <td>{h['stop_distance_pct']:+.1f}%</td>
        </tr>"""
        for h in p["holdings"]
    )
    return f"""<div style="margin-bottom:14px;">
      <b>{p['icon']} {p['label']}</b> ({p['holdings_count']} 銘柄・PnL <span class="{'up' if p['pnl_jpy'] >= 0 else 'down'}">¥{p['pnl_jpy']:+,.0f} ({p['pnl_pct']:+.2f}%)</span>)
      <table>
        <tr><th>コード</th><th>会社名</th><th>株数</th><th>取得</th><th>現在</th><th>時価</th><th>含み損益</th><th>期限</th><th>stop 距離</th></tr>
        {rows}
      </table>
    </div>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", choices=("AM", "PM"), default=None, help="AM (朝) or PM (夕)")
    ap.add_argument("--out-dir", default="autoreport/ds_integrated")
    args = ap.parse_args()

    engine = get_engine(Path("data") / "trading.sqlite")
    create_all(engine)

    pilots = _load_pilot_data(engine)
    stats = _eval_stats(engine)
    outlook = _build_outlook(pilots, stats)
    treasury = treasury_view(engine)
    from trading_agent.portfolio.misato import auto_trade_view
    auto_trade = auto_trade_view(engine)
    now = dt.datetime.now()

    # サマリー集計
    total_overlay = sum(p["overlay_cash_jpy"] for p in pilots)
    total_invested = sum(p["invested_jpy"] for p in pilots)
    total_cash = sum(p["cash_jpy"] for p in pilots)
    total_mv = sum(p["market_value_jpy"] for p in pilots)
    total_value = sum(p["total_value_jpy"] for p in pilots)
    total_pnl = sum(p["pnl_jpy"] for p in pilots)
    total_pnl_pct = (total_pnl / total_overlay * 100) if total_overlay else 0.0
    total_pnl_cls = "up" if total_pnl > 0 else "down" if total_pnl < 0 else ""
    total_pnl_str = f"{'+' if total_pnl >= 0 else ''}¥{total_pnl:,.0f}"
    total_pnl_pct_str = f"{'+' if total_pnl_pct >= 0 else ''}{total_pnl_pct:.2f}%"

    slot_label = args.slot or ("AM" if now.hour < 12 else "PM")
    slot_jp = "朝の進捗" if slot_label == "AM" else "夕の進捗"

    # MISATO 配分内訳
    total_invested_pct = (total_invested / total_overlay * 100) if total_overlay else 0.0

    def _alloc_row(p: dict[str, Any]) -> str:
        alloc_pct = (p["overlay_cash_jpy"] / total_overlay * 100) if total_overlay else 0.0
        invested_within = (p["invested_jpy"] / p["overlay_cash_jpy"] * 100) if p["overlay_cash_jpy"] else 0.0
        status = "✅ 配分済" if p["overlay_cash_jpy"] > 0 else "⏸ 未配分"
        if p["overlay_cash_jpy"] > 0 and p["holdings_count"] == 0:
            status = "💼 配分済（未投資）"
        return f"""<tr>
          <td>{p['icon']} {p['label']}</td>
          <td>¥{p['overlay_cash_jpy']:,.0f}</td>
          <td>{alloc_pct:.1f}%</td>
          <td>¥{p['invested_jpy']:,.0f}</td>
          <td>{invested_within:.0f}%</td>
          <td>¥{p['cash_jpy']:,.0f}</td>
          <td>{status}</td>
        </tr>"""

    alloc_breakdown_rows = "\n".join(_alloc_row(p) for p in pilots)

    # 自動売買状態
    master = auto_trade["master"]
    if master["active"]:
        rem = master["remaining_minutes"]
        master_auto_status = f"🟢 ON (残 {rem // 60}h)"
    else:
        master_auto_status = "⚫ OFF"

    # MISATO 最新 dispatch plan（snapshot.json から）
    misato_plan_block = ""
    snap_path = Path(__file__).resolve().parent.parent / "ui" / "public" / "data" / "snapshot.json"
    if snap_path.exists():
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
        plan = (snap.get("misato") or {}).get("plan")
        if plan:
            picked = plan.get("picked_count", 0)
            short = plan.get("shortlist_count", 0)
            assignments = plan.get("assignments", [])
            picked_list = [a for a in assignments if a.get("picked")]
            picked_summary = "<br>".join(
                f"  {a['ticker']} → {a['assigned_to']} ¥{a['proposed_budget_jpy']:,} "
                f"({a.get('source', 'magi')}{' / ' + a['preset'] if a.get('preset') else ''})"
                for a in picked_list[:12]
            )
            misato_plan_block = f"""
              <h2 style="margin-top:18px;">📋 最新 MISATO 配分案</h2>
              <div style="font-size:12px;color:#a8a89e;">
                配分方式: {plan['allocation'].get('mode', 'needs-based')}（{plan['allocation'].get('reason', '')}）<br>
                割当 {len(assignments)} 件・実 fill {picked} 件・shortlist {short} 件
              </div>
              <div style="margin-top:8px;font-size:11px;color:#ece9de;font-family:'JetBrains Mono',ui-monospace,monospace;">
                {picked_summary}
              </div>
            """
        else:
            misato_plan_block = '<div style="margin-top:14px;color:#888;font-size:12px;">📋 配分案: 未配分残高なし or HALT 中</div>'

    html = _HTML_TEMPLATE.format(
        title=f"DS 統合レポート {now.strftime('%Y-%m-%d')} {slot_label}",
        slot_label=f"{slot_jp}（{now.strftime('%Y-%m-%d %H:%M')}）",
        generated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        seed=int(treasury["seed_jpy"]),
        allocated=int(treasury["allocated_jpy"]),
        available=int(treasury["available_jpy"]),
        deposit_count=treasury.get("deposit_count", 0),
        last_deposit_at=treasury.get("last_deposit_at") or "—",
        master_auto_status=master_auto_status,
        alloc_breakdown_rows=alloc_breakdown_rows,
        total_invested_pct=total_invested_pct,
        misato_plan_block=misato_plan_block,
        pilot_cards="\n".join(_render_pilot_card(p) for p in pilots),
        summary_rows="\n".join(_render_summary_row(p) for p in pilots),
        total_overlay=int(total_overlay),
        total_invested=int(total_invested),
        total_cash=int(total_cash),
        total_mv=int(total_mv),
        total_value=int(total_value),
        total_pnl_cls=total_pnl_cls,
        total_pnl_str=total_pnl_str,
        total_pnl_pct_str=total_pnl_pct_str,
        total_decisions=stats["total_decisions"],
        evaluated=stats["evaluated"],
        hit=stats["hit"],
        miss=stats["miss"],
        neutral=stats["neutral"],
        pending=stats["pending"],
        outlook_items="\n".join(f"<li>{x}</li>" for x in outlook),
        holdings_tables="\n".join(_render_holdings_table(p) for p in pilots),
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = now.strftime("%Y-%m-%d_%H%M")
    out_path = out_dir / f"{timestamp}_{slot_label}.html"
    out_path.write_text(html, encoding="utf-8")

    # latest.html も更新（最新レポートへのショートカット）
    latest = out_dir / "latest.html"
    latest.write_text(html, encoding="utf-8")

    print(f"✅ wrote {out_path} ({len(html):,} bytes)")
    print(f"✅ latest: {latest}")
    print(f"   slot: {slot_label} / pilots: {len(pilots)} / outlook items: {len(outlook)}")


if __name__ == "__main__":
    main()
