"""増額ゲート⑥の一意判定（official_gate_evaluation）。

「表示できる」と「ゲートで承認/拒否できる」は別。本モジュールは勝ち定義
**「ゲート⑥ + コスト後α>0」** を、公式データ部分集合に対して **まとめて判定**する単一関数を
提供する。report / 昇格 / UI はこの関数だけを参照する。

正本値: trading_agent/risk/params.py:RiskParams（gate_min_decisions / gate_max_drawdown /
gate_min_avg_r / gate_min_hit_rate）+ 勝ち定義の avg_net_excess>0（コスト後α）+ 両局面通過。

公式データ部分集合（official）の定義（A6・欺瞞防止）:
  - hit_or_miss が確定（hit/miss/neutral）かつ actual_return / stop_pct あり
  - **entry_market_regime あり**＝接続修正(A3)後の正規 fill 経路由来。entry_market_regime=NULL の
    旧 fill（legacy）は公式カウントから除外（新規からカウント方針）。

公式集合の識別（A7 実装済み・欺瞞防止）:
  - filled_via in ("ds_dispatch", "manual") のみを公式実績に数える＝DS A+ dispatch（paper）と
    実弾代行（live）だけ。notify の paper_auto sim は二重計上になるため除外（codex 条件③）。
  - entry_market_regime も必須（= A3/A8 後の正規 fill）。両条件で legacy / 非DS を確実に除外。
  - コスト後α（avg_net_excess）は benchmark_return のある行だけで平均されるため、benchmark 欠損が
    あると部分集合での誤通過になり得る。→ 本関数は **全 official に benchmark がある時だけ** コスト後α
    criterion を pass にする（benchmark_count == n を必須化）。
  - 最大 DD は評価列（actual_return を等ウェイトで積んだ擬似 equity）の DD proxy であり、実口座 DD
    ではない（criterion 名に明記）。実 equity DD は正式判定前に置換予定。

全てコード計算・LLM 非関与・コスト0。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.evaluation.metrics import EvalResult, build_track_record
from trading_agent.evaluation.job import _DEFAULT_ROUND_TRIP_COST_PCT
from trading_agent.evaluation.official_sources import OFFICIAL_FILL_SOURCES
from trading_agent.models.decisions import Decision
from trading_agent.risk.params import DEFAULT_RISK, RiskParams

# 市場局面マッピング（A8: trailing market_cycle bull/bear/sideways を主、A3 単日 risk_on/off は後方互換）。
# 両局面通過 = 逆境(bear/risk_off) と 順境(bull/risk_on) の双方で entry 実績があること。
# sideways / neutral / unknown はどちらにもカウントしない（厳しめ＝両局面の意味を保つ）。
_ADVERSE_REGIMES = {"bear", "risk_off"}
_FAVORABLE_REGIMES = {"bull", "risk_on"}

# A7/L3: 公式実績に数える約定経路は evaluation/official_sources に集約。
# DS 公式 dispatch（paper）と実弾代行（live）のみ。notify の paper_auto sim は二重計上に
# なるため公式集合から除外（codex 条件③）。除外理由の詳細は official_sources.py の docstring。
_OFFICIAL_SOURCES = OFFICIAL_FILL_SOURCES


@dataclass
class GateCriterion:
    name: str
    value: float | int | str | None
    threshold: str
    passed: bool


@dataclass
class GateResult:
    passed: bool
    n: int
    criteria: list[GateCriterion] = field(default_factory=list)
    regimes_present: list[str] = field(default_factory=list)
    max_drawdown: float = 0.0
    notes: list[str] = field(default_factory=list)
    # broker_mode 分離（paper=システム edge 検証 / live=実運用実績）。
    broker_mode: str | None = None
    # actionable=False は combined（paper+live 混在）の参考値。増額根拠にしてはいけない。
    actionable: bool = True

    def summary(self) -> str:
        scope = (
            f"{self.broker_mode}" if self.broker_mode else "—"
        )
        if not self.actionable:
            head = "📊 ゲート⑥ 参考(combined・増額不可)"
        else:
            head = "✅ 増額ゲート⑥ 通過" if self.passed else "⛔ 増額ゲート⑥ 未通過"
        lines = [f"{head}（{scope} / 公式評価 n={self.n}）"]
        for c in self.criteria:
            mark = "✓" if c.passed else "✗"
            lines.append(f"  {mark} {c.name}: {c.value}（要件 {c.threshold}）")
        if not self.actionable:
            lines.append("  ⚠ combined は paper(edge検証)と live(実運用)の混在＝参考のみ。増額判断には使わない。")
        if self.notes:
            lines.append("  注記: " + " / ".join(self.notes))
        return "\n".join(lines)


def _max_drawdown(returns_in_order: list[float]) -> float:
    """評価日順の realized リターン列から最大ドローダウンを算出（正値・0〜1）。

    各 decision を等ウェイトで時系列に積んだ擬似 equity curve のピーク→トラフ最大下落率。
    サンプルが無ければ 0。
    """
    if not returns_in_order:
        return 0.0
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns_in_order:
        equity *= 1.0 + r
        peak = max(peak, equity)
        if peak > 0:
            dd = (peak - equity) / peak
            max_dd = max(max_dd, dd)
    return max_dd


def _query_official_rows(
    engine: Engine, *, broker_mode: str | None
) -> list[Decision]:
    """公式集合の生クエリ。broker_mode を渡すと entry_broker_mode で絞る（None=combined）。

    公式条件: hit/miss/neutral 確定 ∧ entry_market_regime あり ∧
    filled_via in (ds_dispatch, manual)。broker_mode 分離で paper/live を別集計する。
    """
    with Session(engine) as session:
        stmt = (
            select(Decision)
            .where(col(Decision.hit_or_miss).in_(("hit", "miss", "neutral")))
            .where(col(Decision.entry_market_regime).is_not(None))
            .where(col(Decision.filled_via).in_(_OFFICIAL_SOURCES))  # A7: DS 公式由来のみ
        )
        if broker_mode is not None:
            stmt = stmt.where(col(Decision.entry_broker_mode) == broker_mode)
        rows = list(session.exec(stmt.order_by(col(Decision.evaluated_at))).all())
    return [d for d in rows if d.actual_return is not None and d.stop_pct]


def official_gate_evaluation(
    engine: Engine,
    *,
    broker_mode: str,
    params: RiskParams = DEFAULT_RISK,
    cost_pct: float = _DEFAULT_ROUND_TRIP_COST_PCT,
) -> GateResult:
    """勝ち定義をまとめて判定する単一関数（増額の可否はこれだけを見る）。

    **broker_mode 必須**（codex 指摘）: paper（システム edge 検証）と live（実運用実績）を
    必ず分離して判定する。filled_via=manual は live 専用ではない（paper/manual もある）ため、
    filled_via だけでは混在汚染する。combined は combined_gate_reference（参考・増額不可）で。

    判定（全て満たして初めて passed=True）:
      1. n ≥ gate_min_decisions
      2. hit_rate ≥ gate_min_hit_rate
      3. avg_r ≥ gate_min_avg_r
      4. max_drawdown ≤ gate_max_drawdown
      5. avg_net_excess（コスト後α）> 0
      6. 両局面通過（順境・逆境の両方で entry 実績がある）
    """
    if broker_mode not in ("paper", "live"):
        raise ValueError(f"broker_mode must be 'paper' or 'live', got {broker_mode!r}")
    official = _query_official_rows(engine, broker_mode=broker_mode)
    return _evaluate_official(
        official, params=params, cost_pct=cost_pct,
        broker_mode=broker_mode, actionable=True,
    )


def combined_gate_reference(
    engine: Engine,
    *,
    params: RiskParams = DEFAULT_RISK,
    cost_pct: float = _DEFAULT_ROUND_TRIP_COST_PCT,
) -> GateResult:
    """paper+live 混在の参考集計（actionable=False＝増額根拠にしない・codex 指摘）。

    表示用途のみ。役割の違う paper(edge検証) と live(実運用) を混ぜているので、
    この passed が True でも増額判断には使ってはいけない。
    """
    official = _query_official_rows(engine, broker_mode=None)
    return _evaluate_official(
        official, params=params, cost_pct=cost_pct,
        broker_mode="combined", actionable=False,
    )


def _evaluate_official(
    official: list[Decision],
    *,
    params: RiskParams,
    cost_pct: float,
    broker_mode: str,
    actionable: bool,
) -> GateResult:
    """公式集合（既に broker_mode で絞り済み）から GateResult を計算する共通コア。"""
    results: list[EvalResult] = [
        EvalResult(
            actual_return=d.actual_return,
            r_multiple=d.actual_return / d.stop_pct,
            outcome=d.hit_or_miss,
            benchmark_return=d.benchmark_return,
            cost_pct=cost_pct,
        )
        for d in official
    ]
    tr = build_track_record(results)
    from collections import Counter
    regime_counts = Counter(d.entry_market_regime for d in official if d.entry_market_regime)
    regimes_present = sorted(regime_counts)
    has_adverse = any(r in _ADVERSE_REGIMES for r in regimes_present)
    has_favorable = any(r in _FAVORABLE_REGIMES for r in regimes_present)
    both_regimes = has_adverse and has_favorable
    max_dd = _max_drawdown([d.actual_return for d in official])

    avg_net_excess = tr.avg_net_excess
    hit_rate = tr.hit_rate if tr.hit_rate is not None else 0.0
    # コスト後α は全 official に benchmark がある時だけ pass（部分欠損での誤通過防止・codex 指摘1）。
    benchmark_count = sum(1 for d in official if d.benchmark_return is not None)
    benchmark_complete = tr.n > 0 and benchmark_count == tr.n
    alpha_ok = benchmark_complete and avg_net_excess is not None and avg_net_excess > 0
    alpha_value = (
        f"{avg_net_excess:+.2%}" if avg_net_excess is not None else "—"
    ) + f"（benchmark {benchmark_count}/{tr.n}）"

    criteria = [
        GateCriterion(
            "評価件数", tr.n, f"≥ {params.gate_min_decisions}", tr.n >= params.gate_min_decisions
        ),
        GateCriterion(
            "命中率", f"{hit_rate:.0%}" if tr.hit_rate is not None else "—",
            f"≥ {params.gate_min_hit_rate:.0%}",
            tr.hit_rate is not None and tr.hit_rate >= params.gate_min_hit_rate,
        ),
        GateCriterion(
            "平均R", f"{tr.avg_r:+.2f}", f"≥ {params.gate_min_avg_r:+.2f}",
            tr.avg_r >= params.gate_min_avg_r,
        ),
        GateCriterion(
            "最大DD(評価列proxy)", f"{max_dd:.1%}", f"≤ {params.gate_max_drawdown:.0%}",
            max_dd <= params.gate_max_drawdown,
        ),
        GateCriterion(
            "コスト後α", alpha_value, "> 0 かつ全件 benchmark 有", alpha_ok,
        ),
        GateCriterion(
            "両局面通過", "/".join(regimes_present) or "なし",
            "順境+逆境 両方", both_regimes,
        ),
    ]
    notes: list[str] = []
    if tr.n > 0 and not benchmark_complete:
        notes.append(
            f"benchmark 欠損 {tr.n - benchmark_count}/{tr.n} 件 → コスト後α は部分集合になるため"
            "criterion は fail 固定（誤通過防止）"
        )
    if regime_counts:
        # 局面内訳を出して「なぜ両局面未達か」を隠さない（codex 提案・欺瞞防止）
        breakdown = " ".join(f"{k}:{v}" for k, v in regime_counts.most_common())
        notes.append(f"局面内訳 [{breakdown}]")
        neutral_n = sum(
            v for k, v in regime_counts.items()
            if k not in _ADVERSE_REGIMES and k not in _FAVORABLE_REGIMES
        )
        if neutral_n:
            notes.append(
                f"うち {neutral_n} 件は sideways/unknown/neutral（両局面のカウント対象外）"
            )
    if regimes_present and not both_regimes:
        miss = "逆境(bear/risk_off)" if not has_adverse else "順境(bull/risk_on)"
        notes.append(f"両局面未達: {miss} の entry 実績がまだ無い")
    notes.append("最大DD は評価列の擬似 equity proxy（実口座 DD ではない・正式判定前に置換予定）")
    via_counts = Counter(d.filled_via for d in official if d.filled_via)
    if via_counts:
        notes.append("約定経路 [" + " ".join(f"{k}:{v}" for k, v in via_counts.most_common()) + "]（公式=ds_dispatch/manual のみ）")

    passed = all(c.passed for c in criteria)
    return GateResult(
        passed=passed,
        n=tr.n,
        criteria=criteria,
        regimes_present=regimes_present,
        max_drawdown=max_dd,
        notes=notes,
        broker_mode=broker_mode,
        actionable=actionable,
    )
