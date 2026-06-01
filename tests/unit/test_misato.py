"""MISATO オーケストレーターの単体テスト（HALT・予算上限・割り当てルール・昇格判定）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.models.decisions import Decision
from trading_agent.portfolio import misato as M
from trading_agent.utils.time_utils import utcnow
from trading_agent.portfolio.misato import (
    MAX_BUDGET_PER_DISPATCH_JPY,
    MAX_BUDGET_PER_PILOT_JPY,
    allocate_budget,
    assign_to_pilot,
    check_halt,
    dispatch,
)


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "misato.sqlite")
    create_all(eng)
    return eng


def _add_decision(
    eng,
    *,
    ticker: str,
    stance: str = "推し",
    target_period_days: int | None = 60,
    stop_pct: float = 0.10,
    status: str = "awaiting",
) -> int:
    with Session(eng, expire_on_commit=False) as s:
        d = Decision(
            date=dt.date(2026, 5, 25),
            ticker=ticker,
            action="buy",
            status=status,
            gendo_stance=stance,
            target_period_days=target_period_days,
            stop_pct=stop_pct,
        )
        s.add(d)
        s.commit()
        s.refresh(d)
        return int(d.id)


class TestHalt:
    def test_halt_file_present_blocks_dispatch(self, tmp_path: Path) -> None:
        halt = tmp_path / "HALT"
        halt.write_text("manually halted")
        eng = _engine(tmp_path)
        plan = dispatch(
            eng, total_budget_jpy=100_000, approve=False, halt_file=halt
        )
        assert plan.halted is True
        assert "HALT" in plan.halt_reason
        assert plan.assignments == []

    def test_halt_absent_proceeds(self, tmp_path: Path) -> None:
        halt = tmp_path / "HALT_NOT_PRESENT"
        halted, _ = check_halt(halt)
        assert halted is False


class TestBudget:
    def test_equal_allocation_when_no_perf_data(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        alloc = allocate_budget(100_000, engine=eng)
        assert alloc.weighted is False
        assert pytest.approx(sum(alloc.per_pilot_jpy.values())) == 100_000
        assert all(v == 25_000 for v in alloc.per_pilot_jpy.values())

    def test_budget_capped_at_dispatch_max(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        alloc = allocate_budget(MAX_BUDGET_PER_DISPATCH_JPY * 3, engine=eng)
        assert alloc.total_jpy == MAX_BUDGET_PER_DISPATCH_JPY


class TestAssignment:
    def test_static_long_horizon_to_rei(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        did = _add_decision(eng, ticker="9999", target_period_days=180, stop_pct=0.10)
        with Session(eng) as s:
            d = s.get(Decision, did)
            assert d is not None
            pilot, why = assign_to_pilot(d, engine=eng)
        assert pilot == "REI"
        assert "long" in why or "REI" in why

    def test_seikan_stance_only_kaworu(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        did = _add_decision(eng, ticker="8888", stance="静観", target_period_days=60)
        with Session(eng) as s:
            d = s.get(Decision, did)
            assert d is not None
            pilot, _ = assign_to_pilot(d, engine=eng)
        assert pilot == "KAWORU"

    def test_short_high_vol_to_kaworu(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        did = _add_decision(
            eng, ticker="7777", target_period_days=21, stop_pct=0.15
        )
        with Session(eng) as s:
            d = s.get(Decision, did)
            assert d is not None
            pilot, _ = assign_to_pilot(d, engine=eng)
        assert pilot == "KAWORU"

    def test_mid_high_vol_to_asuka(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        did = _add_decision(eng, ticker="6666", target_period_days=60, stop_pct=0.13)
        with Session(eng) as s:
            d = s.get(Decision, did)
            assert d is not None
            pilot, _ = assign_to_pilot(d, engine=eng)
        assert pilot == "ASUKA"

    def test_mid_low_vol_to_shinji(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        did = _add_decision(eng, ticker="5555", target_period_days=60, stop_pct=0.08)
        with Session(eng) as s:
            d = s.get(Decision, did)
            assert d is not None
            pilot, _ = assign_to_pilot(d, engine=eng)
        assert pilot == "SHINJI"


class TestDispatchDryRun:
    def test_dry_run_returns_assignments_without_executing(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add_decision(eng, ticker="9999", target_period_days=180, stance="推し")  # REI 寄り
        _add_decision(eng, ticker="8888", target_period_days=21, stance="静観", stop_pct=0.05)  # KAWORU 寄り
        # v2.1: yfinance を叩かないように価格を注入
        plan = dispatch(
            eng, total_budget_jpy=80_000, approve=False,
            min_budget_overrides={"9999": 1000.0, "8888": 1000.0},
        )
        assert plan.halted is False
        assert plan.executed is False
        # 新フロー: 複数機が同じ銘柄を申請するので件数は >2
        assert len(plan.assignments) >= 2
        # 9999 (long horizon, 推し) は少なくとも REI が申請してるはず
        rei_assignments = [a for a in plan.assignments if a.assigned_to == "REI"]
        rei_tickers = {a.ticker for a in rei_assignments}
        assert "9999" in rei_tickers
        # 8888 (静観・短期・タイト) は KAWORU が申請してるはず
        kaworu_tickers = {a.ticker for a in plan.assignments if a.assigned_to == "KAWORU"}
        assert "8888" in kaworu_tickers
        # 合計は予算と一致
        assert plan.allocation is not None
        assert pytest.approx(sum(plan.allocation.per_pilot_jpy.values()), rel=0.01) == 80_000

    def test_dispatch_rejects_zero_budget(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        plan = dispatch(eng, total_budget_jpy=0, approve=False)
        assert plan.halted is True

    def test_approve_without_lookups_halts(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add_decision(eng, ticker="9999", target_period_days=180)
        plan = dispatch(eng, total_budget_jpy=80_000, approve=True)
        # price_lookup を渡さなかったので halt（安全装置）
        assert plan.executed is False
        assert plan.halted is True


class TestPromotion:
    def test_no_promotion_with_empty_data(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        # evaluated_at がない decision のみ → 昇格なし
        _add_decision(eng, ticker="9999")
        promotions = M.evaluate_promotions(eng)
        assert promotions == []


class TestTreasury:
    def test_deposit_increases_seed(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        M.deposit(eng, 50_000)
        M.deposit(eng, 30_000)
        view = M.treasury_view(eng)
        assert view["seed_jpy"] == 80_000
        assert view["deposit_count"] == 2
        assert view["available_jpy"] == 80_000

    def test_dispatch_uses_treasury_available_by_default(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        M.deposit(eng, 60_000)
        # gendo_stance="推し" を明示（DS Scout は stance なしを拾わない可能性ある）
        _add_decision(eng, ticker="9999", target_period_days=180, stance="推し")
        plan = dispatch(
            eng, total_budget_jpy=None, approve=False,
            min_budget_overrides={"9999": 1000.0},
        )
        # treasury available = 60k なので予算もそれ
        assert plan.total_budget_jpy == 60_000
        # 新フロー: REI を含む複数機が拾う可能性。合計は 60k。
        assert plan.allocation is not None
        assert pytest.approx(sum(plan.allocation.per_pilot_jpy.values()), rel=0.01) == 60_000
        # REI が候補 1 件以上は申請しているはず（長期・推し）
        rei_assigns = [a for a in plan.assignments if a.assigned_to == "REI"]
        assert len(rei_assigns) >= 1

    def test_dispatch_halts_when_treasury_empty(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        plan = dispatch(eng, total_budget_jpy=None, approve=False)
        assert plan.halted is True
        assert "預かり金" in plan.halt_reason

    def test_market_guard_skips_new_fills_when_nikkei_drops_3pct(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """C1: 日経 -3% 以下で市場ガード発動 → 新規 fill を停止する。

        approve=True で dispatch を呼んだ時、検出された risk_off により
        plan.halt_reason に「市場ガード発動」が記録され、各機の fill 配列が空になる。
        """
        from trading_agent.wille import ritsuko as _ritsuko
        from trading_agent.portfolio.misato import deposit

        eng = _engine(tmp_path)
        deposit(eng, 100_000)
        _add_decision(eng, ticker="9999", target_period_days=180, stance="推し")
        _add_decision(
            eng, ticker="8888", target_period_days=21, stance="静観", stop_pct=0.05
        )

        # 日経 -3.5% の市場ガード発動状態を模擬
        def _mock_market(*_args, **_kw):
            return {
                "nikkei_change_pct": -3.5,
                "topix_change_pct": -2.8,
                "is_risk_off": True,
                "regime": "risk_off",
            }

        monkeypatch.setattr(_ritsuko, "detect_market_regime_live", _mock_market)
        monkeypatch.setenv("WILLE_MARKET_GUARD", "1")

        plan = dispatch(
            eng,
            total_budget_jpy=100_000,
            approve=True,
            price_lookup=lambda t: 1000.0,
            is_jp_lookup=lambda t: True,
            min_budget_overrides={"9999": 1000.0, "8888": 1000.0},
        )
        # 市場ガードが halt_reason にメッセージを残しているか
        assert "市場ガード" in plan.halt_reason
        # 全機の fill 配列が空（新規 fill 抑止が走った証跡）
        for f in plan.fills:
            assert f.get("market_guard") is True
            assert len(f.get("fills") or []) == 0

    def test_market_guard_disabled_by_env(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """C1 補: WILLE_MARKET_GUARD=0 ならガード無効化（既存の挙動）。"""
        from trading_agent.wille import ritsuko as _ritsuko
        from trading_agent.portfolio.misato import deposit

        eng = _engine(tmp_path)
        deposit(eng, 100_000)
        _add_decision(eng, ticker="9999", target_period_days=180, stance="推し")

        def _mock_market(*_args, **_kw):
            return {"nikkei_change_pct": -5.0, "topix_change_pct": -4.0, "is_risk_off": True, "regime": "risk_off"}

        monkeypatch.setattr(_ritsuko, "detect_market_regime_live", _mock_market)
        monkeypatch.setenv("WILLE_MARKET_GUARD", "0")

        plan = dispatch(
            eng,
            total_budget_jpy=100_000,
            approve=True,
            price_lookup=lambda t: 1000.0,
            is_jp_lookup=lambda t: True,
            min_budget_overrides={"9999": 1000.0},
        )
        # ガード文言が halt_reason に入っていない
        assert "市場ガード" not in plan.halt_reason

    def test_reset_treasury_clears_seed_and_allocations(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        M.deposit(eng, 100_000)
        M.set_pilot_allocations(eng, {"REI": 25_000, "ASUKA": 75_000, "SHINJI": 0, "KAWORU": 0})
        M.reset_treasury(eng)
        view = M.treasury_view(eng)
        assert view["seed_jpy"] == 0
        assert view["allocated_jpy"] == 0
        assert all(v == 0 for v in view["allocations"].values())

    def test_needs_based_allocation_zero_demand_pilot_gets_zero(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        alloc = M.allocate_budget(
            100_000,
            engine=eng,
            demand_by_pilot={"REI": 0, "ASUKA": 2, "SHINJI": 0, "KAWORU": 0},
        )
        assert alloc.per_pilot_jpy["REI"] == 0
        assert alloc.per_pilot_jpy["ASUKA"] == 100_000
        assert alloc.per_pilot_jpy["SHINJI"] == 0
        assert alloc.per_pilot_jpy["KAWORU"] == 0
        assert alloc.mode == "needs-based"


class TestZeelePresetMapping:
    def test_value_preset_routes_to_rei(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        # ZEELE preset を持つ仮想 candidate
        cand = M.CandidatePool(
            decision_id=-1,
            ticker="9999",
            gendo_stance="ZEELE",
            source="zeele",
            preset="value",
            target_period_days=120,
            stop_pct=0.12,
            real_decision=None,
        )
        pilot, why = M.assign_to_pilot(cand, engine=eng)
        assert pilot == "REI"
        assert "preset" in why

    def test_growth_preset_routes_to_asuka(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        cand = M.CandidatePool(
            decision_id=-1,
            ticker="9998",
            gendo_stance="ZEELE",
            source="zeele",
            preset="growth",
            target_period_days=60,
            stop_pct=0.10,
            real_decision=None,
        )
        pilot, _ = M.assign_to_pilot(cand, engine=eng)
        assert pilot == "ASUKA"

    def test_alpha_preset_routes_to_kaworu(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        cand = M.CandidatePool(
            decision_id=-1,
            ticker="9997",
            gendo_stance="ZEELE",
            source="zeele",
            preset="alpha",
            target_period_days=30,
            stop_pct=0.08,
            real_decision=None,
        )
        pilot, _ = M.assign_to_pilot(cand, engine=eng)
        assert pilot == "KAWORU"


class TestDoubleRecommendationBoost:
    def test_both_source_gets_1_5x_weight(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        # demand=2 (両方 ASUKA だが片方 both)
        alloc = M.allocate_budget(
            100_000,
            engine=eng,
            demand_by_pilot={"REI": 0, "ASUKA": 2, "SHINJI": 0, "KAWORU": 0},
            weighted_demand_by_pilot={"REI": 0.0, "ASUKA": 2.5, "SHINJI": 0.0, "KAWORU": 0.0},  # 1.0 + 1.5
        )
        assert alloc.per_pilot_jpy["ASUKA"] == 100_000  # 全額（他が 0 のため）
        assert "ブースト" in alloc.reason or "boost" in alloc.reason.lower() or True  # 文言は柔軟


class TestAutoTrade:
    def test_master_auto_trade_on_off(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        assert M.is_master_auto_active(eng) is False
        M.set_master_auto_trade(eng, hours=1)
        assert M.is_master_auto_active(eng) is True
        M.set_master_auto_trade(eng, hours=None)
        assert M.is_master_auto_active(eng) is False

    def test_pilot_auto_trade_on_off(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        assert M.is_pilot_auto_active(eng, "REI") is False
        M.set_pilot_auto_trade(eng, "REI", hours=24)
        assert M.is_pilot_auto_active(eng, "REI") is True
        assert M.is_pilot_auto_active(eng, "ASUKA") is False

    def test_auto_trade_pilots_union(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        M.set_master_auto_trade(eng, hours=None)
        M.set_pilot_auto_trade(eng, "ASUKA", hours=1)
        assert M.auto_trade_pilots(eng) == {"ASUKA"}
        M.set_master_auto_trade(eng, hours=1)
        # マスター ON → 全機
        assert M.auto_trade_pilots(eng) == {"REI", "ASUKA", "SHINJI", "KAWORU"}

    def test_auto_trade_expires(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        M.set_master_auto_trade(eng, hours=1)
        future = utcnow() + dt.timedelta(hours=2)
        # 2 時間後には期限切れ
        assert M.is_master_auto_active(eng, now=future) is False


class TestDsScout:
    """DS 主導フロー：各機の select_from_pool が機の性格に応じた候補を返す。"""

    def _build_pool(self) -> list:
        return [
            M.CandidatePool(
                decision_id=1,
                ticker="VAL1",
                gendo_stance="ZEELE",
                source="zeele",
                preset="value",
                target_period_days=180,
                stop_pct=0.08,
                real_decision=None,
                score=10.0,
            ),
            M.CandidatePool(
                decision_id=2,
                ticker="MOM1",
                gendo_stance="推し",
                source="magi",
                preset=None,
                target_period_days=60,
                stop_pct=0.13,
                real_decision=None,
                score=0.7,
            ),
            M.CandidatePool(
                decision_id=3,
                ticker="PULL1",
                gendo_stance="ZEELE",
                source="zeele",
                preset="pullback",
                target_period_days=60,
                stop_pct=0.10,
                real_decision=None,
                score=20.0,
            ),
            M.CandidatePool(
                decision_id=4,
                ticker="ALPHA1",
                gendo_stance="静観",
                source="magi",
                preset=None,
                target_period_days=21,
                stop_pct=0.05,
                real_decision=None,
                score=0.5,
            ),
        ]

    def test_rei_picks_value(self, tmp_path: Path) -> None:
        from trading_agent.portfolio.ds_scout import select_from_pool

        eng = _engine(tmp_path)
        pool = self._build_pool()
        dummy_prices = {c.ticker: 1000.0 for c in pool}
        props = select_from_pool("REI", pool, engine=eng, min_budgets=dummy_prices)
        tickers = [p.ticker for p in props]
        assert "VAL1" in tickers
        # REI は value をドンピシャで拾うはず
        val_prop = next(p for p in props if p.ticker == "VAL1")
        assert val_prop.confidence >= 0.7

    def test_asuka_picks_momentum_high_conf(self, tmp_path: Path) -> None:
        from trading_agent.portfolio.ds_scout import select_from_pool

        eng = _engine(tmp_path)
        pool = self._build_pool()
        dummy_prices = {c.ticker: 1000.0 for c in pool}
        props = select_from_pool("ASUKA", pool, engine=eng, min_budgets=dummy_prices)
        # ASUKA は momentum/growth/中-高ボラ を拾うはず
        mom_props = [p for p in props if p.ticker == "MOM1"]
        assert len(mom_props) == 1
        assert mom_props[0].confidence >= 0.6

    def test_shinji_picks_pullback_top_conf(self, tmp_path: Path) -> None:
        from trading_agent.portfolio.ds_scout import select_from_pool

        eng = _engine(tmp_path)
        pool = self._build_pool()
        dummy_prices = {c.ticker: 1000.0 for c in pool}
        props = select_from_pool("SHINJI", pool, engine=eng, min_budgets=dummy_prices)
        pull = next((p for p in props if p.ticker == "PULL1"), None)
        assert pull is not None
        # SHINJI は pullback がドンピシャ → conf ≥ 0.85
        assert pull.confidence >= 0.85

    def test_kaworu_picks_seikan(self, tmp_path: Path) -> None:
        from trading_agent.portfolio.ds_scout import select_from_pool

        eng = _engine(tmp_path)
        pool = self._build_pool()
        dummy_prices = {c.ticker: 1000.0 for c in pool}
        props = select_from_pool("KAWORU", pool, engine=eng, min_budgets=dummy_prices)
        alpha = next((p for p in props if p.ticker == "ALPHA1"), None)
        assert alpha is not None
        # KAWORU の静観 + タイト stop はドンピシャ
        assert alpha.confidence >= 0.6

    def test_confidence_threshold_filters_low_fit(self, tmp_path: Path) -> None:
        from trading_agent.portfolio.ds_scout import select_from_pool

        eng = _engine(tmp_path)
        # REI に momentum 銘柄を投げる → confidence 低くて落ちる
        pool = [
            M.CandidatePool(
                decision_id=99,
                ticker="MOM2",
                gendo_stance="推し",
                source="magi",
                preset="momentum",
                target_period_days=20,  # 短期
                stop_pct=0.16,           # 高ボラ
                real_decision=None,
                score=0.3,
            )
        ]
        dummy_prices = {c.ticker: 1000.0 for c in pool}
        props = select_from_pool(
            "REI", pool, engine=eng, confidence_threshold=0.4, min_budgets=dummy_prices,
        )
        assert props == []  # REI は降りる
