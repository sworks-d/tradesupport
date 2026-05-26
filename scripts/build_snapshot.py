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

from trading_agent.brokers import StandInBroker, load_positions
from trading_agent.db import create_all, get_engine
from trading_agent.discipline.exposure_coach import ExposureInputs, decide_exposure
from trading_agent.discipline.holding_health import check_all_holdings
from trading_agent.llm.anthropic_client import AnthropicClient
from trading_agent.magi import casper_llm, classify_split, command, run_judges, verify
from trading_agent.magi.gendo import gendo_recommend
from trading_agent.magi.persist import derive_gendo_stance
from trading_agent.models.thesis import Thesis, ThesisStatus
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
    return {
        t: _quote(_DEMO_PRICE[t][0] + 0.2, _DEMO_PRICE[t][1])
        for t in tickers
        if t in _DEMO_PRICE
    }


def _fmt_price(ticker: str, price: float) -> str:
    return f"¥ {price:,.0f}" if _is_jp(ticker) else f"$ {price:,.2f}"


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

    positions, broker_src = load_positions(prefer_moomoo=prefer_moomoo)
    account = StandInBroker().get_account()  # 運用元本¥100,000（moomoo口座連携は後続）
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

    holdings_source = "moomoo ペーパー" if broker_src == "moomoo" else "サンプル/未接続"

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
    # screening pipeline → ZEELE 移植は次セッション以降。
    # 現状はプレースホルダ候補で UI 構造を整える（mode=demo で固定セット）。
    zeele = _build_zeele_section(
        mode_is_live=live,
        account_total_jpy=float(total) if total else 100000.0,  # D-23 既定¥100k
        usdjpy=usdjpy,
    )

    return {
        "generated_at": utcnow().strftime("%Y-%m-%d %H:%M"),
        "mode": "live" if live else "demo",
        "broker": broker_src,
        "holdings_source": holdings_source,
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


def _build_zeele_section(
    *, mode_is_live: bool, account_total_jpy: float = 100000.0, usdjpy: float = 150.0
) -> dict[str, object]:
    """ZEELE 攻めレコメンド枠（暫定プレースホルダ）。

    本配線は次セッション以降（screening_agent / topics_collector → ZEELE への
    ingest アダプタ完成時）。それまでは UI 構造確認用の固定セットを返す。
    候補は universe の TOPIX 中型銘柄から選び、narrative は仮テキスト。
    各候補に **推奨サイジング**（D-23 準拠）を付与する。
    """
    # ZEELE 候補（暫定プレースホルダ）。
    # 「熟成中の攻め候補」framing：1日の bump ではなく数週〜月単位の継続性を示す。
    # price_history_12w は 12 週分の終値（直近右端）。Sparkline 描画用。
    candidates: list[dict[str, object]] = [
        {
            "ticker": "8035",
            "name": "東京エレクトロン",
            "preset": "momentum",
            "narrative": "AI 半導体設備投資の構造的拡大。受注残高 1 年以上の積み上がり。",
            "structural_thesis": "3週連続で momentum/alpha 複数プリセット上位入賞",
            "reference_score": 72,
            "x_sentiment": "ポジティブ",
            "zeele_entered_at": "2026-05-08",
            "zeele_weeks": 3,
            "period_return_pct": 18.2,
            "price_history_12w": [22000, 22300, 21800, 22600, 23100, 23400, 23800, 24500, 25100, 25400, 25800, 26000],
            "promoted": False,
        },
        {
            "ticker": "6857",
            "name": "アドバンテスト",
            "preset": "growth",
            "narrative": "HBM/AI チップテスタ需要・受注残高過去最高更新中。",
            "structural_thesis": "4週連続で growth 上位・HBM テーマ持続",
            "reference_score": 68,
            "x_sentiment": "ポジティブ",
            "zeele_entered_at": "2026-04-26",
            "zeele_weeks": 4,
            "period_return_pct": 24.5,
            "price_history_12w": [5800, 5900, 6100, 6050, 6300, 6500, 6800, 6900, 7100, 7000, 7200, 7220],
            "promoted": False,
        },
        {
            "ticker": "4452",
            "name": "花王",
            "preset": "contrarian",
            "narrative": "中国逆風で割安水準。配当継続性◎・原材料価格反落で利益率回復余地。",
            "structural_thesis": "PBR 1倍割れ改善要請＋配当王の歴史。下値堅い",
            "reference_score": 61,
            "x_sentiment": "中立",
            "zeele_entered_at": "2026-04-12",
            "zeele_weeks": 6,
            "period_return_pct": 4.8,
            "price_history_12w": [5800, 5750, 5700, 5780, 5820, 5870, 5900, 5950, 5980, 6000, 6020, 6080],
            "promoted": False,
        },
        {
            "ticker": "9101",
            "name": "商船三井",
            "preset": "value",
            "narrative": "PER 4倍台・配当利回り 5%超。BS 健全・自社株買い継続。",
            "structural_thesis": "海運 3社の PBR 改善継続。配当方針強化",
            "reference_score": 65,
            "x_sentiment": "中立",
            "zeele_entered_at": "2026-03-29",
            "zeele_weeks": 8,
            "period_return_pct": 12.1,
            "price_history_12w": [4800, 4850, 4900, 4870, 4950, 5000, 5050, 5100, 5180, 5220, 5300, 5380],
            "promoted": False,
        },
    ]
    narrative_themes: list[dict[str, str]] = [
        {
            "title": "AI 設備投資の継続",
            "summary": "NVDA / 東エレ / アドバンテストに追い風。HBM・先端パッケージ向け装置の受注が伸びる",
            "source": "（テーマ仮）",
        },
        {
            "title": "JP 配当株の再評価",
            "summary": "東証 PBR1倍割れ改善要請を受け、配当・自社株買いの強化が継続",
            "source": "（テーマ仮）",
        },
    ]
    x_trends: list[dict[str, str]] = [
        {
            "title": "#半導体",
            "summary": "東エレ・アドバンテスト・SUMCO 言及増加（仮）",
        },
    ]
    # 現金可用額：snapshot を作る側でまだ現金を引数化していないため、暫定で口座総額。
    # ペーパー運用後（実保有のとき）は available_cash を分離して渡す。
    available_cash_jpy = account_total_jpy  # 現状=現金100%（保有0前提）

    # 各候補に推奨サイジングを付与（D-23 準拠・参考値）。
    # stop_pct はプリセット由来で **銘柄ごとに変動**。
    for c in candidates:
        history = c.get("price_history_12w") or []
        if not history:
            continue
        last = float(history[-1])
        ticker = c["ticker"] if isinstance(c["ticker"], str) else ""
        is_jp = ticker.isdigit()
        entry_jpy = last if is_jp else last * usdjpy
        preset = c.get("preset", "")
        preset_key = preset if isinstance(preset, str) else ""

        # 動的 stop：preset 既定 と 実現ボラ×4σ の max（広い方 = 保守的）
        history_f = [float(v) for v in history if v is not None]
        stop_pct, stop_source = _stop_pct_for_candidate(history_f, preset_key)

        sizing = _suggest_size(
            account_total_jpy=account_total_jpy,
            available_cash_jpy=available_cash_jpy,
            entry_jpy=entry_jpy,
            stop_pct=stop_pct,
        )
        c["last_price"] = round(last, 2)
        c["last_price_jpy"] = round(entry_jpy)
        c["suggested_jpy"] = sizing["suggested_jpy"]
        c["suggested_shares"] = sizing["suggested_shares"]
        c["sizing_constraint"] = sizing["constraint"]
        c["stop_pct_used"] = sizing["stop_pct_used"]
        c["stop_pct_source"] = stop_source  # "preset" | "vol" | "default"

    return {
        "candidates": candidates,
        "narrative_themes": narrative_themes,
        "x_trends": x_trends,
        "note": "screening pipeline → ZEELE の ingest 配線は次セッション。現状はプレースホルダ。",
        "generated_at": utcnow().strftime("%Y-%m-%d %H:%M"),
        "account_total_jpy": round(account_total_jpy),
        "available_cash_jpy": round(available_cash_jpy),
        "cash_floor_jpy": round(account_total_jpy * 0.20),
        "investable_cash_jpy": round(max(0, available_cash_jpy - account_total_jpy * 0.20)),
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
