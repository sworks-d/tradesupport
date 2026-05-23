"""ポートフォリオ規律ゲート（外骨格 G-3）の単体テスト。"""

from __future__ import annotations

from trading_agent.risk import (
    Candidate,
    Held,
    evaluate_portfolio_guard,
)

TOTAL = 100_000.0


def _c(ticker: str, sector: str, amount: float = 15_000.0) -> Candidate:
    return Candidate(ticker=ticker, sector=sector, amount_jpy=amount)


class TestDrawdownHalt:
    def test_halts_all_when_dd_exceeds(self) -> None:
        # 高値¥100k→現在¥85k＝-15% で新規停止
        res = evaluate_portfolio_guard(
            [_c("A", "Tech")], account_total_jpy=85_000.0, peak_total_jpy=100_000.0
        )
        assert res.halt_new is True
        assert all(v.action == "block" for v in res.verdicts)
        assert res.allowed() == []

    def test_no_halt_within_band(self) -> None:
        res = evaluate_portfolio_guard(
            [_c("A", "Tech")], account_total_jpy=90_000.0, peak_total_jpy=100_000.0
        )
        assert res.halt_new is False


class TestPositionCount:
    def test_blocks_beyond_max_positions(self) -> None:
        # 既に5銘柄保有 → 新規はblock（満員）
        held = [Held(f"H{i}", "Tech", 10_000.0) for i in range(5)]
        res = evaluate_portfolio_guard([_c("A", "Energy")], held=held, account_total_jpy=TOTAL)
        assert res.verdicts[0].action == "block"
        assert "同時保有上限" in res.verdicts[0].reason


class TestSectorConcentration:
    def test_blocks_third_in_same_sector(self) -> None:
        # 同一セクター3銘柄目はblock（上限2＝AI集中=1ベットを止める）
        cands = [_c("A", "AI"), _c("B", "AI"), _c("C", "AI")]
        res = evaluate_portfolio_guard(cands, account_total_jpy=TOTAL)
        actions = [v.action for v in res.verdicts]
        assert actions[0] == "allow"
        assert actions[1] == "allow"
        assert actions[2] == "block"
        assert "セクター" in res.verdicts[2].reason

    def test_reduces_when_sector_weight_exceeds_30pct(self) -> None:
        # セクター30%枠=¥30k。1銘柄¥25k許可後、2銘柄目¥25kは残¥5kへ縮小
        cands = [_c("A", "AI", 25_000.0), _c("B", "AI", 25_000.0)]
        res = evaluate_portfolio_guard(cands, account_total_jpy=TOTAL)
        assert res.verdicts[0].action == "allow"
        assert res.verdicts[1].action == "reduce"
        assert res.verdicts[1].amount_jpy == 5_000

    def test_different_sectors_both_allowed(self) -> None:
        cands = [_c("A", "AI"), _c("B", "Bank")]
        res = evaluate_portfolio_guard(cands, account_total_jpy=TOTAL)
        assert all(v.action == "allow" for v in res.verdicts)


class TestZeroSizing:
    def test_zero_amount_blocked(self) -> None:
        res = evaluate_portfolio_guard([_c("A", "Tech", 0.0)], account_total_jpy=TOTAL)
        assert res.verdicts[0].action == "block"
        assert "サイジング0" in res.verdicts[0].reason
