# Trading Agent — 実装フェーズ計画

最終更新：2026-05-22
ステータス：**STEP D：実装タスクリスト（Claude Code 用）**

このファイルは Claude Code が **タスク単位で実装を進める** ための詳細リスト。

各タスクには以下を含む：
- **目的**：何のためのタスクか
- **関連設計書**：どこを参照するか
- **完了基準**：何ができれば完了か
- **依存タスク**：先に必要なタスク
- **想定セッション数**：1セッションで完結 or 複数必要か

---

# 全体構造

```
Phase 1.0：環境構築           [4 タスク、1 セッション]
Phase 1.1：MCP ツール基盤      [8 タスク、3-4 セッション]
Phase 1.2：moomoo 連携        [4 タスク、2 セッション]
Phase 1.3：エージェント基盤    [6 タスク、2 セッション]
Phase 1.4：エージェント実装    [6 タスク、6 セッション]
Phase 1.5：オーケストレーター  [5 タスク、3 セッション]
Phase 1.6：UI（Next.js）      [10 タスク、6-8 セッション]
Phase 1.7：常駐化と運用       [5 タスク、2 セッション]
Phase 1.8：4週間運用 + 調整  [運用フェーズ]
```

**想定合計セッション数：25-30 セッション**（1 セッション 1-2 時間想定）

---

# Phase 1.0：環境構築

**ゴール**：実装に着手できる土台を作る。プロジェクトが起動できる状態。

## Task 1.0.1：プロジェクト初期化

### 目的
プロジェクトの骨格を作る。

### 関連設計書
- CLAUDE_CODE_INSTRUCTIONS.md セクション 4（プロジェクト構造）
- SYSTEM_DESIGN.md セクション 1.2

### 実装内容
- `pyproject.toml` 作成（uv で管理）
- 依存パッケージリスト（SYSTEM_DESIGN セクション 5 のリスト参照、ただし Phase 1 で必要なものに絞る）
- `.gitignore`（`.env`, `__pycache__`, `*.sqlite`, `logs/`, `.venv` 等）
- `README.md`（簡潔、詳細は docs/ に誘導）
- ディレクトリ構造（CLAUDE_CODE_INSTRUCTIONS セクション 4 通り）

### 完了基準
- `uv sync` が成功する
- `uv run python -c "import trading_agent"` が成功する
- ディレクトリツリーが SYSTEM_DESIGN.md の構造と一致

### 依存
なし（最初のタスク）

### 想定セッション数
0.5 セッション

---

## Task 1.0.2：設定モジュール（config.py）

### 目的
環境変数と設定の読み込み機構を作る。

### 関連設計書
- SYSTEM_DESIGN.md セクション 6（環境変数 / 設定ファイル仕様）

### 実装内容
- `trading_agent/config.py`：Pydantic Settings ベース
- `.env.example`：必須・オプション全項目をコメント付きで記載
- 起動時のバリデーション（必須項目欠落時はエラー）
- `~/.trading-agent/` ディレクトリの自動作成

### 完了基準
- `.env` をコピーして起動できる
- `from trading_agent.config import get_settings` が機能
- 必須項目欠落時に明確なエラーメッセージ
- 単体テスト（テストでは pytest-mock で env を上書き）

### 依存
- Task 1.0.1

### 想定セッション数
0.5 セッション

---

## Task 1.0.3：ロギング基盤（structlog）

### 目的
全モジュールが使う構造化ロガーをセットアップ。

### 関連設計書
- CLAUDE_CODE_INSTRUCTIONS.md セクション 6.6
- OPERATIONS.md セクション 5

### 実装内容
- `trading_agent/utils/logger.py`：structlog セットアップ
- 日次ファイルローテーション
- ログレベルは .env から（デフォルト INFO）
- API キー等をマスキングするプロセッサ

### 完了基準
- `from trading_agent.utils.logger import log` で使える
- 起動時にログファイルが `~/.trading-agent/logs/YYYY-MM-DD.log` に作成
- JSON 形式で出力（jq でパース可能）
- API キーがログに出ない

### 依存
- Task 1.0.2

### 想定セッション数
0.3 セッション

---

## Task 1.0.4：データベース初期化

### 目的
SQLite DB を作成、全テーブル定義、初期データ投入。

### 関連設計書
- SYSTEM_DESIGN.md セクション 2（データモデル）

### 実装内容
- `trading_agent/models/` に各テーブルの SQLModel 定義
  - portfolio, portfolio_snapshots, universe, market_data_cache, earnings_calendar
  - screening_results, buy_signals, sell_signals, scenarios, decisions
  - topics, manual_inputs, analysis_logs, cost_logs, health_checks, settings, batch_states
- Alembic 初期化（`alembic init alembic`、`alembic.ini` 設定）
- 初回マイグレーション（全テーブル作成）
- `scripts/init_db.py`：DB 初期化スクリプト
  - テーブル作成
  - settings テーブルにデフォルト値投入
  - universe テーブルに初期銘柄リストの **空テーブル作成**（実データ投入は別タスク）

### 完了基準
- `uv run python scripts/init_db.py` で DB が作られる
- 全テーブルが存在することを確認
- settings テーブルにデフォルト値（SYSTEM_DESIGN セクション 2.3 settings 参照）
- 単体テスト（インメモリ SQLite で各モデルの基本 CRUD）

### 依存
- Task 1.0.2, Task 1.0.3

### 想定セッション数
1 セッション

---

## Phase 1.0 完了基準

- [x] プロジェクトディレクトリが整っている
- [x] `uv sync` で全依存パッケージがインストールできる
- [x] `.env` をコピーしてバリデーションが通る
- [x] `init_db.py` で空の DB が作成できる
- [x] ロガーが動作する

ここまでで **約 2 セッション、4-6 時間** の作業を想定。

---

# Phase 1.1：MCP ツール基盤

**ゴール**：エージェントが使うデータアクセスツールを揃える。

## Task 1.1.1：MCP ツールの基底クラス

### 目的
全 MCP ツールが継承する基底クラスを作る。

### 関連設計書
- SYSTEM_DESIGN.md セクション 3.1（共通インターフェース）

### 実装内容
- `trading_agent/mcp_tools/base.py`：MCPTool 基底クラス
- `MCPToolInput`, `MCPToolOutput` の Pydantic モデル
- 共通エラーハンドリング（リトライ、フォールバック）
- ツール登録機構（MCPHost クラス）

### 完了基準
- `MCPTool` を継承して新ツールが作れる
- 単体テスト（モックツールで基底クラスの動作確認）

### 依存
- Phase 1.0 完了

### 想定セッション数
0.5 セッション

---

## Task 1.1.2：market_data ツール

### 目的
株価・出来高をリアルタイムで取得する MCP ツール。

### 関連設計書
- SYSTEM_DESIGN.md セクション 3.2（market_data）

### 実装内容
- `trading_agent/mcp_tools/market_data.py`
- メモリキャッシュ（5分 TTL）
- DB キャッシュ（market_data_cache テーブル）
- **moomoo を Phase 1.2 で接続するまでは yfinance フォールバックを主とする**
- 単体テスト（モック化された yfinance で）

### 完了基準
- ティッカーを渡すと現在価格を返す
- キャッシュが効く（同じ ticker は 5分 API 呼ばない）
- API 失敗時にキャッシュから返す
- 単体テストが通る

### 依存
- Task 1.1.1

### 想定セッション数
1 セッション

---

## Task 1.1.3：fundamentals ツール

### 目的
財務データを取得する MCP ツール。

### 関連設計書
- SYSTEM_DESIGN.md セクション 3.2（fundamentals）

### 実装内容
- `trading_agent/mcp_tools/fundamentals.py`
- 米国株：SEC EDGAR の API（10-K, 10-Q）
- 日本株：EDINET の API
- 補助：yfinance / J-Quants
- データ正規化（PER、PBR、EPS、売上等の共通フォーマット）

### 完了基準
- 米国株・日本株両方でファンダデータが取れる
- 一次情報の URL を返す（透明性）
- 単体テスト

### 依存
- Task 1.1.1

### 想定セッション数
1 セッション

---

## Task 1.1.4：news ツール

### 目的
ニュースを取得する MCP ツール。

### 関連設計書
- SYSTEM_DESIGN.md セクション 3.2（news）

### 実装内容
- `trading_agent/mcp_tools/news.py`
- NewsAPI 連携
- RSS フィード（feedparser）：日経、Bloomberg、Reuters、TechCrunch、9to5Mac、政府公式
- デデュープ（URL 完全一致 + headline ハッシュ + 類似度）
- 言語自動判別

### 完了基準
- 過去 24 時間のニュースが取得できる
- 重複が除去されている
- 単体テスト（モック化された RSS で）

### 依存
- Task 1.1.1

### 想定セッション数
1 セッション

---

## Task 1.1.5：disclosure ツール

### 目的
適時開示・IR を取得する MCP ツール。

### 関連設計書
- SYSTEM_DESIGN.md セクション 3.2（disclosure）

### 実装内容
- `trading_agent/mcp_tools/disclosure.py`
- TDnet RSS パース
- EDINET API（日本企業の有報・四報）
- 米国は将来 SEC 8-K に対応（Phase 1 では fundamentals と併用で間に合う）

### 完了基準
- 日本企業の適時開示が取得できる
- 単体テスト

### 依存
- Task 1.1.1

### 想定セッション数
0.5 セッション

---

## Task 1.1.6：technicals ツール

### 目的
テクニカル指標を計算する MCP ツール。

### 関連設計書
- SYSTEM_DESIGN.md セクション 3.2（technicals）

### 実装内容
- `trading_agent/mcp_tools/technicals.py`
- TA-Lib（または pandas_ta）で指標計算
- RSI, MACD, SMA, ボリンジャーバンド
- シグナル名の定義（"overbought_rsi", "golden_cross" 等）
- 過去データは market_data から取得

### 完了基準
- 90日分の過去データから指標が計算できる
- 単体テスト（固定の price series で期待値検証）

### 依存
- Task 1.1.1, Task 1.1.2

### 想定セッション数
0.5 セッション

---

## Task 1.1.7：screening ツール

### 目的
V字回復・テーマスコアを計算する MCP ツール。

### 関連設計書
- SYSTEM_DESIGN.md セクション 3.2（screening）
- AGENT_SPECS.md セクション 1.5, 1.6（スコア算出式）

### 実装内容
- `trading_agent/mcp_tools/screening.py`
- V字回復スコア計算（4軸）
- テーマスコア計算（4軸）
- 各銘柄のスコアを screening_results テーブルに保存

### 完了基準
- universe を渡すと各銘柄のスコアが返る
- スコア計算式が AGENT_SPECS と一致
- 単体テスト（固定データで期待スコア検証）

### 依存
- Task 1.1.2, Task 1.1.3, Task 1.1.4, Task 1.1.6

### 想定セッション数
1 セッション

---

## Task 1.1.8：llm_call ツール

### 目的
LLM 呼び分け・コスト管理を統合した MCP ツール。

### 関連設計書
- SYSTEM_DESIGN.md セクション 3.2（llm_call）, セクション 5（LLM 呼び分け）

### 実装内容
- `trading_agent/llm/router.py`：ルーティングロジック
- `trading_agent/llm/anthropic_client.py`：Anthropic API ラッパー
- `trading_agent/llm/ollama_client.py`：Ollama ラッパー
- `trading_agent/llm/budget.py`：予算管理（BudgetGuard）
- `trading_agent/mcp_tools/llm_call.py`：MCP ツール化

### 完了基準
- routing_hint と purpose で適切な LLM が選ばれる
- 全呼び出しが cost_logs に記録される
- 予算超過時に拒否される
- 単体テスト（モック化された LLM で）

### 依存
- Task 1.1.1

### 想定セッション数
1 セッション

---

## Phase 1.1 完了基準

- [x] 8 つの MCP ツールが基底クラスを継承して実装されている
- [x] 各ツールの単体テストが通る
- [x] エラーハンドリング（リトライ、フォールバック）が機能
- [x] コスト記録が機能

ここまでで Phase 1.0 + 1.1 = **約 7-8 セッション**。

---

# Phase 1.2：moomoo 連携

**ゴール**：moomoo OpenD と読み取り連携。

## Task 1.2.1：BrokerConnection クラス

### 目的
moomoo OpenD への接続を管理する Singleton。

### 関連設計書
- SYSTEM_DESIGN.md セクション 4（moomoo OpenD 接続管理）

### 実装内容
- `trading_agent/brokers/moomoo_client.py`：moomoo SDK ラッパー
- `trading_agent/brokers/connection_manager.py`：BrokerConnection クラス
- 接続・切断・リトライ・heartbeat

### 完了基準
- moomoo OpenD が起動していれば接続できる
- 切断時に自動再接続を試みる
- 単体テスト（モック化された moomoo SDK で）

### 依存
- Phase 1.1 完了
- moomoo OpenD インストール済み（ユーザー作業）

### 想定セッション数
1 セッション

---

## Task 1.2.2：broker_read MCP ツール

### 目的
moomoo から保有銘柄・残高・約定を取得。

### 関連設計書
- SYSTEM_DESIGN.md セクション 3.2（broker_read）

### 実装内容
- `trading_agent/mcp_tools/broker_read.py`
- positions, balance, transactions の取得
- BrokerConnection 経由

### 完了基準
- moomoo の保有銘柄が取得できる
- 接続切れの場合にエラーメッセージで通知
- 単体テスト

### 依存
- Task 1.2.1

### 想定セッション数
0.5 セッション

---

## Task 1.2.3：moomoo 同期ジョブ

### 目的
5分ごとに portfolio テーブルを moomoo と同期。

### 関連設計書
- ORCHESTRATION.md セクション 6（moomoo 同期フロー）

### 実装内容
- `trading_agent/orchestrator/moomoo_sync.py`
- APScheduler で 5 分ごとに実行
- 新規ポジション / 売却 / 数量変動の差分検知
- portfolio テーブル更新
- 売却完了時の decision 評価更新

### 完了基準
- 手動で moomoo に保有を追加すると、5 分以内に portfolio テーブルに反映
- 売却時に decisions が更新される
- 単体テスト

### 依存
- Task 1.2.2

### 想定セッション数
0.5 セッション

---

## Task 1.2.4：market_data の moomoo 切り替え

### 目的
Task 1.1.2 で yfinance ベースだった market_data を moomoo 優先に。

### 関連設計書
- SYSTEM_DESIGN.md セクション 3.2（market_data）

### 実装内容
- market_data.py 修正：moomoo を第一優先に
- フォールバック順序：moomoo → メモリキャッシュ → DB キャッシュ → yfinance

### 完了基準
- moomoo 接続時は moomoo の価格が使われる
- 切断時は yfinance フォールバック
- 単体テスト更新

### 依存
- Task 1.2.1, Task 1.2.2

### 想定セッション数
0.3 セッション

---

## Phase 1.2 完了基準

- [x] moomoo OpenD と接続できる
- [x] 保有銘柄が portfolio テーブルに同期される
- [x] 価格データが moomoo から取れる
- [x] 切断時のフォールバックが機能

---

# Phase 1.3：エージェント基盤

**ゴール**：エージェント実装のための基盤を作る。

## Task 1.3.1：エージェント基底クラス

### 目的
全エージェントが継承する基底クラス。

### 関連設計書
- AGENT_SPECS.md セクション 0（共通設計）

### 実装内容
- `trading_agent/agents/base.py`
- AgentInput, AgentOutput, Agent クラス
- execute_agent ラッパー（共通の前処理・後処理）

### 完了基準
- 基底クラスを継承して新エージェントが作れる
- ロギング・コスト記録・エラーハンドリングが共通化されている

### 依存
- Phase 1.2 完了

### 想定セッション数
0.5 セッション

---

## Task 1.3.2：プロンプト管理

### 目的
プロンプトを外部ファイルで管理。

### 関連設計書
- AGENT_SPECS.md セクション 9（プロンプト管理）

### 実装内容
- `trading_agent/agents/prompts/` ディレクトリ
- XML 形式でプロンプトを保存
- Python から読み込み + Jinja2 でテンプレート展開
- バージョン情報を冒頭に記録

### 完了基準
- プロンプトを差し替えやすい
- 単体テスト

### 依存
- Task 1.3.1

### 想定セッション数
0.3 セッション

---

## Task 1.3.3：LangChain 統合

### 目的
LangChain ベースの Agent パターン実装。

### 関連設計書
- AGENT_SPECS.md セクション 0.1（共通インターフェース）

### 実装内容
- LangChain の Tool 化（MCPTool → LangChain Tool）
- structured_output の使い方統一
- コールバックでコスト記録

### 完了基準
- 1つのサンプルエージェントが LangChain で動く
- コールバックで cost_logs に記録される

### 依存
- Task 1.3.1, Task 1.3.2

### 想定セッション数
1 セッション

---

## Task 1.3.4：HALT・予算チェック

### 目的
エージェント実行前のガードレール。

### 関連設計書
- ORCHESTRATION.md セクション 1.4 / 2.2（pre_check）

### 実装内容
- HALT ファイル検知
- 予算チェック（事前見積もり）
- 緊急停止時の挙動

### 完了基準
- HALT ファイル作成でエージェント実行が止まる
- 予算超過で実行拒否される

### 依存
- Task 1.3.1

### 想定セッション数
0.3 セッション

---

## Task 1.3.5：エージェント実行ログ

### 目的
analysis_logs への記録。

### 関連設計書
- SYSTEM_DESIGN.md セクション 2.3（analysis_logs）

### 実装内容
- execute_agent ラッパー内で analysis_logs に記録
- invocation_id によるトレース

### 完了基準
- 全エージェント実行が DB に記録される
- 単体テスト

### 依存
- Task 1.3.1

### 想定セッション数
0.3 セッション

---

## Task 1.3.6：エージェント結果のシリアライズ

### 目的
エージェント出力を DB に保存できる形式に統一。

### 実装内容
- buy_signals, sell_signals, screening_results, scenarios への保存ヘルパー
- 出力 Pydantic モデル → SQLModel への変換

### 完了基準
- 全エージェントが結果を DB に保存できる
- 単体テスト

### 依存
- Task 1.3.1

### 想定セッション数
0.3 セッション

---

## Phase 1.3 完了基準

- [x] エージェント基底クラスが完成
- [x] プロンプト外部化されている
- [x] LangChain と統合されている
- [x] HALT・予算チェックが機能

---

# Phase 1.4：エージェント実装

**ゴール**：6 つのエージェントを単体動作する状態まで作る。

## Task 1.4.1：topics-collector エージェント

### 関連設計書
- AGENT_SPECS.md セクション 6
- PANEL_SPECS.md C-1.5

### 実装内容
- news, disclosure ツールから収集
- デデュープ
- 重要度判定（ルール + LLM）
- 影響先銘柄抽出
- topics テーブルへの保存

### 完了基準
- 単体実行で topics が DB に保存される
- 重要度が適切に分類される
- 単体テスト + 統合テスト（実 API 叩く）

### 依存
- Phase 1.3 完了

### 想定セッション数
1 セッション

---

## Task 1.4.2：screening-agent

### 関連設計書
- AGENT_SPECS.md セクション 1
- PANEL_SPECS.md C-1.3（買い推奨）

### 実装内容
- screening ツール経由で V字 / テーマスコア
- 上位 20-30 件を screening_results に保存

### 完了基準
- universe から候補が抽出される
- 単体テスト + 統合テスト

### 依存
- Phase 1.3 完了
- Task 1.1.7（screening ツール）

### 想定セッション数
1 セッション

---

## Task 1.4.3：market-analyst エージェント

### 関連設計書
- AGENT_SPECS.md セクション 2
- PANEL_SPECS.md C-1.3

### 実装内容
- 候補銘柄を並列で分析
- 5 軸スコア
- 3 シナリオ
- thesis_checklist 生成
- buy_signals に保存

### 完了基準
- 1 銘柄あたり 30 秒以内で分析完了
- 10 銘柄並列で 5 分以内
- 単体テスト + 統合テスト

### 依存
- Task 1.4.2

### 想定セッション数
1.5 セッション

---

## Task 1.4.4：sell-recommender エージェント

### 関連設計書
- AGENT_SPECS.md セクション 3
- PANEL_SPECS.md C-1.2

### 実装内容
- 保有銘柄ごとに利確 / 損切りスコア
- シナリオ進捗評価
- 規律メッセージ（損切り時）
- sell_signals, scenarios に保存

### 完了基準
- 保有銘柄の状態が正しく分類される
- 単体テスト + 統合テスト

### 依存
- Task 1.4.3

### 想定セッション数
1 セッション

---

## Task 1.4.5：portfolio-builder エージェント

### 関連設計書
- AGENT_SPECS.md セクション 4

### 実装内容
- review モード（毎朝）
- initial モード（初回構築）
- 配分チェック

### 完了基準
- review モードが朝バッチで動く
- 単体テスト

### 依存
- Task 1.4.4

### 想定セッション数
0.5 セッション

---

## Task 1.4.6：manual-input-analyst エージェント

### 関連設計書
- AGENT_SPECS.md セクション 5
- PANEL_SPECS.md C-1.7

### 実装内容
- 投入テキストの解釈
- 影響先銘柄抽出
- manual_inputs テーブルへの保存
- トピックス化オプション

### 完了基準
- テキスト投入で 15 秒以内に解釈完了
- 単体テスト + 統合テスト

### 依存
- Phase 1.3 完了

### 想定セッション数
1 セッション

---

## Phase 1.4 完了基準

- [x] 6 エージェントが単体で動作
- [x] 各エージェントの出力が DB に保存される
- [x] スコア算出ロジックが PANEL_SPECS と一致
- [x] テストカバレッジ 70%+

ここまでで Phase 1.0-1.4 = **約 16-18 セッション**。

---

# Phase 1.5：オーケストレーター

**ゴール**：朝バッチが DAG として実行できる。

## Task 1.5.1：DAG エンジン

### 関連設計書
- ORCHESTRATION.md セクション 2.3, 2.4

### 実装内容
- `trading_agent/orchestrator/dag.py`
- DAGNode, DAGExecutor クラス
- 依存関係解決、並列実行
- タイムアウト処理

### 完了基準
- 簡単な DAG が実行できる
- 並列度が制御できる
- 単体テスト

### 依存
- Phase 1.4 完了

### 想定セッション数
1 セッション

---

## Task 1.5.2：朝バッチ DAG 定義

### 関連設計書
- ORCHESTRATION.md セクション 2.4

### 実装内容
- `trading_agent/orchestrator/morning_batch.py`
- 朝バッチの DAG 定義
- pre_check → topics → screening → market_analyst × N → sell_recommender → portfolio_builder → link_topics → summary → notify

### 完了基準
- 朝バッチ全体が DAG として実行される
- 各ステップの結果が DB に保存される
- 統合テスト

### 依存
- Task 1.5.1

### 想定セッション数
1 セッション

---

## Task 1.5.3：エラーハンドリング

### 関連設計書
- ORCHESTRATION.md セクション 4（エラー処理戦略）

### 実装内容
- Graceful Degradation の実装
- リトライ戦略（tenacity）
- batch_states テーブルへの記録

### 完了基準
- 1 エージェント失敗で全停止しない
- moomoo 切断時に degraded モードで継続
- 単体テスト

### 依存
- Task 1.5.2

### 想定セッション数
0.5 セッション

---

## Task 1.5.4：APScheduler 統合

### 関連設計書
- ORCHESTRATION.md セクション 1.2

### 実装内容
- 朝バッチを JST 5:00 にスケジュール
- daily_snapshot を JST 6:30 に
- 価格更新 / moomoo 同期 / 健康チェック

### 完了基準
- 起動時にスケジューラが動く
- 各ジョブがスケジュール通り実行される

### 依存
- Task 1.5.2

### 想定セッション数
0.3 セッション

---

## Task 1.5.5：健康チェック

### 関連設計書
- ORCHESTRATION.md セクション 7

### 実装内容
- 5 分ごとに全コンポーネントをチェック
- health_checks テーブルへの記録

### 完了基準
- 健康状態が DB から確認できる
- 単体テスト

### 依存
- Task 1.5.4

### 想定セッション数
0.5 セッション

---

## Phase 1.5 完了基準

- [x] 朝バッチが DAG として実行できる
- [x] スケジューラが動作
- [x] エラー時も止まらない
- [x] 健康チェックが機能

---

# Phase 1.6：UI（Next.js）

**ゴール**：ダッシュボードが動作する。

## Task 1.6.1：Next.js プロジェクトセットアップ

### 関連設計書
- OPERATIONS.md セクション 1.10

### 実装内容
- `ui/` ディレクトリに Next.js 14 プロジェクト
- TypeScript、Tailwind 設定
- 静的ビルド設定

### 完了基準
- `npm run dev` で起動できる
- 静的ビルド `next build && next export` が成功

### 依存
- Phase 1.5 完了

### 想定セッション数
0.5 セッション

---

## Task 1.6.2：FastAPI による Next.js 配信

### 実装内容
- FastAPI から静的ファイル配信
- API ルートと UI の共存

### 完了基準
- http://localhost:8000 でダッシュボードが表示

### 依存
- Task 1.6.1

### 想定セッション数
0.3 セッション

---

## Task 1.6.3：API ルート実装

### 関連設計書
- ORCHESTRATION.md セクション 10（手動トリガー）

### 実装内容
- `trading_agent/api/` の各ルーター
- /api/dashboard（全パネルのデータ取得）
- /api/refresh-prices
- /api/run-morning-batch
- /api/sync-moomoo
- /api/manual-input
- /api/halt, /api/resume

### 完了基準
- 全エンドポイントが Pydantic 検証通り
- OpenAPI ドキュメント生成
- 単体テスト

### 依存
- Task 1.6.2

### 想定セッション数
1 セッション

---

## Task 1.6.4：サイドバー UI

### 関連設計書
- PANEL_SPECS.md C-1.4
- dashboard.html（参考実装）

### 実装内容
- 総資産・配分・サマリー・ナビ
- 30日推移グラフ（Chart.js or 自作 SVG）
- Live インジケータ

### 完了基準
- データが API から取得できて表示
- 30日推移が描画される

### 依存
- Task 1.6.3

### 想定セッション数
1 セッション

---

## Task 1.6.5：売り買い推奨カード UI

### 関連設計書
- PANEL_SPECS.md C-1.2, C-1.3
- dashboard.html

### 実装内容
- 売りゾーン / 買いゾーンの左右配置
- カード（スコア、ティッカー、サマリー、ミニグラフ）
- ミニ予測グラフ（売り：取得→今→未来、買い：今→3シナリオ扇形）
- 「他の候補」リスト

### 完了基準
- データが API から取得できて表示
- グラフが正しく描画

### 依存
- Task 1.6.3

### 想定セッション数
1.5 セッション

---

## Task 1.6.6：保有銘柄カード UI

### 関連設計書
- PANEL_SPECS.md C-1.1
- dashboard.html

### 実装内容
- 3 カラムグリッド
- 状態縦線、ラベル
- 銘柄ごとの目標期間グラフ
- 進捗ステータス

### 完了基準
- 全保有銘柄が状態色で表示される
- グラフが目標期間で描画

### 依存
- Task 1.6.3

### 想定セッション数
1 セッション

---

## Task 1.6.7：トピックスセクション UI

### 関連設計書
- PANEL_SPECS.md C-1.5

### 実装内容
- タブ（すべて / マクロ / 業界 / 個別）
- トピックカード（重要度、見出し、要約、ソース、影響）
- 影響先銘柄のタグ

### 完了基準
- トピックスが重要度順 + 時系列で表示
- 外部リンクが開ける

### 依存
- Task 1.6.3

### 想定セッション数
0.5 セッション

---

## Task 1.6.8：Track Record UI

### 関連設計書
- PANEL_SPECS.md C-1.6

### 実装内容
- 統計 4 枚
- 累積リターン推移グラフ
- 各点のホバー詳細

### 完了基準
- 過去のレコメンドの集計が表示
- グラフが描画

### 依存
- Task 1.6.3

### 想定セッション数
0.5 セッション

---

## Task 1.6.9：手動投入 + 警告 + 決算予定 UI

### 関連設計書
- PANEL_SPECS.md C-1.7, C-1.8

### 実装内容
- 手動投入フォーム + 結果表示
- 警告セクション
- 決算予定セクション

### 完了基準
- 手動投入が API 経由で実行できる
- 警告・決算予定が表示

### 依存
- Task 1.6.3

### 想定セッション数
0.5 セッション

---

## Task 1.6.10：詳細パネル（売り / 買い）

### 関連設計書
- PANEL_SPECS.md C-1.2 セクション 9, C-1.3 セクション 9

### 実装内容
- 右からスライドイン、820px 幅
- 8 セクション構成
- moomoo で売る / 買う準備セクション（コピー可能フィールド）

### 完了基準
- 売り買いカードクリックで詳細パネルが開く
- 全 8 セクションが表示

### 依存
- Task 1.6.5

### 想定セッション数
1.5 セッション

---

## Phase 1.6 完了基準

- [x] ダッシュボードの全パネルが表示
- [x] 詳細パネルが開く
- [x] グラフ・インタラクションが動作
- [x] API 連携完了

ここまでで Phase 1.0-1.6 = **約 25 セッション**。

---

# Phase 1.7：常駐化と運用

**ゴール**：launchd 常駐、バックアップ、運用フロー完成。

## Task 1.7.1：launchd 設定

### 関連設計書
- OPERATIONS.md セクション 1.11

### 実装内容
- `scripts/setup_launchd.py`
- plist 生成
- インストールガイド

### 完了基準
- launchctl load で常駐化できる
- 再起動後も自動起動

### 依存
- Phase 1.6 完了

### 想定セッション数
0.5 セッション

---

## Task 1.7.2：バックアップスクリプト

### 関連設計書
- OPERATIONS.md セクション 3

### 実装内容
- `scripts/backup.py`
- 日次自動 + 手動
- 30 日分の保持

### 完了基準
- バックアップが作成される
- 復旧手順が機能

### 依存
- Task 1.7.1

### 想定セッション数
0.3 セッション

---

## Task 1.7.3：通知システム

### 関連設計書
- ORCHESTRATION.md セクション 2.2 notification

### 実装内容
- macOS 通知（pync）
- Slack webhook（オプション）

### 完了基準
- 朝バッチ完了通知が来る
- 重大エラー時に通知

### 依存
- Phase 1.5

### 想定セッション数
0.3 セッション

---

## Task 1.7.4：エンドツーエンドテスト

### 実装内容
- 朝バッチ → ダッシュボード表示 → 採用 → moomoo 同期 → 評価
- 全フローの動作確認

### 完了基準
- ペーパー口座で 1 日通しで動作

### 依存
- Task 1.7.1

### 想定セッション数
1 セッション

---

## Task 1.7.5：ドキュメント整備

### 実装内容
- README.md（セットアップガイド）
- CHANGELOG.md
- トラブルシューティング集（OPERATIONS.md 抜粋）

### 完了基準
- 新規ユーザーが README だけでセットアップできる

### 依存
- Task 1.7.4

### 想定セッション数
0.5 セッション

---

## Phase 1.7 完了基準

- [x] launchd で常駐化
- [x] バックアップ・通知が動作
- [x] 1 日通しでエンドツーエンドテスト成功
- [x] ドキュメント完備

ここまでで **Phase 1 実装完了**、約 27-30 セッション。

---

# Phase 1.8：4週間運用 + パラメータ調整

**ゴール**：実運用で精度を見て、settings を調整。

## 運用フェーズの作業

これは「タスクをこなす」ではなく「**運用しながら気づきを記録**」。

### 週次のチェック

- 朝バッチが毎日成功しているか
- スコアが妥当か（極端に偏ってない）
- API コストが見積もり内か
- ユーザーの採用率（adopted / skipped 比率）

### 月次の調整

- 設計書付録の「実運用で要調整の数値」を見直し
- パラメータ調整（settings テーブル）
- 必要ならコード修正

### Phase 2 移行判定

- OPERATIONS.md セクション 11 の チェックリストを満たすか
- 満たせば Phase 2 計画を開始

---

# 進捗トラッキング（PROGRESS.md）

実装中、Claude Code は `docs/PROGRESS.md` を更新：

```markdown
# Trading Agent 実装進捗

最終更新：YYYY-MM-DD

## Phase 1.0：環境構築
- [done] 1.0.1 プロジェクト初期化
- [done] 1.0.2 設定モジュール
- [done] 1.0.3 ロギング基盤
- [wip]  1.0.4 データベース初期化

## Phase 1.1：MCP ツール基盤
- [ ] 1.1.1 基底クラス
- [ ] 1.1.2 market_data
...
```

各セッション開始時に PROGRESS.md を確認、終了時に更新。

---

# よくある質問（Claude Code 向け）

### Q: 設計書に書いてない実装の細部は？
A: あなたの判断で実装してください。ただし設計書のセクション「Claude Code に委ねる範囲」を必ず確認。

### Q: テストを書く時間がない時は？
A: 純粋関数（スコア算出等）は必ず書く。それ以外は最低限のスモークテスト。Phase 1.7 のエンドツーエンドテストでカバーされる。

### Q: 既存のコードを大きく変更したくなったら？
A: ユーザーに確認してから動く。「もっと良い方法」は Phase 2 以降の検討事項。

### Q: LLM プロンプトを試行錯誤したい
A: OK。ただし AGENT_SPECS.md の構造（入力・出力スキーマ）は守る。プロンプトの細部は実装中に調整。

### Q: 1 つのタスクが想定セッション数を超えそう
A: タスクを分割。「Task 1.4.3.a」「Task 1.4.3.b」のように。PROGRESS.md に記録。

### Q: 設計書に矛盾を見つけた
A: ユーザーに報告。CLAUDE_CODE_INSTRUCTIONS.md セクション 9.3 の質問形式で。

---

# 完了の定義

Phase 1 が「完了」と言える条件：

✅ 全 8 Phase（1.0-1.7）のタスクが done
✅ ペーパー口座で 4 週間以上の朝バッチ実行
✅ 月次 API コストが ¥5,000 以内で安定
✅ ダッシュボードの全パネルが正常表示
✅ moomoo 連携（読み取り）が安定動作
✅ ユーザーが trace を通読してシステムに納得
✅ 緊急停止・復旧が確認済み

これらが揃って、Phase 2 への移行検討に入る。

---

**お疲れさまでした。Phase 1.0 から着手してください。**
