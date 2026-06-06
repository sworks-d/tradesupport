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
from trading_agent.screening.financials import (
    STRUCTURED_EVENTS_PARSER_VERSION,
    Financials,
    ForecastPoint,
    PeriodFinancials,
)


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


# ============================================================
# PIPELINE v3 Track B / A prime: J-Quants 由来 earnings signal_tags（record-only）
# ============================================================


def derive_earnings_signal_tags(
    fin: Financials | None, asof: str | None = None
) -> tuple[list[str], dict]:
    """J-Quants 財務から earnings 系 signal_tags を導出する（record-only・A prime）。

    `_ignition`（決算 net_income の二階微分>0＝成長加速、または営業マージン YoY 改善）が
    立てば `earnings_accel`。yfinance 由来の dead な代理（zeele_curator）に対し、本番 magi_verify が
    既に引いている J-Quants `fin` を再利用するため JP 中小型でも実発火が期待できる（¥0・新規fetch無し）。

    ※ これは真の PEAD ではない（開示日時 + 発表時 surprise + post-announcement window が無い）。
      開示 recency は fin.current の期末で近似。真 PEAD は pead_candidate/pead_confirmed で別実装。

    返り値: (tags, evidence)。evidence は「なぜそのタグが付いたか」の監査用メタ（source/asof/値）。
    取得失敗・データ欠損時は空（推測しない・H10）。
    """
    tags: list[str] = []
    evidence: dict = {}
    if fin is None:
        return tags, evidence
    try:
        ignition = assess_turnaround(fin).axes.get("ignition")
    except Exception:
        return tags, evidence
    if ignition is True:
        tags.append("earnings_accel")
        cur = fin.current
        prior = fin.prior
        evidence["earnings_accel"] = {
            "source": getattr(fin, "source", "jquants"),
            "asof": asof or getattr(cur, "period", None),  # 期末日（開示 recency の近似）
            "net_income": getattr(cur, "net_income", None),
            "prior_net_income": getattr(prior, "net_income", None) if prior else None,
        }
    return tags, evidence


# (b) dividend change: 発行株数がこの比率を超えて変動したら split 疑い → per-share 比較を抑止
# （per-share 配当は split で機械的に変わるため・false-data 回避）。
_SPLIT_SHARE_TOLERANCE = 0.10


def _latest_point_with(history: list[ForecastPoint], attr: str) -> ForecastPoint | None:
    """`attr` が非 None な最新（開示日最大）の ForecastPoint。無ければ None。"""
    cands = [p for p in history if getattr(p, attr) is not None and p.disc_date is not None]
    return max(cands, key=lambda p: p.disc_date) if cands else None  # type: ignore[arg-type,return-value]


def derive_structured_event_tags(fin: Financials | None) -> tuple[list[str], dict]:
    """J-Quants 予想/配当の**開示間比較**から構造化イベントタグを導出（record-only・(b) MVP）。

    master/codex 承認の verified-safe な2系統のみ:
      - forecast revision: `FNP`(当期予想純利益)を**同一 `CurFYEn`** の開示間で比較。
        最新 > 直前 = `event_upward_revision` / 最新 < 直前 = `event_downward_revision`。
        asof = 最新開示 `DiscDate`。
      - dividend change: 最新の `FDivAnn`(当期予想年配) vs **前 FY** の `DivAnn`(前期実績年配)。
        高い=`event_dividend_hike` / 低い=`event_dividend_cut`。
        **`ShOutFY` 変動>10% は split 疑いで抑止**（per-share 交絡回避）。

    buyback/dilution は share-count が split/消却と交絡=false-data risk のため defer（不実装）。
    PIT: `DiscDate` 基準。期末日(`CurFYEn`)を開示日扱いしない。値欠損・比較不能は無タグ（H10）。
    返り値 (tags, evidence)。evidence に source / disc_date(before→after) / field /
    値(before→after) / parser_version を残す（監査・再現）。
    """
    tags: list[str] = []
    evidence: dict = {}
    if fin is None or not getattr(fin, "forecast_history", None):
        return tags, evidence
    history = sorted(
        (p for p in fin.forecast_history if p.disc_date is not None),
        key=lambda p: p.disc_date,  # type: ignore[arg-type,return-value]
    )
    src = getattr(fin, "source", "jquants")

    # --- forecast revision: 同一 CurFYEn の最新2開示で FNP を比較 ---
    latest_f = _latest_point_with(history, "forecast_profit")
    if latest_f is not None and latest_f.cur_fy_end is not None:
        same_fy = [
            p for p in history
            if p.cur_fy_end == latest_f.cur_fy_end and p.forecast_profit is not None
        ]
        if len(same_fy) >= 2:
            prev_f, cur_f = same_fy[-2], same_fy[-1]
            before, after = prev_f.forecast_profit, cur_f.forecast_profit
            if before is not None and after is not None and after != before:
                tag = "event_upward_revision" if after > before else "event_downward_revision"
                tags.append(tag)
                evidence[tag] = {
                    "source": src,
                    "field": "FNP",
                    "cur_fy_end": cur_f.cur_fy_end,
                    "disc_date_before": prev_f.disc_date.isoformat() if prev_f.disc_date else None,
                    "disc_date_after": cur_f.disc_date.isoformat() if cur_f.disc_date else None,
                    "before": before,
                    "after": after,
                    "parser_version": STRUCTURED_EVENTS_PARSER_VERSION,
                }

    # --- dividend change: 最新 FDivAnn vs 前 FY の DivAnn（split ガード） ---
    latest_fdiv = _latest_point_with(history, "forecast_div_annual")
    if latest_fdiv is not None and latest_fdiv.cur_fy_end is not None:
        prior_pts = [
            p for p in history
            if p.result_div_annual is not None
            and p.cur_fy_end is not None
            and p.cur_fy_end < latest_fdiv.cur_fy_end  # 前 FY（同一 FY を除外）
        ]
        if prior_pts:
            prior_div = prior_pts[-1]
            after, before = latest_fdiv.forecast_div_annual, prior_div.result_div_annual
            sh_after, sh_before = latest_fdiv.shares_outstanding, prior_div.shares_outstanding
            # codex P1: per-share 配当は split/併合で機械的に変動する。株数が両期とも取れない限り
            # split を否定できない＝タグを出さない（欠損は無タグ H10）。株数既知時のみ split 判定。
            shares_known = sh_after is not None and sh_before is not None and sh_before > 0
            split_suspected = (
                shares_known and abs(sh_after / sh_before - 1.0) > _SPLIT_SHARE_TOLERANCE
            )
            if (
                before is not None
                and after is not None
                and after != before
                and shares_known
                and not split_suspected
            ):
                tag = "event_dividend_hike" if after > before else "event_dividend_cut"
                tags.append(tag)
                evidence[tag] = {
                    "source": src,
                    "field": "FDivAnn_vs_DivAnn",
                    "fy_before": prior_div.cur_fy_end,
                    "fy_after": latest_fdiv.cur_fy_end,
                    "disc_date_before": (
                        prior_div.disc_date.isoformat() if prior_div.disc_date else None
                    ),
                    "disc_date_after": (
                        latest_fdiv.disc_date.isoformat() if latest_fdiv.disc_date else None
                    ),
                    "before": before,
                    "after": after,
                    "split_guarded": False,
                    "parser_version": STRUCTURED_EVENTS_PARSER_VERSION,
                }
    return tags, evidence
