"""MAGI 判断層：3審判の独立検証 → 防御層 → 統合機構 → 碇司令。

本パッケージは「ジャッジ直前の独立検証機関」。既存エージェントの分析・推奨を入力に取り、
互いを見ずに可否を出す（三権独立）。総合スコアは出さない。判定不能は na。
B2 ではまず3審判（judges）を実装する。
"""

from trading_agent.magi.commander import CommanderResult, command
from trading_agent.magi.defense import VerificationResult, verify
from trading_agent.magi.integration import SplitResult, classify_split
from trading_agent.magi.judges import balthasar, casper, melchior, run_judges

__all__ = [
    "CommanderResult",
    "SplitResult",
    "VerificationResult",
    "balthasar",
    "casper",
    "classify_split",
    "command",
    "melchior",
    "run_judges",
    "verify",
]
