"""zeele-curator（ZEELE 車線の銘柄キュレーター）。

screening_results を週次ビンに分け、**3週連続で screening 入賞** した銘柄を
ZEELE プールに entry する。1日の bump で出入りしない設計（[[zeele-magi-role-split]]）。

入賞の継続性は「直近21日を7日ずつ3バケットに区切り、全バケットで passed=True が
1件以上あること」で判定する。週末・祝日でスクリーニングが走らない日があっても
バケットに1件入っていれば「その週は入賞」と見なす（吸収）。

ZEELE 入り後は最新の入賞時の preset / composite_score / 関連 topic を保持する。
4週連続で screening_results に登場しなくなった銘柄は is_active=False に降格。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import Field
from sqlmodel import Session, col, select

from trading_agent.agents.base import Agent, AgentInput, AgentOutput
from trading_agent.agents.context import AgentContext
from trading_agent.models.signals import ScreeningResult
from trading_agent.models.topics import Topic
from trading_agent.models.universe import Universe
from trading_agent.models.zeele import ZeeleState
from trading_agent.utils.logger import get_logger
from trading_agent.utils.time_utils import utcnow

# preset 推定マップ：screening の matched_strategies / theme_details → ZEELE preset
# screening 側は "v_shape" / "theme" の2系統のみだが、ZEELE UI は7プリセットを持つ。
# 当面は粗いマッピングにとどめ、後で screening 側を拡張したら細分化する。
_PRESET_FROM_STRATEGY: dict[str, str] = {
    "v_shape": "pullback",  # V字回復 ≒ 押し目反転
    "theme": "momentum",    # テーマモメンタム
}
# screening_results に matched_strategies が無いので、composite_score 由来の details から推測する
_DEFAULT_PRESET = "momentum"

# 3週連続を判定するための窓
_LOOKBACK_DAYS = 21
_WEEK_DAYS = 7
_QUALIFICATION_WEEKS = 3  # 3週連続で entry 確定

# 降格判定：4週以上、screening にすら登場しなかった ZEELE 銘柄は inactive
_DEACTIVATION_DAYS = 28


class ZeeleCuratorInput(AgentInput):
    """zeele_curator の入力。"""

    # 評価基準日（既定：今日）。テスト時に固定するために注入可能。
    as_of: dt.date | None = None
    # 連続入賞の閾値。テスト時に短縮するために調整可能。
    qualification_weeks: int = _QUALIFICATION_WEEKS


class ZeeleCuratorOutput(AgentOutput):
    """zeele_curator の出力。"""

    candidates: list[dict[str, Any]] = Field(default_factory=list)
    newly_entered: list[str] = Field(default_factory=list)
    still_active: list[str] = Field(default_factory=list)
    deactivated: list[str] = Field(default_factory=list)


def _weekly_buckets(
    rows: list[ScreeningResult], *, as_of: dt.date, weeks: int = _QUALIFICATION_WEEKS
) -> dict[str, list[bool]]:
    """ticker → "直近 weeks 週のバケットそれぞれで passed が1件以上あったか" のリスト。

    バケット0 = 直近7日（as_of-6 .. as_of）、バケット1 = その前7日、…の順。
    各バケットに passed=True が1件以上あれば True。
    """
    buckets: dict[str, list[bool]] = {}
    for r in rows:
        if not r.screening_passed:
            continue
        # ZeeleCuratorInput.as_of は date。screened_at は datetime。as_of との差で日数を測る。
        days_ago = (as_of - r.screened_at.date()).days
        if days_ago < 0 or days_ago >= weeks * _WEEK_DAYS:
            continue
        bucket = days_ago // _WEEK_DAYS  # 0..weeks-1
        bs = buckets.setdefault(r.ticker, [False] * weeks)
        bs[bucket] = True
    return buckets


def _is_qualified(bucket_flags: list[bool], required_weeks: int) -> bool:
    """直近 required_weeks 週すべてに passed が記録されていれば True。"""
    return all(bucket_flags[:required_weeks])


def _latest_screening_by_ticker(
    rows: list[ScreeningResult],
) -> dict[str, ScreeningResult]:
    """ticker → 最新の screening_results 行。"""
    latest: dict[str, ScreeningResult] = {}
    for r in rows:
        cur = latest.get(r.ticker)
        if cur is None or r.screened_at > cur.screened_at:
            latest[r.ticker] = r
    return latest


def _infer_preset(row: ScreeningResult) -> str:
    """screening_results 1行から ZEELE preset を推定する。

    現状 screening 側は v_shape / theme 2系統のみ：
    - V字回復スコアが上回る → "pullback"
    - テーマスコアが上回る → "momentum"
    今後 screening を拡張したらここを細分化する。
    """
    if row.v_shape_score > row.theme_score:
        return _PRESET_FROM_STRATEGY["v_shape"]
    if row.theme_score > 0:
        return _PRESET_FROM_STRATEGY["theme"]
    return _DEFAULT_PRESET


def _thesis_from_row(row: ScreeningResult) -> str:
    """screening_results 1行から構造的根拠（narrative）を組み立てる。

    topics と join 出来なかった時のフォールバック。
    """
    parts: list[str] = []
    if row.v_shape_score >= 50:
        if isinstance(row.v_shape_details, dict):
            ign = row.v_shape_details.get("earnings_turnaround")
            if ign:
                parts.append(f"業績反転：{ign}")
            if row.v_shape_details.get("price_bottom") is True:
                parts.append("株価底打ちシグナル点灯")
    if row.theme_score >= 50:
        if isinstance(row.theme_details, dict):
            kw = row.theme_details.get("keyword_match_count")
            if isinstance(kw, int) and kw > 0:
                parts.append(f"テーマキーワード {kw} 件マッチ")
    if not parts:
        parts.append(
            f"composite={row.composite_score:.0f} 3週連続入賞"
        )
    return " / ".join(parts)


def _topic_narrative_for(ticker: str, topics: list[Topic]) -> str:
    """ticker に関連する直近の topic から1行 narrative を作る。"""
    related = [t for t in topics if ticker in (t.affected_tickers or [])]
    if not related:
        return ""
    related.sort(key=lambda t: t.collected_at, reverse=True)
    top = related[0]
    return f"{top.headline}（{top.source}）"


class ZeeleCuratorAgent(Agent[ZeeleCuratorInput]):
    """ZEELE プールのキュレーター。"""

    name = "zeele_curator"
    description = (
        "screening_results を週次ビンで集約し、3週連続入賞銘柄を ZEELE プールに upsert する。"
    )
    required_tools: list[str] = []  # DB アクセスのみ。MCP ツール不要
    default_routing = "hot"

    def __init__(self, context: AgentContext) -> None:
        self._ctx = context
        self._log = get_logger("agent").bind(agent=self.name)

    async def execute(self, agent_input: ZeeleCuratorInput) -> AgentOutput:
        as_of = agent_input.as_of or utcnow().date()
        required = max(1, agent_input.qualification_weeks)

        rows = self._load_screening(as_of=as_of, lookback_days=_LOOKBACK_DAYS)
        topics = self._load_recent_topics(as_of=as_of, lookback_days=_LOOKBACK_DAYS)

        buckets = _weekly_buckets(rows, as_of=as_of, weeks=required)
        latest = _latest_screening_by_ticker(rows)

        qualified: list[str] = [t for t, b in buckets.items() if _is_qualified(b, required)]

        newly_entered: list[str] = []
        still_active: list[str] = []
        candidates: list[dict[str, Any]] = []

        with Session(self._ctx.engine, expire_on_commit=False) as session:
            existing_states = {
                s.ticker: s
                for s in session.exec(select(ZeeleState))
            }
            universe_map = self._load_universe_names(session, qualified)

            for ticker in qualified:
                row = latest.get(ticker)
                if row is None:
                    continue
                preset = _infer_preset(row)
                thesis = _topic_narrative_for(ticker, topics) or _thesis_from_row(row)

                state = existing_states.get(ticker)
                if state is None:
                    state = ZeeleState(
                        ticker=ticker,
                        entered_at=as_of,
                        weeks_in_zeele=required,
                        consecutive_weeks=required,
                        last_screened_at=row.screened_at,
                        preset=preset,
                        structural_thesis=thesis,
                        reference_score=row.composite_score,
                        is_active=True,
                        exited_at=None,
                    )
                    newly_entered.append(ticker)
                else:
                    weeks_total = max(
                        required, (as_of - state.entered_at).days // _WEEK_DAYS + 1
                    )
                    state.weeks_in_zeele = weeks_total
                    state.consecutive_weeks = required
                    state.last_screened_at = row.screened_at
                    state.preset = preset
                    state.structural_thesis = thesis
                    state.reference_score = row.composite_score
                    state.is_active = True
                    state.exited_at = None
                    state.updated_at = utcnow()
                    still_active.append(ticker)

                if not agent_input.dry_run:
                    session.merge(state)

                candidates.append(
                    {
                        "ticker": ticker,
                        "name": universe_map.get(ticker, ""),
                        "preset": preset,
                        "structural_thesis": thesis,
                        "reference_score": row.composite_score,
                        "zeele_entered_at": state.entered_at.isoformat(),
                        "zeele_weeks": state.weeks_in_zeele,
                    }
                )

            deactivated = self._deactivate_stale(
                session,
                existing_states=existing_states,
                qualified=set(qualified),
                as_of=as_of,
                dry_run=agent_input.dry_run,
            )

            if not agent_input.dry_run:
                session.commit()

        summary = (
            f"ZEELE: 新規 {len(newly_entered)} / "
            f"継続 {len(still_active)} / 降格 {len(deactivated)}"
        )
        return ZeeleCuratorOutput(
            success=True,
            invocation_id=agent_input.invocation_id,
            summary=summary,
            candidates=candidates,
            newly_entered=newly_entered,
            still_active=still_active,
            deactivated=deactivated,
        )

    def _load_screening(
        self, *, as_of: dt.date, lookback_days: int
    ) -> list[ScreeningResult]:
        cutoff = dt.datetime.combine(
            as_of - dt.timedelta(days=lookback_days), dt.time.min
        )
        with Session(self._ctx.engine) as session:
            return list(
                session.exec(
                    select(ScreeningResult).where(
                        col(ScreeningResult.screened_at) >= cutoff
                    )
                )
            )

    def _load_recent_topics(
        self, *, as_of: dt.date, lookback_days: int
    ) -> list[Topic]:
        cutoff = dt.datetime.combine(
            as_of - dt.timedelta(days=lookback_days), dt.time.min
        )
        with Session(self._ctx.engine) as session:
            return list(
                session.exec(
                    select(Topic).where(col(Topic.collected_at) >= cutoff)
                )
            )

    def _load_universe_names(self, session: Session, tickers: list[str]) -> dict[str, str]:
        if not tickers:
            return {}
        rows = session.exec(
            select(Universe).where(col(Universe.ticker).in_(tickers))
        )
        return {u.ticker: u.name for u in rows}

    def _deactivate_stale(
        self,
        session: Session,
        *,
        existing_states: dict[str, ZeeleState],
        qualified: set[str],
        as_of: dt.date,
        dry_run: bool,
    ) -> list[str]:
        """4週以上 screening に登場していない ZEELE 銘柄を降格する。"""
        deactivated: list[str] = []
        for ticker, state in existing_states.items():
            if not state.is_active:
                continue
            if ticker in qualified:
                continue
            days_since = (as_of - state.last_screened_at.date()).days
            if days_since >= _DEACTIVATION_DAYS:
                state.is_active = False
                state.exited_at = as_of
                state.consecutive_weeks = 0
                state.updated_at = utcnow()
                if not dry_run:
                    session.merge(state)
                deactivated.append(ticker)
        return deactivated
