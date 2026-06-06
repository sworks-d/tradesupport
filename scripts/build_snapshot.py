"""ダッシュボードのスナップショットJSONを生成する（UIが /data/snapshot.json として読む）。

実稼働想定：現金（運用元本¥100,000）・保有はブローカー（口座未接続なら0）。
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

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.brokers import load_account, load_positions
from trading_agent.db import create_all, get_engine
from trading_agent.discipline.exposure_coach import ExposureInputs, decide_exposure
from trading_agent.discipline.holding_health import check_all_holdings
from trading_agent.llm.anthropic_client import AnthropicClient
from trading_agent.magi import casper_llm, classify_split, command, run_judges, verify
from trading_agent.magi.gendo import gendo_recommend
from trading_agent.magi.persist import derive_gendo_stance
from trading_agent.mcp_tools.fundamentals import (
    FundamentalsInput,
    FundamentalsOutput,
    FundamentalsTool,
)
from trading_agent.mcp_tools.llm_call import LLMCallTool
from trading_agent.mcp_tools.market_data import Fetcher, MarketDataInput, MarketDataTool
from trading_agent.mcp_tools.news import NewsInput, NewsOutput, NewsTool
from trading_agent.mcp_tools.technicals import (
    TechnicalsInput,
    TechnicalsOutput,
    TechnicalsTool,
)
from trading_agent.models.thesis import Thesis, ThesisStatus
from trading_agent.models.universe import Universe
from trading_agent.models.zeele import ZeeleState
from trading_agent.portfolio import recommend_position
from trading_agent.portfolio.sizing import SizeRec
from trading_agent.screening import (
    assess_credibility,
    fetch_financials,
    melchior_credibility_counter,
)
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import utcnow

_log = get_logger("snapshot")

# 買い候補（カードの data-detail / data-panel id → ティッカー）。まず NVDA。
# 買い候補の MAGI 詳細評価対象。F0 期は NVDA をサンプル枠として固定していたが、
# 「実データだけを UI に出す」方針へ転換したため空にする。decisions テーブルに
# 入った buy 候補は当面 candidates_pending（簡易表示）として snapshot に流す。
# 詳細 MAGI 評価は HANDOFF §5「低 4：CANDIDATES マップ拡張」のタスクで拡張する。
CANDIDATES: dict[str, str] = {}

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

    from trading_agent.mcp_tools.market_data import looks_like_anti_bot_response

    out: dict[str, dict[str, float]] = {}
    for t in tickers:
        suffix = ".jp" if _is_jp(t) else ".us"
        sym = (t.split(".")[0] + suffix).lower()
        try:
            r = httpx.get(f"https://stooq.com/q/l/?s={sym}&f=sd2t2ohlcv&h&e=csv", timeout=10.0)
            r.raise_for_status()
            # anti-bot 検知: stooq が /__verify や challenge HTML を返したら価格化しない。
            # 誤った値を reconcile に混入させず、明示的に skip（float 例外で誤魔化さない）。
            if looks_like_anti_bot_response(r.text, r.headers.get("content-type")):
                _log.warning("secondary_anti_bot_skipped", ticker=t)
                continue
            rows = r.text.strip().splitlines()
            if len(rows) < 2:
                continue
            cells = rows[1].split(",")
            if len(cells) <= 6:
                continue
            close = cells[6].strip()
            # 数値でない（N/D・空・想定外文字列）は価格化しない（推測しない）
            try:
                price = float(close)
            except ValueError:
                continue
            if price <= 0:
                continue
            out[t] = _quote(price, price)
        except Exception as exc:
            _log.warning("secondary_fetch_failed", ticker=t, error=str(exc))
    return out


def _usdjpy(live: bool) -> float | None:
    """USD/JPY レート（米株を¥に換算）。

    v2.5 TASK-P11: 取得失敗時 None を返す（旧 150.0 fallback は実レートと乖離）。
    demo は固定 150（テスト用・現実値からの差は明示）。
    """
    if not live:
        return 150.0
    try:
        import yfinance as yf

        return float(yf.Ticker("JPY=X").fast_info.last_price)
    except Exception as exc:
        _log.warning("usdjpy_fetch_failed", error=str(exc))
        return None  # 旧 150.0 → None で「取得失敗」を明示


# ---- demo: オフライン決定論データ -------------------------------------------
_DEMO_PRICE = {"NVDA": (305.0, 300.0)}  # (cur, prev) USD


def _demo_primary(tickers: list[str]) -> dict[str, dict[str, float]]:
    return {t: _quote(*_DEMO_PRICE[t]) for t in tickers if t in _DEMO_PRICE}


def _demo_secondary(tickers: list[str]) -> dict[str, dict[str, float]]:
    return {
        t: _quote(_DEMO_PRICE[t][0] + 0.2, _DEMO_PRICE[t][1])
        for t in tickers
        if t in _DEMO_PRICE
    }


def _fmt_price(ticker: str, price: float) -> str:
    return f"¥ {price:,.0f}" if _is_jp(ticker) else f"$ {price:,.2f}"


def _holdings_source_label(broker_src: str, trading_mode: str, account_src: str) -> str:
    # broker_src は保有取得（保有 0 件だと "standin" にフォールバックされる仕様）。
    # v2.10: broker_provider 別の表示
    # 口座側（account_src）が各 broker に繋がっていれば「保有 0 件」のメッセージで明示する。
    suffix = "実弾" if trading_mode == "live" else "紙運用 overlay"
    label_map = {
        "moomoo": "moomoo 実弾" if trading_mode == "live" else "moomoo JP REAL（紙運用：仮想入金 overlay）",
        "rakuten": f"楽天かぶミニ（{suffix}・手動発注 + mark_filled 経由で記録）",
        "sbi": f"SBI S 株（{suffix}・手動発注 + mark_filled 経由で記録）",
        "monex": f"マネックス ワン株（{suffix}・手動発注 + mark_filled 経由で記録）",
        "kabucom": f"auカブコム kabu STATION API（{suffix}・Phase 2 で実装）",
        "fractional": f"単元未満株シミュレーション（{suffix}・仮想 broker）",
    }
    if broker_src in label_map:
        return label_map[broker_src]
    if account_src in label_map:
        return f"{label_map[account_src]}・保有 0 件"
    # 既存互換
    if broker_src == "moomoo":
        return label_map["moomoo"]
    if account_src in ("moomoo", "moomoo+overlay"):
        return f"moomoo 接続済・保有 0 件（{suffix}）"
    return "サンプル/未接続"


def _fetch_price_histories(tickers: list[str], days: int = 30) -> dict[str, list[float]]:
    """yfinance で複数銘柄の終値履歴を一括取得（30 日分）。失敗銘柄は除外。"""
    if not tickers:
        return {}
    import yfinance as yf

    from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol

    sym_map = {to_yfinance_symbol(t): t for t in tickers}
    out: dict[str, list[float]] = {}
    try:
        df = yf.download(
            list(sym_map.keys()),
            period=f"{days}d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
        )
    except Exception as exc:
        _log.warning("price_history_fetch_failed", error=str(exc))
        return out
    if df is None or df.empty:
        return out
    for sym, ticker in sym_map.items():
        try:
            series = df[sym]["Close"] if len(sym_map) > 1 else df["Close"]
            closes = [float(x) for x in series.dropna().tolist()]
            if closes:
                out[ticker] = closes
        except Exception as exc:
            _log.warning("price_history_extract_failed", ticker=ticker, error=str(exc))
    return out


def _build_dummy_system(engine: Engine) -> dict[str, object]:
    """ダミーシステム 4 機（DS/REI・ASUKA・SHINJI・KAWORU）のサマリーを返す。

    UI 最上段に出すための簡潔版。詳細レポートは autoreport/daily/ にある。
    時価評価は yfinance 現在価格（ブラウザでリロードする度に最新化される設計）。
    各機の元本（overlay）は **MISATO の PilotAllocation を優先**して読む。
    """
    from trading_agent.models.portfolio import Portfolio as P
    from trading_agent.models.universe import Universe as Uni
    from trading_agent.portfolio.misato import get_pilot_allocations
    from trading_agent.portfolio.personality import (
        RULE_SUMMARY,
        all_personalities,
        effective_max_position_pct,
    )

    with Session(engine) as s:
        # v2.8: broker_mode 別に保有を取得
        from trading_agent.utils.lot_size import get_broker_mode as _gbm_p

        _cur_mode_p = _gbm_p()
        ports = s.exec(
            select(P).where(
                col(P.status) == "active",
                col(P.broker_mode) == _cur_mode_p,
            )
        ).all()
        uni_rows = s.exec(select(Uni)).all()
    # 優先順: Universe.name_ja → 辞書フォールバック → Universe.name → ticker
    from trading_agent.utils.jp_company_names import is_jp_ticker, lookup as _lookup_jp
    def _resolve_jp(u: Uni) -> str:
        if u.name_ja:
            return u.name_ja
        if is_jp_ticker(u.ticker):
            d = _lookup_jp(u.ticker, "")
            if d and d != u.ticker:
                return d
        return u.name or u.ticker
    name_by_ticker = {u.ticker: _resolve_jp(u) for u in uni_rows}
    by_personality: dict[str | None, list[P]] = {}
    for p in ports:
        by_personality.setdefault(p.personality, []).append(p)

    # MISATO の動的配分。未配分（=0）の機は personality.overlay_cash_jpy にフォールバック。
    # v2.8: broker_mode 別に取得（Paper / Live 並行運用）
    from trading_agent.utils.lot_size import get_broker_mode as _gbm_for_alloc

    pilot_allocs = get_pilot_allocations(engine, _gbm_for_alloc())

    # yfinance 現在価格を bulk 取得（snapshot 生成時刻＝リロード時の最新値）
    tickers = sorted({p.ticker for p in ports})
    price_map: dict[str, float] = {}
    if tickers:
        try:
            import yfinance as yf

            from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol

            sym_map = {to_yfinance_symbol(t): t for t in tickers}
            df = yf.download(
                list(sym_map.keys()),
                period="2d",
                interval="1d",
                auto_adjust=True,
                progress=False,
                group_by="ticker",
                threads=True,
            )
            if df is not None and not df.empty:
                for sym, ticker in sym_map.items():
                    try:
                        series = df[sym]["Close"] if len(sym_map) > 1 else df["Close"]
                        closes = [float(x) for x in series.dropna().tolist()]
                        if closes:
                            price_map[ticker] = closes[-1]
                    except Exception:
                        continue
        except Exception:
            pass

    out: list[dict[str, object]] = []
    for pers in all_personalities():
        rows = by_personality.get(pers.name, [])
        invested = sum(float(r.buy_price or 0) * float(r.qty or 0) for r in rows)
        market_value = 0.0
        for r in rows:
            cur = price_map.get(r.ticker, float(r.buy_price or 0))
            market_value += cur * float(r.qty or 0)
        # MISATO 配分 > 0 ならそれを元本に使う（動的）。0 (=未配分) なら 0 元本。
        overlay = float(pilot_allocs.get(pers.name, 0.0))
        cash = overlay - invested
        total = cash + market_value
        pnl = total - overlay
        pnl_pct = (pnl / overlay * 100.0) if overlay else 0.0

        # 保有銘柄（最大 20 件まで）
        holdings = []
        for r in rows[:20]:
            cur = price_map.get(r.ticker, float(r.buy_price or 0))
            qty = float(r.qty or 0)
            cost = float(r.buy_price or 0) * qty
            mkt = cur * qty
            unrealized = mkt - cost
            unrealized_pct = (unrealized / cost * 100) if cost else 0.0
            # v2.8: 単元株（100 株）での実弾相当金額を併記
            # 日本株 (4 桁数字) は単元株 = 100 株、それ以外は 1 株単位
            is_jp_4d = len(r.ticker) == 4 and r.ticker.isdigit()
            lot_size = 100 if is_jp_4d else 1
            lot_equivalent_jpy = cur * lot_size
            # v2.10 Phase 1A-Step2: ピラミッディングステージ
            # v2.10 Phase 1A-Step2 修正 (UI 6): planned=None は「対象外」と明示
            from trading_agent.portfolio.pyramiding import get_stage_label

            planned_qty = getattr(r, "planned_total_qty", None)
            current_alloc_pct: float | None = None
            if planned_qty is None or planned_qty <= 0:
                # Phase 1A-Step2 以前の fill or 旧 portfolio → ピラミッド機能の対象外
                pyramid_stage = "対象外（旧 fill）"
            else:
                pnl_pct_frac = unrealized_pct / 100.0
                pyramid_stage = get_stage_label(pers.name, pnl_pct_frac)
                current_alloc_pct = round(qty / planned_qty * 100.0, 1)

            holdings.append(
                {
                    "ticker": r.ticker,
                    "name": name_by_ticker.get(r.ticker, r.ticker),
                    "qty": int(qty),
                    "buy_price": float(r.buy_price or 0),
                    "current_price": cur,
                    "market_value": mkt,
                    "unrealized": unrealized,
                    "unrealized_pct": unrealized_pct,
                    # v2.1 TASK-SZ4: stop_loss_pct は正値（0.15 = -15%）
                    "stop_price": float(r.buy_price or 0) * (1.0 - float(r.stop_loss_pct or 0)),
                    "target_date": r.target_date.isoformat() if r.target_date else None,
                    # v2.8: 単元未満株モード表示用
                    "lot_size": lot_size,
                    "lot_equivalent_jpy": lot_equivalent_jpy,
                    # v2.10 Phase 1A-Step2: ピラミッディング情報
                    "pyramid_stage": pyramid_stage,
                    "planned_total_qty": planned_qty,
                    "current_alloc_pct": current_alloc_pct,
                    "peak_pnl_pct": (
                        round(float(getattr(r, "peak_pnl_pct", 0) or 0) * 100, 2)
                        if getattr(r, "peak_pnl_pct", None) is not None
                        else None
                    ),
                }
            )

        out.append(
            {
                "name": pers.name,
                "label": pers.label,
                "icon": pers.icon,
                "description": pers.description,
                "rule_summary": RULE_SUMMARY.get(pers.name, ""),
                "accept_stances": list(pers.accept_stances),
                "max_position_pct": pers.max_position_pct,
                "effective_max_pct": effective_max_position_pct(pers, pnl_pct=pnl_pct),
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
            }
        )

    from trading_agent.utils.lot_size import get_max_lot_cost_jpy, is_lot_mode, is_moomoo_live

    lot = is_lot_mode()
    moomoo_live = is_moomoo_live()
    max_lot_cost = get_max_lot_cost_jpy() if lot else None

    # v2.8: 用語の整理
    # broker_mode: "paper" (DB 記録のみ) / "moomoo_live" (本番)
    # share_mode:  "fractional" (1 株) / "lot" (単元株 100 株)
    broker_mode = "moomoo_live" if moomoo_live else "paper"
    share_mode = "lot" if lot else "fractional"

    # 接続状態は重いので、build_snapshot 単発実行時のみチェック（軽量モード時はスキップ）
    moomoo_status = None
    if moomoo_live:
        try:
            from trading_agent.brokers.moomoo import MoomooBroker
            from trading_agent.config import load_settings

            settings = load_settings()
            broker = MoomooBroker.from_settings(settings)
            moomoo_status = broker.is_connected()
        except Exception as exc:
            moomoo_status = {"connected": False, "error": str(exc)}

    note_text = (
        f"単元株モード (Paper): JP 株 100 株単位で fill / DB 記録のみ。1 単元上限 ¥{int(max_lot_cost):,} 超は除外。"
        if lot and not moomoo_live
        else "1 株単位 fill / Paper モード (検証専用)"
        if not lot
        else f"⚡ moomoo Live (本番): リアルマネーで実発注。1 単元上限 ¥{int(max_lot_cost):,}"
    )

    # v2.10: 集中投資 KPI サマリー（D 案: ¥100k 集中投資の検証用）
    # treasury_view から元本/余力を取得し、personalities 集計と組み合わせる
    try:
        from trading_agent.portfolio.misato import treasury_view as _tv_kpi

        _tv = _tv_kpi(engine, broker_mode if broker_mode == "paper" else "live")
        _seed_jpy = float(_tv.get("seed_jpy") or 0)
        _allocated_jpy = float(_tv.get("allocated_jpy") or 0)
        _available_jpy = float(_tv.get("available_jpy") or 0)
    except Exception:
        _seed_jpy = 0.0
        _allocated_jpy = 0.0
        _available_jpy = 0.0

    _total_invested_cost = sum(float(p["invested_jpy"]) for p in out)
    _total_market_value = sum(float(p["market_value_jpy"]) for p in out)
    _total_unrealized_pnl = _total_market_value - _total_invested_cost
    _total_unrealized_pct = (
        (_total_unrealized_pnl / _total_invested_cost * 100.0) if _total_invested_cost else 0.0
    )
    _position_count = sum(int(p["holdings_count"]) for p in out)
    _cash_reserve_pct = (_available_jpy / _seed_jpy * 100.0) if _seed_jpy else 0.0
    _investment_pct = (_total_invested_cost / _seed_jpy * 100.0) if _seed_jpy else 0.0
    # 累積リターン = (現時点総資産 - 元本) / 元本
    _current_total = _available_jpy + _total_market_value
    _cumulative_return_jpy = _current_total - _seed_jpy
    _cumulative_return_pct = (_cumulative_return_jpy / _seed_jpy * 100.0) if _seed_jpy else 0.0

    concentration_kpi = {
        "seed_jpy": _seed_jpy,                          # 元本（treasury 全体）
        "invested_jpy": _total_invested_cost,           # 投入額（取得コスト）
        "market_value_jpy": _total_market_value,        # 現在の時価
        "cash_reserve_jpy": _available_jpy,             # cash 余力
        "unrealized_pnl_jpy": _total_unrealized_pnl,    # 含み損益額
        "unrealized_pnl_pct": _total_unrealized_pct,    # 含み損益率（投入額ベース）
        "cumulative_return_jpy": _cumulative_return_jpy,  # 累積リターン額（元本比）
        "cumulative_return_pct": _cumulative_return_pct,  # 累積リターン率（元本比）
        "position_count": _position_count,              # 保有銘柄数
        "cash_reserve_pct": _cash_reserve_pct,          # 余力率（元本比）
        "investment_pct": _investment_pct,              # 投入率（元本比）
    }

    # v2.10 Phase 2 Mini: 判断精度（過去 30 日）
    try:
        from trading_agent.reporting.judgment_accuracy import compute_judgment_accuracy

        judgment_accuracy = compute_judgment_accuracy(engine, lookback_days=30)
    except Exception as _e:
        judgment_accuracy = {
            "lookback_days": 30,
            "status": "error",
            "error": str(_e),
        }

    # v2.10 Phase 1: ポートフォリオ相関分析（隠れ集中の検出）
    try:
        from trading_agent.portfolio.correlation import compute_correlation_matrix

        correlation_analysis = compute_correlation_matrix(
            engine, broker_mode=broker_mode if broker_mode == "paper" else "live"
        )
    except Exception as _e:
        correlation_analysis = {
            "status": "error",
            "error": str(_e),
        }

    # v2.10 Phase 4: テーマ強度
    try:
        from trading_agent.portfolio.theme_strength import compute_theme_strength

        theme_strength = compute_theme_strength(engine)
    except Exception as _e:
        theme_strength = {"status": "error", "error": str(_e)}

    # v2.10 Phase 5: factor exposure
    try:
        from trading_agent.portfolio.factor_exposure import compute_portfolio_exposure

        factor_exposure = compute_portfolio_exposure(
            engine, broker_mode=broker_mode if broker_mode == "paper" else "live"
        )
    except Exception as _e:
        factor_exposure = {"status": "error", "error": str(_e)}

    # v2.10 Phase 1C: DS 4 機間の銘柄重複度
    try:
        from trading_agent.portfolio.ds_overlap import compute_ds_overlap

        ds_overlap = compute_ds_overlap(
            engine, broker_mode=broker_mode if broker_mode == "paper" else "live"
        )
    except Exception as _e:
        ds_overlap = {"status": "error", "error": str(_e)}

    # v2.10 Phase 2A: portfolio-level リスク指標 (VaR/CVaR/DD)
    try:
        from trading_agent.portfolio.risk_metrics import compute_risk_metrics

        risk_metrics = compute_risk_metrics(engine, lookback_days=60)
    except Exception as _e:
        risk_metrics = {"status": "error", "error": str(_e)}

    return {
        "personalities": out,
        "generated_at": utcnow().strftime("%Y-%m-%d %H:%M"),
        # v2.8: 用語整理（broker_mode / share_mode の 2 軸）
        "broker_mode": broker_mode,                # "paper" / "moomoo_live"
        "share_mode": share_mode,                  # "fractional" / "lot"
        "moomoo_connected": bool(moomoo_status and moomoo_status.get("connected")),
        "moomoo_status": moomoo_status,
        # 旧互換
        "live_mode": lot,                          # = is_lot_mode（旧 is_live_mode）
        "fractional_share_mode": not lot,
        "fractional_share_note": note_text,
        "max_lot_cost_jpy": max_lot_cost,
        # v2.10: 集中投資 KPI サマリー（D 案）
        "concentration_kpi": concentration_kpi,
        # v2.10 Phase 2 Mini: 判断精度（過去 30 日）
        "judgment_accuracy": judgment_accuracy,
        # v2.10 Phase 1: ポートフォリオ相関分析
        "correlation_analysis": correlation_analysis,
        # v2.10 Phase 4: テーマ強度
        "theme_strength": theme_strength,
        # v2.10 Phase 5: factor exposure
        "factor_exposure": factor_exposure,
        # v2.10 Phase 1C: DS 4 機間の銘柄重複度
        "ds_overlap": ds_overlap,
        # v2.10 Phase 2A: VaR/CVaR/DD
        "risk_metrics": risk_metrics,
    }


def _compute_portfolio_dd(engine: Engine, *, current_total: float, lookback_days: int = 60) -> float | None:
    """v2.2 TASK-EX3: portfolio_snapshots から過去 lookback_days 日のピーク値で DD% を算出。

    DD = (current - peak) / peak。
    snapshots が無い時は None を返す（hack 値を返さない＝欺瞞回避）。
    """
    import datetime as dt_mod

    from trading_agent.models.portfolio import PortfolioSnapshot

    if current_total <= 0:
        return None
    cutoff_date = dt_mod.date.today() - dt_mod.timedelta(days=lookback_days)
    with Session(engine) as s:
        snaps = s.exec(
            select(PortfolioSnapshot).where(col(PortfolioSnapshot.date) >= cutoff_date)
        ).all()
    if not snaps:
        return None
    peak = max(snap.total_assets_jpy for snap in snaps)
    peak = max(peak, current_total)  # 今日が最大の場合
    if peak <= 0:
        return None
    return (current_total - peak) / peak  # 負値（DD は減少率）


def _build_misato_outlook(
    *,
    strategy: object,
    briefs_with_boost: list[dict],
    situation_counts: dict[str, int],
    market_info: dict[str, object],
    live_mode: bool = False,
    max_lot_cost: float | None = None,
    max_lot_pct: float | None = None,
    treasury_jpy: float | None = None,
    affordable_count: int = 0,
    opportunity_mode: bool = False,
    opportunity_planned: float = 0,
    opportunity_remaining: float = 0,
    opportunity_selected: int = 0,
    opportunity_rejected: int = 0,
) -> list[str]:
    """KATSURAGI の現状認識・利益期待・予測を決定論で生成（LLM 不使用）。

    KATSURAGI の判断原則: **利益優先**。
    機ごとに予算を「寄せる」のではなく、boost が高い銘柄＝利益期待が高い銘柄から順に
    予算を投じる。outlook は機別の誘導はせず、利益機会と現状認識のみを述べる。

    Returns:
        3-5 行の短文リスト。UI に縦並びで表示する想定。
    """
    out: list[str] = []
    preset = getattr(strategy, "preset", "balanced")

    # v2.9: 機会駆動モード時の説明
    if opportunity_mode:
        out.append(
            f"🎯 機会駆動 fill: 採用 {opportunity_selected} / 却下 {opportunity_rejected} / "
            f"投入 ¥{int(opportunity_planned):,} / cash 保持 ¥{int(opportunity_remaining):,}"
        )
        out.append(
            "  ↑ 質ありき・枠埋め圧力なし。良い案件がなければ cash 保持（4 ガードレール + 質基準）"
        )

    # 1. 戦略の意図（重み付けの判断軸）
    preset_descriptions = {
        "balanced": "ニュース・業界・ピアを均等重視（標準スタンス）",
        "news_focused": "ニュース sentiment を最重視（決算シーズン向き）",
        "trend_focused": "業界トレンドを最重視（業界転換期向き）",
        "peer_focused": "ピア相対力を最重視（競争激化局面向き）",
    }
    out.append(f"📌 {preset}: {preset_descriptions.get(preset, '')}")

    # v2.8: 実弾モード時の予算情報
    if live_mode and max_lot_cost is not None:
        total = len(briefs_with_boost)
        if total > 0:
            pct_aff = int(affordable_count / total * 100)
            lot_pct_str = f"{int((max_lot_pct or 0) * 100)}%" if max_lot_pct else "—"
            treasury_str = f"¥{int(treasury_jpy or 0):,}"
            out.append(
                f"💴 実弾モード: treasury {treasury_str} × {lot_pct_str} = 1 単元上限 ¥{int(max_lot_cost):,} → 予算内候補 {affordable_count}/{total} 件 ({pct_aff}%)"
            )

    # 2. 市場 regime（地合いの認識）
    regime = market_info.get("regime", "unknown")
    nikkei = market_info.get("nikkei_change_pct")
    if regime == "risk_off":
        out.append(f"⚠ 市場 risk_off（日経 {nikkei:+.1f}%）→ 新規 fill 停止（市場ガード発動）")
    elif regime == "risk_on" and nikkei is not None:
        out.append(f"📈 市場 risk_on（日経 {nikkei:+.1f}%）→ 通常配分続行")
    elif regime == "neutral" and nikkei is not None:
        out.append(f"➖ 市場 neutral（日経 {nikkei:+.1f}%）→ 通常配分続行")

    # 3. 利益期待の集計（boost 分布から）
    if briefs_with_boost:
        sorted_briefs = sorted(briefs_with_boost, key=lambda x: -x["boost"])
        # 上位 3 件と平均 boost
        top3 = sorted_briefs[:3]
        avg_boost = sum(b["boost"] for b in briefs_with_boost) / len(briefs_with_boost)
        positive_count = sum(1 for b in briefs_with_boost if b["boost"] > 0.05)
        out.append(
            f"💰 利益期待: 候補 {len(briefs_with_boost)} 件中 {positive_count} 件で boost > +0.05 / 平均 {avg_boost:+.2f}"
        )
        # トップ銘柄を列挙（機関係なく利益優先）
        top_label = " / ".join(
            f"{b.get('name') or b['ticker']} ({b['boost']:+.2f})"
            for b in top3
            if b["boost"] > 0.05
        )
        if top_label:
            out.append(f"🎯 採用優先: {top_label}")

    # 4. 例外ブロック警告
    blocked_count = sum(1 for b in briefs_with_boost if b.get("blocked"))
    if blocked_count > 0:
        out.append(f"🚧 例外ブロック {blocked_count} 件（決算前/ピア最下位 等）→ 次回 dispatch でも見送り")

    # 5. データ品質の注意喚起（情報透明性）
    unknown_count = situation_counts.get("unknown", 0)
    if briefs_with_boost and unknown_count >= len(briefs_with_boost) * 0.3:
        out.append(f"⚠ 技術指標 unknown {unknown_count} 件 → boost が peer のみ依存のリスク")

    # 6. B3: industry_score 未実装の明示
    if preset == "trend_focused":
        out.append("⚠ trend_focused: industry スコア未配線（Phase B-2 で実装予定）→ 現状は事実上 balanced と同等")

    # 7. B1: 全体のデータ品質サマリ
    if briefs_with_boost:
        dq_counts = {"measured": 0, "unavailable": 0, "not_implemented": 0}
        for b in briefs_with_boost:
            for state in (b.get("data_quality") or {}).values():
                if state in dq_counts:
                    dq_counts[state] += 1
        total_cells = sum(dq_counts.values())
        if total_cells > 0:
            measured_pct = int(dq_counts["measured"] / total_cells * 100)
            out.append(
                f"📊 データ品質: 実測 {dq_counts['measured']} / 未取得 {dq_counts['unavailable']} / 未実装 {dq_counts['not_implemented']} "
                f"(measured {measured_pct}%)"
            )

    return out


def _build_wille_section(engine: Engine) -> dict[str, object]:
    """v2.8 INVESTIGELION: WILLE 組織の RITSUKO Brief + MISATO 戦略を snapshot に。

    新フロー:
      - RITSUKO: 候補銘柄ごとに TickerBrief（5 中立スコア + 生データ）
      - MISATO:  戦略パラメータ（balanced 等） + 重み付け boost
      - 銘柄→1機指名は廃止。priority は dispatch 時に proposals に付与される
    """
    try:
        import os as _os

        from trading_agent.portfolio.misato import _build_candidate_pool
        from trading_agent.wille import misato as wille_misato
        from trading_agent.wille import ritsuko as wille_ritsuko

        pool = _build_candidate_pool(engine)
        if not pool:
            return {
                "ritsuko": {"briefs": [], "market": {"regime": "unknown"}, "situation_counts": {}},
                "misato": {"strategy": wille_misato.MisatoStrategy().as_dict()},
            }

        # === RITSUKO Brief 構築（5 中立スコア） ===
        briefs = wille_ritsuko.build_briefs_from_pool(pool, engine=engine)

        # v2.8: 実弾モード時、各銘柄が予算内で買えるかを計算
        # treasury 残高 × WILLE_MAX_LOT_PCT を 1 単元上限とする
        from trading_agent.portfolio.misato import treasury_view as _tv
        from trading_agent.utils.lot_size import (
            can_afford_one_lot,
            get_lot_size,
            get_max_lot_cost_jpy,
            get_max_lot_pct,
            is_live_mode,
        )

        live_mode = is_live_mode()
        treasury_for_lot = None
        if live_mode:
            try:
                treasury_for_lot = float(_tv(engine).get("seed_jpy") or 0)
            except Exception:
                treasury_for_lot = 0.0
        max_lot_cost = get_max_lot_cost_jpy(treasury_for_lot) if live_mode else None
        max_lot_pct = get_max_lot_pct(treasury_for_lot) if live_mode else None
        affordable_count = 0
        for ticker, brief in briefs.items():
            price = brief.current_price or 0
            if price > 0 and (
                not live_mode or can_afford_one_lot(ticker, price, treasury_for_lot)
            ):
                affordable_count += 1

        # === 銘柄名の lookup（Universe テーブル → なければ ticker そのまま）===
        name_by_ticker: dict[str, str] = {}
        try:
            from sqlmodel import Session, col, select as _sel_uv

            from trading_agent.models.universe import Universe

            with Session(engine) as _s:
                rows = _s.exec(
                    _sel_uv(Universe).where(col(Universe.ticker).in_(list(briefs.keys())))
                ).all()
                # 優先順: name_ja（日本語）→ 辞書フォールバック → name（英語）→ ticker
                from trading_agent.utils.jp_company_names import is_jp_ticker, lookup as _lookup_jp
                for r in rows:
                    jp_dict = _lookup_jp(r.ticker, "") if is_jp_ticker(r.ticker) else ""
                    name_by_ticker[r.ticker] = (
                        r.name_ja or (jp_dict if jp_dict and jp_dict != r.ticker else "") or r.name or r.ticker
                    )
        except Exception:
            pass

        # === MISATO 戦略 + 各銘柄の boost を計算 ===
        preset_name = _os.environ.get("MISATO_STRATEGY", "balanced")
        if preset_name not in ("balanced", "news_focused", "trend_focused", "peer_focused"):
            preset_name = "balanced"
        strategy = wille_misato.MisatoStrategy.from_preset(preset_name)  # type: ignore[arg-type]

        # 各 Brief に対する MISATO boost（戦略適用後）
        brief_with_boost = []
        for ticker, brief in briefs.items():
            boost = wille_misato.compute_boost_from_brief(brief, strategy)
            verdict = wille_misato.check_proposal_exceptions(ticker, brief)
            # v2.8: 実弾モード時、1 単元コストと予算内かを判定
            price_val = brief.current_price or 0
            lot_size_v = get_lot_size(ticker) if live_mode else 1
            lot_cost_v = price_val * lot_size_v if price_val > 0 else None
            affordable = (
                price_val > 0 and (not live_mode or can_afford_one_lot(ticker, price_val))
            )
            brief_with_boost.append(
                {
                    "ticker": ticker,
                    "name": name_by_ticker.get(ticker, ticker),
                    "current_price": round(brief.current_price, 2) if brief.current_price else None,
                    "lot_size": lot_size_v,
                    "lot_cost_jpy": round(lot_cost_v, 0) if lot_cost_v else None,
                    "affordable": affordable,
                    "scores": {
                        "news_sentiment": round(brief.news_sentiment_score, 3),
                        "industry": round(brief.industry_score, 3),
                        "peer": round(brief.peer_score, 3),
                        "event": round(brief.event_score, 3),
                        "deep": round(brief.deep_brief_score, 3),
                    },
                    "boost": round(boost, 3),
                    "blocked": verdict.blocked,
                    "block_reason": verdict.reason if verdict.blocked else "",
                    "technicals": {
                        "rsi": brief.technicals.rsi,
                        "macd_signal": brief.technicals.macd_signal,
                        "trend": brief.technicals.trend,
                        "situation": brief.technicals.situation,
                    },
                    "news_count": len(brief.news),
                    "industry_sector": brief.industry.sector,
                    "upcoming_event_count": len(brief.upcoming_events),
                    "data_quality": dict(brief.data_quality),  # B1: 情報透明性
                }
            )

        situation_counts = {
            s: sum(1 for b in briefs.values() if b.technicals.situation == s)
            for s in ("bullish", "bearish", "pullback", "neutral", "breakout", "unknown")
        }

        # 後方互換: 旧 UI が参照する reports[] / misato_orders[] を briefs から派生
        legacy_reports = [
            {
                "ticker": b["ticker"],
                "situation": b["technicals"]["situation"],
                "confidence": round(briefs[b["ticker"]].technicals.rsi or 0.0, 2) if briefs[b["ticker"]].technicals.rsi else 0.0,
                "signals": [
                    s for s in [
                        f"RSI={b['technicals']['rsi']:.0f}" if b['technicals']['rsi'] is not None else None,
                        f"MACD={b['technicals']['macd_signal']}" if b['technicals']['macd_signal'] else None,
                        f"trend={b['technicals']['trend']}" if b['technicals']['trend'] else None,
                    ] if s
                ],
                "recommended_pilots": [],
                "reasoning": f"boost={b['boost']:+.2f}",
            }
            for b in brief_with_boost
        ]
        # v2.8: 市場 regime 実取得（日経/TOPIX 当日変動）
        market_info = {"regime": "unknown", "notes": []}
        try:
            mkt = wille_ritsuko.detect_market_regime_live()
            nikkei = mkt.get("nikkei_change_pct")
            topix = mkt.get("topix_change_pct")
            notes_list = []
            if nikkei is not None:
                notes_list.append(f"日経 {nikkei:+.2f}%")
            if topix is not None:
                notes_list.append(f"TOPIX {topix:+.2f}%")
            market_info = {
                "regime": mkt.get("regime", "unknown"),
                "nikkei_change_pct": nikkei,
                "topix_change_pct": topix,
                "is_risk_off": bool(mkt.get("is_risk_off", False)),
                "notes": notes_list,
            }
        except Exception as exc:
            _log.warning("wille_market_regime_failed", error=str(exc))

        # v2.8: MISATO 戦略の展望テキスト（決定論で生成・LLM 不使用）
        outlook_lines = _build_misato_outlook(
            strategy=strategy,
            briefs_with_boost=brief_with_boost,
            situation_counts=situation_counts,
            market_info=market_info,
            live_mode=live_mode,
            max_lot_cost=max_lot_cost,
            max_lot_pct=max_lot_pct,
            treasury_jpy=treasury_for_lot,
            affordable_count=affordable_count,
        )

        return {
            "ritsuko": {
                "briefs": brief_with_boost,
                "reports": legacy_reports,  # 後方互換
                "market": market_info,
                "situation_counts": situation_counts,
            },
            "misato": {
                "strategy": strategy.as_dict(),
                "blocked_count": sum(1 for b in brief_with_boost if b["blocked"]),
                "market_guard_armed": bool(market_info.get("is_risk_off", False)),
                "outlook": outlook_lines,
                # v2.8: 実弾モード時の上限金額（割合表示用）
                "max_lot_pct": max_lot_pct,
                "max_lot_cost_jpy": max_lot_cost,
                "treasury_for_lot_jpy": treasury_for_lot,
                "affordable_count": affordable_count,
            },
            "misato_orders": [],  # 後方互換
        }
    except Exception as exc:
        _log.warning("wille_section_failed", error=str(exc))
        return {
            "ritsuko": {"briefs": [], "reports": [], "market": {"regime": "unknown"}, "situation_counts": {}},
            "misato": {"strategy": {}},
            "misato_orders": [],
            "error": str(exc),
        }


def _build_misato_section(engine: Engine) -> dict[str, object]:
    """MISATO の現状（treasury・dry-run 配分案・HALT・昇格候補）を snapshot に載せる。

    予算は **treasury の未配分残高** を使う（ユーザー入金から動的）。
    """
    from trading_agent.portfolio.misato import (
        DEFAULT_HALT_FILE,
        MAX_BUDGET_PER_DISPATCH_JPY,
        MAX_BUDGET_PER_PILOT_JPY,
        PROMOTION_THRESHOLDS,
        auto_trade_view,
        check_halt,
        dispatch,
        plan_to_dict,
        treasury_view,
    )

    halted, halt_reason = check_halt()
    # v2.8: broker_mode 別の treasury（Paper / Live 並行）
    from trading_agent.utils.lot_size import get_broker_mode as _gbm_t

    treasury = treasury_view(engine, _gbm_t())
    auto_trade = auto_trade_view(engine)
    available = float(treasury["available_jpy"])

    base_payload: dict[str, object] = {
        "halted": halted,
        "halt_reason": halt_reason,
        "halt_file_path": str(DEFAULT_HALT_FILE),
        "treasury": treasury,
        "auto_trade": auto_trade,
        "default_budget_jpy": round(min(max(available, 0.0), MAX_BUDGET_PER_DISPATCH_JPY)),
        "max_per_dispatch_jpy": MAX_BUDGET_PER_DISPATCH_JPY,
        "max_per_pilot_jpy": MAX_BUDGET_PER_PILOT_JPY,
        "promotion_thresholds": PROMOTION_THRESHOLDS,
        "plan": None,
    }
    if halted or available <= 0:
        return base_payload

    try:
        plan = dispatch(engine, total_budget_jpy=available, approve=False)
        base_payload["plan"] = plan_to_dict(plan)
    except Exception as exc:  # noqa: BLE001
        _log.warning("misato_section_failed", error=str(exc))
        base_payload["error"] = str(exc)
    return base_payload


def _build_pending_decisions(engine: Engine) -> dict[str, dict[str, object]]:
    """decisions テーブルから決裁待ちの buy 候補を取り出し、推奨度順に整形。

    各 decision に `commander_rec` の最新 1 件と `judge_verdict`（MAGI 3 審判）の最新を
    join し、`commander_recommendation` `commander_counter` `verdicts` を含める。

    ソート優先順（推奨度の高い順）:
        1. commander 推奨カテゴリ: 買い → 保留 → その他 → 推奨なし
        2. gendo_stance: 推し → 要検討 → 静観 → 不明
        3. score: 降順（None は最後）
        4. expected_return: 降順（None は最後）
        5. id: 降順（最新優先）
    """
    from trading_agent.models.decisions import Decision
    from trading_agent.models.magi import CommanderRec, JudgeVerdict
    from trading_agent.models.universe import Universe as Uni

    stance_priority: dict[str, int] = {"推し": 0, "要検討": 1, "静観": 2}

    def commander_category(rec: str | None) -> int:
        if not rec:
            return 3
        if "買い" in rec[:10]:
            return 0
        if "保留" in rec[:10]:
            return 1
        return 2

    with Session(engine) as s:
        rows = s.exec(
            select(Decision)
            .where(col(Decision.action) == "buy")
            .where(col(Decision.status).in_(("awaiting", "approved")))
        ).all()
        commanders: dict[int, CommanderRec] = {}
        for cr in s.exec(
            select(CommanderRec).order_by(col(CommanderRec.created_at).asc())
        ).all():
            if cr.decision_id is not None:
                commanders[cr.decision_id] = cr
        verdicts_by_decision: dict[int, dict[str, str]] = {}
        for v in s.exec(
            select(JudgeVerdict).order_by(col(JudgeVerdict.created_at).asc())
        ).all():
            if v.decision_id is None:
                continue
            verdicts_by_decision.setdefault(v.decision_id, {})[v.judge] = v.verdict
        universe_meta: dict[str, dict[str, str]] = {}
        for u in s.exec(select(Uni)).all():
            universe_meta[u.ticker] = {
                "name": u.name or "",
                "market": u.market or "",
                "sector": u.sector or "",
            }

    def sort_key(d: Decision) -> tuple[int, int, float, float, int]:
        cmd = commanders.get(d.id or -1)
        return (
            commander_category(cmd.recommendation if cmd else None),
            stance_priority.get(d.gendo_stance or "", 9),
            -(float(d.score) if d.score is not None else -1.0),
            -(float(d.expected_return) if d.expected_return is not None else -999.0),
            -(d.id or 0),
        )

    sorted_rows = sorted(rows, key=sort_key)

    # 価格履歴を一括取得（30 日分の終値・sparkline 描画用）。失敗しても続行。
    histories = _fetch_price_histories(
        [d.ticker for d in sorted_rows if d.id is not None], days=30
    )

    # 要件①「何株・いくらで」: order_list の推奨株数 / 想定価格 / stop を join。
    # build_order_items は yfinance 価格取得のみ（LLM なし・コスト0）。失敗しても続行。
    order_items: dict[int, object] = {}
    try:
        from trading_agent.reporting.order_list import build_order_items

        for it in build_order_items(engine, available_jpy=None):
            if it.decision_id:
                order_items[it.decision_id] = it
    except Exception as exc:  # noqa: BLE001
        _log.warning("order_items_join_failed", error_type=type(exc).__name__)

    out: dict[str, dict[str, object]] = {}
    for d in sorted_rows:
        if d.id is None:
            continue
        card_id = f"d{d.id}"
        cmd = commanders.get(d.id)
        meta = universe_meta.get(d.ticker, {"name": "", "market": "JP", "sector": ""})
        oi = order_items.get(d.id)
        _hist = histories.get(d.ticker, [])
        # 想定価格: order_list の現在値 → 無ければ 30日履歴の最新終値 → 無ければ entry_price。
        # （予算非依存。何株は予算待ちでも価格/stop は出せる）
        _sugg = (
            (oi.current_price if (oi and oi.current_price) else None)
            or (float(_hist[-1]) if _hist else None)
            or d.entry_price
        )
        _stop = _sugg * (1.0 - abs(float(d.stop_pct or 0.10))) if _sugg else None
        out[card_id] = {
            "decision_id": d.id,
            "ticker": d.ticker,
            "name": meta["name"],
            "market": meta["market"],
            "sector": meta["sector"],
            "action": d.action,
            "status": d.status,
            "gendo_stance": d.gendo_stance or "—",
            "thesis": d.thesis_at_decision or "",
            "entry_price": d.entry_price,
            "stop_pct": d.stop_pct,
            "target_period_days": d.target_period_days,
            "score": d.score,
            "expected_return": d.expected_return,
            "commander_recommendation": cmd.recommendation if cmd else None,
            "commander_counter": cmd.counter_argument if cmd else None,
            "verdicts": verdicts_by_decision.get(d.id, {}),
            "price_history_30d": histories.get(d.ticker, []),
            # 要件①: 何株・いくらで・stop（order_list から join。約定ボタンのプリフィル元）
            "recommended_shares": oi.recommended_shares if oi else None,
            "suggested_price": _sugg,
            "stop_price": _stop,
            "estimated_cost_jpy": oi.estimated_cost_jpy if oi else None,
            "target_price": oi.target_price if oi else None,
            "expected_return_pct": oi.expected_return_pct if oi else None,
        }
    return out


def _build_topics_section(engine: Engine, limit: int = 30) -> dict[str, object]:
    """topics テーブル → snapshot.topics（重要度順・category 別 count）。

    朝バッチの topics_collector が `topics` テーブルに保存した記事を、UI 表示用に整形する。
    全 archived=False を対象に、importance を high → medium → low 順、その中で
    collected_at の降順で並べる。表示は最新 `limit` 件まで。
    """
    from trading_agent.models.topics import Topic

    IMP_ORDER: dict[str, int] = {"high": 0, "medium": 1, "low": 2}

    with Session(engine) as s:
        rows = s.exec(
            select(Topic).where(col(Topic.is_archived) == False)  # noqa: E712
        ).all()

    counts: dict[str, int] = {"all": 0, "macro": 0, "sector": 0, "stock": 0}
    for r in rows:
        counts["all"] += 1
        cat = r.category
        if cat in counts:
            counts[cat] += 1

    sorted_rows = sorted(
        rows,
        key=lambda r: (
            IMP_ORDER.get(r.importance, 99),
            -(r.collected_at.timestamp() if r.collected_at else 0.0),
        ),
    )

    items = [
        {
            "id": r.id,
            "importance": r.importance,
            "category": r.category,
            "headline": r.headline,
            "summary": (r.summary or "")[:240],
            "source": r.source,
            "url": r.source_url,
            "affected_tickers": list(r.affected_tickers or []),
            "impact_text": r.impact_text,
            "collected_at": (
                r.collected_at.strftime("%Y-%m-%d %H:%M") if r.collected_at else ""
            ),
        }
        for r in sorted_rows[:limit]
    ]

    return {"counts": counts, "items": items}


def _build_sell_section(engine: Engine) -> dict[str, dict[str, object]]:
    """holdings に対する売り推奨を snapshot.sell に流し込む。

    sell_signals テーブルから ticker ごとに最新 1 件を取り、UI 表示用の dict に整形する。
    sell_recommender は active な portfolio に対してのみ動くため、保有 0 件なら自然に空 dict。
    """
    from trading_agent.models.signals import SellSignal

    out: dict[str, dict[str, object]] = {}
    with Session(engine) as s:
        rows = s.exec(
            select(SellSignal).order_by(col(SellSignal.created_at).desc())
        ).all()
    seen: set[str] = set()
    for row in rows:
        if row.ticker in seen:
            continue
        seen.add(row.ticker)
        out[f"sell-{row.ticker.lower()}"] = {
            "ticker": row.ticker,
            "signal_type": row.signal_type,
            "score": int(row.score),
            "ai_confidence": float(row.ai_confidence or 0.0),
        }
    return out


def _market_cap(ticker: str) -> float | None:
    """yfinance fast_info の時価総額（Altman Z 用・best-effort）。"""
    try:
        import yfinance as yf

        sym = f"{ticker}.T" if _is_jp(ticker) else ticker
        return float(yf.Ticker(sym).fast_info.market_cap)
    except Exception:
        return None


def _credibility_flag(ticker: str, verdicts: list) -> str:
    """2期財務→信用性(S5)。MELCHIOR反証(S6)を付与し credibility_flag を返す。

    v2.5 TASK-P12: 旧 失敗時 "ok" は false negative（未検証なのに安全扱い）。
    新 失敗時は "unknown" を返し、defense.verify で default_hold=True に寄せる。
    """
    try:
        fin = fetch_financials(ticker, market_cap=_market_cap(ticker))
    except Exception as exc:
        _log.warning("credibility_failed", ticker=ticker, error=str(exc))
        return "unknown"  # 旧 "ok" → "unknown"
    if fin is None:
        return "unknown"  # 旧 "ok" → "unknown"
    cred = assess_credibility(fin)
    counter = melchior_credibility_counter(cred)
    if counter:
        for v in verdicts:
            if v.judge == "MELCHIOR":
                v.counter_within_domain = [*v.counter_within_domain, *counter]
    return cred.credibility_flag


def _serialize_candidate(
    verdicts: list, sizing: dict[str, object], *,
    credibility_flag: str = "ok", sizerec: SizeRec | None = None,
) -> dict[str, object]:
    judges = []
    for v in verdicts:
        word, color = _VD_DISPLAY.get(v.verdict, ("—", "var(--ink-3)"))
        judges.append(
            {
                "judge": v.judge,
                "role": _ROLE.get(v.judge, ""),
                "dot": _DOT.get(v.judge, "#888888"),
                "verdict": v.verdict,  # 生の可否（詳細パネルの行マッピング用）
                "verdict_word": word,
                "color": color,
                "dim": v.verdict == "na",
                "reason": v.reason,
                "counter": [c.get("claim", "") for c in (v.counter_within_domain or [])],
            }
        )
    # 統合機構(B4)・防御層(B3・信用性S5)・碇司令(B5)
    split = classify_split(verdicts)
    vr = verify(verdicts, credibility_flag=credibility_flag)
    cmd = command(verdicts, split, vr)
    # 碇の構え＝投票審判(業績・文脈)のみで算定（BALTHASARは投票外＝非投票の決定に整合）
    gendo = derive_gendo_stance(verdicts, default_hold=vr.default_hold)
    # GENDO推奨カード（初心者コーチ・守り主導・攻めは灰色）
    card = gendo_recommend(
        verdicts, split, vr, cmd,
        credibility_flag=credibility_flag, offense_strong=False, sizing=sizerec,
    )
    verification = {
        "default_decision": "保留" if vr.default_hold else "可",
        "flags": [  # 設計の3フラグ（時点は数値照合に内包）
            {
                "label": "数値照合",
                "status": "ok" if (vr.figures_checked and vr.time_ok) else "warn",
            },
            {"label": "信用性", "status": vr.credibility_flag},
            {"label": "碇MAGI準拠", "status": "ok" if cmd.magi_compliant else "warn"},
        ],
        "unverified": vr.unverified_claims,
    }
    return {
        "judges": judges,
        "split": split.label,
        "split_interp": split.interpretation,
        "gendo": gendo,
        "sizing": sizing,
        "verification": verification,
        "commander": {
            "recommendation": cmd.recommendation,
            "counter": cmd.counter_argument,
            "src_note": cmd.src_note,
            "compliant": cmd.magi_compliant,
        },
        "gendo_card": {  # 初心者向けGENDO推奨カード（守り主導・攻めは灰色・決めるのは人間）
            "action": card.action,
            "sleeve": card.sleeve,
            "reason": card.reason,
            "counter": card.counter,
            "guardrail": card.guardrail,
            "defense_confidence": card.defense_confidence,
            "offense_confidence": card.offense_confidence,
            "learn_note": card.learn_note,
        },
    }


def _maybe_llm_tool(engine: Engine, *, live: bool) -> LLMCallTool | None:
    """Anthropic キーがあれば CASPER 用の LLMCallTool を作る（live のみ・任意）。

    キー未設定なら None＝決定論版 CASPER のまま（コスト0）。予算超過時は LLMCallTool 内で拒否。
    """
    if not live:
        return None
    try:
        from trading_agent.config import get_settings

        key = get_settings().anthropic_api_key
    except Exception:
        return None
    if not key:
        return None
    return LLMCallTool(engine, anthropic_client=AnthropicClient(key))


async def _build_candidates(
    live: bool,
    total_assets: float,
    cash: float,
    usdjpy: float,
    *,
    llm_tool: LLMCallTool | None = None,
) -> dict:
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

        # P3-7：CASPER を Sonnet 解釈に格上げ（キーがある時のみ・失敗時は決定論版のまま）
        if llm_tool is not None:
            upgraded = await casper_llm(ticker, news=news, llm_tool=llm_tool)
            verdicts = [upgraded if v.judge == "CASPER" else v for v in verdicts]

        # S5b/S6：信用性フィルタ→MELCHIOR反証＋credibility_flag（live のみ）
        credibility_flag = _credibility_flag(ticker, verdicts) if live else "ok"

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
        out[card_id] = _serialize_candidate(
            verdicts, sizing, credibility_flag=credibility_flag, sizerec=rec
        )
    return out


def _serialize_gate(g) -> dict[str, object]:
    """GateResult → UI 用 dict。"""
    return {
        "passed": g.passed,
        "n": g.n,
        "actionable": g.actionable,
        "broker_mode": g.broker_mode,
        "max_drawdown": round(g.max_drawdown, 4),
        "regimes_present": list(g.regimes_present),
        "notes": list(g.notes),
        "summary": g.summary(),
        "criteria": [
            {
                "name": c.name,
                "value": c.value,
                "threshold": c.threshold,
                "passed": c.passed,
            }
            for c in g.criteria
        ],
    }


def _build_gates_section(engine: Engine) -> dict[str, object]:
    """増額ゲート⑥を paper/live 別 + combined(参考・増額不可) で評価して返す。

    paper=システム edge 検証 / live=実運用実績 を **混ぜない**（broker_mode で分離）。
    combined は actionable=False（増額根拠にしない・「参考」ラベル必須）。
    """
    from trading_agent.evaluation.gate import (
        combined_gate_reference,
        official_gate_evaluation,
    )

    out: dict[str, object] = {}
    for mode in ("paper", "live"):
        try:
            out[mode] = _serialize_gate(
                official_gate_evaluation(engine, broker_mode=mode)
            )
        except Exception as exc:  # noqa: BLE001
            out[mode] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        out["combined"] = _serialize_gate(combined_gate_reference(engine))
    except Exception as exc:  # noqa: BLE001
        out["combined"] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


def _build_scaling_section(engine: Engine) -> dict[str, object]:
    """段階大規模化（paper 専用）: treasury_view(paper) + 資本注入履歴 + ladder。"""
    from trading_agent.models.misato_treasury import TreasuryInjection
    from trading_agent.portfolio.misato import PAPER_CEILING_JPY, treasury_view

    tv = treasury_view(engine, "paper")
    injections: list[dict[str, object]] = []
    with Session(engine) as s:
        rows = s.exec(
            select(TreasuryInjection)
            .where(col(TreasuryInjection.broker_mode) == "paper")
            .order_by(col(TreasuryInjection.created_at).asc())
        ).all()
        for r in rows:
            injections.append(
                {
                    "amount_jpy": round(float(r.amount_jpy)),
                    "reason": r.reason,
                    "tier_after_jpy": round(float(r.tier_after_jpy)),
                    "created_at": r.created_at.strftime("%Y-%m-%d %H:%M")
                    if r.created_at
                    else None,
                }
            )
    return {
        "current_risk_budget_jpy": tv.get("current_risk_budget_jpy"),
        "target_ceiling_jpy": tv.get("target_ceiling_jpy") or PAPER_CEILING_JPY,
        "ceiling_progress_pct": tv.get("ceiling_progress_pct"),
        "ladder_jpy": [100000, 300000, 600000, 1000000],
        "injections": injections,
        "seed_jpy": tv.get("seed_jpy"),
        "deposit_count": tv.get("deposit_count"),
    }


async def build(*, live: bool, prefer_moomoo: bool, light: bool = False) -> dict[str, object]:
    eng = get_engine(Path(tempfile.gettempdir()) / "snapshot.sqlite")
    create_all(eng)

    # 設定と本番 DB を読み込む（口座状態の Portfolio.active コスト算出に必要）。
    # 実運用スクリプト群（load_universe / run_morning_batch / run_paper / run_evaluation）が
    # `data/trading.sqlite` を使っているので、それに合わせる。
    from trading_agent.config import load_settings

    settings = load_settings()
    prod_engine = get_engine(Path("data") / "trading.sqlite")
    create_all(prod_engine)

    positions, broker_src = load_positions(
        prefer_moomoo=prefer_moomoo, settings=settings, engine=prod_engine
    )
    account, account_src = load_account(prod_engine, settings)
    total = account.total_assets
    cash = account.cash
    # v2.5 TASK-P11: 取得失敗時は None。下流で US 銘柄を扱う時は明示エラーが望ましい。
    usdjpy_raw = _usdjpy(live)
    # 下流の数値計算（米株換算）は usdjpy を float で受けるため、None → 0.0 で渡し、
    # それを使う側（候補/保有/ZEELE 等）が is_jp=True 経路だけ動かせば OK。
    # 同時に snapshot にも raw を出して UI で「取得失敗」を可視化。
    usdjpy = usdjpy_raw if usdjpy_raw is not None else 0.0

    # 保有（口座未接続なら空＝現金100%）
    holdings: dict[str, dict[str, object]] = {}
    if positions:
        primary: Fetcher = _live_primary if live else _demo_primary
        secondary: Fetcher = _live_secondary if live else _demo_secondary
        tool = MarketDataTool(eng, fetcher=primary, reconcile_fetcher=secondary)
        out = await tool.execute(
            MarketDataInput(tickers=[p.code for p in positions], fields=["current_price"])
        )
        # 保有カード拡充用メタ: 名称/セクター(Universe) + 目標/stop/取得日(Portfolio) + 30日履歴
        _codes_h = [p.code for p in positions]
        _hist_h = _fetch_price_histories(_codes_h, days=30)
        _uni_h: dict[str, dict[str, str]] = {}
        _pf_h: dict[str, dict[str, object]] = {}
        with Session(prod_engine) as _sh:
            from trading_agent.models.portfolio import Portfolio as _PfH

            for _u in _sh.exec(
                select(Universe).where(col(Universe.ticker).in_(_codes_h))
            ).all():
                _uni_h[_u.ticker] = {
                    "name": _u.name or "",
                    "sector": _u.sector or "",
                    "market": _u.market or "",
                }
            for _r in _sh.exec(
                select(_PfH)
                .where(col(_PfH.status) == "active")
                .where(col(_PfH.ticker).in_(_codes_h))
            ).all():
                _pf_h[_r.ticker] = {
                    "target_pct": _r.target_pct,
                    "stop_loss_pct": _r.stop_loss_pct,
                    "buy_date": str(_r.buy_date) if _r.buy_date else None,
                    "strategy": _r.strategy_category or "",
                    "thesis": _r.thesis or "",
                    "target_period_days": _r.target_period_days,
                }
        for p in positions:
            price = p.nominal_price or out.data.get(p.code, {}).get("current_price")
            pnl = None
            if p.pl_ratio is not None:
                pnl = {
                    "ratio_display": f"{p.pl_ratio:+.1f}%",
                    "direction": "up" if p.pl_ratio >= 0 else "down",
                }
            elif price is not None and p.cost_price:
                r = (price - p.cost_price) / p.cost_price * 100.0
                pnl = {"ratio_display": f"{r:+.1f}%", "direction": "up" if r >= 0 else "down"}
            _cost = float(p.cost_price or 0.0)
            _qty = float(p.qty or 0)
            _unreal = ((price - _cost) * _qty) if (price is not None and _cost) else None
            _um = _uni_h.get(p.code, {})
            _pm = _pf_h.get(p.code, {})
            holdings[p.code] = {
                "price_display": _fmt_price(p.code, price) if price is not None else None,
                "current_price": price,
                "reconciliation": out.reconciliation.get(p.code, "single"),
                "as_of": out.data_asof.strftime("%Y-%m-%d %H:%M") if out.data_asof else None,
                "pnl": pnl,
                "qty": _qty,
                "cost_price": _cost,
                "cost_jpy": _cost * _qty,
                "unrealized_jpy": _unreal,
                "name": _um.get("name") or p.code,
                "sector": _um.get("sector") or "",
                "market": _um.get("market") or "",
                "target_pct": _pm.get("target_pct"),
                "stop_pct": _pm.get("stop_loss_pct"),
                "buy_date": _pm.get("buy_date"),
                "strategy": _pm.get("strategy"),
                "thesis": _pm.get("thesis"),
                "target_period_days": _pm.get("target_period_days"),
                "history_30d": _hist_h.get(p.code, []),
            }

    if light:
        # 軽量リフレッシュ: 候補生成（唯一の LLM 経路）をスキップし、前回 snapshot の
        # candidates を再利用。口座/保有/決定など他セクションは DB+yfinance で作り直す（コスト0）。
        try:
            _prior_path = (
                Path(__file__).resolve().parent.parent
                / "ui" / "public" / "data" / "snapshot.json"
            )
            _prior = json.loads(_prior_path.read_text(encoding="utf-8"))
            candidates = _prior.get("candidates", {})
            # codex P0: stale メタは candidates dict に同居させない（UI が Object.keys(candidates) を
            # 候補 ID として数えるため件数が狂う）。candidates_meta に分離し、candidates は card_id→候補
            # の純 map に保つ。前回版が誤って入れた stale/source_generated_at は除去する。
            if isinstance(candidates, dict):
                candidates.pop("stale", None)
                candidates.pop("source_generated_at", None)
            # codex P1: source は「候補生成時刻」を保持。prior の candidates_meta を優先し、
            # 無ければ generated_at fallback（generated_at は価格/保有更新で上書きされ得るため二次）。
            _prior_meta = _prior.get("candidates_meta") or {}
            _src = _prior_meta.get("source_generated_at") or _prior.get("generated_at")
            candidates_meta = {"stale": True, "source_generated_at": _src}
        except Exception:
            candidates = {}
            candidates_meta = {"stale": True, "source_generated_at": None}
    else:
        llm_tool = _maybe_llm_tool(eng, live=live)
        candidates = await _build_candidates(live, total, cash, usdjpy, llm_tool=llm_tool)
        # full 生成＝候補生成時刻を確定保存（refresh では触らない）。
        candidates_meta = {"stale": False, "source_generated_at": utcnow().strftime("%Y-%m-%d %H:%M")}

    holdings_source = _holdings_source_label(broker_src, settings.trading_mode, account_src)
    sell_section = _build_sell_section(prod_engine)
    topics_section = _build_topics_section(prod_engine)
    pending_decisions = _build_pending_decisions(prod_engine)
    dummy_system = _build_dummy_system(prod_engine)
    misato = _build_misato_section(prod_engine)
    # v2.7 INVESTIGELION: WILLE 組織内 RITSUKO + MISATO の作戦指示
    wille_section = _build_wille_section(prod_engine)

    # === X-2C exposure_coach: 今日のポスチャー ===
    # 実 breadth/uptrend データは未配線（X-2C 完成で接続）。LOW confidence → REDUCE_ONLY フォールバック。
    # v2.2 TASK-EX3: portfolio_snapshots の過去 60 日ピーク値から実 DD% を算出する。
    # 旧版は cash==total なら DD=0 という hack で常にゼロ → DD ハードゲートが発火しなかった。
    portfolio_dd_pct = _compute_portfolio_dd(prod_engine, current_total=float(total))
    exposure_decision = decide_exposure(
        ExposureInputs(
            # 環境スコアは未配線（X-2C 完成時に market-breadth-analyzer 等から流す）
            portfolio_dd_pct=portfolio_dd_pct,
        )
    )

    # === X-2B holding_health: 保有銘柄の T1-T5 ===
    # 現状の holdings に補助データ（dividend / perf / filings_text）は未配線。
    # T4 のみ topics 経由で本来配線可能（次セッション以降）。
    health_inputs = [
        {"ticker": ticker, **(h.get("health_inputs") or {})}
        for ticker, h in holdings.items()
    ]
    health_report = check_all_holdings(
        health_inputs, data_asof=utcnow().strftime("%Y-%m-%d %H:%M")
    )
    # holdings に health バッジを足す（UI 描画用）
    for finding in health_report.findings:
        if finding.ticker in holdings:
            holdings[finding.ticker]["health"] = {
                "state": finding.state,
                "triggers_fired": finding.triggers_fired,
                "evidence": [
                    {
                        "trigger_id": e.trigger_id,
                        "state": e.state,
                        "reason": e.reason,
                    }
                    for e in finding.evidence
                ],
            }

    # === X-2A theses_summary: 投資テーゼのライフサイクル統計 ===
    # メイン DB（~/.trading-agent/db.sqlite）から theses 表を読む。
    theses_summary = _build_theses_summary()

    # === D-26 資金配分（Core / Satellite / Cash）===
    # codex #3: posture を exposure_decision.recommendation に配線（固定 REDUCE_ONLY を解消）。
    # exposure が未配線入力で LOW confidence の間は recommendation も保守側になるが、由来は明示する。
    _exp_rec = getattr(exposure_decision, "recommendation", None)
    allocation = _build_allocation(
        positions=positions,
        cash_jpy=float(cash),
        total_jpy=float(total) if total else 100000.0,
        usdjpy=usdjpy,
        posture=_exp_rec or "REDUCE_ONLY",
        posture_source=("exposure_decision" if _exp_rec else "fixed_safe_default"),
    )

    # === ZEELE 攻めレコメンド枠（D-24/D-25・X-2 ZEELE 車線） ===
    # zeele_curator が ZeeleState テーブルに永続化した「3週連続入賞」銘柄を読み出し、
    # 価格履歴は yfinance（live）から取得。DB が空なら候補なし＝Cash 優先メッセージ。
    zeele = _build_zeele_section(
        engine=prod_engine,
        mode_is_live=live,
        account_total_jpy=float(total) if total else 100000.0,  # D-23 既定¥100k
        usdjpy=usdjpy,
        available_cash_jpy=float(cash) if cash else float(total) if total else 100000.0,
    )

    # 増額ゲート⑥（paper/live 別 + combined 参考）と 段階大規模化（paper 専用）
    gates = _build_gates_section(prod_engine)
    scaling = _build_scaling_section(prod_engine)

    # Phase C 現況（レポートビュー用・常に最新・コスト0・DBのみ・価格 fetch なし）。
    # _suppressed で構造化ログを stderr へ逃がし純度を保ち、_json_safe で None キーを正規化（codex P0/P1）。
    try:
        import phase_c_status as _pcs

        with _pcs._suppressed():
            phase_c = _pcs._json_safe(_pcs.build_phase_c_status(prod_engine))
    except Exception as exc:  # noqa: BLE001
        phase_c = {"error": f"{type(exc).__name__}: {exc}"}

    # v2.10: dashboard が provider 別バッジを表示できるよう broker_provider を出力
    from trading_agent.utils.lot_size import get_broker_provider as _gbp_for_snap

    snap_out: dict = {
        "generated_at": utcnow().strftime("%Y-%m-%d %H:%M"),
        "mode": "live" if live else "demo",
        "trading_mode": settings.trading_mode,
        "broker_provider": _gbp_for_snap(),
        "gates": gates,
        "scaling": scaling,
        "phase_c": phase_c,
        "broker": broker_src,
        "account_source": account_src,
        "holdings_source": holdings_source,
        "sell": sell_section,
        "topics": topics_section,
        "pending_decisions": pending_decisions,
        # v2.5 TASK-P11: raw=None なら null（UI で「取得失敗」表示用）
        "usdjpy": round(usdjpy_raw, 2) if usdjpy_raw is not None else None,
        # D-24 北極星 / D-25 市場対象（脳裏チップ表示用）
        "north_star": {
            "name": "claude-trading-skills",
            "url": "https://github.com/tradermonty/claude-trading-skills",
            "mantra": "Plan → Trade → Record → Review → Improve",
        },
        "market_focus": {
            "primary": "JP",
            "primary_weight_pct": 90,
            "satellite": "US ETF (QQQ/VOO)",
            "satellite_weight_pct": 10,
            "decision_id": "D-25",
        },
        # X-2C exposure_coach 出力（今日のポスチャー）
        "exposure": {
            "recommendation": exposure_decision.recommendation,
            "bias": exposure_decision.bias,
            "participation": exposure_decision.participation,
            "confidence": exposure_decision.confidence,
            "ceiling_pct": exposure_decision.ceiling_pct,
            "rationale": exposure_decision.rationale,
            "inputs_provided": exposure_decision.inputs_provided,
            "inputs_missing": exposure_decision.inputs_missing,
        },
        # X-2B holding_health サマリ
        "holding_health_summary": dict(health_report.summary),
        # X-2A theses_summary
        "theses_summary": theses_summary,
        # v2.8: broker_mode で分岐（_build_account_section_v28 で実装）
        #   - paper:        KATSURAGI 預かり金（DS 4 機の総計） — Paper 検証用
        #   - moomoo_live:  moomoo 口座の実残高（本番）
        "account": (lambda: {
            "cash": round(sum(p["cash_jpy"] for p in dummy_system["personalities"])) if dummy_system["personalities"] else round(cash),
            "total_assets": round(sum(p["total_value_jpy"] for p in dummy_system["personalities"])) if dummy_system["personalities"] else round(total),
            "cash_ratio": round(
                (sum(p["cash_jpy"] for p in dummy_system["personalities"]) /
                 max(sum(p["total_value_jpy"] for p in dummy_system["personalities"]), 1)) * 100
            ) if dummy_system["personalities"] else (round(cash / total * 100) if total else 0),
            "currency": account.currency if account else "JPY",
            "positions": sum(p["holdings_count"] for p in dummy_system["personalities"]),
            "source": "paper_treasury",  # KATSURAGI 預かり金（DS 4 機合計）
        })(),
        "holdings": holdings,
        "candidates": candidates,
        "candidates_meta": candidates_meta,  # codex P0: stale 等は candidates と分離（件数誤りを防ぐ）
        "zeele": zeele,
        "allocation": allocation,
        "dummy_system": dummy_system,
        "misato": misato,
        # v2.7 INVESTIGELION: WILLE 組織内（RITSUKO 分析 + MISATO 作戦指示）
        "wille": wille_section,
    }

    # v2.8: moomoo Live モード時、account を moomoo 実残高で上書き（本番）
    try:
        from trading_agent.utils.lot_size import is_moomoo_live

        if is_moomoo_live():
            try:
                from trading_agent.brokers.moomoo import MoomooBroker

                moomoo_broker = MoomooBroker.from_settings(settings)
                moomoo_acct = moomoo_broker.get_account()
                if moomoo_acct is not None:
                    snap_out["account"] = {
                        "cash": round(moomoo_acct.cash),
                        "total_assets": round(moomoo_acct.total_assets),
                        "cash_ratio": round(
                            moomoo_acct.cash / max(moomoo_acct.total_assets, 1) * 100
                        ),
                        "currency": moomoo_acct.currency or "JPY",
                        "positions": sum(p["holdings_count"] for p in dummy_system["personalities"]),
                        "source": "moomoo_live",  # ⚡ 本番 moomoo 実口座残高
                    }
                else:
                    snap_out["account"] = {
                        "cash": 0,
                        "total_assets": 0,
                        "cash_ratio": 0,
                        "currency": "JPY",
                        "positions": 0,
                        "source": "moomoo_live_unavailable",
                        "error": "moomoo 口座読み取り失敗",
                    }
            except Exception as exc:
                snap_out["account"] = {
                    "cash": 0,
                    "total_assets": 0,
                    "cash_ratio": 0,
                    "currency": "JPY",
                    "positions": 0,
                    "source": "moomoo_live_error",
                    "error": str(exc),
                }
    except Exception:
        pass

    # 注: Phase C 現況（snap_out["phase_c"]）は上の dict リテラルで 1 回だけ生成済み
    # （_suppressed + _json_safe）。常に UI 最新反映は build_snapshot 再生成（朝バッチ+5分自動更新+
    # 手動更新）で担保。二重生成しない（codex P1）。

    return snap_out


# D-26 資金配分の既定値
_ALLOC_TARGET_PCT = {"core": 60, "satellite": 20, "cash": 20}
_MONTHLY_ADD_DEFAULT_JPY = 30000  # ユーザー設定で 30,000-50,000 を想定
# 月次追加配分（exposure posture で上書き）
_MONTHLY_ADD_RULES: dict[str, dict[str, int]] = {
    "NEW_ENTRY_ALLOWED": {"core": 70, "satellite": 10, "cash": 20},
    "REDUCE_ONLY":       {"core": 70, "satellite": 0,  "cash": 30},
    "CASH_PRIORITY":     {"core": 30, "satellite": 0,  "cash": 70},
}

# Core 扱いするティッカー（ETF + 高配当 JP）。実保有判定用。
_CORE_TICKERS = {
    # US ETF (D-25)
    "QQQ", "VOO", "SOXX", "SPY", "DIA", "VTI", "VYM", "SCHD",
    # JP 高配当主力（暫定）
    "9433", "9432", "8306", "8316", "8411", "8001", "8058",
    "9020", "4502", "4503", "4452",
}


def _classify_position(ticker: str) -> str:
    """ポジションを core / satellite に分類。"""
    if ticker in _CORE_TICKERS:
        return "core"
    return "satellite"


def _build_allocation(
    *,
    positions: list,
    cash_jpy: float,
    total_jpy: float,
    usdjpy: float,
    posture: str = "REDUCE_ONLY",
    posture_source: str = "fixed_safe_default",
) -> dict[str, object]:
    """D-26 資金配分の計算（current / target / 月次追加配分提案）。

    posture は __init__.py の build() 内で計算済の exposure_decision を参照
    したいが、現状は循環参照を避けて allocation 内で簡易再計算する。
    （次セッションで build() の引数として渡す形に整理予定）
    """
    # 現状の集計
    core_jpy = 0.0
    satellite_jpy = 0.0
    if positions:
        for p in positions:
            qty = getattr(p, "qty", 0) or 0
            price = getattr(p, "nominal_price", None) or 0
            currency = getattr(p, "currency", "JPY")
            value = qty * price
            if currency == "USD":
                value *= usdjpy
            if _classify_position(getattr(p, "code", "")) == "core":
                core_jpy += value
            else:
                satellite_jpy += value
    current = {
        "core": round(core_jpy),
        "satellite": round(satellite_jpy),
        "cash": round(cash_jpy),
    }

    target_jpy = {k: round(total_jpy * v / 100) for k, v in _ALLOC_TARGET_PCT.items()}
    gap_jpy = {k: target_jpy[k] - current[k] for k in target_jpy}

    # 月次追加配分: posture は exposure_decision.recommendation を配線（codex #3）。
    # 未配線（呼び出し側が渡さない）時のみ安全側 "REDUCE_ONLY"。posture_source で由来を明示。
    rule = _MONTHLY_ADD_RULES.get(posture, _MONTHLY_ADD_RULES["REDUCE_ONLY"])
    monthly_default = _MONTHLY_ADD_DEFAULT_JPY
    split = {
        "core_jpy":      round(monthly_default * rule["core"] / 100),
        "satellite_jpy": round(monthly_default * rule["satellite"] / 100),
        "cash_jpy":      round(monthly_default * rule["cash"] / 100),
    }

    # 配分ガード違反チェック（情報表示用）
    warnings: list[str] = []
    if total_jpy > 0:
        core_pct = current["core"] / total_jpy * 100
        sat_pct = current["satellite"] / total_jpy * 100
        cash_pct = current["cash"] / total_jpy * 100
        if cash_pct < 20:
            warnings.append("Cash が下限 20% を割っている（D-23 #4）")
        if sat_pct > 30:
            warnings.append("Satellite が 30% を超えている（D-23 セクター上限相当）")

    return {
        "current": current,
        "target_pct": dict(_ALLOC_TARGET_PCT),
        "target_jpy": target_jpy,
        "gap_jpy": gap_jpy,
        "monthly_addition_default_jpy": monthly_default,
        "monthly_addition_split": split,
        "posture_used": posture,
        "posture_source": posture_source,  # exposure_decision 由来か fixed_safe_default か（codex #3）
        "rule_pct": rule,
        "warnings": warnings,
        "note": (
            "月次追加は posture により配分上書き：NEW_ENTRY_ALLOWED→70/10/20 / "
            "REDUCE_ONLY→70/0/30 / CASH_PRIORITY→30/0/70（D-26）"
        ),
    }


def _suggest_size(
    *,
    account_total_jpy: float,
    available_cash_jpy: float,
    entry_jpy: float,
    stop_pct: float,                   # 銘柄毎に変動（ボラ・プリセット由来）
    risk_pct: float = 0.02,            # D-23 #1
    max_pos_pct: float = 0.20,         # D-23 1銘柄上限
    cash_floor_pct: float = 0.20,      # D-23 #4 現金下限
) -> dict[str, object]:
    """D-23 8数値準拠の推奨サイジング（ZEELE 候補・参考値）。

    3つの制約の最小値を取る：
      - risk : 1Rのリスクを stop_pct で吸収する shares 上限
      - cap  : 1銘柄ポジション率 max_pos_pct の上限
      - cash : 現金下限 cash_floor_pct を確保した上で **今買える** 上限
    fractional shares 許容（moomoo 1株単元未満手数料0・D-23 ②）。
    """
    if entry_jpy <= 0 or account_total_jpy <= 0:
        return {
            "suggested_jpy": 0,
            "suggested_shares": 0.0,
            "constraint": "n/a",
            "stop_pct_used": stop_pct,
            "investable_cash_jpy": 0,
        }
    risk_jpy = account_total_jpy * risk_pct
    cash_floor_jpy = account_total_jpy * cash_floor_pct
    investable_cash = max(0.0, available_cash_jpy - cash_floor_jpy)

    shares_by_risk = risk_jpy / (entry_jpy * stop_pct) if stop_pct > 0 else 0
    shares_by_cap = (account_total_jpy * max_pos_pct) / entry_jpy
    shares_by_cash = investable_cash / entry_jpy

    shares = min(shares_by_risk, shares_by_cap, shares_by_cash)
    # 制約特定（同点は risk > cap > cash の優先順）
    if shares <= 0:
        constraint = "cash"  # 投入余地ゼロ
    elif shares == shares_by_risk:
        constraint = "risk"
    elif shares == shares_by_cap:
        constraint = "cap"
    else:
        constraint = "cash"

    return {
        "suggested_jpy": round(shares * entry_jpy),
        "suggested_shares": round(shares, 2),
        "constraint": constraint,
        "stop_pct_used": round(stop_pct, 3),
        "investable_cash_jpy": round(investable_cash),
    }


# プリセット → 既定 stop_pct（ボラ感の差を表現・D-23 10-15%の範囲を中心に）
_STOP_PCT_BY_PRESET: dict[str, float] = {
    "momentum": 0.15,      # 高ボラ：広めの stop で許容
    "growth": 0.13,
    "alpha": 0.13,
    "pullback": 0.11,
    "value": 0.09,         # 低ボラ：狭めの stop で許容
    "contrarian": 0.10,
    "growth-value": 0.10,
    "dividend": 0.08,
}


def _stop_pct_for_candidate(
    history: list[float] | None, preset: str
) -> tuple[float, str]:
    """銘柄毎の stop_pct と算出根拠を返す。

    手法：preset 既定値と **実現ボラ×4σ** の max（広い方）を採用。
    実現ボラ = 12週終値から計算した weekly returns の標準偏差。
    D-23 8-20% にクランプ。
    """
    preset_base = _STOP_PCT_BY_PRESET.get(preset)

    vol_based: float | None = None
    if history and len(history) >= 3:
        returns: list[float] = []
        for i in range(1, len(history)):
            prev = history[i - 1]
            if prev > 0:
                returns.append(history[i] / prev - 1)
        if len(returns) >= 2:
            mean_r = sum(returns) / len(returns)
            var_r = sum((r - mean_r) ** 2 for r in returns) / len(returns)
            std_r = var_r ** 0.5
            vol_based = max(0.08, min(0.20, std_r * 4))

    if preset_base is not None and vol_based is not None:
        if vol_based > preset_base:
            return round(vol_based, 3), "vol"
        return preset_base, "preset"
    if preset_base is not None:
        return preset_base, "preset"
    if vol_based is not None:
        return round(vol_based, 3), "vol"
    return 0.12, "default"


def _fetch_zeele_price_history(ticker: str) -> tuple[list[float], str | None]:
    """ZEELE 候補の 12週分の終値と直近終値日付を yfinance から取得する。

    Sparkline 描画と stop_pct 算出（実現ボラ）に使う。週次 12 本（period="3mo", interval="1wk"）。
    失敗時は空リストと None を返す（呼び出し側でグレースフル降格）。
    """
    try:
        import yfinance as yf

        sym = f"{ticker}.T" if ticker.isdigit() else ticker
        df = yf.Ticker(sym).history(period="3mo", interval="1wk", auto_adjust=False)
        if df is None or df.empty or "Close" not in df.columns:
            return [], None
        closes = [float(v) for v in df["Close"].dropna().tolist()[-12:]]
        if not closes:
            return [], None
        last_date = df.index[-1].strftime("%Y-%m-%d") if len(df.index) > 0 else None
        return closes, last_date
    except Exception as exc:
        _log.warning("zeele_price_history_failed", ticker=ticker, error=str(exc))
        return [], None


def _fetch_zeele_last_price(ticker: str) -> float | None:
    """直近終値（fast_info.last_price）。週次履歴の最終値より精度が高い。失敗時 None。"""
    try:
        import yfinance as yf

        sym = f"{ticker}.T" if ticker.isdigit() else ticker
        info = yf.Ticker(sym).fast_info
        return float(info.last_price)
    except Exception as exc:
        _log.warning("zeele_last_price_failed", ticker=ticker, error=str(exc))
        return None


def _build_zeele_section(
    *,
    engine: Engine,
    mode_is_live: bool,
    account_total_jpy: float = 100000.0,
    usdjpy: float = 150.0,
    available_cash_jpy: float | None = None,
) -> dict[str, object]:
    """ZEELE 攻めレコメンド枠：zeele_curator が永続化した銘柄を読み出し、価格を肉付けする。

    フロー：
      1. ZeeleState (is_active=True) を DB から取得
      2. 各銘柄について yfinance で 12週終値・直近終値を取得（live モードのみ）
      3. preset / 履歴から stop_pct を算出し、D-23 サイジングを付与
      4. UI 描画用の dict にまとめて返す

    候補ゼロ時は candidates=[] を返す（UI 側で「該当なし・Cash 優先」を表示）。
    """
    # 現金可用額（呼出側で計算済なら使う・無ければ口座総額を流用）
    cash_available = (
        available_cash_jpy if available_cash_jpy is not None else account_total_jpy
    )

    # ZEELE は「量より質：3-5銘柄で十分」（ZeelePanel.tsx の設計コメント）。
    # qualifier が多数になっても UI には reference_score 上位だけを出す。
    _ZEELE_DISPLAY_TOP_N = 5

    candidates: list[dict[str, object]] = []
    with Session(engine) as session:
        active_states = list(
            session.exec(
                select(ZeeleState)
                .where(col(ZeeleState.is_active))
                .order_by(col(ZeeleState.reference_score).desc())
                .order_by(col(ZeeleState.weeks_in_zeele).desc())
                .limit(_ZEELE_DISPLAY_TOP_N)
            )
        )
        name_map = {
            u.ticker: u.name
            for u in session.exec(
                select(Universe).where(col(Universe.ticker).in_([s.ticker for s in active_states]))
            )
        } if active_states else {}

    for state in active_states:
        # 価格履歴（live モードでのみ yfinance）。demo モードでは履歴なしで進める。
        history: list[float] = []
        last_date: str | None = None
        last_price: float | None = None
        if mode_is_live:
            history, last_date = _fetch_zeele_price_history(state.ticker)
            last_price = _fetch_zeele_last_price(state.ticker)

        # last_price が取れなければ履歴末尾を fallback
        if last_price is None and history:
            last_price = history[-1]
        if last_price is None:
            # 取得不能なら候補から除外（モック価格は出さない）
            _log.warning("zeele_candidate_skipped_no_price", ticker=state.ticker)
            continue

        is_jp = state.ticker.isdigit()
        entry_jpy = last_price if is_jp else last_price * usdjpy

        stop_pct, stop_source = _stop_pct_for_candidate(history, state.preset)
        sizing = _suggest_size(
            account_total_jpy=account_total_jpy,
            available_cash_jpy=cash_available,
            entry_jpy=entry_jpy,
            stop_pct=stop_pct,
        )

        period_return_pct: float | None = None
        if len(history) >= 2 and history[0] > 0:
            period_return_pct = round((history[-1] / history[0] - 1) * 100, 1)

        candidates.append(
            {
                "ticker": state.ticker,
                "name": name_map.get(state.ticker, ""),
                "preset": state.preset,
                "structural_thesis": state.structural_thesis,
                "reference_score": round(state.reference_score, 1),
                "zeele_entered_at": state.entered_at.isoformat(),
                "zeele_weeks": state.weeks_in_zeele,
                "period_return_pct": period_return_pct,
                "price_history_12w": history,
                "last_price": round(last_price, 2),
                "last_price_jpy": round(entry_jpy),
                "last_price_asof": last_date,  # UI で「終値（日付）」を出すための明示
                "suggested_jpy": sizing["suggested_jpy"],
                "suggested_shares": sizing["suggested_shares"],
                "sizing_constraint": sizing["constraint"],
                "stop_pct_used": sizing["stop_pct_used"],
                "stop_pct_source": stop_source,
                "promoted": False,
            }
        )

    # 候補が無い時は UI 側で「該当なし（プール乾燥中・Cash優先）」を出す
    note = (
        "ZEELE プールが乾燥中（3週連続入賞銘柄なし）。Cash 優先。"
        if not candidates
        else f"ZEELE プールに {len(candidates)} 銘柄が在籍中。"
    )

    return {
        "candidates": candidates,
        # narrative テーマは structural_thesis に集約。section レベルは現状未使用
        "narrative_themes": [],
        "x_trends": [],
        "note": note,
        "generated_at": utcnow().strftime("%Y-%m-%d %H:%M"),
        "account_total_jpy": round(account_total_jpy),
        "available_cash_jpy": round(cash_available),
        "cash_floor_jpy": round(account_total_jpy * 0.20),
        "investable_cash_jpy": round(max(0, cash_available - account_total_jpy * 0.20)),
    }


def _build_theses_summary() -> dict[str, object]:
    """投資テーゼのライフサイクル統計を取得（X-2A）。"""
    try:
        from trading_agent.config import load_settings

        settings = load_settings()
        settings.ensure_directories()
        engine = get_engine(settings.db_path)
        create_all(engine)
        counts: dict[str, int] = {s.value: 0 for s in ThesisStatus}
        with Session(engine) as s:
            for status in ThesisStatus:
                n = len(list(s.exec(select(Thesis).where(col(Thesis.status) == status)).all()))
                counts[status.value] = n
        return {
            "counts": counts,
            "total": sum(counts.values()),
            "active": counts.get("ACTIVE", 0),
        }
    except Exception as exc:  # noqa: BLE001
        # 設定欠落・DB未作成等は静かに空サマリを返す（UI は "—" 表示）
        _log.warning("theses_summary_failed", error=str(exc))
        return {"counts": {}, "total": 0, "active": 0}


async def main() -> None:
    live = "--demo" not in sys.argv
    prefer_moomoo = "--moomoo" in sys.argv
    light = "--light" in sys.argv
    snapshot = await build(live=live, prefer_moomoo=prefer_moomoo, light=light)
    out_path = Path(__file__).resolve().parent.parent / "ui" / "public" / "data" / "snapshot.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}  (mode={snapshot['mode']}, broker={snapshot['broker']})")
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
