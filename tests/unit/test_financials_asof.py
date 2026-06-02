"""BT-0: fetch_financials_asof の PIT（look-ahead 回避）単体テスト。API 非依存・fixtures。"""

from __future__ import annotations

import datetime as dt

from trading_agent.screening.financials import (
    _parse_disclosed_date,
    fetch_financials_asof,
)


def _stmt(disclosed: str, sales: str, op: str, np_: str) -> dict:
    return {"DisclosedDate": disclosed, "CurPerEn": disclosed, "Sales": sales, "OP": op, "NP": np_}


class TestParseDisclosedDate:
    def test_iso(self) -> None:
        assert _parse_disclosed_date({"DisclosedDate": "2025-05-10"}) == dt.date(2025, 5, 10)

    def test_slash(self) -> None:
        assert _parse_disclosed_date({"DiscDate": "2025/05/10"}) == dt.date(2025, 5, 10)

    def test_yyyymmdd(self) -> None:
        assert _parse_disclosed_date({"DisclosedDate": "20250510"}) == dt.date(2025, 5, 10)

    def test_none_when_missing(self) -> None:
        assert _parse_disclosed_date({"Sales": "100"}) is None


class TestFetchFinancialsAsof:
    def _three(self) -> list[dict]:
        return [
            _stmt("2024-05-10", "100", "10", "8"),  # 古い
            _stmt("2024-11-10", "110", "12", "9"),
            _stmt("2025-05-10", "120", "14", "10"),  # 新しい
        ]

    def test_excludes_future_disclosed(self) -> None:
        # as_of=2025-01-01 → 2025-05-10 開示は未来 → 除外（look-ahead 回避）
        fin = fetch_financials_asof("7203", self._three(), dt.date(2025, 1, 1))
        assert fin is not None
        # current = 直近で as_of 以前に開示された 2024-11-10 の期
        assert fin.current.revenue == 110.0
        assert fin.prior is not None and fin.prior.revenue == 100.0
        assert fin.prior2 is None  # as_of 前は 2 期のみ

    def test_includes_all_when_asof_after(self) -> None:
        fin = fetch_financials_asof("7203", self._three(), dt.date(2025, 6, 1))
        assert fin is not None
        assert fin.current.revenue == 120.0  # 最新
        assert fin.prior2 is not None  # 3 期揃う

    def test_none_when_all_future(self) -> None:
        fin = fetch_financials_asof("7203", self._three(), dt.date(2024, 1, 1))
        assert fin is None  # as_of 前の開示が無い

    def test_excludes_statements_without_disclosed_date(self) -> None:
        # 開示日が無い statement は保守的に除外（未来財務の silent 混入防止）
        stmts = [{"Sales": "999", "OP": "99", "NP": "90"}]  # 開示日なし
        assert fetch_financials_asof("7203", stmts, dt.date(2025, 6, 1)) is None

    def test_real_jquants_schema(self) -> None:
        # 実 API dry-read で確認した実スキーマ（DiscDate=日付型, Sales/OP/NP/TA/CFO）を固定。
        # 財務値が None でなく populate されることを確認（列名ミスマッチの退行検出）。
        stmts = [{
            "DiscDate": dt.datetime(2025, 5, 10), "CurPerEn": "2025-03-31",
            "Sales": "45000000", "OP": "5000000", "NP": "4000000",
            "TA": "90000000", "CFO": "4200000", "EPS": "300.0",
        }]
        fin = fetch_financials_asof("7203", stmts, dt.date(2025, 6, 1))
        assert fin is not None
        assert fin.current.revenue == 45000000.0   # Sales
        assert fin.current.ebit == 5000000.0        # OP
        assert fin.current.net_income == 4000000.0  # NP
        assert fin.current.total_assets == 90000000.0  # TA
