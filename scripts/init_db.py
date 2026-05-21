#!/usr/bin/env python
"""DB 初期化スクリプト（Task 1.0.4）。

空の SQLite DB を作成し、全テーブルを定義、settings にデフォルト値を投入する。

実行::

    uv run python scripts/init_db.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# uv の editable install 経由でなくても import できるようにする保険
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_agent.config import get_settings  # noqa: E402
from trading_agent.db import init_database  # noqa: E402
from trading_agent.utils.logger import configure_logging, log  # noqa: E402


def main() -> None:
    settings = get_settings()  # ~/.trading-agent/ を自動作成
    configure_logging()
    result = init_database(settings.db_path)
    log.info("db_initialized", **result)

    print(f"✅ DB 初期化完了: {result['db_path']}")
    print(f"   テーブル数: {result['tables']}")
    print(f"   settings 追加: {result['settings_inserted']} 件")


if __name__ == "__main__":
    main()
