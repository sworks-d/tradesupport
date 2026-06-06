"""(b) 構造化イベントタグ（derive_structured_event_tags）の単体テスト。

J-Quants 予想/配当の開示間比較から上方/下方修正・増配/減配を record-only タグ化する。
fixture 注入のみ（実 J-Quants API を叩かない=¥0）。PIT/split ガード/欠損無タグ(H10)を検証。
master/codex 承認スコープ: forecast revision + dividend change のみ（buyback/dilution は defer）。
"""

from __future__ import annotations

import datetime as dt

from trading_agent.screening.financials import (
    STRUCTURED_EVENTS_PARSER_VERSION,
    Financials,
    ForecastPoint,
    PeriodFinancials,
    _build_forecast_history,
)
from trading_agent.screening.turnaround import (
    derive_structured_event_tags,
    evaluate_structured_events,
)


def _fin(history: list[ForecastPoint]) -> Financials:
    return Financials(
        ticker="3697",
        current=PeriodFinancials(period="2026"),
        prior=None,
        market_cap=1e9,
        source="jquants",
        forecast_history=history,
    )


def _fp(d: str, fy: str, *, fnp=None, fdiv=None, rdiv=None, sh=None) -> ForecastPoint:
    return ForecastPoint(
        disc_date=dt.date.fromisoformat(d),
        cur_fy_end=fy,
        doc_type="決算短信",
        forecast_profit=fnp,
        forecast_op=None,
        forecast_div_annual=fdiv,
        result_div_annual=rdiv,
        shares_outstanding=sh,
    )


def test_upward_revision() -> None:
    h = [_fp("2025-08-01", "2026-03-31", fnp=100), _fp("2025-11-01", "2026-03-31", fnp=130)]
    tags, ev = derive_structured_event_tags(_fin(h))
    assert tags == ["event_upward_revision"]
    e = ev["event_upward_revision"]
    assert e["before"] == 100 and e["after"] == 130
    assert e["field"] == "FNP"
    assert e["disc_date_after"] == "2025-11-01"
    assert e["parser_version"] == STRUCTURED_EVENTS_PARSER_VERSION


def test_downward_revision() -> None:
    h = [_fp("2025-08-01", "2026-03-31", fnp=100), _fp("2025-11-01", "2026-03-31", fnp=70)]
    assert derive_structured_event_tags(_fin(h))[0] == ["event_downward_revision"]


def test_different_fy_not_compared() -> None:
    """異なる CurFYEn の予想は比較しない（来期予想と当期予想を混ぜない・PIT/semantics）。"""
    h = [_fp("2024-11-01", "2025-03-31", fnp=100), _fp("2025-11-01", "2026-03-31", fnp=130)]
    assert derive_structured_event_tags(_fin(h))[0] == []


def test_no_change_no_tag() -> None:
    h = [_fp("2025-08-01", "2026-03-31", fnp=100), _fp("2025-11-01", "2026-03-31", fnp=100)]
    assert derive_structured_event_tags(_fin(h))[0] == []


def test_dividend_hike() -> None:
    """前 FY 実績年配 20 → 当 FY 予想年配 30、株数安定 = 増配。"""
    h = [
        _fp("2024-11-01", "2025-03-31", rdiv=20, sh=1000),
        _fp("2025-11-01", "2026-03-31", fdiv=30, sh=1000),
    ]
    tags, ev = derive_structured_event_tags(_fin(h))
    assert tags == ["event_dividend_hike"]
    assert ev["event_dividend_hike"]["before"] == 20 and ev["event_dividend_hike"]["after"] == 30


def test_dividend_cut() -> None:
    h = [
        _fp("2024-11-01", "2025-03-31", rdiv=20, sh=1000),
        _fp("2025-11-01", "2026-03-31", fdiv=15, sh=1000),
    ]
    assert derive_structured_event_tags(_fin(h))[0] == ["event_dividend_cut"]


def test_split_guard_suppresses_dividend_tag() -> None:
    """配当 per-share 20→11 でも株数 2:1 split なら split 疑いで抑止（false-data 回避）。"""
    h = [
        _fp("2024-11-01", "2025-03-31", rdiv=20, sh=1000),
        _fp("2025-11-01", "2026-03-31", fdiv=11, sh=2000),
    ]
    assert derive_structured_event_tags(_fin(h))[0] == []


def test_dividend_no_tag_when_shares_missing() -> None:
    """codex P1: 株数(ShOutFY)欠損は split/併合を否定できない → 配当タグを出さない（H10）。"""
    # FDivAnn 30 / prior DivAnn 20 だが片方/両方の株数が欠損 → 無タグ
    h_both_missing = [
        _fp("2024-11-01", "2025-03-31", rdiv=20, sh=None),
        _fp("2025-11-01", "2026-03-31", fdiv=30, sh=None),
    ]
    assert derive_structured_event_tags(_fin(h_both_missing))[0] == []

    h_after_missing = [
        _fp("2024-11-01", "2025-03-31", rdiv=20, sh=1000),
        _fp("2025-11-01", "2026-03-31", fdiv=30, sh=None),
    ]
    assert derive_structured_event_tags(_fin(h_after_missing))[0] == []

    h_before_missing = [
        _fp("2024-11-01", "2025-03-31", rdiv=20, sh=None),
        _fp("2025-11-01", "2026-03-31", fdiv=30, sh=1000),
    ]
    assert derive_structured_event_tags(_fin(h_before_missing))[0] == []


def test_missing_data_no_tag() -> None:
    assert derive_structured_event_tags(_fin([]))[0] == []
    assert derive_structured_event_tags(None)[0] == []


def test_forecast_and_dividend_coexist() -> None:
    """同一銘柄で 上方修正 と 増配 が同時に立つ（union）。"""
    h = [
        _fp("2024-11-01", "2025-03-31", fnp=80, rdiv=20, sh=1000),
        _fp("2025-08-01", "2026-03-31", fnp=100, fdiv=30, sh=1000),
        _fp("2025-11-01", "2026-03-31", fnp=130, fdiv=30, sh=1000),
    ]
    tags = derive_structured_event_tags(_fin(h))[0]
    assert "event_upward_revision" in tags
    assert "event_dividend_hike" in tags


def test_build_forecast_history_from_short_columns() -> None:
    """_build_forecast_history が短縮列(FNP/FDivAnn/DivAnn/CurFYEn/DiscDate)から構築する。"""
    stmts = [
        {
            "DiscDate": dt.date(2025, 11, 1),
            "CurFYEn": "2026-03-31",
            "DocType": "決算短信",
            "FNP": "130",
            "FDivAnn": "30",
            "DivAnn": "",
            "ShOutFY": "1000",
        }
    ]
    pts = _build_forecast_history(stmts)
    assert len(pts) == 1
    assert pts[0].forecast_profit == 130.0
    assert pts[0].forecast_div_annual == 30.0
    assert pts[0].result_div_annual is None  # 空文字は None（推測しない）
    assert pts[0].cur_fy_end == "2026-03-31"


def test_build_forecast_history_excludes_no_disc_date() -> None:
    """開示日が取れない開示は除外（PIT 比較の土台が無い・silent look-ahead を出さない）。"""
    stmts = [{"CurFYEn": "2026-03-31", "FNP": "130"}]  # DiscDate 無し
    assert _build_forecast_history(stmts) == []


# === M1 観測 hardening: evaluate_structured_events の no-fire 理由(diag) ===

def test_diag_fin_not_fetched() -> None:
    """fin=None（rate-limit/取得失敗）→ 両 family fin_not_fetched（発火しない理由を観測）。"""
    tags, _, diag = evaluate_structured_events(None)
    assert tags == []
    assert diag == {"forecast": "fin_not_fetched", "dividend": "fin_not_fetched"}


def test_diag_no_forecast_data_when_history_empty() -> None:
    tags, _, diag = evaluate_structured_events(_fin([]))
    assert tags == []
    assert diag == {"forecast": "no_forecast_data", "dividend": "no_forecast_data"}


def test_diag_forecast_reasons() -> None:
    # fired_upward
    _, _, d = evaluate_structured_events(
        _fin([_fp("2025-08-01", "2026-03-31", fnp=100), _fp("2025-11-01", "2026-03-31", fnp=130)])
    )
    assert d["forecast"] == "fired_upward"
    # no_change
    _, _, d = evaluate_structured_events(
        _fin([_fp("2025-08-01", "2026-03-31", fnp=100), _fp("2025-11-01", "2026-03-31", fnp=100)])
    )
    assert d["forecast"] == "no_change"
    # single_fy_point（同一 FY の開示1点のみ）
    _, _, d = evaluate_structured_events(_fin([_fp("2025-11-01", "2026-03-31", fnp=100)]))
    assert d["forecast"] == "single_fy_point"


def test_diag_dividend_reasons() -> None:
    # missing_shares
    _, _, d = evaluate_structured_events(
        _fin([_fp("2024-11-01", "2025-03-31", rdiv=20, sh=None),
              _fp("2025-11-01", "2026-03-31", fdiv=30, sh=None)])
    )
    assert d["dividend"] == "missing_shares"
    # split_suspected
    _, _, d = evaluate_structured_events(
        _fin([_fp("2024-11-01", "2025-03-31", rdiv=20, sh=1000),
              _fp("2025-11-01", "2026-03-31", fdiv=11, sh=2000)])
    )
    assert d["dividend"] == "split_suspected"
    # fired_hike
    _, _, d = evaluate_structured_events(
        _fin([_fp("2024-11-01", "2025-03-31", rdiv=20, sh=1000),
              _fp("2025-11-01", "2026-03-31", fdiv=30, sh=1000)])
    )
    assert d["dividend"] == "fired_hike"


def test_derive_wrapper_matches_evaluate() -> None:
    """derive_structured_event_tags は evaluate の (tags, evidence) と一致（公開契約不変）。"""
    h = [_fp("2025-08-01", "2026-03-31", fnp=100), _fp("2025-11-01", "2026-03-31", fnp=130)]
    tags, ev = derive_structured_event_tags(_fin(h))
    e_tags, e_ev, _ = evaluate_structured_events(_fin(h))
    assert tags == e_tags and ev == e_ev
