"""M5(unlock 方式): Phase C 段階大規模化（口座 ¥100万 固定・deploy 上限 ¥10万 から段階解放）の単体テスト。

codex 推奨の unlock 方式: seed(口座総額)は ¥100万 固定で equity 曲線を歪めず、実際に deploy
してよい上限だけを ¥10万 から gate⑥通過ごとに 10→30→60→100万 へ解放する。解放の唯一根拠は
official_gate_evaluation(broker_mode="paper").passed。多重解放防止に新規評価件数を要求する。
LLM/ネット非依存。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.models.decisions import Decision
from trading_agent.portfolio.misato import (
    PAPER_ACCOUNT_CAPITAL_JPY,
    PAPER_CEILING_JPY,
    PAPER_START_BUDGET_JPY,
    advance_paper_budget,
    current_risk_budget_jpy,
    deployable_budget_jpy,
    init_paper_staging,
    treasury_view,
)
from trading_agent.utils.time_utils import utcnow


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "staging.sqlite")
    create_all(eng)
    return eng


def _add_passing_paper_records(eng, n: int, prefix: str) -> None:
    """ゲート⑥を満たす paper の評価済 decision を n 件足す（両局面・命中・α>0）。"""
    with Session(eng, expire_on_commit=False) as s:
        for i in range(n):
            regime = "bull" if i % 3 else "bear"  # 両局面を確保
            s.add(Decision(
                date=dt.date(2026, 5, 25), ticker=f"{prefix}{i:04d}", action="buy",
                status="filled", entry_price=1000.0, stop_pct=0.10, expected_return=0.20,
                actual_return=0.10, benchmark_return=0.0, hit_or_miss="hit",
                evaluated_at=utcnow(), entry_market_regime=regime,
                filled_via="ds_dispatch", entry_broker_mode="paper",
            ))
        s.commit()


class TestInitPaperStaging:
    def test_init_sets_capital_and_unlock(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        info = init_paper_staging(eng)
        assert info["status"] == "initialized"
        # 口座総額 ¥100万 固定 / 解放済み deploy 上限 ¥10万
        assert info["account_capital_jpy"] == PAPER_ACCOUNT_CAPITAL_JPY
        assert current_risk_budget_jpy(eng, "paper") == PAPER_START_BUDGET_JPY
        v = treasury_view(eng, "paper")
        assert v["seed_jpy"] == PAPER_ACCOUNT_CAPITAL_JPY  # 口座総額は ¥100万
        assert v["current_risk_budget_jpy"] == PAPER_START_BUDGET_JPY  # 解放は ¥10万

    def test_init_idempotent(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        init_paper_staging(eng)
        again = init_paper_staging(eng)
        assert again["status"] == "already_initialized"
        assert current_risk_budget_jpy(eng, "paper") == PAPER_START_BUDGET_JPY


class TestDeployableBudget:
    def test_deployable_is_unlocked_minus_exposure(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        init_paper_staging(eng)  # 解放 ¥10万・exposure 0
        assert deployable_budget_jpy(eng, "paper") == PAPER_START_BUDGET_JPY
        # active paper 保有を 1 件作る（¥40,000 ぶん）→ deployable は解放上限 − exposure
        from trading_agent.models.portfolio import Portfolio
        with Session(eng, expire_on_commit=False) as s:
            s.add(Portfolio(ticker="7203", buy_date=dt.date(2026, 5, 25),
                            buy_price=1000.0, qty=40, currency="JPY",
                            strategy_category="中期", target_period_days=90,
                            target_pct=0.20, stop_loss_pct=0.10,
                            target_date=dt.date(2026, 8, 25), thesis="test",
                            status="active", broker_mode="paper"))
            s.commit()
        # codex F#8: exposure は手数料/スリッページ保守バッファ込み（×1.005）
        assert deployable_budget_jpy(eng, "paper") == PAPER_START_BUDGET_JPY - 40_000 * 1.005

    def test_deployable_none_when_not_initialized(self, tmp_path: Path) -> None:
        # 解放上限未設定（legacy/非 Phase C）は None＝cap なし（従来挙動を壊さない）
        eng = _engine(tmp_path)
        assert deployable_budget_jpy(eng, "paper") is None


class TestCommonLayerCap:
    """codex#5: 安全装置を paper_fill_approved 共通層に置き、直呼びでも cap が効く。"""

    def test_paper_fill_caps_cash_at_deployable(self, tmp_path: Path) -> None:
        from trading_agent.models.decisions import Decision
        from trading_agent.portfolio.paper_exec import paper_fill_approved

        eng = _engine(tmp_path)
        init_paper_staging(eng)  # 口座¥100万・解放¥10万
        # approved な buy decision を 1 件（高額銘柄）。呼び出し側が ¥100万 を渡しても
        # deployable(¥10万) で cap され、¥10万 を超える deploy はできない。
        with Session(eng, expire_on_commit=False) as s:
            s.add(Decision(date=dt.date(2026, 5, 25), ticker="7203", action="buy",
                           status="approved", gendo_stance="推し"))
            s.commit()
        prices = {"7203": 5000.0}
        res = paper_fill_approved(
            eng,
            price_lookup=lambda t: prices.get(t),
            is_jp_lookup=lambda t: True,
            cash_jpy=1_000_000.0,  # ¥100万 を直叩きで渡す（攻撃ケース）
            broker_mode="paper",
        )
        deployed = sum(f.amount_jpy for f in res.fills)
        assert deployed <= PAPER_START_BUDGET_JPY  # 解放枠 ¥10万 を超えない


class TestAdvancePaperBudget:
    def test_no_advance_when_gate_not_passed(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        init_paper_staging(eng)
        info = advance_paper_budget(eng)
        assert info["status"] == "gate_not_passed"
        assert current_risk_budget_jpy(eng, "paper") == PAPER_START_BUDGET_JPY  # 据え置き

    def test_advance_one_tier_on_gate_pass(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        init_paper_staging(eng)  # 解放 ¥10万・last_unlock_eval_n=0
        _add_passing_paper_records(eng, 30, "A")  # 新規 30 件
        info = advance_paper_budget(eng)
        assert info["status"] == "advanced"
        assert current_risk_budget_jpy(eng, "paper") == 300_000.0  # ¥10万→¥30万

    def test_double_unlock_blocked_without_fresh_evals(self, tmp_path: Path) -> None:
        # 同一 gate snapshot で連続解放しない（多重解放防止・codex 指摘）
        eng = _engine(tmp_path)
        init_paper_staging(eng)
        _add_passing_paper_records(eng, 30, "A")
        advance_paper_budget(eng)  # ¥30万へ（last_unlock_eval_n=30）
        info = advance_paper_budget(eng)  # 新規評価なしで再解放を試みる
        assert info["status"] == "insufficient_fresh_evals"
        assert current_risk_budget_jpy(eng, "paper") == 300_000.0  # 据え置き

    def test_staged_unlock_to_ceiling_with_fresh_samples(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        init_paper_staging(eng)
        for tier, prefix in zip((300_000.0, 600_000.0, 1_000_000.0), "ABC"):
            _add_passing_paper_records(eng, 30, prefix)  # 各 tier ごとに新規 30 件
            advance_paper_budget(eng)
            assert current_risk_budget_jpy(eng, "paper") == tier
        # 上限到達後はそれ以上解放しない
        _add_passing_paper_records(eng, 30, "D")
        assert advance_paper_budget(eng)["status"] == "ceiling_reached"
        assert current_risk_budget_jpy(eng, "paper") == PAPER_CEILING_JPY

    def test_live_budget_not_touched_by_paper_staging(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        init_paper_staging(eng)
        assert current_risk_budget_jpy(eng, "live") == 0.0
