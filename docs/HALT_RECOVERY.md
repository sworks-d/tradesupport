# HALT 復旧手順（v2.10 Phase I-10）

## HALT とは

WILLE の自動売買モードでは、以下の異常が検知されると **HALT 機構** が発火し、auto_fill / close_due / pyramid_check の自動執行が止まります。

| 検知条件 | 閾値 |
|---|---|
| ポートフォリオ累計 DD | ≤ -15% |
| ポートフォリオ日次 DD | ≤ -7% |
| 約定失敗の連続発生 | 3 連続以上 |

HALT 中は `~/.trading-agent/HALT` ファイルが存在し、JSON で発火理由が記録されます。

## HALT 発火時の挙動

- 朝バッチの `close_due` / `auto_fill` ノードが `status=skipped, reason=halted` で素通り
- I-11 設定済なら Discord に critical 通知（HALT 発火を即時通知）
- BatchState には記録される（DAG 自体は止まらない）

## 解除手順

### 推奨：CLI スクリプト

```bash
.venv/bin/python scripts/clear_halt.py
```

このスクリプトは：
1. 現在の HALT 理由・発火元・発火時刻を表示
2. y/N で確認
3. 削除後に Discord に通知（解除イベントの監査ログ）

### 緊急：手動削除

```bash
rm ~/.trading-agent/HALT
```

確認なし。Discord 通知も飛びません。

### バッチ環境（force モード）

```bash
.venv/bin/python scripts/clear_halt.py --force
```

確認プロンプトをスキップ。CI/CD や自動復旧スクリプトから呼ぶ場合。

## HALT 解除前に必ず確認すること

HALT は **安全装置** です。問題を解決せずに解除すると同じ事象で再発火します。

以下を確認してから解除してください：

- [ ] `~/.trading-agent/HALT` の reason を読み、何が起きたか把握した
- [ ] DD 関連の HALT なら、市場状況・保有銘柄の損失額を確認した
- [ ] 約定失敗の HALT なら、brokers/yfinance 等の障害がないか確認した
- [ ] auto モードを継続するか、一旦 manual モードに切り替えるか判断した

## HALT が自動解除されない設計理由

- 自動解除すると同じ問題で再発火するため
- 翌日 0:00 リセット等の自動解除は「気づかないうちに復旧」となり、原因追跡が困難になる
- 解除は人間判断の責任で行う

## 関連ファイル

- `trading_agent/portfolio/anomaly_detector.py` — HALT 発火・解除のロジック
- `scripts/clear_halt.py` — 解除 CLI
- `data/wille_settings.json` — automation_mode 設定
