"""今朝の発注リスト HTML を生成（v2.10 楽天かぶミニ運用向け）。

使い方:
  .venv/bin/python scripts/generate_order_list.py             # 今日分を生成
  .venv/bin/python scripts/generate_order_list.py 2026-05-31  # 日付指定

出力: autoreport/orders/YYYY-MM-DD.html
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from trading_agent.db import get_engine
from trading_agent.reporting.order_list import generate_order_list


def main() -> int:
    engine = get_engine(Path("data") / "trading.sqlite")
    date = None
    if len(sys.argv) > 1:
        try:
            date = dt.date.fromisoformat(sys.argv[1])
        except ValueError:
            print(f"日付形式が不正: {sys.argv[1]} (YYYY-MM-DD を期待)")
            return 1

    path = generate_order_list(engine, date=date)
    print(f"✓ 発注リスト生成: {path}")
    print(f"  ブラウザで開く: file://{path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
