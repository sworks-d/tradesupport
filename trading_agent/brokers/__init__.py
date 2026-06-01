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
        (account, source)  source は "moomoo" / "moomoo+overlay" / "rakuten" / "kabucom" / "standin"。

    v2.10: broker_provider 別 dispatcher パターン。
      - moomoo: 既存 MoomooBroker 経由（OpenD 接続）
      - rakuten/sbi/monex: RakutenBroker（DB ベース、mark_filled 経由）
      - kabucom: KabuComBroker（Phase 2 で実装、kabu STATION API）
      - fractional/不明: standin にフォールバック
    """
    if settings is None:
        return StandInBroker().get_account() or Account(0.0, 0.0, "JPY"), "standin"

    # v2.10: broker_provider 別 dispatcher（将来の自動売買接続のため）
    from trading_agent.utils.lot_size import get_broker_provider as _gbp

    provider = _gbp()

    # 楽天/SBI/モネックス: API なし、DB ベース（mark_filled 経由で記録）
    if provider in ("rakuten", "sbi", "monex"):
        try:
            from trading_agent.brokers.rakuten import RakutenBroker

            from trading_agent.utils.lot_size import get_broker_mode as _gbm
            broker = RakutenBroker(engine=engine, broker_mode=_gbm())
            acct = broker.get_account()
            if acct is not None:
                return acct, provider
        except Exception as exc:
            _log.warning(
                "rakuten_broker_failed",
                provider=provider,
                error_type=type(exc).__name__,
            )
        return StandInBroker().get_account() or Account(0.0, 0.0, "JPY"), "standin"

    # kabu.com: Phase 2 で実装（現状は未実装で standin にフォールバック）
    if provider == "kabucom":
        try:
            from trading_agent.brokers.kabucom import KabuComBroker

            broker = KabuComBroker.from_settings(settings)
            acct = broker.get_account()
            if acct is not None:
                return acct, "kabucom"
        except BrokerUnavailable:
            _log.info("kabucom_not_implemented_using_standin")
        except Exception as exc:
            _log.warning(
                "kabucom_broker_failed",
                error_type=type(exc).__name__,
            )
        return StandInBroker().get_account() or Account(0.0, 0.0, "JPY"), "standin"

    # 以下は既存の moomoo 経路（broker_provider == "moomoo" or その他）
    trading_mode = getattr(settings, "trading_mode", "paper")
    overlay = int(getattr(settings, "paper_overlay_cash_jpy", 0) or 0)

    if trading_mode == "live":
        # v2.10: broker_provider != "moomoo" なら moomoo OpenD 接続を試みない
        # （楽天/SBI/kabu.com 等では別経路で約定。同期残高は overlay or 手動 mark_filled で管理）
        from trading_agent.utils.lot_size import get_broker_provider as _gbp

        if _gbp() == "moomoo":
            try:
                from trading_agent.brokers.moomoo import MoomooBroker

                acct = MoomooBroker.from_settings(settings).get_account()
                if acct is not None:
                    return acct, "moomoo"
                _log.warning("moomoo_account_empty_fallback_standin")
            except BrokerUnavailable as exc:
                _log.warning("moomoo_account_unavailable_fallback_standin", reason=str(exc))
        return StandInBroker().get_account() or Account(0.0, 0.0, "JPY"), "standin"

    # v2.10: broker_provider != "moomoo" のときは moomoo OpenD 接続を試みない
    # （ECONNREFUSED のリトライで build_snapshot が無限ループする問題を防ぐ）
    from trading_agent.utils.lot_size import get_broker_provider as _gbp

    moomoo_cash = 0.0
    if _gbp() == "moomoo":
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

    # v2.10: broker_mode 別に集計（paper / live 並行運用時のコスト混在バグ防止）
    from trading_agent.utils.lot_size import get_broker_mode as _get_bm

    active_cost = _active_portfolio_cost_jpy(engine, broker_mode=_get_bm())
    cash = moomoo_cash + float(overlay) - active_cost
    # v2.6 修正: total_assets = 現金 + 保有銘柄の取得コスト = 預けた額そのまま
    # （取得コストを「時価近似」として加算。yfinance を叩かない分軽量で
    # snapshot 側で時価を上書きする責務にしてもいい。ここでは取得コストで近似）
    total_assets = cash + active_cost
    return Account(cash=cash, total_assets=total_assets, currency="JPY"), "moomoo+overlay"


def _active_portfolio_cost_jpy(
    engine: Any,
    personality: str | None = None,
    broker_mode: str | None = None,
) -> float:
    """紙約定で取得した active な Portfolio の累計コスト（JPY 建て）。

    `personality` 指定時はその性格の active 行のみ集計。None 時は全 active 集計。
    v2.10: broker_mode フィルタ追加（指定時はその broker_mode のみ。
    paper/live 並行運用時に試験運用と楽天本番のコストが混ざるバグを防止）。
    """
    from trading_agent.models.portfolio import Portfolio

    with Session(engine) as s:
        stmt = select(Portfolio).where(col(Portfolio.status) == "active")
        if personality is not None:
            stmt = stmt.where(col(Portfolio.personality) == personality)
        if broker_mode is not None:
            stmt = stmt.where(col(Portfolio.broker_mode) == broker_mode)
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
    # v2.10: 性格別 + broker_mode 別の集計（並行運用時の混在防止）
    from trading_agent.utils.lot_size import get_broker_mode as _get_bm

    active_cost = _active_portfolio_cost_jpy(
        engine, personality=personality, broker_mode=_get_bm()
    )
    cash = float(overlay_cash_jpy) - active_cost
    return Account(cash=cash, total_assets=cash, currency="JPY")
