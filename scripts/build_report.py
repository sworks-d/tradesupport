"""ダミーシステム日次レポートを autoreport/YYYY-MM-DD.html に出力する。

実行:
    uv run python scripts/build_report.py                  # 今日
    uv run python scripts/build_report.py --date 2026-05-28 # 指定日
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from trading_agent.db import get_engine
from trading_agent.reporting import build_report_payload, render_html


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD（既定：今日）")
    ap.add_argument(
        "--out-dir",
        default="autoreport",
        help="出力先ディレクトリ（既定：autoreport/）",
    )
    args = ap.parse_args()

    target_date = (
        dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    )
    engine = get_engine(Path("data") / "trading.sqlite")
    payload = build_report_payload(engine, target_date)
    html = render_html(payload)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{target_date.isoformat()}.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"✅ wrote {out_path} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
