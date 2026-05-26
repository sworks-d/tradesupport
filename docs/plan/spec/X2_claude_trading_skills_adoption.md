# X-2 claude-trading-skills 翻案実装スペック

作成日：2026-05-26
位置づけ：X-1（[X1_reuse_inventory.md](X1_reuse_inventory.md) §6）の **claude-trading-skills 採用方針を、tradesupport 側の具体的な実装スペックに落とす**。
**親原則**：D-21（自前実装・コード非直輸入）／D-22（個人利用・精度最優先）／D-23（規律層＝外骨格）。
**位置関係**：claude-trading-skills は D-23「規律層（外骨格）」を**コンクリートに具体化**する。本書は D-23 の実装スペック詳細化。

---

## 0. 結論（4行）

1. **5ワークフロー骨格**を tradesupport の朝バッチ DAG / 週次 / 月次サイクルに翻案
2. **5キー翻案モジュール**を実装：`exposure_coach` / `holding_health`（Kanchi T1-T5）/ `thesis_store`（Trader Memory）/ `postmortem` / `performance_coach`
3. **SCORE:NONE 適合**：すべて決定論的計算＋定性ラベル。LLM は「読む・整形」のみ
4. **着手順**：`thesis_store`（D-23の外骨格と decisions テーブルに直接効く）→ `holding_health` → `exposure_coach` → `postmortem` → `performance_coach`

---

## 1. ワークフロー骨格の翻案（5本 → tradesupport DAG）

claude-trading-skills の YAML schema（`schema_version: 1` / `cadence` / `estimated_minutes` / `required_skills` / `optional_skills` / `artifacts` / `steps[decision_gate]` / `manual_review` / `journal_destination`）を**そのまま採用**する。

### 1.1 朝バッチ（market-regime-daily 翻案）

```yaml
id: morning-batch
cadence: daily
estimated_minutes: 5  # 朝5分ダッシュボード確認
required:
  - market_breadth   # 既存 market_analyst の breadth 部を分離
  - uptrend          # 既存 market_analyst の uptrend 部を分離
  - exposure_coach   # NEW — D-23 の外骨格判定の中核
optional:
  - market_top_detector  # ZEELE 解凍後
artifacts:
  - market_breadth_report   → MAGI BALTHASAR 入力
  - uptrend_report          → MAGI BALTHASAR 入力
  - exposure_decision       → 朝ダッシュボード「今日のポスチャー」表示
steps:
  - market_breadth_analyzer  (no_decision_gate)
  - uptrend_analyzer         (no_decision_gate)
  - exposure_coach           (decision_gate: 「新規エントリー可？」)
```

**翻案ポイント**：
- 既存 [agents/market_analyst.py](../../../trading_agent/agents/market_analyst.py) は「LLM が bull/base/bear scenario の数値を生成」しており **SCORE:NONE 違反**。これを **breadth/uptrend/exposure_coach の 3決定論的モジュールに分解**し、LLM は「読む・整形」のみに後退させる
- `exposure_decision` は MAGI 4段の**前段ゲート**として機能（CASH_PRIORITY 時は新規買い推奨を抑制）

### 1.2 週次PFレビュー（core-portfolio-weekly 翻案）

```yaml
id: weekly-portfolio-review
cadence: weekly
estimated_minutes: 60
required:
  - portfolio_snapshot      # 既存 portfolio/operator_view.py
  - allocation_report       # NEW — セクター/銘柄/通貨 HHI（stock_skills借用）
  - thesis_store_review     # NEW — 期日到来したテーゼの再評価
optional:
  - holding_health          # NEW — Kanchi T1-T5 翻案
  - rebalance_actions       # 既存 portfolio_builder の拡張
artifacts:
  - holdings_snapshot       → 月次に引き継ぎ
  - allocation_report
  - dividend_review_findings  → Kanchi T1-T5 結果
  - rebalance_actions
  - weekly_journal_entry    → thesis_store 追記
```

**翻案ポイント**：
- `allocation_report` は stock_skills の `portfolio/concentration.py`（HHI算出）借用
- `holding_health` は Kanchi T1-T5 翻案（§3.2）
- `weekly_journal_entry` は新規スキーマ（§3.3）

### 1.3 ペーパー振り返り（trade-memory-loop 翻案）

```yaml
id: paper-trade-review
cadence: ad-hoc  # クローズ毎に実行
estimated_minutes: 30
required:
  - thesis_close            # NEW — Trader Memory Core 翻案
  - postmortem              # NEW — Signal Postmortem 翻案
optional:
  - performance_coach       # NEW — Trade Performance Coach 翻案
  - backtest_revalidation
artifacts:
  - closed_thesis_record    → ペーパー P&L＋実現リターン
  - postmortem_findings     → 分類（TP/FP/MO/RM）
  - next_session_operating_rules  → 「次回これに気をつける」リスト
  - lessons_log_entry       → thesis_store 追記
```

**翻案ポイント**：
- 既存 [evaluation/paper_review.py](../../../trading_agent/evaluation/paper_review.py) を `postmortem` モジュールとして拡張。クラス分類（TRUE_POSITIVE / FALSE_POSITIVE / MISSED_OPPORTUNITY / REGIME_MISMATCH）を追加
- **`next_session_operating_rules` は人間が採否を選ぶ提案**（原則7・自己改変不採用と完全両立）

### 1.4 月次振り返り（monthly-performance-review 翻案）

```yaml
id: monthly-performance-review
cadence: monthly
estimated_minutes: 90
required:
  - monthly_aggregate       # トレード集計
  - aggregate_postmortem    # 月次パターン抽出
optional:
  - performance_coach       # 月次プロセスレビュー
  - backtest_revalidation
  - skill_review            # どのエージェント/モジュールが効いたか
artifacts:
  - monthly_decision_log
  - rule_changes_for_next_month  → G_risk_discipline / params.py 更新提案
  - skill_improvement_backlog    → 開発バックログ
```

**翻案ポイント**：
- `rule_changes_for_next_month` は [trading_agent/risk/params.py](../../../trading_agent/risk/params.py)（D-23の8数値）の更新提案を生成
- これが**人間 A/B ロジック育成（原則7）の入り口**

### 1.5 ZEELE 探索（swing-opportunity-daily 翻案・凍結中）

ZEELE 車線解凍時に着手。market-regime-daily の `exposure_decision` が **non-restrictive** の時のみ走る `prerequisite_workflows` 制約を採用。

---

## 2. 5キー翻案モジュール（実装スペック）

すべて [trading_agent/discipline/](../../../trading_agent/discipline/) 新ディレクトリ配下に配置。D-23「規律層＝外骨格」の実体。

### 2.1 `discipline/exposure_coach.py` — Market Posture 統合

**入力**：
- `breadth_report` (dict): 既存 market_analyst から分離
- `uptrend_report` (dict): 同上
- `top_risk_report` (dict, optional): ZEELE 解凍後
- `regime_report` (dict, optional)

**出力**（dict）：
```python
{
  "exposure_ceiling_pct": int,       # 0-100、コード算出（重み付き和）
  "bias": "GROWTH" | "VALUE",        # 定性ラベル
  "participation": "BROAD" | "NARROW",  # 定性ラベル
  "recommendation": "NEW_ENTRY_ALLOWED" | "REDUCE_ONLY" | "CASH_PRIORITY",
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "component_scores": {              # 各成分（コード由来）
    "breadth_score": int,
    "uptrend_score": int,
    "regime_score": int,
    "top_risk_score": int,
  },
  "rationale": str,                  # LLM が読み・整形した1行説明
  "source_refs": list[dict],         # 出典必須（原則準拠）
  "data_asof": str,                  # 時点必須
}
```

**SCORE:NONE 適合**：
- `exposure_ceiling_pct` と `component_scores` は**判断ではなく状態の計測値**。UI では「数値」ではなく「ラベル」（HIGH/MEDIUM/LOW・BROAD/NARROW）として表示
- `rationale` のみ LLM 生成だが、**LLM は構成スコアを読んで要約するだけ**で、新たな数値は生成しない

**判定式（暫定・D-23の8数値と整合）**：
```python
# 重み付き和の例（実装時調整）
ceiling = (
    0.30 * breadth_score
  + 0.25 * uptrend_score
  + 0.20 * (100 - top_risk_score)
  + 0.15 * regime_score
  + 0.10 * institutional_score
)
# D-23 の DD-15% 条件と組み合わせ
if portfolio_dd_pct <= -15:
    recommendation = "CASH_PRIORITY"  # 強制
elif ceiling >= 70:
    recommendation = "NEW_ENTRY_ALLOWED"
elif ceiling >= 40:
    recommendation = "REDUCE_ONLY"
else:
    recommendation = "CASH_PRIORITY"
```

### 2.2 `discipline/holding_health.py` — Kanchi T1-T5 翻案

**入力**：`holdings_snapshot` (各保有銘柄の正規化された dict)

**出力**（dict）：
```python
{
  "summary": {"OK": int, "WARN": int, "REVIEW": int},
  "findings": [
    {
      "ticker": str,
      "state": "OK" | "WARN" | "REVIEW",
      "triggers_fired": list[str],   # ["T1", "T4"] 等
      "evidence": list[dict],        # 各トリガーの根拠
      "next_review_date": str,
      "manual_check_required": bool,
    },
  ],
  "review_tickets": list[dict],      # state=REVIEW のみ
  "data_asof": str,
}
```

**T1-T5 トリガー仕様**（claude-trading-skills を翻案。日本株対応のため EDINET 統合）：

| ID | 名称 | 頻度 | 入力 | 判定 | 状態 |
|---|---|---|---|---|---|
| **T1** | 配当減配・無配 | 日次 | `dividend.latest_regular` vs `dividend.prior_regular` | `latest < prior * 0.5` or `latest == 0` | REVIEW |
| **T2** | カバレッジ悪化 | 四半期 | `FCF / dividends_paid` | `coverage < 1.0` for 2四半期連続 | REVIEW |
| **T3** | 信用格下げ・代理指標 | 週次 | 株価 vs セクター中央値、ボラティリティ | `relative_perf < -20%` & `vol > median * 1.5` | WARN |
| **T4** | 適時開示キーワード | 日次 | TDnet/EDINET 8-K相当フィード | キーワード（"不適切会計"/"訂正"/"訴訟"等）ヒット | REVIEW |
| **T5** | 構造的悪化 | 四半期 | revenue_CAGR / margin_trend / guidance | 3四半期連続悪化 | WARN |

**重要な原則**：
- **絶対に auto-sell しない**。REVIEW は人間判断の入口
- 複数トリガー時は **最高 severity を採用**（REVIEW > WARN > OK）
- すべて **コード判定**。LLM は使わない（D-23 の「コード反証」と整合）

**統合先**：
- 朝バッチ後・週次 PF レビュー前に走る
- 出力は MAGI 防御層の入力にも使う（保有銘柄に REVIEW がある時は碇推奨を保留）

### 2.3 `discipline/thesis_store.py` — Trader Memory Core 翻案

**スキーマ**（YAML、`state/theses/<thesis_id>.yaml`）：

```yaml
thesis_id: <ulid>
ticker: 7203.T
status: IDEA | ENTRY_READY | ACTIVE | CLOSED
thesis_type: dividend_income | growth_momentum | mean_reversion | turnaround
thesis_statement: "EV普及で部品需要増。配当継続性◎"
origin:
  source: screening_agent | manual_input | sell_recommender
  source_refs:
    - {url: "...", as_of: "2026-05-26"}
  raw_provenance: {...}
plan:
  entry_price: 2850.0
  stop_price: 2510.0    # D-23 損切り10-15%
  target_price: null    # SCORE:NONE — bull/base/bear生成は禁止
  risk_r: 2000          # D-23 1R = ¥2,000
  size_shares: 100
exits:
  - {price: 2510.0, type: stop, planned: true}
actual:
  entry_price: 2845.0
  entry_date: "2026-05-26"
  shares: 100
  exit_price: null
  exit_date: null
  realized_return_pct: null
lifecycle_log:
  - {at: "2026-05-26T05:00", status: IDEA, by: "screening_agent"}
review:
  due_date: "2026-08-26"   # 四半期後
  review_count: 0
postmortem:
  root_cause: null
  lessons: []
  classified: null
magi_links:
  decision_ids: []         # MAGI 判定との紐付け
```

**移行**：
- 既存 [models/decisions.py](../../../trading_agent/models/decisions.py) との関係：decisions は MAGI 判定の記録（買い/売り推奨の出力）、theses は**保有思想のライフサイクル**（IDEA→ACTIVE→CLOSED）。FK は `theses.magi_links.decision_ids` で複数の MAGI判定を束ねる
- thesis_id を ULID で発番し、ペーパー約定時に紐付け

**LLM 関与**：
- `thesis_statement` の文章生成は LLM（"読む" 側、人間が修正可能）
- 数値（entry_price / stop_price / risk_r / size_shares）は**コード由来のみ**
- 強制的なバリデーション：`stop_price` が entry の 10-15% 以内（D-23 の8数値）

### 2.4 `discipline/postmortem.py` — Signal Postmortem 翻案

**入力**：closed thesis records + 実現リターン（コード計算）

**出力**：
```python
{
  "thesis_id": str,
  "classification": "TRUE_POSITIVE" | "FALSE_POSITIVE" | "MISSED_OPPORTUNITY" | "REGIME_MISMATCH",
  "predicted_direction": "LONG",          # ペーパーは LONG のみ
  "realized_return_5d_pct": float,        # コード計算
  "realized_return_20d_pct": float,       # コード計算
  "realized_return_to_close_pct": float,
  "alpha_vs_benchmark_pct": float,        # S&P/TOPIX 比（D-22 精度ドライバー）
  "root_cause": "thesis_quality" | "execution" | "risk_sizing" | "market_environment" | "rule_violation" | "randomness",
  "rule_adherence": {                     # D-23 8数値遵守
    "stop_honored": bool,
    "risk_within_limit": bool,
    "position_size_correct": bool,
  },
  "notes": list[str],                     # LLM 生成可（読み・整形）
  "weight_feedback": dict,                # 将来の重み調整のための提案（人間採否）
  "data_asof": str,
}
```

**統合先**：
- 既存 [evaluation/paper_review.py](../../../trading_agent/evaluation/paper_review.py) を拡張するか、新規ファイルとして配置
- 月次集計時に `aggregate_postmortem` を作成

### 2.5 `discipline/performance_coach.py` — Trade Performance Coach 翻案

**入力**：closed_thesis_record + postmortem_findings + risk_plan（D-23 params.py）

**出力**：
```python
{
  "review_type": "single_trade" | "monthly_aggregate",
  "process_adherence": {
    "thesis_recorded_before_entry": bool,
    "setup_confirmed": bool,
    "stop_moved": bool,
    "entry_before_confirmation": bool,
    "traded_against_regime": bool,
  },
  "risk_discipline": {
    "max_risk_per_trade_violated": bool,
    "max_portfolio_heat_violated": bool,
    "consecutive_losses": int,
  },
  "execution_quality": {
    "entry_slippage_pct": float,
    "exit_slippage_pct": float,
  },
  "behavior_pattern_tags": list[str],     # ["FOMO", "revenge_trade", "stop_moving"...]
  "next_session_operating_rules": [       # 人間が採否を選ぶ
    {"rule": str, "evidence": list[str], "severity": "HIGH"|"MEDIUM"|"LOW"},
  ],
  "coach_questions": list[str],           # 内省を促す質問
  "data_asof": str,
}
```

**重要**：
- **絶対に売買推奨をしない**。プロセスレビューのみ
- `behavior_pattern_tags` は決定論的判定（"stop_moved == True && stop_move_planned == False" → "stop_moving"）
- `next_session_operating_rules` は **提案**。人間が DECISIONS.md / params.py に反映するかを決める

---

## 3. データ層の変更

### 3.1 新規テーブル / YAML

| 場所 | 内容 | スキーマ |
|---|---|---|
| `state/theses/<id>.yaml` | 投資テーゼ ライフサイクル | §2.3 |
| `models/holding_health.py` | T1-T5 trigger 結果保存 | findings: list[dict] |
| `models/postmortem.py` | クローズ後分類結果 | §2.4 |
| `models/operating_rules.py` | 月次レビューで提案された運用ルール（人間採否前） | rule_proposals: list[dict] |

### 3.2 既存テーブル拡張

- `decisions`：`thesis_id`（FK to theses）追加
- `decisions`：`rule_adherence_*`（D-23 8数値の遵守フラグ）追加
- `analysis_logs`：`exposure_decision`（朝バッチ毎の posture）追加

### 3.3 Alembic マイグレーション

各データ層変更を Alembic で管理（B0 規約と整合）。

---

## 4. 実装フェーズ（X-2A〜X-2D）

D-23（規律層・外骨格）の実装と並走。B0〜B6（MAGI本体）の進捗を遅らせない。

### X-2A：thesis_store（最優先・即着手可能）
- **理由**：D-23 の外骨格に直接効く（reciprocal=stop/risk_r/sizeの履歴管理）。既存 decisions テーブル拡張のみで動く
- **成果物**：`discipline/thesis_store.py` + `state/theses/` + 1テスト
- **見積もり**：2-3 セッション

### X-2B：holding_health（T1-T5）
- **理由**：保有銘柄の規律監督。tradesupport が既に EDINET を使っているので T4 が直接実装可能
- **依存**：thesis_store（T1-T5 結果を保有銘柄に紐付け）
- **成果物**：`discipline/holding_health.py` + 5トリガー + テスト
- **見積もり**：3-4 セッション

### X-2C：exposure_coach
- **理由**：朝バッチの中核。既存 `market_analyst` の SCORE:NONE 違反箇所を解体
- **依存**：breadth/uptrend モジュールの分離（既存 market_analyst から）
- **成果物**：`discipline/exposure_coach.py` + 朝バッチ DAG 更新
- **見積もり**：4-5 セッション

### X-2D：postmortem + performance_coach
- **理由**：ペーパー運用が走り始めた後の評価層
- **依存**：ペーパー運用が回っていること（P4 完了後）
- **成果物**：`discipline/postmortem.py` + `discipline/performance_coach.py` + 月次レビュー DAG
- **見積もり**：4-5 セッション

---

## 5. SCORE:NONE 原則との適合表

| モジュール | コード由来 | LLM 関与 | UI 表示形式 |
|---|---|---|---|
| `exposure_coach` | ceiling_pct / scores 全成分 | rationale 1行のみ | ラベル（HIGH/MED/LOW + posture） |
| `holding_health` | T1-T5 全判定 | なし | 状態（OK/WARN/REVIEW）+ 根拠リスト |
| `thesis_store` | 数値全て | thesis_statement のみ | テーゼカード（ライフサイクル状態） |
| `postmortem` | 分類・リターン計算 | notes 整形 | クラス分類（TP/FP/MO/RM）+ 根拠 |
| `performance_coach` | 全パターン判定 | coach_questions のみ | プロセスチェックリスト＋ルール提案 |

**重要**：5モジュール全て、**MAGI 4段の入力にはなるが、決裁の数値そのものは出さない**。SCORE:NONE 維持。

---

## 6. 既存 tradesupport モジュールとの整合

| 既存 | 関係 | 処置 |
|---|---|---|
| [agents/market_analyst.py](../../../trading_agent/agents/market_analyst.py) | bull/base/bear scenarioを LLM生成（SCORE:NONE 違反） | exposure_coach + breadth + uptrend に分解。LLM は読み・整形のみ |
| [evaluation/paper_review.py](../../../trading_agent/evaluation/paper_review.py) | ペーパー P&L 集計 | postmortem に拡張（分類追加） |
| [risk/params.py](../../../trading_agent/risk/params.py) | D-23 の8数値 | 維持。performance_coach が遵守フラグを生成 |
| [risk/portfolio_guard.py](../../../trading_agent/risk/portfolio_guard.py) | リスクガード | exposure_coach の入力に統合 |
| [portfolio/sizing.py](../../../trading_agent/portfolio/sizing.py) | R-mult ベース sizing | 維持。thesis_store の数値検証に再利用 |
| [agents/sell_recommender.py](../../../trading_agent/agents/sell_recommender.py) | 売り推奨 | holding_health の REVIEW 状態を入力に追加 |
| [magi/](../../../trading_agent/magi/) | MAGI 4段 | exposure_decision を前段ゲートに、holding_health の REVIEW を防御層に統合 |

---

## 7. 受入条件

- [ ] §1 の 5ワークフロー YAML が `trading_agent/orchestrator/` に存在
- [ ] §2 の 5モジュールが `trading_agent/discipline/` に存在し、テスト green
- [ ] §3 のデータ層変更が Alembic マイグレーション済み
- [ ] §5 の SCORE:NONE 適合がすべてのモジュールでテスト担保
- [ ] §6 の既存モジュールとの整合が動作確認済み

---

## 8. 着想元の記録（D-21 適合）

本書の全モジュール・全ワークフローは [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills)（MIT）の**考え方を蒸留して自前実装**。コードの直輸入はなし。各モジュールの先頭コメントに "Inspired by claude-trading-skills/{skill_name}" を記載する。

Kanchi T1-T5 の概念は同リポジトリの `kanchi-dividend-review-monitor` から。日本株対応のための EDINET 統合は本家になく、tradesupport 側の追加実装。
