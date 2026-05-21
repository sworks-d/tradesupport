"""settings テーブル（UI から変更できる動的設定）。SYSTEM_DESIGN.md §2.3 / §6.4。

``value`` は JSON 文字列として保存する（``value_type`` で型を示す）。
``DEFAULT_SETTINGS`` は init_db 時の初期投入値。【たたき台】の数値はここに集約し、
4週間運用後にユーザーが UI から調整する前提（CLAUDE_CODE_INSTRUCTIONS.md 原則4）。
"""

import datetime as dt
import json
from typing import Any, NamedTuple

from sqlmodel import Field, SQLModel

from trading_agent.models._common import utcnow


class Setting(SQLModel, table=True):
    """1つの動的設定（key-value）。"""

    __tablename__ = "settings"

    key: str = Field(primary_key=True)
    value: str  # JSON 文字列
    value_type: str  # "int" / "float" / "str" / "json" / "bool"
    category: str  # "budget" / "strategy" / "ui" 等
    description: str | None = None
    updated_at: dt.datetime = Field(default_factory=utcnow)
    updated_by: str = "user"


class _Default(NamedTuple):
    key: str
    value: Any  # Python 値（JSON 化前）
    value_type: str
    category: str
    description: str


# SYSTEM_DESIGN.md §2.3（settings 初期値）+ §6.4（theme_keywords）
DEFAULT_SETTINGS: tuple[_Default, ...] = (
    _Default("monthly_budget_jpy", 5000, "int", "budget", "月次API予算上限（円）"),
    _Default("daily_budget_jpy", 500, "int", "budget", "日次API予算上限（円）"),
    _Default("budget_breach_action", "halt", "str", "budget", "予算超過時の挙動"),
    _Default(
        "usd_jpy_rate_override", None, "float", "currency", "USD/JPY 手動上書き（null で自動）"
    ),
    _Default("morning_batch_time", "05:00", "str", "schedule", "朝バッチ実行時刻（JST）"),
    _Default("auto_sync_interval_min", 5, "int", "schedule", "moomoo 同期間隔（分）"),
    _Default("max_position_pct_of_cash", 0.20, "float", "risk", "1銘柄の現金比上限"),
    _Default("max_position_pct_of_total", 0.10, "float", "risk", "1銘柄の総資産比上限"),
    _Default("screening_universe_size", 500, "int", "screening", "スクリーニング母集団サイズ"),
    _Default("paper_mode", True, "bool", "mode", "ペーパーモード"),
    _Default(
        "theme_keywords",
        ["AI", "半導体", "防衛", "原油"],
        "json",
        "screening",
        "テーマ戦略のキーワード",
    ),
)


def default_setting_rows() -> list[Setting]:
    """``DEFAULT_SETTINGS`` から ``Setting`` 行を生成する（呼ぶたびに新インスタンス）。"""
    return [
        Setting(
            key=d.key,
            value=json.dumps(d.value, ensure_ascii=False),
            value_type=d.value_type,
            category=d.category,
            description=d.description,
            updated_by="system",
        )
        for d in DEFAULT_SETTINGS
    ]
