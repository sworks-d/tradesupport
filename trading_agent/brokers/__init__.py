"""ブローカー層：口座・保有・資金（moomoo OpenAPI）。SYSTEM_DESIGN.md §4 / Phase 1.2。

口座未接続でも全体が動くよう、実ブローカーが使えない時はスタンドインへ自動フォールバックする。
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, col, select

from trading_agent.brokers.base import (
    Account,
    BrokerClient,
    BrokerUnavailable,
    Position,
)
from trading_agent.brokers.standin import StandInBroker
from trading_agent.utils.logger import get_logger

__all__ = [
    "Account",
    "BrokerClient",
    "BrokerUnavailable",
    "Position",
    "StandInBroker",
    "load_account",
    "load_personality_account",
    "load_positions",
]

_log = get_logger("broker")


def load_positions(
    *, prefer_moomoo: bool = False, settings: Any | None = None
) -> tuple[list[Position], str]:
    """保有ポジションを取得する。

    prefer_moomoo=True かつ接続可能なら moomoo の実データ、不可なら StandIn のサンプル。
    口座未開設 / OpenD未起動 / SDK未導入では自動的にスタンドインへ落ちる。

    Returns:
        (positions, source)  source は "moomoo" / "standin"。
    """
    if prefer_moomoo:
        try:
            from trading_agent.brokers.moomoo import MoomooBroker

            broker = (
                MoomooBroker.from_settings(settings)
                if settings is not None
                else MoomooBroker()
            )
            positions = broker.get_positions()
            if positions:
                return positions, "moomoo"
            # 接続OKだが保有0（口座開設中/ペーパー未取引）→ サンプル表示で継続
            _log.info("moomoo_connected_no_positions_using_standin")
        except BrokerUnavailable as exc:
            _log.warning("moomoo_unavailable_fallback_standin", reason=str(exc))
    return StandInBroker().get_positions(), "standin"


def load_account(engine: Any, settings: Any | None = None) -> tuple[Account, str]:
    """口座情報を取得する（live / paper 両対応）。

    - **live**: moomoo の実残高をそのまま反映（overlay 加算なし）。
                失敗時は StandIn にフォールバック（運用前に異常検知できるよう警告ログを残す）。
    - **paper**: JP REAL の口座読取（入金前は ¥0）+ `paper_overlay_cash_jpy` の仮想入金
                − Portfolio(status=active) の紙約定累計コスト。

    実弾化は `TRADING_MODE=live` への切替だけで完了する（overlay が自動的に外れる）。
    JP は moomoo SIMULATE 非対応のため、paper でも JP REAL で口座を読む。

    Returns:
        (account, source)  source は "moomoo" / "moomoo+overlay" / "standin"。
    """
    if settings is None:
        return StandInBroker().get_account() or Account(0.0, 0.0, "JPY"), "standin"

    trading_mode = getattr(settings, "trading_mode", "paper")
    overlay = int(getattr(settings, "paper_overlay_cash_jpy", 0) or 0)

    if trading_mode == "live":
        try:
            from trading_agent.brokers.moomoo import MoomooBroker

            acct = MoomooBroker.from_settings(settings).get_account()
            if acct is not None:
                return acct, "moomoo"
            _log.warning("moomoo_account_empty_fallback_standin")
        except BrokerUnavailable as exc:
            _log.warning("moomoo_account_unavailable_fallback_standin", reason=str(exc))
        return StandInBroker().get_account() or Account(0.0, 0.0, "JPY"), "standin"

    moomoo_cash = 0.0
    try:
        from trading_agent.brokers.moomoo import MoomooBroker

        # JP は SIMULATE 非対応 → paper でも JP REAL で口座読取（入金前は ¥0）
        paper_broker = MoomooBroker(
            host=getattr(settings, "moomoo_opend_host", "127.0.0.1"),
            port=int(getattr(settings, "moomoo_opend_port", 11111)),
            trd_env="REAL",
            markets=("JP",),
            security_firm=getattr(settings, "moomoo_security_firm", "FUTUJP"),
            currency="JPY",
        )
        acct = paper_broker.get_account()
        if acct is not None:
            moomoo_cash = float(acct.cash)
    except BrokerUnavailable as exc:
        _log.warning("moomoo_paper_account_unavailable", reason=str(exc))

    active_cost = _active_portfolio_cost_jpy(engine)
    cash = moomoo_cash + float(overlay) - active_cost
    return Account(cash=cash, total_assets=cash, currency="JPY"), "moomoo+overlay"


def _active_portfolio_cost_jpy(engine: Any, personality: str | None = None) -> float:
    """紙約定で取得した active な Portfolio の累計コスト（JPY 建て）。

    `personality` 指定時はその性格の active 行のみ集計。None 時は全 active 集計。
    """
    from trading_agent.models.portfolio import Portfolio

    with Session(engine) as s:
        stmt = select(Portfolio).where(col(Portfolio.status) == "active")
        if personality is not None:
            stmt = stmt.where(col(Portfolio.personality) == personality)
        rows = s.exec(stmt).all()
    total = 0.0
    for p in rows:
        buy_price = float(p.buy_price or 0.0)
        qty = float(p.qty or 0.0)
        total += buy_price * qty
    return total


def load_personality_account(
    engine: Any, *, personality: str, overlay_cash_jpy: int
) -> Account:
    """性格別の cash を計算する（overlay - その性格の active コスト）。

    paper モード前提。性格ごとに独立した overlay と portfolio を持つ。
    moomoo の口座残高は加算しない（性格単位の検証なのでバイアスを入れない）。
    """
    active_cost = _active_portfolio_cost_jpy(engine, personality=personality)
    cash = float(overlay_cash_jpy) - active_cost
    return Account(cash=cash, total_assets=cash, currency="JPY")
