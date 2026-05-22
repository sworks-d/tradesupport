"""MAGI 3審判の単体テスト（B2）。

決定論ロジック（実数値→可否）を検証する。LLM不使用＝コスト0・再現可能。
独立性（各審判が自分のソースのみ）と na（判定不能を正直に出す）を確認する。
"""

from __future__ import annotations

from datetime import datetime

from trading_agent.magi import balthasar, casper, melchior, run_judges
from trading_agent.mcp_tools.base import SourceRef
from trading_agent.mcp_tools.fundamentals import FundamentalsOutput
from trading_agent.mcp_tools.news import NewsOutput
from trading_agent.mcp_tools.technicals import TechnicalsOutput

_ASOF = datetime(2026, 5, 22)


def _fund(data: dict) -> FundamentalsOutput:
    return FundamentalsOutput(
        success=True,
        data=data,
        data_asof=_ASOF,
        source_refs=[SourceRef(source="yfinance", ref="NVDA", as_of=_ASOF)],
    )


def _tech(data: dict, signals: list[str]) -> TechnicalsOutput:
    return TechnicalsOutput(
        success=True,
        data=data,
        signals=signals,
        data_asof=_ASOF,
        source_refs=[SourceRef(source="computed", ref="NVDA", as_of=_ASOF)],
    )


def _news(articles: list[dict]) -> NewsOutput:
    return NewsOutput(success=True, articles=articles, data_asof=_ASOF, source_refs=[])


# --- MELCHIOR（業績） -------------------------------------------------------
def test_melchior_buy_on_strong_fundamentals() -> None:
    v = melchior("NVDA", _fund({"revenue_growth": 0.15, "operating_margin": 0.12}))
    assert v.judge == "MELCHIOR"
    assert v.verdict == "buy"
    assert v.confidence == "高"
    assert v.source_refs and v.source_refs[0]["source"] == "yfinance"  # provenance 引継ぎ


def test_melchior_warn_on_negative_growth() -> None:
    v = melchior("NVDA", _fund({"revenue_growth": -0.05, "operating_margin": 0.08}))
    assert v.verdict == "warn"


def test_melchior_na_on_missing_data() -> None:
    v = melchior("NVDA", _fund({}))
    assert v.verdict == "na"
    assert v.confidence == "na"


# --- BALTHASAR（株価） ------------------------------------------------------
def test_balthasar_buy_on_golden_cross() -> None:
    v = balthasar("NVDA", _tech({"rsi": 55.0}, ["golden_cross", "macd_bullish"]))
    assert v.judge == "BALTHASAR"
    assert v.verdict == "buy"


def test_balthasar_warn_on_overbought() -> None:
    v = balthasar("NVDA", _tech({"rsi": 78.0}, ["overbought_rsi"]))
    assert v.verdict == "warn"


def test_balthasar_na_on_missing_data() -> None:
    v = balthasar("NVDA", _tech({}, []))
    assert v.verdict == "na"


# --- CASPER（文脈） ---------------------------------------------------------
def test_casper_warn_on_negative_keywords() -> None:
    v = casper("TSLA", news=_news([{"title": "Tesla、FSD収益化が遅延", "summary": "下方修正"}]))
    assert v.judge == "CASPER"
    assert v.verdict == "warn"
    assert v.confidence == "低"  # 決定論キーワードは弱い（正直に低）


def test_casper_na_on_no_material() -> None:
    v = casper("NVDA", news=_news([]))
    assert v.verdict == "na"


# --- 独立性 / 統合 ----------------------------------------------------------
def test_run_judges_returns_three_independent_verdicts() -> None:
    verdicts = run_judges(
        "NVDA",
        fundamentals=_fund({"revenue_growth": 0.2, "operating_margin": 0.3}),
        technicals=_tech({"rsi": 50.0}, ["golden_cross"]),
        news=_news([{"title": "record revenue", "summary": "beat"}]),
    )
    assert [v.judge for v in verdicts] == ["MELCHIOR", "BALTHASAR", "CASPER"]
    # 各審判が独立に判定（同方向に揃うこともあるが、それは集計で扱う＝統合機構B4）
    assert all(v.verdict in {"buy", "sell", "hold", "warn", "na"} for v in verdicts)
