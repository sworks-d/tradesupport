"""日次レポートのデータ集約 + HTML レンダリング。

`build_report_payload(engine, date)` で集約データを返し、`render_html(payload)` で
Jinja2 テンプレートに流し込んで autoreport/YYYY-MM-DD.html を出力する。
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.decisions import Decision
from trading_agent.models.portfolio import Portfolio
from trading_agent.portfolio.personality import (
    RULE_SUMMARY,
    all_personalities,
    effective_max_position_pct,
)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _fetch_current_prices(tickers: list[str]) -> dict[str, tuple[float, float]]:
    """保有銘柄の現在価格と前日終値を yfinance から bulk 取得。

    Returns: {ticker: (current_jpy, prev_close_jpy)}
    JP は ".T" suffix で取得、US は USD のまま（呼び出し側で換算）。
    """
    if not tickers:
        return {}
    import yfinance as yf

    from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol

    sym_map = {to_yfinance_symbol(t): t for t in tickers}
    out: dict[str, tuple[float, float]] = {}
    try:
        df = yf.download(
            list(sym_map.keys()),
            period="2d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
        )
    except Exception:
        return out
    if df is None or df.empty:
        return out
    for sym, ticker in sym_map.items():
        try:
            series = df[sym]["Close"] if len(sym_map) > 1 else df["Close"]
            closes = [float(x) for x in series.dropna().tolist()]
            if len(closes) >= 2:
                out[ticker] = (closes[-1], closes[-2])
            elif len(closes) == 1:
                out[ticker] = (closes[0], closes[0])
        except Exception:
            continue
    return out


@dataclass
class PersonalitySummary:
    name: str
    label: str
    icon: str
    description: str
    overlay_cash_jpy: int
    holdings_count: int
    invested_jpy: float        # 累計投資額（取得価格ベース）
    cash_jpy: float            # 残現金
    market_value_jpy: float    # 保有時価合計（yfinance 現在価格）
    total_value_jpy: float     # cash + market_value（総資産時価）
    pnl_jpy: float             # total - overlay（累積収支）
    pnl_pct: float             # 対 overlay %
    day_change_jpy: float = 0.0  # 前日比（前日終値→現在価格）
    accept_stances: list[str] = field(default_factory=list)
    max_position_pct: float = 0.0
    effective_max_pct: float = 0.0  # PnL 連動で動的調整された値
    horizon_days: int = 0
    stop_loss_pct: float = 0.0
    rule_summary: str = ""  # 自動売買ルールの 1 行要約
    fees_total_jpy: float = 0.0
    fx_cost_total_jpy: float = 0.0
    holdings: list[dict[str, Any]] = field(default_factory=list)
    thoughts: list[str] = field(default_factory=list)


@dataclass
class ReportPayload:
    date: dt.date
    generated_at: dt.datetime
    summary: dict[str, Any]
    personalities: list[PersonalitySummary]
    stance_breakdown: dict[str, int]
    cost_today_jpy: float
    cost_month_jpy: float
    cost_month_budget: int
    universe_size: int
    topics_today: int
    decisions_today: list[dict[str, Any]]


def build_report_payload(engine: Engine, date: dt.date) -> ReportPayload:
    """指定日のレポート用ペイロードを DB から集約する。"""
    from trading_agent.models.universe import Universe

    with Session(engine) as s:
        # 性格別 portfolio 集計
        ports = s.exec(
            select(Portfolio).where(col(Portfolio.status) == "active")
        ).all()
        port_by_personality: dict[str | None, list[Portfolio]] = defaultdict(list)
        for p in ports:
            port_by_personality[p.personality].append(p)
        # ticker → 会社名のマップ（日本語名 → 英名 → ticker の順で fallback）
        universe_rows = s.exec(select(Universe)).all()
        name_by_ticker: dict[str, str] = {
            u.ticker: (u.name_ja or u.name or u.ticker) for u in universe_rows
        }
        # 当日 decisions
        today_decisions = s.exec(
            select(Decision)
            .where(col(Decision.date) == date)
            .order_by(col(Decision.id).asc())
        ).all()
        stance_breakdown: dict[str, int] = defaultdict(int)
        for d in today_decisions:
            stance_breakdown[d.gendo_stance or "未判定"] += 1

        # コスト集計
        cost_today = _scalar(
            s,
            "SELECT COALESCE(SUM(cost_jpy), 0) FROM cost_logs WHERE date = :d",
            {"d": date.isoformat()},
        )
        month_first = date.replace(day=1)
        cost_month = _scalar(
            s,
            "SELECT COALESCE(SUM(cost_jpy), 0) FROM cost_logs WHERE date >= :d",
            {"d": month_first.isoformat()},
        )

        # universe / topics
        universe_size = _scalar(
            s, "SELECT COUNT(*) FROM universe WHERE is_active = 1", {}
        )
        topics_today = _scalar(
            s,
            "SELECT COUNT(*) FROM topics WHERE DATE(collected_at) = :d",
            {"d": date.isoformat()},
        )

    # 保有銘柄の現在価格を bulk 取得（時価評価・前日比用）
    unique_tickers = sorted({p.ticker for p in ports})
    price_map = _fetch_current_prices(unique_tickers)

    # 性格サマリー組み立て
    personalities = []
    for p in all_personalities():
        port_rows = port_by_personality.get(p.name, [])
        invested = sum(float(r.buy_price or 0) * float(r.qty or 0) for r in port_rows)
        cash = float(p.overlay_cash_jpy) - invested
        # 時価評価（yfinance 現在価格）と前日比
        market_value = 0.0
        day_change = 0.0
        for r in port_rows:
            cur, prev = price_map.get(r.ticker, (float(r.buy_price or 0), float(r.buy_price or 0)))
            qty = float(r.qty or 0)
            market_value += cur * qty
            day_change += (cur - prev) * qty
        total = cash + market_value  # 総資産時価
        pnl = total - float(p.overlay_cash_jpy)
        pnl_pct = (pnl / p.overlay_cash_jpy * 100.0) if p.overlay_cash_jpy else 0.0
        # 思考ログ（Portfolio.thesis に rationale を `|` 区切りで埋め込んでいる）
        thoughts: list[str] = []
        for r in port_rows:
            text = r.thesis or ""
            if "|" in text:
                _, _, rationale = text.partition("|")
                rationale = rationale.strip()
                ticker_label = f"{r.ticker} ({name_by_ticker.get(r.ticker, r.ticker)})"
                line = f"{ticker_label}: {rationale}"
                if rationale and line not in thoughts:
                    thoughts.append(line)
        holdings_payload = []
        for r in port_rows:
            cur, prev = price_map.get(r.ticker, (float(r.buy_price or 0), float(r.buy_price or 0)))
            qty = float(r.qty or 0)
            cost = float(r.buy_price or 0) * qty
            mkt = cur * qty
            day_pnl = (cur - prev) * qty
            unrealized = mkt - cost
            unrealized_pct = (unrealized / cost * 100) if cost else 0.0
            holdings_payload.append(
                {
                    "ticker": r.ticker,
                    "name": name_by_ticker.get(r.ticker, r.ticker),
                    "qty": int(qty),
                    "buy_price": r.buy_price,
                    "current_price": cur,
                    "buy_date": r.buy_date.isoformat() if r.buy_date else "",
                    "cost": cost,
                    "market_value": mkt,
                    "unrealized": unrealized,
                    "unrealized_pct": unrealized_pct,
                    "day_change": day_pnl,
                    "horizon_days": r.target_period_days,
                }
            )
        personalities.append(
            PersonalitySummary(
                name=p.name,
                label=p.label,
                icon=p.icon,
                description=p.description,
                overlay_cash_jpy=p.overlay_cash_jpy,
                holdings_count=len(port_rows),
                invested_jpy=invested,
                cash_jpy=cash,
                market_value_jpy=market_value,
                total_value_jpy=total,
                pnl_jpy=pnl,
                pnl_pct=pnl_pct,
                day_change_jpy=day_change,
                accept_stances=list(p.accept_stances),
                max_position_pct=p.max_position_pct,
                effective_max_pct=effective_max_position_pct(p, pnl_pct=pnl_pct),
                horizon_days=p.horizon_days,
                stop_loss_pct=p.stop_loss_pct,
                rule_summary=RULE_SUMMARY.get(p.name, ""),
                holdings=holdings_payload,
                thoughts=thoughts,
            )
        )

    decisions_payload = [
        {
            "id": d.id,
            "ticker": d.ticker,
            "name": name_by_ticker.get(d.ticker, d.ticker),
            "stance": d.gendo_stance or "未判定",
            "status": d.status,
            "filled": list(d.personalities_filled or []),
        }
        for d in today_decisions
    ]

    return ReportPayload(
        date=date,
        generated_at=dt.datetime.now(),
        summary={
            "decisions_total": len(today_decisions),
            "推し": stance_breakdown.get("推し", 0),
            "要検討": stance_breakdown.get("要検討", 0),
            "静観": stance_breakdown.get("静観", 0),
        },
        personalities=personalities,
        stance_breakdown=dict(stance_breakdown),
        cost_today_jpy=float(cost_today or 0),
        cost_month_jpy=float(cost_month or 0),
        cost_month_budget=5000,
        universe_size=int(universe_size or 0),
        topics_today=int(topics_today or 0),
        decisions_today=decisions_payload,
    )


def _scalar(session: Session, sql: str, params: dict[str, Any]) -> Any:
    from sqlalchemy import text

    result = session.execute(text(sql), params)
    val = result.scalar()
    return val if val is not None else 0


def render_html(payload: ReportPayload) -> str:
    """Jinja2 でレンダリング。"""
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("daily.html")
    return template.render(payload=payload)
