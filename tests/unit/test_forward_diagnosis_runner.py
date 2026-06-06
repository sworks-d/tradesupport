"""forward_diagnosis runner の定期化ゲート（A+C 監査）の単体テスト。LLM/ネット非依存。

scripts/forward_diagnosis.py の forward_diagnosis_enabled() が Setting
forward_diagnosis_enabled を正しく解釈し、既定 OFF（未設定/false/不正値 → False）で
構築期間中の自動 yfinance 実行を防ぐことを固定する。
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session

from scripts.forward_diagnosis import forward_diagnosis_enabled
from trading_agent.db import create_all, get_engine
from trading_agent.models.settings import Setting


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "fwd_runner.sqlite")
    create_all(eng)
    return eng


def _set(eng, value: str) -> None:
    with Session(eng) as s:
        s.add(
            Setting(
                key="forward_diagnosis_enabled", value=value,
                value_type="bool", category="ops",
            )
        )
        s.commit()


def test_disabled_when_setting_absent(tmp_path: Path) -> None:
    # 既定 OFF（未設定）→ 構築期間中は走らない
    assert forward_diagnosis_enabled(_engine(tmp_path)) is False


def test_enabled_when_true(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    _set(eng, "true")
    assert forward_diagnosis_enabled(eng) is True


def test_disabled_when_false(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    _set(eng, "false")
    assert forward_diagnosis_enabled(eng) is False


def test_disabled_when_invalid_value(tmp_path: Path) -> None:
    # 不正値は安全側（False）に倒す（推測で活性化しない）
    eng = _engine(tmp_path)
    _set(eng, "not-json")
    assert forward_diagnosis_enabled(eng) is False
