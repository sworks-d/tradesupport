# Trading Agent — システム設計書

最終更新：2026-05-22
ステータス：**STEP C-2 確定版**

このファイルは Trading Agent の **システム基盤** を定義する。
データモデル（テーブル定義）、MCPツール仕様、moomoo OpenD 接続管理、LLM呼び分け、設定ファイル仕様を含む。

関連ドキュメント：
- STEP_A_FINAL.md：要件定義
- STEP_B_FINAL.md：UI設計
- PANEL_SPECS.md：パネル別ロジック（C-1）
- AGENT_SPECS.md：エージェント設計（C-3、並行進行）
- ORCHESTRATION.md：オーケストレーター設計（C-4）
- OPERATIONS.md：運用設計（C-5）

---

# 📋 朝の確認用：C-2 で踏み込んだ判断

夜間作業中の判断ポイント。違和感あれば朝に議論したい。

## A. 大きな判断

### A-1. データベースは SQLite 一本でいく
- 候補：SQLite / PostgreSQL / DuckDB
- **採用：SQLite**（理由：ローカル完結、運用シンプル、Phase 1 のデータ量なら十分）
- 懸念：時系列データ（market_data, portfolio_snapshots）が増えた時の性能 → Phase 2- で DuckDB 併用を検討

### A-2. ORM は SQLModel
- 候補：SQLAlchemy / SQLModel / Tortoise / 生 SQL
- **採用：SQLModel**（FastAPI と相性が良く、Pydantic 統合で型安全）

### A-3. MCP ツールは Python で実装、FastAPI とは別プロセス
- MCP プロトコルでデータアクセスを統一
- 各 MCP ツールは独立したクラス、依存性逆転で moomoo / yfinance を差し替え可能に
- FastAPI が MCP ホストとして起動

### A-4. moomoo OpenD は別プロセス、Python SDK で接続
- moomoo OpenD は公式アプリ（GUI）として常時起動
- Python から moomoo-api パッケージで OpenD に接続
- 接続管理は専用クラス（BrokerConnection）で集約、リトライ・再接続を担当

### A-5. キャッシュは SQLite + メモリの2層
- 株価などの頻繁アクセスデータはメモリキャッシュ（5分 TTL）
- ファンダ・ニュースは SQLite に永続化
- LLM 応答はキャッシュしない（同じ入力でも結果が変わる可能性、検証性の低下）

## B. 細かい判断（推奨で進めた）

- B-1. 通貨は **JPY ベース**、外貨建ては元通貨で持って表示時に換算
- B-2. 時刻は **UTC で永続化、JST で表示**（時差処理を一箇所に集約）
- B-3. ID は **整数の自動採番**（UUID は使わない、デバッグしにくい）
- B-4. JSON カラムは SQLite の JSON 型（SQLModel の List/Dict + Column(JSON)）
- B-5. ログは **構造化ログ**（structlog）、stdout + ファイル両方
- B-6. 設定は **環境変数 + .env + settings テーブル** の3層
  - 環境変数：Docker 化した時の上書き用
  - .env：個人開発の中心
  - settings テーブル：ユーザーが UI から変えられる項目

## C. Phase 2- に意識的に残した項目

- C-1. **マルチユーザー対応**（Phase 1 は個人利用、認証なし）
- C-2. **Web 公開**（Phase 1 はローカル）
- C-3. **データのバックアップ自動化**（Phase 1 は手動）
- C-4. **マイグレーション管理**（Phase 1 は Alembic 導入のみ、初期スキーマで稼働）

---

# 1. 全体アーキテクチャ

## 1.1 プロセス構成

```
┌──────────────────────────────────────────────────────┐
│ macOS                                                 │
│                                                       │
│ ┌──────────────────────────────────────────────────┐ │
│ │ moomoo OpenD（GUI）                                │ │
│ │ - 常時起動                                          │ │
│ │ - localhost:11111 で WebSocket/REST 提供           │ │
│ └──────────────────────────────────────────────────┘ │
│                       │                                │
│                       │ moomoo-api SDK                 │
│                       ▼                                │
│ ┌──────────────────────────────────────────────────┐ │
│ │ trading-agent プロセス（FastAPI）                   │ │
│ │ - スケジューラ（APScheduler）                       │ │
│ │ - MCP ホスト                                        │ │
│ │ - エージェントオーケストレーター                     │ │
│ │ - Next.js UI 提供（静的ビルド）                     │ │
│ │ - localhost:8000                                   │ │
│ └──────────────────────────────────────────────────┘ │
│                       │                                │
│                       ├─ SQLite (~/.trading-agent/db) │
│                       ├─ Anthropic API (HTTPS)        │
│                       ├─ Ollama (localhost:11434)     │
│                       └─ 各種外部 API                   │
│                                                       │
│ launchd plist で trading-agent を常駐                 │
└──────────────────────────────────────────────────────┘
                       │
                       ▼
              ブラウザ (localhost:8000)
```

## 1.2 ディレクトリ構成

```
~/Projects/trading-agent/
├── pyproject.toml
├── .env                    # ユーザーが編集
├── .env.example            # コミット用テンプレート
├── README.md
│
├── trading_agent/          # メインパッケージ
│   ├── __init__.py
│   ├── main.py             # FastAPI エントリーポイント
│   ├── config.py           # 設定読み込み
│   │
│   ├── models/             # SQLModel テーブル定義
│   │   ├── __init__.py
│   │   ├── portfolio.py
│   │   ├── decisions.py
│   │   ├── topics.py
│   │   ├── signals.py      # buy_signals, sell_signals
│   │   ├── snapshots.py    # portfolio_snapshots
│   │   ├── universe.py
│   │   ├── analytics.py    # analysis_logs, cost_logs
│   │   └── settings.py
│   │
│   ├── mcp_tools/          # MCP ツール実装
│   │   ├── __init__.py
│   │   ├── base.py         # MCPTool 基底クラス
│   │   ├── market_data.py
│   │   ├── fundamentals.py
│   │   ├── disclosure.py
│   │   ├── news.py
│   │   ├── technicals.py
│   │   ├── screening.py
│   │   ├── broker_read.py
│   │   └── llm_call.py
│   │
│   ├── agents/             # エージェント実装
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── screening_agent.py
│   │   ├── market_analyst.py
│   │   ├── sell_recommender.py
│   │   ├── portfolio_builder.py
│   │   ├── manual_input_analyst.py
│   │   └── topics_collector.py
│   │
│   ├── orchestrator/       # オーケストレーター
│   │   ├── __init__.py
│   │   ├── morning_batch.py
│   │   ├── messages.py     # エージェント間メッセージスキーマ
│   │   └── error_handler.py
│   │
│   ├── brokers/            # ブローカー接続
│   │   ├── __init__.py
│   │   ├── moomoo_client.py
│   │   └── connection_manager.py
│   │
│   ├── llm/                # LLM 呼び分け
│   │   ├── __init__.py
│   │   ├── router.py       # Hot/Cold/Critical 判定
│   │   ├── anthropic_client.py
│   │   └── ollama_client.py
│   │
│   ├── api/                # FastAPI ルーター
│   │   ├── __init__.py
│   │   ├── routes_dashboard.py
│   │   ├── routes_actions.py     # 価格更新、採用ボタン等
│   │   └── routes_admin.py        # 緊急停止、設定変更
│   │
│   └── utils/
│       ├── __init__.py
│       ├── logger.py
│       ├── time_utils.py
│       └── currency.py
│
├── ui/                     # Next.js プロジェクト
│   └── ...
│
├── tests/
│   └── ...
│
└── scripts/
    ├── init_db.py          # 初回 DB 作成
    ├── setup_launchd.sh
    └── health_check.py
```

## 1.3 起動シーケンス

```
1. launchd が trading-agent プロセスを起動
2. config.py が .env と settings テーブルを読み込み
3. SQLite に接続、必要なら初期化
4. moomoo OpenD への接続を試みる（リトライ）
5. APScheduler を起動（朝バッチをスケジュール）
6. FastAPI サーバー起動（ポート 8000）
7. 健康チェックが各依存サービスをポーリング開始
```

## 1.4 通信プロトコル

| 通信 | プロトコル | 備考 |
|---|---|---|
| ブラウザ ↔ FastAPI | HTTP/JSON | localhost:8000 |
| FastAPI ↔ Anthropic | HTTPS/REST | API キー認証 |
| FastAPI ↔ Ollama | HTTP/REST | localhost:11434 |
| FastAPI ↔ moomoo OpenD | TCP/Socket | moomoo-api SDK 経由、localhost:11111 |
| FastAPI ↔ SQLite | ファイル | ~/.trading-agent/db.sqlite |
| FastAPI ↔ 外部 RSS/API | HTTPS/REST | NewsAPI, EDGAR, EDINET 等 |

---

# 2. データモデル（ERD）

## 2.1 全テーブル一覧

| カテゴリ | テーブル | 役割 |
|---|---|---|
| **マスタ** | universe | 銘柄母集団 |
| **マスタ** | settings | ユーザー設定 |
| **保有** | portfolio | 現在保有 |
| **保有** | portfolio_snapshots | 日次資産集計 |
| **市場データ** | market_data_cache | 価格キャッシュ（メモリ + DB） |
| **市場データ** | earnings_calendar | 決算予定 |
| **エージェント出力** | screening_results | スクリーニング結果 |
| **エージェント出力** | buy_signals | 買いレコメンド |
| **エージェント出力** | sell_signals | 売りレコメンド |
| **エージェント出力** | scenarios | シナリオ進捗 |
| **判断履歴** | decisions | レコメンド + 採否の履歴 |
| **情報** | topics | 収集ニュース |
| **情報** | manual_inputs | 手動投入分析 |
| **運用** | analysis_logs | エージェント実行ログ |
| **運用** | cost_logs | API消費ログ |
| **運用** | health_checks | 健康チェック結果 |

合計 **16テーブル**。

## 2.2 ER 図（テキスト表現）

```
universe ────┐
             │ ticker
             ▼
        portfolio ←──── moomoo_sync (同期トリガー)
             │ ticker
             ├──→ scenarios (1:1)
             ├──→ sell_signals (1:N、朝バッチで上書き)
             ├──→ decisions (1:N、履歴)
             └──→ portfolio_snapshots (集計用)

universe ────→ screening_results ──→ buy_signals ──→ decisions
                                                      │
                                                      ▼
                                                  portfolio
                                                  (採用時)

topics ────→ decisions (linked_decisions FK)
       ────→ manual_inputs (手動投入を topics 化した時)

analysis_logs ←─── 全エージェントから INSERT
cost_logs    ←─── 全 LLM 呼び出しから INSERT
health_checks ←── 5分ごとの監視ジョブから INSERT
```

## 2.3 テーブル詳細定義

以下、SQLModel での Python 定義を提示（実装そのままではないが、構造を示す）。

### universe（銘柄母集団）

```python
class Universe(SQLModel, table=True):
    __tablename__ = "universe"

    ticker: str = Field(primary_key=True)
    name: str
    name_en: Optional[str]
    market: str  # "US" / "JP"
    sector: str
    industry: Optional[str]
    market_cap: float  # USD or JPY、元通貨
    market_cap_jpy: float  # JPY 換算後
    avg_volume_30d: float  # 30日平均出来高
    is_active: bool = True  # スクリーニング対象か
    listed_date: Optional[date]
    delisted_date: Optional[date]
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

**更新頻度**：週次（毎週月曜の朝バッチ）
**サイズ目安**：1500銘柄（US 1000 + JP 500）

### portfolio（保有銘柄）

```python
class Portfolio(SQLModel, table=True):
    __tablename__ = "portfolio"

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(foreign_key="universe.ticker", index=True)

    # 取得情報
    buy_date: date
    buy_price: float  # 元通貨
    qty: int
    currency: str  # "USD" / "JPY"

    # 戦略情報（買い推奨採用時にコピー）
    strategy_category: str  # "中期" / "長期" / "中期-長期" / "短期"
    target_period_days: int
    target_pct: float  # 0.10 = +10%
    stop_loss_pct: float  # -0.08 = -8%
    target_date: date  # buy_date + target_period_days
    thesis: str  # 投資仮説（テキスト）
    thesis_checklist: List[dict] = Field(sa_column=Column(JSON))
    # 例: [{"item": "Q売上 +20%", "checked": false}, ...]

    # ステータス
    status: str  # "active" / "closed" / "watching"
    closed_at: Optional[datetime]
    closed_price: Optional[float]
    closed_reason: Optional[str]  # "profit_taking" / "stop_loss" / "manual"

    # 同期情報
    moomoo_position_id: Optional[str]  # moomoo側のID
    last_synced_at: datetime = Field(default_factory=datetime.utcnow)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

**更新頻度**：5分ごとに moomoo 同期 + ユーザー採用時
**サイズ目安**：active 5-20件、closed 含めて累計100件以下/年

### portfolio_snapshots（日次資産集計）

```python
class PortfolioSnapshot(SQLModel, table=True):
    __tablename__ = "portfolio_snapshots"

    date: date = Field(primary_key=True)

    total_assets_jpy: float
    cash_jpy: float
    us_stocks_value_jpy: float
    jp_stocks_value_jpy: float
    satellite_value_jpy: float
    core_value_jpy: float

    usd_jpy_rate: float
    holding_count: int
    daily_pnl_jpy: float  # 前日比

    created_at: datetime = Field(default_factory=datetime.utcnow)
```

**更新頻度**：日次（米国市場引け後 = JST 早朝）
**サイズ目安**：1レコード/日、5年で 1800件

### market_data_cache（価格キャッシュ）

```python
class MarketDataCache(SQLModel, table=True):
    __tablename__ = "market_data_cache"

    ticker: str = Field(primary_key=True)

    current_price: float
    open_price: float
    high_today: float
    low_today: float
    prev_close: float
    volume_today: float
    price_change_today: float
    price_change_pct_today: float

    bid: Optional[float]
    ask: Optional[float]

    market_status: str  # "open" / "closed" / "pre" / "post"

    as_of: datetime = Field(default_factory=datetime.utcnow)
    source: str = "moomoo"
```

**更新頻度**：5分ごと（市場時間中）
**メモリキャッシュも併用**：5分 TTL、永続化はバックアップ用

### earnings_calendar（決算予定）

```python
class EarningsCalendar(SQLModel, table=True):
    __tablename__ = "earnings_calendar"

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(foreign_key="universe.ticker", index=True)
    earnings_date: date = Field(index=True)
    earnings_time: str  # "before_open" / "after_close" / "during" / "unknown"
    fiscal_quarter: Optional[str]  # "Q1 2026" 等
    eps_estimate: Optional[float]
    revenue_estimate: Optional[float]
    eps_actual: Optional[float]  # 発表後
    revenue_actual: Optional[float]  # 発表後
    source: str  # "yfinance" / "earningswhispers" 等
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

**更新頻度**：朝バッチ
**サイズ目安**：1000銘柄 × 4Q/年 = 4000件/年

### screening_results（スクリーニング結果）

```python
class ScreeningResult(SQLModel, table=True):
    __tablename__ = "screening_results"

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(foreign_key="universe.ticker", index=True)
    screened_at: datetime = Field(index=True)

    v_shape_score: float  # 0-100
    theme_score: float  # 0-100
    composite_score: float  # 上記2つの最大値
    screening_passed: bool

    v_shape_details: dict = Field(sa_column=Column(JSON))
    # 例: {"earnings_turnaround": true, "price_bottom": true, "rsi": 35, ...}

    theme_details: dict = Field(sa_column=Column(JSON))
    # 例: {"matched_themes": ["AI", "半導体"], "theme_strength": 0.8}

    created_at: datetime = Field(default_factory=datetime.utcnow)
```

**更新頻度**：朝バッチ
**サイズ目安**：500-700 銘柄 × 1回/日 = 200K件/年（古いものは月次でアーカイブ）

### buy_signals

```python
class BuySignal(SQLModel, table=True):
    __tablename__ = "buy_signals"

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(foreign_key="universe.ticker", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)

    # スコア（5軸の総合）
    score: int  # 0-100
    fundamental_score: float
    technical_score: float
    news_sentiment_score: float
    strategy_fit_score: float
    ai_confidence: float

    # 予測
    expected_return: float
    win_rate: float
    target_period_days: int
    target_price: float
    entry_price: float
    stop_loss_price: float

    # 戦略
    strategy_category: str

    # 詳細
    thesis_checklist: List[dict] = Field(sa_column=Column(JSON))
    reasons: List[dict] = Field(sa_column=Column(JSON))
    risks: List[dict] = Field(sa_column=Column(JSON))
    scenarios: List[dict] = Field(sa_column=Column(JSON))
    # scenarios 例:
    # [
    #   {"type": "bull", "target_price": 140, "return_pct": 0.42, "prob": 0.30, "desc": "..."},
    #   {"type": "base", "target_price": 125, "return_pct": 0.27, "prob": 0.50, "desc": "..."},
    #   {"type": "bear", "target_price": 90, "return_pct": -0.09, "prob": 0.20, "desc": "..."}
    # ]

    # メタ
    recommended_amount_jpy: int
    is_active: bool = True  # 翌日には false、新しいバッチで上書き
```

**更新頻度**：朝バッチ（前回分は is_active = false にする）
**サイズ目安**：10-20件/日 = 5000件/年

### sell_signals

```python
class SellSignal(SQLModel, table=True):
    __tablename__ = "sell_signals"

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(foreign_key="portfolio.ticker", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)

    signal_type: str  # "profit_taking" / "stop_loss"

    # スコア
    score: int  # 0-100
    # 利確の場合
    target_achievement_score: Optional[float]
    scenario_achievement_score: Optional[float]
    technical_warning_score: Optional[float]
    # 損切りの場合
    scenario_break_score: Optional[float]
    loss_magnitude_score: Optional[float]
    negative_news_score: Optional[float]
    # 共通
    ai_confidence: float

    # 詳細
    reasons: List[dict] = Field(sa_column=Column(JSON))
    risks: List[dict] = Field(sa_column=Column(JSON))
    recommended_action: dict = Field(sa_column=Column(JSON))
    # 例: {"type": "limit_sell", "price": 2830, "qty": 5, "note": "全量売却"}

    is_active: bool = True
```

**更新頻度**：朝バッチ
**サイズ目安**：0-5件/日

### scenarios（シナリオ進捗）

```python
class Scenario(SQLModel, table=True):
    __tablename__ = "scenarios"

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(foreign_key="portfolio.ticker", unique=True)

    scenario_health: float  # 0.0-1.0
    scenario_status: str  # "intact" / "weakening" / "broken"

    # チェックリストの進捗（thesis_checklist との連動）
    checklist_progress: List[dict] = Field(sa_column=Column(JSON))
    # 例: [{"item": "Q売上+20%", "checked": true, "evidence": "Q3決算..."}, ...]

    # 評価根拠
    evaluation_notes: str

    latest_update: datetime = Field(default_factory=datetime.utcnow)
```

**更新頻度**：朝バッチ
**サイズ目安**：active 保有銘柄分のみ、5-20件

### decisions（判断履歴）

```python
class Decision(SQLModel, table=True):
    __tablename__ = "decisions"

    id: Optional[int] = Field(default=None, primary_key=True)

    # 何のレコメンドか
    date: date = Field(index=True)
    ticker: str = Field(index=True)
    action: str  # "buy" / "sell_profit" / "sell_loss"
    source_signal_id: Optional[int]  # buy_signals.id or sell_signals.id

    # レコメンド時のスナップショット
    score: int
    expected_return: float
    target_period_days: int
    scenarios: dict = Field(sa_column=Column(JSON))
    thesis_at_decision: str

    # ユーザーの反応
    user_action: Optional[str]  # "adopted" / "skipped" / "modified" / "deferred"
    user_acted_at: Optional[datetime]
    user_note: Optional[str]

    # 評価
    evaluation_date: date  # date + target_period_days
    actual_return: Optional[float]
    hit_or_miss: str = "pending"  # "hit" / "miss" / "neutral" / "pending"
    evaluated_at: Optional[datetime]

    # 紐付いたトピックス
    supporting_topic_ids: List[int] = Field(sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=datetime.utcnow)
```

**更新頻度**：レコメンド時 + ユーザー採用時 + 評価日
**サイズ目安**：累計 1000件/年

### topics（収集ニュース）

```python
class Topic(SQLModel, table=True):
    __tablename__ = "topics"

    id: Optional[int] = Field(default=None, primary_key=True)
    collected_at: datetime = Field(default_factory=datetime.utcnow, index=True)

    # ソース
    source: str  # "Bloomberg" / "Reuters" / "EDINET" 等
    source_url: str
    additional_sources: List[dict] = Field(sa_column=Column(JSON))
    # 例: [{"name": "日経", "url": "..."}, ...]

    # 内容
    category: str  # "macro" / "sector" / "stock"
    importance: str  # "high" / "medium" / "low"
    headline: str
    summary: str
    original_text: Optional[str]  # 元のテキスト（オプション、容量節約）
    original_text_hash: str  # デデュープ用

    # 影響
    affected_tickers: List[str] = Field(sa_column=Column(JSON))
    impact_text: str  # "TSLA 損切り推奨の根拠の一つ"
    linked_decisions: List[int] = Field(sa_column=Column(JSON))

    # メタ
    fetched_by: str  # "morning_batch" / "manual_input"
    importance_judged_by: str  # "rule" / "llm" / "user"

    is_archived: bool = False  # 古いものは月次でアーカイブ
```

**更新頻度**：朝バッチ + 手動投入時
**サイズ目安**：30-100件/日 = 30K件/年（アーカイブで圧縮）

### manual_inputs

```python
class ManualInput(SQLModel, table=True):
    __tablename__ = "manual_inputs"

    id: Optional[int] = Field(default=None, primary_key=True)
    submitted_at: datetime = Field(default_factory=datetime.utcnow)

    input_text: str
    input_url: Optional[str]
    input_type: str  # "text" / "url" / "ticker"

    # 分析結果
    analyzed_at: datetime
    result_summary: str
    affected_tickers: List[str] = Field(sa_column=Column(JSON))
    impact_direction: str  # "positive" / "negative" / "neutral"
    impact_magnitude: str  # "large" / "medium" / "small"
    recommended_action: Optional[str]

    # ユーザーアクション
    added_to_topics: bool = False
    topic_id: Optional[int]  # トピックス化した場合

    # メタ
    llm_model: str
    llm_cost_jpy: float
```

**更新頻度**：ユーザー投入時
**サイズ目安**：5-20件/日

### analysis_logs（エージェント実行ログ）

```python
class AnalysisLog(SQLModel, table=True):
    __tablename__ = "analysis_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    agent: str  # "screening_agent" / "market_analyst" / 等
    invocation_id: str  # UUID for grouping

    started_at: datetime
    ended_at: Optional[datetime]
    duration_ms: Optional[int]

    input_summary: str  # input の summary（巨大な場合は省略）
    input_full: Optional[dict] = Field(sa_column=Column(JSON))

    output_summary: str
    output_full: Optional[dict] = Field(sa_column=Column(JSON))

    status: str  # "running" / "success" / "failure" / "timeout"
    error_msg: Optional[str]

    llm_calls: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_cost_jpy: float = 0.0
```

**更新頻度**：エージェント実行ごと
**サイズ目安**：100件/日 = 36K件/年

### cost_logs（API消費ログ）

```python
class CostLog(SQLModel, table=True):
    __tablename__ = "cost_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow, index=True)
    date: date = Field(index=True)

    model: str  # "claude-sonnet-4-6" / "claude-opus-4-7" / "ollama:llama3.1" / 等
    agent: str  # 呼び出し元のエージェント
    purpose: str  # "screening" / "analysis" / "summarization" 等

    tokens_in: int
    tokens_out: int
    cost_usd: float
    cost_jpy: float

    invocation_id: Optional[str]  # analysis_logs と連携
```

**更新頻度**：LLM 呼び出しごと
**サイズ目安**：500件/日 = 180K件/年

### health_checks（健康チェック）

```python
class HealthCheck(SQLModel, table=True):
    __tablename__ = "health_checks"

    id: Optional[int] = Field(default=None, primary_key=True)
    component: str  # "moomoo_opend" / "anthropic_api" / "ollama" / "newsapi" / 等
    status: str  # "ok" / "degraded" / "down"
    checked_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    response_time_ms: Optional[int]
    error_msg: Optional[str]
```

**更新頻度**：5分ごと
**サイズ目安**：10コンポーネント × 288回/日 = 3K件/日（週次でアーカイブ）

### settings（ユーザー設定）

```python
class Setting(SQLModel, table=True):
    __tablename__ = "settings"

    key: str = Field(primary_key=True)
    value: str  # JSON string
    value_type: str  # "int" / "float" / "str" / "json" / "bool"
    category: str  # "budget" / "strategy" / "ui" / 等
    description: Optional[str]
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    updated_by: str = "user"
```

**初期値**：

| key | value | type | category |
|---|---|---|---|
| monthly_budget_jpy | 5000 | int | budget |
| daily_budget_jpy | 500 | int | budget |
| budget_breach_action | "halt" | str | budget |
| usd_jpy_rate_override | null | float | currency |
| morning_batch_time | "05:00" | str | schedule |
| auto_sync_interval_min | 5 | int | schedule |
| max_position_pct_of_cash | 0.20 | float | risk |
| max_position_pct_of_total | 0.10 | float | risk |
| screening_universe_size | 500 | int | screening |
| paper_mode | true | bool | mode |

## 2.4 インデックス戦略

クエリ頻度の高い列：

```sql
-- Portfolio
CREATE INDEX idx_portfolio_status ON portfolio(status);
CREATE INDEX idx_portfolio_ticker ON portfolio(ticker);

-- Decisions
CREATE INDEX idx_decisions_date ON decisions(date);
CREATE INDEX idx_decisions_ticker ON decisions(ticker);
CREATE INDEX idx_decisions_hit_miss ON decisions(hit_or_miss) WHERE hit_or_miss != 'pending';

-- Topics
CREATE INDEX idx_topics_collected_at ON topics(collected_at);
CREATE INDEX idx_topics_importance ON topics(importance);
CREATE INDEX idx_topics_category ON topics(category);
CREATE INDEX idx_topics_archived ON topics(is_archived) WHERE is_archived = false;

-- Cost logs (時系列クエリ多い)
CREATE INDEX idx_cost_logs_date ON cost_logs(date);
CREATE INDEX idx_cost_logs_timestamp ON cost_logs(timestamp);
```

## 2.5 マイグレーション

- **Alembic** を使用
- 初期スキーマは `scripts/init_db.py` で一括作成
- Phase 1 中は Breaking Change OK（個人利用、データ捨てて再構築可）
- Phase 2- では正式にマイグレーション運用

---

# 3. MCP ツール仕様

## 3.1 共通インターフェース

全 MCP ツールは以下の基底クラスを継承：

```python
from abc import ABC, abstractmethod
from typing import Any, Dict
from pydantic import BaseModel

class MCPToolInput(BaseModel):
    """ツール入力の基底"""
    pass

class MCPToolOutput(BaseModel):
    """ツール出力の基底"""
    success: bool
    error: Optional[str] = None
    data: Optional[Any] = None
    metadata: Dict[str, Any] = {}

class MCPTool(ABC):
    """全 MCP ツールの基底クラス"""

    name: str  # ツール名
    description: str  # LLM 向けの説明
    input_schema: type[MCPToolInput]
    output_schema: type[MCPToolOutput]

    @abstractmethod
    async def execute(self, input: MCPToolInput) -> MCPToolOutput:
        """ツールを実行"""
        pass

    async def health_check(self) -> bool:
        """ツールの依存先（API等）が生きているか確認"""
        return True
```

## 3.2 各ツールの仕様

### market_data

```python
class MarketDataInput(MCPToolInput):
    tickers: List[str]
    fields: List[str] = ["current_price", "volume_today", "prev_close"]
    use_cache: bool = True  # 5分以内のキャッシュを使うか

class MarketDataOutput(MCPToolOutput):
    data: Dict[str, Dict[str, float]]  # {ticker: {field: value}}
    as_of: datetime
    sources: Dict[str, str]  # どのソースから取得したか
```

**実装方針**：
- 第一優先：moomoo OpenD（リアルタイム）
- 第二優先：メモリキャッシュ（5分以内）
- 第三優先：market_data_cache テーブル
- 第四優先：yfinance / J-Quants（補助）

**エラー処理**：
- moomoo 失敗 → ログに記録 + yfinance フォールバック
- 全失敗 → cache テーブルの最新を返す + warning

### fundamentals

```python
class FundamentalsInput(MCPToolInput):
    ticker: str
    fields: List[str] = ["eps", "per", "pbr", "revenue_growth", "operating_margin"]
    period: str = "latest"  # "latest" / "ttm" / "annual"

class FundamentalsOutput(MCPToolOutput):
    data: Dict[str, float]
    fiscal_period: str
    source_url: Optional[str]  # 一次情報へのリンク
```

**実装方針**：
- 米国株：SEC EDGAR（10-K, 10-Q）
- 日本株：EDINET（有報、四報）
- yfinance / J-Quants を補助
- LLM で要約は別途（fundamentals 自体は raw データ提供）

### disclosure

```python
class DisclosureInput(MCPToolInput):
    tickers: Optional[List[str]] = None  # None なら全市場
    since: Optional[datetime] = None  # この時刻以降
    market: Optional[str] = "JP"  # 当面 JP のみ

class DisclosureOutput(MCPToolOutput):
    disclosures: List[dict]
    # 例: [{"ticker": "7203", "title": "決算速報", "url": "...", "published_at": "..."}]
```

**実装方針**：
- TDnet RSS をパース
- EDINET の API も併用
- 米国は SEC EDGAR の Form 8-K 系を将来追加

### news

```python
class NewsInput(MCPToolInput):
    tickers: Optional[List[str]] = None
    topics: Optional[List[str]] = None  # ["AI", "半導体", "規制" 等]
    since: Optional[datetime] = None
    sources: Optional[List[str]] = None  # 特定ソースに絞る場合

class NewsOutput(MCPToolOutput):
    articles: List[dict]
    # 例: [{"title": "...", "summary": "...", "source": "Bloomberg", "url": "...", "published_at": "..."}]
```

**実装方針**：
- NewsAPI + RSS フィード
- 重複除去（headline + URL のハッシュ）
- 日本語/英語の自動判別

### technicals

```python
class TechnicalsInput(MCPToolInput):
    ticker: str
    indicators: List[str] = ["rsi", "macd", "sma_20", "sma_60", "bollinger"]
    period_days: int = 90

class TechnicalsOutput(MCPToolOutput):
    data: Dict[str, Any]
    # 例: {"rsi": 72, "macd": {"value": 1.2, "signal": 0.8}, "sma_20": 215.3, ...}
    signals: List[str]
    # 例: ["overbought_rsi", "death_cross_warning"]
```

**実装方針**：
- TA-Lib で計算
- 過去データは market_data_cache or moomoo
- シグナル名はあらかじめ定義（"overbought_rsi", "golden_cross" 等）

### screening

```python
class ScreeningInput(MCPToolInput):
    strategy: str  # "v_shape" / "theme"
    universe: Optional[List[str]] = None  # None なら settings の universe
    filters: Optional[dict] = None

class ScreeningOutput(MCPToolOutput):
    results: List[dict]
    # 例: [{"ticker": "...", "score": 78, "details": {...}}]
    total_screened: int
    passed_count: int
```

**実装方針**：
- V字回復ロジック、テーマロジックは別モジュール
- screening tool は「実行 + 結果保存」が責務、ロジックそのものは別

### broker_read

```python
class BrokerReadInput(MCPToolInput):
    action: str  # "positions" / "balance" / "transactions" / "all"
    since: Optional[datetime] = None  # transactions のみ

class BrokerReadOutput(MCPToolOutput):
    data: dict
    # 例:
    # {"positions": [...], "cash": {"USD": 100, "JPY": 50000}, "transactions": [...]}
    as_of: datetime
```

**実装方針**：
- moomoo OpenD への問い合わせを抽象化
- 接続切れの場合はキャッシュから返す + warning
- portfolio テーブルとの同期はここでは行わない（呼び出し側の責務）

### llm_call

```python
class LLMCallInput(MCPToolInput):
    prompt: str
    system: Optional[str] = None
    purpose: str  # "screening" / "analysis" / "summarization" 等
    routing_hint: Optional[str] = None  # "hot" / "cold" / "critical"
    max_tokens: int = 4000
    temperature: float = 0.0
    response_format: Optional[str] = None  # "json" / "text"

class LLMCallOutput(MCPToolOutput):
    response: str
    model_used: str
    tokens_in: int
    tokens_out: int
    cost_jpy: float
    duration_ms: int
```

**実装方針**：
- ルーティングは routing_hint と purpose から自動判定（後述）
- 全呼び出しを cost_logs に記録
- 日次予算超過時はエラー（routing_hint='critical' なら警告のみで通す）

## 3.3 MCP ツールの実行順序例

朝バッチの中で、screening_agent が複数ツールを順次呼び出す例：

```
1. screening tool: V字回復スコアを計算 → 候補20件
2. market_data tool: 候補20件の最新価格を取得
3. fundamentals tool: 候補20件の財務データを取得
4. news tool: 候補20件の最新ニュースを取得
5. technicals tool: 候補20件のテクニカル指標を計算
6. llm_call tool: 各銘柄を LLM で総合評価
7. → buy_signals テーブルに保存
```

## 3.4 エラーハンドリングの統一原則

各ツールは以下のエラーパターンを明示的に扱う：

| エラータイプ | 挙動 |
|---|---|
| **NETWORK_ERROR** | 3回リトライ（指数バックオフ）→ キャッシュにフォールバック |
| **RATE_LIMIT** | リトライ時間を尊重して待機 → キャッシュにフォールバック |
| **AUTH_ERROR** | 即時失敗、health_check が検知 |
| **DATA_NOT_FOUND** | 空データ返却（エラーではない） |
| **VALIDATION_ERROR** | 即時失敗、bug の可能性 |

ツール出力の `success: false` は呼び出し側で判断、`data: None` でも `success: true` の場合は「該当データなし」と区別する。

---

# 4. moomoo OpenD 接続管理

## 4.1 構成

moomoo OpenD は **公式アプリ（GUI）として常時起動**。Trading Agent はそこに **Python SDK 経由で接続** する。

```
┌─────────────────────────────┐
│ Trading Agent               │
│ ┌─────────────────────────┐ │
│ │ BrokerConnection (Singleton) │
│ │ - connect()              │ │
│ │ - subscribe(tickers)     │ │
│ │ - get_positions()        │ │
│ │ - get_balance()          │ │
│ │ - get_transactions()     │ │
│ │ - is_connected()         │ │
│ │ - reconnect()            │ │
│ └─────────────┬─────────────┘ │
│               │ moomoo-api    │
└───────────────┼───────────────┘
                │
                ▼
        moomoo OpenD (GUI)
        - localhost:11111
        - WebSocket / Socket
```

## 4.2 BrokerConnection クラス

```python
class BrokerConnection:
    """moomoo OpenD への接続を管理する Singleton"""

    def __init__(self):
        self.client = None
        self.connected = False
        self.last_heartbeat = None
        self.subscribed_tickers = set()

    async def connect(self) -> bool:
        """初回接続。失敗時は再試行。"""
        ...

    async def is_connected(self) -> bool:
        """接続状態を確認（5秒以内に heartbeat があれば True）"""
        ...

    async def reconnect(self) -> bool:
        """再接続。指数バックオフでリトライ。"""
        ...

    async def subscribe(self, tickers: List[str]):
        """ティッカーをサブスクライブ（リアルタイム価格更新）"""
        ...

    async def get_positions(self) -> List[Position]:
        ...

    async def get_balance(self) -> Balance:
        ...

    async def get_transactions(self, since: datetime) -> List[Transaction]:
        ...

    async def get_quote(self, ticker: str) -> Quote:
        ...
```

## 4.3 接続戦略

### 起動時
1. moomoo OpenD への接続を試みる
2. 失敗 → 5秒待ってリトライ（最大 6回 = 30秒）
3. それでも失敗 → degraded モードで起動（market_data はキャッシュのみ）
4. 接続成功 → 保有銘柄をサブスクライブ

### 運用中
- 30秒ごとに heartbeat を送信
- 5秒以内に応答がない → reconnect
- 連続 5回失敗 → degraded モード、ユーザーに通知

### 切断時
- portfolio.last_synced_at が 10分以上古い → ダッシュボードに警告
- 価格データは market_data_cache から提供（古いことを明示）

## 4.4 ペーパー/実弾モード

```python
class TradingMode(Enum):
    PAPER = "paper"
    LIVE = "live"
```

- 設定は `.env` の `TRADING_MODE`
- 起動時に**明示的な確認を要求**（実弾モード時）
- moomoo SDK の接続先を変える（ペーパー口座 vs 実口座）

## 4.5 イベント

moomoo からの非同期イベントを受け取る：

- **約定通知**：portfolio テーブル更新トリガー
- **建玉変動**：portfolio 同期トリガー
- **接続切断**：reconnect トリガー
- **市場開閉**：market_status の更新

---

# 5. LLM 呼び分け基準

## 5.1 3段階のルーティング

```
purpose + routing_hint → モデル選択
                              │
                              ├─ Cold Path：Ollama（無料、ローカル）
                              ├─ Hot Path：Claude Sonnet 4.6
                              └─ Critical：Claude Opus 4.7
```

## 5.2 判定マトリクス

| purpose / routing_hint | Cold | Hot | Critical |
|---|---|---|---|
| `summarization` | ✓ | (重い時のみ) | - |
| `classification` (重要度判定等) | ✓ | (曖昧な時のみ) | - |
| `screening` (定量) | - | ✓ | - |
| `analysis` (個別銘柄) | - | ✓ | - |
| `sell_recommendation` | - | ✓ | - |
| `buy_recommendation` | - | ✓ | - |
| `manual_input_analysis` | - | ✓ | - |
| `deep_dive` (詳細分析) | - | - | ✓ |
| `scenario_break_analysis` (損切り根拠) | - | (推奨) | (曖昧な時) |

## 5.3 自動判定ロジック

```python
def route_llm_call(input: LLMCallInput) -> str:
    """どのモデルを使うか決定"""

    # 明示的なヒントがあればそれを尊重
    if input.routing_hint:
        if input.routing_hint == "cold":
            return "ollama"
        if input.routing_hint == "critical":
            return "claude-opus-4-7"
        return "claude-sonnet-4-6"

    # purpose ベースのデフォルト
    cold_purposes = {"summarization", "classification", "ner"}
    critical_purposes = {"deep_dive"}

    if input.purpose in cold_purposes:
        # コスト節約：可能ならcold
        # ただし、入力が長すぎる場合（>4000 token）は hot に格上げ
        if estimate_tokens(input.prompt) > 4000:
            return "claude-sonnet-4-6"
        return "ollama"

    if input.purpose in critical_purposes:
        return "claude-opus-4-7"

    # デフォルトは Hot
    return "claude-sonnet-4-6"
```

## 5.4 モデル別のコスト想定

【たたき台】2026年5月時点：

| モデル | 入力 (per 1K) | 出力 (per 1K) | 用途 | 月次想定 |
|---|---|---|---|---|
| Ollama (local) | ¥0 | ¥0 | 要約・分類 | ¥0 |
| Claude Sonnet 4.6 | ¥0.45 | ¥2.25 | 分析・判断 | ¥3,000-4,000 |
| Claude Opus 4.7 | ¥2.25 | ¥11.25 | 深掘り（年数回） | ¥500-1,000 |

合計 **月 ¥3,500-5,000** で予算内に収まる試算。実コストは Phase 1 で実測。

## 5.5 予算管理

```python
class BudgetGuard:
    """LLM 呼び出し前の予算チェック"""

    async def can_proceed(self, estimated_cost: float, hint: str) -> bool:
        today_spent = await get_today_cost()
        month_spent = await get_month_cost()

        daily_limit = settings.daily_budget_jpy
        monthly_limit = settings.monthly_budget_jpy

        # critical は警告のみで通す
        if hint == "critical":
            if today_spent + estimated_cost > daily_limit:
                log.warning("Daily budget exceeded by critical call")
            return True

        # それ以外は厳密
        if today_spent + estimated_cost > daily_limit:
            return False
        if month_spent + estimated_cost > monthly_limit:
            return False
        return True
```

## 5.6 LLM クライアント実装

```python
class LLMRouter:
    """LLM 呼び分けの実装"""

    def __init__(self):
        self.anthropic = AnthropicClient()
        self.ollama = OllamaClient()
        self.budget = BudgetGuard()

    async def call(self, input: LLMCallInput) -> LLMCallOutput:
        # ルーティング
        model = route_llm_call(input)

        # 予算チェック
        estimated = estimate_cost(model, input.prompt, input.max_tokens)
        if not await self.budget.can_proceed(estimated, input.routing_hint):
            raise BudgetExceededError(
                f"Cannot proceed: today=¥{today_spent}, "
                f"estimated=+¥{estimated}, limit=¥{daily_limit}"
            )

        # 実行
        start = time.time()
        if model.startswith("claude"):
            response = await self.anthropic.call(model, input)
        else:
            response = await self.ollama.call(model, input)
        duration_ms = (time.time() - start) * 1000

        # コスト記録
        await record_cost(model, response.tokens, input.purpose)

        return LLMCallOutput(
            response=response.text,
            model_used=model,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            cost_jpy=response.cost,
            duration_ms=duration_ms,
        )
```

---

# 6. 環境変数 / 設定ファイル仕様

## 6.1 .env（プロジェクトルート）

```bash
# === API キー（必須） ===
ANTHROPIC_API_KEY=sk-ant-...

# === オプション API キー ===
NEWSAPI_KEY=...
JQUANTS_REFRESH_TOKEN=...
EDINET_API_KEY=...  # EDINETは2026年からキー必要

# === moomoo ===
MOOMOO_OPEND_HOST=localhost
MOOMOO_OPEND_PORT=11111
MOOMOO_TRADING_PWD=...  # 取引パスワード
MOOMOO_ACCOUNT_ID=...

# === モード ===
TRADING_MODE=paper  # paper / live
LOG_LEVEL=INFO

# === パス ===
DB_PATH=~/.trading-agent/db.sqlite
LOG_DIR=~/.trading-agent/logs
DATA_DIR=~/.trading-agent/data

# === LLM ===
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b

# === 通知（オプション） ===
SLACK_WEBHOOK_URL=
MACOS_NOTIFICATION=true

# === 緊急停止 ===
HALT_FILE=~/.trading-agent/HALT  # このファイルがあれば全停止
```

## 6.2 .env.example（コミット用）

`.env` から実際のキーを除いたテンプレート。新規セットアップ時にコピー → 編集。

## 6.3 config.py

```python
from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    # API キー
    anthropic_api_key: str
    newsapi_key: Optional[str] = None
    jquants_refresh_token: Optional[str] = None
    edinet_api_key: Optional[str] = None

    # moomoo
    moomoo_opend_host: str = "localhost"
    moomoo_opend_port: int = 11111
    moomoo_trading_pwd: str
    moomoo_account_id: str

    # モード
    trading_mode: Literal["paper", "live"] = "paper"
    log_level: str = "INFO"

    # パス
    db_path: Path = Path("~/.trading-agent/db.sqlite").expanduser()
    log_dir: Path = Path("~/.trading-agent/logs").expanduser()
    data_dir: Path = Path("~/.trading-agent/data").expanduser()

    # LLM
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"

    # 通知
    slack_webhook_url: Optional[str] = None
    macos_notification: bool = True

    # 緊急停止
    halt_file: Path = Path("~/.trading-agent/HALT").expanduser()

    class Config:
        env_file = ".env"

@lru_cache()
def get_settings() -> Settings:
    return Settings()
```

## 6.4 動的設定（settings テーブル）

ユーザーが UI から変更できる項目は **settings テーブル** で管理。
.env は「再起動が必要な設定」、settings テーブルは「即反映の設定」。

| 設定 | デフォルト | UI から変更可 |
|---|---|---|
| monthly_budget_jpy | 5000 | ✓ |
| daily_budget_jpy | 500 | ✓ |
| budget_breach_action | "halt" | ✓ |
| morning_batch_time | "05:00" | ✓ |
| auto_sync_interval_min | 5 | ✓ |
| max_position_pct_of_cash | 0.20 | ✓ |
| max_position_pct_of_total | 0.10 | ✓ |
| screening_universe_size | 500 | ✓ |
| theme_keywords | ["AI", "半導体", ...] | ✓ |
| anthropic_api_key | (.env) | × |

## 6.5 緊急停止フロー

```
1. ユーザーが `touch ~/.trading-agent/HALT` を実行
2. スケジューラと API ルーターが起動時 / 各ジョブ前にチェック
3. HALT ファイルがある → 全処理を中止、UI に通知
4. ユーザーが `rm ~/.trading-agent/HALT` で再開
```

緊急停止の判定タイミング：
- 朝バッチ起動時
- 各エージェント実行前
- 各 MCP ツール呼び出し前
- API リクエスト処理開始時

---

# 7. ロギング・監視

## 7.1 構造化ログ（structlog）

全ログを JSON で出力：

```json
{
  "timestamp": "2026-05-22T05:01:23+09:00",
  "level": "INFO",
  "agent": "screening_agent",
  "invocation_id": "abc123",
  "event": "screening_started",
  "universe_size": 500,
  "strategy": "v_shape"
}
```

ログレベル：
- DEBUG：詳細トレース（実装時のみ）
- INFO：エージェント実行、API 呼び出し成功
- WARNING：リトライ、フォールバック、軽い問題
- ERROR：処理失敗、回復不能
- CRITICAL：システム停止級

## 7.2 ログ出力先

- stdout（FastAPI / launchd 経由）
- ファイル：`~/.trading-agent/logs/YYYY-MM-DD.log`（日次ローテーション）
- 重要イベントのみ Slack 通知（webhook 設定時）

## 7.3 監視（health_checks）

5分ごとに各コンポーネントをチェック：

| component | チェック内容 |
|---|---|
| moomoo_opend | heartbeat 応答 |
| anthropic_api | /v1/messages への HEAD リクエスト |
| ollama | /api/tags への GET |
| newsapi | /v2/top-headlines への小規模リクエスト |
| edinet | /api/v1/documents への HEAD |
| disk_space | data_dir の空き容量 |
| memory | プロセスメモリ使用量 |

問題検知時：
- WARNING → ログのみ
- ERROR → UI のヘッダーに表示 + （重大なら）Slack 通知

## 7.4 メトリクス（Phase 2-）

Phase 1 は基本ログのみ。Phase 2- で以下を追加検討：
- Prometheus エクスポート
- エージェント実行時間の集計
- スコア精度の継続監視

---

# 8. パフォーマンスと制限

## 8.1 想定負荷

| 項目 | Phase 1 想定 |
|---|---|
| 同時ユーザー | 1（個人利用） |
| 朝バッチ実行時間 | 10-30分 |
| 価格更新（API） | 5分ごと |
| DB サイズ | 1年で 100-500MB |
| メモリ使用量 | 500MB - 1GB |
| CPU | 朝バッチ時 1-2 コア、平常時 < 5% |

## 8.2 制約

- moomoo OpenD のレート制限（実測必要）
- NewsAPI の月次制限（無料 100req/日）→ EDGAR + RSS で補完
- Anthropic の rate limit（個人キーで十分）

## 8.3 スケール時の対処（Phase 2-）

- 時系列データを DuckDB に分離
- ニュース全文を別ストレージ（S3 互換）
- DB の論理削除（is_archived フラグで月次バッチアーカイブ）

---

# 9. セキュリティ

## 9.1 認証

- Phase 1：localhost バインドのみ、認証なし
- Phase 2-：localhost 以外からのアクセスを拒否、または OAuth 追加

## 9.2 シークレット管理

- API キーは .env に集約、コミット禁止（.gitignore）
- moomoo 取引パスワードは .env に平文（個人利用前提のリスク受容）
- ログには API キーを絶対に出力しない（filter で除去）

## 9.3 入力検証

- 全 API エンドポイントで Pydantic 検証
- SQL injection 対策（SQLModel が自動エスケープ）
- LLM プロンプトへのユーザー入力は sanitize（手動投入時）

---

# 10. Phase 1 → Phase 2 への移行ポイント

C-2 で意識的に「Phase 1 のままで OK、Phase 2 で拡張」とした項目：

| 項目 | Phase 1 | Phase 2- |
|---|---|---|
| DB | SQLite 単独 | SQLite + DuckDB（時系列） |
| 認証 | なし | localhost 制限 + 必要なら OAuth |
| バックアップ | 手動 | 自動（日次 → S3 / NAS） |
| マイグレーション | Alembic 導入のみ | 正式運用 |
| 監視 | health_checks + ログ | Prometheus + Grafana |
| 通知 | Slack webhook | macOS 通知 + メール + Slack |
| broker 発注 | broker_read のみ | broker_order 追加 |
| マルチユーザー | × | × （個人利用なので不要） |

---

# 11. 次のステップ

- **C-3：エージェント設計**（並行進行）
- **C-4：オーケストレーター設計**
- **C-5：運用設計**

C-2 で定義した：
- データモデル（16テーブル）
- MCP ツール仕様（8ツール）
- moomoo 接続管理（BrokerConnection）
- LLM 呼び分け（3段階ルーティング + 予算管理）
- 設定ファイル（.env + config.py + settings テーブル）

これらを基礎として、C-3 で各エージェントが「**どのテーブルを読み、どの MCP ツールを呼び、何を出力するか**」を定義する。
