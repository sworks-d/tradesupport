# 0014 A-4 MAGIをDAGに貫通 — 判断が実行経路に乗り永続化される

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「自動定義（推奨）」を選択（A-1）。その先の A-4（DAG接続）まで「自走できるところまで自走」「全コミット許可」。

## 判断（なぜA-4を自走対象に）
A-1（universe）が入り、A-3（decisionsスキーマ）も済んだことで、A-4 の前提が揃った。
A-4 はIMPROVEMENT_PLANで「★貫通の核」とされ、**[要ユーザー判断]タグは無く実装が確定**。
MCP取得を注入できる形にすればモックでE2Eテスト可能。これを通すと精度ドライバー③(P6)が読む実データが貯まる。

## 実施内容
- 新規 `trading_agent/magi/persist.py`：
  - `materialize_decisions(engine, candidates)`：候補→当日 `Decision(status="verifying")`。**同日同銘柄は再利用（冪等）**。
  - `pending_decision_ids(engine)`：当日の未検証 decision id（DAGハンドオフ＝DB経由）。
  - `make_live_judge_fn(call_tool, llm_tool=None)`：MCP（`ctx.call_tool`）で fundamentals/technicals/news を集め、
    `run_judges→classify_split→verify→command` を回す judge_fn を生成。**llm_tool 注入時のみ CASPER を Sonnet 格上げ**
    （バッチ既定はOFF＝決定論・コスト0・再現可能。UI は build_snapshot 側で格上げ）。
  - `magi_verify(engine, ids, judge_fn)`：各 decision で judge_fn→`persist_bundle`。**1件失敗は隔離**（verifying のまま）。
  - `persist_bundle`：judge_verdict×3／split_pattern／verification／commander_rec を **decision_id 付きで作り直し**（冪等）、
    decision を verifying→**awaiting** に進め、`verification.default_hold` を `gendo_stance`（推し/要検討/静観）に反映。
- `orchestrator/morning_batch.py`：portfolio_builder の後に **materialize_decisions→magi_verify** を直列追加（link_topics は magi_verify 依存に）。
  候補源 `_buy_candidate_tickers`＝active buy_signals 優先・無ければ screening上位（spec の「(or screening上位)」）。
- テスト：`test_magi_persist.py` 11（materialize冪等・gendo導出・4表永続・held計上・再検証冪等・失敗隔離）、
  `test_morning_batch.py` +1（貫通E2E：decision生成→awaiting→4表）。既存ノード数 10→**12**。

## 実測（受入）
- 全スイート green（276）。触ったファイル ruff clean。
- ライブ実証（決定論・実データ・`data/trading.sqlite`）：NVDA/AAPL → decision生成 → 3審判 → 碇まで保存。
  両者 default_hold=True（割れ）→ status=awaiting・gendo_stance=要検討・碇=「保留（または極小）」。

## docs更新
- `IMPROVEMENT_PLAN_FOR_CODE.md` A-4 を ✅（実績追記）。`spec/P3_magi.md` 冒頭に貫通の注記。

## コミット
- 本md＋persist.py＋morning_batch.py＋test_magi_persist.py＋test_morning_batch.py＋IMPROVEMENT_PLAN／spec／README を同一コミット。

## 状態/次（自走の到達点）
- **判断が孤立を脱し、実行経路で永続化されるようになった**＝MAGIシステムが実体として回る。
- これで精度ドライバー③(P6評価＝実データの hit/miss 評価)・④(B群反証層＝A-4後が前提)に着手可能。
- 当面の運用：朝バッチは決定論MAGIで保存（コスト0・再現可能）。UIスナップショットは CASPER=Sonnet 格上げ表示。
  バッチでもLLM格上げしたい場合は `make_live_judge_fn(..., llm_tool=...)` を有効化（予算ガード内）。
