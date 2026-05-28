# ユーザー（俺）のタスクチェックリスト

最終更新：2026-05-26
位置づけ：**ユーザー以外には実行できない作業のみ**を可視化。実装側のタスクは別管理。
原則：D-22「個人利用・精度最優先」／D-25「JP 90% / US 10%（ETFサテライト）」

---

## 🟢 いま進める（API キー取得）

すべて **無料 or 無料枠** で取得可能。完了したら ✅ に変える。

### A1. EDINET API キー（最優先・JP）

- [ ] **設定済か確認**：`.env` に `EDINET_API_KEY=...` が入っているか
- [ ] 入っていなければ取得：
  - URL：https://disclosure2.edinet-fsa.go.jp/weee0010.aspx
  - 手順：開発者向けページ → APIキー登録 → メール認証 → 発行
  - 所要：5-10分
  - 費用：**無料・無期限**
- [ ] `.env` に `EDINET_API_KEY=<取得値>` を追記
- [ ] 確認：`.venv/bin/python -c "from trading_agent.config import load_settings; print(bool(load_settings().edinet_api_key))"` で `True` が出ればOK

**重要性**：★★★ 日本株のファンダ・GC注記・不適切会計検知の柱。dexter-jp 借用のためにも必須。

---

### A2. J-Quants V2 リフレッシュトークン（推奨・JP）

- [ ] アカウント作成：https://jpx-jquants.com/
- [ ] 無料プラン選択：「Free」プラン（メール認証のみ）
- [ ] ダッシュボード → API認証 → リフレッシュトークン発行（コピー）
  - 所要：10分
  - 費用：**無料**（無料プランで日次株価・財務にアクセス可能）
- [ ] `.env` に `JQUANTS_REFRESH_TOKEN=<取得値>` を追記

**重要性**：★★ moomoo OpenAPI で日本株価は取れるので必須ではないが、**2ソース照合の原則**にとって理想的（D-02 / X-1）。

**有料プラン**（参考、当面採用しない）：
- Standard：$50/月相当・3か月遅延データなし
- Premium：$200/月相当・リアルタイムTOPIX等

---

### A3. NewsAPI キー（任意・海外ニュース時のみ）

D-25 で JP 90% に絞った結果、**優先度は下がった**（日本ニュースは TDnet RSS で代替可）。米国ETF（QQQ/VOO等）保有時のみ意味あり。

- [ ] 取得：https://newsapi.org/register
- [ ] Free プラン選択（100 req/日・developer 用途）
  - 所要：5分
  - 費用：**無料**（無料プランで開発用途は十分）
- [ ] `.env` に `NEWSAPI_KEY=<取得値>` を追記

**重要性**：★ 後回し可。ETF サテライト導入時に取得すれば良い。

---

### A4. Anthropic API キー（必須・既に取得済の想定）

- [ ] 確認のみ：`.env` に `ANTHROPIC_API_KEY=...` が入っているか
- [ ] 入ってなければ：https://console.anthropic.com/ → API Keys → Create Key
- [ ] **月¥5,000 予算は `BudgetGuard` が hard cap として強制**（[docs/OPERATING_COSTS.md](OPERATING_COSTS.md) §3）

**重要性**：★★★ tradesupport の必須キー（[trading_agent/config.py](../trading_agent/config.py) で required）

---

## 🟢 常駐機セットアップ（**このPC（`shotaro`）を常駐機として運用**）

**2026-05-26 追記**：構成変更。「実装PC ≠ 開発PC」前提を廃止し、**このリポジトリが置かれている `shotaro` PC を常駐機として運用**する方針に切り替えた。下記 S1〜S4 はこの PC 上で実施済（2026-05-26）。

### S1. 常駐機の確定

- [x] 常駐機の選定 — このPC（`shotaro`）を常駐機とする（スリープ無効化済：`pmset sleep=0 / displaysleep=0`）
- [x] 常駐機の macOS ユーザー名 / パス：`shotaro` / `/Users/shotaro/tradesupport`

### S2. tradesupport の配置

- [x] 配置済（`/Users/shotaro/tradesupport`・`feat/magi-rebuild` ブランチ）
- [x] `uv sync` 完了
- [x] `.env` 必須3キー + EDINET_API_KEY 設定済
- [x] `python scripts/init_db.py` 実行済（22 テーブル / `~/.trading-agent/db.sqlite`）
- [x] `python scripts/load_universe.py` 実行済（JP30 + US ETF2 = 32 銘柄）

### S3. moomoo OpenD 常駐

- [x] moomoo OpenD インストール済
- [x] OpenD 起動済（`lsof -i :11111` で `moomoo_Op` LISTEN 確認）
- [x] `.env` に `MOOMOO_OPEND_HOST=localhost` / `MOOMOO_OPEND_PORT=11111` 設定済
- ※ JP は moomoo SIMULATE 非対応のため StandIn フォールバック（docs/progress/0057 参照）

### S4. launchd の登録

- [x] plist のパス書換え済（`/Users/a05/...` → `/Users/shotaro/...`、`~/Library/LaunchAgents/` に配置）
- [x] `launchctl bootstrap` 実行済（`com.tradesupport.morning-batch` / `com.tradesupport.evaluation`）
- [x] 朝バッチ手動 dry-run 検証済（2026-05-26 23:xx：11/12 ノード success、決裁待ち 10 件 materialize）
- [ ] 数日 `data/logs/*.log` を確認

> **別 PC を開発機として併用する場合**は、その PC 側には launchd を登録しない（または `launchctl bootout` する）。本ツールの自動実行は **この常駐機の 1 箇所のみ** にする。

---

## 🟡 後回し（ペーパー運用が回ってから）

### B1. moomoo 取引同意②（実弾用）

ペーパー運用優先のため、**実弾化までは不要**。

- [ ] 増額ゲート達成（D-23）：①30 decision評価完了 ②強気/弱気の両局面通過 ③DD-15%内 ④平均R>0 — を満たしてから着手
- [ ] moomoo アプリで取引同意②（実弾用の二段階目）を実行
- [ ] OpenD 経由で実保有取得（現在 StandIn ¥100k）

### B2. ¥100k 入金（D-23 元本）

- [ ] 上記 B1 が完了するまで保留
- [ ] 銀行 → moomoo 口座への入金
- [ ] `~/.trading-agent/HALT` で全停止できることを再確認してから入金

### B3. Ollama インストール（MELCHIOR の独立性強化）

- [ ] 完全に後回しでOK（現状 MELCHIOR は決定論ロジックのみで稼働中・問題なし）
- [ ] 着手時は：`brew install ollama` → `ollama serve` → `ollama pull llama3.1:8b`
- [ ] その後 B2c（MELCHIOR LLM 解釈オプトイン）を実装側で対応

---

## ✅ 完了済み（再確認のみ）

| 項目 | 状態 | 確認方法 |
|---|---|---|
| moomoo OpenD 起動 | ✅ | `lsof -i :11111` で `moomoo_Op` が出る |
| moomoo OpenAPI 接続 | ✅ | JP/US/SIMULATE 確認済（memory: moomoo-jp-api-status） |
| `.env` 必須3キー | ✅ | ANTHROPIC / MOOMOO_TRADING_PWD / MOOMOO_ACCOUNT_ID |
| `~/.trading-agent/` 初期化 | ✅ | data/ + db.sqlite + logs/ |
| `uv sync` で依存同期 | ✅（推定） | 435 テスト緑 |
| HALT 制御 | ✅ | `~/.trading-agent/HALT` ファイルなし＝稼働可 |

---

## 進捗の見方

- このファイルを開いて `[ ]` → `[x]` で完了マーク
- API キー取得が3つ済めば D-25 反映実装の精度が上がる
- B 群（moomoo 同意② / ¥100k / Ollama）は **増額ゲート達成までは不要**
