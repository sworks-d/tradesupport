"""モデル共通のユーティリティ。"""

from __future__ import annotations

import datetime as dt


def utcnow() -> dt.datetime:
    """tz-naive な UTC 現在時刻を返す（SYSTEM_DESIGN.md §B-2: UTC で永続化）。

    ``datetime.utcnow`` は Python 3.12 で非推奨のため、tz-aware で取得して
    tzinfo を落とす。表示時に JST へ変換する（責務は表示層）。
    """
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)
