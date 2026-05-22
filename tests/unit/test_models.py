"""モデルと DB 初期化の単体テスト（Task 1.0.4）。

ファイルベースの一時 SQLite を使い、テーブル作成・CRUD・JSON 往復・settings
シードの冪等性を検証する。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlmodel import Session, select

from trading_agent.db import (
    create_all,
    get_engine,
    init_database,
    seed_default_settings,
)
from trading_agent.models import (
    DEFAULT_SETTINGS,
    BatchState,
    BuySignal,
    Setting,
    Topic,
    Universe,
)

EXPECTED_TABLES = {
    "universe",
    "portfolio",
    "portfolio_snapshots",
    "market_data_cache",
    "earnings_calendar",
    "screening_results",
    "buy_signals",
    "sell_signals",
    "scenarios",
    "decisions",
    "topics",
    "manual_inputs",
    "analysis_logs",
    "cost_logs",
    "health_checks",
    "settings",
    "batch_states",
}


@pytest.fixture()
def engine(tmp_path: Path):
    eng = get_engine(tmp_path / "test.sqlite")
    create_all(eng)
    return eng


class TestSchema:
    def test_all_tables_created(self, engine) -> None:
        names = set(inspect(engine).get_table_names())
        assert EXPECTED_TABLES <= names
        assert len(EXPECTED_TABLES) == 17

    def test_indexes_exist(self, engine) -> None:
        inspector = inspect(engine)
        portfolio_indexes = {
            col for idx in inspector.get_indexes("portfolio") for col in idx["column_names"]
        }
        # status / ticker にインデックス（SYSTEM_DESIGN §2.4）
        assert "status" in portfolio_indexes
        assert "ticker" in portfolio_indexes


class TestCrud:
    def test_universe_crud(self, engine) -> None:
        with Session(engine) as session:
            session.add(
                Universe(
                    ticker="AAPL",
                    name="Apple",
                    market="US",
                    sector="Technology",
                    market_cap=3.0e12,
                    market_cap_jpy=4.5e14,
                    avg_volume_30d=5.0e7,
                )
            )
            session.commit()
        with Session(engine) as session:
            row = session.get(Universe, "AAPL")
            assert row is not None
            assert row.name == "Apple"
            assert row.is_active is True  # デフォルト

    def test_buy_signal_json_roundtrip(self, engine) -> None:
        scenarios = [
            {"type": "bull", "target_price": 140, "return_pct": 0.42, "prob": 0.30},
            {"type": "base", "target_price": 125, "return_pct": 0.27, "prob": 0.50},
        ]
        with Session(engine) as session:
            session.add(
                BuySignal(
                    ticker="AAPL",
                    score=78,
                    fundamental_score=0.8,
                    technical_score=0.7,
                    news_sentiment_score=0.6,
                    strategy_fit_score=0.9,
                    ai_confidence=0.75,
                    expected_return=0.27,
                    win_rate=0.6,
                    target_period_days=90,
                    target_price=125.0,
                    entry_price=100.0,
                    stop_loss_price=92.0,
                    strategy_category="中期",
                    scenarios=scenarios,
                    recommended_amount_jpy=20000,
                )
            )
            session.commit()
        with Session(engine) as session:
            row = session.exec(select(BuySignal)).one()
            assert row.scenarios == scenarios
            assert row.thesis_checklist == []  # default_factory
            assert row.is_active is True

    def test_topic_list_fields_roundtrip(self, engine) -> None:
        with Session(engine) as session:
            session.add(
                Topic(
                    source="Bloomberg",
                    source_url="https://example.com/a",
                    category="macro",
                    importance="high",
                    headline="FRB 利下げ観測",
                    summary="...",
                    original_text_hash="abc123",
                    affected_tickers=["AAPL", "MSFT"],
                    impact_text="ハイテク全般に追い風",
                    fetched_by="morning_batch",
                    importance_judged_by="rule",
                )
            )
            session.commit()
        with Session(engine) as session:
            row = session.exec(select(Topic)).one()
            assert row.affected_tickers == ["AAPL", "MSFT"]
            assert row.is_archived is False

    def test_batch_state_dict_roundtrip(self, engine) -> None:
        node_status = {"screening": "success", "market_analyst": "partial"}
        with Session(engine) as session:
            session.add(
                BatchState(
                    invocation_id="morning_2026-05-22",
                    batch_type="morning",
                    status="partial",
                    started_at=dt.datetime(2026, 5, 22, 5, 0, 0),
                    node_status=node_status,
                    summary="2 銘柄失敗",
                )
            )
            session.commit()
        with Session(engine) as session:
            row = session.get(BatchState, "morning_2026-05-22")
            assert row is not None
            assert row.node_status == node_status


class TestSeedSettings:
    def test_seed_inserts_all_defaults(self, engine) -> None:
        with Session(engine) as session:
            inserted = seed_default_settings(session)
        assert inserted == len(DEFAULT_SETTINGS)
        with Session(engine) as session:
            count = len(session.exec(select(Setting)).all())
        assert count == len(DEFAULT_SETTINGS)

    def test_seed_is_idempotent(self, engine) -> None:
        with Session(engine) as session:
            seed_default_settings(session)
        with Session(engine) as session:
            second = seed_default_settings(session)
        assert second == 0  # 2回目は追加なし
        with Session(engine) as session:
            count = len(session.exec(select(Setting)).all())
        assert count == len(DEFAULT_SETTINGS)

    def test_setting_value_is_json(self, engine) -> None:
        with Session(engine) as session:
            seed_default_settings(session)
        with Session(engine) as session:
            budget = session.get(Setting, "monthly_budget_jpy")
            assert budget is not None
            assert json.loads(budget.value) == 5000
            assert budget.value_type == "int"
            keywords = session.get(Setting, "theme_keywords")
            assert keywords is not None
            assert json.loads(keywords.value) == ["AI", "半導体", "防衛", "原油"]


class TestInitDatabase:
    def test_init_creates_db_and_seeds(self, tmp_path: Path) -> None:
        db_path = tmp_path / "init.sqlite"
        result = init_database(db_path)
        assert db_path.exists()
        assert result["tables"] == 19  # 既存17 + judge_verdict(B2) + verification(B3)
        assert result["settings_inserted"] == len(DEFAULT_SETTINGS)

    def test_init_is_idempotent(self, tmp_path: Path) -> None:
        db_path = tmp_path / "init.sqlite"
        init_database(db_path)
        second = init_database(db_path)
        # 2回目はテーブルもsettingsも追加されない
        assert second["settings_inserted"] == 0
