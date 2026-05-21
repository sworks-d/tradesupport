"""エージェント出力シリアライズの単体テスト（Task 1.3.6）。"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, col, select

from trading_agent.agents.serialization import (
    save_buy_signals,
    save_scenarios,
    save_sell_signals,
)
from trading_agent.db import create_all, get_engine
from trading_agent.models.signals import BuySignal, Scenario, SellSignal


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "ser.sqlite")
    create_all(eng)
    return eng


def _buy(ticker: str) -> BuySignal:
    return BuySignal(
        ticker=ticker,
        score=80,
        fundamental_score=0.8,
        technical_score=0.8,
        news_sentiment_score=0.8,
        strategy_fit_score=0.8,
        ai_confidence=0.8,
        expected_return=0.3,
        win_rate=0.6,
        target_period_days=90,
        target_price=100.0,
        entry_price=80.0,
        stop_loss_price=72.0,
        strategy_category="中期",
        recommended_amount_jpy=20000,
    )


class TestBuySignals:
    def test_save_deactivates_previous(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        save_buy_signals(engine, [_buy("OLD")])
        save_buy_signals(engine, [_buy("NEW")])
        with Session(engine) as s:
            active = s.exec(select(BuySignal).where(col(BuySignal.is_active))).all()
            all_rows = s.exec(select(BuySignal)).all()
        assert {r.ticker for r in active} == {"NEW"}
        assert len(all_rows) == 2  # 旧も残るが is_active=False


class TestSellSignals:
    def test_save_deactivates_previous(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        save_sell_signals(
            engine,
            [SellSignal(ticker="A", signal_type="profit_taking", score=60, ai_confidence=0.7)],
        )
        save_sell_signals(
            engine, [SellSignal(ticker="B", signal_type="stop_loss", score=70, ai_confidence=0.7)]
        )
        with Session(engine) as s:
            active = s.exec(select(SellSignal).where(col(SellSignal.is_active))).all()
        assert {r.ticker for r in active} == {"B"}


class TestScenarios:
    def test_upsert_by_ticker(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        save_scenarios(
            engine,
            [
                Scenario(
                    ticker="X", scenario_health=0.5, scenario_status="intact", evaluation_notes="v1"
                )
            ],
        )
        save_scenarios(
            engine,
            [
                Scenario(
                    ticker="X",
                    scenario_health=0.2,
                    scenario_status="weakening",
                    evaluation_notes="v2",
                )
            ],
        )
        with Session(engine) as s:
            rows = s.exec(select(Scenario).where(col(Scenario.ticker) == "X")).all()
        assert len(rows) == 1  # upsert（重複しない）
        assert rows[0].scenario_status == "weakening"
        assert rows[0].evaluation_notes == "v2"
