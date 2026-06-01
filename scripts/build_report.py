"""ダミーシステム日次レポートを autoreport/YYYY-MM-DD.html に出力する。

実行後に同時実行されること:
- autoreport/index.html（履歴一覧 + 性格別累積 PnL 推移グラフ）
- autoreport/feedback_log.json（評価期日到来の MAGI 学習データ追記）

実行:
    uv run python scripts/build_report.py                  # 今日
    uv run python scripts/build_report.py --date 2026-05-28 # 指定日
    uv run python scripts/build_report.py --no-index       # index 更新スキップ
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from trading_agent.db import get_engine
from trading_agent.reporting import build_report_payload, render_html
from trading_agent.reporting.feedback import (
    append_feedback_log,
    collect_feedback_records,
)
from trading_agent.reporting.index_builder import write_index


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD（既定：今日）")
    ap.add_argument(
        "--out-dir",
        default="autoreport",
        help="出力先ディレクトリ（既定：autoreport/）",
    )
    ap.add_argument(
        "--no-index", action="store_true", help="index.html / feedback_log の更新をスキップ"
    )
    args = ap.parse_args()

    target_date = (
        dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    )
    engine = get_engine(Path("data") / "trading.sqlite")
    payload = build_report_payload(engine, target_date)
    html = render_html(payload)

    out_dir = Path(args.out_dir)
    # フォルダ構造: autoreport/{daily,weekly,monthly,data}
    daily_dir = out_dir / "daily"
    data_dir = out_dir / "data"
    daily_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    out_path = daily_dir / f"{target_date.isoformat()}.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"✅ wrote {out_path} ({len(html):,} bytes)")

    if not args.no_index:
        # フィードバックログを追記（評価期日到来の MAGI 学習データ）
        records = collect_feedback_records(engine)
        fb_path = append_feedback_log(
            records, out_path=data_dir / "feedback_log.json"
        )
        print(f"✅ feedback_log {fb_path}: {len(records)} 件評価済")

        # 履歴インデックスを更新（autoreport/index.html・凍結スナップショット一覧）
        index_path = write_index(out_dir)
        print(f"✅ index {index_path}")


if __name__ == "__main__":
    main()
