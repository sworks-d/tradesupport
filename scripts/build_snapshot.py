"""ダッシュボードのスナップショットJSONを生成する（UIが /data/snapshot.json として読む）。

実稼働想定：現金（仮¥1,000,000）・保有はブローカー（口座未接続なら0）。
買い候補は実データで3審判判定し、予算内のポジションサイジング（1銘柄20%上限・米株端株）を付ける。
価格は MarketDataTool / yfinance（2ソース照合つき）、財務/テクニカル/ニュースは各MCPツール。

実行:
    .venv/bin/python scripts/build_snapshot.py            # live価格 + StandIn口座
    .venv/bin/python scripts/build_snapshot.py --moomoo   # moomoo実保有（要OpenD/口座/同意）
    .venv/bin/python scripts/build_snapshot.py --demo     # オフライン（ネット不要）

出力: ui/public/data/snapshot.json
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from trading_agent.brokers import StandInBroker, load_positions
from trading_agent.db import create_all, get_engine
from trading_agent.magi import run_judges
from trading_agent.magi.judges import split_label
from trading_agent.mcp_tools.fundamentals import (
    FundamentalsInput,
    FundamentalsOutput,
    FundamentalsTool,
)
from trading_agent.mcp_tools.market_data import Fetcher, MarketDataInput, MarketDataTool
from trading_agent.mcp_tools.news import NewsInput, NewsOutput, NewsTool
from trading_agent.mcp_tools.technicals import (
    TechnicalsInput,
    TechnicalsOutput,
    TechnicalsTool,
)
from trading_agent.portfolio import recommend_position
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import utcnow

_log = get_logger("snapshot")

# 買い候補（カードの data-detail / data-panel id → ティッカー）。まず NVDA。
CANDIDATES: dict[str, str] = {"nvda": "NVDA"}

_ROLE = {"MELCHIOR": "業績", "BALTHASAR": "株価", "CASPER": "文脈"}
_DOT = {"MELCHIOR": "#ff8c42", "BALTHASAR": "#4ecdc4", "CASPER": "#fbbf24"}
_VD_DISPLAY = {
    "buy": ("買", "var(--up)"),
    "warn": ("慎重", "var(--warn)"),
    "hold": ("中立", "var(--ink-2)"),
    "sell": ("売", "var(--down)"),
    "na": ("不能", "var(--ink-3)"),
}


def _is_jp(ticker: str) -> bool:
    return ticker.split(".")[0].isdigit()


def _quote(cur: float, prev: float) -> dict[str, float]:
    return {
        "current_price": cur,
        "open_price": prev,
        "high_today": cur + 1,
        "low_today": prev - 1,
        "prev_close": prev,
        "volume_today": 1_000_000.0,
        "price_change_today": cur - prev,
        "price_change_pct_today": (cur - prev) / prev if prev else 0.0,
    }


# ---- live: 実データ ---------------------------------------------------------
def _live_primary(tickers: list[str]) -> dict[str, dict[str, float]]:
    import yfinance as yf

    out: dict[str, dict[str, float]] = {}
    for t in tickers:
        sym = f"{t}.T" if _is_jp(t) else t
        try:
            info = yf.Ticker(sym).fast_info
            out[t] = _quote(float(info.last_price), float(info.previous_close))
        except Exception as exc:
            _log.warning("primary_fetch_failed", ticker=t, error=str(exc))
    return out


def _live_secondary(tickers: list[str]) -> dict[str, dict[str, float]]:
    import httpx

    out: dict[str, dict[str, float]] = {}
    for t in tickers:
        suffix = ".jp" if _is_jp(t) else ".us"
        sym = (t.split(".")[0] + suffix).lower()
        try:
            r = httpx.get(f"https://stooq.com/q/l/?s={sym}&f=sd2t2ohlcv&h&e=csv", timeout=10.0)
            r.raise_for_status()
            rows = r.text.strip().splitlines()
            if len(rows) < 2:
                continue
            close = rows[1].split(",")[6]
            if close in ("N/D", ""):
                continue
            out[t] = _quote(float(close), float(close))
        except Exception as exc:
            _log.warning("secondary_fetch_failed", ticker=t, error=str(exc))
    return out


def _usdjpy(live: bool) -> float:
    """USD/JPY レート（米株を¥に換算）。live は yfinance、demo は固定。"""
    if not live:
        return 150.0
    try:
        import yfinance as yf

        return float(yf.Ticker("JPY=X").fast_info.last_price)
    except Exception as exc:
        _log.warning("usdjpy_fetch_failed", error=str(exc))
        return 150.0


# ---- demo: オフライン決定論データ -------------------------------------------
_DEMO_PRICE = {"NVDA": (305.0, 300.0)}  # (cur, prev) USD


def _demo_primary(tickers: list[str]) -> dict[str, dict[str, float]]:
    return {t: _quote(*_DEMO_PRICE[t]) for t in tickers if t in _DEMO_PRICE}


def _demo_secondary(tickers: list[str]) -> dict[str, dict[str, float]]:
    return {t: _quote(_DEMO_PRICE[t][0] + 0.2, _DEMO_PRICE[t][1]) for t in tickers if t in _DEMO_PRICE}


def _fmt_price(ticker: str, price: float) -> str:
    return f"¥ {price:,.0f}" if _is_jp(ticker) else f"$ {price:,.2f}"


def _serialize_candidate(verdicts: list, sizing: dict[str, object]) -> dict[str, object]:
    judges = []
    for v in verdicts:
        word, color = _VD_DISPLAY.get(v.verdict, ("—", "var(--ink-3)"))
        judges.append(
            {
                "judge": v.judge,
                "role": _ROLE.get(v.judge, ""),
                "dot": _DOT.get(v.judge, "#888888"),
                "verdict_word": word,
                "color": color,
                "dim": v.verdict == "na",
                "reason": v.reason,
            }
        )
    buys = sum(1 for v in verdicts if v.verdict == "buy")
    gendo = "推し" if buys == len(verdicts) else ("要検討" if buys >= 1 else "静観")
    return {"judges": judges, "split": split_label(verdicts), "gendo": gendo, "sizing": sizing}


async def _build_candidates(live: bool, total_assets: float, cash: float, usdjpy: float) -> dict:
    out: dict[str, object] = {}
    for card_id, ticker in CANDIDATES.items():
        if live:
            fund = await FundamentalsTool().execute(FundamentalsInput(ticker=ticker))
            tech = await TechnicalsTool().execute(TechnicalsInput(ticker=ticker))
            try:
                news = await NewsTool().execute(NewsInput(tickers=[ticker]))
            except Exception as exc:
                _log.warning("candidate_news_failed", ticker=ticker, error=str(exc))
                news = None
            price_usd = _live_primary([ticker]).get(ticker, {}).get("current_price")
        else:
            fund = FundamentalsOutput(
                success=True, data={"revenue_growth": 0.55, "operating_margin": 0.32}
            )
            tech = TechnicalsOutput(
                success=True, data={"rsi": 58.0}, signals=["golden_cross", "macd_bullish"]
            )
            news = NewsOutput(success=True, articles=[{"title": "record 需要", "summary": "surge"}])
            price_usd = _DEMO_PRICE[ticker][0]

        verdicts = run_judges(ticker, fundamentals=fund, technicals=tech, news=news)

        # 予算内サイジング（米株は端株可。JP は単元）
        is_jp = _is_jp(ticker)
        price_jpy = (price_usd or 0.0) * (1.0 if is_jp else usdjpy)
        rec = recommend_position(
            price_jpy=price_jpy,
            total_assets_jpy=total_assets,
            cash_jpy=cash,
            is_jp=is_jp,
        )
        sizing = {
            "amount_display": f"¥{rec.amount_jpy:,.0f}",
            "shares": rec.shares,
            "weight_pct": round(rec.weight * 100, 1),
            "price_jpy": round(price_jpy),
            "note": rec.note,
        }
        out[card_id] = _serialize_candidate(verdicts, sizing)
    return out


async def build(*, live: bool, prefer_moomoo: bool) -> dict[str, object]:
    eng = get_engine(Path(tempfile.gettempdir()) / "snapshot.sqlite")
    create_all(eng)

    positions, broker_src = load_positions(prefer_moomoo=prefer_moomoo)
    account = StandInBroker().get_account()  # 仮¥1,000,000（moomoo口座連携は後続）
    total = account.total_assets if account else 0.0
    cash = account.cash if account else 0.0
    usdjpy = _usdjpy(live)

    # 保有（口座未接続なら空＝現金100%）
    holdings: dict[str, dict[str, object]] = {}
    if positions:
        primary: Fetcher = _live_primary if live else _demo_primary
        secondary: Fetcher = _live_secondary if live else _demo_secondary
        tool = MarketDataTool(eng, fetcher=primary, reconcile_fetcher=secondary)
        out = await tool.execute(
            MarketDataInput(tickers=[p.code for p in positions], fields=["current_price"])
        )
        for p in positions:
            price = p.nominal_price or out.data.get(p.code, {}).get("current_price")
            pnl = None
            if p.pl_ratio is not None:
                pnl = {"ratio_display": f"{p.pl_ratio:+.1f}%", "direction": "up" if p.pl_ratio >= 0 else "down"}
            elif price is not None and p.cost_price:
                r = (price - p.cost_price) / p.cost_price * 100.0
                pnl = {"ratio_display": f"{r:+.1f}%", "direction": "up" if r >= 0 else "down"}
            holdings[p.code] = {
                "price_display": _fmt_price(p.code, price) if price is not None else None,
                "reconciliation": out.reconciliation.get(p.code, "single"),
                "as_of": out.data_asof.strftime("%Y-%m-%d %H:%M") if out.data_asof else None,
                "pnl": pnl,
            }

    candidates = await _build_candidates(live, total, cash, usdjpy)

    holdings_source = "moomoo ペーパー" if broker_src == "moomoo" else "サンプル/未接続"
    return {
        "generated_at": utcnow().strftime("%Y-%m-%d %H:%M"),
        "mode": "live" if live else "demo",
        "broker": broker_src,
        "holdings_source": holdings_source,
        "usdjpy": round(usdjpy, 2),
        "account": {
            "cash": round(cash),
            "total_assets": round(total),
            "cash_ratio": round(cash / total * 100) if total else 0,
            "currency": account.currency if account else "JPY",
            "positions": len(positions),
        },
        "holdings": holdings,
        "candidates": candidates,
    }


async def main() -> None:
    live = "--demo" not in sys.argv
    prefer_moomoo = "--moomoo" in sys.argv
    snapshot = await build(live=live, prefer_moomoo=prefer_moomoo)
    out_path = Path(__file__).resolve().parent.parent / "ui" / "public" / "data" / "snapshot.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}  (mode={snapshot['mode']}, broker={snapshot['broker']})")
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
