"""zeele_llm_scout エージェントの単体テスト（Phase 4-B / M4.B7）。

実 LLM (Anthropic API) を呼ばずに、mock 経由で:
  - preset enum 強制 (H2)
  - confidence 値域正規化
  - cache 動作
  - BudgetGuard 連動
  - ZeeleState upsert
  - 後方互換（LLM 失敗時はスキップ）
を検証する。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlmodel import Session, select, col

from trading_agent.agents.context import AgentContext
from trading_agent.agents.zeele_llm_scout import (
    ZeeleLLMScoutAgent,
    ZeeleLLMScoutInput,
    _build_prompt,
    _normalize_confidence,
    _normalize_preset,
    _VALID_PRESETS,
    reset_cache,
)
from trading_agent.db import create_all, get_engine
from trading_agent.mcp_tools.base import MCPHost
from trading_agent.models.analytics import CostLog
from trading_agent.models.universe import Universe
from trading_agent.models.zeele import ZeeleState
from trading_agent.utils.time_utils import today_jst


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def engine(tmp_path: Path):
    eng = get_engine(tmp_path / "zls.sqlite")
    create_all(eng)
    return eng


@pytest.fixture
def ctx(engine):
    host = MCPHost()
    return AgentContext(
        engine=engine,
        host=host,
        invocation_id="test_zls",
    )


@pytest.fixture(autouse=True)
def _reset():
    reset_cache()
    yield
    reset_cache()


def _add_universe(engine, tickers: list[tuple[str, str]]) -> None:
    """tickers: [(ticker, sector), ...]"""
    with Session(engine) as s:
        for t, sec in tickers:
            s.add(
                Universe(
                    ticker=t,
                    name=f"Co_{t}",
                    market="JP",
                    sector=sec,
                    is_active=True,
                    market_cap=100_000_000_000.0,
                    market_cap_jpy=100_000_000_000.0,
                    avg_volume_30d=100000.0,
                )
            )
        s.commit()


# ============================================================
# H2 値域検証
# ============================================================


class TestPresetNormalization:
    def test_valid_preset_passes(self):
        for p in _VALID_PRESETS:
            assert _normalize_preset(p) == p

    def test_invalid_string_falls_back_to_alpha(self):
        assert _normalize_preset("invalid_preset") == "alpha"

    def test_empty_falls_back_to_alpha(self):
        assert _normalize_preset("") == "alpha"

    def test_none_falls_back_to_alpha(self):
        assert _normalize_preset(None) == "alpha"

    def test_int_falls_back_to_alpha(self):
        assert _normalize_preset(42) == "alpha"

    def test_case_normalized(self):
        assert _normalize_preset("VALUE") == "value"
        assert _normalize_preset("Growth") == "growth"


class TestConfidenceNormalization:
    def test_in_range(self):
        assert _normalize_confidence(0.5) == 0.5
        assert _normalize_confidence(0.0) == 0.0
        assert _normalize_confidence(1.0) == 1.0

    def test_out_of_range_clamped(self):
        assert _normalize_confidence(1.5) == 1.0
        assert _normalize_confidence(-0.5) == 0.0

    def test_invalid_returns_zero(self):
        assert _normalize_confidence("foo") == 0.0
        assert _normalize_confidence(None) == 0.0


class TestPromptBuilding:
    def test_prompt_includes_ticker(self):
        prompt = _build_prompt("7203", "Automotive", "brief test")
        assert "7203" in prompt
        assert "Automotive" in prompt

    def test_prompt_has_hallucination_block(self):
        prompt = _build_prompt("X", None, "")
        assert "ハルシネーション禁止" in prompt
        assert "推測でデータ作らない" not in prompt  # 文言は ritsuko 寄せでない
        assert "数値や将来予測を生成しない" in prompt

    def test_prompt_has_json_schema(self):
        prompt = _build_prompt("X", None, "")
        assert '"preset"' in prompt
        assert '"confidence"' in prompt
        assert '"reason"' in prompt


# ============================================================
# エージェント実行
# ============================================================


class TestAgentExecution:
    async def test_zero_max_calls_skips(self, engine, ctx) -> None:
        agent = ZeeleLLMScoutAgent(ctx)
        out = await agent.execute(
            ZeeleLLMScoutInput(invocation_id="t", max_calls=0)
        )
        assert out.success is True
        assert "skip" in (out.summary or "").lower() or "スキップ" in (out.summary or "")

    async def test_no_universe_returns_empty(self, engine, ctx) -> None:
        agent = ZeeleLLMScoutAgent(ctx)
        out = await agent.execute(
            ZeeleLLMScoutInput(invocation_id="t", max_calls=5)
        )
        assert out.success is True
        assert out.new_candidates == []

    async def test_llm_call_upserts_zeele_state(self, engine, ctx) -> None:
        _add_universe(engine, [("T100", "Tech"), ("T101", "Tech")])

        mock_result = {
            "preset": "growth",
            "confidence": 0.8,
            "reason": "LLM 判定 growth",
            "tokens_in": 100,
            "tokens_out": 30,
        }

        agent = ZeeleLLMScoutAgent(ctx)
        with patch(
            "trading_agent.agents.zeele_llm_scout.call_haiku_preset",
            return_value=mock_result,
        ):
            out = await agent.execute(
                ZeeleLLMScoutInput(invocation_id="t", max_calls=2)
            )

        assert out.success is True
        assert out.llm_call_count == 2

        # ZeeleState に upsert されている
        with Session(engine) as s:
            states = list(s.exec(select(ZeeleState).where(col(ZeeleState.is_active))))
            assert len(states) == 2
            for st in states:
                assert st.preset == "growth"
                assert st.reference_score == 80.0  # confidence 0.8 × 100
                assert "LLM 探索" in st.structural_thesis

    async def test_daily_budget_caps_calls(self, engine, ctx) -> None:
        """A-3 回帰: daily_budget_jpy が実際に効く（旧実装では dead param だった）。

        当日の zeele_llm_scout 既消費を上限直下まで seed しておくと、新規 LLM 呼び出しは
        予算超過で打ち切られる（llm_call_count==0）。
        """
        _add_universe(engine, [("T500", "Tech"), ("T501", "Tech"), ("T502", "Tech")])
        # 当日すでに ¥9.95 消費済（日次上限 ¥10 直下）
        with Session(engine) as s:
            s.add(
                CostLog(
                    date=today_jst(),
                    model="haiku",
                    agent="zeele_llm_scout",
                    purpose="zeele_preset_scout",
                    tokens_in=1,
                    tokens_out=1,
                    cost_usd=9.95 / 150.0,
                    cost_jpy=9.95,
                )
            )
            s.commit()

        mock_result = {
            "preset": "growth",
            "confidence": 0.8,
            "reason": "should not be called",
            "tokens_in": 100,
            "tokens_out": 30,
        }
        agent = ZeeleLLMScoutAgent(ctx)
        with patch(
            "trading_agent.agents.zeele_llm_scout.call_haiku_preset",
            return_value=mock_result,
        ) as mock_call:
            out = await agent.execute(
                ZeeleLLMScoutInput(invocation_id="t", max_calls=3, daily_budget_jpy=10.0)
            )
        assert out.success is True
        assert out.llm_call_count == 0  # 予算上限で打ち切り
        assert mock_call.call_count == 0
        with Session(engine) as s:
            states = list(s.exec(select(ZeeleState).where(col(ZeeleState.is_active))))
        assert states == []

    async def test_llm_failure_skips_without_crash(self, engine, ctx) -> None:
        _add_universe(engine, [("T200", "Tech")])
        agent = ZeeleLLMScoutAgent(ctx)
        with patch(
            "trading_agent.agents.zeele_llm_scout.call_haiku_preset",
            return_value=None,  # 失敗
        ):
            out = await agent.execute(
                ZeeleLLMScoutInput(invocation_id="t", max_calls=1)
            )
        # LLM 失敗時は alpha フォールバックでスキップ、上書きしない
        assert out.success is True
        with Session(engine) as s:
            states = list(s.exec(select(ZeeleState)))
        assert states == []  # 何も入らない

    async def test_low_confidence_not_upserted(self, engine, ctx) -> None:
        _add_universe(engine, [("T300", "Tech")])
        mock_result = {
            "preset": "growth",
            "confidence": 0.3,  # 0.5 未満 → upsert スキップ
            "reason": "low conf",
            "tokens_in": 50,
            "tokens_out": 20,
        }
        agent = ZeeleLLMScoutAgent(ctx)
        with patch(
            "trading_agent.agents.zeele_llm_scout.call_haiku_preset",
            return_value=mock_result,
        ):
            await agent.execute(
                ZeeleLLMScoutInput(invocation_id="t", max_calls=1)
            )
        with Session(engine) as s:
            states = list(s.exec(select(ZeeleState).where(col(ZeeleState.is_active))))
        assert len(states) == 0  # confidence < 0.5 で upsert しない

    async def test_invalid_preset_normalized_then_upserted(self, engine, ctx) -> None:
        """LLM が想定外 preset を返しても alpha に強制される (H2)。"""
        _add_universe(engine, [("T400", "Tech")])
        mock_result = {
            "preset": "WTF_INVALID",  # 想定外
            "confidence": 0.7,
            "reason": "test",
            "tokens_in": 100,
            "tokens_out": 30,
        }
        # call_haiku_preset 内で _normalize_preset が呼ばれて alpha になる
        # ここでは call_haiku_preset の戻り値を直接モックするので preset を alpha にしておく
        # → 実 Haiku 呼び出し経路をシミュレートするため、normalize 済みの値を返す
        mock_result["preset"] = _normalize_preset(mock_result["preset"])

        agent = ZeeleLLMScoutAgent(ctx)
        with patch(
            "trading_agent.agents.zeele_llm_scout.call_haiku_preset",
            return_value=mock_result,
        ):
            await agent.execute(
                ZeeleLLMScoutInput(invocation_id="t", max_calls=1)
            )
        with Session(engine) as s:
            st = s.exec(select(ZeeleState).where(col(ZeeleState.is_active))).first()
        assert st is not None
        assert st.preset == "alpha"  # H2 フォールバック

    async def test_cache_avoids_repeat_llm_call(self, engine, ctx) -> None:
        _add_universe(engine, [("T500", "Tech")])
        mock_result = {
            "preset": "value",
            "confidence": 0.9,
            "reason": "value 判定",
            "tokens_in": 100,
            "tokens_out": 30,
        }
        agent = ZeeleLLMScoutAgent(ctx)
        with patch(
            "trading_agent.agents.zeele_llm_scout.call_haiku_preset",
            return_value=mock_result,
        ) as m:
            await agent.execute(
                ZeeleLLMScoutInput(invocation_id="t", max_calls=1)
            )
            await agent.execute(
                ZeeleLLMScoutInput(invocation_id="t2", max_calls=1)
            )
        # 1 回目は call、2 回目は cache hit
        assert m.call_count == 1

    async def test_explicit_tickers_respected(self, engine, ctx) -> None:
        _add_universe(engine, [("T600", "Tech"), ("T601", "Tech"), ("T602", "Tech")])
        mock_result = {
            "preset": "alpha",
            "confidence": 0.6,
            "reason": "test",
            "tokens_in": 50,
            "tokens_out": 20,
        }
        agent = ZeeleLLMScoutAgent(ctx)
        with patch(
            "trading_agent.agents.zeele_llm_scout.call_haiku_preset",
            return_value=mock_result,
        ):
            await agent.execute(
                ZeeleLLMScoutInput(
                    invocation_id="t",
                    target_tickers=["T601"],
                    max_calls=5,
                )
            )
        with Session(engine) as s:
            states = list(s.exec(select(ZeeleState).where(col(ZeeleState.is_active))))
        assert len(states) == 1
        assert states[0].ticker == "T601"
