# B0 — 差分計画書（MAGIゲート挿入の設計）

作成日：2026-05-22
正本：`README.md`／タスク：`TASKS.md`／意思決定：`DECISIONS.md`
目的：既存（Phase 1.4）を壊さずに、MAGI 4段を「decisionsをUIに出す前」に挿入する設計を確定する。**実装はしない。設計のみ。**

---

## 1. 重大な前提（監査で判明）

1. **`decisions` レコードは現状どこでも生成されていない**（孤立テーブル）。
   - `morning_batch` の `link_topics` は `{"skipped": True}`。`save_decisions()` も存在せず、`Decision(...)` のインスタンス化は皆無。
   - API（`routes_dashboard.py`）は portfolio/buy_signals/sell_signals/topics のみ参照。decisions は読まない。
   - → **MAGIゲートの前に「signals → decisions 生成」ステップを新設する必要がある。**
2. **LLM数値生成の原則違反が 9直接＋1間接**（§4に file:line で列挙）。再チューニングの対象。
3. `decisions` テーブルに **状態(status) フィールドが無い**（`user_action` はあるが verifying/verified/awaiting の遷移を持たない）。→ 追加が必要。

---

## 2. MAGIゲートの挿入点（DAG差分）

### 現行 DAG（morning_batch.py）
```
pre_check → topics_collector / universe_refresh
         → screening → market_analyst → sell_recommender
         → portfolio_builder → link_topics → summary → notify
```

### 改修後 DAG（★＝新規ノード）
```
pre_check → topics_collector / universe_refresh
         → screening → market_analyst → sell_recommender
         → portfolio_builder
         → ★materialize_decisions   （active な buy/sell signals → decisions 生成。status=verifying）
         → ★magi_verify             （decisionsごとに MAGI 4段を実行）
              ├ 3審判（独立）→ judge_verdict
              ├ 防御層（機械照合）→ verification
              ├ 統合機構（割れ方）→ split_pattern
              └ 碇司令（推奨＋反対）→ commander_rec
            （完了で status=verified→awaiting。赤/割れは決裁既定=保留）
         → link_topics → summary → notify
```

- **挿入点**：`portfolio_builder` の後、`link_topics` の前。`materialize_decisions` → `magi_verify` の2ノードを直列追加。
- **依存**：`magi_verify` は `materialize_decisions` に依存。`materialize_decisions` は `sell_recommender`（売り信号）と `market_analyst`（買い信号）両方の完了後。
- **UI公開条件**：`magi_verify` 完了（status≥verified）まで decisions を「決裁待ち」にしない（生信号をUIに出さない）。
- **状態遷移**：`verifying → verified → awaiting → approved/denied/held → order_listed → ordered → holding`。

> 補足：`materialize_decisions` を独立ノードにする理由＝「decisions生成」と「MAGI検証」を分離し、検証失敗時も decisions 自体は残す（再検証キューに乗せる）。

---

## 3. データモデル差分（既存表 無改変・追加のみ）

### 3.1 既存 `decisions` への追加カラム（既存カラムは保持。`score` も残す＝D-06）
```
status          str   # verifying/verified/awaiting/approved/denied/held/order_listed/ordered/holding
gendo_stance    str   # 推し/利確/撤退/要検討/静観（UIの .g-word）
verified_at     datetime | None
```
- 既存 `score`(int) は残すが UI に総合点として出さない（D-06）。
- 既存 `user_action`(adopted/skipped/...) は決裁の人間アクションとして併存（status と役割分担：status=システム状態、user_action=決裁種別）。

### 3.2 追加4テーブル（すべて `decision_id` で `decisions.id` にFK）
```
judge_verdict
  id, decision_id(FK), judge(MELCHIOR/BALTHASAR/CASPER),
  verdict(buy/sell/hold/warn/na), confidence(高/中/低 ラベル＝D-11),
  reason(str), source_refs(JSON 出典), data_asof(datetime 時点), created_at

split_pattern
  id, decision_id(FK), agree_count(int), total(int),
  label(str 例「3/3 利確・一致」), interpretation(str), created_at
  ※ decisions.split_pattern_id で逆参照も可（FK設計は実装時）

commander_rec
  id, decision_id(FK), recommendation(str), counter_argument(str),
  magi_compliant(bool=gendo_compliant), src_note(str), created_at

verification
  id, decision_id(FK), figures_checked(bool),
  unverified_claims(JSON), credibility_flag(ok/warn),
  gendo_compliant(bool), data_asof(datetime), created_at
```
- FK方向：4表 → decisions（多対一。1 decision に judge_verdict×3、split/commander/verification×1）。
- `decisions` 側の逆参照（gendo_stance / split_pattern_id）は表示の都合で持たせる（実装時に Relationship 設計）。

### 3.3 予測値（既存 `scenarios`）の扱い
- 既存 `scenarios`（bull/base/bear, target_price 等）は **LLM生成＝未照合**。`is_model_generated=true` 相当のフラグを立て、UIで「未照合」バッジ、碇の根拠から除外（§4で再チューニング）。

---

## 4. LLM数値生成の違反一覧（再チューニング対象＝B1/B2で潰す）

原則：数値はコードが取得/計算/照合した実データのみ。LLMは「読む・解釈」役。

| # | 箇所 | LLM生成の数値 | 保存先 | 是正方針 |
|---|---|---|---|---|
| 1 | market_analyst.py:259 | `ai_confidence`(0-100) | BuySignal.ai_confidence | 確信度は定性ラベル化（D-11）。数値はコード指標から導出 or ラベルのみ |
| 2 | market_analyst.py:283 | scenario `target_price` | BuySignal.target_price | 予測値＝未照合扱い。碇の根拠から除外。UIに未照合バッジ |
| 3 | market_analyst.py:284 | scenario `return_pct`→expected_return | BuySignal.expected_return | 同上（予測値・未照合） |
| 4 | market_analyst.py:285 | scenario `prob`→win_rate | BuySignal.win_rate | 同上。確率はモデル生成と明示 |
| 5 | market_analyst.py:281-305,414-423 | scenarios一式の正規化 | BuySignal.scenarios | LLM生成と明示。実データと同じ見せ方をしない |
| 6 | sell_recommender.py:306 | `overall_health`(0-1) | Scenario.scenario_health | 仮説進捗は定性化。スコア計算からLLM数値を排除 |
| 7 | sell_recommender.py:309 | `ai_confidence`(0-1) | スコア計算入力 | 定性ラベル化。score算出をコード指標のみへ |
| 8 | sell_recommender.py:212,228 | `score`(LLM依存) | SellSignal.score | ai_confidence依存を除去。コード指標のみで算出 |
| 9 | manual_input_analyst.py:122 | `confidence`(0-1) | parsed（未保存） | 定性化。保存時も数値で持たない |
| 10 | llm_call.py:73-119（間接） | 応答の数値を素通し | — | JSON応答の数値はコード照合を通すまで信用しない（防御層B3） |

- **MAGIの3審判では**：MELCHIOR/BALTHASARの数値はコード（fundamentals/technicals）から取得・計算し、LLMは解釈のみ（D-10）。CASPERは定性判断＋出典。
- `BuySignal.score`（5軸統合）はコード算出だが UI非表示（D-06）。`SellSignal.score` は ai_confidence依存を切る。

---

## 5. 既存182テストを壊さない方針

| 区分 | 方針 |
|---|---|
| 追加（4表・materialize_decisions・magi_verify・防御層） | **新規テストを足す**。既存に影響しない |
| 再チューニング（agentsのLLM数値排除） | 該当テスト（test_market_analyst / test_sell_recommender / test_manual_input_analyst）は **意図的に更新**。「LLMが数値を返す」前提のアサートを「数値はコード由来／確信度は定性ラベル」前提へ書き換え |
| 無関係（mcp_tools基盤・dag・models・api・llm router） | green 維持。`source_refs`/`data_asof` 追加は**任意フィールド**で後方互換にし、既存テストを壊さない |
| マイグレーション | 4表追加＋decisionsカラム追加。Alembic（現状create_all運用）。既存データ無し前提でcreate_all拡張、本番化時にAlembic移行 |

Exit（B0完了条件）：本書で **挿入点・4表/FK・状態遷移・違反一覧・テスト維持方針** が確定 → 達成。

---

## 6. B1 への申し送り

1. MCP出力に `source_refs`＋`data_asof` を**任意フィールド**で追加（後方互換）。
2. 重要数値の2ソース照合（±0.5%＝D-12）。
3. 第1信用性フィルタ（上場廃止/特設注意を先＝D-14）。
4. §4の違反10件を「コード由来＋未照合明示」に再チューニング。
5. `materialize_decisions` の生成ルール（active signals → decisions、status=verifying）を実装。
