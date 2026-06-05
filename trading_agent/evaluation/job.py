"""P6-b：評価ジョブ（評価期日到来分を実価格で採点）。RESEARCH_METHODS 領域4。

発注時に `record_entry` で entry_price / stop_pct / target / evaluation_date を decision に刻み、
評価期日が来た decision を `evaluate_due_decisions` が実価格で採点（hit/miss）し永続化する。
**先読みしない**：評価は評価期日以降にのみ行う（評価期日前は pending のまま＝前倒し評価しない）。
価格lookupは注入（テスト可能・ネット非依存）。全てコード（LLM非関与）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.evaluation.metrics import (
    EvalResult,
    TrackRecord,
    build_track_record,
    evaluate_position,
)
from trading_agent.models.decisions import Decision
from trading_agent.utils.time_utils import today_jst, utcnow

PriceLookup = Callable[[str], float | None]
# A4: decision ごとの保有期間（entry→evaluation_date）ベンチマークリターン。
# 当日騰落ではなく decision の保有期間に同期する必要があるため、ticker 単体でなく
# Decision を受け取る（entry 日 / evaluation_date / ticker を参照できる）。
BenchmarkLookup = Callable[["Decision"], float | None]

# 評価対象の status（保有/発注済/承認＝ポジションを持ち得る段階）
# v2.10 P0: "filled"（paper auto fill / mark_filled / 朝バッチ notify の直書き約定）も
# 評価対象に含める。これらの経路は record_entry を通らず status="filled" を直書きするため、
# stamp_evaluation_fields で評価前提フィールドを刻んだ上で評価ジョブに乗せる。
_EVALUABLE = ("approved", "order_listed", "ordered", "holding", "filled")


# fill 時に評価の前提フィールドを刻むときの既定値（朝バッチ Portfolio 生成と整合）。
_DEFAULT_STOP_PCT = 0.10
_DEFAULT_TARGET_RETURN = 0.20
_DEFAULT_PERIOD_DAYS = 90

# P0.5: 取引コスト（往復）の既定値。楽天リアルタイム往復 0.22%×2＝0.44%。
# backtest エンジン（transaction_cost_pct=0.0044）と整合。寄付執行なら ~0% だが
# 保守的に realtime 往復を既定とする。「コスト後α>0」の判定に使う。
_DEFAULT_ROUND_TRIP_COST_PCT = 0.0044


def _merge_signal_tags(d: Decision, new_tags: list[str]) -> None:
    """Decision.entry_signal_tags に new_tags を union merge（順序保持・重複なし・in-place）。

    既存タグ（magi_verify が verify 時点で書いた earnings_accel 等）を消さずに足す。
    SQLModel の JSON 変更検知のため新リストを代入する（in-place mutate では dirty にならない）。
    """
    if not new_tags:
        return
    existing = list(d.entry_signal_tags or [])
    merged = existing + [t for t in new_tags if t not in existing]
    if merged != existing:
        d.entry_signal_tags = merged


def _lookup_signal_tags(session: Session, ticker: str) -> list[str]:
    """fill 時点の ZEELE signal_tags を引く（Track B・record-only スナップショット）。

    エントリ時点で Decision.entry_signal_tags に固定するための lookup。評価時に live join
    すると陳腐化/look-ahead するため、fill のタイミングで一度だけ刻む（codex 地雷 #2 回避）。
    ZeeleState 不在（ZEELE 由来でない候補）や失敗時は空リスト（推測しない・H10）。
    """
    try:
        from trading_agent.models.zeele import ZeeleState

        state = session.get(ZeeleState, ticker)
        return list(state.signal_tags or []) if state is not None else []
    except Exception:
        return []


def stamp_evaluation_fields(
    d: Decision,
    *,
    stop_pct: float | None = None,
    target_return: float | None = None,
    target_period_days: int | None = None,
    on_date: dt.date | None = None,
    market_regime: str | None = None,
    filled_via: str | None = None,
    broker_mode: str | None = None,
) -> None:
    """fill 時に評価の前提（stop / target / 評価期日 / entry regime / 経路 / broker_mode）を Decision に刻む（in-place）。

    `record_entry` と違い entry_price / shares は触らない。status="filled" を直書きする
    fill 経路（auto_fill_paper / mark_filled / 朝バッチ notify）が、呼び出し側の Session で
    既にセットした entry_price をそのまま活かしつつ、評価に必要な
    stop_pct / expected_return / target_period_days / evaluation_date / entry_market_regime
    を補完するための関数。

    既に値がある場合は上書きしない（katsuragi_dispatch 等で設定済みの値を尊重する）。
    `market_regime` は A3：ゲート⑥「両局面通過」判定のためエントリ時点の市場地合いを固定保存する。
    """
    base = on_date or today_jst()
    period = int(target_period_days or d.target_period_days or _DEFAULT_PERIOD_DAYS)
    if d.stop_pct is None:
        d.stop_pct = float(stop_pct if stop_pct is not None else _DEFAULT_STOP_PCT)
    if d.expected_return is None:
        d.expected_return = float(
            target_return if target_return is not None else _DEFAULT_TARGET_RETURN
        )
    if d.target_period_days is None:
        d.target_period_days = period
    if d.evaluation_date is None:
        d.evaluation_date = base + dt.timedelta(days=period)
    if d.entry_market_regime is None and market_regime is not None:
        d.entry_market_regime = market_regime
    # A7: 約定経路（公式集合識別）と実約定日（benchmark 起点）を刻む。
    if d.filled_via is None and filled_via is not None:
        d.filled_via = filled_via
    if d.entry_date is None:
        d.entry_date = base
    # broker_mode 分離（gate⑥/昇格を paper/live で分けるため）。既存値は尊重。
    if d.entry_broker_mode is None and broker_mode is not None:
        d.entry_broker_mode = broker_mode
    # Track B: fill 時点の ZEELE signal_tags をスナップショット（record-only）。
    # d が attach された session を辿って lookup（呼び出し側の引数追加が不要）。
    # **union merge**: magi_verify が verify 時点で書いた earnings_accel 等を消さず、sector_rs を足す
    # （skip-if-nonempty だと先に付いたタグが ZEELE タグを排除する＝codex 地雷 #2）。
    from sqlalchemy.orm import object_session

    _sess = object_session(d)
    if _sess is not None:
        _merge_signal_tags(d, _lookup_signal_tags(_sess, d.ticker))


def record_entry(
    engine: Engine,
    decision_id: int,
    *,
    entry_price: float,
    stop_pct: float,
    target_return: float,
    target_period_days: int,
    shares: float = 1.0,
    on_date: dt.date | None = None,
    filled_via: str | None = None,
    market_regime: str | None = None,
    broker_mode: str | None = None,
) -> bool:
    """発注時：entry/stop/target/評価期日を decision に刻む（評価の前提）。

    v2.1 TASK-E2: 複数 fill の場合、shares で加重平均する。
    最初の fill: そのまま記録 / 2 回目以降: (既存価格×既存株数 + 新価格×新株数) / 合計株数
    A7: filled_via（経路）/ entry_date（実約定日）/ market_regime も刻む（既存値は尊重）。
    broker_mode（paper/live）も刻む（gate⑥/昇格の分離集計用・既存値尊重）。
    """
    base = on_date or today_jst()
    with Session(engine, expire_on_commit=False) as session:
        d = session.get(Decision, decision_id)
        if d is None:
            return False
        # entry_price は加重平均（v2.1 TASK-E2）
        existing_shares = float(d.shares_filled or 0.0)
        if d.entry_price is not None and existing_shares > 0:
            total = existing_shares + shares
            d.entry_price = (
                d.entry_price * existing_shares + entry_price * shares
            ) / total
            d.shares_filled = total
        else:
            d.entry_price = entry_price
            d.shares_filled = shares
        d.stop_pct = stop_pct
        d.expected_return = target_return
        d.target_period_days = target_period_days
        d.evaluation_date = base + dt.timedelta(days=target_period_days)
        if d.entry_date is None:
            d.entry_date = base
        if d.filled_via is None and filled_via is not None:
            d.filled_via = filled_via
        if d.entry_market_regime is None and market_regime is not None:
            d.entry_market_regime = market_regime
        if d.entry_broker_mode is None and broker_mode is not None:
            d.entry_broker_mode = broker_mode
        # Track B: fill 時点の ZEELE signal_tags をスナップショット（record-only・union merge）。
        # magi_verify が先に書いた earnings_accel 等を消さず sector_rs を足す（codex 地雷 #2）。
        _merge_signal_tags(d, _lookup_signal_tags(session, d.ticker))
        if d.status in ("approved", "order_listed"):
            d.status = "ordered"
        session.add(d)
        session.commit()
    return True


def evaluate_due_decisions(
    engine: Engine,
    *,
    price_lookup: PriceLookup,
    benchmark_lookup: BenchmarkLookup | None = None,
    today: dt.date | None = None,
    cost_pct: float = _DEFAULT_ROUND_TRIP_COST_PCT,
) -> tuple[int, TrackRecord]:
    """評価期日が到来した未評価 decision を実価格で採点する。

    Returns: (今回評価した件数, 全評価済みの Track Record)。
    """
    day = today or today_jst()
    evaluated_now = 0
    with Session(engine, expire_on_commit=False) as session:
        due = session.exec(
            select(Decision)
            .where(col(Decision.hit_or_miss) == "pending")
            .where(col(Decision.status).in_(_EVALUABLE))
            .where(col(Decision.evaluation_date).is_not(None))
            .where(col(Decision.entry_price).is_not(None))
        ).all()
        for d in due:
            if d.evaluation_date is None or d.evaluation_date > day:
                continue  # 先読みしない（期日前は据え置き）
            exit_price = price_lookup(d.ticker)
            if exit_price is None or d.entry_price is None:
                continue
            # v2.1 TASK-E1: stop_pct/expected_return の fallback を撤去
            # データ不足は採点せず "skipped" として残す（hit/miss を捏造しない）
            if d.stop_pct is None or d.expected_return is None:
                d.hit_or_miss = "skipped"
                d.evaluated_at = utcnow()
                session.add(d)
                continue
            stop = d.stop_pct
            target = d.expected_return
            # v2.5 TASK-E5: benchmark 取得失敗を明示（旧版は None を黙殺）
            bench = None
            if benchmark_lookup is not None:
                try:
                    bench = benchmark_lookup(d)
                    if bench is None:
                        from trading_agent.utils.logger import get_logger
                        get_logger("evaluation").info(
                            "benchmark_unavailable", ticker=d.ticker,
                            note="benchmark_lookup returned None",
                        )
                except Exception as exc:
                    from trading_agent.utils.logger import get_logger
                    get_logger("evaluation").warning(
                        "benchmark_lookup_failed", ticker=d.ticker, error=str(exc),
                    )
            res = evaluate_position(
                entry_price=d.entry_price, exit_price=exit_price,
                target_return=target, stop_pct=stop, benchmark_return=bench,
                cost_pct=cost_pct,
            )
            d.actual_return = res.actual_return
            d.benchmark_return = bench
            # v2.5 TASK-E4: 計算上の near_hit/near_miss は DB には neutral として保存
            # （hit_rate の母集団は明確な hit/miss だけに維持する設計）
            # 観察用には EvalResult.outcome が near_* を保持。
            outcome_persisted = {
                "near_hit": "neutral",
                "near_miss": "neutral",
            }.get(res.outcome, res.outcome)
            d.hit_or_miss = outcome_persisted
            d.evaluated_at = utcnow()
            session.add(d)
            evaluated_now += 1
        session.commit()

    return evaluated_now, _track_record(engine, cost_pct=cost_pct)


def _track_record(
    engine: Engine, *, cost_pct: float = _DEFAULT_ROUND_TRIP_COST_PCT
) -> TrackRecord:
    """評価済み（hit/miss/neutral）decision から Track Record を集計。

    actual_return は DB にグロスで保存されているため、net 系（avg_net_return /
    avg_net_excess＝コスト後α）は集計時に cost_pct を差し引いて導出する（DB カラム追加なし）。
    """
    with Session(engine) as session:
        done = session.exec(
            select(Decision).where(col(Decision.hit_or_miss).in_(("hit", "miss", "neutral")))
        ).all()
    # v2.2 TASK-E3: r_multiple=0 を「stop 不明」と「実際にゼロ」で区別できないため、
    # stop_pct/actual_return が無い decision は集計から除外する（None マーキング）
    results: list[EvalResult] = []
    for d in done:
        if d.actual_return is None or not d.stop_pct:
            continue  # 評価不能データは集計から除外
        results.append(
            EvalResult(
                actual_return=d.actual_return,
                r_multiple=d.actual_return / d.stop_pct,
                outcome=d.hit_or_miss,
                benchmark_return=d.benchmark_return,
                cost_pct=cost_pct,
            )
        )
    return build_track_record(results)
