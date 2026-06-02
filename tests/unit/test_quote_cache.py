"""BT-1 補助: J-Quants 価格キャッシュの単体テスト（一時 DB・API 非依存）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from trading_agent.backtest.quote_cache import (
    cached_codes,
    load_quotes,
    store_quotes,
)


def _q(n: int) -> list[tuple[dt.date, float]]:
    base = dt.date(2025, 1, 1)
    return [(base + dt.timedelta(days=i), 1000.0 + i) for i in range(n)]


class TestQuoteCache:
    def test_store_and_load_roundtrip(self, tmp_path: Path) -> None:
        p = tmp_path / "c.sqlite"
        store_quotes("7203", _q(5), path=p)
        got = load_quotes("7203", dt.date(2025, 1, 1), dt.date(2025, 1, 31), path=p)
        assert len(got) == 5
        assert got[0] == (dt.date(2025, 1, 1), 1000.0)

    def test_date_range_filter(self, tmp_path: Path) -> None:
        p = tmp_path / "c.sqlite"
        store_quotes("7203", _q(10), path=p)
        got = load_quotes("7203", dt.date(2025, 1, 3), dt.date(2025, 1, 5), path=p)
        assert [d for d, _ in got] == [dt.date(2025, 1, 3), dt.date(2025, 1, 4), dt.date(2025, 1, 5)]

    def test_no_duplicate_on_re_store(self, tmp_path: Path) -> None:
        # 同一 (code,date) は再 store で重複しない（再 fetch 抑止の前提）
        p = tmp_path / "c.sqlite"
        store_quotes("7203", _q(3), path=p)
        store_quotes("7203", _q(3), path=p)  # 同じ区間を再投入
        got = load_quotes("7203", dt.date(2025, 1, 1), dt.date(2025, 12, 31), path=p)
        assert len(got) == 3  # 重複なし

    def test_cached_codes(self, tmp_path: Path) -> None:
        p = tmp_path / "c.sqlite"
        store_quotes("7203", _q(2), path=p)
        store_quotes("6758", _q(2), path=p)
        assert cached_codes(path=p) == {"7203", "6758"}


class TestCacheSufficient:
    """codex 指摘 2: 両端カバレッジ判定（len>63 だけだと片寄りを見逃す）。"""

    def test_sufficient_when_full_range(self) -> None:
        from scripts.backtest_v3_slice import _cache_sufficient
        cached = _q(100)  # 2025-01-01 から 100 日連続
        frm, to = dt.date(2025, 1, 1), dt.date(2025, 4, 10)
        assert _cache_sufficient(cached, frm, to) is True

    def test_insufficient_when_one_sided(self) -> None:
        # 70 本あるが全部 frm 付近に偏り、to 近辺が欠落 → 不十分
        from scripts.backtest_v3_slice import _cache_sufficient
        cached = _q(70)  # 2025-01-01..03-11 のみ
        frm, to = dt.date(2025, 1, 1), dt.date(2025, 11, 1)  # to が遠い
        assert _cache_sufficient(cached, frm, to) is False

    def test_insufficient_when_too_few(self) -> None:
        from scripts.backtest_v3_slice import _cache_sufficient
        assert _cache_sufficient(_q(30), dt.date(2025, 1, 1), dt.date(2025, 1, 31)) is False
