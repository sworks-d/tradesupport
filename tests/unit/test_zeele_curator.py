"""zeele-curator の単体テスト：3週連続入賞 → ZEELE entry / 降格判定。"""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path

from sqlmodel import Session, select

from trading_agent.agents.context import AgentContext
from trading_agent.agents.zeele_curator import (
    ZeeleCuratorAgent,
    ZeeleCuratorInput,
    _derive_signal_tags,
    _is_qualified,
    _weekly_buckets,
)
from trading_agent.db import create_all, get_engine
from trading_agent.mcp_tools.base import MCPHost
from trading_agent.models.signals import ScreeningResult
from trading_agent.models.topics import Topic
from trading_agent.models.universe import Universe
from trading_agent.models.zeele import ZeeleState

AS_OF = dt.date(2026, 5, 27)


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "zeele.sqlite")
    create_all(eng)
    return eng


def _add_universe(engine, tickers: list[tuple[str, str]]) -> None:
    with Session(engine) as session:
        for code, name in tickers:
            session.add(
                Universe(
                    ticker=code,
                    name=name,
                    market="JP",
                    sector="X",
                    market_cap=1.0e10,
                    market_cap_jpy=1.0e10,
                    avg_volume_30d=1.0e6,
                    is_active=True,
                )
            )
        session.commit()


def _screening(
    ticker: str,
    days_ago: int,
    *,
    passed: bool = True,
    v: float = 60.0,
    theme: float = 30.0,
    theme_details: dict | None = None,
    v_shape_details: dict | None = None,
) -> ScreeningResult:
    screened_at = dt.datetime.combine(AS_OF, dt.time(9, 0)) - dt.timedelta(days=days_ago)
    return ScreeningResult(
        ticker=ticker,
        screened_at=screened_at,
        v_shape_score=v,
        theme_score=theme,
        composite_score=max(v, theme),
        screening_passed=passed,
        theme_details=theme_details or {},
        v_shape_details=v_shape_details or {},
    )


def _insert_screening(engine, rows: list[ScreeningResult]) -> None:
    with Session(engine) as session:
        for r in rows:
            session.add(r)
        session.commit()


def _run(engine, *, qualification_weeks: int = 3, dry_run: bool = False, as_of: dt.date = AS_OF):
    ctx = AgentContext(host=MCPHost(), engine=engine, invocation_id="zeele-test")
    agent = ZeeleCuratorAgent(ctx)
    return asyncio.run(
        agent.execute(
            ZeeleCuratorInput(
                invocation_id="zeele-test",
                as_of=as_of,
                qualification_weeks=qualification_weeks,
                dry_run=dry_run,
            )
        )
    )


# === pure-function unit tests ===


def test_weekly_buckets_assigns_correct_week() -> None:
    rows = [
        _screening("AAA", 1),   # bucket 0
        _screening("AAA", 8),   # bucket 1
        _screening("AAA", 15),  # bucket 2
    ]
    buckets = _weekly_buckets(rows, as_of=AS_OF, weeks=3)
    assert buckets["AAA"] == [True, True, True]


def test_weekly_buckets_ignores_failed_rows() -> None:
    rows = [_screening("AAA", 1, passed=False)]
    buckets = _weekly_buckets(rows, as_of=AS_OF, weeks=3)
    assert "AAA" not in buckets


def test_weekly_buckets_ignores_out_of_window() -> None:
    rows = [_screening("AAA", 30)]  # 30日前 = 窓外
    buckets = _weekly_buckets(rows, as_of=AS_OF, weeks=3)
    assert "AAA" not in buckets


def test_is_qualified_requires_all_weeks_present() -> None:
    assert _is_qualified([True, True, True], 3) is True
    assert _is_qualified([True, True, False], 3) is False
    assert _is_qualified([True, False, True], 3) is False
    # 1週でも欠けたら NG（連続性が ZEELE のコア）


# === end-to-end agent tests ===


def test_three_consecutive_weeks_enters_zeele(tmp_path: Path) -> None:
    """3週連続で screening 入賞した銘柄が ZEELE プールに entry する。"""
    engine = _engine(tmp_path)
    _add_universe(engine, [("8035", "東京エレクトロン")])
    _insert_screening(
        engine,
        [
            _screening("8035", 2),   # 週0
            _screening("8035", 9),   # 週1
            _screening("8035", 16),  # 週2
        ],
    )

    out = _run(engine)
    assert "8035" in out.newly_entered
    assert not out.still_active
    assert any(c["ticker"] == "8035" and c["zeele_weeks"] == 3 for c in out.candidates)

    with Session(engine) as s:
        state = s.exec(select(ZeeleState).where(ZeeleState.ticker == "8035")).one()
        assert state.is_active
        assert state.entered_at == AS_OF
        assert state.consecutive_weeks == 3


def test_two_consecutive_weeks_does_not_enter(tmp_path: Path) -> None:
    """2週しか入賞してない銘柄は ZEELE に入らない。"""
    engine = _engine(tmp_path)
    _add_universe(engine, [("6857", "アドバンテスト")])
    _insert_screening(
        engine,
        [_screening("6857", 3), _screening("6857", 10)],
    )

    out = _run(engine)
    assert out.newly_entered == []
    assert out.candidates == []

    with Session(engine) as s:
        assert s.exec(select(ZeeleState)).first() is None


def test_gap_in_middle_week_does_not_qualify(tmp_path: Path) -> None:
    """週0と週2は入賞だが週1が抜けている → ZEELE 入らない（連続性違反）。"""
    engine = _engine(tmp_path)
    _add_universe(engine, [("4452", "花王")])
    _insert_screening(
        engine,
        [_screening("4452", 2), _screening("4452", 16)],
    )
    out = _run(engine)
    assert out.candidates == []


def test_existing_state_increments_weeks(tmp_path: Path) -> None:
    """既に ZEELE に入っている銘柄は weeks_in_zeele がカレンダー経過で増える。"""
    engine = _engine(tmp_path)
    _add_universe(engine, [("9101", "商船三井")])
    # 60日前に entry したことにする
    entered = AS_OF - dt.timedelta(days=60)
    with Session(engine) as s:
        s.add(
            ZeeleState(
                ticker="9101",
                entered_at=entered,
                weeks_in_zeele=3,
                consecutive_weeks=3,
                last_screened_at=dt.datetime.combine(entered, dt.time(9, 0)),
                preset="value",
                structural_thesis="initial",
                reference_score=70.0,
                is_active=True,
            )
        )
        s.commit()

    # 直近3週連続で入賞し続けている
    _insert_screening(
        engine,
        [_screening("9101", 2), _screening("9101", 9), _screening("9101", 16)],
    )
    out = _run(engine)

    assert "9101" in out.still_active
    cand = next(c for c in out.candidates if c["ticker"] == "9101")
    # 60日 ≒ 8週超え。weeks_in_zeele が 3 のままではない
    assert cand["zeele_weeks"] >= 8


def test_stale_zeele_state_is_deactivated(tmp_path: Path) -> None:
    """v2.4 TASK-Z4: 28日以上沈黙 + 直近 28 日に screening が 20 回以上走った場合に降格。"""
    engine = _engine(tmp_path)
    _add_universe(engine, [("7203", "トヨタ"), ("OTHER", "ダミー")])
    long_ago = AS_OF - dt.timedelta(days=60)
    with Session(engine) as s:
        s.add(
            ZeeleState(
                ticker="7203",
                entered_at=long_ago,
                weeks_in_zeele=4,
                consecutive_weeks=4,
                last_screened_at=dt.datetime.combine(long_ago, dt.time(9, 0)),
                preset="value",
                structural_thesis="stale",
                reference_score=65.0,
                is_active=True,
            )
        )
        s.commit()

    # v2.4 TASK-Z4: screening_results に 7203 以外を 20 日連続で入れる
    # （screening 自体は走ってるが 7203 は未登場 → 降格対象）
    _insert_screening(
        engine,
        [_screening("OTHER", days_ago=d, v=70, theme=30) for d in range(1, 22)],
    )

    out = _run(engine)
    assert "7203" in out.deactivated

    with Session(engine) as s:
        state = s.exec(select(ZeeleState).where(ZeeleState.ticker == "7203")).one()
        assert state.is_active is False
        assert state.exited_at == AS_OF


def test_dry_run_does_not_persist(tmp_path: Path) -> None:
    """dry_run=True なら DB に書かない。"""
    engine = _engine(tmp_path)
    _add_universe(engine, [("8035", "東京エレクトロン")])
    _insert_screening(
        engine,
        [_screening("8035", 2), _screening("8035", 9), _screening("8035", 16)],
    )

    out = _run(engine, dry_run=True)
    assert "8035" in out.newly_entered

    with Session(engine) as s:
        assert s.exec(select(ZeeleState)).first() is None


def test_preset_inferred_from_score_dominance(tmp_path: Path) -> None:
    """v2.1 TASK-Z1: V字>50 でかつ details に price_bottom/value_trap が無ければ contrarian、
    theme>50 でかつ keyword_count<5 なら growth、≥5 なら momentum。
    """
    engine = _engine(tmp_path)
    _add_universe(engine, [("AAA", "A"), ("BBB", "B")])
    _insert_screening(
        engine,
        [
            # v2.10: 閾値 50→30 に下げたので theme は閾値未満（10）で V 字単独優位を作る
            # AAA: V字優位（v=70 ≥ 30, theme=10 < 30 → V 字主軸）→ contrarian
            _screening("AAA", 2, v=70, theme=10),
            _screening("AAA", 9, v=70, theme=10),
            _screening("AAA", 16, v=70, theme=10),
            # BBB: テーマ優位（v=10 < 30, theme=80 ≥ 30）→ growth
            _screening("BBB", 2, v=10, theme=80),
            _screening("BBB", 9, v=10, theme=80),
            _screening("BBB", 16, v=10, theme=80),
        ],
    )
    out = _run(engine)
    by_ticker = {c["ticker"]: c["preset"] for c in out.candidates}
    assert by_ticker["AAA"] == "contrarian"  # V 字主軸 + details なし → contrarian
    assert by_ticker["BBB"] == "growth"  # テーマ主軸 + keyword<5 → growth


# === Track B: signal_tags（record-only・shadow 計測用）===


def test_derive_signal_tags_sector_rs() -> None:
    """B2: theme_details.sector_outperformance > 0.05 で sector_rs タグが立つ。"""
    # 閾値超え（+8%pt）→ sector_rs
    assert _derive_signal_tags(
        _screening("AAA", 2, theme_details={"sector_outperformance": 0.08})
    ) == ["sector_rs"]
    # 閾値ちょうど未満（+3%pt）→ タグなし
    assert _derive_signal_tags(
        _screening("AAA", 2, theme_details={"sector_outperformance": 0.03})
    ) == []
    # theme_details なし → タグなし（KeyError/None で落ちない）
    assert _derive_signal_tags(_screening("AAA", 2, theme_details={})) == []
    # 非数値 → タグなし（型ガード）
    assert _derive_signal_tags(
        _screening("AAA", 2, theme_details={"sector_outperformance": "strong"})
    ) == []


def test_derive_signal_tags_earnings_accel_not_from_zeele() -> None:
    """earnings_accel は J-Quants 由来（magi_verify）に一本化。zeele_curator は v_shape から付けない。

    ※ codex 指摘: yfinance 由来代理は 0% 発火（dead）かつ source が混ざるため撤去。
    """
    # 旧 yfinance ignition があっても zeele_curator は earnings_accel を付けない
    assert _derive_signal_tags(
        _screening("AAA", 2, v_shape_details={"earnings_turnaround": "赤字→黒字"})
    ) == []
    # sector_rs は引き続き付く
    assert _derive_signal_tags(
        _screening(
            "AAA", 2,
            theme_details={"sector_outperformance": 0.08},
            v_shape_details={"earnings_turnaround": "赤字→黒字"},
        )
    ) == ["sector_rs"]


def test_signal_tags_propagate_to_candidate_and_state(tmp_path: Path) -> None:
    """B1: 3週連続入賞銘柄の signal_tags が candidate dict と ZeeleState に伝播・永続化する。"""
    engine = _engine(tmp_path)
    _add_universe(engine, [("AAA", "Aで強い"), ("BBB", "Bで普通")])
    sec_strong = {"sector_outperformance": 0.09}
    sec_weak = {"sector_outperformance": 0.01}
    _insert_screening(
        engine,
        [
            _screening("AAA", 2, theme=80, theme_details=sec_strong),
            _screening("AAA", 9, theme=80, theme_details=sec_strong),
            _screening("AAA", 16, theme=80, theme_details=sec_strong),
            _screening("BBB", 2, theme=80, theme_details=sec_weak),
            _screening("BBB", 9, theme=80, theme_details=sec_weak),
            _screening("BBB", 16, theme=80, theme_details=sec_weak),
        ],
    )
    out = _run(engine)
    by_ticker = {c["ticker"]: c["signal_tags"] for c in out.candidates}
    assert by_ticker["AAA"] == ["sector_rs"]  # 対セクター +9%pt → tag
    assert by_ticker["BBB"] == []  # +1%pt → tag なし

    # 永続化確認（ZeeleState に signal_tags が保存される）
    with Session(engine) as s:
        aaa = s.get(ZeeleState, "AAA")
        bbb = s.get(ZeeleState, "BBB")
        assert aaa is not None and aaa.signal_tags == ["sector_rs"]
        assert bbb is not None and bbb.signal_tags == []


def test_topic_narrative_joined_when_available(tmp_path: Path) -> None:
    """直近 topic で affected_tickers に含まれていれば structural_thesis に使う。"""
    engine = _engine(tmp_path)
    _add_universe(engine, [("8035", "東京エレクトロン")])
    _insert_screening(
        engine,
        [_screening("8035", 2), _screening("8035", 9), _screening("8035", 16)],
    )
    with Session(engine) as s:
        s.add(
            Topic(
                collected_at=dt.datetime.combine(AS_OF - dt.timedelta(days=1), dt.time(9, 0)),
                source="nikkei",
                source_url="https://example.com/x",
                category="market",
                importance="high",
                headline="AI 設備投資 受注残更新",
                summary="...",
                original_text_hash="h1",
                affected_tickers=["8035"],
                impact_text="positive",
                linked_decisions=[],
                fetched_by="news",
                importance_judged_by="rule",
                is_archived=False,
            )
        )
        s.commit()

    out = _run(engine)
    cand = next(c for c in out.candidates if c["ticker"] == "8035")
    assert "AI 設備投資" in cand["structural_thesis"]
