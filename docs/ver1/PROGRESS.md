# Trading Agent 実装進捗

最終更新：2026-05-22

状態凡例：`[ ]` 未着手 / `[wip]` 着手中 / `[done]` 完了 / `[blocked]` 待ち / `[skip]` スキップ

> ✅ **Phase 1.0 は実機検証まで完了**。`uv sync` / `pytest`（30件）/ `init_db.py` /
> ロガー / ruff・black・mypy --strict をすべて green で確認済み。
> （途中、許可プロンプトの扱いで一時停滞したが解消。`.claude/settings.json` に
> 開発コマンドの allow を登録済み。）

---

## Phase 1.0：環境構築 — ✅ 完了（検証済み）

- [done] 1.0.1 プロジェクト初期化
  - pyproject.toml（Phase 1.0 必要依存に絞る）/ .python-version(3.12) / .gitignore / README.md
  - パッケージ骨格：`trading_agent/{models,mcp_tools,agents,orchestrator,brokers,llm,api,utils}/`
  - `tests/{unit,integration,fixtures}/` / `scripts/` / `ui/`
- [done] 1.0.2 設定モジュール config.py
  - `trading_agent/config.py`（pydantic-settings v2、必須欠落で ConfigError、~/.trading-agent 自動作成、validate_default=True）
  - `.env.example`（全項目・プレースホルダで検証通過）/ `tests/unit/test_config.py`
- [done] 1.0.3 ロギング基盤
  - `trading_agent/utils/logger.py`（structlog、stdout+日次ファイル、JSON、APIキーマスキング、JST）
  - `trading_agent/utils/time_utils.py`（JST/UTC 変換）/ `tests/unit/test_logger.py`
- [done] 1.0.4 データベース初期化
  - `trading_agent/models/` 全17テーブル（SYSTEM_DESIGN 16 + ORCHESTRATION の batch_states）
  - `trading_agent/db.py`（engine/create_all/seed_default_settings/init_database）/ `scripts/init_db.py`
  - Alembic 導入（alembic.ini / alembic/env.py / script.py.mako / versions/）※初期スキーマは create_all
  - `tests/unit/test_models.py`

### Phase 1.0 完了基準（実機検証済み）
- [done] `uv sync` 成功（33パッケージ / editable `trading-agent==0.1.0`）
- [done] `uv run python -c "import trading_agent"` → `import OK 0.1.0`（Python 3.12.13）
- [done] `cp .env.example .env` でバリデーション通過
- [done] `uv run pytest` 緑（30件 passed：test_config / test_logger / test_models）
- [done] `uv run python scripts/init_db.py` → 17 テーブル + settings 11 件、2回目は追加0（冪等）
- [done] ロガーが `~/.trading-agent/logs/2026-05-22.log` を作成（JSON / JST / 機微値マスキング）
- [done] 品質：ruff All checks passed / black 整形済 / mypy --strict no issues

### 朝の確認待ち（ユーザー判断が要る項目）
1. **必須設定フィールドの扱い**：`anthropic_api_key` / `moomoo_trading_pwd` / `moomoo_account_id`
   を「必須（欠落で起動失敗）」にしている（SYSTEM_DESIGN §6.3 準拠。`.env.example` の
   プレースホルダで検証は通る）。完全 degraded 起動を優先するなら「任意＋警告」に変える余地あり。
   **推奨：当面は必須のまま**、moomoo 接続実装（Phase 1.2）で再検討。

---

## Phase 1.1：MCP ツール基盤 — ✅ 完了（8ツール / pytest 125件 green）
- [done] 1.1.1 基底クラス — `mcp_tools/base.py`：MCPTool（リトライ/フォールバック/エラー分類）
  + MCPHost（登録・取得・health_check_all）+ 型付き例外 + MCPErrorType。tenacity 採用。
  単体テスト 13 件（計 43 件 green）。
- [done] 1.1.2 market_data — `mcp_tools/market_data.py`：メモリ5分TTL + DBキャッシュ
  フォールバック。yfinance を live 主ソース（moomoo 接続は Task 1.2.4）。fetcher 注入で
  ネットワーク非依存テスト 9 件（計 52 件 green）。base のリトライ設定をインスタンス上書き可に変更。
- [done] 1.1.3 fundamentals — `mcp_tools/fundamentals.py`：PER/PBR/EPS/増収率/営業利益率を
  共通フォーマットで取得 + 一次情報 URL（EDGAR/EDINET）。**方針 A 採用**（数値=yfinance 主、
  EDGAR/EDINET は source_url。設計の「EDGAR/EDINET 主」を実装上反転 ＝ 要確認・差替可）。
  fetcher 注入 + メモリ6h TTL。テスト 13 件（計 65 件 green）。
- [done] 1.1.4 news — `mcp_tools/news.py`：NewsAPI + RSS(feedparser) 収集、デデュープ
  （URL/見出しハッシュ/類似度）、言語判別、期間/銘柄フィルタ、Graceful Degradation。
  fetcher 注入。feedparser/httpx 追加。テスト 12 件（計 77 件 green）。
- [done] 1.1.5 disclosure — `mcp_tools/disclosure.py`：TDnet RSS(feedparser) + EDINET API(httpx)。
  期間/銘柄フィルタ・URL デデュープ・縮退。fetcher 注入。米 8-K は将来。テスト 7 件（計 84 件 green）。
- [done] 1.1.6 technicals — `mcp_tools/technicals.py`：RSI/MACD/SMA/ボリンジャーを
  **numpy/pandas で自前計算**（TA-Lib 不使用＝C依存回避。設計は「TA-Lib または pandas_ta」許容）。
  シグナル名（overbought_rsi/golden_cross 等）、history_provider 注入。テスト 16 件（計 100 件 green）。
- [done] 1.1.7 screening — `mcp_tools/screening.py`：V字回復/テーマスコア（各4軸、AGENT_SPECS
  §1.5/1.6 準拠の純粋関数）+ composite=max + ランク/フィルタ + screening_results 保存。
  データ収集・universe 選定は agent(1.4.2) 側。テスト 15 件（計 112 件 green、固定データで期待値検証）。
- [done] 1.1.8 llm_call — `llm/{types,router,budget,anthropic_client,ollama_client}.py` +
  `mcp_tools/llm_call.py`：routing_hint/purpose でモデル選択（Sonnet/Opus/Ollama）、予算ガード
  （cost_logs 集計 + settings 上限、critical はバイパス）、全呼び出しを cost_logs 記録。
  client 注入でモック。anthropic 追加。テスト 13 件（計 125 件 green）。実 Ollama は未導入（テストはモック）。

## Phase 1.2：moomoo 連携
- [ ] 1.2.1 BrokerConnection / 1.2.2 broker_read / 1.2.3 同期ジョブ / 1.2.4 market_data 切替

## Phase 1.3：エージェント基盤 — ✅ 完了（pytest 139件 green）
- [done] 1.3.1 基底 — `agents/base.py`：Agent[TIn] / AgentInput / AgentOutput / execute_agent ラッパー
- [done] 1.3.2 プロンプト管理 — `agents/prompts.py`（XML + Jinja2 + 版数）+ `agents/prompts/`。jinja2 追加
- [done] 1.3.3 ツールアクセス層 — `agents/context.py`（AgentContext）。**LangChain 自律ループは不採用**
  ＝決定論・コスト管理・テスト容易性を優先（§A-1 からの逸脱・差替可）。agents は MCP/llm_call を明示呼び出し
- [done] 1.3.4 HALT・予算チェック — execute_agent 内（HALT ファイル / BudgetGuard）
- [done] 1.3.5 実行ログ — execute_agent が analysis_logs に start/end 記録（invocation_id トレース）
- [done] 1.3.6 シリアライズ — `agents/serialization.py`（buy/sell は旧 active を false 化、scenarios は upsert）

## Phase 1.4：エージェント実装 — ✅ 完了（6体 / pytest 173件 green）
- [done] 1.4.1 topics-collector — `agents/topics_collector.py`：news+disclosure 収集 → URL デデュープ
  → ルール重要度（保有/決算速報/FOMC 等）→ 影響先抽出（$X/(NNNN)/universe）→ topics 保存。
  LLM は低重要度の補強のみ（キー無しでも動作）。テスト 9 件（計 145 件 green）。
- [done] 1.4.2 screening-agent — `agents/screening_agent.py`：universe から market_data/technicals/
  fundamentals を集約 → screening ツールで採点・ランク → screening_results 保存。テスト 3 件（計 148）。
  ※部分データ採点（90日高安/四半期EPS/銘柄別ニュースの配線は後日）。
- [done] 1.4.3 market-analyst — `agents/market_analyst.py`：5軸スコア（fundamental/technical は純粋関数、
  strategy_fit=screening、news=50中立、ai_confidence/scenarios は LLM）+ 推奨数量・指値（§2.7）→ buy_signals。
  LLM 無しでも縮退動作。テスト 7 件（計 155）。
- [done] 1.4.4 sell-recommender — `agents/sell_recommender.py`：保有評価 → 利確/損切りスコア
  （純粋関数）+ シナリオ進捗（LLM、縮退 0.5）+ 規律メッセージ（損切り≥70）+ 売却数量（§3.7）→
  sell_signals/scenarios 保存。買って3日内スキップ。テスト 12 件（計 167）。
- [done] 1.4.5 portfolio-builder — `agents/portfolio_builder.py`：initial（コア/サテ配分推奨）+
  review（コア比率・セクター集中の警告）。ルールベース決定論。テスト 3 件。
- [done] 1.4.6 manual-input-analyst — `agents/manual_input_analyst.py`：投入テキストを LLM 解釈
  → 影響先/方向/規模/推奨アクション + manual_inputs 保存 + トピックス化候補。短文拒否・縮退対応。テスト 3 件。

## Phase 1.5：オーケストレーター
- [ ] 1.5.1 DAG / 1.5.2 朝バッチ定義 / 1.5.3 エラー処理 / 1.5.4 APScheduler / 1.5.5 健康チェック

## UI プレビュー（前倒し・確認用） — ✅ 稼働
- [done] FastAPI 配信 + `/api/dashboard`（DB集約）+ `/api/health`：`trading_agent/main.py` /
  `trading_agent/api/routes_dashboard.py`
- [done] 静的ダッシュボード `ui/static/`（サマリ+30日推移 / 売り左・買い右 / 保有 / トピックス）
- [done] `scripts/seed_sample_data.py`（¥10万サンプル投入で全パネル描画）
- 起動：`uv run uvicorn trading_agent.main:app --port 8000` → http://localhost:8000
- 位置づけ：エージェント実装前の**確認用プレビュー**。API層は本番再利用、UIは Phase 1.6 で
  Next.js 本実装へ発展。実データはエージェント（1.4/1.5）稼働後に置き換わる。

## Phase 1.6：UI（Next.js）
- [ ] 1.6.1〜1.6.10（上記プレビューを正式 Next.js 化）

## Phase 1.7：常駐化と運用
- [ ] 1.7.1 launchd / 1.7.2 backup / 1.7.3 通知 / 1.7.4 E2E / 1.7.5 ドキュメント

## Phase 1.8：4週間運用 + 調整
- [ ] 運用フェーズ

---

## 実装メモ（判断ログ）

- **依存スコープ**：pyproject の `dependencies` は Phase 1.0 で実際に使う5つ
  （pydantic, pydantic-settings, sqlmodel, alembic, structlog）に限定。完全リスト（ta-lib 等）を
  入れると `uv sync` が C ライブラリ不足で失敗し完了基準を壊すため（原則4）。将来依存は
  pyproject 内コメントに明記。
- **uv.lock を追跡**：再現性・ロールバック性が 4週間運用とコスト管理に有利なため `.gitignore` から除外。
- **Python 3.12 ピン**：システムは 3.14.3。ta-lib/moomoo-api のホイール未整備リスクを避け
  `.python-version=3.12`（実体は 3.12.13）。OPERATIONS §B-1 とも整合。
- **SQLModel × future annotations**：linter が table モデルから `from __future__ import annotations`
  を除去。フィールド名 `date` と型の衝突を避けるため型は `import datetime as dt`（`dt.date`）で
  統一しており、eager 評価（3.12）でも動作影響なし。
- **config validate_default=True**：pydantic v2 は既定でデフォルト値に validator を効かせないため設定。
  これがないと env 未指定時の `~` 展開・log_level 正規化が走らない。
- **batch_states**：SYSTEM_DESIGN の16表に未定義だが Task 1.0.4 が要求 → ORCHESTRATION §9.1 の
  定義を採用（計17テーブル）。
- **FK→portfolio.ticker（非ユニーク）**：SYSTEM_DESIGN の定義どおり実装。Phase 1 は SQLite の
  FK 非強制のため create/insert は通る。
- **partial index**：SYSTEM_DESIGN §2.4 の `WHERE` 付き部分インデックスは通常インデックスで代替
  （Phase 1 の割り切り、最適化は後日）。
