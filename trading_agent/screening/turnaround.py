"""S7：V字（ターンアラウンド）の質判定。RESEARCH_METHODS 領域3-A。

研究の核心：**V字＝「底にいる」でなく「底から反転の点火」**。
- ①業績の底（極小/赤字マージン）※これ単独は買わない＝value trap
- ②反転の点火（**earnings acceleration＝成長率の加速**・最重要）／3期無ければマージンYoY改善で代替
- ③株価の底打ち転換（Stage1→2＝BALTHASARのシグナルで近似：GC在/DC不在）
- ④生存性（M/F/Z＝信用性で代替・倒産しない）
判定：①〜④が揃えば **v_candidate**。①あるが②欠＝**value_trap**。①無し＝**not_applicable**。
全てコード（数値はコード・LLM非関与）。欠損は na。Value×Momentum両立(Asness)＝②と③のAND。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from trading_agent.screening.credibility import CredibilityResult
from trading_agent.screening.financials import Financials, PeriodFinancials


@dataclass
class TurnaroundResult:
    zone: str  # v_candidate / value_trap / not_applicable / na
    # 4軸：bottom / ignition / price_turn / survival
    axes: dict[str, bool | None] = field(default_factory=dict)
    note: str = ""


def _op_margin(p: PeriodFinancials) -> float | None:
    if p.ebit is not None and p.revenue:
        return p.ebit / p.revenue
    return None


def _growth(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev)


def _ignition(fin: Financials) -> bool | None:
    """反転の点火。3期あれば earnings acceleration（成長率の加速）、無ければマージンYoY改善。

    v2.5 TASK-Z9: 3 期分が無くても 2 期 + マージン改善で代替判定（既存挙動）。
    将来は四半期版（quarterly_*）の取得を追加して短期検証にも対応可能（fallback hook）。
    """
    t, p = fin.current, fin.prior
    if p is None:
        return None
    if fin.has_three_periods():
        g_now = _growth(t.net_income, p.net_income)
        g_prev = _growth(p.net_income, fin.prior2.net_income)  # type: ignore[union-attr]
        if g_now is not None and g_prev is not None:
            return g_now > g_prev  # 成長率が加速（二階微分>0）
    # 代替：営業マージンが前年より改善（オペレーティング・モメンタム回復）
    m_t, m_p = _op_margin(t), _op_margin(p)
    if m_t is not None and m_p is not None:
        return m_t > m_p
    return None


def assess_turnaround(
    fin: Financials,
    *,
    signals: list[str] | None = None,
    credibility: CredibilityResult | None = None,
) -> TurnaroundResult:
    """V字の質を4軸で判定する。"""
    signals = signals or []
    t = fin.current

    # v2.5 TASK-Z14: bottom 閾値を定数化
    _BOTTOM_OP_MARGIN_THRESHOLD = 0.05  # 営業マージン 5% 未満で「底」と判定
    # ① 業績の底：営業マージンが極小（<5%）または赤字
    m = _op_margin(t)
    bottom = (m < _BOTTOM_OP_MARGIN_THRESHOLD) if m is not None else None

    # ② 反転の点火
    ignition = _ignition(fin)

    # ③ 株価の底打ち転換（Stage2 近似）：GC在 かつ DC不在
    if signals:
        price_turn: bool | None = ("golden_cross" in signals) and ("death_cross" not in signals)
    else:
        price_turn = None

    # ④ 生存性：信用性で倒産/粉飾リスクが無い（Z/F が risk でない）
    survival: bool | None = None
    if credibility is not None:
        survival = credibility.z_score.zone != "risk" and credibility.f_score.zone != "risk"

    axes = {
        "bottom": bottom, "ignition": ignition, "price_turn": price_turn, "survival": survival
    }

    if bottom is None:
        return TurnaroundResult("na", axes, "業績の底を判定できず（マージン欠損）")
    if not bottom:
        return TurnaroundResult("not_applicable", axes, "業績は底でない（対象外）")
    # 底にいる：点火していなければ value trap
    if ignition is False or ignition is None:
        reason = "底だが反転の点火なし＝value trap警戒（底にいるだけ）"
        return TurnaroundResult("value_trap", axes, reason)
    # 底＋点火：株価転換と生存性が揃えば V字候補（揃わなければ value trap 寄り）
    if price_turn and survival is not False:
        return TurnaroundResult("v_candidate", axes, "底＋反転の点火＋株価転換＝V字候補")
    miss = "株価未転換" if not price_turn else "生存性に難"
    return TurnaroundResult("value_trap", axes, f"底＋点火だが{miss}＝確度不足")
