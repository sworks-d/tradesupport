"""エージェント出力の DB 永続化ヘルパー（Task 1.3.6）。

buy_signals / sell_signals は「翌日には is_active=False、新バッチで上書き」（SYSTEM_DESIGN §2.3）。
scenarios は 1 銘柄 1 行（ticker ユニーク）なので upsert する。
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.signals import BuySignal, Scenario, SellSignal
from trading_agent.models.topics import Topic


def save_buy_signals(engine: Engine, signals: list[BuySignal]) -> int:
    """既存の active を false にしてから新しい買いシグナルを保存する。"""
    with Session(engine) as session:
        session.execute(update(BuySignal).where(col(BuySignal.is_active)).values(is_active=False))
        for sig in signals:
            sig.is_active = True
            session.add(sig)
        session.commit()
    return len(signals)


def save_sell_signals(engine: Engine, signals: list[SellSignal]) -> int:
    """既存の active を false にしてから新しい売りシグナルを保存する。"""
    with Session(engine) as session:
        session.execute(update(SellSignal).where(col(SellSignal.is_active)).values(is_active=False))
        for sig in signals:
            sig.is_active = True
            session.add(sig)
        session.commit()
    return len(signals)


def save_scenarios(engine: Engine, scenarios: list[Scenario]) -> int:
    """シナリオを ticker 単位で upsert する。"""
    with Session(engine) as session:
        for sc in scenarios:
            existing = session.exec(
                select(Scenario).where(col(Scenario.ticker) == sc.ticker)
            ).first()
            if existing is not None:
                existing.scenario_health = sc.scenario_health
                existing.scenario_status = sc.scenario_status
                existing.checklist_progress = sc.checklist_progress
                existing.evaluation_notes = sc.evaluation_notes
                existing.latest_update = sc.latest_update
                session.add(existing)
            else:
                session.add(sc)
        session.commit()
    return len(scenarios)


def save_topics(engine: Engine, topics: list[Topic]) -> int:
    """トピックスを保存する（重複除去は呼び出し側 / news ツールの責務）。"""
    with Session(engine) as session:
        for topic in topics:
            session.add(topic)
        session.commit()
    return len(topics)
