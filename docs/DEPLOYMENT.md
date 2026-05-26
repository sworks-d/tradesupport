# 別 PC（常駐機）への移行手順

作成日：2026-05-26
位置づけ：**開発機（このノート PC）→ 常駐機**（24/7 稼働）への移行手順書。
USER_CHECKLIST.md と組み合わせて使う（こちらは "移行" に特化、向こうは "ユーザー作業の一覧"）。

---

## 0. 前提と方針

| 項目 | 値 |
|---|---|
| 元本 | ¥100,000（D-23） |
| 月次追加 | ¥30,000-¥50,000（D-26） |
| 配分 | Core 60% / Satellite 20% / Cash 20%（D-26） |
| 市場 | JP 90% / US ETF 10%（D-25） |
| モード | **paper 運用**（実弾は増額ゲート達成後・D-23） |
| 常駐機 | macOS マシン（Mac mini 等を想定） |
| 移行 PC | このノートではない別マシン |

---

## 1. 常駐機のセットアップ（フェーズ1）

### 1.1 OS / ツール
- [ ] macOS 13+（M1/M2/M3 推奨）
- [ ] Homebrew インストール
- [ ] [uv](https://docs.astral.sh/uv/) インストール: `brew install uv`
- [ ] Git: `xcode-select --install`

### 1.2 ファイル配置
- [ ] 配置先決定（例：`/Users/<user>/tradesupport`）
- [ ] git clone 又は rsync で配置
- [ ] `cd <配置先>`
- [ ] `uv sync` — 依存解決
- [ ] `mkdir -p data data/logs`

### 1.3 .env 設定
- [ ] `cp .env.example .env`
- [ ] 編集：以下を設定
  ```
  ANTHROPIC_API_KEY=...        # 必須
  MOOMOO_TRADING_PWD=...       # 必須
  MOOMOO_ACCOUNT_ID=...        # 必須
  EDINET_API_KEY=...           # ★推奨（無料）
  JQUANTS_REFRESH_TOKEN=...    # ★推奨（無料）
  NEWSAPI_KEY=...              # 任意
  TRADING_MODE=paper           # ペーパー固定
  ```
- [ ] 確認：`uv run python -c "from trading_agent.config import load_settings; print('OK')"`

### 1.4 DB 初期化
- [ ] `uv run python scripts/init_db.py`
- [ ] 確認：`ls ~/.trading-agent/db.sqlite`

### 1.5 universe 投入（D-25 反映済）
- [ ] `uv run python scripts/load_universe.py`
- [ ] 確認：JP 30 + US ETF 2 = 32 銘柄が投入される

### 1.6 moomoo OpenD（常駐機側）
- [ ] moomoo OpenD インストール
- [ ] アカウント設定（**ペーパー口座で開始**）
- [ ] OpenD 起動（localhost:11111 で LISTEN 確認）
- [ ] 同期：`lsof -i :11111` で確認

### 1.7（任意）Ollama
- [ ] `brew install ollama`
- [ ] `ollama serve` 起動
- [ ] `ollama pull llama3.1:8b`
- [ ] Ollama 無しでも build_snapshot.py は動くが、MELCHIOR 独立性が下がる

---

## 2. 動作確認（フェーズ2）

### 2.1 snapshot 生成（最も基本）
```bash
uv run python scripts/build_snapshot.py --demo    # オフライン
uv run python scripts/build_snapshot.py           # ライブ価格
uv run python scripts/build_snapshot.py --moomoo  # moomoo 実保有（要 OpenD）
```
- [ ] `ui/public/data/snapshot.json` が生成される
- [ ] zeele / allocation / exposure 各セクションが入っている

### 2.2 朝バッチ（オプション・LLM 依存）
```bash
# 軽量（決定論のみ・Ollama 不要・推奨）
uv run python scripts/run_morning_batch.py --no-quality

# 本番（quality on・Ollama 必須・¥500/日予算消費）
uv run python scripts/run_morning_batch.py
```
- [ ] エラー無く完走する（Ollama 未導入なら警告は出る・無視可）

**実機検証済の出力例**（2026-05-26 開発機で `--no-quality` 実行）:
```
status: partial（topics_collector が timeout 以外は全 success）
✓ universe_refresh / screening / market_analyst / sell_recommender /
  portfolio_builder / materialize_decisions / magi_verify / link_topics / summary / notify
決裁待ち decision: 10 件（META=推し / 他は要検討・静観）
```
→ 本流（universe → MAGI → decisions）は完走する。topics_collector timeout は
ネット遅延 / NewsAPI 未設定時の既知挙動（graceful degradation 設計）。

### 2.3 評価ジョブ（P6-1 既実装）
```bash
uv run python scripts/run_evaluation.py
```
- [ ] decisions テーブルから評価期日到来分を採点

### 2.4 ペーパー運用（C3 既実装）
```bash
uv run python scripts/run_paper.py
```
- [ ] decisions → ペーパー約定 → portfolio 更新

### 2.5 UI 起動
```bash
cd ui
npm install
npm run dev     # 開発時のみ・http://localhost:3100
# or
npm run build && npm start  # 本番ビルド
```
- [ ] ダッシュボードが表示される（ZEELE 攻め / MAGI 守り / 資金配分 / 保有 / etc）

---

## 3. 自動化（launchd・常駐機のみ）

### 3.1 plist のパス書換
`ops/launchd/com.tradesupport.morning-batch.plist` と
`ops/launchd/com.tradesupport.evaluation.plist` の **絶対パス** を常駐機の
実パスに書き換える：
- [ ] `<string>/Users/a05/tradesupport/...</string>` → `<string>/Users/<常駐機user>/tradesupport/...</string>`

### 3.2 LaunchAgents 配置
```bash
cp ops/launchd/com.tradesupport.*.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradesupport.morning-batch.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradesupport.evaluation.plist
launchctl list | grep tradesupport
```
- [ ] 2 件登録される

### 3.3 動作監視
```bash
tail -f data/logs/morning-batch.out.log
tail -f data/logs/evaluation.out.log
```
- [ ] 数日確認して安定稼働を確認

### 3.4 緊急停止
- [ ] `~/.trading-agent/HALT` ファイル作成で全 LLM 停止
- [ ] `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.tradesupport.morning-batch.plist` で停止

---

## 4. 朝5分の運用フロー

1. **常駐機の UI を開く**（http://localhost:3100 等）
   - 朝バッチが 7:00 に走り、snapshot が更新済
2. **資金配分パネル**で総資産・posture・月次提案を確認
3. **action-zone** を見る
   - ZEELE 攻め：熟成中の候補（数週滞留）
   - MAGI 売り：利確 / 損切り候補
   - MAGI 買い：本日の推奨
4. **保有**を見る — ⚠ MAGI 利確等のバッジで売り注視を確認
5. **決裁**：気になるカードをクリック → 詳細パネル → 承認/否認/保留
6. **moomoo アプリで手動発注**：UI からコピーした指値・数量を入力
7. **約定は自動同期**（moomoo OpenD 経由）
8. **必要なら追加投入を記録**：CashFlowPanel の `+ 追加投入を記録` ボタン

---

## 5. 現状の制限（既知）

| 項目 | 制限 | 影響 |
|---|---|---|
| **B6 DAG挿入＆状態遷移** | 未実装（要ユーザー判断） | 決裁ボタンと decisions テーブルの連結はまだ。**手動運用 OK だが UI 上のステータス遷移は不完全** |
| **watchlist 昇格の永続化** | localStorage のみ | ブラウザ単位での保存。常駐機の UI を使う前提なら問題なし。次セッションで thesis_store / DB 化予定 |
| **screening → ZEELE 実候補** | プレースホルダ | snapshot.zeele.candidates は固定 4 件。実 screening 結果との配線は次セッション（X-2A ingest アダプタ） |
| **F5 ZEELE 探索画面** | skeleton のみ | サイドバーから到達できるが中身はプレースホルダ。次セッション以降 |
| **MAGI 判定** | NVDA のみ実データ | 他候補は dashboard.html のサンプル値。CANDIDATES マップ拡張で対応可 |
| **Ollama / MELCHIOR LLM** | 未必須 | 入れない場合 MELCHIOR は決定論のみ。3 審判独立性が弱まるが運用は可能 |

---

## 6. ペーパー運用→実弾化（D-23 増額ゲート）

増額ゲートは以下 4 条件すべて満たすまで実弾化しない：
1. **30 decision の評価完了**
2. **強気 / 弱気の両局面通過**
3. **期間中の最大 DD が −15% 以内**
4. **平均 R > 0**

達成後の手順：
- [ ] moomoo 同意②（実弾用の二段階目）
- [ ] ¥100k 入金（D-23 元本）
- [ ] `.env` `TRADING_MODE=live`（StandIn → moomoo 実保有へ）
- [ ] 1 銘柄¥20k 上限 / 現金 20%下限 / 損切 10-15% を厳守

---

## 7. 連絡用 / トラブル時

- `data/logs/*.log` を確認
- `~/.trading-agent/HALT` で即停止
- 月次予算超過（¥5,000）時は自動停止＋通知（cost_logs を確認）
- DB 破損時は `data/trading.sqlite.bak` から復旧
