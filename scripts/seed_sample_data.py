#!/usr/bin/env python
"""ダッシュボード確認用のサンプルデータ投入（開発・UI 確認用）。

エージェント稼働前でも全パネルが描画できるよう、現実的なサンプル（¥10万ベース）を
DB に投入する。再実行可能（対象テーブルをクリアしてから入れ直す）。

実行：uv run python scripts/seed_sample_data.py
注意：本番データではない。エージェント実装後は朝バッチが実データで上書きする。
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, SQLModel, select  # noqa: E402

from trading_agent.config import get_settings  # noqa: E402
from trading_agent.db import get_engine, init_database  # noqa: E402
from trading_agent.models.market_data import MarketDataCache  # noqa: E402
from trading_agent.models.portfolio import Portfolio, PortfolioSnapshot  # noqa: E402
from trading_agent.models.signals import BuySignal, SellSignal  # noqa: E402
from trading_agent.models.topics import Topic  # noqa: E402
from trading_agent.models.universe import Universe  # noqa: E402
from trading_agent.utils.time_utils import utcnow  # noqa: E402

_TODAY = dt.date(2026, 5, 22)


def _clear(session: Session, model: type[SQLModel]) -> None:
    for row in session.exec(select(model)).all():
        session.delete(row)


def _universe() -> list[Universe]:
    return [
        Universe(
            ticker="7203",
            name="トヨタ自動車",
            market="JP",
            sector="自動車",
            market_cap=4.0e13,
            market_cap_jpy=4.0e13,
            avg_volume_30d=2.0e7,
        ),
        Universe(
            ticker="NVDA",
            name="NVIDIA",
            market="US",
            sector="半導体",
            market_cap=3.3e12,
            market_cap_jpy=5.0e14,
            avg_volume_30d=4.0e8,
        ),
        Universe(
            ticker="AMD",
            name="Advanced Micro Devices",
            market="US",
            sector="半導体",
            market_cap=2.6e11,
            market_cap_jpy=3.9e13,
            avg_volume_30d=5.0e7,
        ),
        Universe(
            ticker="6758",
            name="ソニーグループ",
            market="JP",
            sector="電機",
            market_cap=1.6e13,
            market_cap_jpy=1.6e13,
            avg_volume_30d=8.0e6,
        ),
    ]


def _snapshots() -> list[PortfolioSnapshot]:
    snaps = []
    base = 98000.0
    for i in range(30):
        d = _TODAY - dt.timedelta(days=29 - i)
        total = base + i * 230 + (180 if i % 3 == 0 else -90)
        snaps.append(
            PortfolioSnapshot(
                date=d,
                total_assets_jpy=round(total),
                cash_jpy=22500,
                us_stocks_value_jpy=round(total * 0.5),
                jp_stocks_value_jpy=round(total * 0.28),
                satellite_value_jpy=0.0,
                core_value_jpy=round(total * 0.78),
                usd_jpy_rate=150.0,
                holding_count=2,
                daily_pnl_jpy=230.0 if i % 3 == 0 else -90.0,
            )
        )
    return snaps


def _holdings() -> list[Portfolio]:
    return [
        Portfolio(
            ticker="7203",
            buy_date=dt.date(2026, 3, 10),
            buy_price=2800.0,
            qty=10,
            currency="JPY",
            strategy_category="中期",
            target_period_days=180,
            target_pct=0.20,
            stop_loss_pct=-0.08,
            target_date=dt.date(2026, 9, 6),
            thesis="PBR改革 + 北米販売回復で中期上昇",
            thesis_checklist=[{"item": "北米販売 +10%", "checked": True}],
            status="active",
            last_synced_at=utcnow(),
        ),
        Portfolio(
            ticker="NVDA",
            buy_date=dt.date(2026, 4, 1),
            buy_price=110.0,
            qty=3,
            currency="USD",
            strategy_category="中期",
            target_period_days=120,
            target_pct=0.30,
            stop_loss_pct=-0.10,
            target_date=dt.date(2026, 7, 30),
            thesis="データセンター需要継続、CUDA 障壁",
            thesis_checklist=[{"item": "Q売上 +40%", "checked": True}],
            status="active",
            last_synced_at=utcnow(),
        ),
    ]


def _market_data() -> list[MarketDataCache]:
    def row(t: str, cur: float, prev: float) -> MarketDataCache:
        return MarketDataCache(
            ticker=t,
            current_price=cur,
            open_price=prev,
            high_today=cur * 1.01,
            low_today=prev * 0.99,
            prev_close=prev,
            volume_today=1e7,
            price_change_today=cur - prev,
            price_change_pct_today=(cur - prev) / prev,
            market_status="closed",
            as_of=utcnow(),
            source="seed",
        )

    return [row("7203", 3100.0, 3050.0), row("NVDA", 135.0, 132.0)]


def _sell_signals() -> list[SellSignal]:
    return [
        SellSignal(
            ticker="7203",
            signal_type="profit_taking",
            score=72,
            target_achievement_score=0.8,
            scenario_achievement_score=0.7,
            technical_warning_score=0.4,
            ai_confidence=0.75,
            reasons=[
                {"text": "目標株価まで残り5%、RSI 71で過熱"},
                {"text": "シナリオ（北米回復）はほぼ達成"},
            ],
            recommended_action={"type": "limit_sell", "price": 3120, "qty": 5, "note": "半量利確"},
        )
    ]


def _buy_signals() -> list[BuySignal]:
    return [
        BuySignal(
            ticker="AMD",
            score=78,
            fundamental_score=0.75,
            technical_score=0.7,
            news_sentiment_score=0.8,
            strategy_fit_score=0.85,
            ai_confidence=0.72,
            expected_return=0.27,
            win_rate=0.6,
            target_period_days=120,
            target_price=210.0,
            entry_price=165.0,
            stop_loss_price=150.0,
            strategy_category="中期",
            scenarios=[
                {"type": "bull", "target_price": 240, "return_pct": 0.45, "prob": 0.3},
                {"type": "base", "target_price": 210, "return_pct": 0.27, "prob": 0.5},
                {"type": "bear", "target_price": 150, "return_pct": -0.09, "prob": 0.2},
            ],
            recommended_amount_jpy=25000,
        ),
        BuySignal(
            ticker="6758",
            score=65,
            fundamental_score=0.7,
            technical_score=0.6,
            news_sentiment_score=0.6,
            strategy_fit_score=0.7,
            ai_confidence=0.65,
            expected_return=0.18,
            win_rate=0.58,
            target_period_days=180,
            target_price=4200.0,
            entry_price=3600.0,
            stop_loss_price=3300.0,
            strategy_category="中期-長期",
            scenarios=[{"type": "base", "target_price": 4200, "return_pct": 0.18, "prob": 0.55}],
            recommended_amount_jpy=20000,
        ),
    ]


def _topics() -> list[Topic]:
    now = utcnow()
    return [
        Topic(
            collected_at=now,
            source="Bloomberg",
            source_url="https://example.com/ai-capex",
            category="sector",
            importance="high",
            headline="AI設備投資、主要クラウド各社が上方修正",
            summary="データセンター投資の加速で半導体需要が継続。",
            original_text_hash="h1",
            affected_tickers=["NVDA", "AMD"],
            impact_text="半導体テーマの追い風",
            fetched_by="seed",
            importance_judged_by="rule",
        ),
        Topic(
            collected_at=now,
            source="日経",
            source_url="https://example.com/toyota",
            category="stock",
            importance="medium",
            headline="トヨタ、通期見通しを上方修正",
            summary="北米販売の回復が寄与。",
            original_text_hash="h2",
            affected_tickers=["7203"],
            impact_text="7203 利確判断の根拠の一つ",
            fetched_by="seed",
            importance_judged_by="rule",
        ),
        Topic(
            collected_at=now,
            source="Reuters",
            source_url="https://example.com/fed",
            category="macro",
            importance="medium",
            headline="FRB、年内利下げ観測が後退",
            summary="インフレ高止まりで慎重姿勢。",
            original_text_hash="h3",
            affected_tickers=[],
            impact_text="グロース株の重し",
            fetched_by="seed",
            importance_judged_by="rule",
        ),
    ]


def main() -> None:
    settings = get_settings()
    init_database(settings.db_path)  # テーブル存在を保証
    engine = get_engine(settings.db_path)

    with Session(engine) as session:
        for model in (SellSignal, BuySignal, Topic, MarketDataCache, PortfolioSnapshot, Portfolio):
            _clear(session, model)
        for ticker in ("7203", "NVDA", "AMD", "6758"):
            existing = session.get(Universe, ticker)
            if existing:
                session.delete(existing)
        session.commit()

        for rows in (
            _universe(),
            _snapshots(),
            _holdings(),
            _market_data(),
            _sell_signals(),
            _buy_signals(),
            _topics(),
        ):
            session.add_all(rows)
        session.commit()

    print("✅ サンプルデータ投入完了")
    print("   保有2件 / 売り推奨1件 / 買い推奨2件 / トピックス3件 / 30日スナップショット")
    print("   起動: uv run uvicorn trading_agent.main:app --port 8000 → http://localhost:8000")


if __name__ == "__main__":
    main()
