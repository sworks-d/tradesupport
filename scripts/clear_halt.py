"""HALT 状態を解除する CLI（v2.10 Phase I-10 関連・致命候補 4 修正）。

使い方:
  .venv/bin/python scripts/clear_halt.py                # 確認プロンプト付き
  .venv/bin/python scripts/clear_halt.py --force        # 確認なしで解除

挙動:
  - HALT 状態を表示（理由・発火元・発火時刻）
  - 確認後、~/.trading-agent/HALT を削除
  - 解除完了を Discord に通知（WILLE_DISCORD_WEBHOOK_URL 設定時）

設計意図:
  - HALT は安全装置なので自動解除しない（同じ問題で再発火するため）
  - 解除は明示的な人間操作のみ
  - 解除イベントは Discord で記録（誤解除の検知）
"""

from __future__ import annotations

import sys

from trading_agent.portfolio.anomaly_detector import (
    clear_halt,
    get_halt_state,
    is_halted,
)


def main() -> int:
    force = "--force" in sys.argv

    state = get_halt_state()
    if not state.get("halted", False):
        print("HALT 状態ではありません（解除不要）。")
        return 0

    print("=== 現在の HALT 状態 ===")
    print(f"  reason       : {state.get('reason', '(不明)')}")
    print(f"  source       : {state.get('source', '(不明)')}")
    print(f"  triggered_at : {state.get('triggered_at', '(不明)')}")
    print()

    if not force:
        ans = input("HALT を解除しますか？ [y/N]: ").strip().lower()
        if ans not in ("y", "yes"):
            print("解除をキャンセルしました。")
            return 1

    cleared = clear_halt()
    if cleared:
        print("✓ HALT を解除しました。")
        # Discord 通知（解除イベントの監査ログ）
        try:
            from trading_agent.utils.discord_notifier import notify_discord

            notify_discord(
                f"✅ **HALT 解除**\n以前の理由: {state.get('reason', '(不明)')}\n"
                f"発火元: {state.get('source', '(不明)')}",
                level="info",
                title="WILLE HALT CLEARED",
            )
        except Exception as exc:
            print(f"  (Discord 通知失敗: {type(exc).__name__})")
        return 0

    print("解除に失敗しました（既に解除済みの可能性）。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
