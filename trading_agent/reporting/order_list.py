"""手動発注リスト HTML 生成（v2.10 楽天かぶミニ運用向け）。

朝バッチ後に「今朝の発注リスト」を HTML で出力。
ユーザーはスマホで開き、各銘柄を楽天証券アプリで発注 → 発注完了タップで
Decision の status を "filled" に更新する。

設計原則（ミスのない・工数最小）:
  - 1 タップで ticker / 株数をクリップボードコピー
  - 残予算リアルタイム表示
  - 発注完了は DB に直結（localStorage は使わない・連携漏れ防止）
  - 翌日繰越は朝バッチで自動処理
  - スマホファースト（モバイル CSS 優先）

出力先:
  autoreport/orders/YYYY-MM-DD.html
"""

from __future__ import annotations

import datetime as dt
import html
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.decisions import Decision
from trading_agent.models.universe import Universe
from trading_agent.portfolio.misato import treasury_view
from trading_agent.utils.lot_size import get_broker_provider, get_lot_size
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import today_jst

_log = get_logger("reporting.order_list")


@dataclass
class OrderItem:
    """発注リスト 1 件分。"""

    priority: int  # 優先度 1〜N
    decision_id: int
    ticker: str
    name: str
    market_cap_jpy: float
    current_price: float | None
    recommended_shares: int
    estimated_cost_jpy: float
    stop_loss_price: float | None
    strategy_category: str  # 短期/中期/長期
    gendo_stance: str  # 推し/要検討/静観
    cumulative_cost_jpy: float  # 積算コスト（残予算可視化用）
    # v2.10: カード型 UI でわかりやすく表示するための追加情報
    target_price: float | None = None  # 目標価格
    expected_return_pct: float = 0.0  # 期待リターン
    risk_pct: float = 0.0  # 損失率（stop_loss）
    sector: str = "—"
    thesis_summary: str = ""  # 推奨理由 1 行


def _fetch_price(ticker: str) -> float | None:
    """yfinance で現在価格取得（None なら判定不能）。"""
    try:
        import yfinance as yf

        from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol

        sym = to_yfinance_symbol(ticker)
        info = yf.Ticker(sym).fast_info
        p = float(info.last_price or 0)
        return p if p > 0 else None
    except Exception as exc:
        _log.warning("price_fetch_failed", ticker=ticker, error_type=type(exc).__name__)
        return None


def build_order_items(
    engine: Engine,
    *,
    date: dt.date | None = None,
    available_jpy: float | None = None,
    per_position_pct: float = 0.10,
    sort_by: str = "priority",
) -> list[OrderItem]:
    """今日の awaiting Decision から発注リスト items を組み立てる。

    Args:
        engine: DB エンジン
        date: 対象日（None なら今日 JST）
        available_jpy: 利用可能予算（None なら treasury_view から取得）
        per_position_pct: 1 ポジションあたりの予算配分（デフォルト 10%、少額時は 1/N で上限調整）
    """
    d = date or today_jst()
    provider = get_broker_provider()

    if available_jpy is None:
        # v2.10: 発注リストは楽天本番予算 (live Treasury) を使う
        # 試験運用 (paper) は朝バッチの auto_fill が使うので別系統
        try:
            tv = treasury_view(engine, "live")
            available_jpy = float(tv.get("available_jpy") or 0)
        except Exception:
            available_jpy = 0.0

    with Session(engine) as s:
        decisions = list(
            s.exec(
                select(Decision)
                .where(col(Decision.date) == d)
                .where(col(Decision.action) == "buy")
                .where(col(Decision.status) == "awaiting")
                .order_by(col(Decision.ticker))
            ).all()
        )

    items: list[OrderItem] = []
    n = len(decisions)
    if n == 0:
        return items

    # 1 ポジションあたり予算（per_position_pct × 予算、ただし 1/N より小さくしない）
    per_pos_budget = max(
        available_jpy * per_position_pct,
        available_jpy / max(n, 1),
    )

    cumulative = 0.0
    with Session(engine) as s:
        for idx, dec in enumerate(decisions, start=1):
            u = s.get(Universe, dec.ticker)
            name = u.name if u else dec.ticker
            cap = float(u.market_cap_jpy or 0) if u else 0.0

            # 現在価格（yfinance、寄付想定値の代替）
            price = _fetch_price(dec.ticker)
            lot = get_lot_size(dec.ticker, provider=provider)

            if price is not None and price > 0:
                # 累積コストが予算を超えないよう、残予算と per_pos_budget の小さい方を使う
                remaining_budget = max(0.0, available_jpy - cumulative)
                effective_pos_budget = min(per_pos_budget, remaining_budget)
                max_by_budget = int(effective_pos_budget // price)
                # 単元株未満は買えない（楽天は lot=1 で問題ないはず）
                shares = (max_by_budget // lot) * lot
                if shares < lot:
                    shares = 0  # 1 単元も買えない（残予算不足）
                est_cost = price * shares
            else:
                shares = 0
                est_cost = 0.0

            # stop_loss / target / リスク・リワード
            stop_pct = float(dec.stop_pct or 0.10)
            # Decision に target_pct はないため getattr or デフォルト
            target_pct = float(getattr(dec, "target_pct", None) or 0.20)
            stop_loss = price * (1.0 - abs(stop_pct)) if price else None
            target = price * (1.0 + target_pct) if price else None

            # thesis 1 行サマリ
            full_thesis = dec.thesis_at_decision or ""
            thesis_summary = full_thesis.split("|")[0].strip()[:80] if full_thesis else ""
            if not thesis_summary and u:
                thesis_summary = f"{u.sector or ''} の中小型成長株候補"

            sector = u.sector if u else "—"

            cumulative += est_cost
            # ソート用のスコアを各 OrderItem に付与（priority は後で再計算）
            items.append(
                OrderItem(
                    priority=idx,
                    decision_id=dec.id or 0,
                    ticker=dec.ticker,
                    name=name,
                    market_cap_jpy=cap,
                    current_price=price,
                    recommended_shares=shares,
                    estimated_cost_jpy=est_cost,
                    stop_loss_price=stop_loss,
                    strategy_category=getattr(dec, "strategy_category", None) or "中期",
                    gendo_stance=dec.gendo_stance or "—",
                    cumulative_cost_jpy=cumulative,
                    target_price=target,
                    expected_return_pct=target_pct,
                    risk_pct=abs(stop_pct),
                    sector=sector,
                    thesis_summary=thesis_summary,
                )
            )

    # v2.10 P15: ソート基準を選択可能に
    if sort_by == "expected_return":
        items.sort(key=lambda it: -it.expected_return_pct)
    elif sort_by == "risk_reward":
        items.sort(
            key=lambda it: -(it.expected_return_pct / it.risk_pct if it.risk_pct > 0 else 0)
        )
    elif sort_by == "stance":
        # 推し → 要検討 → 静観 の順
        order = {"推し": 0, "要検討": 1, "静観": 2}
        items.sort(key=lambda it: (order.get(it.gendo_stance, 9), it.priority))
    # priority のときは元の順序を維持

    # priority を再採番
    for i, it in enumerate(items, start=1):
        it.priority = i
    return items


# ============================================================
# HTML 生成
# ============================================================


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<!-- 30 分ごと自動更新（朝バッチ後に同日内で再生成されてもユーザー画面が最新に） -->
<meta http-equiv="refresh" content="1800">
<title>今朝の発注リスト ({date})</title>
<style>
:root {{
  --bg: #0a0e14;
  --bg-2: #131923;
  --bg-3: #1d2533;
  --bg-4: #28324a;
  --fg: #e8ecf3;
  --fg-mute: #8893a5;
  --suggest: #4ec9b0;
  --review: #569cd6;
  --watch: #6c7689;
  --warn: #f5a85a;
  --danger: #e85c5c;
  --border: rgba(255,255,255,0.06);
  --r-lg: 16px;
  --r-md: 10px;
}}
* {{ box-sizing: border-box; -webkit-tap-highlight-color: transparent; }}
body {{
  margin: 0; padding: 0;
  font-family: -apple-system, "SF Pro Text", "Hiragino Sans", sans-serif;
  background: var(--bg); color: var(--fg);
  font-size: 16px; line-height: 1.5;
}}
.container {{ max-width: 720px; margin: 0 auto; padding: 16px 12px 32px; }}

/* ===== ヘッダー ===== */
.header {{
  background: linear-gradient(180deg, var(--bg-2) 0%, rgba(19,25,35,0.6) 100%);
  border-radius: var(--r-lg);
  padding: 18px 16px;
  margin-bottom: 12px;
  border: 1px solid var(--border);
}}
.header h1 {{ margin: 0 0 4px; font-size: 19px; font-weight: 700; }}
.header .subtitle {{ color: var(--fg-mute); font-size: 12px; }}
.summary {{
  display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px;
  margin-top: 14px;
}}
.summary > div {{
  background: var(--bg-3); padding: 10px 8px;
  border-radius: var(--r-md);
  text-align: center;
}}
.summary .label {{ font-size: 11px; color: var(--fg-mute); margin-bottom: 2px; }}
.summary .value {{ font-size: 17px; font-weight: 700; color: var(--suggest); }}
.summary .value.warn {{ color: var(--warn); }}

/* ===== セクション見出し ===== */
.section-header {{
  font-size: 13px;
  color: var(--fg-mute);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin: 18px 0 8px 4px;
  font-weight: 600;
}}
.section-header.suggest {{ color: var(--suggest); }}
.section-header.review {{ color: var(--review); }}
.section-header.watch {{ color: var(--watch); }}

/* ===== カード ===== */
.order-card {{
  background: var(--bg-2);
  border-radius: var(--r-lg);
  padding: 16px;
  margin-bottom: 10px;
  border: 1px solid var(--border);
  position: relative;
}}
.order-card.stance-suggest {{
  border-left: 4px solid var(--suggest);
  background: linear-gradient(90deg, rgba(78,201,176,0.06) 0%, var(--bg-2) 60%);
}}
.order-card.stance-review {{
  border-left: 4px solid var(--review);
}}
.order-card.stance-watch {{
  opacity: 0.7;
  border-left: 4px solid var(--watch);
}}
.order-card.filled {{ opacity: 0.4; }}

.card-head {{
  display: flex; align-items: flex-start; justify-content: space-between;
  margin-bottom: 12px;
}}
.card-title .ticker {{
  font-size: 22px; font-weight: 800; color: var(--review);
  letter-spacing: 0.02em;
}}
.card-title .name {{
  font-size: 13px; color: var(--fg-mute); margin-top: 2px;
}}
.card-priority {{
  background: var(--bg-4); color: var(--fg);
  font-weight: 700; font-size: 14px;
  width: 32px; height: 32px;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}}
.stance-suggest .card-priority {{ background: var(--suggest); color: var(--bg); }}

.tags {{
  display: flex; gap: 6px; flex-wrap: wrap;
  margin-bottom: 12px;
}}
.tag {{
  display: inline-flex; align-items: center;
  padding: 3px 8px; border-radius: 4px;
  font-size: 11px; font-weight: 600;
  background: var(--bg-3); color: var(--fg-mute);
}}
.tag.tag-stance {{ background: var(--bg-4); color: var(--fg); }}
.stance-suggest .tag.tag-stance {{ background: var(--suggest); color: var(--bg); }}
.stance-review .tag.tag-stance {{ background: var(--review); color: var(--bg); }}

/* ===== 推奨理由 ===== */
.thesis {{
  background: var(--bg-3);
  border-radius: var(--r-md);
  padding: 10px 12px;
  font-size: 13px;
  color: var(--fg);
  margin-bottom: 12px;
  border-left: 3px solid var(--review);
}}
.thesis .lead {{
  color: var(--fg-mute); font-size: 10px;
  text-transform: uppercase; letter-spacing: 0.06em;
  margin-bottom: 2px;
}}

/* ===== 価格情報 ===== */
.price-grid {{
  display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
  margin-bottom: 12px;
}}
.price-box {{
  background: var(--bg-3);
  border-radius: var(--r-md);
  padding: 10px 12px;
}}
.price-box .label {{
  font-size: 10px; color: var(--fg-mute);
  text-transform: uppercase; letter-spacing: 0.06em;
}}
.price-box .value {{
  font-size: 18px; font-weight: 700;
  margin-top: 2px;
}}
.price-box .sub {{
  font-size: 11px; color: var(--fg-mute); margin-top: 2px;
}}
.price-box.target .value {{ color: var(--suggest); }}
.price-box.target .sub {{ color: var(--suggest); }}
.price-box.stop .value {{ color: var(--warn); }}
.price-box.stop .sub {{ color: var(--warn); }}

/* ===== バー：リスク vs リワード ===== */
.rr-bar {{
  display: flex; height: 8px; border-radius: 4px;
  overflow: hidden;
  margin-bottom: 12px;
  background: var(--bg-3);
}}
.rr-bar .risk-side {{ background: var(--warn); }}
.rr-bar .reward-side {{ background: var(--suggest); }}
.rr-labels {{
  display: flex; justify-content: space-between;
  font-size: 10px; color: var(--fg-mute);
  margin-top: -8px; margin-bottom: 12px;
}}

/* ===== 楽天証券での発注内容 ===== */
.rakuten-order {{
  background: var(--bg-4);
  border-radius: var(--r-md);
  padding: 14px;
  margin-bottom: 12px;
  border: 1px solid rgba(189, 0, 0, 0.2);
}}
.ro-header {{
  font-size: 12px;
  color: var(--fg-mute);
  margin-bottom: 10px;
  font-weight: 600;
  letter-spacing: 0.03em;
}}
.ro-grid {{
  display: flex; flex-direction: column; gap: 8px;
}}
.ro-row {{
  display: grid;
  grid-template-columns: 110px 1fr 36px;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px solid rgba(255,255,255,0.04);
}}
.ro-row:last-child {{ border-bottom: none; }}
.ro-row.ro-total {{
  margin-top: 4px;
  padding-top: 10px;
  border-top: 1px solid rgba(255,255,255,0.1);
  border-bottom: none;
}}
.ro-label {{
  color: var(--fg-mute); font-size: 12px;
}}
.ro-value {{
  font-size: 16px; font-weight: 700; color: var(--fg);
}}
.ro-mono {{
  font-family: "SF Mono", Menlo, Monaco, "Courier New", monospace;
  letter-spacing: 0.02em;
}}
.ro-amount {{
  font-size: 20px; color: var(--suggest);
}}
.ro-copy {{
  background: var(--bg-3); border: 1px solid var(--border);
  color: var(--fg);
  width: 36px; height: 36px;
  border-radius: 6px;
  cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  font-size: 14px;
  padding: 0;
}}
.ro-copy:active {{ background: var(--suggest); color: var(--bg); }}
.ro-sub {{
  color: var(--fg-mute); font-size: 11px;
  margin-top: 8px; text-align: right;
}}

/* ===== アクション ===== */
.actions {{
  display: grid; grid-template-columns: 1fr 1fr; gap: 6px;
  margin-top: 4px;
}}
.actions .btn-done {{ grid-column: 1 / -1; }}
.btn {{
  min-height: 44px;
  background: var(--bg-3); color: var(--fg);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  font-size: 14px; font-weight: 600;
  cursor: pointer; user-select: none;
  display: flex; align-items: center; justify-content: center;
  font-family: inherit; padding: 0 10px;
  gap: 6px;
}}
.btn:active {{ transform: scale(0.98); }}
.btn-copy {{ background: var(--bg-3); }}
.btn-done {{
  background: var(--suggest); color: var(--bg);
  font-weight: 700; font-size: 15px;
}}
.btn-done.completed {{ background: var(--fg-mute); }}

/* ===== フッター ===== */
.footer {{
  text-align: center; padding: 24px 0 8px;
  color: var(--fg-mute); font-size: 12px;
  line-height: 1.7;
}}
.no-orders {{
  text-align: center; padding: 60px 20px;
  color: var(--fg-mute); font-size: 14px;
}}

@media (max-width: 480px) {{
  body {{ font-size: 15px; }}
  .container {{ padding: 12px 8px 32px; }}
  .order-card {{ padding: 14px; }}
  .summary .value {{ font-size: 15px; }}
  .card-title .ticker {{ font-size: 20px; }}
}}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>🌅 今朝の発注リスト</h1>
    <div class="subtitle">{date} / 楽天かぶミニ寄付発注 / {n} 銘柄推奨</div>
    <div class="summary">
      <div>
        <div class="label">予算</div>
        <div class="value">¥{available:,.0f}</div>
      </div>
      <div>
        <div class="label">推奨合計</div>
        <div class="value{cost_warn_class}">¥{total_cost:,.0f}</div>
      </div>
      <div>
        <div class="label">残予算</div>
        <div class="value">¥{remaining:,.0f}</div>
      </div>
    </div>
  </div>

{cards}

  <div class="footer">
    💡 上から順に楽天証券で発注 → 「✓ 完了」ボタンで DB に反映<br>
    未完了 Decision は翌朝バッチで再評価・繰越されます
  </div>
</div>

<script>
function copyTicker(t) {{
  navigator.clipboard.writeText(t).then(() => {{
    alert('ticker ' + t + ' をコピーしました');
  }});
}}
function copyShares(s) {{
  navigator.clipboard.writeText(s).then(() => {{
    alert(s + ' 株をコピーしました');
  }});
}}
function markFilled(decisionId, ticker, btn) {{
  if (!confirm(ticker + ' を発注完了にしますか?')) return;
  const cmd = '.venv/bin/python scripts/mark_filled.py --decision-id ' + decisionId;
  navigator.clipboard.writeText(cmd).then(() => {{
    btn.textContent = '✓ 完了済';
    btn.classList.add('completed');
    btn.closest('.order-card').classList.add('filled');
    alert('発注完了マーク用 CLI コマンドをコピーしました。PC で実行してください:\\n' + cmd);
  }});
}}
</script>
</body>
</html>
"""


_CARD_TEMPLATE = """  <div class="order-card stance-{stance_class}" id="order-{decision_id}">
    <div class="card-head">
      <div class="card-title">
        <div class="ticker">{ticker}</div>
        <div class="name">{name}</div>
      </div>
      <div class="card-priority">{priority}</div>
    </div>
    <div class="tags">
      <span class="tag tag-stance">{gendo_stance}</span>
      <span class="tag">{strategy}</span>
      <span class="tag">{sector}</span>
      <span class="tag">時価総額 ¥{cap_b}B</span>
    </div>
    <div class="thesis">
      <div class="lead">▾ 推奨理由</div>
      <div>{thesis}</div>
    </div>
    <div class="price-grid">
      <div class="price-box target">
        <div class="label">▲ 目標 (+{ret_pct})</div>
        <div class="value">¥{target_str}</div>
        <div class="sub">期待リターン</div>
      </div>
      <div class="price-box stop">
        <div class="label">▼ 損切 (-{risk_pct})</div>
        <div class="value">¥{stop_str}</div>
        <div class="sub">stop_loss</div>
      </div>
    </div>
    <div class="rr-bar">
      <div class="risk-side" style="width: {risk_w}%;"></div>
      <div class="reward-side" style="width: {reward_w}%;"></div>
    </div>
    <div class="rr-labels">
      <span>リスク {risk_pct}</span>
      <span>R/R {rr_ratio}</span>
      <span>リワード {ret_pct}</span>
    </div>
    <div class="rakuten-order">
      <div class="ro-header">🛒 楽天証券での発注内容</div>
      <div class="ro-grid">
        <div class="ro-row">
          <span class="ro-label">銘柄コード</span>
          <span class="ro-value ro-mono">{ticker}</span>
          <button class="ro-copy" onclick="copyTicker('{ticker}')" aria-label="銘柄コードをコピー">📋</button>
        </div>
        <div class="ro-row">
          <span class="ro-label">数量</span>
          <span class="ro-value ro-mono">{shares} 株</span>
          <button class="ro-copy" onclick="copyShares({shares})" aria-label="株数をコピー">📋</button>
        </div>
        <div class="ro-row">
          <span class="ro-label">注文タイプ</span>
          <span class="ro-value">寄付（成行）</span>
          <span></span>
        </div>
        <div class="ro-row ro-total">
          <span class="ro-label">概算約定金額</span>
          <span class="ro-value ro-mono ro-amount">¥{cost:,.0f}</span>
          <span></span>
        </div>
      </div>
      <div class="ro-sub">@現在株価 ¥{price_str} / 株 × {shares} 株</div>
    </div>
    <div class="actions">
      <button class="btn btn-done" onclick="markFilled({decision_id}, '{ticker}', this)">✓ 楽天で発注した</button>
    </div>
  </div>
"""


_SECTION_HEADER_TEMPLATE = (
    '  <div class="section-header {cls}">{icon} {label}（{n} 件）</div>\n'
)


def render_html(items: list[OrderItem], *, date: dt.date, available_jpy: float) -> str:
    """発注リストを HTML に変換（カード型・推奨度別セクション分け）。"""
    if not items:
        cards = '  <div class="order-card no-orders">本日の発注候補はありません。</div>'
    else:
        # gendo_stance でセクション分け（推し → 要検討 → 静観）
        stance_order = ["推し", "要検討", "静観"]
        grouped: dict[str, list[OrderItem]] = {s: [] for s in stance_order}
        for item in items:
            key = item.gendo_stance if item.gendo_stance in grouped else "静観"
            grouped[key].append(item)

        section_label = {
            "推し": ("✨", "今すぐ買う候補", "suggest"),
            "要検討": ("🤔", "迷ったら買う", "review"),
            "静観": ("⏳", "優先度低・様子見", "watch"),
        }

        cards_list: list[str] = []
        for stance in stance_order:
            group = grouped[stance]
            if not group:
                continue
            icon, label, cls = section_label[stance]
            cards_list.append(
                _SECTION_HEADER_TEMPLATE.format(
                    cls=cls, icon=icon, label=label, n=len(group)
                )
            )
            for item in group:
                cards_list.append(_render_card(item, cls))
        cards = "".join(cards_list)

    total_cost = sum(item.estimated_cost_jpy for item in items)
    remaining = max(0.0, available_jpy - total_cost)
    cost_warn = " warn" if total_cost > available_jpy else ""

    return _HTML_TEMPLATE.format(
        date=date.isoformat(),
        available=available_jpy,
        total_cost=total_cost,
        remaining=remaining,
        cost_warn_class=cost_warn,
        n=len(items),
        cards=cards,
    )


def _render_card(item: OrderItem, stance_class: str) -> str:
    """1 件分のカード HTML。"""
    cap_b = int(item.market_cap_jpy / 1e9) if item.market_cap_jpy else 0
    price_str = f"{int(item.current_price):,}" if item.current_price else "—"
    stop_str = f"{int(item.stop_loss_price):,}" if item.stop_loss_price else "—"
    target_str = f"{int(item.target_price):,}" if item.target_price else "—"
    ret_pct = f"{item.expected_return_pct * 100:.0f}%"
    risk_pct = f"{item.risk_pct * 100:.0f}%"
    rr_ratio = (
        f"{item.expected_return_pct / item.risk_pct:.1f}"
        if item.risk_pct > 0
        else "—"
    )
    # バー：リスク部分の割合（リスク / (リスク + リワード)）
    total_rr = item.expected_return_pct + item.risk_pct
    risk_w = (item.risk_pct / total_rr * 100) if total_rr > 0 else 50
    reward_w = 100 - risk_w
    thesis = html.escape(item.thesis_summary) if item.thesis_summary else "中小型成長銘柄候補"

    return _CARD_TEMPLATE.format(
        decision_id=item.decision_id,
        priority=item.priority,
        ticker=html.escape(item.ticker),
        name=html.escape(item.name[:30]),
        gendo_stance=html.escape(item.gendo_stance),
        stance_class=stance_class,
        strategy=html.escape(item.strategy_category),
        sector=html.escape(item.sector),
        cap_b=f"{cap_b:,}",
        shares=item.recommended_shares,
        cost=item.estimated_cost_jpy,
        price_str=price_str,
        stop_str=stop_str,
        target_str=target_str,
        ret_pct=ret_pct,
        risk_pct=risk_pct,
        rr_ratio=rr_ratio,
        risk_w=f"{risk_w:.0f}",
        reward_w=f"{reward_w:.0f}",
        thesis=thesis,
    )


def generate_order_list(
    engine: Engine,
    *,
    date: dt.date | None = None,
    output_dir: Path | None = None,
) -> Path:
    """発注リスト HTML を生成して指定ディレクトリに出力。

    Returns:
        出力ファイルのパス
    """
    d = date or today_jst()
    out_dir = output_dir or (Path("autoreport") / "orders")
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        tv = treasury_view(engine, "live")
        available = float(tv.get("available_jpy") or 0)
    except Exception:
        available = 0.0

    items = build_order_items(engine, date=d, available_jpy=available)
    html_content = render_html(items, date=d, available_jpy=available)

    out_path = out_dir / f"{d.isoformat()}.html"
    out_path.write_text(html_content, encoding="utf-8")

    # v2.10: 固定 URL の latest.html を常に最新日に同期
    latest_path = out_dir / "latest.html"
    try:
        latest_path.write_text(html_content, encoding="utf-8")
    except Exception as exc:
        _log.warning("latest_html_write_failed", error_type=type(exc).__name__)

    # v2.10: dashboard（Next.js）からの動線用に ui/public/orders/ にもコピー
    # Next.js は public/ 配下を `/` ルートで配信するため、
    # /orders/latest.html / /orders/YYYY-MM-DD.html でアクセス可能になる
    ui_public_orders = Path("ui/public/orders")
    try:
        ui_public_orders.mkdir(parents=True, exist_ok=True)
        (ui_public_orders / f"{d.isoformat()}.html").write_text(html_content, encoding="utf-8")
        (ui_public_orders / "latest.html").write_text(html_content, encoding="utf-8")
    except Exception as exc:
        _log.warning("ui_public_orders_write_failed", error_type=type(exc).__name__)

    _log.info(
        "order_list_generated",
        path=str(out_path),
        latest_path=str(latest_path),
        ui_public_orders=str(ui_public_orders),
        items=len(items),
        available=available,
    )
    return out_path
