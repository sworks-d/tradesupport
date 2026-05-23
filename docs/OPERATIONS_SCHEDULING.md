# 定期実行（スケジューリング）— macOS launchd

朝バッチ（候補→MAGI→decision永続）と評価ジョブ（測って上げる）を毎朝自動実行する設定。
※決裁・発注は moomoo 画面で**手動**（自動発注はしない＝Tier1）。自動化するのは「判断の生成」と「評価」まで。

## 実行されるもの
| ジョブ | スクリプト | 既定時刻 | 内容 |
|---|---|---|---|
| 朝バッチ | `scripts/run_morning_batch.py` | 07:00 | universe→screening→MAGI→decision永続（弾ON：信用性/EDINET） |
| 評価 | `scripts/run_evaluation.py` | 07:30 | 評価期日到来の decision を実価格で採点＋Track Record |

前提：`.env`（ANTHROPIC/MOOMOO/EDINET 等）設定済、`scripts/load_universe.py` 実行済、`uv sync` 済。

## 設置手順（launchd）
plist 内のパスは `/Users/a05/tradesupport`・`.venv/bin/python` を前提（環境が違えば書き換え）。

```sh
mkdir -p data/logs
# plist を LaunchAgents に配置（コピー）
cp ops/launchd/com.tradesupport.morning-batch.plist ~/Library/LaunchAgents/
cp ops/launchd/com.tradesupport.evaluation.plist    ~/Library/LaunchAgents/
# 読み込み（macOS 13+ は bootstrap 推奨）
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradesupport.morning-batch.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradesupport.evaluation.plist
# 確認 / 手動キック / 解除
launchctl list | grep tradesupport
launchctl kickstart -k gui/$(id -u)/com.tradesupport.morning-batch   # 即時テスト実行
launchctl bootout gui/$(id -u)/com.tradesupport.morning-batch        # 解除
```

ログ：`data/logs/*.log`（gitignore 対象の data/ 配下）。

## 注意
- **コスト**：朝バッチは market_analyst 等で LLM/ネットを使う＝日次¥500枠を意識。重ければ
  `run_morning_batch.py --no-quality`（弾OFF・決定論のみ）に切替可。
- **平日のみ**にしたい場合は plist の `StartCalendarInterval` を Weekday 1〜5 の複数 dict に。
- スリープ中は launchd がスキップ／復帰後に遅延実行（`StartCalendarInterval` の挙動）。常時起動でなければ
  起床後に手動 `launchctl kickstart` でも可。
- cron 派は `crontab -e` で `0 7 * * 1-5 cd /Users/a05/tradesupport && .venv/bin/python scripts/run_morning_batch.py` でも同等。
