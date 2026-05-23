"""screening ツールの単体テスト（Task 1.1.7）。固定データで期待スコアを厳密検証。"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from trading_agent.db import create_all, get_engine
from trading_agent.mcp_tools.base import MCPErrorType
from trading_agent.mcp_tools.screening import (
    ScreeningInput,
    ScreeningTickerData,
    ScreeningTool,
    calculate_theme_score,
    calculate_v_shape_score,
)
from trading_agent.models.signals import ScreeningResult


class TestVShapeScore:
    def test_full_combo_is_85(self) -> None:
        d = ScreeningTickerData(
            ticker="X",
            eps_latest_q=1.0,
            eps_prev_prev_q=-1.0,  # 赤字→黒字 +25
            current_price=112.0,
            min_price_90d=100.0,  # drawdown 0.12 > 0.1
            max_price_90d=200.0,  # 112 < 170 → +30
            rsi=40.0,  # 30<40<50 → +10
            macd_cross_recent=True,  # +10
            volume_5d_avg=200.0,
            volume_30d_avg=100.0,  # 200 > 150 → +10
        )
        score, details = calculate_v_shape_score(d)
        assert score == 85.0
        assert details["earnings_turnaround"] == "赤字→黒字"

    def test_revenue_growth_only(self) -> None:
        d = ScreeningTickerData(ticker="X", revenue_growth_latest_q=0.15)
        score, _ = calculate_v_shape_score(d)
        assert score == 10.0

    def test_empty_is_zero(self) -> None:
        assert calculate_v_shape_score(ScreeningTickerData(ticker="X"))[0] == 0.0

    def test_bottom_without_ignition_is_value_trap(self) -> None:
        # 底だが点火（業績反転）なし → 満額30でなく10＋value_trapフラグ（Value×Momentum）
        d = ScreeningTickerData(
            ticker="X",
            current_price=112.0, min_price_90d=100.0, max_price_90d=200.0,  # 底条件
        )
        score, details = calculate_v_shape_score(d)
        assert score == 10.0  # 満額30でない
        assert details.get("value_trap") is True

    def test_bottom_with_ignition_full_points(self) -> None:
        # 点火（赤字→黒字）あり → 底が満額30（25＋30＝55）。value_trapフラグなし
        d = ScreeningTickerData(
            ticker="X", eps_latest_q=1.0, eps_prev_prev_q=-1.0,
            current_price=112.0, min_price_90d=100.0, max_price_90d=200.0,
        )
        score, details = calculate_v_shape_score(d)
        assert score == 55.0
        assert "value_trap" not in details
        assert details["price_bottom"] is True

    def test_revenue_growth_is_not_ignition(self) -> None:
        # 増収だけは点火でない → 底は割引（10＋10＝20・value_trap）
        d = ScreeningTickerData(
            ticker="X", revenue_growth_latest_q=0.15,
            current_price=112.0, min_price_90d=100.0, max_price_90d=200.0,
        )
        score, details = calculate_v_shape_score(d)
        assert score == 20.0
        assert details.get("value_trap") is True


class TestThemeScore:
    def test_components_sum(self) -> None:
        d = ScreeningTickerData(
            ticker="X",
            keyword_match_count=4,  # 4*5 = 20
            sector_return_30d=0.10,
            market_return_30d=0.0,  # outperf 0.10 → 10
            institutional_activity_score=15.0,  # 15
            llm_theme_alignment_score=8.0,  # 8
        )
        score, _ = calculate_theme_score(d)
        assert score == 53.0

    def test_keyword_capped_at_40(self) -> None:
        d = ScreeningTickerData(ticker="X", keyword_match_count=20)  # 100 → 40
        assert calculate_theme_score(d)[0] == 40.0

    def test_negative_sector_not_subtracted(self) -> None:
        d = ScreeningTickerData(
            ticker="X", keyword_match_count=4, sector_return_30d=-0.1, market_return_30d=0.0
        )
        assert calculate_theme_score(d)[0] == 20.0  # キーワード20のみ、セクターは0


class TestTool:
    async def test_ranks_and_filters(self) -> None:
        high = ScreeningTickerData(
            ticker="HIGH",
            market_cap=1e9,
            eps_latest_q=1.0,
            eps_prev_prev_q=-1.0,
            current_price=112.0,
            min_price_90d=100.0,
            max_price_90d=200.0,
            rsi=40.0,
            macd_cross_recent=True,
            volume_5d_avg=200.0,
            volume_30d_avg=100.0,
        )  # v=85
        low = ScreeningTickerData(
            ticker="LOW", market_cap=2e9, revenue_growth_latest_q=0.15
        )  # v=10
        tool = ScreeningTool()
        out = await tool.execute(
            ScreeningInput(tickers_data=[low, high], min_score=50.0, persist=False)
        )
        assert out.total_screened == 2
        assert out.passed_count == 1
        assert [r["ticker"] for r in out.results] == ["HIGH"]

    async def test_only_v_shape_strategy(self) -> None:
        d = ScreeningTickerData(ticker="X", keyword_match_count=20)  # theme would be 40
        tool = ScreeningTool()
        out = await tool.execute(
            ScreeningInput(tickers_data=[d], strategies=["v_shape"], persist=False, min_score=1.0)
        )
        assert out.results[0]["theme_score"] == 0.0

    async def test_fallback_top5_when_none_pass(self) -> None:
        data = [
            ScreeningTickerData(ticker=f"T{i}", market_cap=float(i), revenue_growth_latest_q=0.15)
            for i in range(8)
        ]  # 各 v=10 < min_score
        tool = ScreeningTool()
        out = await tool.execute(ScreeningInput(tickers_data=data, min_score=50.0, persist=False))
        assert out.passed_count == 0
        assert len(out.results) == 5  # フォールバック上位5

    async def test_tie_break_by_market_cap(self) -> None:
        a = ScreeningTickerData(ticker="A", market_cap=1e9, revenue_growth_latest_q=0.15)
        b = ScreeningTickerData(ticker="B", market_cap=5e9, revenue_growth_latest_q=0.15)
        tool = ScreeningTool()
        out = await tool.execute(ScreeningInput(tickers_data=[a, b], min_score=1.0, persist=False))
        assert [r["ticker"] for r in out.results] == ["B", "A"]  # 同点→時価総額大が先

    async def test_persist_to_db(self, tmp_path: Path) -> None:
        engine = get_engine(tmp_path / "s.sqlite")
        create_all(engine)
        d = ScreeningTickerData(ticker="X", revenue_growth_latest_q=0.15)
        tool = ScreeningTool(engine)
        await tool.execute(ScreeningInput(tickers_data=[d], persist=True))
        with Session(engine) as session:
            rows = session.exec(select(ScreeningResult)).all()
        assert len(rows) == 1
        assert rows[0].ticker == "X"

    async def test_empty_input_is_validation_error(self) -> None:
        tool = ScreeningTool()
        out = await tool.execute(ScreeningInput(tickers_data=[]))
        assert out.success is False
        assert out.error_type == MCPErrorType.VALIDATION_ERROR
