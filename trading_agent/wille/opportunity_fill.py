"""🎯 機会駆動 fill エンジン（v2.9・枠なし上限あり設計）。

設計原則:
  - **枠埋め圧力を持たない**: 予算を使い切ることはゴールにしない
  - **質ありき**: 採用基準（priority + boost）を満たすものだけ買う
  - **4 ガードレール**で集中リスクを抑える（上限のみ・下限なし）
  - **残予算 → cash 保持**: 案件不足は次回 dispatch を待つ

4 ガードレール:
  1. 1 銘柄上限    : 予算 × max_lot_pct (デフォルト 20%)
  2. 1 source 上限 : 予算 × max_source_pct (60%) — MAGI/ZEELE 集中防止
  3. 1 セクター上限 : 予算 × max_sector_pct (30%) — 業種集中防止
  4. 1 機上限      : 予算 × max_pilot_pct (35%) — DS 4 機偏重防止

質の最低ライン:
  - priority >= min_priority (デフォルト 0.40)
  - boost >= min_boost (デフォルト -0.10)
  - 例外判定で blocked でない（KATSURAGI check_proposal_exceptions）
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from trading_agent.utils.logger import get_logger

_log = get_logger("wille.opportunity_fill")


@dataclass(frozen=True)
class GuardrailConfig:
    """4 ガードレールの上限率（予算に対する割合）+ 質基準 + cash 余力ハード制約。"""

    max_lot_pct: float = 0.20       # 1 銘柄
    max_source_pct: float = 0.60    # 1 source (magi / zeele / both)
    max_sector_pct: float = 0.30    # 1 セクター
    max_pilot_pct: float = 0.35     # 1 機
    min_priority: float = 0.40      # priority 最低ライン
    min_boost: float = -0.10        # boost 最低ライン
    fee_margin_pct: float = 0.015   # 手数料・スリッページ余裕（1.5%）
    min_cash_reserve_pct: float = 0.40  # cash 余力ハード制約: 予算 × この比率は買付不可（上昇株への身動き確保）
    # 大指針 #2(中小型成長株): 大型(¥1兆超)への配分上限。予算 × この比率を超える大型 fill を拒否。
    # 中小型優先のサンプリング(M1/M2)が効けば普段ヒットしないが、fill 側の最終防御として持つ。
    max_large_cap_pct: float = 0.30


@dataclass
class OpportunityFillPlan:
    """機会駆動 fill の計画結果（dispatch に返す）。"""

    # 採用案件
    selected: list[dict[str, Any]] = field(default_factory=list)
    # ガードレール / 質基準で落ちた案件
    rejected: list[dict[str, Any]] = field(default_factory=list)
    # 機別の配分予算（実 fill 時に paper_fill_approved に渡す）
    per_pilot_budget: dict[str, float] = field(default_factory=dict)
    # 統計
    total_budget: float = 0.0
    total_planned: float = 0.0
    cash_remaining: float = 0.0
    # ガードレール hit 履歴（透明性）
    guardrail_hits: dict[str, int] = field(default_factory=dict)
    # 質基準で落ちた件数
    quality_filter_count: int = 0


# 大指針 #2(中小型成長株)の size 区分（時価総額 JPY）。large=¥1兆超 / mid=¥3000億〜¥1兆 / small=未満。
_LARGE_CAP_MIN_JPY = 1.0e12
_MID_CAP_MIN_JPY = 3.0e11


def size_bucket(market_cap_jpy: float | None) -> str:
    if not market_cap_jpy or market_cap_jpy <= 0:
        return "unknown"
    if market_cap_jpy >= _LARGE_CAP_MIN_JPY:
        return "large"
    if market_cap_jpy >= _MID_CAP_MIN_JPY:
        return "mid"
    return "small"


def opportunity_driven_fill(
    proposals_by_pilot: dict[str, list[Any]],
    *,
    total_budget_jpy: float,
    config: GuardrailConfig | None = None,
    sector_lookup: dict[str, str] | None = None,
    boost_lookup: dict[str, float] | None = None,
    blocked_tickers: set[str] | None = None,
    pilot_multipliers: dict[str, float] | None = None,
    account_total_jpy: float | None = None,
    market_cap_lookup: dict[str, float] | None = None,
) -> OpportunityFillPlan:
    """全 proposal を priority 降順で並べ、ガードレール内で greedy fill。

    Args:
        proposals_by_pilot: {pilot_name: [PilotProposal, ...]}
        total_budget_jpy: KATSURAGI が用意できる予算
        config: ガードレール設定
        sector_lookup: {ticker: sector}（None なら sector 上限は無効）
        boost_lookup: {ticker: boost 値}（None なら boost 基準は無効）
        blocked_tickers: 例外判定でブロックされた銘柄集合
        pilot_multipliers: 機別の予算上限重み（feedback ループから）。
            None or 1.0 なら通常 (max_pilot_pct)。1.2 なら 20% 拡張、0.5 なら半減。
        account_total_jpy: 口座総額 (N1: G-7 逓減と整合させるため)。
            指定時は params_for_account の cash_floor を使用して min_cash_reserve_pct
            を口座規模に合わせて動的に取得（少額時 0.20 / 大額時 0.10）。
            None なら config.min_cash_reserve_pct を使用（後方互換）。

    Returns:
        OpportunityFillPlan
    """
    config = config or GuardrailConfig()
    sector_lookup = sector_lookup or {}
    boost_lookup = boost_lookup or {}
    blocked_tickers = blocked_tickers or set()
    pilot_multipliers = pilot_multipliers or {}
    market_cap_lookup = market_cap_lookup or {}

    # N1: G-7 逓減と整合した min_cash_reserve_pct を動的取得
    if account_total_jpy is not None and account_total_jpy > 0:
        from trading_agent.risk.params import params_for_account

        effective_cash_reserve = float(params_for_account(account_total_jpy).cash_floor)
    else:
        effective_cash_reserve = config.min_cash_reserve_pct

    plan = OpportunityFillPlan(total_budget=total_budget_jpy)

    # 1) 全 proposal を平坦化。M4(大指針): 補完(source=supplement)は last-resort なので、
    #    実候補(screening/MAGI/ZEELE)を先に、補完を後に並べる。各群内は priority(confidence) 降順。
    #    これで実候補が予算を先取りし、補完は残予算のみ埋める（補完の主経路化を防ぐ）。
    all_props: list[tuple[str, Any]] = []
    for pilot, props in proposals_by_pilot.items():
        for prop in props:
            all_props.append((pilot, prop))
    all_props.sort(
        key=lambda x: (
            str(getattr(x[1], "source", "")) == "supplement",  # 補完は後（False=実候補が先）
            -float(getattr(x[1], "confidence", 0.0)),
        )
    )

    # 2) 上限値（円換算）を予算から計算
    lot_cap = total_budget_jpy * config.max_lot_pct
    source_cap = total_budget_jpy * config.max_source_pct
    sector_cap = total_budget_jpy * config.max_sector_pct
    pilot_cap = total_budget_jpy * config.max_pilot_pct
    large_cap = total_budget_jpy * config.max_large_cap_pct  # 大型(¥1兆超)への配分上限

    spent_by_pilot: dict[str, float] = defaultdict(float)
    spent_by_source: dict[str, float] = defaultdict(float)
    spent_by_sector: dict[str, float] = defaultdict(float)
    spent_by_size: dict[str, float] = defaultdict(float)
    plan.guardrail_hits = defaultdict(int)

    # 3) priority 降順で greedy fill
    for pilot, prop in all_props:
        ticker = prop.ticker
        priority = float(getattr(prop, "confidence", 0.0))
        # 手数料・スリッページ余裕を加味
        base_lot_cost = float(getattr(prop, "min_budget_jpy", 0.0) or 0.0)
        lot_cost = base_lot_cost * (1 + config.fee_margin_pct)
        source = str(getattr(prop, "source", "magi"))
        sector = sector_lookup.get(ticker, "unknown")
        boost = boost_lookup.get(ticker, 0.0)

        # --- 質基準 ---
        if priority < config.min_priority:
            plan.rejected.append({
                "ticker": ticker, "pilot": pilot, "reason": f"priority {priority:.2f} < {config.min_priority}"
            })
            plan.quality_filter_count += 1
            continue
        if boost < config.min_boost:
            plan.rejected.append({
                "ticker": ticker, "pilot": pilot, "reason": f"boost {boost:+.2f} < {config.min_boost}"
            })
            plan.quality_filter_count += 1
            continue
        if ticker in blocked_tickers:
            plan.rejected.append({
                "ticker": ticker, "pilot": pilot, "reason": "例外判定 blocked"
            })
            continue
        if lot_cost <= 0:
            plan.rejected.append({
                "ticker": ticker, "pilot": pilot, "reason": "min_budget 不明"
            })
            continue

        # --- ガードレール ---
        if lot_cost > lot_cap:
            plan.rejected.append({
                "ticker": ticker, "pilot": pilot,
                "reason": f"1銘柄上限超過 (¥{int(lot_cost):,} > ¥{int(lot_cap):,})"
            })
            plan.guardrail_hits["1銘柄"] += 1
            continue
        if spent_by_source[source] + lot_cost > source_cap:
            plan.rejected.append({
                "ticker": ticker, "pilot": pilot,
                "reason": f"1source上限超過 ({source}: ¥{int(spent_by_source[source] + lot_cost):,} > ¥{int(source_cap):,})"
            })
            plan.guardrail_hits["1source"] += 1
            continue
        if sector != "unknown" and spent_by_sector[sector] + lot_cost > sector_cap:
            plan.rejected.append({
                "ticker": ticker, "pilot": pilot,
                "reason": f"1セクター上限超過 ({sector}: ¥{int(spent_by_sector[sector] + lot_cost):,} > ¥{int(sector_cap):,})"
            })
            plan.guardrail_hits["1セクター"] += 1
            continue
        # 大指針 #2: 大型(¥1兆超)+不明(時価総額 lookup 漏れ=検証不能)への配分上限。
        # codex P1: unknown は size_bucket 判定できず大型がすり抜けるので、large と合算して縛る。
        # 中小型優先のサンプリングをすり抜けても fill 側で止める最終防御。
        bucket = size_bucket(market_cap_lookup.get(ticker))
        if bucket in ("large", "unknown"):
            risky_spent = spent_by_size["large"] + spent_by_size["unknown"]
            if risky_spent + lot_cost > large_cap:
                plan.rejected.append({
                    "ticker": ticker, "pilot": pilot, "size_bucket": bucket,
                    "reason": (
                        f"大型/不明上限超過 ({bucket}: ¥{int(risky_spent + lot_cost):,} > "
                        f"¥{int(large_cap):,}; 中小型優先)"
                    )
                })
                plan.guardrail_hits["大型/不明上限"] += 1
                continue
        # 1 機上限: feedback ループから受け取った multiplier で拡張/縮小
        # 機別に勝率が高い→ 1.2、低い→ 0.5、データ不足→ 1.0（中立）
        pilot_mul = float(pilot_multipliers.get(pilot, 1.0))
        adjusted_pilot_cap = pilot_cap * pilot_mul
        if spent_by_pilot[pilot] + lot_cost > adjusted_pilot_cap:
            plan.rejected.append({
                "ticker": ticker, "pilot": pilot,
                "reason": (
                    f"1機上限超過 ({pilot}: ¥{int(spent_by_pilot[pilot] + lot_cost):,} > "
                    f"¥{int(adjusted_pilot_cap):,}; mul={pilot_mul:.2f})"
                )
            })
            plan.guardrail_hits["1機"] += 1
            continue

        # 残予算チェック
        remaining = total_budget_jpy - plan.total_planned
        if lot_cost > remaining:
            plan.rejected.append({
                "ticker": ticker, "pilot": pilot,
                "reason": f"残予算不足 (¥{int(remaining):,})"
            })
            continue

        # cash 余力ハード制約: 買付後の cash 残が min_cash_reserve_pct を下回るなら拒否
        # 「上昇株が来た時の身動き」を確保するための死守ライン
        # N1: account_total_jpy 指定時は G-7 逓減と整合した cash_floor を使う
        cash_floor = total_budget_jpy * effective_cash_reserve
        cash_after = remaining - lot_cost
        if cash_after < cash_floor:
            plan.rejected.append({
                "ticker": ticker, "pilot": pilot,
                "reason": f"cash 最低保持を割り込む (買付後 ¥{int(cash_after):,} < 死守 ¥{int(cash_floor):,})"
            })
            plan.guardrail_hits["cash保持"] += 1
            continue

        # --- 採用 ---
        plan.selected.append({
            "ticker": ticker, "pilot": pilot,
            "lot_cost_jpy": lot_cost,
            "priority": priority,
            "boost": boost,
            "source": source,
            "sector": sector,
            "size_bucket": bucket,  # small/mid/large（中小型偏重の可視化・再発検知）
        })
        spent_by_pilot[pilot] += lot_cost
        spent_by_source[source] += lot_cost
        if sector != "unknown":
            spent_by_sector[sector] += lot_cost
        spent_by_size[bucket] += lot_cost
        plan.total_planned += lot_cost

    # 4) 機別予算を集計（paper_fill_approved に渡す用）
    plan.per_pilot_budget = dict(spent_by_pilot)
    plan.cash_remaining = total_budget_jpy - plan.total_planned

    _log.info(
        "opportunity_fill_planned",
        selected=len(plan.selected),
        rejected=len(plan.rejected),
        guardrail_hits=dict(plan.guardrail_hits),
        quality_filter=plan.quality_filter_count,
        planned_jpy=int(plan.total_planned),
        remaining_jpy=int(plan.cash_remaining),
    )
    return plan
