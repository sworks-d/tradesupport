"""DS Scout の単体テスト。A+ の KAWORU 軽量 technicals 経路（brief 非依存選定）中心。"""

from __future__ import annotations

from trading_agent.portfolio.ds_scout import (
    _compute_kaworu_short_term_confidence,
    select_kaworu_contrarian,
)
from trading_agent.portfolio.misato import CandidatePool


def _cand(ticker: str, *, horizon: int = 21, score: float = 0.5) -> CandidatePool:
    return CandidatePool(
        decision_id=1, ticker=ticker, gendo_stance="静観", source="zeele",
        preset="contrarian", target_period_days=horizon, stop_pct=0.05,
        real_decision=None, score=score,
    )


class TestKaworuConfidenceValueBased:
    def test_rsi_sweetspot_plus_horizon(self) -> None:
        # RSI 55（スイートスポット 0.30）+ 短期 horizon（KAWORU 21d 適性）。業界/ニュース 0。
        c = _cand("7203", horizon=21)
        conf = _compute_kaworu_short_term_confidence(c, rsi=55.0)
        assert conf >= 0.35  # 選定閾値を超える（RSI+horizon だけで）

    def test_industry_news_not_used_in_value_path(self) -> None:
        # 値経路では industry_s/news_s を渡しても、明示しなければ 0 のまま（選定に効かない）
        c = _cand("7203", horizon=21)
        only_rsi = _compute_kaworu_short_term_confidence(c, rsi=55.0)
        with_extra = _compute_kaworu_short_term_confidence(
            c, rsi=55.0, industry_s=0.5, news_s=0.5
        )
        assert with_extra > only_rsi  # 明示で渡せば加点される（priority 段で使う用）


class TestSelectKaworuTechnicalsPath:
    def test_technicals_path_selects_on_rsi(self) -> None:
        # technicals_lookup を渡すと brief 非依存で RSI 選定
        pool = [_cand("7203", horizon=21), _cand("6758", horizon=21)]
        tech = {"7203": {"rsi": 55.0}, "6758": {"rsi": 90.0}}  # 6758 は過熱で加点なし
        props = select_kaworu_contrarian(
            pool, excluded_tickers=set(), technicals_lookup=tech,
            min_budgets={"7203": 1000.0, "6758": 1000.0},
        )
        picked = {p.ticker for p in props}
        assert "7203" in picked  # RSI スイートスポット
        # reason に RSI が入る
        assert any("RSI=55" in p.reason for p in props)

    def test_excluded_tickers_skipped(self) -> None:
        pool = [_cand("7203"), _cand("6758")]
        tech = {"7203": {"rsi": 55.0}, "6758": {"rsi": 55.0}}
        props = select_kaworu_contrarian(
            pool, excluded_tickers={"7203"}, technicals_lookup=tech,
            min_budgets={"6758": 1000.0},
        )
        assert all(p.ticker != "7203" for p in props)
