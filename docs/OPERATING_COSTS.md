# 運用コスト整理

作成日：2026-05-26
親原則：D-22「個人利用・精度最優先」 ／ **月¥5,000 LLM 予算（hard cap）／日¥500（hard cap）**
正本：`trading_agent/llm/router.py`（単価）／`trading_agent/llm/budget.py`（守り）／`models/settings.py`（予算値）

---

## 0. 結論（4行）

1. **固定コスト＝¥0**（moomoo・EDINET・SEC EDGAR・TDnet・yfinance がすべて無料）
2. **変動コスト＝LLM のみ**。Sonnet ¥0.45/¥2.25 per 1k tok（in/out）、Opus ¥2.25/¥11.25、Ollama ¥0
3. **月¥5,000 予算は `BudgetGuard` が強制**。超過時 `budget_breach_action=halt` で自動停止
4. **X-2（discipline 層）追加でコスト構造はほぼ変わらない**（ローカル計算中心、LLM 呼び出し増は微小）

---

## 1. 固定コスト（月額）

| 項目 | 月額 | 備考 |
|---|---:|---|
| moomoo OpenAPI | **¥0** | 口座開設者は OpenD 経由で無料利用。米株+日株 |
| SEC EDGAR | **¥0** | User-Agent 必須・throttle 必須 |
| EDINET API | **¥0** | API キーは任意（環境変数 `EDINET_API_KEY`） |
| TDnet RSS | **¥0** | 公開 RSS |
| yfinance | **¥0** | 非公式 Python ライブラリ（無料） |
| J-Quants V2 | **¥0**（refresh tokenのみ） | 無料プランで日次株価可。有料プランは未採用 |
| NewsAPI | **¥0**（free tier） | 100 req/日。超過時は無料 RSS で代替 |
| Anthropic API | 0〜¥5,000（変動） | §2 |
| Ollama | **¥0** | ローカル（Mac mini / MacBook 上で動作）。電気代のみ |
| インフラ | **¥0** | macOS / FastAPI / SQLite / launchd 全て無料 |
| ドメイン・ホスティング | **¥0** | localhost 完結。外部公開なし（D-22） |
| **合計（固定）** | **¥0** | |

**注意**：moomoo は口座開設・実弾取引が前提。OpenAPI 利用に追加料金は無いが、取引手数料は別途（買付/売却毎）。

---

## 2. 変動コスト（LLM）

### 2.1 単価（`trading_agent/llm/router.py` の `PRICING_JPY`）

| モデル | 入力 ¥/1k tok | 出力 ¥/1k tok | 用途 |
|---|---:|---:|---|
| Claude **Sonnet** 4.6 (Hot Path) | 0.45 | 2.25 | BALTHASAR・CASPER（解釈）・市場分析・narrative |
| Claude **Opus** 4.7 (Critical) | 2.25 | 11.25 | 月次レビュー・重要判断のみ |
| **Ollama** (Cold Path・ローカル) | 0 | 0 | MELCHIOR（独立審判）・要約・整形 |

USD/JPY 換算は `USD_JPY_FALLBACK=150`（Phase 1 固定。実運用では `usd_jpy_rate_override` で上書き可）。

### 2.2 ワークフロー別の見積もり（暫定・実測前）

各ワークフローの想定 LLM 呼び出し回数と1回あたりトークン数。**X-2 翻案後の構成を反映**。

| ワークフロー | 頻度 | LLM 呼び出し | 1日コスト | 月コスト |
|---|---|---|---:|---:|
| **朝バッチ**（market-regime-daily 翻案） | 1回/日 | Sonnet ×3（breadth解釈・uptrend解釈・exposure_coach rationale整形） | ~¥30 | ~¥900 |
| 朝バッチ MAGI 4段 | 1回/日 | Sonnet ×2（BALTHASAR・CASPER）+ Ollama ×1（MELCHIOR・無料） | ~¥40 | ~¥1,200 |
| 朝バッチ 防御層 | 1回/日 | Sonnet ×1（矛盾検出） | ~¥15 | ~¥450 |
| 朝バッチ 碇司令 | 1回/日 | Sonnet ×1（推奨整形） | ~¥15 | ~¥450 |
| **週次PFレビュー**（core-portfolio-weekly 翻案） | 1回/週 | Sonnet ×2（allocation解釈・rebalance要約） | — | ~¥240 |
| **ペーパー振り返り**（trade-memory-loop 翻案） | クローズ毎（〜2回/週） | Sonnet ×2（postmortem notes・coach 質問生成） | — | ~¥200 |
| **月次レビュー**（monthly-performance-review 翻案） | 1回/月 | **Opus** ×1（重要判断・¥200）+ Sonnet ×3 | — | ~¥350 |
| ZEELE 探索（Z系列・凍結中） | 解凍時 | — | — | — |
| **月次合計（推定）** | | | | **~¥3,790** |

→ **月¥5,000 予算の 76%**。安全マージンあり。Opus は月次のみ。Ollama 活用が予算保持の鍵。

### 2.3 既存実装での平均消費（参考）

`cost_logs` テーブルの集計で実測可能（実装済み）。

```sql
SELECT date, SUM(cost_jpy) FROM cost_logs WHERE date >= '2026-05-01' GROUP BY date;
SELECT model, SUM(cost_jpy) FROM cost_logs WHERE date >= '2026-05-01' GROUP BY model;
```

実測値が積み上がれば §2.2 を更新する。

---

## 3. 月次予算と守り（`BudgetGuard`）

### 3.1 既存実装（`trading_agent/llm/budget.py`）

```
settings 表:
  daily_budget_jpy   = 500
  monthly_budget_jpy = 5000
  budget_breach_action = "halt"     ← 超過時は LLM 呼び出しを停止

BudgetGuard:
  - today_cost_jpy()  : 当日の cost_logs 合計
  - month_cost_jpy()  : 当月の cost_logs 合計
  - LLM 呼び出し前に上限を超えていないかチェック
  - 超過時：critical 呼び出しは警告のみ通す、それ以外は halt
```

### 3.2 HALT 制御

- `~/.trading-agent/HALT` ファイル作成で **全 LLM 停止**（手動緊急停止）
- 自動 HALT：月¥5,000 / 日¥500 超過時
- `.env` の `PAPER` ⇄ `LIVE` 切替で実弾運用の On/Off

### 3.3 ¥5,000 超過時の対応

1. 当月残りの非 critical 呼び出しを停止
2. `monthly-performance-review` で「何にコストを使ったか」を分析
3. 次月の予算配分を調整（D-23 8数値の「増額ゲート」と同じ思想）
4. 必要なら `monthly_budget_jpy` を一時引き上げ（要 DECISIONS 追記）

---

## 4. X-2（discipline 層）追加後の変化

claude-trading-skills 翻案の5モジュール追加後のコスト影響を予測：

| モジュール | 追加 LLM 呼び出し | 月コスト増 |
|---|---|---:|
| `exposure_coach` | Sonnet ×1（rationale 整形のみ）／既存 market_analyst から移行 | **±0**（既存の置換） |
| `holding_health` | **無し**（純粋なルール判定・コードのみ） | **¥0** |
| `thesis_store` | **無し**（YAML 永続化のみ） | **¥0** |
| `postmortem` | Sonnet ×1（notes 整形）/クローズ毎 | ~¥100 |
| `performance_coach` | Sonnet ×1（coach 質問生成）/クローズ毎＋月次 | ~¥150 |
| **合計増** | | **~¥250/月**（5%） |

**X-2 追加でも月¥5,000 予算内に余裕で収まる**。

---

## 5. 拡張余地と検討対象（採用判断時にコスト見直し）

| 候補 | 月額 | 検討タイミング | 判断 |
|---|---:|---|---|
| **FMP**（Financial Modeling Prep） | ¥0〜$50 (~¥7,500) | claude-trading-skills の swing 系を本格採用時 | 無料枠（250 req/日）で開始可。今は不要 |
| **FINVIZ Elite** | $39.50 (~¥6,000) | claude-trading-skills の theme-detector 採用時 | ZEELE 解凍後に検討。今は不要 |
| **Alpaca** | $0 / $99 | ペーパー口座のみなら無料 | tradesupport は moomoo 中心なので不要 |
| **Grok API (xAI)** | ~$10-30 (~¥1,500-4,500) | ZEELE 解凍時の Grok narrative 採用 | Z系列着手後に検討 |
| **Neo4j Aura** | $65+ (~¥10,000) | GraphRAG（stock_skills の graph-query 相当）採用時 | 自前ホスト無料版で代替可。コスト見合わず |
| **OpenBB Workspace** | エンタープライズ価格 | 採用見送り | AGPLv3＋エンタープライズ向け。個人ツールでは過大 |
| **TEI Embedding** | ローカルなら ¥0 | 投資メモのベクトル検索追加時 | Ollama と同様にローカル運用 |

→ **すべて採用見送り**または **無料枠で開始**。当面 ¥5,000 予算は LLM 専用で十分。

---

## 6. コスト管理機構の状態

| 機構 | 状態 | ファイル |
|---|---|---|
| LLM 単価表 | ✅ 実装済 | `trading_agent/llm/router.py` PRICING_JPY |
| 予算ガード | ✅ 実装済 | `trading_agent/llm/budget.py` BudgetGuard |
| `cost_logs` テーブル | ✅ 実装済 | `trading_agent/models/analytics.py` |
| 設定（月次/日次予算） | ✅ 実装済 | `trading_agent/models/settings.py` |
| HALT ファイル機構 | ✅ 実装済 | `~/.trading-agent/HALT` |
| `analysis_logs` の集計 | ✅ 実装済（total_cost_jpy 列） | 同上 |
| **月次コストダッシュボード UI** | ⏳ **未実装** | UI 側で実装すれば見える化できる |
| **予算超過時の通知** | ⏳ **未実装**（log のみ） | 通知（メール / Slack / Push）は後回し |

→ **既存機構は十分**。可視化（ダッシュボードのコスト欄）と通知（任意）は追加で良い。

---

## 7. 残った論点

| # | 論点 | 状態 |
|---|---|---|
| C-1 | Opus 4.7 を月次以外で呼ぶ場面はあるか？ | 現状なし。崩れた時のみ critical で許可 |
| C-2 | Ollama のローカル運用負荷（メモリ・速度） | Mac mini M1 / 16GB で十分実用。実測待ち |
| C-3 | USD/JPY 為替変動の Anthropic コストへの影響 | `USD_JPY_FALLBACK=150` 固定。為替反映は `usd_jpy_rate_override` で手動 |
| C-4 | X-2D 後の実測値で §2.2 を更新 | ペーパー運用後に再見積もり |
| C-5 | 月次コストダッシュボード UI 実装 | F5（MVP後の新規6画面）に含める候補 |

---

## 8. 1行サマリ

**固定コスト ¥0、変動コストは LLM のみ。月¥5,000 予算（hard cap・自動 halt 付き）の枠内で 5-7 のワークフローが全て回る。X-2 追加でも 5%増（~¥250/月）で吸収可能**。
