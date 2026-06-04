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

    def test_nan_none_inf_not_stored(self, tmp_path: Path) -> None:
        # 実害バグ回帰: AdjC=NaN(売買停止日)が SQLite で NULL 化 → load で float(None) クラッシュ。
        # store 側で NaN/None/inf/0以下 を弾く（有限 ∧ 正のみ・codex 指摘で 0以下も追加）。
        p = tmp_path / "c.sqlite"
        base = dt.date(2025, 1, 1)
        dirty = [
            (base, 1000.0),
            (base + dt.timedelta(days=1), float("nan")),    # 売買停止日相当
            (base + dt.timedelta(days=2), None),            # 欠損
            (base + dt.timedelta(days=3), float("inf")),    # 異常値
            (base + dt.timedelta(days=4), 0.0),             # 0 円（価格系列として異常）
            (base + dt.timedelta(days=5), -5.0),            # 負値
            (base + dt.timedelta(days=6), 1006.0),
        ]
        stored = store_quotes("7203", dirty, path=p)
        assert stored == 2  # 有限 ∧ 正の 2 件だけ
        got = load_quotes("7203", base, base + dt.timedelta(days=10), path=p)
        assert [c for _, c in got] == [1000.0, 1006.0]  # load も壊れない

    def test_fetch_valid_price_rejects_inf_in_run(self) -> None:
        # codex 指摘: _fetch_quotes が NaN だけ弾き inf を見逃すと、store はされなくても
        # その run の q に inf が混ざり backtest に渡る。_valid_price 共通化で塞ぐ。
        from trading_agent.backtest.quote_cache import _valid_price
        assert _valid_price(1234.5) == 1234.5
        assert _valid_price(float("nan")) is None
        assert _valid_price(float("inf")) is None
        assert _valid_price(float("-inf")) is None
        assert _valid_price(0.0) is None
        assert _valid_price(-1.0) is None
        assert _valid_price(None) is None
        assert _valid_price("abc") is None

    def test_load_skips_legacy_null_rows(self, tmp_path: Path) -> None:
        # 旧 cache に既に NULL(adjc) が残っていても load は壊れず skip する（防御）。
        import sqlite3
        p = tmp_path / "c.sqlite"
        store_quotes("7203", _q(3), path=p)
        conn = sqlite3.connect(p)
        conn.execute(
            "INSERT OR REPLACE INTO jquants_daily_quotes(code, date, adjc, fetched_at) "
            "VALUES ('7203', '2025-01-10', NULL, '')"
        )
        conn.commit()
        conn.close()
        got = load_quotes("7203", dt.date(2025, 1, 1), dt.date(2025, 1, 31), path=p)
        assert len(got) == 3  # NULL 行は無視


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
