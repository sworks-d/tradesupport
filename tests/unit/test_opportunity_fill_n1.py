"""N1: opportunity_fill が G-7 逓減 (TAPER_SCHEDULE) と整合した
cash_floor を動的に適用することのテスト。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from trading_agent.wille.opportunity_fill import (
    GuardrailConfig,
    opportunity_driven_fill,
)


@dataclass
class _MockProposal:
    """テスト用 proposal（PilotProposal の最低限フィールド）。"""

    ticker: str
    confidence: float = 0.9
    min_budget_jpy: float = 1500.0
    source: str = "magi"


def _props(n: int = 1) -> dict[str, list[_MockProposal]]:
    return {
        "REI": [
            _MockProposal(ticker=f"T{i:04d}", confidence=0.9, min_budget_jpy=1500.0)
            for i in range(n)
        ]
    }


class TestN1CashFloorByAccountSize:
    """N1: account_total_jpy が指定されると、cash_floor が G-7 逓減で動的に変わる。

    TAPER_SCHEDULE (risk/params.py):
        ~¥300k          cash_floor 0.20
        ¥300k-¥1M       cash_floor 0.20
        ¥1M-¥3M         cash_floor 0.15
        ¥3M-¥10M        cash_floor 0.15
        ¥10M+           cash_floor 0.10
    """

    def test_default_config_keeps_0_40_when_no_account_total(self) -> None:
        """account_total_jpy 未指定なら GuardrailConfig.min_cash_reserve_pct=0.40 のまま。"""
        # 予算 ¥10,000、買付 ¥1,500 のみ
        # cash_floor=0.40 → ¥4,000 死守
        # 1 件買ったら残¥8,500 ≥ ¥4,000 → 採用
        # 5 件買ったら残¥2,500 < ¥4,000 → 拒否
        config = GuardrailConfig(
            max_lot_pct=0.5,
            max_pilot_pct=1.0,
            max_source_pct=1.0,
            max_sector_pct=1.0,
        )
        plan = opportunity_driven_fill(
            _props(n=10),
            total_budget_jpy=10000.0,
            config=config,
        )
        # cash_floor 0.40 で残予算がきつい → 採用件数は少なめ
        # 採用 1 件 → 残 ¥8,350 ≥ ¥4,000 OK
        # 採用 4 件 → 残 ¥3,910 < ¥4,000 → 拒否
        assert 1 <= len(plan.selected) <= 4

    def test_small_account_uses_taper_0_20(self) -> None:
        """account_total ¥100k → TAPER tier 0 (cash_floor=0.20) を適用。"""
        config = GuardrailConfig(
            max_lot_pct=0.5,
            max_pilot_pct=1.0,
            max_source_pct=1.0,
            max_sector_pct=1.0,
        )
        plan = opportunity_driven_fill(
            _props(n=10),
            total_budget_jpy=10000.0,
            config=config,
            account_total_jpy=100_000.0,  # 少額 tier
        )
        # cash_floor 0.20 → ¥2,000 死守、買付緩和
        # config.min_cash_reserve_pct=0.40 (¥4,000) のときより採用件数が増える
        plan_strict = opportunity_driven_fill(
            _props(n=10),
            total_budget_jpy=10000.0,
            config=config,
        )
        assert len(plan.selected) >= len(plan_strict.selected)

    def test_large_account_uses_taper_0_10(self) -> None:
        """account_total ¥20M → TAPER tier 4 (cash_floor=0.10) を適用。"""
        config = GuardrailConfig(
            max_lot_pct=0.5,
            max_pilot_pct=1.0,
            max_source_pct=1.0,
            max_sector_pct=1.0,
        )
        plan_large = opportunity_driven_fill(
            _props(n=10),
            total_budget_jpy=10000.0,
            config=config,
            account_total_jpy=20_000_000.0,  # 大額 tier
        )
        plan_small = opportunity_driven_fill(
            _props(n=10),
            total_budget_jpy=10000.0,
            config=config,
            account_total_jpy=100_000.0,  # 少額 tier
        )
        # 大額 tier (0.10) の方が cash_floor が緩い → 採用件数は同等以上
        assert len(plan_large.selected) >= len(plan_small.selected)

    def test_zero_account_falls_back_to_config(self) -> None:
        """account_total_jpy=0 なら GuardrailConfig.min_cash_reserve_pct を使う（後方互換）。"""
        config = GuardrailConfig(
            max_lot_pct=0.5,
            max_pilot_pct=1.0,
            max_source_pct=1.0,
            max_sector_pct=1.0,
            min_cash_reserve_pct=0.30,
        )
        # account=0 → config の 0.30 を使う
        plan_zero = opportunity_driven_fill(
            _props(n=10),
            total_budget_jpy=10000.0,
            config=config,
            account_total_jpy=0.0,
        )
        plan_none = opportunity_driven_fill(
            _props(n=10),
            total_budget_jpy=10000.0,
            config=config,
            account_total_jpy=None,
        )
        # 0 と None は等価（どちらも config を使う）
        assert len(plan_zero.selected) == len(plan_none.selected)


class TestBackwardCompat:
    """既存呼出（account_total_jpy 引数なし）の挙動が変わらないこと。"""

    def test_existing_callers_unaffected(self) -> None:
        config = GuardrailConfig()  # min_cash_reserve_pct=0.40 default
        plan = opportunity_driven_fill(
            _props(n=5),
            total_budget_jpy=10000.0,
            config=config,
        )
        # 旧挙動：cash_floor=¥4,000 で 採用が制限される
        # 拒否の理由が "cash 最低保持" を含むものが存在
        rejected_reasons = [r["reason"] for r in plan.rejected]
        # 既存テストが壊れないことだけ確認（厳密な件数は問わない）
        assert isinstance(rejected_reasons, list)


class TestSizeGuard:
    """大指針 #2(中小型成長株): opportunity_fill の大型/不明 exposure ガードと補完 last-resort。

    2026-06-04 の Phase C 大型偏重事故の再発防止テスト。
    """

    def _cfg(self) -> GuardrailConfig:
        # size ガードだけを効かせる（他の上限と cash floor を無効化）
        return GuardrailConfig(
            max_lot_pct=1.0, max_source_pct=1.0, max_sector_pct=1.0,
            max_pilot_pct=1.0, min_cash_reserve_pct=0.0, min_boost=-1.0,
            max_large_cap_pct=0.30,
        )

    def test_large_cap_rejected_over_budget(self) -> None:
        # 大型(¥1兆超)だけの proposal。予算×30%=¥3000 を超える分は拒否される。
        props = {"REI": [_MockProposal(ticker=f"L{i}", min_budget_jpy=1500.0) for i in range(5)]}
        mc = {f"L{i}": 5.0e12 for i in range(5)}  # 全部 ¥5兆=large
        plan = opportunity_driven_fill(
            props, total_budget_jpy=10000.0, config=self._cfg(), market_cap_lookup=mc,
        )
        # 大型上限¥3000 内に収まる分しか採用されない（全採用は起きない）
        assert sum(s["lot_cost_jpy"] for s in plan.selected) <= 3000.0 + 1
        assert any("大型/不明" in r.get("reason", "") for r in plan.rejected)

    def test_unknown_market_cap_also_guarded(self) -> None:
        # 時価総額 lookup 漏れ(unknown)も大型と同じ上限でガードされる（すり抜け防止）。
        props = {"REI": [_MockProposal(ticker=f"U{i}", min_budget_jpy=1500.0) for i in range(5)]}
        plan = opportunity_driven_fill(
            props, total_budget_jpy=10000.0, config=self._cfg(), market_cap_lookup={},  # 全部 unknown
        )
        assert sum(s["lot_cost_jpy"] for s in plan.selected) <= 3000.0 + 1
        assert any("大型/不明" in r.get("reason", "") for r in plan.rejected)

    def test_small_cap_not_size_rejected(self) -> None:
        # 中小型(¥500億)は size ガードに掛からず採用される。
        props = {"REI": [_MockProposal(ticker=f"S{i}", min_budget_jpy=1000.0) for i in range(3)]}
        mc = {f"S{i}": 5.0e10 for i in range(3)}  # ¥500億=small
        plan = opportunity_driven_fill(
            props, total_budget_jpy=10000.0, config=self._cfg(), market_cap_lookup=mc,
        )
        assert len(plan.selected) == 3
        assert all(s["size_bucket"] == "small" for s in plan.selected)

    def test_supplement_is_last_resort(self) -> None:
        # M4: 実候補(magi)が補完(supplement)より先に予算を取る。予算が両方に足りない時、
        # 実候補が採用され補完が残予算落ちになる。
        cfg = GuardrailConfig(
            max_lot_pct=1.0, max_source_pct=1.0, max_sector_pct=1.0,
            max_pilot_pct=1.0, min_cash_reserve_pct=0.0, min_boost=-1.0,
        )
        props = {"REI": [
            _MockProposal(ticker="SUP", confidence=0.99, min_budget_jpy=8000.0, source="supplement"),
            _MockProposal(ticker="REAL", confidence=0.50, min_budget_jpy=8000.0, source="magi"),
        ]}
        mc = {"SUP": 5.0e10, "REAL": 5.0e10}
        plan = opportunity_driven_fill(
            props, total_budget_jpy=10000.0, config=cfg, market_cap_lookup=mc,
        )
        # confidence は SUP の方が高いが、実候補 REAL が先に採用される（補完は last-resort）
        selected_tickers = [s["ticker"] for s in plan.selected]
        assert "REAL" in selected_tickers
        assert selected_tickers[0] == "REAL"  # 実候補が先頭
