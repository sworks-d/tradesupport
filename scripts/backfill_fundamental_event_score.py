"""(c) 既存 verified decision の fundamental_event_score を PIT 安全に backfill（一回限り）。

実行（適用）: .venv/bin/python scripts/backfill_fundamental_event_score.py --apply
ドライ（既定）: .venv/bin/python scripts/backfill_fundamental_event_score.py

背景:
  `_apply_event_score`（magi/persist.py）は (c) デプロイ後に verify した decision にのみ score を
  付与する。デプロイ前に verify 済の decision は fundamental_event_score=NULL のまま →
  forward/edge では unscored に隔離される（誤りではないが score 別測定が空になる）。

何をするか:
  確定済 `entry_signal_tags`（verify 時に PIT 固定）から `compute_fundamental_event_score` で
  score を**再計算するだけ**の純関数 backfill。look-ahead 無し（タグは過去に確定済）。
  独自 SQL UPDATE はせず ORM 経由で、`_apply_event_score` と同じ式・version を使う。

対象範囲（測定に効く live ステータスのみ）:
  status in (awaiting, approved, ordered, filled) かつ fundamental_event_score IS NULL。
  cancelled はデッド（forward/edge 集計対象外）でノイズになるため**除外**。
  既に score 済（=(c) デプロイ後 verify）は触らない。

安全性:
  record-only（売買・gate・status を一切変えない・score 2カラムのみ）。reversible（NULL に戻せる）。
  --apply 時は更新前に DB を timestamp 付きでバックアップ。commit/--apply は master が実行。
"""

from __future__ import annotations

import datetime as dt
import shutil
import sys
from pathlib import Path

from sqlmodel import Session, select

from trading_agent.db import get_engine
from trading_agent.models.decisions import Decision
from trading_agent.screening.event_score import (
    EVENT_SCORE_VERSION,
    bucket_event_score,
    compute_fundamental_event_score,
)

# 測定に効く live ステータスのみ（cancelled 等のデッドは除外）。
_TARGET_STATUSES = ("awaiting", "approved", "ordered", "filled")


def _backup_db(db: Path) -> Path:
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = db.with_name(f"{db.name}.bak_backfill_score_{ts}")
    shutil.copy2(db, dst)
    return dst


def _targets(session: Session) -> list[Decision]:
    rows = session.exec(
        select(Decision).where(
            Decision.fundamental_event_score.is_(None),  # type: ignore[union-attr]
            Decision.status.in_(_TARGET_STATUSES),  # type: ignore[union-attr]
        )
    ).all()
    return list(rows)


def main() -> None:
    apply = "--apply" in sys.argv
    db = Path("data/trading.sqlite")
    if not db.exists():
        print(f"DB not found: {db}")
        return

    engine = get_engine(db)
    with Session(engine) as session:
        targets = _targets(session)

        if not targets:
            print("✅ backfill 対象なし（live ステータスの NULL-score decision は 0 件）")
            return

        # プレビュー: backfill 後の bucket 分布（誤りなく見せる）。
        dist: dict[str, int] = {}
        for d in targets:
            score = compute_fundamental_event_score(d.entry_signal_tags or [])
            b = bucket_event_score(score, EVENT_SCORE_VERSION).bucket
            dist[b] = dist.get(b, 0) + 1

        print(f"対象 {len(targets)} 件（status in {_TARGET_STATUSES} かつ score NULL）")
        print(f"  backfill 後 bucket 分布見込み: {dict(sorted(dist.items()))}")
        print(f"  version: {EVENT_SCORE_VERSION}")

        if not apply:
            print("  → 適用するには --apply を付けて実行（master 承認後）")
            return

        backup = _backup_db(db)
        print(f"🛟 backup: {backup}")

        n = 0
        for d in targets:
            d.fundamental_event_score = compute_fundamental_event_score(
                d.entry_signal_tags or []
            )
            d.event_score_version = EVENT_SCORE_VERSION
            session.add(d)
            n += 1
        session.commit()
        print(f"✅ backfilled: {n} 件に fundamental_event_score を付与（record-only）")


if __name__ == "__main__":
    main()
