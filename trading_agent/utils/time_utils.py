"""時刻ユーティリティ（SYSTEM_DESIGN.md §B-2: UTC で永続化、JST で表示）。

永続化する時刻は naive UTC（``utcnow()``）。SQLite/SQLAlchemy の DateTime は
tz 情報を保持しないため、保存値は naive UTC に統一する。表示時に ``to_jst()`` で変換。

NOTE: ``utcnow`` は ``trading_agent.models._common.utcnow`` と同一契約（naive UTC）。
現状は両所に同実装が存在する（モデル層の import 安定性を優先）。将来この
``time_utils`` に集約し、``_common`` から再エクスポートする整理を想定（朝の確認事項）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

# 日本標準時
JST = timezone(timedelta(hours=9))


def utcnow() -> datetime:
    """naive な UTC 現在時刻を返す（SQLite の DateTime カラム互換）。

    ``datetime.utcnow()`` は非推奨のため、tz-aware で取得して tzinfo を落とす。
    SQLModel の ``default_factory`` にそのまま渡せる。
    """
    return datetime.now(UTC).replace(tzinfo=None)


def today_jst() -> date:
    """JST の「今日の日付」を返す（v2.10 致命候補 1 修正）。

    朝バッチ・日次レポート・Decision.date 等の **業務日付** 用。
    ``utcnow().date()`` を使うと JST 朝 7:00 実行時に UTC 前日が返るため、
    invocation_id や Decision.date 等の業務キーには不向き。

    datetime（タイムスタンプ・created_at 等）は引き続き UTC 永続化（``utcnow()``）。
    """
    return datetime.now(JST).date()


def to_jst(value: datetime) -> datetime:
    """UTC（または任意 tz / naive）の datetime を JST に変換する。

    naive datetime は UTC とみなす（永続化値が naive UTC のため）。
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(JST)


def to_utc(value: datetime) -> datetime:
    """JST（または任意 tz / naive）の datetime を naive UTC に変換する。

    naive datetime は JST とみなす（ユーザー入力は JST 由来が多いため）。
    永続化用に tzinfo を落とした naive UTC を返す。
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=JST)
    return value.astimezone(UTC).replace(tzinfo=None)
