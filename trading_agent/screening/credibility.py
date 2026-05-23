"""S5：信用性フィルタ（Beneish M / Piotroski F / Altman Z）。RESEARCH_METHODS 領域1。

D-17（不正企業＝ゼロ化）への最重要防御。**3手法とも純粋にコード計算・LLM不使用**（原則4）。
- 一致を求めない：各々が別の側面を見る独立warnフラグ（一致率は低い＝別物）。
- ハード除外でなく**ソフト警戒**（誤検出あり）。閾値超は `credibility_flag=warn`。
- **UIに数値を出さない**（SCORE:NONE思想）：内部はスコア、外向きは定性ゾーン（安全/グレー/危険）。
- 業種注意：M/Z は金融・REIT に当てはまらない（除外）。Z は製造業向け（非製造は参考）。
  日本株は米国閾値をそのまま使わず「参考」に留める（zone 判定は同式・解釈は控えめ）。
データ：2期分財務（`screening.financials.Financials`）。欠損は na（捏造しない＝R4）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from trading_agent.screening.financials import Financials, PeriodFinancials

# M/Z が当てはまらない業種（会計構造が異なる）
_EXCLUDED_SECTORS = {
    "Financial Services", "Financial", "Banks", "Bank", "Insurance",
    "Real Estate", "REIT",
}


@dataclass
class ScoreResult:
    name: str  # "Beneish M" / "Piotroski F" / "Altman Z"
    value: float | None  # 内部スコア（UIには出さない）
    zone: str  # "safe" / "grey" / "risk" / "na"
    note: str


@dataclass
class CredibilityResult:
    m_score: ScoreResult
    f_score: ScoreResult
    z_score: ScoreResult
    credibility_flag: str  # "ok" / "warn"（防御層 credibility_flag へ）
    warnings: list[str] = field(default_factory=list)  # 危険ゾーンの独立フラグ列


def _ratio(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _gross_margin(p: PeriodFinancials) -> float | None:
    if p.gross_profit is not None:
        return _ratio(p.gross_profit, p.revenue)
    if p.revenue is not None and p.cogs is not None:
        return _ratio(p.revenue - p.cogs, p.revenue)
    return None


def beneish_m_score(fin: Financials) -> ScoreResult:
    """8比率の Beneish M-Score（前期比中心）。M>-1.78 で操作の疑い。"""
    if not fin.has_two_periods():
        return ScoreResult("Beneish M", None, "na", "2期分の財務が無く算定不能")
    t, p = fin.current, fin.prior
    assert p is not None

    dsri = _ratio(_ratio(t.receivables, t.revenue), _ratio(p.receivables, p.revenue))
    gmi = _ratio(_gross_margin(p), _gross_margin(t))
    aqi_t = _ratio((t.current_assets or 0) + (t.ppe or 0), t.total_assets)
    aqi_p = _ratio((p.current_assets or 0) + (p.ppe or 0), p.total_assets)
    aqi = _ratio(1 - aqi_t, 1 - aqi_p) if aqi_t is not None and aqi_p is not None else None
    sgi = _ratio(t.revenue, p.revenue)
    depi_t = _ratio(t.depreciation, (t.depreciation or 0) + (t.ppe or 0))
    depi_p = _ratio(p.depreciation, (p.depreciation or 0) + (p.ppe or 0))
    depi = _ratio(depi_p, depi_t)
    sgai = _ratio(_ratio(t.sga, t.revenue), _ratio(p.sga, p.revenue))
    lev_t = _ratio((t.long_term_debt or 0) + (t.current_liabilities or 0), t.total_assets)
    lev_p = _ratio((p.long_term_debt or 0) + (p.current_liabilities or 0), p.total_assets)
    lvgi = _ratio(lev_t, lev_p)
    tata = _ratio(
        (t.net_income - t.operating_cashflow)
        if t.net_income is not None and t.operating_cashflow is not None
        else None,
        t.total_assets,
    )

    parts = {"DSRI": dsri, "GMI": gmi, "AQI": aqi, "SGI": sgi, "TATA": tata}
    missing = [k for k, v in parts.items() if v is None]
    if missing:  # 主要変数が欠けたら算定不能（埋めない）
        return ScoreResult("Beneish M", None, "na", f"主要変数欠損：{'/'.join(missing)}")

    m = (
        -4.84
        + 0.920 * dsri  # type: ignore[operator]
        + 0.528 * gmi  # type: ignore[operator]
        + 0.404 * aqi  # type: ignore[operator]
        + 0.892 * sgi  # type: ignore[operator]
        + 0.115 * (depi or 1.0)
        - 0.172 * (sgai or 1.0)
        + 4.679 * tata  # type: ignore[operator]
        - 0.327 * (lvgi or 1.0)
    )
    if m > -1.78:
        zone, note = "risk", "利益操作の疑い（M>-1.78）。売掛金/発生高など要警戒"
    elif m > -2.22:
        zone, note = "grey", "グレー（-2.22<M<-1.78）"
    else:
        zone, note = "safe", "操作の兆候は弱い（M<-2.22）"
    return ScoreResult("Beneish M", round(m, 3), zone, note)


def piotroski_f_score(fin: Financials) -> ScoreResult:
    """9項目の Piotroski F-Score（0-9）。7-9良好/5-6可/0-4危険。"""
    if not fin.has_two_periods():
        return ScoreResult("Piotroski F", None, "na", "2期分の財務が無く算定不能")
    t, p = fin.current, fin.prior
    assert p is not None
    roa_t = _ratio(t.net_income, t.total_assets)
    roa_p = _ratio(p.net_income, p.total_assets)
    cr_t = _ratio(t.current_assets, t.current_liabilities)
    cr_p = _ratio(p.current_assets, p.current_liabilities)
    lev_t = _ratio(t.long_term_debt, t.total_assets)
    lev_p = _ratio(p.long_term_debt, p.total_assets)
    gm_t, gm_p = _gross_margin(t), _gross_margin(p)
    at_t = _ratio(t.revenue, t.total_assets)
    at_p = _ratio(p.revenue, p.total_assets)

    checks: list[bool | None] = [
        (roa_t > 0) if roa_t is not None else None,
        (t.operating_cashflow > 0) if t.operating_cashflow is not None else None,
        (roa_t > roa_p) if roa_t is not None and roa_p is not None else None,
        (t.operating_cashflow > t.net_income)
        if t.operating_cashflow is not None and t.net_income is not None
        else None,
        (lev_t < lev_p) if lev_t is not None and lev_p is not None else None,
        (cr_t > cr_p) if cr_t is not None and cr_p is not None else None,
        (t.shares <= p.shares) if t.shares is not None and p.shares is not None else None,
        (gm_t > gm_p) if gm_t is not None and gm_p is not None else None,
        (at_t > at_p) if at_t is not None and at_p is not None else None,
    ]
    usable = [c for c in checks if c is not None]
    if len(usable) < 5:  # 半分も判定できないなら na
        return ScoreResult("Piotroski F", None, "na", "判定項目が不足（欠損多）")
    score = sum(1 for c in usable if c)
    if score >= 7:
        zone, note = "safe", f"財務良好（{score}/{len(usable)}項目）"
    elif score >= 5:
        zone, note = "grey", f"可（{score}/{len(usable)}）"
    else:
        zone, note = "risk", f"財務の質に難（{score}/{len(usable)}）＝value trap警戒"
    return ScoreResult("Piotroski F", float(score), zone, note)


def altman_z_score(fin: Financials) -> ScoreResult:
    """5比率の Altman Z-Score。Z>2.99安全/1.81-2.99グレー/<1.81危険。要 market_cap。"""
    t = fin.current
    a = _ratio(t.working_capital, t.total_assets)
    b = _ratio(t.retained_earnings, t.total_assets)
    c = _ratio(t.ebit, t.total_assets)
    d = _ratio(fin.market_cap, t.total_liabilities)
    e = _ratio(t.revenue, t.total_assets)
    parts = {"WC/TA": a, "RE/TA": b, "EBIT/TA": c, "MktCap/TL": d, "Sales/TA": e}
    missing = [k for k, v in parts.items() if v is None]
    if missing:
        return ScoreResult("Altman Z", None, "na", f"変数欠損：{'/'.join(missing)}")
    z = 1.2 * a + 1.4 * b + 3.3 * c + 0.6 * d + 1.0 * e  # type: ignore[operator]
    if z > 2.99:
        zone, note = "safe", "倒産リスク低（Z>2.99）"
    elif z >= 1.81:
        zone, note = "grey", "グレー（1.81≤Z≤2.99）"
    else:
        zone, note = "risk", "倒産リスク警戒（Z<1.81）"
    return ScoreResult("Altman Z", round(z, 2), zone, note)


def assess_credibility(fin: Financials, *, sector: str | None = None) -> CredibilityResult:
    """3手法を独立に評価し、防御層向けの credibility_flag（ok/warn）に集約する。

    M/Z は金融・REIT で当てはまらない＝na（除外）。**一致は求めない**：どれか1つでも危険なら warn。
    F-Score は質評価（MELCHIORへ）。低Fも value trap 警戒として warn に寄与。
    """
    excluded = sector is not None and sector in _EXCLUDED_SECTORS
    if excluded:
        m = ScoreResult("Beneish M", None, "na", f"業種除外（{sector}）")
        z = ScoreResult("Altman Z", None, "na", f"業種除外（{sector}）")
    else:
        m = beneish_m_score(fin)
        z = altman_z_score(fin)
    f = piotroski_f_score(fin)

    warnings: list[str] = []
    if m.zone == "risk":
        warnings.append(f"M-Score:{m.note}")
    if z.zone == "risk":
        warnings.append(f"Z-Score:{z.note}")
    if f.zone == "risk":
        warnings.append(f"F-Score:{f.note}")
    flag = "warn" if warnings else "ok"
    return CredibilityResult(
        m_score=m, f_score=f, z_score=z, credibility_flag=flag, warnings=warnings
    )
