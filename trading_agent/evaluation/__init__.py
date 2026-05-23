"""P6 評価層（測って上げる）。RESEARCH_METHODS 領域4（バックテストの罠を避ける）。"""

from trading_agent.evaluation.backtest import Trade, backtest_signal, sma_cross_signal
from trading_agent.evaluation.job import evaluate_due_decisions, record_entry
from trading_agent.evaluation.metrics import (
    EvalResult,
    TrackRecord,
    build_track_record,
    evaluate_position,
)

__all__ = [
    "EvalResult",
    "Trade",
    "TrackRecord",
    "backtest_signal",
    "build_track_record",
    "evaluate_due_decisions",
    "evaluate_position",
    "record_entry",
    "sma_cross_signal",
]
