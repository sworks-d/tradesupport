"""ブローカー層の単体テスト（Phase 1.2）。

口座未接続でもスタンドインへ落ちて全体が動くことを保証する。
SDK（moomoo/futu）はテスト環境に未導入のため、moomoo経路は BrokerUnavailable で
スタンドインへフォールバックする想定。
"""

from __future__ import annotations

from trading_agent.brokers import StandInBroker, load_positions
from trading_agent.brokers.base import BrokerClient, Position


def test_standin_real_state_cash_no_positions() -> None:
    broker = StandInBroker()
    assert broker.get_positions() == []  # 実稼働初期＝保有0
    acct = broker.get_account()
    assert acct is not None
    assert acct.cash == 1_000_000.0  # 仮の運用元本
    assert acct.total_assets == 1_000_000.0
    assert acct.currency == "JPY"


def test_standin_satisfies_broker_protocol() -> None:
    assert isinstance(StandInBroker(), BrokerClient)


def test_load_positions_default_uses_standin() -> None:
    positions, source = load_positions(prefer_moomoo=False)
    assert source == "standin"
    assert positions == []  # 実稼働初期＝保有0


def test_position_shape() -> None:
    p = Position(code="AAPL", qty=1, cost_price=289.91, currency="USD")
    assert p.nominal_price is None  # moomoo提供時のみ埋まる
    assert p.pl_ratio is None


class _FakeDF:
    """DataFrame.iterrows() を模した最小フェイク（dict 行）。"""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def iterrows(self):  # noqa: ANN201
        return enumerate(self._rows)


def test_moomoo_parse_strips_market_prefix_and_maps_fields() -> None:
    from trading_agent.brokers.moomoo import MoomooBroker

    rows = [
        {
            "code": "US.AAPL",
            "qty": 2,
            "cost_price": 100.0,
            "currency": "USD",
            "nominal_price": 110.0,
            "market_val": 220.0,
            "pl_ratio": 10.0,
            "pl_val": 20.0,
        }
    ]
    positions = MoomooBroker()._parse(_FakeDF(rows))
    assert positions[0].code == "AAPL"  # "US." 接頭辞を除去
    assert positions[0].qty == 2.0
    assert positions[0].pl_ratio == 10.0
    assert positions[0].nominal_price == 110.0
