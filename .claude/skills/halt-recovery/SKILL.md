---
name: halt-recovery
description: HALT 発火時の状態確認と安全な解除手順。理由表示・解除前確認・Discord 通知付き。
---

# HALT 復旧スキル

INVESTIGELION の auto モード（または手動）で HALT が発火した場合、安全に解除するためのスキル。

## 背景

HALT は **安全装置**：以下の条件で発火する。
- ポートフォリオ累計 DD ≤ -15%
- 日次 DD ≤ -7%
- 約定失敗 3 営業日連続

HALT 中は auto モードの執行系（close_due / auto_fill）が止まる。自動解除はしない（同じ問題で再発火を防ぐため）。

## このスキルの目的

1. HALT 状態を確認（発火中か / 理由 / 発火時刻）
2. **解除前の必須確認**（DD 状況、市場状況、原因把握）
3. ユーザー承認後に解除実行
4. 解除イベントを Discord に通知（監査ログ）

## 実行手順

### 1. HALT 状態確認

```bash
.venv/bin/python -c "
from trading_agent.portfolio.anomaly_detector import get_halt_state
import json
state = get_halt_state()
print(json.dumps(state, ensure_ascii=False, indent=2, default=str))
"
```

結果がない場合：「HALT 状態ではありません。解除不要。」と返して終了。

### 2. 発火理由の説明

HALT 中なら、以下の情報を整理して提示：

```
🛑 HALT 発火中

理由 (reason): {reason}
発火元 (source): {source}
発火時刻 (triggered_at): {triggered_at}

⚠️ 解除前に必ず確認:
- DD 関連の HALT なら、市場状況・保有銘柄の損失額を確認
- 約定失敗の HALT なら、brokers/yfinance 等の障害がないか確認
- auto モードを継続するか、一旦 manual に切り替えるか判断
```

### 3. 解除前のチェックリスト提示

```
解除前チェック:

[ ] HALT 理由を読み、何が起きたか把握した
[ ] DD 関連なら市場状況を確認した（暴落？ノイズ？）
[ ] 約定失敗関連なら API 障害を確認した
[ ] 同じ原因で再発火しない対策を検討した
[ ] auto モード継続 or manual 切替の判断をした

すべてチェックが入ったら『解除します』と答えてください。
チェック不十分なら『中止』と答えてください。
```

### 4. ユーザー承認後の解除実行

```bash
.venv/bin/python scripts/clear_halt.py --force
```

スクリプト内で自動的に Discord 通知が送信される（WILLE_DISCORD_WEBHOOK_URL 設定時）。

### 5. 解除後の状態確認

```bash
.venv/bin/python -c "
from trading_agent.portfolio.anomaly_detector import is_halted
print(f'is_halted: {is_halted()}')
"
```

「HALT 解除完了 ✓」と報告。

## エラー対応

### HALT ファイルが読めない場合
```
~/.trading-agent/HALT が存在するが読み取り失敗。
ファイル権限を確認してください。
```

### Discord 通知失敗
HALT 解除自体は成功している。Discord 通知失敗は warn ログのみ（致命ではない）。

## 関連ドキュメント

- [docs/HALT_RECOVERY.md](../../../docs/HALT_RECOVERY.md) — 詳細
- [scripts/clear_halt.py](../../../scripts/clear_halt.py) — 解除 CLI

## 重要な仕様

- **自動解除は絶対にしない**（同じ問題で再発火するため）
- **解除イベントは Discord に通知**（誤解除の検知）
- **解除前にユーザーが原因把握したか確認**
