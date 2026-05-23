"""ポートフォリオ規律ゲート（規律層 G-3）。MAGIの外側で集中・DDを制約する。

MAGIが各候補を独立に「買い」と出しても、ポートフォリオ全体では：
- **DD新規停止**：高値から−15%でクールダウン（全新規をblock）。
- **同時保有上限**：保有＋採用が5銘柄を超えたらblock（満員）。
- **テーマ/セクター集中**：同一セクターが2銘柄 or 口座30%を超える分はblock/reduce
  （AI3銘柄＝実質1ベットを止める）。

判断（買う/売る）はしない＝MAGIの仕事。ここは「いくつ・どれだけ・止めるか」の**規律**。
全てコード（数値はコード・LLM非関与）。候補は順に評価（上位＝MAGI確信度順を想定）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from trading_agent.risk.params import DEFAULT_RISK, RiskParams


@dataclass
class Candidate:
    ticker: str
    sector: str
    amount_jpy: float  # サイジング(R-mult)の推奨額


@dataclass
class Held:
    ticker: str
    sector: str
    amount_jpy: float


@dataclass
class GuardVerdict:
    ticker: str
    action: str  # "allow" / "reduce" / "block"
    amount_jpy: float  # 規律適用後の額（reduceで縮む・blockは0）
    reason: str


@dataclass
class PortfolioGuardResult:
    halt_new: bool
    halt_reason: str
    verdicts: list[GuardVerdict] = field(default_factory=list)

    def allowed(self) -> list[GuardVerdict]:
        return [v for v in self.verdicts if v.action != "block" and v.amount_jpy > 0]


def evaluate_portfolio_guard(
    candidates: Iterable[Candidate],
    *,
    held: Iterable[Held] = (),
    account_total_jpy: float,
    peak_total_jpy: float | None = None,
    params: RiskParams = DEFAULT_RISK,
) -> PortfolioGuardResult:
    """候補に集中・DD・保有数の規律を適用する（順に貪欲評価）。"""
    held_list = list(held)
    peak = peak_total_jpy if peak_total_jpy is not None else account_total_jpy

    # DD新規停止（高値から-X%）
    drawdown = (peak - account_total_jpy) / peak if peak > 0 else 0.0
    if drawdown >= params.drawdown_halt:
        return PortfolioGuardResult(
            halt_new=True,
            halt_reason=f"ドローダウン{drawdown:.0%}（高値比）が停止閾値{params.drawdown_halt:.0%}に到達。新規停止",
            verdicts=[
                GuardVerdict(c.ticker, "block", 0.0, "DD新規停止中") for c in candidates
            ],
        )

    # 現在のセクター別件数・金額／保有数（保有から開始）
    sector_count: dict[str, int] = {}
    sector_amount: dict[str, float] = {}
    for h in held_list:
        sector_count[h.sector] = sector_count.get(h.sector, 0) + 1
        sector_amount[h.sector] = sector_amount.get(h.sector, 0.0) + h.amount_jpy
    positions = len(held_list)
    theme_cap_jpy = account_total_jpy * params.max_theme_weight

    verdicts: list[GuardVerdict] = []
    for c in candidates:
        if c.amount_jpy <= 0:
            verdicts.append(
                GuardVerdict(c.ticker, "block", 0.0, "サイジング0（予算/リスクで購入不可）")
            )
            continue
        if positions >= params.max_positions:
            verdicts.append(
                GuardVerdict(c.ticker, "block", 0.0, f"同時保有上限{params.max_positions}に到達")
            )
            continue
        if sector_count.get(c.sector, 0) >= params.max_per_theme:
            verdicts.append(
                GuardVerdict(
                    c.ticker, "block", 0.0,
                    f"セクター『{c.sector}』が上限{params.max_per_theme}銘柄に到達（集中回避）",
                )
            )
            continue

        # セクター金額上限（30%）：超過分は reduce
        room = theme_cap_jpy - sector_amount.get(c.sector, 0.0)
        if room <= 0:
            verdicts.append(
                GuardVerdict(
                    c.ticker, "block", 0.0,
                    f"セクター『{c.sector}』が口座{params.max_theme_weight:.0%}上限に到達",
                )
            )
            continue
        if c.amount_jpy > room:
            amt = round(room)
            verdicts.append(
                GuardVerdict(
                    c.ticker, "reduce", amt,
                    f"セクター『{c.sector}』{params.max_theme_weight:.0%}枠に合わせ¥{amt:,}へ縮小",
                )
            )
        else:
            amt = c.amount_jpy
            verdicts.append(GuardVerdict(c.ticker, "allow", amt, "規律クリア"))

        positions += 1
        sector_count[c.sector] = sector_count.get(c.sector, 0) + 1
        sector_amount[c.sector] = sector_amount.get(c.sector, 0.0) + amt

    return PortfolioGuardResult(halt_new=False, halt_reason="", verdicts=verdicts)
