"""P4-1 ペーパー執行ループ：approved な decision を紙約定→Portfolio化→record_entry。

実ブローカー不要（前向き＝今日のデータは定義上 point-in-time）。**決裁は人間（原則2）**＝
`status="approved"` の decision だけを執行する（自動承認はしない）。紙約定の価格は
**翌営業日の寄り**（先読み回避）を呼び出し側が `price_lookup` で渡す前提（JPY建て）。

コアは守り主導の質分散塊（B'＝利確で刻まない・固定stop＋保有期限で出口）。サイジングは
規律層 `recommend_position`（R-mult・現金下限・枠は呼び出し側で制御）。

注（v1の簡約）：Portfolio.qty は整数。US端株(小数)は当面**整数株に丸める**（高単価USは
小予算で買えないことがある＝正直な制約）。小数株対応はモデル拡張後（別タスク）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.evaluation.job import record_entry
from trading_agent.models.decisions import Decision
from trading_agent.models.portfolio import Portfolio
from trading_agent.portfolio.sizing import recommend_position
from trading_agent.risk.params import DEFAULT_RISK, RiskParams
from trading_agent.utils.time_utils import today_jst, utcnow

PriceLookup = Callable[[str], float | None]  # ticker → 翌寄りの約定価格(JPY)。取得不可は None
IsJpLookup = Callable[[str], bool]  # ticker → 日本株か（端株可否・通貨に使用）

_HORIZON_DAYS = 120  # B' 保有期限（time-exit）の既定


@dataclass
class PaperFill:
    decision_id: int
    ticker: str
    shares: int
    price: float            # 市場価格（slippage 適用前・参考）
    amount_jpy: float       # 現金から引かれた合計（手数料・FX 含む）
    fill_price: float = 0.0  # 実約定価格（slippage 適用後）
    fee_jpy: float = 0.0    # 手数料
    fx_cost_jpy: float = 0.0  # 為替スプレッド分
    rationale: str = ""     # この約定を行った理由（思考ログ）


@dataclass
class PaperResult:
    fills: list[PaperFill] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (ticker, 理由)
    cash_after: float = 0.0


@dataclass
class PaperClose:
    """1 件の自動売却の結果。"""

    portfolio_id: int
    ticker: str
    qty: int
    buy_price: float
    sell_price: float
    proceeds_jpy: float  # 売却収入（手数料引き後）
    pnl_jpy: float       # 売却差益（手数料 × 2 引き後）
    reason: str          # "stop_loss" / "time_exit"


@dataclass
class CloseResult:
    closes: list[PaperClose] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


def _record_exit_on_decision(session, portfolio, *, exit_price: float, day) -> None:
    """C: stop/time で閉じた Portfolio の実退出を、紐付く buy Decision の実績に記録する。

    Portfolio.decision_id から buy Decision を引き、未評価（hit_or_miss=="pending"）なら
    realized リターンで actual_return / hit_or_miss / evaluated_at を確定する。
    evaluate_due_decisions は hit_or_miss!="pending" を採点しないため、二重評価しない。
    decision_id 無し（旧データ）や評価済みは何もしない。
    """
    did = getattr(portfolio, "decision_id", None)
    if did is None:
        return
    d = session.get(Decision, did)
    if d is None or d.hit_or_miss != "pending":
        return
    entry = d.entry_price if d.entry_price else portfolio.buy_price
    if not entry or entry <= 0:
        return
    actual = (exit_price - float(entry)) / float(entry)
    stop = d.stop_pct if d.stop_pct else float(portfolio.stop_loss_pct or 0.10)
    target = d.expected_return if d.expected_return else float(portfolio.target_pct or 0.20)
    if actual <= -abs(stop):
        outcome = "miss"
    elif actual >= target:
        outcome = "hit"
    else:
        outcome = "neutral"
    d.actual_return = round(actual, 4)
    d.hit_or_miss = outcome
    d.evaluated_at = utcnow()
    if d.evaluation_date is None:
        d.evaluation_date = day
    session.add(d)


def paper_close_due(
    engine: Engine,
    *,
    price_lookup: PriceLookup,
    is_jp_lookup: IsJpLookup,
    today: dt.date | None = None,
    personality_filter: str | None = None,
) -> CloseResult:
    """active な Portfolio のうち以下を自動売却して closed に進める。

    1. **stop_loss 到達**: 現在価格 ≤ buy_price × (1 - stop_loss_pct)
       （v2.1 TASK-SZ4: stop_loss_pct は正値で統一・例 0.15 = -15% で機能）
    2. **time_exit**: 今日 ≥ target_date

    `personality_filter` 指定時はその性格の保有のみ対象。
    broker_provider 別の simulate_fill で売却コスト（手数料・スプレッド・スリッページ）を適用する。
    """
    from trading_agent.portfolio.fill_simulator import simulate_fill_for_provider
    from trading_agent.utils.lot_size import get_broker_provider

    provider = get_broker_provider()
    day = today or today_jst()
    result = CloseResult()
    with Session(engine, expire_on_commit=False) as session:
        stmt = select(Portfolio).where(col(Portfolio.status) == "active")
        if personality_filter is not None:
            stmt = stmt.where(col(Portfolio.personality) == personality_filter)
        ports = session.exec(stmt).all()

        for p in ports:
            if p.id is None:
                continue
            cur = price_lookup(p.ticker)
            if cur is None or cur <= 0:
                result.skipped.append((p.ticker, "現価取得不可"))
                continue
            # v2.1 TASK-SZ4: stop_loss_pct は正値で統一（旧 -0.15 → 新 0.15）
            stop_threshold = float(p.buy_price) * (1.0 - float(p.stop_loss_pct or 0))
            reason: str | None = None
            if cur <= stop_threshold:
                reason = "stop_loss"
            elif p.target_date and day >= p.target_date:
                reason = "time_exit"
            if reason is None:
                continue

            sell = simulate_fill_for_provider(
                broker_provider=provider,
                market_price=cur,
                qty=int(p.qty),
                is_jp=is_jp_lookup(p.ticker),
                side="sell",
            )
            # 取得時も同じスリッページ・手数料が引かれている前提で、PnL は売却収入 - 取得コスト
            buy_cost = float(p.buy_price) * int(p.qty)
            pnl = sell.total_cost_jpy - buy_cost  # sell.total_cost_jpy は売却の手取り
            p.status = "closed"
            p.closed_at = utcnow()
            p.closed_price = sell.fill_price
            p.closed_reason = reason
            session.add(p)
            # C（測定正確性）: stop/time の実退出を、紐付く buy Decision の実績に書き戻す。
            # これが無いと評価ジョブが後日 horizon 価格で採点し、実際の stop 退出を無視して
            # TrackRecord が現実より良く出る。realized リターンで hit/miss を確定する。
            _record_exit_on_decision(
                session, p, exit_price=sell.fill_price, day=day
            )
            result.closes.append(
                PaperClose(
                    portfolio_id=p.id,
                    ticker=p.ticker,
                    qty=int(p.qty),
                    buy_price=float(p.buy_price),
                    sell_price=sell.fill_price,
                    proceeds_jpy=sell.total_cost_jpy,
                    pnl_jpy=pnl,
                    reason=reason,
                )
            )
        session.commit()
    return result


def _consensus_tickers(engine: Engine, peers: tuple[str, ...]) -> set[str]:
    """指定の性格群（peers）が全員 active 保有している ticker の集合を返す。

    KAWORU の "いいとこどり" 用：REI / ASUKA / SHINJI が共通で買った銘柄
    （合議シグナル＝皆が買うものは強い）を優先的に拾うために使う。
    """
    from trading_agent.models.portfolio import Portfolio

    with Session(engine) as session:
        sets: list[set[str]] = []
        for peer in peers:
            rows = session.exec(
                select(Portfolio)
                .where(col(Portfolio.personality) == peer)
                .where(col(Portfolio.status) == "active")
            ).all()
            sets.append({r.ticker for r in rows})
    if not sets or any(not s for s in sets):
        return set()
    return set.intersection(*sets)


def paper_fill_approved(
    engine: Engine,
    *,
    price_lookup: PriceLookup,
    is_jp_lookup: IsJpLookup,
    cash_jpy: float,
    positions_value_jpy: float = 0.0,
    params: RiskParams = DEFAULT_RISK,
    horizon_days: int = _HORIZON_DAYS,
    today: dt.date | None = None,
    personality: object | None = None,
    budget_cap_per_decision_jpy: float | None = None,  # v2.2 TASK-P5: MISATO 配分上限
    allowed_tickers: set[str] | None = None,
    filled_via: str = "ds_dispatch",
    market_regime: str | None = None,
) -> PaperResult:
    """status=approved の decision を翌寄り価格で紙約定し、Portfolio(active)化＋record_entry する。

    `personality` 指定時はその性格固有の sizing/horizon/stop を適用し、Portfolio.personality
    に性格名を刻む。同じ decision に対して複数性格が並行 fill する場合、
    Decision.personalities_filled に追加して重複 fill を防ぐ。

    `allowed_tickers`（A+・測定帰属保護）: 指定すると、その ticker 集合の decision **だけ**を
    fill 対象にする。dispatch 経由では picked（priority/例外/排他を通った銘柄）のみを渡すことで、
    personality モードで awaiting を stance 一致で広く拾う経路（plan 外 fill）を物理的に塞ぐ。
    None なら従来通り（status / stance ベース）。

    現金は引数で受け、残額を返す（永続化は呼び出し側＝run_paper の責務）。
    """
    day = today or today_jst()
    if personality is not None:
        # 性格固有の運用ルールで上書き
        stop = float(getattr(personality, "stop_loss_pct", params.default_stop_pct))
        horizon = int(getattr(personality, "horizon_days", horizon_days))
        accept_stances: set[str] | None = set(
            getattr(personality, "accept_stances", ())
        )
        # 動的 max_position_pct：現在の PnL に応じて ×0.8〜×1.2 で可変
        # （勝ってる時は強気・負けてる時は守り）
        try:
            from trading_agent.portfolio.personality import effective_max_position_pct

            overlay = float(getattr(personality, "overlay_cash_jpy", cash_jpy))
            current_total = cash_jpy + positions_value_jpy
            pnl_pct = ((current_total - overlay) / overlay * 100.0) if overlay else 0.0
            max_pos_pct = effective_max_position_pct(personality, pnl_pct=pnl_pct)
        except Exception:
            max_pos_pct = float(getattr(personality, "max_position_pct", 0.20))
        personality_name: str | None = str(getattr(personality, "name", ""))
    else:
        stop = params.default_stop_pct
        horizon = horizon_days
        accept_stances = None
        max_pos_pct = 0.20
        personality_name = None

    cash = cash_jpy
    result = PaperResult(cash_after=cash)

    with Session(engine, expire_on_commit=False) as session:
        # 性格モードでは複数機が同じ decision を独立に fill できるよう、
        # record_entry で進んだ "ordered"/"holding" 状態も対象に含める
        # （重複防止は personalities_filled で行う）。
        if personality is not None:
            decisions = session.exec(
                select(Decision)
                .where(
                    col(Decision.status).in_(
                        ("approved", "awaiting", "ordered", "holding")
                    )
                )
                .where(col(Decision.action) == "buy")
            ).all()
        else:
            decisions = session.exec(
                select(Decision)
                .where(col(Decision.status) == "approved")
                .where(col(Decision.action) == "buy")
            ).all()

        # A+: allowed_tickers 指定時は picked 銘柄だけに物理限定（plan 外 fill 防止）
        if allowed_tickers is not None:
            decisions = [d for d in decisions if d.ticker in allowed_tickers]

    # v2.8: KAWORU の合議銘柄ロジック廃止（コントラリアン短期機に再設計のため）
    # 旧: 他 3 機が保有する銘柄を KAWORU が後追いで買う → 機跨ぎ重複の原因
    # 新: KAWORU の proposal は ds_scout.select_kaworu_contrarian が作成（dispatch 段階で生成）
    # → paper_exec ではソート優先順位の特別処理は不要

    with Session(engine, expire_on_commit=False) as session:

        for d in decisions:
            if d.id is None:
                continue
            # v2.10: ハルシネーション対策 — fill 直前の Universe 最終照合（出口の防壁）
            # 仮想銘柄・上場廃止銘柄・虚偽 ticker をここで物理的に弾く。
            # screening/MAGI/ZEELE が万一虚偽 ticker を返しても、ここで止まる。
            from trading_agent.models.universe import Universe as _Uni

            _u_check = session.exec(
                select(_Uni).where(
                    col(_Uni.ticker) == d.ticker,
                    col(_Uni.is_active),
                )
            ).first()
            if _u_check is None:
                result.skipped.append(
                    (d.ticker, "Universe 不在（ハルシネーション防止）")
                )
                continue
            # 性格モード：受容 stance + 重複 fill チェック
            if personality is not None:
                # v2.8: status="approved" は ds_scout / dispatch 経由で機が承認済
                # → accept_stances 外でも受け入れる（重複判定の防止のみ）
                if (
                    d.status != "approved"
                    and accept_stances is not None
                    and d.gendo_stance not in accept_stances
                ):
                    continue
                filled_already = list(d.personalities_filled or [])
                if personality_name in filled_already:
                    continue
                # v2.8: 同一機が同一銘柄を二重保有しない（別 decision_id でも buy しない）
                # broker_mode 別に判定（Paper と Live で別管理）
                from trading_agent.utils.lot_size import get_broker_mode as _gbm

                _cur_mode = _gbm()
                existing_holding = session.exec(
                    select(Portfolio).where(
                        col(Portfolio.personality) == personality_name,
                        col(Portfolio.ticker) == d.ticker,
                        col(Portfolio.status) == "active",
                        col(Portfolio.broker_mode) == _cur_mode,
                    )
                ).first()
                # v2.10 Phase 1A-Step2 修正 (致命 2): ピラミッディング追加買付は
                # 既存 Portfolio を merge する（新規作成すると同銘柄が分裂する）
                is_pyramid = "ピラミッディング" in (d.thesis_at_decision or "")
                if existing_holding is not None and not is_pyramid:
                    result.skipped.append((d.ticker, f"{personality_name} 既保有"))
                    continue
            price = price_lookup(d.ticker)
            if price is None or price <= 0:
                result.skipped.append((d.ticker, "価格取得不可"))
                continue
            is_jp = is_jp_lookup(d.ticker)

            # v2.8 → v2.9: Stage 3 (paper_exec) の max_lot_cost フィルタは廃止
            # Stage 0（候補プール構築時）で treasury 全額 × max_lot_pct のガードレールが効くので、
            # ここで機別 cash で再フィルタすると過剰除外（cash が小さい機で全銘柄 skip）になる。
            # 代わりに、次の lot 丸めロジックで「1 単元買えるなら買う」判定をする。
            total = cash + positions_value_jpy
            rec = recommend_position(
                price_jpy=price, total_assets_jpy=total, cash_jpy=cash,
                is_jp=is_jp, stop_pct=stop, params=params,
            )
            shares = int(rec.shares)
            # 性格モードでは max_position_pct で上限制約
            if personality is not None:
                max_cost = total * max_pos_pct
                max_shares = int(max_cost // price)
                shares = min(shares, max_shares)
            # v2.2 TASK-P5: MISATO 配分上限を強制適用（recommend_position を上書き）
            if budget_cap_per_decision_jpy is not None and budget_cap_per_decision_jpy > 0:
                cap_shares = int(budget_cap_per_decision_jpy // price)
                shares = min(shares, cap_shares)
            # v2.8: 実弾モード時は単元株（100 株）の倍数に丸める
            from trading_agent.utils.lot_size import effective_lot_size, is_live_mode

            lot = effective_lot_size(d.ticker)
            if lot > 1:
                lot_cost = price * lot
                # 丸めて 0 になっても、cash で 1 単元買えるなら 1 単元許可
                # （少額予算で max_position_pct や 1 単元未満 shares でも実弾運用可能に）
                if shares > 0:
                    rounded = (shares // lot) * lot
                    if rounded == 0 and cash >= lot_cost:
                        rounded = lot
                    shares = rounded
                else:
                    # max_pos で 0 株でも 1 単元買えるなら 1 単元
                    if cash >= lot_cost:
                        shares = lot
                if shares <= 0:
                    result.skipped.append(
                        (
                            d.ticker,
                            f"実弾モード: 単元株（{lot}株 ¥{lot_cost:,.0f}）に予算不足",
                        )
                    )
                    continue

            # v2.10 Phase 1A-Step2: ピラミッディング適用（personality 指定時のみ）
            # planned_total_qty = ガードレール後の "予定総量"
            # 実 fill = planned_total_qty × get_initial_alloc(機別)
            # 旧挙動互換: personality is None なら従来通り全量一括 fill
            planned_total_qty = shares  # ガードレール反映後の予定総量
            if personality_name is not None:
                from trading_agent.portfolio.pyramiding import get_initial_alloc

                initial_alloc = get_initial_alloc(personality_name)
                initial_shares = int(shares * initial_alloc)
                # 単元株丸め（再度・initial_alloc 適用後）
                if lot > 1:
                    initial_shares = (initial_shares // lot) * lot
                    # 0 株になったら最低 1 単元（買えるなら）
                    if initial_shares == 0 and cash >= price * lot:
                        initial_shares = lot
                if initial_shares > 0:
                    shares = initial_shares  # 実 fill 量を縮小
            if shares <= 0:
                if is_live_mode():
                    result.skipped.append((d.ticker, f"実弾モード: サイズ0（{rec.note}）"))
                else:
                    result.skipped.append((d.ticker, f"サイズ0（{rec.note}）"))
                continue
            # v2.10: broker_provider 別の simulate_fill（楽天/kabu.com/moomoo）
            # is_moomoo_live() は broker_provider="moomoo" + broker_mode="live" の時のみ True
            from trading_agent.portfolio.fill_simulator import simulate_fill_for_provider
            from trading_agent.utils.lot_size import (
                get_broker_provider,
                is_moomoo_live,
            )

            provider = get_broker_provider()

            if is_moomoo_live() and is_jp:
                # 本番運用: moomoo OpenD 経由で実発注（裏で構築済・ローカル運用者の責任で有効化）
                try:
                    from trading_agent.brokers.moomoo import MoomooBroker
                    from trading_agent.config import load_settings

                    settings = load_settings()
                    broker = MoomooBroker.from_settings(settings)
                    order_result = broker.place_order(
                        ticker=d.ticker,
                        shares=shares,
                        side="buy",
                        market="JP",
                        order_type="MARKET",
                        trd_pwd=settings.moomoo_trading_pwd,
                    )
                    if not order_result.get("ok"):
                        result.skipped.append(
                            (d.ticker, f"moomoo発注失敗: {order_result.get('error')}")
                        )
                        continue
                    # 約定価格が取れない場合は market_price で代替
                    fill_price = order_result.get("fill_price") or price
                    fill = simulate_fill_for_provider(
                        broker_provider="moomoo",
                        market_price=fill_price,
                        qty=shares,
                        is_jp=True,
                        side="buy",
                    )
                except Exception as exc:
                    result.skipped.append((d.ticker, f"moomoo broker例外: {exc}"))
                    continue
            else:
                # 検証モード: broker_provider 別の simulate_fill で実コストを反映
                # 楽天かぶミニ: 寄付取引（朝バッチ標準）= 手数料 0 + スプレッド 0 + スリッページのみ
                fill = simulate_fill_for_provider(
                    broker_provider=provider,
                    market_price=price,
                    qty=shares,
                    is_jp=is_jp,
                    side="buy",
                )
            if fill.total_cost_jpy > cash:
                result.skipped.append(
                    (
                        d.ticker,
                        f"現金不足（必要 ¥{fill.total_cost_jpy:,.0f} / 残 ¥{cash:,.0f}）",
                    )
                )
                continue
            cash -= fill.total_cost_jpy
            # 思考ログ：性格別の判断理由を記録
            stance_label = d.gendo_stance or "—"
            rationale = (
                f"MAGI={stance_label} を accept_stances={list(accept_stances) if accept_stances else 'all'} に基づき採用。"
                f" サイジング: max {max_pos_pct*100:.0f}% / 保有 {horizon}日 / stop -{stop*100:.0f}%。"
                f" 実費 all-in {fill.all_in_pct*100:+.2f}%（手数料 ¥{fill.fee_jpy:,.0f}・スリッページ含む）。"
                if personality is not None
                else (
                    f"標準ペーパー fill: stance={stance_label}・stop -{stop*100:.0f}%・保有 {horizon}日"
                )
            )
            # v2.8: 現在の broker_mode を取得してレコードに記録
            from trading_agent.utils.lot_size import get_broker_mode

            current_mode = get_broker_mode()
            # v2.10 Phase 1A-Step2 修正 (致命 2): ピラミッディング追加買付なら
            # 既存 active Portfolio を merge（qty 加算 + buy_price 加重平均）
            if (
                personality is not None
                and "is_pyramid" in dir()
                and is_pyramid
                and existing_holding is not None
            ):
                # merge: qty 加算 + buy_price 加重平均
                old_qty = int(existing_holding.qty or 0)
                new_qty = old_qty + shares
                old_avg = float(existing_holding.buy_price or 0)
                if new_qty > 0:
                    existing_holding.buy_price = (
                        old_avg * old_qty + fill.fill_price * shares
                    ) / new_qty
                existing_holding.qty = new_qty
                existing_holding.updated_at = utcnow()
                # peak_pnl_pct は新しい平均から計算し直すためリセット
                existing_holding.peak_pnl_pct = None
                # planned_total_qty は既存値を維持（既に保存済み）
                session.add(existing_holding)
            else:
                session.add(
                    Portfolio(
                        ticker=d.ticker,
                        buy_date=day,
                        buy_price=fill.fill_price,
                        qty=shares,
                        currency="JPY" if is_jp else "USD",
                        strategy_category="中期",
                        target_period_days=horizon,
                        target_pct=0.0,
                        stop_loss_pct=stop,
                        target_date=day + dt.timedelta(days=horizon),
                        thesis=(d.thesis_at_decision or "コア（守り主導の質分散塊）") + " | " + rationale,
                        status="active",
                        personality=personality_name,
                        broker_mode=current_mode,  # v2.8: Paper / Live 分離
                        # v2.10 Phase 1A-Step2: ピラミッディング予定総量
                        # personality=None なら shares == planned_total_qty（旧挙動）
                        # personality 指定時は planned_total_qty > shares で後続の追加 fill を待つ
                        planned_total_qty=planned_total_qty,
                        peak_pnl_pct=None,  # 初期は未設定（初回 trailing_check で初期化）
                        decision_id=d.id,
                    )
                )
            if personality is not None:
                filled_new = list(d.personalities_filled or [])
                if personality_name not in filled_new:
                    filled_new.append(personality_name)
                d.personalities_filled = filled_new
            else:
                d.status = "holding"
            session.add(d)
            result.fills.append(
                PaperFill(
                    decision_id=d.id,
                    ticker=d.ticker,
                    shares=shares,
                    price=price,
                    amount_jpy=fill.total_cost_jpy,
                    fill_price=fill.fill_price,
                    fee_jpy=fill.fee_jpy,
                    fx_cost_jpy=fill.fx_cost_jpy,
                    rationale=rationale,
                )
            )
        session.commit()

    # 評価の前提（entry/stop/評価期日/経路/局面）を刻む。実約定価格（slippage 適用後）を渡す。
    # A7: target_return を 0.0 → 標準 0.20 に（gate の hit/miss を他経路と整合。0.0 だと
    #     actual≥0 が全部 hit になり hit_rate が歪む）。filled_via / market_regime も刻む。
    for f in result.fills:
        record_entry(
            engine, f.decision_id,
            entry_price=f.fill_price if f.fill_price else f.price,
            stop_pct=stop, target_return=0.20,
            target_period_days=horizon, on_date=day,
            filled_via=filled_via, market_regime=market_regime,
        )

    result.cash_after = cash
    return result


# === v2.10 Phase 1A-Step2 修正 (致命 1): sell Decision の実行ロジック ===

def paper_close_approved(
    engine: Engine,
    *,
    price_lookup: PriceLookup,
    today: dt.date | None = None,
    broker_mode: str | None = None,
) -> dict[str, Any]:
    """status="approved" + action="sell_loss"/"sell_profit" の Decision を実行し、
    対応する active Portfolio を closed にする（v2.10 Phase 1A-Step2 修正）。

    trailing_check 等が登録した sell Decision を「実際に売却執行」する。
    closed_price / closed_reason / actual_return を Decision に記録する
    （evaluate_due_decisions が後で正しく評価できるように）。

    Args:
        engine: DB エンジン
        price_lookup: ticker → 現価
        today: 今日（None なら utcnow）
        broker_mode: "paper"/"live"（None なら現在のモード）

    Returns:
        実行サマリ dict。
    """
    if today is None:
        today = today_jst()
    if broker_mode is None:
        from trading_agent.utils.lot_size import get_broker_mode

        broker_mode = get_broker_mode()

    closed_count = 0
    skipped_no_price: list[str] = []
    skipped_no_holding: list[str] = []
    closed_details: list[dict[str, Any]] = []

    with Session(engine, expire_on_commit=False) as session:
        sell_decisions = session.exec(
            select(Decision)
            .where(col(Decision.status) == "approved")
            .where(col(Decision.action).in_(("sell_loss", "sell_profit")))
        ).all()

        for sd in sell_decisions:
            # v2.10 Phase G-3: Universe 照合（ハルシネーション完全性・最終防壁）
            from trading_agent.models.universe import Universe

            uni = session.get(Universe, sd.ticker)
            if uni is None or not uni.is_active:
                # 仮想 ticker や上場廃止銘柄は処理しない（推測しない）
                skipped_no_holding.append(sd.ticker)
                continue
            # 対応する active Portfolio
            ports = session.exec(
                select(Portfolio)
                .where(col(Portfolio.ticker) == sd.ticker)
                .where(col(Portfolio.status) == "active")
                .where(col(Portfolio.broker_mode) == broker_mode)
            ).all()
            if not ports:
                skipped_no_holding.append(sd.ticker)
                continue
            current_price = price_lookup(sd.ticker)
            if current_price is None or current_price <= 0:
                skipped_no_price.append(sd.ticker)
                continue
            # 全 active Portfolio を close（同銘柄に複数機が保有していれば全部）
            for p in ports:
                entry_p = float(p.buy_price or 0)
                qty = int(p.qty or 0)
                pnl_jpy = (current_price - entry_p) * qty
                p.status = "closed"
                p.closed_at = utcnow()
                p.closed_price = current_price
                p.closed_reason = sd.action  # "sell_loss" or "sell_profit"
                p.updated_at = utcnow()
                session.add(p)
                closed_count += 1
                closed_details.append(
                    {
                        "ticker": sd.ticker,
                        "personality": p.personality,
                        "qty": qty,
                        "buy_price": entry_p,
                        "closed_price": current_price,
                        "pnl_jpy": round(pnl_jpy, 0),
                        "action": sd.action,
                    }
                )
                # Decision に actual_return を記録（evaluate との整合性）
                if entry_p > 0:
                    sd.actual_return = (current_price - entry_p) / entry_p
                sd.evaluated_at = utcnow()
            # Decision を「処理済」に
            sd.status = "ordered"  # 既存 _EVALUABLE に含まれる
            session.add(sd)
        session.commit()

    return {
        "status": "active",
        "closed": closed_count,
        "skipped_no_price": skipped_no_price,
        "skipped_no_holding": skipped_no_holding,
        "details": closed_details,
    }
