"""L3 substrate: 公式約定ソース集約の回帰テスト。LLM/ネット非依存。

観測台帳の「公式集合」判定 (forward_diagnosis / feedback / gate) が単一定義
(evaluation/official_sources.OFFICIAL_FILL_SOURCES) を共有することと、非公式
filled_via ラベルが 3 経路すべての公式集合から漏れることを固定する。

目的（master/codex L3）:
- 重複定義の再発防止（3 箇所が同一オブジェクトを import）。
- 値の不変ガード（('ds_dispatch','manual')）。
- paper_auto が意図的に非公式である記録（二重計上回避・codex 条件③）。
- 新 fill 経路を足したら OFFICIAL_FILL_SOURCES とこのテストを同時更新する規約のロック
  （非公式ラベルが silently drop されることを可視化する）。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.evaluation.gate import official_gate_evaluation
from trading_agent.evaluation.official_sources import OFFICIAL_FILL_SOURCES
from trading_agent.models.decisions import Decision
from trading_agent.reporting.feedback import collect_feedback_records
from trading_agent.reporting.forward_diagnosis import compute_forward_diagnosis
from trading_agent.utils.time_utils import utcnow


def _engine(tmp_path: Path):
    eng = get_engine(tmp_path / "official.sqlite")
    create_all(eng)
    return eng


def _add_full_evaluated(eng, *, ticker: str, filled_via: str) -> None:
    """forward/feedback/gate すべての公式集合条件を満たす評価済 Decision。

    filled_via のみ可変。entry_date/evaluation_date(過去)/regime/actual_return/stop/
    entry_broker_mode を全て埋める。
    """
    entry = dt.date(2026, 1, 1)
    with Session(eng, expire_on_commit=False) as s:
        s.add(
            Decision(
                date=entry, ticker=ticker, action="buy", status="filled",
                entry_price=1000.0, stop_pct=0.10, expected_return=0.20,
                actual_return=0.15, benchmark_return=0.0, hit_or_miss="hit",
                evaluated_at=utcnow(),
                entry_date=entry,
                evaluation_date=dt.date(2026, 4, 1),  # 到来済
                entry_market_regime="bull",
                filled_via=filled_via,
                entry_broker_mode="paper",
            )
        )
        s.commit()


class TestValueAndConsolidation:
    def test_value_unchanged(self) -> None:
        # 値は不変（変更は意図的レビューを要する）。
        assert OFFICIAL_FILL_SOURCES == ("ds_dispatch", "manual")

    def test_paper_auto_excluded_by_design(self) -> None:
        # paper_auto は二重計上回避のため意図的に非公式（codex 条件③）。
        assert "paper_auto" not in OFFICIAL_FILL_SOURCES

    def test_all_modules_share_single_source(self) -> None:
        # L3: forward/feedback/gate/phase_c_status が同一定義を import（重複定義の再発防止）。
        import scripts.phase_c_status as pc
        import trading_agent.evaluation.gate as gt
        import trading_agent.reporting.feedback as fb
        import trading_agent.reporting.forward_diagnosis as fd

        assert fd._OFFICIAL_SOURCES is OFFICIAL_FILL_SOURCES
        assert fb._OFFICIAL_SOURCES is OFFICIAL_FILL_SOURCES
        assert gt._OFFICIAL_SOURCES is OFFICIAL_FILL_SOURCES
        assert pc._OFFICIAL_SOURCES is OFFICIAL_FILL_SOURCES


class TestNonOfficialLabelDroppedEverywhere:
    """非公式 filled_via は forward/feedback/gate の全公式集合から漏れる。

    新 fill 経路（未登録ラベル）を足して OFFICIAL_FILL_SOURCES を更新し忘れると、その
    実績が観測台帳から silently drop されることを示す＝同時更新の規約をロックする。
    """

    def test_official_included_nonofficial_dropped(self, tmp_path: Path) -> None:
        eng = _engine(tmp_path)
        _add_full_evaluated(eng, ticker="1111", filled_via="ds_dispatch")   # 公式
        _add_full_evaluated(eng, ticker="2222", filled_via="new_path_v2")   # 非公式（未登録の新経路想定）

        # gate⑥ 公式集合: ds_dispatch のみ
        gate = official_gate_evaluation(eng, broker_mode="paper")
        assert gate.n == 1

        # feedback official_only: ds_dispatch のみ
        recs = collect_feedback_records(eng, official_only=True, broker_mode="paper")
        assert len(recs) == 1
        assert all(r["filled_via"] == "ds_dispatch" for r in recs)

        # forward_diagnosis: ds_dispatch のみ
        def fetcher(tickers, _start):
            return {t: [1000.0, 1100.0, 1200.0] for t in tickers}

        fwd = compute_forward_diagnosis(
            eng, series_fetcher=fetcher, horizons=(2,), broker_mode="paper"
        )
        assert fwd["decisions_n"] == 1
