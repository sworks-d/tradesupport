"""SQLModel テーブル定義（SYSTEM_DESIGN.md §2 / ORCHESTRATION.md §9.1）。

このパッケージを import すると全テーブルが ``SQLModel.metadata`` に登録される。
"""

from trading_agent.models.analytics import AnalysisLog, CostLog, HealthCheck
from trading_agent.models.batch import BatchState
from trading_agent.models.decisions import Decision
from trading_agent.models.magi import JudgeVerdict
from trading_agent.models.market_data import EarningsCalendar, MarketDataCache
from trading_agent.models.portfolio import Portfolio, PortfolioSnapshot
from trading_agent.models.settings import DEFAULT_SETTINGS, Setting, default_setting_rows
from trading_agent.models.signals import (
    BuySignal,
    Scenario,
    ScreeningResult,
    SellSignal,
)
from trading_agent.models.topics import ManualInput, Topic
from trading_agent.models.universe import Universe

__all__ = [
    "AnalysisLog",
    "BatchState",
    "BuySignal",
    "CostLog",
    "DEFAULT_SETTINGS",
    "Decision",
    "EarningsCalendar",
    "HealthCheck",
    "JudgeVerdict",
    "ManualInput",
    "MarketDataCache",
    "Portfolio",
    "PortfolioSnapshot",
    "Scenario",
    "ScreeningResult",
    "SellSignal",
    "Setting",
    "Topic",
    "Universe",
    "default_setting_rows",
]
