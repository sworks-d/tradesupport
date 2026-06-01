"""SQLModel テーブル定義（SYSTEM_DESIGN.md §2 / ORCHESTRATION.md §9.1）。

このパッケージを import すると全テーブルが ``SQLModel.metadata`` に登録される。
"""

from trading_agent.models.analytics import AnalysisLog, CostLog, HealthCheck
from trading_agent.models.batch import BatchState
from trading_agent.models.decisions import DECISION_STATUSES, Decision
from trading_agent.models.magi import (
    CommanderRec,
    JudgeVerdict,
    SplitPattern,
    Verification,
)
from trading_agent.models.market_data import EarningsCalendar, MarketDataCache
from trading_agent.models.misato_treasury import MisatoTreasury, PilotAllocation
from trading_agent.models.portfolio import Portfolio, PortfolioSnapshot
from trading_agent.models.settings import DEFAULT_SETTINGS, Setting, default_setting_rows
from trading_agent.models.signals import (
    BuySignal,
    Scenario,
    ScreeningResult,
    SellSignal,
)
from trading_agent.models.thesis import (
    THESIS_STATUS_ORDER,
    Thesis,
    ThesisStatus,
    ThesisType,
)
from trading_agent.models.topics import ManualInput, Topic
from trading_agent.models.universe import Universe
from trading_agent.models.zeele import ZeeleState

__all__ = [
    "AnalysisLog",
    "BatchState",
    "BuySignal",
    "CommanderRec",
    "CostLog",
    "DECISION_STATUSES",
    "DEFAULT_SETTINGS",
    "Decision",
    "EarningsCalendar",
    "HealthCheck",
    "JudgeVerdict",
    "ManualInput",
    "MarketDataCache",
    "MisatoTreasury",
    "PilotAllocation",
    "Portfolio",
    "PortfolioSnapshot",
    "Scenario",
    "ScreeningResult",
    "SellSignal",
    "Setting",
    "SplitPattern",
    "THESIS_STATUS_ORDER",
    "Thesis",
    "ThesisStatus",
    "ThesisType",
    "Topic",
    "Universe",
    "Verification",
    "ZeeleState",
    "default_setting_rows",
]
