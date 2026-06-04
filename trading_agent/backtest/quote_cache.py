"""BT-1 補助: J-Quants 価格キャッシュ（Free レート保護）。

codex 指摘の「Free API を実験対象でなく貴重なデータ補給源として扱う」ための層。
- 取得済み (code, date)→AdjC を独立 SQLite（data/jquants_cache.sqlite）に永続化。
- cache-first: 必要期間が cache に揃っていれば API を叩かない。
- 同一 (code, date) は再 fetch しない（PRIMARY KEY で重複防止）。
- 本番 trading.sqlite を汚さない（別ファイル）。

純粋なキャッシュ層（fetch 自体は呼び出し側が client で行い、store/load だけ担う）。
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

_CACHE_PATH = Path("data") / "jquants_cache.sqlite"


def _conn(path: Path | None = None) -> sqlite3.Connection:
    p = path or _CACHE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p)
    c.execute(
        "CREATE TABLE IF NOT EXISTS jquants_daily_quotes ("
        "code TEXT, date TEXT, adjc REAL, fetched_at TEXT, PRIMARY KEY(code, date))"
    )
    return c


def _valid_price(adj) -> float | None:
    """有限かつ正の価格のみ返す。None/NaN/inf/0以下/変換不能は None（codex 指摘）。

    SQLite は NaN を NULL 化し load の float(None) で壊れる。inf や 0以下は価格系列として
    異常値なので、推測補完せず弾く（H10: 推測しない）。store/load/fetch で共通利用。
    """
    if adj is None:
        return None
    try:
        v = float(adj)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")) or v <= 0:  # NaN / ±inf / 0以下
        return None
    return v


def store_quotes(
    code: str, quotes: list[tuple[dt.date, float]], *, path: Path | None = None,
    now: str = "",
) -> int:
    """(date, AdjC) を upsert。再 fetch 抑止のため既存は置換。NaN/None/inf/0以下 行は保存しない。"""
    rows = [
        (code, d.isoformat(), v, now)
        for d, adj in quotes
        if (v := _valid_price(adj)) is not None
    ]
    if not rows:
        return 0
    c = _conn(path)
    try:
        c.executemany(
            "INSERT OR REPLACE INTO jquants_daily_quotes(code, date, adjc, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            rows,
        )
        c.commit()
        return len(rows)
    finally:
        c.close()


def load_quotes(
    code: str, frm: dt.date, to: dt.date, *, path: Path | None = None
) -> list[tuple[dt.date, float]]:
    """cache から [frm, to] の (date, AdjC) を古→新で返す。"""
    c = _conn(path)
    try:
        rows = c.execute(
            "SELECT date, adjc FROM jquants_daily_quotes "
            "WHERE code = ? AND date >= ? AND date <= ? ORDER BY date",
            (code, frm.isoformat(), to.isoformat()),
        ).fetchall()
    finally:
        c.close()
    # 防御的: 旧 cache に NULL/inf/0以下 が残っていても壊れず skip（NaN 由来の残骸対策）
    out: list[tuple[dt.date, float]] = []
    for d, a in rows:
        v = _valid_price(a)
        if v is not None:
            out.append((dt.date.fromisoformat(d), v))
    return out


def cached_codes(*, path: Path | None = None) -> set[str]:
    """cache に何らかの価格がある code 集合。"""
    c = _conn(path)
    try:
        return {r[0] for r in c.execute("SELECT DISTINCT code FROM jquants_daily_quotes")}
    finally:
        c.close()
