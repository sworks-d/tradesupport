"""テスト全体の共通設定（v2.8 / v2.9）。

- WILLE_PAPER_MODE=1: Paper モード（1 株単位 fill）で動かす（既存テスト互換）
- WILLE_OPPORTUNITY_FILL=0: 旧 needs-based 配分を使う（既存テスト互換）
  機会駆動モードは新規実装のため、専用テストで別途検証する
"""

from __future__ import annotations

import os


def pytest_configure(config):  # noqa: ARG001
    """テスト開始時の環境変数固定。"""
    os.environ.setdefault("WILLE_PAPER_MODE", "1")
    os.environ.setdefault("WILLE_OPPORTUNITY_FILL", "0")
