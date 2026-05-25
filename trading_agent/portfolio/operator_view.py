"""オペレーター意思決定ビュー：永続化された MAGI 検証から GENDO 推奨カードを組む（P4-3コア）。

`awaiting`（MAGI検証済・決裁待ち）の decision について、永続4表（JudgeVerdict / Verification /
SplitPattern / CommanderRec）を読み、規律層サイジングを当てて `GendoCard` を作る。ネット非依存
（価格・市場・現金は注入）＝テスト可能。run_paper（オーケストレーション）がこれを使って提示する。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.magi.commander import CommanderResult
from trading_agent.magi.defense import VerificationResult
from trading_agent.magi.gendo import GendoCard, gendo_recommend
from trading_agent.magi.integration import SplitResult
from trading_agent.models.decisions import Decision
from trading_agent.models.magi import CommanderRec, JudgeVerdict, SplitPattern, Verification
from trading_agent.portfolio.sizing import recommend_position
from trading_agent.risk.params import DEFAULT_RISK, RiskParams

PriceLookup = Callable[[str], float | None]  # ticker → 想定約定価格(JPY・翌寄り)。不可は None
IsJpLookup = Callable[[str], bool]


@dataclass
class OperatorCard:
    decision_id: int
    ticker: str
    gendo_stance: str | None  # 碇の構え（推し/要検討/静観 等）
    card: GendoCard


def _verification(row: Verification) -> VerificationResult:
    return VerificationResult(
        figures_checked=row.figures_checked,
        credibility_flag=row.credibility_flag,
        time_ok=row.time_ok,
        gendo_compliant=row.gendo_compliant,
        unverified_claims=list(row.unverified_claims or []),
        default_hold=row.default_hold,
    )


def _commander(row: CommanderRec) -> CommanderResult:
    return CommanderResult(
        recommendation=row.recommendation,
        counter_argument=row.counter_argument,
        magi_compliant=row.magi_compliant,
        src_note=row.src_note,
    )


def _split(row: SplitPattern | None) -> SplitResult:
    if row is None:
        return SplitResult(agree_count=0, total=0, label="", interpretation="")
    return SplitResult(
        agree_count=row.agree_count, total=row.total,
        label=row.label, interpretation=row.interpretation,
    )


def operator_cards(
    engine: Engine,
    *,
    price_lookup: PriceLookup,
    is_jp_lookup: IsJpLookup,
    cash_jpy: float,
    positions_value_jpy: float = 0.0,
    params: RiskParams = DEFAULT_RISK,
) -> list[OperatorCard]:
    """awaiting decision を GENDO 推奨カード化（守り主導・攻めは情報のみ=offense_strong=False）。"""
    out: list[OperatorCard] = []
    with Session(engine) as s:
        decisions = s.exec(select(Decision).where(col(Decision.status) == "awaiting")).all()
        for d in decisions:
            if d.id is None:
                continue
            vs = list(s.exec(select(JudgeVerdict).where(col(JudgeVerdict.decision_id) == d.id)))
            vr_row = s.exec(
                select(Verification).where(col(Verification.decision_id) == d.id)
            ).first()
            cr_row = s.exec(
                select(CommanderRec).where(col(CommanderRec.decision_id) == d.id)
            ).first()
            sp_row = s.exec(
                select(SplitPattern).where(col(SplitPattern.decision_id) == d.id)
            ).first()
            if vr_row is None or cr_row is None:
                continue
            price = price_lookup(d.ticker)
            sizing = None
            if price is not None and price > 0:
                sizing = recommend_position(
                    price_jpy=price, total_assets_jpy=cash_jpy + positions_value_jpy,
                    cash_jpy=cash_jpy, is_jp=is_jp_lookup(d.ticker),
                    stop_pct=params.default_stop_pct, params=params,
                )
            card = gendo_recommend(
                vs, _split(sp_row), _verification(vr_row), _commander(cr_row),
                credibility_flag=vr_row.credibility_flag, offense_strong=False, sizing=sizing,
            )
            out.append(OperatorCard(d.id, d.ticker, d.gendo_stance, card))
    return out


def render_card(oc: OperatorCard) -> str:
    """1銘柄1カードの文字列（初心者向け）。"""
    c = oc.card
    return "\n".join(
        [
            f"━━ {oc.ticker}（碇の構え：{oc.gendo_stance or '—'}）━━",
            f" GENDOの推奨：{c.action}" + (f"（{c.sleeve}）" if c.sleeve != "—" else ""),
            f"  なぜ：{c.reason}",
            f"  {c.counter}",
            f"  従うなら：{c.guardrail}",
            f"  確信度：守り{c.defense_confidence}／攻め{c.offense_confidence}",
            f"  ひとこと：{c.learn_note}",
            "  ※決めるのはあなた（GENDOは推奨のみ）。",
        ]
    )
