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
from trading_agent.utils.time_utils import today_jst, utcnow

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

# 降格判定：v2.4 TASK-Z4
# 旧: 4 週（28 日）screening に登場しなかったら降格 → 障害/休止日もカウントする問題
# 新: screening が走った日のうち N 回連続未登場で降格（暦日でなくスクリーニング回数）
_DEACTIVATION_DAYS = 28  # 旧式互換（フォールバック）
_DEACTIVATION_SCREENING_RUNS = 20  # screening が 20 回走って未登場なら降格


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


# v2.10 致命候補 A 修正: 上流 screening の実態に整合した閾値
# spec は 50 だったが universe 全件 50 未満で 7 戦略が機能不全 → alpha 一色に退化していた。
# 上流データで発火する 30 に下げる（メモリ [[feedback_pipeline_observability]] の教訓）。
# スコア計算の根本改善は別タスク（screening_agent / mcp_tools/screening.py の signal 拡充）。
#
# PIPELINE v3 Phase 4-A (2026-06-01): 30 でも alpha 76% (200/263) のまま、再校正。
# 上流 screening は v_shape/theme とも最高 30 前後実態。20 まで下げて preset 分類を有効化。
# 根本対策の screening signal 拡充は別タスク (S1 動的閾値化と統合検討)。
_PRESET_AXIS_THRESHOLD = 20.0  # v_shape / theme スコアの分類閾値
_PRESET_COMPOSITE_THRESHOLD = 20.0  # composite の フォールバック分類閾値
_PRESET_V_SECONDARY_THRESHOLD = 10.0  # value 分類用の二次閾値


def _infer_preset(row: ScreeningResult) -> str:
    """screening_results 1行から ZEELE preset を推定する（7 種類対応・v2.1 TASK-Z1）。

    screening 側が出すスコアは v_shape / theme の 2 軸＋詳細（v_shape_details, theme_details）。
    v2.10: 閾値を 50 → 30 に下げた（上流データが 50 に届かないため）。

    分類:
      - contrarian: V 字スコア >= 30 ∩ value_trap=True
      - pullback:   V 字 >= 30 ∩ price_bottom + earnings_turnaround
      - growth-value: V 字 + テーマ両方 30 以上
      - momentum:   テーマ >= 30 ∩ keyword_match >= 5 ∩ sector_outperformance > 0.05
      - growth:     テーマ >= 30 ∩ それ以外
      - value:      composite >= 30 ∩ v >= 20（しぶとい底値）
      - alpha:      上記非該当（フォールバック）
    """
    v = row.v_shape_score or 0.0
    t = row.theme_score or 0.0
    composite = row.composite_score or 0.0
    v_details = row.v_shape_details if isinstance(row.v_shape_details, dict) else {}
    t_details = row.theme_details if isinstance(row.theme_details, dict) else {}

    # 1. 両方が高い: growth-value（V 字反転 + テーマ追い風）
    if v >= _PRESET_AXIS_THRESHOLD and t >= _PRESET_AXIS_THRESHOLD:
        return "growth-value"

    # 2. V 字主軸
    if v >= _PRESET_AXIS_THRESHOLD:
        # value_trap シグナル（点火なしで底だけ）→ 逆張り
        if v_details.get("value_trap") is True:
            return "contrarian"
        # price_bottom + ignition → pullback（押し目）
        if v_details.get("price_bottom") and v_details.get("earnings_turnaround"):
            return "pullback"
        # それ以外（業績反転だが株価未転換等）→ contrarian 寄り
        # v2.1 TASK-Z1 設計: V字主軸の銘柄は逆張り傾向と分類
        return "contrarian"

    # 3. テーマ主軸
    if t >= _PRESET_AXIS_THRESHOLD:
        keyword_count = t_details.get("keyword_matches") or 0
        sector_outperf = t_details.get("sector_outperformance") or 0.0
        # 高キーワード一致 + 強いセクター → momentum
        if keyword_count >= 5 and sector_outperf > 0.05:
            return "momentum"
        return "growth"

    # 4. composite だけある（軸が立っていない）→ alpha or value
    if composite >= _PRESET_COMPOSITE_THRESHOLD:
        # V 字が二次閾値以上ある → value（しぶとい底値）
        if v >= _PRESET_V_SECONDARY_THRESHOLD:
            return "value"
        return "alpha"

    # 5. 最終フォールバック
    return "alpha"


# ============================================================
# PIPELINE v3 Track B: シグナルタグ導出（record-only・shadow 計測用）
# ============================================================
# **設計規律（codex レビュー反映）**:
#   - タグは売買判断を変えない（地雷 #1：macro/signal で銘柄選定を上書きしない）。
#   - 既存の screening 計算値を読むだけ（新規 fetch・LLM なし＝コスト 0・重複収集なし）。
#   - tag 別 hit率/avgR/net_excess を feedback_transparency で測り、
#     null 比較を継続的に上回ったものだけ将来 score 加点に昇格する（B4/B5）。
#   - 同一シグナルの多重加点を避けるため、タグは preset/screening と直交する観測軸として保つ。
#
# 既知タグ（順次拡張）:
#   - sector_rs        : 同セクター/対市場で相対的に強い（既存 sector_outperformance）★B2 実装
#   - earnings_accel   : 業績の点火/加速。**J-Quants 由来（A prime・magi_verify 再利用）に一本化**。
#     ※ かつて yfinance v_shape 由来の代理を zeele_curator でも付けていたが、JP 中小型で 0% 発火
#       （dead）かつ J-Quants 由来と source が混ざり解釈が濁るため撤去（codex 指摘）。
#   - macro_tailwind   : 地合い regime が追い風（Track A exposure）            … Track A
#   - impact_peer      : ニュース波及の関連 peer（co-mention グラフ）          … 波及 MVP
#   - pead_candidate / pead_confirmed : 真の PEAD（event_asof + 開示 surprise + 価格反応）… 後段
_SECTOR_RS_OUTPERF_THRESHOLD = 0.05  # momentum 分類と同じ閾値（対セクター +5%pt）

# whitelist：未知タグの混入を防ぐ（H2 同様の防壁）。新タグ追加時はここに足す。
# ※ earnings_accel は magi_verify（J-Quants）が付与する。zeele_curator は付けない（source 一本化）。
_VALID_SIGNAL_TAGS = frozenset(
    {"sector_rs", "earnings_accel", "macro_tailwind", "impact_peer",
     "pead_candidate", "pead_confirmed"}
)


def _derive_signal_tags(row: ScreeningResult) -> list[str]:
    """screening_results 1行から signal_tags を導出する（record-only・B2）。

    既存の theme_details を読むだけ。新規データ収集・LLM は呼ばない（¥0）。
    返すタグは _VALID_SIGNAL_TAGS の whitelist 内に限定（未知タグ混入を防ぐ）。
    ※ earnings_accel は J-Quants 由来で magi_verify が付与する（ここでは付けない・source 一本化）。
    """
    tags: list[str] = []
    t_details = row.theme_details if isinstance(row.theme_details, dict) else {}

    # B2: sector_rs — 同セクター/対市場で相対的に強い銘柄（既存 sector_outperformance を流用）。
    sector_outperf = t_details.get("sector_outperformance")
    if isinstance(sector_outperf, int | float) and sector_outperf > _SECTOR_RS_OUTPERF_THRESHOLD:
        tags.append("sector_rs")

    # whitelist 防壁（H2 同様）：未知タグは落とす。
    return [t for t in tags if t in _VALID_SIGNAL_TAGS]


def _thesis_from_row(row: ScreeningResult) -> str:
    """screening_results 1行から構造的根拠（narrative）を組み立てる（v2.2 TASK-Z5 拡張）。

    topics と join 出来なかった時のフォールバック。
    v_shape_details / theme_details の主要キーをできるだけ拾って具体性を上げる。
    """
    parts: list[str] = []
    v_det = row.v_shape_details if isinstance(row.v_shape_details, dict) else {}
    t_det = row.theme_details if isinstance(row.theme_details, dict) else {}

    # V 字側の詳細
    if row.v_shape_score >= 50 or v_det:
        ign = v_det.get("earnings_turnaround")
        if ign:
            parts.append(f"業績反転：{ign}")
        if v_det.get("price_bottom") is True:
            parts.append("株価底打ちシグナル点灯")
        elif v_det.get("price_bottom"):  # 文字列の場合（例: "底だが点火なし"）
            parts.append(f"株価：{v_det.get('price_bottom')}")
        if v_det.get("value_trap") is True:
            parts.append("⚠ value trap 警戒")
        if v_det.get("rsi_reversal") is not None:
            parts.append(f"RSI {v_det['rsi_reversal']:.0f} 反転圏")
        if v_det.get("macd_cross") is True:
            parts.append("MACD クロス点灯")
        if v_det.get("volume_surge") is True:
            parts.append("出来高サージ")

    # テーマ側の詳細
    if row.theme_score >= 50 or t_det:
        kw = t_det.get("keyword_match_count") or t_det.get("keyword_matches")
        if isinstance(kw, int) and kw > 0:
            parts.append(f"テーマキーワード {kw} 件マッチ")
        sec_outperf = t_det.get("sector_outperformance")
        if isinstance(sec_outperf, int | float) and sec_outperf > 0:
            parts.append(f"セクター対市場 {sec_outperf*100:+.1f}%")
        inst = t_det.get("institutional")
        if isinstance(inst, int | float) and inst > 0:
            parts.append(f"機関投資家フロー {inst:.0f}")

    if not parts:
        parts.append(
            f"composite={row.composite_score:.0f}・3週連続入賞（詳細データ薄）"
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
        as_of = agent_input.as_of or today_jst()
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
                tags = _derive_signal_tags(row)  # Track B: record-only signal_tags
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
                        signal_tags=tags,
                        structural_thesis=thesis,
                        reference_score=row.composite_score,
                        is_active=True,
                        exited_at=None,
                    )
                    newly_entered.append(ticker)
                else:
                    # v2.2 TASK-Z3: max(required, ...) を撤去。実滞在週数を素直に出す。
                    # entry 当日は 1 週、entry+7日 は 2 週、entry+14日 は 3 週。
                    days_since_entry = (as_of - state.entered_at).days
                    weeks_total = max(1, days_since_entry // _WEEK_DAYS + 1)
                    state.weeks_in_zeele = weeks_total
                    state.consecutive_weeks = required
                    state.last_screened_at = row.screened_at
                    state.preset = preset
                    state.signal_tags = tags
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
                        "signal_tags": tags,
                        "structural_thesis": thesis,
                        "reference_score": row.composite_score,
                        "zeele_entered_at": state.entered_at.isoformat(),
                        "zeele_weeks": state.weeks_in_zeele,
                    }
                )

            # v2.1 TASK-Z2: ZEELE 在籍中の銘柄も reference_score を最新 screening 値で
            # 再計算する（陳腐化防止）。qualified に入ってない（今週は登場せず）でも
            # 直近 screening 値で更新する。
            for ticker, state in existing_states.items():
                if not state.is_active or ticker in qualified:
                    continue
                latest_row = latest.get(ticker)
                if latest_row is None:
                    continue
                state.reference_score = latest_row.composite_score
                state.signal_tags = _derive_signal_tags(latest_row)  # Track B: 陳腐化防止
                state.last_screened_at = latest_row.screened_at
                state.updated_at = utcnow()
                if not agent_input.dry_run:
                    session.merge(state)

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
        """ZEELE 銘柄の降格判定（v2.4 TASK-Z4）。

        screening が走った日数（暦日でなく実行回数）でカウント。screening が休んだ日は
        ノーカウント。次の screening が来る前に降格させない。
        """
        deactivated: list[str] = []
        # screening 実行日数を集計（直近 28 日内に行われた screening の日数 = N）
        cutoff = as_of - dt.timedelta(days=_DEACTIVATION_DAYS)
        screening_dates_set: set[dt.date] = set()
        screenings = session.exec(
            select(ScreeningResult).where(col(ScreeningResult.screened_at) >= dt.datetime.combine(cutoff, dt.time.min))
        )
        for sr in screenings:
            screening_dates_set.add(sr.screened_at.date())
        screening_runs = len(screening_dates_set)

        for ticker, state in existing_states.items():
            if not state.is_active:
                continue
            if ticker in qualified:
                continue
            # screening 実行回数ベースで判定
            days_since_last = (as_of - state.last_screened_at.date()).days
            # 旧基準（暦日） + 新基準（screening 実行回数）の両方を満たす場合に降格
            if days_since_last >= _DEACTIVATION_DAYS and screening_runs >= _DEACTIVATION_SCREENING_RUNS:
                state.is_active = False
                state.exited_at = as_of
                state.consecutive_weeks = 0
                state.updated_at = utcnow()
                if not dry_run:
                    session.merge(state)
                deactivated.append(ticker)
        return deactivated
