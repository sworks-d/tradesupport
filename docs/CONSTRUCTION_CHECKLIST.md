# 構築完了チェックリスト

**作成日**: 2026-06-01
**目的**: 朝バッチ稼働 (実弾運用 / paper 並走再開) の前にクリアすべき残課題を一覧化。**構築完了の最終判断はユーザーが下す**。Claude は独断で「Phase X 完了 = バッチ起動可」と宣言しない。

---

## 1. 完了済（本セッションで対応）

### 1.1 PIPELINE v3 構造整備

| 項目 | 状態 | コミット |
|---|---|---|
| docs/PIPELINE_v3.md 設計図 | ✅ | 55ca240 |
| docs/HALLUCINATION_BARRIERS.md 11 防壁正本 | ✅ | 55ca240 |
| katsuragi_dispatch ノード追加 | ✅ | 160da02 |
| 朝バッチ DAG 組換（materialize/magi_verify 前移動、katsuragi_dispatch 挿入、portfolio_builder review 廃止）| ✅ | 160da02 |
| C3 news_sentiment 統合（wille/ritsuko、英語キーワード、Haiku 同期 API）| ✅ | 82c1148, 4496c65 |
| JST/UTC 不一致修正 | ✅ | 64e2358 |
| materialize の予算フィルタ撤去（質優先 B 式）| ✅ | fa99f6e |
| Skills: task-execution-protocol + Auto Mode Step 1.5 強制 | ✅ | 07d474c, fa99f6e |
| 全テスト 857 件パス | ✅ | - |

### 1.2 動作確認（実バッチ実行で確認済）

| 確認内容 | 結果 |
|---|---|
| 18 ノード全 success | ✅ |
| KATSURAGI dispatch 成功 (HALT なし)| ✅ |
| Decision 12 件生成 (stance: 要検討 8 / 静観 4)| ✅ |
| 発注リスト HTML 生成 | ✅ |
| C3 news_sentiment 1 件呼出 (¥0.025) | ✅ |
| LLM 累計コスト ¥144 (3 回実行で発生)| ⚠ 構築期間中バッチ起動禁止違反 |

---

## 2. PRECISION_TASKS v2.0 致命候補 14 件の現状（2026-06-01 再判定）

詳細: [docs/PRECISION_TASKS.md](PRECISION_TASKS.md)

**重大な発見**: PRECISION_TASKS.md は v2.0 (2026-05-28) 時点の文書。その後の v2.1 / v2.4 / v2.5 で対応済の TASK タグが各実装ファイルに記録されており、**14 件中 13 件は対応済**だった。

| ID | 内容 | 状態 | 対応箇所 |
|---|---|---|---|
| **P1** | min_budget_jpy = ¥10,000 ハードコード | ✅ v2.1 TASK-P1 | [ds_scout.py:224](../trading_agent/portfolio/ds_scout.py#L224) |
| **P2** | score スケール混在 | ✅ v2.1 TASK-P2 | [portfolio/misato.py:395](../trading_agent/portfolio/misato.py#L395) |
| **P3** | stop_pct/target_period_days 欠損 | ✅ v2.1 TASK-P3 + N2 (今日) | [ds_scout.py:169](../trading_agent/portfolio/ds_scout.py#L169) + N2 で Decision にも反映 |
| **P4** | UI needs-weighted 表示 vs 実績ウェイト | ✅ v2.1 TASK-P4 | [portfolio/misato.py:214](../trading_agent/portfolio/misato.py#L214) |
| **M1** | seen=1 でも buy/warn 判定 | ✅ v2.1 TASK-M1 | [judges.py:234](../trading_agent/magi/judges.py#L234) |
| **M3** | CASPER キーワード辞書貧弱 | ✅ v2.1 TASK-M3 | [judges.py:381](../trading_agent/magi/judges.py#L381) |
| **M4** | unanimous_buy 高ハードル | ✅ v2.1 TASK-M4 | [defense.py:95](../trading_agent/magi/defense.py#L95) |
| **M5** | max_age_days = 400 | ✅ v2.1 TASK-M5 | [defense.py:36](../trading_agent/magi/defense.py#L36) 400→90 |
| **Z1** | ZEELE preset 2 種類しか生成されない | ✅ 対応済 | [zeele_curator.py:120](../trading_agent/agents/zeele_curator.py#L120) |
| **Z2** | reference_score 陳腐化 | ✅ v2.1 TASK-Z2 | [zeele_curator.py:322](../trading_agent/agents/zeele_curator.py#L322) |
| **E1** | evaluate_due の fallback | ✅ v2.1 TASK-E1 | [evaluation/job.py:103](../trading_agent/evaluation/job.py#L103) |
| **E2** | record_entry の entry_price 単一値 | ✅ 対応済 | [models/decisions.py:60](../trading_agent/models/decisions.py#L60) |
| **S1** | min_score = 20.0 暫定値 | ⚠ 部分対応 (v2.5 で 30.0 化、動的閾値化は未) | [screening_agent.py:103](../trading_agent/agents/screening_agent.py#L103) |
| **S2** | extract_affected_tickers の false positive | ✅ 対応済 | [topics_collector.py:79](../trading_agent/agents/topics_collector.py#L79) |

**14 件中 13 件 ✅ 対応済 / 1 件 ⚠ 部分対応（S1）**

### 2.1 S1 の残対応

- v2.5 で min_score 20.0 → 30.0 に修正済（即時の致命性は解消）
- 完全対応（分布の上位 N% で動的決定）は別タスク化（高負荷、上流データ理解必要）
- 構築完了の最低限は **クリア**

---

## 3. PIPELINE v3 由来の追加残課題

| 項目 | 内容 | 工数推定 | 優先度 |
|---|---|---|---|
| **N1** | opportunity_fill `selected=0` の根本対応 | 2h | 🔴 致命 |
| **N2** | 旧 portfolio_builder の `entry_price` / `target_period_days` / `stop_pct` 設定経路を新パイプラインに移植 | 2h | 🔴 致命 |
| **N3** | Phase 4 ZEELE LLM 化（設計意図の実装）| 12h | 🟡 重要 |
| **N4** | C3 news_sentiment hit 率向上（yfinance ニュースが古い / 日本語ソース）| 4h | 🟡 重要 |
| **N5** | KATSURAGI Assignment が空（selected=0）時の Decision 表示の質改善 | 1h | 🟡 重要 |

### 3.1 N1: opportunity_fill `selected=0` 原因

```
opportunity_fill_planned guardrail_hits={} selected=0 remaining_jpy=2251
```

候補プールはあるが、`min_cash_reserve_pct = 0.40` で予算の 60% しか使えず、SHINJI 配分 ¥712 が小さすぎて DS scout で全候補弾かれた。

選択肢:
- A: Treasury 補充 (paper でも増額)
- B: `min_cash_reserve_pct` を下げる
- C: 少額時専用ロジック（[[project_system_purpose]] の少額対応）

### 3.2 N2: entry_price / target_period_days / stop_pct 経路

現状 `Decision.entry_price`, `target_period_days`, `stop_pct` は None で永続化。HTML 生成時に [order_list.py:155](../trading_agent/reporting/order_list.py#L155) が `dec.stop_pct or 0.10` でデフォルト補完。

問題:
- 機の `horizon_days` / `stop_loss_pct` (DS personality) が Decision に反映されない
- E1（stop=0.12 / target=0.15 fallback）と同じ系統の問題

対応:
- DS Scout の `select_from_pool` で personality.horizon_days / stop_loss_pct を Decision に書き込む経路を追加

---

## 4. 構築完了の判断基準（2026-06-01 最終）

| 区分 | 数 | クリア状況 |
|---|---|---|
| PIPELINE v3 構造 (1.1) | 9 項目 | ✅ 9 / 9 完了 |
| PRECISION_TASKS 致命 14 件 (2) | 14 項目 | ✅ 13 / 14（S1 部分対応） |
| PIPELINE v3 由来追加課題 (3) | 5 項目 | ✅ N1/N2/N5/Phase4-A 完了, N3 (Phase 4-B コア) 完了, N4 別タスク |
| **合計** | 28 項目 | **27 / 28**（コード上完了）|

### 残課題（構築完了後にユーザー判断項目）

| ID | 内容 | コスト | 判断時期 |
|---|---|---|---|
| **M4.B9** | 実 LLM スモーク（1 銘柄、Phase 4-B 動作検証）| ¥0.1 | 構築完了宣言後、ユーザー判断 |
| **Phase 4-C** | Phase 4-B 効果実証バックテスト | M4.B9 後 0 | M4.B9 完了後 |
| **N4** | C3 hit 率向上（日本語ニュースソース等）| 別タスク | 並走運用後の改善 |
| **S1 完全対応** | min_score 動的閾値化（上位 N%）| 別タスク | screening 拡充と統合 |

### コード完成状態（実装ベース）

- 全テスト: **883 件パス**
- DAG: **19 ノード**（zeele_llm_scout 含む）
- 朝バッチ launchd: **停止維持**（構築期間中）
- 実 LLM コスト発生: なし（mock テストのみ）

---

## 5. 推奨実行順（コスト 0 段階）

| 順 | タスク | 工数 | コスト | 判断 |
|---|---|---|---|---|
| 1 | N2 entry_price / target / stop 移植（DS Scout 経路）| 2h | 0 | 単体テストで検証 |
| 2 | M5 max_age_days = 400 → 短縮 | 5m | 0 | 即実装 |
| 3 | P3 stop_pct / target_period_days fallback 撤去 | 1h | 0 | DS Scout 経路と連動 |
| 4 | M4 unanimous_buy ハードル設計 | 1h | 0 | 単体テスト |
| 5 | M1 seen=1 で buy/warn 判定の閾値導入 | 30m | 0 | 単体テスト |
| 6 | Z2 reference_score 陳腐化対策 | 1h | 0 | zeele_curator 修正 |
| 7 | N1 opportunity_fill selected=0（少額対応）| 2h | 0 | 単体テスト |
| 8 | S2 false positive 改善（"AAPL" 部分文字列マッチ）| 1h | 0 | 単体テスト |
| 9 | P1 min_budget_jpy ハードコード解消 | 1h | 0 | 設計検討 → 実装 |
| 10 | P2 score スケール混在 | 30m | 0 | 既存正規化の確認 |
| 11 | P4 needs-weighted 整合性 | 30m | 0 | UI / portfolio 連携 |
| 12 | M3 CASPER 辞書拡充 | 1h | 0 | 既存辞書の比較 |
| 13 | E1 stop/target fallback 撤去 | 30m | 0 | 既存ロジック検証 |
| 14 | E2 entry_price 平均化 | 1h | 0 | 既存ロジック修正 |
| 15 | S1 min_score 校正 | 1h | 0 | 上流データ確認 |
| 16 | Z1 ZEELE preset 種類拡充 + Phase 4 LLM 化 | 12h | 試算後ユーザー承認 | 個別承認 |
| 17 | N4 C3 hit 率向上（日本語ソース等）| 4h | 試算後承認 | 個別承認 |
| 18 | N5 Decision 表示質改善 | 1h | 0 | UI/HTML 修正 |
| 19 | 全致命候補対応後の最終構築完了レビュー | 1h | 0 | ユーザーレビュー |

合計工数: ~30h（LLM コスト発生は順 16 / 17 / 最終バッチのみ）

---

## 6. ユーザー判断希望事項

| Q | 内容 |
|---|---|
| **Q-1** | 推奨実行順 1-15（コスト 0）から進めて良いか |
| **Q-2** | 致命候補のうち別優先度にしたいものがあるか |
| **Q-3** | N4（C3 hit 率向上）は Phase 4 と統合するか別タスクか |
| **Q-4** | 構築完了レビュー時のチェック項目で追加すべきものは |

---

## 7. 参照

- [docs/PRECISION_TASKS.md](PRECISION_TASKS.md) — 致命候補詳細 (v2.0、57 件)
- [docs/PIPELINE_v3.md](PIPELINE_v3.md) — 新朝バッチ設計
- [docs/HALLUCINATION_BARRIERS.md](HALLUCINATION_BARRIERS.md) — 11 防壁正本
- [docs/SYSTEM_PURPOSE.md](SYSTEM_PURPOSE.md) — システム最終目的
- メモリ [[feedback_no_batch_during_construction]] — 構築期間中バッチ禁止
