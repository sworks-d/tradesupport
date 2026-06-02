"""P4-1 ペーパー執行ループの単体テスト（approved のみ・翌寄り紙約定・record_entry）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session, col, select

from trading_agent.db import create_all, get_engine
from trading_agent.models.decisions import Decision
from trading_agent.models.portfolio import Portfolio
from trading_agent.models.universe import Universe
from trading_agent.portfolio.paper_exec import paper_close_due, paper_fill_approved


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "paper.sqlite")
    create_all(eng)
    return eng


def _add(eng, ticker: str, status: str) -> int:
    with Session(eng, expire_on_commit=False) as s:
        # v2.10: paper_fill_approved の Universe 照合（ハルシネーション防壁）通過のため
        # テスト fixture にも Universe を追加する。
        existing = s.exec(select(Universe).where(col(Universe.ticker) == ticker)).first()
        if existing is None:
            s.add(
                Universe(
                    ticker=ticker,
                    name=ticker,
                    market="JP",
                    sector="Industrials",
                    market_cap=1.0e12,
                    market_cap_jpy=1.0e12,
                    avg_volume_30d=1.0e6,
                    is_active=True,
                )
            )
        d = Decision(date=dt.date(2026, 5, 25), ticker=ticker, action="buy", status=status)
        s.add(d)
        s.commit()
        s.refresh(d)
        return int(d.id)


class TestAllowedTickersGate:
    """A+（測定帰属保護）: allowed_tickers 指定時、picked 外の awaiting は fill されない。"""

    def _seed(self, eng, ticker: str, stance: str, status: str) -> int:
        with Session(eng, expire_on_commit=False) as s:
            if s.exec(select(Universe).where(col(Universe.ticker) == ticker)).first() is None:
                s.add(Universe(
                    ticker=ticker, name=ticker, market="JP", sector="Industrials",
                    market_cap=1e12, market_cap_jpy=1e12, avg_volume_30d=1e6, is_active=True,
                ))
            d = Decision(date=dt.date(2026, 5, 25), ticker=ticker, action="buy",
                         status=status, gendo_stance=stance, stop_pct=0.10,
                         target_period_days=60)
            s.add(d); s.commit(); s.refresh(d)
            return int(d.id)

    def test_non_picked_awaiting_not_filled(self, tmp_path: Path) -> None:
        from trading_agent.portfolio.personality import PERSONALITIES
        eng = _engine(tmp_path)
        # picked（approved）= 7203、非picked（awaiting・stance一致）= 6758
        self._seed(eng, "7203", "推し", "approved")
        self._seed(eng, "6758", "推し", "awaiting")
        rei = PERSONALITIES["REI"]  # accept に「推し」を含む
        res = paper_fill_approved(
            eng, price_lookup=lambda _t: 1000.0, is_jp_lookup=lambda _t: True,
            cash_jpy=1_000_000.0, today=dt.date(2026, 5, 25),
            personality=rei, allowed_tickers={"7203"},
        )
        filled_tickers = {f.ticker for f in res.fills}
        assert "7203" in filled_tickers       # picked は fill
        assert "6758" not in filled_tickers   # 非picked awaiting は弾かれる（plan外fill防止）

    def test_without_allowed_tickers_legacy_behavior(self, tmp_path: Path) -> None:
        # allowed_tickers 無指定なら従来通り（awaiting stance一致も拾う）
        from trading_agent.portfolio.personality import PERSONALITIES
        eng = _engine(tmp_path)
        self._seed(eng, "6758", "推し", "awaiting")
        rei = PERSONALITIES["REI"]
        res = paper_fill_approved(
            eng, price_lookup=lambda _t: 1000.0, is_jp_lookup=lambda _t: True,
            cash_jpy=1_000_000.0, today=dt.date(2026, 5, 25), personality=rei,
        )
        assert "6758" in {f.ticker for f in res.fills}  # 従来挙動は維持


class TestExitRecordsOnDecision:
    """C（測定正確性）: stop/time 退出が紐付く buy Decision の実績に反映される。"""

    def _seed_position(self, eng, *, ticker="7203", buy=1000.0, stop=0.10) -> int:
        with Session(eng, expire_on_commit=False) as s:
            s.add(Universe(
                ticker=ticker, name=ticker, market="JP", sector="Industrials",
                market_cap=1e12, market_cap_jpy=1e12, avg_volume_30d=1e6, is_active=True,
            ))
            d = Decision(
                date=dt.date(2026, 5, 25), ticker=ticker, action="buy", status="filled",
                entry_price=buy, stop_pct=stop, expected_return=0.20,
                target_period_days=90, evaluation_date=dt.date(2026, 8, 23),
                hit_or_miss="pending",
            )
            s.add(d)
            s.commit()
            s.refresh(d)
            s.add(Portfolio(
                ticker=ticker, buy_date=dt.date(2026, 5, 25), buy_price=buy, qty=10,
                currency="JPY", strategy_category="中期", target_period_days=90,
                target_pct=0.20, stop_loss_pct=stop,
                target_date=dt.date(2026, 8, 23), thesis="t", status="active",
                broker_mode="paper", planned_total_qty=10, decision_id=d.id,
            ))
            s.commit()
            return int(d.id)

    def test_stop_exit_records_miss_on_decision(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        did = self._seed_position(eng)
        # 現価 850 = -15% ≤ -stop(10%) → stop_loss 発火
        paper_close_due(
            eng, price_lookup=lambda _t: 850.0, is_jp_lookup=lambda _t: True,
            today=dt.date(2026, 6, 10),
        )
        with Session(eng) as s:
            d = s.get(Decision, did)
            assert d.hit_or_miss == "miss"  # 実退出が損失確定
            assert d.actual_return is not None and d.actual_return < 0
            assert d.evaluated_at is not None

    def test_evaluation_does_not_double_count(self, tmp_path: Path) -> None:
        # 退出記録後、評価ジョブは pending でないので再採点しない
        from trading_agent.evaluation.job import evaluate_due_decisions
        eng = _engine(tmp_path)
        self._seed_position(eng)
        paper_close_due(
            eng, price_lookup=lambda _t: 850.0, is_jp_lookup=lambda _t: True,
            today=dt.date(2026, 6, 10),
        )
        n, _ = evaluate_due_decisions(
            eng, price_lookup=lambda _t: 1300.0, today=dt.date(2026, 8, 24)
        )
        assert n == 0  # 既に実退出で評価済み＝horizon価格で上書きされない


class TestPaperFill:
    def test_approved_is_filled(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        did = _add(eng, "7203", "approved")
        res = paper_fill_approved(
            eng, price_lookup=lambda _t: 1000.0, is_jp_lookup=lambda _t: True,
            cash_jpy=100_000.0, today=dt.date(2026, 5, 25),
        )
        assert len(res.fills) == 1
        f = res.fills[0]
        assert f.ticker == "7203"
        # v2.2 TASK-SZ2: 端数 0.67 切り上げで 17 株（旧 16 株）
        assert f.shares == 17
        # v2.4 TASK-F1: volume データ無し → slippage 1.5x（0.2% × 1.5 = 0.3%） → fill_price ≈ 1003
        # cost = 17 × 1003 = 17,051、現金 ≈ 82,949
        import pytest
        assert res.cash_after == pytest.approx(82_949.0, abs=0.001)
        with Session(eng) as s:
            pos = s.exec(select(Portfolio).where(col(Portfolio.ticker) == "7203")).one()
            assert pos.status == "active"
            assert pos.qty == 17
            # Portfolio.buy_price は実約定価格（slippage 適用後・float 精度で 1002.99...）
            assert pos.buy_price == pytest.approx(1003.0, abs=0.001)
            assert pos.stop_loss_pct == 0.12  # v2.1 TASK-SZ4: 正値で統一
            assert pos.target_pct == 0.0  # B'：利確で刻まない
            d = s.get(Decision, did)
            assert d.status == "holding"
            assert d.entry_price == pytest.approx(1003.0, abs=0.001)  # v2.4 TASK-F1
            assert d.evaluation_date is not None  # 評価期日が付く

    def test_only_approved_touched(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add(eng, "6758", "verifying")  # 未検証は執行しない
        _add(eng, "9984", "awaiting")  # 決裁待ちも執行しない
        res = paper_fill_approved(
            eng, price_lookup=lambda _t: 1000.0, is_jp_lookup=lambda _t: True,
            cash_jpy=100_000.0,
        )
        assert res.fills == []
        with Session(eng) as s:
            assert s.exec(select(Portfolio)).all() == []

    def test_price_unavailable_is_skipped(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add(eng, "7203", "approved")
        res = paper_fill_approved(
            eng, price_lookup=lambda _t: None, is_jp_lookup=lambda _t: True,
            cash_jpy=100_000.0,
        )
        assert res.fills == []
        assert res.skipped and res.skipped[0][0] == "7203"

    def test_too_expensive_is_skipped(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add(eng, "9983", "approved")  # 1株が予算超（高単価JP）
        res = paper_fill_approved(
            eng, price_lookup=lambda _t: 50_000.0, is_jp_lookup=lambda _t: True,
            cash_jpy=100_000.0,
        )
        # budget=min(1R/stop=16667, ...)=16667 < 50000 → 0株 → skip
        assert res.fills == []
        assert res.skipped and "サイズ0" in res.skipped[0][1]
