"""MISATO オーケストレーター：ダミーシステムの司令塔。

責務（4つ）:
1. **予算配分**: 人間から受領した総予算を 4 機（REI/ASUKA/SHINJI/KAWORU）に振り分ける。
   既定は均等 25%×4。評価データが ≥10 件 揃った機体は hit_rate×avg_R で重み付けに切り替え。
2. **銘柄→パイロット割り当て**: 候補 decision を性格ルール（horizon/ボラ/stance/合議）で
   最適パイロットに decisive に振り分ける（ロジックは決定論・LLM なし）。
3. **安全装置**: HALT ファイル `~/.trading-agent/HALT` で全停止。dry-run 既定。
   人間が `approve=True` を渡した時だけ paper_fill_approved を呼ぶ。
4. **昇格判定**: 各機の評価データ ≥30 件 ∩ hit_rate ≥50% ∩ avg_R ≥+0.5 で昇格候補化。
   UI に提示するのみで、実弾 moomoo 発注は人間が手動で行う（D-23 守り原則）。

CLI: `scripts/misato_dispatch.py --budget 100000 [--approve] [--personality REI]`
UI: ダッシュボード「価格を更新」ボタン → /api/refresh → dispatch(dry_run=True) → snapshot 反映
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from collections.abc import Callable

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.decisions import Decision
from trading_agent.models.misato_treasury import MisatoTreasury, PilotAllocation
from trading_agent.models.portfolio import Portfolio
from trading_agent.portfolio.personality import (
    PERSONALITIES,
    all_personalities,
)
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import utcnow

PriceLookup = Callable[[str], float | None]
IsJpLookup = Callable[[str], bool]

_log = get_logger("misato")

# 安全装置の固定値（.env から override 可能にする余地は残す）
DEFAULT_HALT_FILE = Path("~/.trading-agent/HALT").expanduser()
MAX_BUDGET_PER_DISPATCH_JPY = 500_000   # 1 命令で振れる総額上限（暴走防止）
MAX_BUDGET_PER_PILOT_JPY = 200_000      # 1 機あたり上限（集中防止）
MIN_BUDGET_PER_PILOT_JPY = 5_000        # 配分対象から外す下限
# v2.6: 1 銘柄あたり最低投入額を ¥10k → ¥5k に（より多くの銘柄に分散・分散投資）
import os as _os_min_invest
MIN_INVEST_PER_DECISION_JPY = int(
    _os_min_invest.environ.get("MISATO_MIN_INVEST_JPY", "5000")
)


@dataclass(frozen=True)
class Assignment:
    """1 件の decision を 1 機のパイロットに割り当てた結果。"""

    decision_id: int
    ticker: str
    gendo_stance: str
    assigned_to: str         # personality.name（REI 等）
    reason: str              # 割り当て理由（horizon / vol / consensus / stance）
    proposed_budget_jpy: float  # この decision に MISATO が当てる予算（参考）
    source: str = "magi"     # "magi" / "zeele" / "both"（ダブル推奨）
    preset: str | None = None  # ZEELE 由来時の preset（value/momentum 等）
    boost: float = 1.0       # 配分時のウェイト（"both" は 1.5x）
    score: float = 0.0       # 確度スコア（高いほど優先）
    picked: bool = False     # True なら実 fill 対象 / False なら shortlist（待機）


@dataclass(frozen=True)
class BudgetAllocation:
    """4 機への予算配分（需要ベース）。

    mode は配分の根拠:
      - "needs-based": 候補がいる機にだけ配分（既定）
      - "needs-weighted": 候補数 × 実績重みで配分
      - "fallback-equal": 候補ゼロ時の均等保留
    """

    per_pilot_jpy: dict[str, float]   # {"REI": 25000.0, ...}
    weighted: bool                    # True = 実績重み付け、False = 均等
    total_jpy: float
    reason: str
    mode: str = "needs-based"
    per_pilot_demand: dict[str, int] = field(default_factory=dict)  # 各機の候補件数


@dataclass(frozen=True)
class PromotionCandidate:
    """昇格推奨機体。"""

    personality: str
    n: int
    hit_rate: float
    avg_r: float
    note: str


@dataclass
class DispatchPlan:
    """1 回の dispatch の全結果（dry-run 既定）。"""

    halted: bool = False
    halt_reason: str = ""
    total_budget_jpy: float = 0.0
    allocation: BudgetAllocation | None = None
    assignments: list[Assignment] = field(default_factory=list)
    promotions: list[PromotionCandidate] = field(default_factory=list)
    executed: bool = False                # approve=True で fill 実行されたか
    fills: list[dict[str, Any]] = field(default_factory=list)  # paper_fill 結果
    generated_at: str = ""


# ----- HALT -----

def check_halt(halt_file: Path | None = None) -> tuple[bool, str]:
    """HALT ファイルの存在を確認。True = 停止中。"""
    path = halt_file or DEFAULT_HALT_FILE
    if path.exists():
        try:
            content = path.read_text(encoding="utf-8").strip()[:200]
        except Exception:
            content = "(read failed)"
        return True, f"HALT file present: {path} | {content}"
    return False, ""


# ----- 予算配分 -----

def _pilot_performance(engine: Engine) -> dict[str, dict[str, float]]:
    """各機体の (n, hit_rate, avg_r) を Decision から集計。

    評価期日到来済の decision を personalities_filled で展開し性格別に集計。
    feedback.collect_feedback_records と意味同等の軽量版（重複ロジックを避けるため再利用も可）。
    """
    from trading_agent.reporting.feedback import (
        collect_feedback_records,
        summarize_feedback,
    )

    records = collect_feedback_records(engine)
    s = summarize_feedback(records)
    return s.get("by_personality", {})


def allocate_budget(
    total_jpy: float,
    *,
    engine: Engine | None = None,
    demand_by_pilot: dict[str, int] | None = None,
    weighted_demand_by_pilot: dict[str, float] | None = None,
    weighted_threshold_n: int = 10,
) -> BudgetAllocation:
    """**需要ベース**で総予算を 4 機に配分する。

    候補（demand_by_pilot）がいる機にだけ予算を寄せる。候補ゼロの機は配分 0。
    候補が拮抗する時は、評価データが揃ってる機（hit_rate × avg_R）に重みを乗せる。

    Args:
        total_jpy: MISATO が振れる総予算（>0）
        engine: 評価実績の重み付け参照用（None なら均等）
        demand_by_pilot: 各機の候補件数 {"REI": 0, "ASUKA": 2, ...}。
            None の時は均等フォールバック（互換用）。

    挙動例:
      - 候補が ASUKA に 2 件・他 0 件 → ASUKA に 100% 配分（他 0）
      - 候補が ASUKA 2, KAWORU 1 → ASUKA に 2/3, KAWORU に 1/3（実績重みあれば微調整）
      - 全機候補ゼロ → ¥0 配分（未配分残のまま）
    """
    pilots = [p.name for p in all_personalities()]
    capped_total = min(total_jpy, MAX_BUDGET_PER_DISPATCH_JPY)

    # demand_by_pilot 未指定（過去互換）→ 均等フォールバック
    if demand_by_pilot is None:
        equal = capped_total / len(pilots)
        return BudgetAllocation(
            per_pilot_jpy={p: equal for p in pilots},
            weighted=False,
            total_jpy=capped_total,
            reason="demand 未指定のため均等フォールバック",
            mode="fallback-equal",
            per_pilot_demand={p: 0 for p in pilots},
        )

    total_demand = sum(demand_by_pilot.get(p, 0) for p in pilots)
    # weighted_demand があれば優先（ダブル推奨 1.5x 反映用）
    effective_demand: dict[str, float] = {
        p: float(weighted_demand_by_pilot.get(p, demand_by_pilot.get(p, 0)))
           if weighted_demand_by_pilot is not None
           else float(demand_by_pilot.get(p, 0))
        for p in pilots
    }
    total_effective = sum(effective_demand.values())

    # 候補ゼロ → 0 配分（MISATO 全額未配分）
    if total_demand == 0:
        return BudgetAllocation(
            per_pilot_jpy={p: 0.0 for p in pilots},
            weighted=False,
            total_jpy=capped_total,
            reason="候補ゼロ：MISATO 全額を未配分のまま保留",
            mode="needs-based",
            per_pilot_demand=dict(demand_by_pilot),
        )

    # 実績重み付けを使うか判定
    perf = _pilot_performance(engine) if engine is not None else {}
    use_weighted = any(
        (perf.get(p, {}).get("n", 0) or 0) >= weighted_threshold_n for p in pilots
    )
    # v2.1 TASK-P4: 評価データの実態を reason に必ず含める
    n_total_data = sum(int(perf.get(p, {}).get("n", 0) or 0) for p in pilots)
    data_status = f"評価データ {n_total_data}/{weighted_threshold_n} 件・"

    if not use_weighted:
        # 需要件数比（boost 反映）で配分（候補ゼロ機は 0）
        denom = total_effective or 1.0
        per_pilot = {
            p: capped_total * (effective_demand[p] / denom)
            for p in pilots
        }
        mode = "needs-based"
        has_boost = weighted_demand_by_pilot is not None and any(
            effective_demand[p] != float(demand_by_pilot.get(p, 0)) for p in pilots
        )
        reason = (
            f"{data_status}実績ウェイト未発動・候補件数比で配分"
            + ("（ダブル推奨 1.5x ブースト適用）" if has_boost else "")
        )
    else:
        # 候補数（boost 反映）× 実績重み（hit_rate × avg_R）で配分
        raw_weights: dict[str, float] = {}
        for p in pilots:
            demand = effective_demand[p]
            if demand == 0:
                raw_weights[p] = 0.0
                continue
            d = perf.get(p, {})
            n = d.get("n", 0)
            if n < weighted_threshold_n:
                raw_weights[p] = float(demand)
                continue
            hr = max(d.get("hit_rate", 0.0), 0.0)
            rr = max(d.get("avg_r", 0.0), 0.1)
            raw_weights[p] = float(demand) * (hr * rr or 0.1)
        s = sum(raw_weights.values()) or 1.0
        per_pilot = {p: capped_total * (raw_weights[p] / s) for p in pilots}
        mode = "needs-weighted"
        reason = f"{data_status}候補件数 × 評価実績 (hit_rate × avg_R) で配分"

    # 1 機上限クランプ・余りは候補のいる他機に均等で戻す
    overflow = 0.0
    for p in pilots:
        if per_pilot[p] > MAX_BUDGET_PER_PILOT_JPY:
            overflow += per_pilot[p] - MAX_BUDGET_PER_PILOT_JPY
            per_pilot[p] = MAX_BUDGET_PER_PILOT_JPY
    if overflow > 0:
        recipients = [p for p in pilots if demand_by_pilot.get(p, 0) > 0 and per_pilot[p] < MAX_BUDGET_PER_PILOT_JPY]
        if recipients:
            share = overflow / len(recipients)
            for p in recipients:
                per_pilot[p] = min(per_pilot[p] + share, MAX_BUDGET_PER_PILOT_JPY)

    return BudgetAllocation(
        per_pilot_jpy=per_pilot,
        weighted=use_weighted,
        total_jpy=capped_total,
        reason=reason,
        mode=mode,
        per_pilot_demand=dict(demand_by_pilot),
    )


# ----- 銘柄 → パイロット割り当て -----

def _is_consensus(engine: Engine, ticker: str) -> bool:
    """REI/ASUKA/SHINJI が全員保有中の ticker か（KAWORU の合議トリガー）。"""
    from trading_agent.portfolio.paper_exec import _consensus_tickers

    return ticker in _consensus_tickers(engine, peers=("REI", "ASUKA", "SHINJI"))


def _already_held(engine: Engine, personality: str, ticker: str) -> bool:
    with Session(engine) as s:
        existing = s.exec(
            select(Portfolio)
            .where(col(Portfolio.personality) == personality)
            .where(col(Portfolio.ticker) == ticker)
            .where(col(Portfolio.status) == "active")
        ).first()
    return existing is not None


def _classify_horizon(target_period_days: int | None) -> str:
    """target_period_days を short/mid/long に分類。"""
    if target_period_days is None:
        return "mid"
    if target_period_days <= 30:
        return "short"
    if target_period_days <= 90:
        return "mid"
    return "long"


def assign_to_pilot(
    decision: Decision | "CandidatePool",
    *,
    engine: Engine,
) -> tuple[str, str]:
    """1 件の候補（MAGI decision or ZEELE プール）を最適パイロットに割り当てる。

    優先順位（上から順に適用・最初に当てはまった機が assignee）:
      0. ZEELE preset がある (= ZEELE 由来 or "both") → preset × 性格マッピング優先
      1. KAWORU 合議: REI/ASUKA/SHINJI 全員保有銘柄なら KAWORU 必須
      2. stance「静観」: 採用するのは KAWORU のみ
      3. horizon long (>90日): REI（守り長期）
      4. horizon short (≤30日) + stop_pct ≥0.10（高ボラ）: KAWORU
      5. horizon short (≤30日) + stop_pct <0.10（中ボラ）: ASUKA
      6. horizon mid + stop_pct ≥0.10: ASUKA
      7. horizon mid + stop_pct <0.10: SHINJI
      8. fallback: SHINJI

    既存ポジ重複時は同点機をスキップして tie-break。
    """
    # 共通プロパティ抽出（Decision でも CandidatePool でも動く）
    ticker = decision.ticker
    stance = getattr(decision, "gendo_stance", "") or ""
    horizon = _classify_horizon(getattr(decision, "target_period_days", None))
    stop_pct = float(getattr(decision, "stop_pct", None) or 0.10)
    preset = getattr(decision, "preset", None)

    # 0. ZEELE preset がある銘柄は preset × 性格マッピング優先
    if preset and preset in ZEELE_PRESET_TO_PILOT:
        pilot = ZEELE_PRESET_TO_PILOT[preset]
        return pilot, f"ZEELE preset={preset} → {pilot} (preset×性格マッピング)"

    # 1. 合議銘柄は KAWORU 優先
    if _is_consensus(engine, ticker):
        return "KAWORU", "consensus: REI/ASUKA/SHINJI 全員保有"

    # 2. 静観は KAWORU 専用
    if stance == "静観":
        return "KAWORU", "stance=静観 → KAWORU のみ採用"

    # 3-7. horizon × volatility で決定論的に振り分け
    candidate_order: list[tuple[str, str]] = []
    if horizon == "long":
        candidate_order = [("REI", "long-horizon (>90d) → REI 守り長期")]
    elif horizon == "short" and stop_pct >= 0.10:
        candidate_order = [
            ("KAWORU", "short-horizon (≤30d) × 高ボラ → KAWORU 極タイト stop"),
            ("ASUKA", "fallback: short + 高ボラ"),
        ]
    elif horizon == "short":
        candidate_order = [
            ("ASUKA", "short-horizon (≤30d) × 中ボラ → ASUKA 攻め"),
            ("KAWORU", "fallback: short"),
        ]
    elif horizon == "mid" and stop_pct >= 0.10:
        candidate_order = [
            ("ASUKA", "mid-horizon × 高ボラ → ASUKA 中期攻め"),
            ("SHINJI", "fallback: mid"),
        ]
    else:
        candidate_order = [
            ("SHINJI", "mid-horizon × 中ボラ → SHINJI 中庸"),
            ("ASUKA", "fallback: mid"),
        ]

    # tie-break: 既存ポジ重複は避ける
    for cand, reason in candidate_order:
        if not _already_held(engine, cand, ticker):
            return cand, reason
    # 全機が既保有 → 先頭を採用（重複承知）
    cand, reason = candidate_order[0]
    return cand, f"{reason}（重複承知）"


def _pending_decisions(engine: Engine) -> list[Decision]:
    """auto-approve 候補：status='awaiting' or 'approved' の buy で stance != 不明。"""
    with Session(engine) as s:
        rows = s.exec(
            select(Decision)
            .where(col(Decision.action) == "buy")
            .where(col(Decision.status).in_(("awaiting", "approved")))
        ).all()
    return [d for d in rows if d.id is not None and d.gendo_stance]


# ----- ZEELE 取り込み -----

# v2.1 TASK-P2: score 正規化（MAGI 0-1 と ZEELE reference_score (0-100) を共通レンジへ）
def _normalize_score(raw: float, kind: str) -> float:
    """全 score を 0.0〜1.0 に正規化（kind ごとに別計算）。"""
    if raw is None:
        return 0.0
    if kind == "magi":
        # MAGI Decision.score は 0.0〜1.0 想定（外れた値は clip）
        return min(max(float(raw), 0.0), 1.0)
    if kind == "zeele":
        # ZEELE reference_score は 0〜100 (composite_score)。これを 0〜1 に。
        return min(max(float(raw) / 100.0, 0.0), 1.0)
    return 0.5


# ZEELE プールから MISATO に流す上位件数（厳選）。
# プール自体（ZeelePanel 表示）は不変、ここで上澄みだけ司令層に渡す。
# reference_score 降順 → weeks_in_zeele 降順 で並べた先頭 N 件。
ZEELE_TOP_N_FOR_MISATO = 5


# ZEELE preset → 機マッピング（preset × 性格）
ZEELE_PRESET_TO_PILOT: dict[str, str] = {
    # 守り長期 → REI
    "value": "REI",
    "dividend": "REI",
    # 攻め中期 → ASUKA
    "momentum": "ASUKA",
    "growth": "ASUKA",
    # 中庸 → SHINJI
    "pullback": "SHINJI",
    "contrarian": "SHINJI",
    # いいとこどり短期 → KAWORU
    "alpha": "KAWORU",
    "growth-value": "KAWORU",
}


@dataclass(frozen=True)
class CandidatePool:
    """MISATO に流れる候補の統合 pool（MAGI + ZEELE）。"""

    decision_id: int          # MAGI 由来: Decision.id / ZEELE 由来: -1000 - state.ticker hash
    ticker: str
    gendo_stance: str         # MAGI 由来 / ZEELE は "ZEELE" 固定
    source: str               # "magi" / "zeele" / "both"
    preset: str | None        # ZEELE 由来時のみ
    target_period_days: int | None
    stop_pct: float
    real_decision: Decision | None  # status 更新の対象（ZEELE 由来は None＝書き換えない）
    score: float = 0.0        # 確度スコア（ZEELE reference_score・MAGI は決定スコア・both は加点）


def _build_candidate_pool(
    engine: Engine, *, zeele_top_n: int = ZEELE_TOP_N_FOR_MISATO
) -> list[CandidatePool]:
    """MAGI awaiting + ZEELE 上澄み N 件 を統合した候補 pool を返す。

    - MAGI awaiting: 全件取り込み
    - ZEELE: reference_score 降順 + weeks_in_zeele 降順 で **上位 zeele_top_n 件のみ**
      （プール全体（ZeelePanel 表示）は不変。MISATO に流すのは上澄みだけ）
    - 同一 ticker は source="both" にマージ（ダブル推奨）
    - 各候補に確度 score を付与（実 fill 選別で使う）
    """
    from trading_agent.models.zeele import ZeeleState

    # MAGI 由来（score は Decision.score 優先、無ければ 0.5 既定）
    magi_decisions = _pending_decisions(engine)
    pool_by_ticker: dict[str, CandidatePool] = {}
    for d in magi_decisions:
        if d.id is None:
            continue
        pool_by_ticker[d.ticker] = CandidatePool(
            decision_id=d.id,
            ticker=d.ticker,
            gendo_stance=d.gendo_stance or "—",
            source="magi",
            preset=None,
            target_period_days=d.target_period_days,
            stop_pct=float(d.stop_pct or 0.10),
            real_decision=d,
            # v2.1 TASK-P2: 0-1 に正規化（fallback も 0.5 のまま）
            score=_normalize_score(d.score, "magi") if d.score is not None else 0.5,
        )

    # ZEELE 由来：reference_score 降順 + weeks_in_zeele 降順で上位 N 件
    with Session(engine) as s:
        zeele_rows = s.exec(
            select(ZeeleState)
            .where(col(ZeeleState.is_active))
            .order_by(col(ZeeleState.reference_score).desc())
            .order_by(col(ZeeleState.weeks_in_zeele).desc())
            .limit(zeele_top_n)
        ).all()

    for z in zeele_rows:
        # v2.1 TASK-P2: ZEELE reference_score も 0-1 に正規化
        z_score_norm = _normalize_score(z.reference_score or 0.0, "zeele")
        if z.ticker in pool_by_ticker:
            # MAGI と重複 → "both"（ダブル推奨）にマージ。両方を 0-1 で平均してボーナス +0.1。
            existing = pool_by_ticker[z.ticker]
            both_score = min(1.0, (existing.score + z_score_norm) / 2.0 + 0.1)
            pool_by_ticker[z.ticker] = CandidatePool(
                decision_id=existing.decision_id,
                ticker=existing.ticker,
                gendo_stance=existing.gendo_stance,
                source="both",
                preset=z.preset or None,
                target_period_days=existing.target_period_days,
                stop_pct=existing.stop_pct,
                real_decision=existing.real_decision,
                score=both_score,
            )
        else:
            virtual_id = -(10000 + abs(hash(z.ticker)) % 90000)
            horizon = (
                30 if (z.preset or "") in ("momentum", "alpha") else
                60 if (z.preset or "") in ("growth", "growth-value", "pullback") else
                120  # value / dividend / contrarian は長期
            )
            pool_by_ticker[z.ticker] = CandidatePool(
                decision_id=virtual_id,
                ticker=z.ticker,
                gendo_stance="ZEELE",
                source="zeele",
                preset=z.preset or None,
                target_period_days=horizon,
                stop_pct=0.12,
                real_decision=None,
                score=z_score_norm,
            )

    pool = list(pool_by_ticker.values())

    # v2.8: 実弾モード時、予算内の優良銘柄を Universe から補完（MAGI + ZEELE 両系統）
    try:
        from trading_agent.utils.lot_size import get_max_lot_cost_jpy, is_live_mode

        if is_live_mode():
            try:
                view = treasury_view(engine)
                seed = float(view.get("seed_jpy") or 0)
            except Exception:
                seed = 0.0
            max_lot = get_max_lot_cost_jpy(seed)
            if 0 < max_lot < float("inf"):
                # 0. 既存 MAGI/ZEELE 由来の予算外銘柄を候補プールから除外
                #    （AKAGI が無駄に Brief 構築するのを防ぐ）
                pool = _filter_existing_pool_by_budget(pool, max_lot)

                # MAGI 補完 (v2.9: max 20 / Universe 300 で候補プール拡大)
                magi_supplements = _supplement_with_budget_friendly(
                    engine,
                    pool,
                    max_lot,
                    source="magi",
                    gendo_stance="推し",
                    fixed_preset="alpha",
                    target_period_days=60,
                    stop_pct=0.08,
                    max_supplement=20,
                    sample_universe=300,
                    id_offset=20000,
                )
                pool.extend(magi_supplements)

                # ZEELE 補完 (v2.9: max 15 / min_return_pct 緩和 0%・モメンタム幅拡大)
                zeele_supplements = _supplement_with_budget_friendly(
                    engine,
                    pool,
                    max_lot,
                    source="zeele",
                    gendo_stance="ZEELE",
                    fixed_preset=None,
                    auto_preset_from_momentum=True,
                    target_period_days=90,
                    stop_pct=0.10,
                    max_supplement=15,
                    sample_universe=300,
                    min_return_pct=0.0,           # +2% → 0% に緩和
                    sort_after_yf_by="return_desc",
                    id_offset=30000,
                )
                pool.extend(zeele_supplements)

                _log.info(
                    "budget_supplement_added",
                    magi_count=len(magi_supplements),
                    zeele_count=len(zeele_supplements),
                    max_lot_cost=max_lot,
                )
    except Exception as exc:
        _log.warning("budget_supplement_failed", error=str(exc))

    return pool


def _filter_existing_pool_by_budget(
    pool: list[CandidatePool], max_lot_cost_jpy: float
) -> list[CandidatePool]:
    """既存 MAGI/ZEELE 由来の候補から「1 単元 > 予算上限」の銘柄を除外。

    実弾モード専用。yfinance bulk で価格取得して判定。
    価格取得失敗銘柄は安全側に倒して残す（=過剰除外を避ける）。
    """
    if not pool or max_lot_cost_jpy <= 0:
        return pool

    # 補完銘柄（decision_id < 0）は既に予算内なのでスキップ
    existing = [c for c in pool if c.decision_id is not None and c.decision_id > 0]
    supplements_or_real = [
        c for c in pool if c.decision_id is None or c.decision_id <= 0
    ]
    if not existing:
        return pool

    tickers = [c.ticker for c in existing]
    try:
        import yfinance as yf

        from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol
    except Exception:
        return pool

    sym_map = {to_yfinance_symbol(t): t for t in tickers}
    try:
        df = yf.download(
            list(sym_map.keys()),
            period="2d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
        )
    except Exception:
        return pool
    if df is None or df.empty:
        return pool

    # ticker → 直近終値
    price_map: dict[str, float] = {}
    for sym, ticker in sym_map.items():
        try:
            series = df[sym]["Close"] if len(sym_map) > 1 else df["Close"]
            closes = [float(x) for x in series.dropna().tolist()]
            if closes:
                price_map[ticker] = closes[-1]
        except Exception:
            continue

    # フィルタ: 価格不明は残す、1 単元 > 上限は除外
    kept: list[CandidatePool] = []
    dropped: list[str] = []
    for c in existing:
        p = price_map.get(c.ticker)
        if p is None:
            kept.append(c)  # 価格不明 → 安全側で残す
            continue
        lot_cost = p * 100  # JP 単元 = 100 株
        if lot_cost > max_lot_cost_jpy:
            dropped.append(c.ticker)
        else:
            kept.append(c)

    if dropped:
        _log.info(
            "pool_budget_filter",
            dropped_count=len(dropped),
            kept_count=len(kept),
            max_lot_cost=max_lot_cost_jpy,
            samples=dropped[:5],
        )

    return kept + supplements_or_real


def _supplement_with_budget_friendly(
    engine: Engine,
    existing_pool: list[CandidatePool],
    max_lot_cost_jpy: float,
    *,
    source: str = "magi",
    gendo_stance: str = "推し",
    fixed_preset: str | None = None,
    auto_preset_from_momentum: bool = False,
    target_period_days: int = 60,
    stop_pct: float = 0.08,
    max_supplement: int = 10,
    sample_universe: int = 150,
    min_return_pct: float = -10.0,
    sort_after_yf_by: str = "default",  # "default"=時価総額順 / "return_desc"=30日リターン降順
    id_offset: int = 20000,
) -> list[CandidatePool]:
    """予算内（1 単元 ≤ max_lot_cost）の優良銘柄を Universe から補完。

    Args:
        source: "magi" / "zeele" 等。CandidatePool.source に入る
        gendo_stance: "推し" / "ZEELE" 等。CandidatePool.gendo_stance に入る
        fixed_preset: 固定 preset 名（None で auto_preset_from_momentum 参照）
        auto_preset_from_momentum: True なら 30 日リターンで preset を自動判定
          - ret > 5%   → "momentum"
          - 0 < ret ≤ 5% → "growth"
          - -5 < ret ≤ 0 → "growth-value"
          - ret ≤ -5%  → "value"
        target_period_days: CandidatePool.target_period_days
        stop_pct: CandidatePool.stop_pct
        max_supplement: 最大補完数
        sample_universe: Universe からサンプリングする件数（時価総額上位）
        min_return_pct: 30 日リターン下限（-10% より悪い銘柄は除外）
        id_offset: virtual decision_id のオフセット（MAGI=20000 / ZEELE=30000）
    """
    from trading_agent.models.universe import Universe

    existing_tickers = {c.ticker for c in existing_pool}

    try:
        with Session(engine) as s:
            rows = s.exec(select(Universe).where(col(Universe.market) == "JP")).all()
    except Exception:
        return []

    candidates = [u for u in rows if u.ticker not in existing_tickers]
    candidates.sort(key=lambda u: -(u.market_cap_jpy or 0))
    candidates = candidates[:sample_universe]

    if not candidates:
        return []

    # yfinance で価格と 30 日リターンを bulk 取得
    try:
        import yfinance as yf

        from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol
    except Exception:
        return []

    sym_map = {to_yfinance_symbol(u.ticker): u.ticker for u in candidates}
    try:
        df = yf.download(
            list(sym_map.keys()),
            period="2mo",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
        )
    except Exception:
        return []
    if df is None or df.empty:
        return []

    # 価格取得 + フィルタを通過した銘柄を集める（ソート前）
    passed: list[tuple[str, float, float]] = []  # (ticker, price, ret_30d)
    for sym, ticker in sym_map.items():
        try:
            series = df[sym]["Close"] if len(sym_map) > 1 else df["Close"]
            closes = [float(x) for x in series.dropna().tolist()]
            if len(closes) < 22:
                continue
            price = closes[-1]
            ret_30d = (price - closes[-22]) / closes[-22] * 100
            lot_cost = price * 100  # JP 単元 = 100 株
            if lot_cost > max_lot_cost_jpy:
                continue
            if ret_30d < min_return_pct:
                continue
            passed.append((ticker, price, ret_30d))
        except Exception:
            continue

    # v2.8: ZEELE 用は「30 日リターン降順」で並べる（モメンタム重視・利益アップ見込み）
    if sort_after_yf_by == "return_desc":
        passed.sort(key=lambda x: -x[2])

    out: list[CandidatePool] = []
    for ticker, price, ret_30d in passed:
        # preset の決定
        if fixed_preset is not None:
            preset = fixed_preset
        elif auto_preset_from_momentum:
            if ret_30d > 10:
                preset = "momentum"
            elif ret_30d > 5:
                preset = "growth"
            elif ret_30d > 0:
                preset = "growth-value"
            elif ret_30d > -5:
                preset = "pullback"
            else:
                preset = "value"
        else:
            preset = None
        virtual_id = -(id_offset + abs(hash(ticker)) % 90000)
        out.append(
            CandidatePool(
                decision_id=virtual_id,
                ticker=ticker,
                gendo_stance=gendo_stance,
                source=source,
                preset=preset,
                target_period_days=target_period_days,
                stop_pct=stop_pct,
                real_decision=None,
                score=0.55,
            )
        )
        if len(out) >= max_supplement:
            break
    return out


# ----- 昇格判定 -----

# v2.1 TASK-SZ5: risk/params.py の D-23 ゲート閾値を単一の正として参照
def _build_promotion_thresholds() -> dict[str, float]:
    from trading_agent.risk.params import DEFAULT_RISK
    return {
        "n_min": float(DEFAULT_RISK.gate_min_decisions),
        "hit_rate_min": float(DEFAULT_RISK.gate_min_hit_rate),
        "avg_r_min": float(DEFAULT_RISK.gate_min_avg_r),
        "max_dd": float(DEFAULT_RISK.gate_max_drawdown),
    }


PROMOTION_THRESHOLDS = _build_promotion_thresholds()


# v2.4 TASK-P8: D-23 ゲートを段階制に
# 旧: n ≥30 のみで「昇格推奨」 / 段階なし
# 新: n=10 「観察開始」、n=20 「暫定推奨」、n=30 「正式推奨」
PROMOTION_STAGES = {
    "observation": {"n_min": 10, "label": "観察開始", "actionable": False},
    "tentative": {"n_min": 20, "label": "暫定推奨", "actionable": False},
    "approved": {"n_min": 30, "label": "正式推奨（昇格）", "actionable": True},
}


def _confidence_interval_95(hits: int, n: int) -> tuple[float, float]:
    """二項比率の信頼区間（Wald 近似・簡易）。"""
    if n == 0:
        return (0.0, 0.0)
    p = hits / n
    import math
    se = math.sqrt(max(p * (1 - p) / n, 0.0))
    return (max(0.0, p - 1.96 * se), min(1.0, p + 1.96 * se))


def evaluate_promotions(engine: Engine) -> list[PromotionCandidate]:
    """D-23 準拠の昇格判定（v2.4 TASK-P8: 段階制）。

    n ≥10 で「観察開始」、≥20 で「暫定推奨」、≥30 で「正式推奨（昇格）」。
    hit_rate と avg_R の閾値は全段階共通。信頼区間も note に表示。
    """
    perf = _pilot_performance(engine)
    out: list[PromotionCandidate] = []
    for name, d in perf.items():
        if not name:
            continue
        n = int(d.get("n", 0) or 0)
        hr = float(d.get("hit_rate", 0.0) or 0.0)
        ar = float(d.get("avg_r", 0.0) or 0.0)
        hit_count = int(hr * n)  # 復元（簡易）

        # 段階判定（n が最大の段階を採用）
        stage = None
        for stage_key, stage_def in PROMOTION_STAGES.items():
            if n >= stage_def["n_min"]:
                stage = stage_key

        if stage is None:
            continue  # n < 10 は集計対象外

        # hit_rate と avg_R の閾値判定
        meets_threshold = (
            hr >= PROMOTION_THRESHOLDS["hit_rate_min"]
            and ar >= PROMOTION_THRESHOLDS["avg_r_min"]
        )
        if not meets_threshold:
            continue

        # 信頼区間
        ci_lo, ci_hi = _confidence_interval_95(hit_count, n)
        stage_label = PROMOTION_STAGES[stage]["label"]
        actionable = PROMOTION_STAGES[stage]["actionable"]

        action_note = (
            "→ 実弾サイジング推奨（人間承認必要）"
            if actionable
            else "→ 観察継続（n 蓄積待ち・実弾化はまだ）"
        )

        out.append(
            PromotionCandidate(
                personality=name,
                n=n,
                hit_rate=hr,
                avg_r=ar,
                note=(
                    f"[{stage_label}] n={n} hit_rate={hr*100:.0f}% "
                    f"(95% CI [{ci_lo*100:.0f}-{ci_hi*100:.0f}%]) avg_R={ar:+.2f} {action_note}"
                ),
            )
        )
    return out


# ----- ディスパッチ本体 -----

def dispatch(
    engine: Engine,
    *,
    total_budget_jpy: float | None = None,
    approve: bool = False,
    halt_file: Path | None = None,
    today: dt.date | None = None,
    only_personality: str | None = None,
    price_lookup: PriceLookup | None = None,
    is_jp_lookup: IsJpLookup | None = None,
    min_budget_overrides: dict[str, float | None] | None = None,
) -> DispatchPlan:
    """MISATO の中核呼び出し。

    Args:
        engine: 本番 DB
        total_budget_jpy: 今回の dispatch に MISATO が振れる総予算
        approve: True なら paper_fill_approved を実行（人間承認後の操作）
        halt_file: HALT パス上書き（テスト用）
        only_personality: 1 機だけ動かす場合（テスト・部分実行用）

    Returns:
        DispatchPlan: 配分案・割り当て案・昇格候補。approve=True の時は fills も含む。
    """
    plan = DispatchPlan(generated_at=utcnow().strftime("%Y-%m-%d %H:%M:%S"))
    halted, reason = check_halt(halt_file)
    if halted:
        plan.halted = True
        plan.halt_reason = reason
        _log.warning("misato_halted", reason=reason)
        return plan

    # 予算未指定 → treasury の未配分残高を使う（流動性に応じて変動）
    if total_budget_jpy is None:
        view = treasury_view(engine)
        total_budget_jpy = float(view["available_jpy"])
        if total_budget_jpy <= 0:
            plan.halt_reason = (
                "MISATO 預かり金が不足（seed=¥{seed_jpy}, allocated=¥{allocated_jpy}, available=¥0）"
                .format(**view)
            )
            plan.halted = True
            return plan

    if total_budget_jpy <= 0:
        plan.halt_reason = f"予算 ¥{total_budget_jpy} は不正（>0 必須）"
        plan.halted = True
        return plan
    if total_budget_jpy > MAX_BUDGET_PER_DISPATCH_JPY:
        _log.warning(
            "misato_budget_capped",
            requested=total_budget_jpy,
            capped=MAX_BUDGET_PER_DISPATCH_JPY,
        )

    plan.total_budget_jpy = min(total_budget_jpy, MAX_BUDGET_PER_DISPATCH_JPY)
    plan.promotions = evaluate_promotions(engine)

    # 1. 候補 pool 構築（MAGI + ZEELE 上位 N）
    pool = _build_candidate_pool(engine)

    # 2. **WILLE 統合フロー** (v2.8 / INVESTIGELION 再設計)
    #   2a. RITSUKO が候補銘柄の TickerBrief を構築（5 中立スコア）
    #   2b. MISATO 戦略パラメータで proposals に boost 加算 → priority 計算
    #   2c. 排他制御（同銘柄に複数機 proposal なら priority 最高が採用）
    #   2d. 例外ルール（決算前/ピア劣位/Sonnet 強い売り）でブロック
    from trading_agent.portfolio.ds_scout import (
        PilotProposal,
        _fetch_min_budgets,
        select_from_pool,
        select_kaworu_contrarian,
    )
    from trading_agent.wille import misato as wille_misato
    from trading_agent.wille import ritsuko as wille_ritsuko

    # テスト時は min_budget_overrides でダミー価格を注入可能（yfinance 不要）
    # v2.8: 実弾モードでの max_lot_cost 判定用に treasury 残高を渡す
    # （treasury が大きいほど高額銘柄も候補化、小さければ低額銘柄のみ）
    _treasury_for_lot = None
    try:
        _treasury_for_lot = float(treasury_view(engine).get("seed_jpy") or 0)
    except Exception:
        pass
    min_budgets = min_budget_overrides if min_budget_overrides is not None else _fetch_min_budgets(
        [c.ticker for c in pool],
        available_budget_jpy=_treasury_for_lot,
    )

    # === RITSUKO Brief 構築（5 中立スコア + 生データ）===
    briefs = wille_ritsuko.build_briefs_from_pool(pool, engine=engine)

    # === MISATO 戦略パラメータ（環境変数で切替可・デフォルト balanced）===
    import os as _os
    preset_name = _os.environ.get("MISATO_STRATEGY", "balanced")
    if preset_name not in ("balanced", "news_focused", "trend_focused", "peer_focused"):
        preset_name = "balanced"
    strategy = wille_misato.MisatoStrategy.from_preset(preset_name)  # type: ignore[arg-type]

    # === 市場全体ガード（日経 / TOPIX -3% で新規 fill 停止）===
    # WILLE_MARKET_GUARD=0 で無効化可能（テスト・dry-run 用）
    market_guard_skip_new_fill = False
    if approve and _os.environ.get("WILLE_MARKET_GUARD", "1") == "1":
        try:
            mkt = wille_ritsuko.detect_market_regime_live()
            if mkt.get("is_risk_off"):
                market_guard_skip_new_fill = True
                plan.halt_reason = (
                    f"市場ガード発動: 日経 {mkt.get('nikkei_change_pct') or 0:+.1f}% / "
                    f"TOPIX {mkt.get('topix_change_pct') or 0:+.1f}% (≤-3% で新規 fill 停止)"
                )
                _log.warning(
                    "wille_market_guard_active",
                    nikkei=mkt.get("nikkei_change_pct"),
                    topix=mkt.get("topix_change_pct"),
                )
        except Exception as exc:
            _log.warning("wille_market_guard_check_failed", error=str(exc))

    # === DS Scout で各機の自発 proposal を取得 ===
    # v2.8: REI/ASUKA/SHINJI は select_from_pool（既存）
    #       KAWORU は select_kaworu_contrarian（他機が選ばなかった銘柄から短期エッジで拾う）
    proposals_by_pilot: dict[str, list] = {p.name: [] for p in all_personalities()}
    for pilot in ("REI", "ASUKA", "SHINJI"):
        if only_personality and pilot != only_personality:
            continue
        if pilot in proposals_by_pilot:
            proposals_by_pilot[pilot] = select_from_pool(
                pilot, pool, engine=engine, min_budgets=min_budgets
            )

    # KAWORU は他機の proposal を見て、選ばれなかった銘柄から短期エッジで拾う
    if (not only_personality or only_personality == "KAWORU") and "KAWORU" in proposals_by_pilot:
        excluded_tickers: set[str] = set()
        for _p in ("REI", "ASUKA", "SHINJI"):
            for _prop in proposals_by_pilot.get(_p, []):
                excluded_tickers.add(_prop.ticker)
        proposals_by_pilot["KAWORU"] = select_kaworu_contrarian(
            pool,
            excluded_tickers=excluded_tickers,
            briefs=briefs,
            engine=engine,
            min_budgets=min_budgets,
        )

    # === MISATO priority 計算 + 例外チェック + 排他制御 ===
    # 各 proposal に boost を加算して priority を求める
    priority_by_key: dict[tuple[str, str], dict[str, float]] = {}
    blocked_by_key: dict[tuple[str, str], str] = {}
    for pilot, props in proposals_by_pilot.items():
        for prop in props:
            brief = briefs.get(prop.ticker)
            _priority, breakdown = wille_misato.compute_priority(
                prop.confidence, brief, strategy
            )
            priority_by_key[(pilot, prop.ticker)] = breakdown
            verdict = wille_misato.check_proposal_exceptions(prop.ticker, brief)
            if verdict.blocked:
                blocked_by_key[(pilot, prop.ticker)] = verdict.reason

    # 排他制御: 同銘柄に複数機の proposal がある場合は priority 最高だけ採用
    best_pilot_by_ticker: dict[str, str] = {}
    for (pilot, ticker), bd in priority_by_key.items():
        if (pilot, ticker) in blocked_by_key:
            continue
        cur_best = best_pilot_by_ticker.get(ticker)
        if cur_best is None:
            best_pilot_by_ticker[ticker] = pilot
        elif bd["priority"] > priority_by_key[(cur_best, ticker)]["priority"]:
            best_pilot_by_ticker[ticker] = pilot

    # 排他 + 例外フィルタ後の proposals_by_pilot を再構築
    new_proposals_by_pilot: dict[str, list] = {p.name: [] for p in all_personalities()}
    for pilot, props in proposals_by_pilot.items():
        for prop in props:
            if (pilot, prop.ticker) in blocked_by_key:
                _log.info(
                    "misato_proposal_blocked",
                    pilot=pilot,
                    ticker=prop.ticker,
                    reason=blocked_by_key[(pilot, prop.ticker)],
                )
                continue
            if best_pilot_by_ticker.get(prop.ticker) != pilot:
                # 同銘柄でより高い priority の機がいる → 除外
                _winner = best_pilot_by_ticker.get(prop.ticker)
                _log.info(
                    "misato_proposal_exclusive_lost",
                    pilot=pilot,
                    ticker=prop.ticker,
                    winner=_winner,
                    my_priority=priority_by_key[(pilot, prop.ticker)]["priority"],
                    winner_priority=priority_by_key[(_winner, prop.ticker)]["priority"],
                )
                continue
            bd = priority_by_key[(pilot, prop.ticker)]
            # priority 内訳を reason に追加（UI に見える）
            extra = (
                f" ｜ 🧪 boost={bd['ritsuko_boost']:+.2f} → priority={bd['priority']:.2f}"
            )
            new_proposals_by_pilot[pilot].append(
                PilotProposal(
                    pilot_name=prop.pilot_name,
                    ticker=prop.ticker,
                    confidence=prop.confidence,
                    min_budget_jpy=prop.min_budget_jpy,
                    reason=f"{prop.reason}{extra}",
                    source=prop.source,
                    preset=prop.preset,
                    score=prop.score,
                )
            )
    proposals_by_pilot = new_proposals_by_pilot

    # 3. **機会駆動 fill**（v2.9: 枠なし上限あり）
    # 全 proposal を priority 降順で並べ、4 ガードレール + 質基準で greedy fill
    # 候補数や needs ベースで予算を「割る」のではなく、案件の質で投入する
    import os as _os_od

    use_opportunity = _os_od.environ.get("WILLE_OPPORTUNITY_FILL", "1") == "1"
    opportunity_plan = None
    if use_opportunity:
        from trading_agent.wille.opportunity_fill import (
            GuardrailConfig,
            opportunity_driven_fill,
        )

        # ticker → sector lookup（Universe テーブル）
        sector_lookup: dict[str, str] = {}
        try:
            from trading_agent.models.universe import Universe

            with Session(engine) as _s:
                _rows = _s.exec(
                    select(Universe).where(
                        col(Universe.ticker).in_(
                            [c.ticker for c in pool]
                        )
                    )
                ).all()
                for _r in _rows:
                    if _r.sector:
                        sector_lookup[_r.ticker] = _r.sector
        except Exception:
            pass
        # ticker → boost lookup
        boost_lookup = {
            t: float(bd["ritsuko_boost"])
            for (_p, t), bd in priority_by_key.items()
        }
        # v2.10: 予算規模適応の max_lot_pct（lot_size.py の哲学と整合）
        # 少額(¥100-200k) → 50% 攻め型 / 中額 → 25% / 大額 → 10%
        from trading_agent.utils.lot_size import get_max_lot_pct
        _adaptive_lot_pct = get_max_lot_pct(plan.total_budget_jpy)

        # v2.10 Phase 2 Mini Feedback: 機別 multiplier（過去 30 日の判断精度ベース）
        # データ不足時は全機 1.0（中立）→ 通常動作と等価
        from trading_agent.portfolio.feedback import compute_pilot_multipliers
        _feedback = compute_pilot_multipliers(engine, lookback_days=30)
        _pilot_mul = _feedback.get("multipliers", {})

        # N1: account_total を treasury から取得し opportunity_fill に渡す。
        # G-7 逓減 (TAPER_SCHEDULE) と整合した cash_floor で動的に運用 (少額時 0.20 /
        # 大額時 0.10)。これで少額 Treasury でも selected=0 状態を緩和できる。
        try:
            _account_total = float(treasury_view(engine).get("seed_jpy") or 0)
        except Exception:
            _account_total = 0.0

        opportunity_plan = opportunity_driven_fill(
            proposals_by_pilot,
            total_budget_jpy=plan.total_budget_jpy,
            config=GuardrailConfig(max_lot_pct=_adaptive_lot_pct),
            sector_lookup=sector_lookup,
            boost_lookup=boost_lookup,
            blocked_tickers={t for (_p, t) in blocked_by_key},
            pilot_multipliers=_pilot_mul,
            account_total_jpy=_account_total if _account_total > 0 else None,
        )
        # 機別予算は計画から算出（needs-based 配分は使わない）
        per_pilot_from_opp = {
            p.name: round(opportunity_plan.per_pilot_budget.get(p.name, 0.0))
            for p in all_personalities()
        }
        plan.allocation = BudgetAllocation(
            total_jpy=int(plan.total_budget_jpy),
            per_pilot_jpy=per_pilot_from_opp,
            weighted=False,
            mode="opportunity-driven",
            reason=(
                f"機会駆動 fill: 採用 {len(opportunity_plan.selected)} 件 / "
                f"却下 {len(opportunity_plan.rejected)} 件 / "
                f"cash 保持 ¥{int(opportunity_plan.cash_remaining):,}"
            ),
            per_pilot_demand={
                p: len(props) for p, props in proposals_by_pilot.items()
            },
        )
    else:
        # 旧 needs-based 配分
        demand_by_pilot: dict[str, int] = {
            p: len(props) for p, props in proposals_by_pilot.items()
        }
        weighted_demand_by_pilot: dict[str, float] = {
            p: sum(prop.confidence for prop in props)
            for p, props in proposals_by_pilot.items()
        }
        plan.allocation = allocate_budget(
            plan.total_budget_jpy,
            engine=engine,
            demand_by_pilot=demand_by_pilot,
            weighted_demand_by_pilot=weighted_demand_by_pilot,
        )

    # 4. 機別 top-K 選別：confidence × ダブル推奨ブースト で上位を採用
    # 機会駆動モード時: opportunity_plan.selected の (pilot, ticker) を picked にする
    opportunity_picked: set[tuple[str, str]] = set()
    if opportunity_plan is not None:
        for sel in opportunity_plan.selected:
            opportunity_picked.add((sel["pilot"], sel["ticker"]))

    plan.assignments = []
    for pilot, proposals in proposals_by_pilot.items():
        budget = plan.allocation.per_pilot_jpy.get(pilot, 0.0)
        max_picks = int(budget // MIN_INVEST_PER_DECISION_JPY) if budget > 0 else 0
        # 機内ソート: confidence 降順 → ダブル推奨優先 → score 降順
        sorted_props = sorted(
            proposals,
            key=lambda p: (
                -p.confidence,
                0 if p.source == "both" else 1,
                -float(p.score),
            ),
        )
        actual_pick_count = min(max_picks, len(sorted_props))
        per_pick = (budget / actual_pick_count) if actual_pick_count > 0 else 0.0
        for i, prop in enumerate(sorted_props):
            # 機会駆動モード時は opportunity_picked に従う
            if opportunity_plan is not None:
                is_picked = (pilot, prop.ticker) in opportunity_picked
            else:
                is_picked = i < actual_pick_count
            # CandidatePool から対応する元 decision_id を引く
            matched = next((c for c in pool if c.ticker == prop.ticker), None)
            decision_id = matched.decision_id if matched else 0
            gendo_stance = matched.gendo_stance if matched else "—"
            boost = 1.5 if prop.source == "both" else 1.0
            plan.assignments.append(
                Assignment(
                    decision_id=decision_id,
                    ticker=prop.ticker,
                    gendo_stance=gendo_stance,
                    assigned_to=pilot,
                    reason=prop.reason,
                    proposed_budget_jpy=per_pick if is_picked else 0.0,
                    source=prop.source,
                    preset=prop.preset,
                    boost=boost,
                    score=prop.score,
                    picked=is_picked,
                )
            )

    # v2.5 TASK-P16: dry-run（approve=False）でも price_lookup を受け取れる設計に。
    # ZEELE 仮想 decision の実価格を将来 fill するための事前データ取得用。
    # 自動売買 ON の機を取得（approve=False でもこれらだけは実 fill する）
    auto_pilots = auto_trade_pilots(engine)

    if not approve and not auto_pilots:
        return plan  # 完全 dry-run

    # === 実 fill を走らせる（approve=True or 自動売買 ON の機）===
    if price_lookup is None or is_jp_lookup is None:
        # 自動売買 ON 機があっても lookups なし → halt せず dry-run のまま返す（safe）
        if not approve:
            return plan
        plan.halt_reason = "approve=True なら price_lookup / is_jp_lookup が必須"
        plan.halted = True
        return plan

    from trading_agent.portfolio.paper_exec import (
        paper_close_due,
        paper_fill_approved,
    )

    # ticker → CandidatePool への lookup（approve 時に real_decision を引くため）
    pool_by_ticker = {c.ticker: c for c in pool}

    for pilot_name, proposals in proposals_by_pilot.items():
        if not proposals:
            continue
        if only_personality and pilot_name != only_personality:
            continue
        # approve=False（手動モード）でも、自動売買 ON の機だけは進める
        if not approve and pilot_name not in auto_pilots:
            continue
        budget = plan.allocation.per_pilot_jpy.get(pilot_name, 0.0)
        if budget < MIN_BUDGET_PER_PILOT_JPY:
            _log.info("misato_skip_pilot_under_min", pilot=pilot_name, budget=budget)
            continue

        # v2.8: 市場ガード発動時は新規 fill をスキップ（既存保有の close は走らせる）
        if market_guard_skip_new_fill:
            close_res = paper_close_due(
                engine,
                price_lookup=price_lookup,
                is_jp_lookup=is_jp_lookup,
                today=today,
                personality_filter=pilot_name,
            )
            plan.fills.append({
                "pilot": pilot_name,
                "budget_jpy": round(budget),
                "fills": [],
                "closes": [
                    {"ticker": c.ticker, "qty": c.qty, "sell_price": round(c.sell_price, 2),
                     "pnl_jpy": round(c.pnl_jpy), "reason": c.reason}
                    for c in close_res.closes
                ],
                "skipped": [],
                "cash_after_jpy": round(budget),
                "market_guard": True,
            })
            continue

        # 1. その機の保有のうち stop/time_exit 到達分を先に売却
        close_res = paper_close_due(
            engine,
            price_lookup=price_lookup,
            is_jp_lookup=is_jp_lookup,
            today=today,
            personality_filter=pilot_name,
        )

        # 2. picked のみ approved に進める（候補全部買わない）
        # v2.8: 補完銘柄 (real_decision=None) は新規 Decision を作成して approved に
        picked_tickers_for_pilot = {
            a.ticker for a in plan.assignments
            if a.assigned_to == pilot_name and a.picked
        }
        with Session(engine) as s:
            for ticker in picked_tickers_for_pilot:
                cand = pool_by_ticker.get(ticker)
                if cand is None:
                    continue
                if cand.real_decision is not None and cand.real_decision.id is not None:
                    # 既存 Decision を approved に
                    tgt = s.get(Decision, cand.real_decision.id)
                    if tgt is not None and tgt.status != "approved":
                        tgt.status = "approved"
                        s.add(tgt)
                else:
                    # 補完銘柄: 新規 Decision を作成（approved）
                    from trading_agent.models.decisions import Decision as _D

                    existing = s.exec(
                        select(_D).where(
                            col(_D.ticker) == ticker,
                            col(_D.status).in_(("approved", "awaiting", "ordered", "holding")),
                        )
                    ).first()
                    if existing is None:
                        import datetime as _dt

                        new_d = _D(
                            date=_dt.date.today(),
                            ticker=ticker,
                            action="buy",
                            status="approved",
                            gendo_stance=cand.gendo_stance,
                            target_period_days=cand.target_period_days,
                            stop_pct=cand.stop_pct,
                            score=cand.score,
                            thesis_at_decision=f"v2.8 補完銘柄（source={cand.source}, preset={cand.preset}）",
                        )
                        s.add(new_d)
                    elif existing.status != "approved":
                        existing.status = "approved"
                        s.add(existing)
            s.commit()

        # 3. MISATO が振った予算を cash_jpy として paper_fill を呼ぶ（= 安全装置）
        personality = PERSONALITIES[pilot_name]
        try:
            res = paper_fill_approved(
                engine,
                price_lookup=price_lookup,
                is_jp_lookup=is_jp_lookup,
                cash_jpy=budget,  # ← MISATO 配分予算で上限を物理的に縛る
                today=today,
                personality=personality,
            )
            fills_summary = {
                "pilot": pilot_name,
                "budget_jpy": round(budget),
                "fills": [
                    {
                        "decision_id": f.decision_id,
                        "ticker": f.ticker,
                        "shares": f.shares,
                        "fill_price": round(f.fill_price, 2),
                        "amount_jpy": round(f.amount_jpy),
                        "fee_jpy": round(f.fee_jpy),
                        "rationale": f.rationale,
                    }
                    for f in res.fills
                ],
                "closes": [
                    {
                        "ticker": c.ticker,
                        "qty": c.qty,
                        "sell_price": round(c.sell_price, 2),
                        "pnl_jpy": round(c.pnl_jpy),
                        "reason": c.reason,
                    }
                    for c in close_res.closes
                ],
                "skipped": [{"ticker": t, "reason": r} for t, r in res.skipped],
                "cash_after_jpy": round(res.cash_after),
            }
        except Exception as exc:
            _log.warning("misato_fill_failed", pilot=pilot_name, error=str(exc))
            fills_summary = {"pilot": pilot_name, "error": str(exc), "fills": []}
        plan.fills.append(fills_summary)

    # === Treasury 連携: 各機の PilotAllocation を MISATO 配分で書き換える ===
    set_pilot_allocations(engine, plan.allocation.per_pilot_jpy if plan.allocation else {})

    plan.executed = True
    return plan


# ----- Treasury 操作 -----


def _resolve_broker_mode(broker_mode: str | None = None) -> str:
    """broker_mode 自動解決: 引数 > 設定ファイル > "paper"。"""
    if broker_mode in ("paper", "live"):
        return broker_mode
    try:
        from trading_agent.utils.lot_size import get_broker_mode
        return get_broker_mode()
    except Exception:
        return "paper"


def get_treasury(engine: Engine, broker_mode: str | None = None) -> MisatoTreasury:
    """KATSURAGI 預かり金を取得（v2.8: broker_mode 別に Paper / Live 並行運用）。

    Paper モード: id=1（既存・互換）
    Live モード: id=2（新規）
    無ければ初期化して返す。
    """
    mode = _resolve_broker_mode(broker_mode)
    row_id = 2 if mode == "live" else 1
    with Session(engine, expire_on_commit=False) as s:
        # まず broker_mode で検索（複合 PK 移行期間の安全策）
        rows = s.exec(
            select(MisatoTreasury).where(col(MisatoTreasury.broker_mode) == mode)
        ).all()
        if rows:
            return rows[0]
        # 無ければ新規作成
        row = MisatoTreasury(
            id=row_id, broker_mode=mode, seed_jpy=0.0, deposit_count=0
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return row


def deposit(
    engine: Engine, amount_jpy: float, broker_mode: str | None = None
) -> MisatoTreasury:
    """MISATO に入金（seed_jpy を加算）。

    v2.8: 負の値も許可（払い戻し）。ただし以下を保証:
      - 払い戻し後の seed >= 配分済 allocated（配分済を取り戻せない）
      - 払い戻し後の seed >= 0（マイナス預かり金は許可しない）
    """
    if amount_jpy == 0:
        raise ValueError("入金/払い戻し額は 0 以外が必要")
    mode = _resolve_broker_mode(broker_mode)
    row_id = 2 if mode == "live" else 1
    with Session(engine, expire_on_commit=False) as s:
        rows = s.exec(
            select(MisatoTreasury).where(col(MisatoTreasury.broker_mode) == mode)
        ).all()
        if rows:
            row = rows[0]
        else:
            row = MisatoTreasury(id=row_id, broker_mode=mode, seed_jpy=0.0, deposit_count=0)
        new_seed = float(row.seed_jpy) + float(amount_jpy)

        if amount_jpy < 0:
            total_allocated = 0.0
            allocs = s.exec(
                select(PilotAllocation).where(col(PilotAllocation.broker_mode) == mode)
            ).all()
            for alloc in allocs:
                total_allocated += float(alloc.allocated_jpy or 0)
            min_seed = max(0.0, total_allocated)
            if new_seed < min_seed:
                raise ValueError(
                    f"払い戻し不可: 配分済 ¥{int(total_allocated):,} を下回ります"
                    f"（現 seed ¥{int(row.seed_jpy):,} → 払戻後 ¥{int(new_seed):,}）"
                )

        row.seed_jpy = new_seed
        row.deposit_count = (row.deposit_count or 0) + 1
        row.last_deposit_at = utcnow()
        row.updated_at = utcnow()
        s.add(row)
        s.commit()
        s.refresh(row)
        return row


def cleanup_fractional_holdings(engine: Engine) -> dict[str, int]:
    """単元株（JP 100 株）未満の active 保有を一括 close する（v2.8 実弾モード化）。

    Paper モード時に 1 株単位で買った銘柄が、実弾モード本流化で「単元未満なのに保持」
    という矛盾状態になるため、qty が lot_size の倍数でない銘柄を強制 close する。

    Returns:
        {"closed_count": N, "closed_tickers": [...]}
    """
    from trading_agent.utils.lot_size import get_lot_size

    closed_tickers: list[str] = []
    now = utcnow()
    with Session(engine, expire_on_commit=False) as s:
        rows = s.exec(select(Portfolio).where(col(Portfolio.status) == "active")).all()
        for p in rows:
            lot = get_lot_size(p.ticker)
            if lot <= 1:
                continue  # 米国株（1 株単位）はスキップ
            qty = int(p.qty or 0)
            if qty % lot != 0:
                # 単元未満保有 → close
                p.status = "closed"
                p.closed_at = now
                p.closed_price = float(p.buy_price or 0)
                p.closed_reason = f"fractional_cleanup (qty={qty} not multiple of {lot})"
                s.add(p)
                closed_tickers.append(f"{p.personality}:{p.ticker}({qty})")
        s.commit()
    return {"closed_count": len(closed_tickers), "closed_tickers": closed_tickers}


def reset_treasury(engine: Engine, broker_mode: str | None = None) -> None:
    """KATSURAGI 預かり金と全機の配分をゼロにリセット（broker_mode 別）。"""
    mode = _resolve_broker_mode(broker_mode)
    row_id = 2 if mode == "live" else 1
    with Session(engine, expire_on_commit=False) as s:
        rows = s.exec(
            select(MisatoTreasury).where(col(MisatoTreasury.broker_mode) == mode)
        ).all()
        if rows:
            row = rows[0]
        else:
            row = MisatoTreasury(id=row_id, broker_mode=mode)
        row.seed_jpy = 0.0
        row.deposit_count = 0
        row.last_deposit_at = None
        row.updated_at = utcnow()
        s.add(row)
        for pilot in (p.name for p in all_personalities()):
            allocs = s.exec(
                select(PilotAllocation).where(
                    col(PilotAllocation.pilot_name) == pilot,
                    col(PilotAllocation.broker_mode) == mode,
                )
            ).all()
            if allocs:
                alloc = allocs[0]
                alloc.allocated_jpy = 0.0
                alloc.updated_at = utcnow()
            else:
                alloc = PilotAllocation(
                    pilot_name=pilot, broker_mode=mode, allocated_jpy=0.0
                )
            s.add(alloc)
        s.commit()


def cleanup_for_fresh_run(engine: Engine) -> dict[str, int]:
    """検証メタ検証で発見した致命的バグの修正（v2.5+）。

    portfolio を closed にしても decision.personalities_filled が残ると、
    次回 dispatch で「もう fill 済」と判定されて新規 fill されない。
    本関数は：
      1. 全 active portfolio を closed (cleanup_pre_batch) に
      2. 該当 decision の personalities_filled をクリア
      3. entry_price / shares_filled / hit_or_miss="pending" をリセット
      4. status を ordered/approved/holding → awaiting に戻す
      5. MISATO Treasury もリセット
    """
    from trading_agent.models.decisions import Decision
    from trading_agent.models.portfolio import Portfolio

    counts = {"portfolios_closed": 0, "decisions_reset": 0, "decisions_cancelled": 0}
    now = utcnow()
    with Session(engine, expire_on_commit=False) as s:
        # 1. active portfolio → closed
        for p in s.exec(select(Portfolio).where(col(Portfolio.status) == "active")).all():
            p.status = "closed"
            p.closed_at = now
            p.closed_price = float(p.buy_price or 0)
            p.closed_reason = "cleanup_for_fresh_run"
            s.add(p)
            counts["portfolios_closed"] += 1
        # 2-4. 関連 decision をリセット（fill 状態をクリア）
        for d in s.exec(
            select(Decision).where(col(Decision.status).in_(("approved", "ordered", "holding")))
        ).all():
            d.personalities_filled = []
            d.entry_price = None
            d.shares_filled = 0.0
            d.evaluation_date = None
            d.actual_return = None
            d.hit_or_miss = "pending"
            d.status = "cancelled"
            s.add(d)
            counts["decisions_reset"] += 1
        # v2.10: awaiting / verifying な過去 Decision も cancel して真の fresh run を実現
        # （materialize_decisions が cancelled を再利用しない設計）
        for d in s.exec(
            select(Decision).where(col(Decision.status).in_(("awaiting", "verifying")))
        ).all():
            d.status = "cancelled"
            s.add(d)
            counts["decisions_cancelled"] += 1
        s.commit()
    # 5. Treasury もリセット
    reset_treasury(engine)
    return counts


def get_pilot_allocations(
    engine: Engine, broker_mode: str | None = None
) -> dict[str, float]:
    """各機の現在配分を返す（v2.8: broker_mode 別）。無ければ 0。"""
    mode = _resolve_broker_mode(broker_mode)
    out: dict[str, float] = {}
    with Session(engine) as s:
        for pilot in (p.name for p in all_personalities()):
            rows = s.exec(
                select(PilotAllocation).where(
                    col(PilotAllocation.pilot_name) == pilot,
                    col(PilotAllocation.broker_mode) == mode,
                )
            ).all()
            out[pilot] = float(rows[0].allocated_jpy) if rows else 0.0
    return out


def set_pilot_allocations(
    engine: Engine,
    per_pilot_jpy: dict[str, float],
    broker_mode: str | None = None,
) -> None:
    """各機の配分を上書き（approve 時に呼ばれる、v2.8: broker_mode 別）。"""
    mode = _resolve_broker_mode(broker_mode)
    with Session(engine, expire_on_commit=False) as s:
        for pilot, amount in per_pilot_jpy.items():
            rows = s.exec(
                select(PilotAllocation).where(
                    col(PilotAllocation.pilot_name) == pilot,
                    col(PilotAllocation.broker_mode) == mode,
                )
            ).all()
            if rows:
                row = rows[0]
                row.allocated_jpy = float(amount)
                row.updated_at = utcnow()
            else:
                row = PilotAllocation(
                    pilot_name=pilot, broker_mode=mode, allocated_jpy=float(amount)
                )
            s.add(row)
        s.commit()


# ----- 自動売買トグル -----

AUTO_TRADE_DEFAULT_HOURS = 24


def is_master_auto_active(engine: Engine, *, now: dt.datetime | None = None) -> bool:
    """マスター自動売買が ON か（期限内か）。"""
    now = _normalize_now(now)
    with Session(engine) as s:
        t = s.get(MisatoTreasury, 1)
    if t is None or t.master_auto_trade_until is None:
        return False
    return _ensure_aware(t.master_auto_trade_until) > now


def is_pilot_auto_active(
    engine: Engine, pilot: str, *, now: dt.datetime | None = None
) -> bool:
    """機別自動売買が ON か。マスター ON でも機側で OFF できる排他構造。"""
    now = _normalize_now(now)
    with Session(engine) as s:
        mode = _resolve_broker_mode(None)
        _rows = s.exec(
            select(PilotAllocation).where(
                col(PilotAllocation.pilot_name) == pilot,
                col(PilotAllocation.broker_mode) == mode,
            )
        ).all()
        row = _rows[0] if _rows else None
    if row is None or row.auto_trade_until is None:
        return False
    return _ensure_aware(row.auto_trade_until) > now


def _naive(d: dt.datetime) -> dt.datetime:
    """offset-aware を naive UTC に揃える（プロジェクト規約: DB は naive UTC）。"""
    if d.tzinfo is not None:
        return d.astimezone(dt.UTC).replace(tzinfo=None)
    return d


def _normalize_now(now: dt.datetime | None) -> dt.datetime:
    return _naive(now) if now is not None else utcnow()


# 旧名互換（既に呼ばれている箇所のため）
def _ensure_aware(d: dt.datetime) -> dt.datetime:
    return _naive(d)


def set_master_auto_trade(engine: Engine, *, hours: int | None) -> dt.datetime | None:
    """マスター自動売買 ON（hours 時間有効）/OFF（hours=None）。返り値は新しい期限。"""
    until = utcnow() + dt.timedelta(hours=hours) if hours else None
    with Session(engine, expire_on_commit=False) as s:
        t = s.get(MisatoTreasury, 1)
        if t is None:
            t = MisatoTreasury(id=1)
        t.master_auto_trade_until = until
        t.updated_at = utcnow()
        s.add(t)
        s.commit()
    return until


def set_pilot_auto_trade(
    engine: Engine, pilot: str, *, hours: int | None
) -> dt.datetime | None:
    """機別自動売買 ON/OFF。"""
    until = utcnow() + dt.timedelta(hours=hours) if hours else None
    mode = _resolve_broker_mode(None)
    with Session(engine, expire_on_commit=False) as s:
        _rows = s.exec(
            select(PilotAllocation).where(
                col(PilotAllocation.pilot_name) == pilot,
                col(PilotAllocation.broker_mode) == mode,
            )
        ).all()
        row = _rows[0] if _rows else None
        if row is None:
            row = PilotAllocation(pilot_name=pilot, broker_mode=mode, allocated_jpy=0.0)
        row.auto_trade_until = until
        row.updated_at = utcnow()
        s.add(row)
        s.commit()
    return until


def auto_trade_pilots(engine: Engine, *, now: dt.datetime | None = None) -> set[str]:
    """現在自動売買が有効な機の集合を返す（マスター ∪ 機別）。"""
    now = _normalize_now(now)
    master_on = is_master_auto_active(engine, now=now)
    out: set[str] = set()
    for p in all_personalities():
        if master_on or is_pilot_auto_active(engine, p.name, now=now):
            out.add(p.name)
    return out


def auto_trade_view(engine: Engine) -> dict[str, Any]:
    """UI 用：マスター + 機別の auto_trade 状態と残り時間を返す。"""
    now = utcnow()
    with Session(engine) as s:
        t = s.get(MisatoTreasury, 1)
        master_until = _ensure_aware(t.master_auto_trade_until) if (t and t.master_auto_trade_until) else None
        per_pilot: dict[str, dict[str, Any]] = {}
        mode = _resolve_broker_mode(None)
        for p in all_personalities():
            _rows = s.exec(
                select(PilotAllocation).where(
                    col(PilotAllocation.pilot_name) == p.name,
                    col(PilotAllocation.broker_mode) == mode,
                )
            ).all()
            row = _rows[0] if _rows else None
            until = _ensure_aware(row.auto_trade_until) if (row and row.auto_trade_until) else None
            per_pilot[p.name] = {
                "until": until.strftime("%Y-%m-%d %H:%M") if until else None,
                "active": until is not None and until > now,
                "remaining_minutes": (
                    int((until - now).total_seconds() // 60) if until and until > now else 0
                ),
            }
    return {
        "master": {
            "until": master_until.strftime("%Y-%m-%d %H:%M") if master_until else None,
            "active": master_until is not None and master_until > now,
            "remaining_minutes": (
                int((master_until - now).total_seconds() // 60) if master_until and master_until > now else 0
            ),
        },
        "per_pilot": per_pilot,
        "default_hours": AUTO_TRADE_DEFAULT_HOURS,
        "checked_at": now.strftime("%Y-%m-%d %H:%M"),
    }


def treasury_view(engine: Engine, broker_mode: str | None = None) -> dict[str, Any]:
    """KATSURAGI 財務の現状を dict で返す（v2.8: broker_mode 別）。"""
    t = get_treasury(engine, broker_mode)
    allocs = get_pilot_allocations(engine, broker_mode)
    allocated = sum(allocs.values())
    return {
        "broker_mode": _resolve_broker_mode(broker_mode),
        "seed_jpy": round(float(t.seed_jpy)),
        "allocated_jpy": round(allocated),
        "available_jpy": round(float(t.seed_jpy) - allocated),
        "allocations": {k: round(v) for k, v in allocs.items()},
        "deposit_count": t.deposit_count or 0,
        "last_deposit_at": (
            t.last_deposit_at.strftime("%Y-%m-%d %H:%M") if t.last_deposit_at else None
        ),
        "updated_at": t.updated_at.strftime("%Y-%m-%d %H:%M") if t.updated_at else None,
    }


def _summarize_ds_proposals(
    assignments: list[Assignment],
) -> dict[str, dict[str, Any]]:
    """機ごとに「提案件数 / 採用件数 / 平均 confidence（≒score 代用）」を集計。

    PilotProposal の confidence は Assignment の reason に "conf=X.XX" 形式で
    埋め込まれているため、ここでは reason から抽出する（簡易・将来は Assignment に直接持たせる）。
    """
    import re

    pat = re.compile(r"conf=([\d.]+)")
    per_pilot: dict[str, dict[str, Any]] = {}
    for pilot in ("REI", "ASUKA", "SHINJI", "KAWORU"):
        mine = [a for a in assignments if a.assigned_to == pilot]
        picked = [a for a in mine if a.picked]
        confs: list[float] = []
        for a in mine:
            m = pat.search(a.reason or "")
            if m:
                try:
                    confs.append(float(m.group(1)))
                except ValueError:
                    pass
        avg_conf = sum(confs) / len(confs) if confs else 0.0
        top_conf = max(confs) if confs else 0.0
        per_pilot[pilot] = {
            "proposed": len(mine),
            "picked": len(picked),
            "avg_confidence": round(avg_conf, 3),
            "top_confidence": round(top_conf, 3),
        }
    return per_pilot


def plan_to_dict(plan: DispatchPlan) -> dict[str, Any]:
    """DispatchPlan を snapshot.json に載せる dict に変換。"""
    return {
        "halted": plan.halted,
        "halt_reason": plan.halt_reason,
        "halt_file_path": str(DEFAULT_HALT_FILE),
        "total_budget_jpy": round(plan.total_budget_jpy),
        "allocation": {
            "per_pilot_jpy": {k: round(v) for k, v in (plan.allocation.per_pilot_jpy.items() if plan.allocation else {})},
            "weighted": plan.allocation.weighted if plan.allocation else False,
            "reason": plan.allocation.reason if plan.allocation else "",
            "mode": plan.allocation.mode if plan.allocation else "fallback-equal",
            "per_pilot_demand": dict(plan.allocation.per_pilot_demand) if plan.allocation else {},
        },
        "assignments": [
            {
                "decision_id": a.decision_id,
                "ticker": a.ticker,
                "gendo_stance": a.gendo_stance,
                "assigned_to": a.assigned_to,
                "reason": a.reason,
                "proposed_budget_jpy": round(a.proposed_budget_jpy),
                "source": a.source,
                "preset": a.preset,
                "boost": a.boost,
                "score": round(a.score, 3),
                "picked": a.picked,
            }
            for a in plan.assignments
        ],
        "picked_count": sum(1 for a in plan.assignments if a.picked),
        "shortlist_count": sum(1 for a in plan.assignments if not a.picked),
        # v2.5 TASK-P15: 実 fill との乖離指標（approve=True 時のみ意味あり）
        "actual_fills_count": sum(
            len(f.get("fills", []))
            for f in plan.fills
            if isinstance(f, dict)
        ),
        "picked_vs_fill_gap": (
            sum(1 for a in plan.assignments if a.picked)
            - sum(len(f.get("fills", [])) for f in plan.fills if isinstance(f, dict))
        ),
        # DS 主導フロー：機別の「提案 N / 採用 K / 平均 confidence」
        "ds_proposals": _summarize_ds_proposals(plan.assignments),
        "promotions": [
            {
                "personality": p.personality,
                "n": p.n,
                "hit_rate": p.hit_rate,
                "avg_r": p.avg_r,
                "note": p.note,
            }
            for p in plan.promotions
        ],
        "executed": plan.executed,
        "fills": plan.fills,
        "generated_at": plan.generated_at,
        "max_per_dispatch_jpy": MAX_BUDGET_PER_DISPATCH_JPY,
        "max_per_pilot_jpy": MAX_BUDGET_PER_PILOT_JPY,
        "promotion_thresholds": PROMOTION_THRESHOLDS,
    }
