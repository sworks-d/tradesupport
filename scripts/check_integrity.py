"""データ整合性チェック CLI（v2.10 P8）。

使い方:
  .venv/bin/python scripts/check_integrity.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from trading_agent.db import get_engine
from trading_agent.portfolio.integrity_check import run_integrity_check


def main() -> int:
    engine = get_engine(Path("data") / "trading.sqlite")
    result = run_integrity_check(engine)
    print(f"=== データ整合性チェック ===")
    print(f"  checked: {result.checked} 行")
    print(f"  issues: {len(result.issues)} 件")
    if not result.issues:
        print("\n✓ 整合性 OK")
        return 0
    print()
    from collections import Counter

    by_kind = Counter(i["kind"] for i in result.issues)
    print("== 種類別 ==")
    for kind, n in by_kind.most_common():
        print(f"  {kind}: {n} 件")
    print()
    print("== 詳細 (上位 10 件) ==")
    for issue in result.issues[:10]:
        print(f"  {issue}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
