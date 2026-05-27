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
    return {
        t: _quote(_DEMO_PRICE[t][0] + 0.2, _DEMO_PRICE[t][1])
        for t in tickers
        if t in _DEMO_PRICE
    }


def _fmt_price(ticker: str, price: float) -> str:
    return f"¥ {price:,.0f}" if _is_jp(ticker) else f"$ {price:,.2f}"


def _holdings_source_label(broker_src: str, trading_mode: str, account_src: str) -> str:
    # broker_src は保有取得（保有 0 件だと "standin" にフォールバックされる仕様）。
    # 口座側（account_src）が moomoo に繋がっていれば「保有 0 件」のメッセージで明示する。
    if broker_src == "moomoo":
        return "moomoo 実弾" if trading_mode == "live" else "moomoo JP REAL（紙運用：仮想入金 overlay）"
    if account_src in ("moomoo", "moomoo+overlay"):
        suffix = "実弾" if trading_mode == "live" else "紙運用 overlay"
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

    out: dict[str, dict[str, object]] = {}
    for d in sorted_rows:
        if d.id is None:
            continue
        card_id = f"d{d.id}"
        cmd = commanders.get(d.id)
        meta = universe_meta.get(d.ticker, {"name": "", "market": "JP", "sector": ""})
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
    """2期財務→信用性(S5)。MELCHIOR反証(S6)を付与し credibility_flag を返す（失敗はok）。"""
    try:
        fin = fetch_financials(ticker, market_cap=_market_cap(ticker))
    except Exception as exc:
        _log.warning("credibility_failed", ticker=ticker, error=str(exc))
        return "ok"
    if fin is None:
        return "ok"
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


async def build(*, live: bool, prefer_moomoo: bool) -> dict[str, object]:
    eng = get_engine(Path(tempfile.gettempdir()) / "snapshot.sqlite")
    create_all(eng)

    # 設定と本番 DB を読み込む（口座状態の Portfolio.active コスト算出に必要）。
    # 実運用スクリプト群（load_universe / run_morning_batch / run_paper / run_evaluation）が
    # `data/trading.sqlite` を使っているので、それに合わせる。
    from trading_agent.config import load_settings

    settings = load_settings()
    prod_engine = get_engine(Path("data") / "trading.sqlite")
    create_all(prod_engine)

    positions, broker_src = load_positions(prefer_moomoo=prefer_moomoo, settings=settings)
    account, account_src = load_account(prod_engine, settings)
    total = account.total_assets
    cash = account.cash
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
                pnl = {
                    "ratio_display": f"{p.pl_ratio:+.1f}%",
                    "direction": "up" if p.pl_ratio >= 0 else "down",
                }
            elif price is not None and p.cost_price:
                r = (price - p.cost_price) / p.cost_price * 100.0
                pnl = {"ratio_display": f"{r:+.1f}%", "direction": "up" if r >= 0 else "down"}
            holdings[p.code] = {
                "price_display": _fmt_price(p.code, price) if price is not None else None,
                "reconciliation": out.reconciliation.get(p.code, "single"),
                "as_of": out.data_asof.strftime("%Y-%m-%d %H:%M") if out.data_asof else None,
                "pnl": pnl,
            }

    llm_tool = _maybe_llm_tool(eng, live=live)
    candidates = await _build_candidates(live, total, cash, usdjpy, llm_tool=llm_tool)

    holdings_source = _holdings_source_label(broker_src, settings.trading_mode, account_src)
    sell_section = _build_sell_section(prod_engine)
    topics_section = _build_topics_section(prod_engine)
    pending_decisions = _build_pending_decisions(prod_engine)

    # === X-2C exposure_coach: 今日のポスチャー ===
    # 実 breadth/uptrend データは未配線（X-2C 完成で接続）。LOW confidence → REDUCE_ONLY フォールバック。
    # D-23 DD-15% gate のため PF DD を渡す（cash=total なら DD=0）。
    portfolio_dd_pct = 0.0 if total > 0 and cash == total else None
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
    allocation = _build_allocation(
        positions=positions,
        cash_jpy=float(cash),
        total_jpy=float(total) if total else 100000.0,
        usdjpy=usdjpy,
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

    return {
        "generated_at": utcnow().strftime("%Y-%m-%d %H:%M"),
        "mode": "live" if live else "demo",
        "trading_mode": settings.trading_mode,
        "broker": broker_src,
        "account_source": account_src,
        "holdings_source": holdings_source,
        "sell": sell_section,
        "topics": topics_section,
        "pending_decisions": pending_decisions,
        "usdjpy": round(usdjpy, 2),
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
        "account": {
            "cash": round(cash),
            "total_assets": round(total),
            "cash_ratio": round(cash / total * 100) if total else 0,
            "currency": account.currency if account else "JPY",
            "positions": len(positions),
        },
        "holdings": holdings,
        "candidates": candidates,
        "zeele": zeele,
        "allocation": allocation,
    }


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

    # 月次追加配分（posture 簡易：cash=total なら NEW_ENTRY_ALLOWED と仮定。
    # 実際は exposure_coach の結果を参照。今は安全側で "REDUCE_ONLY" 既定とする）
    posture = "REDUCE_ONLY"  # 暫定：実 exposure_decision と整合させるのは次セッションで
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
    snapshot = await build(live=live, prefer_moomoo=prefer_moomoo)
    out_path = Path(__file__).resolve().parent.parent / "ui" / "public" / "data" / "snapshot.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}  (mode={snapshot['mode']}, broker={snapshot['broker']})")
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
