# Trading Agent — オーケストレーション設計書

最終更新：2026-05-22
ステータス：**STEP C-4 確定版**

このファイルは Trading Agent の **エージェント統合・実行フロー** を定義する。

関連ドキュメント：
- AGENT_SPECS.md：各エージェントの単体仕様（C-3）
- SYSTEM_DESIGN.md：MCPツール、データモデル（C-2）

---

# 📋 朝の確認用：C-4 で踏み込んだ判断

## A. 大きな判断

### A-1. オーケストレーターは独自実装、LangGraph は使わない
- 候補：LangGraph / 自作 / Temporal / Prefect
- **採用：自作（薄い実装）**
- 理由：Phase 1 のフロー複雑度なら独自で十分、LangGraph はオーバーキル
- Phase 2- でフローが複雑化したら LangGraph 検討

### A-2. 朝バッチは「順次 + 部分並列」のハイブリッド
- 純粋並列：API レート制限・コストの問題
- 純粋順次：時間がかかりすぎる
- **採用：DAG（有向非巡回グラフ）で依存関係を表現、並列可能な部分は並列実行**

### A-3. エラー処理は「Graceful Degradation」
- 1つのエージェント失敗で全停止しない
- 失敗時もダッシュボードは表示（前日のデータ + 警告）
- 重要度の高いエラーのみユーザー通知

### A-4. リトライ戦略
- ネットワークエラー：3回（指数バックオフ）
- LLM のレートリミット：レスポンスヘッダーに従う
- moomoo 切断：5回再接続試行
- それ以外：1回リトライ → 失敗ならスキップ

### A-5. スケジューリング
- 朝バッチ：JST 5:00 開始（市場が動く前）
- 価格更新：5分ごと（市場時間中のみ）
- 健康チェック：5分ごと（常時）
- 日次集計：JST 6:30（米国市場引け後）

## B. Phase 1 の朝バッチの全体時間

想定実行時間（実測前の見積もり）：

| フェーズ | 想定時間 |
|---|---|
| topics-collector | 3-5分 |
| screening-agent | 2-3分 |
| market-analyst × N（並列、10銘柄） | 5-8分 |
| sell-recommender | 1-2分 |
| portfolio-builder（review） | 30秒 |
| 集計・通知 | 30秒 |
| **合計** | **15-20分** |

実測値で 30分超なら最適化が必要。

## C. 大きな未決事項

### C-1. 価格更新を「定期」か「オンデマンド」か
- 定期：5分ごとに自動更新（コスト発生）
- オンデマンド：ユーザーがボタン押した時のみ（リアル感は薄い）
- **採用：ハイブリッド** — 朝バッチ後は1時間ごと、市場時間中は5分ごと、ユーザー操作時は即時

### C-2. moomoo 切断時の挙動
- **採用：degraded モードで継続**
  - 価格は cache から表示（古いことを明示）
  - 売買レコメンドは一旦停止（実価格不明のため）
  - 復旧したら自動再開

---

# 1. システム全体のイベントループ

## 1.1 構成

```
┌────────────────────────────────────────────────────────┐
│ FastAPI Server                                          │
│                                                         │
│ ┌─────────────────┐  ┌─────────────────┐              │
│ │ APScheduler     │  │ Event Loop      │              │
│ │ - morning_batch │  │ - health_check  │              │
│ │ - daily_snap    │  │ - moomoo_sync   │              │
│ │ - hourly_price  │  │                 │              │
│ └─────────────────┘  └─────────────────┘              │
│                                                         │
│ ┌─────────────────┐  ┌─────────────────┐              │
│ │ API Routes      │  │ MCP Host        │              │
│ │ - dashboard     │  │ - tool registry │              │
│ │ - actions       │  │ - call routing  │              │
│ └─────────────────┘  └─────────────────┘              │
│                                                         │
│ ┌─────────────────────────────────────────────────────┐│
│ │ Orchestrator                                         ││
│ │ - DAG executor                                       ││
│ │ - state manager                                      ││
│ │ - error handler                                      ││
│ └─────────────────────────────────────────────────────┘│
└────────────────────────────────────────────────────────┘
```

## 1.2 スケジュールされるジョブ

| ジョブ | スケジュール | 内容 |
|---|---|---|
| morning_batch | JST 5:00 daily | 朝バッチ実行 |
| daily_snapshot | JST 6:30 daily | portfolio_snapshots 保存 |
| price_update_market | 毎時 5分（市場時間） | 価格を 5分ごと更新 |
| price_update_offmarket | 毎時 0分（市場外） | 価格を 1時間ごと更新 |
| moomoo_sync | 5分ごと | portfolio との同期 |
| health_check | 5分ごと | 全コンポーネント健康確認 |
| weekly_universe_update | 月曜 JST 3:00 | universe テーブル更新 |
| monthly_archive | 月初 JST 2:00 | 古いデータをアーカイブ |

---

# 2. 朝バッチ DAG

## 2.1 全体フロー

```
                    [START]
                       │
                       ▼
                ┌─ pre_check ─┐
                │             │
                │ ・HALT?     │
                │ ・予算?     │
                │ ・接続?     │
                └──────┬──────┘
                       │
              ┌────────┴────────┐
              ▼                 ▼
       topics-collector    universe_refresh
              │            (週次のみ)
              │                 │
              └────────┬────────┘
                       ▼
              screening-agent
                       │
                       ▼
              ┌──────────────────┐
              │ market-analyst × N│ ← 候補数だけ並列
              │ (並列、最大5並列) │
              └──────────────────┘
                       │
                       ▼
              sell-recommender
              (active な保有銘柄ごと)
                       │
                       ▼
          portfolio-builder (review)
                       │
                       ▼
              link_topics_decisions
              (topics ↔ decisions の相互更新)
                       │
                       ▼
                summary_generator
                  (UI用サマリー)
                       │
                       ▼
              health_report_save
                       │
                       ▼
                  notification
              (Slack / macOS 通知)
                       │
                       ▼
                    [END]
```

## 2.2 各ステップの詳細

### pre_check

```python
async def pre_check() -> tuple[bool, str]:
    # 1. HALT ファイル
    if halt_file_exists():
        return False, "HALT file present"

    # 2. 月次予算チェック
    month_spent = await get_month_cost()
    if month_spent >= settings.monthly_budget_jpy:
        return False, f"Monthly budget exceeded: ¥{month_spent}"

    # 3. moomoo OpenD 接続
    if not await broker.is_connected():
        await broker.reconnect()
        if not await broker.is_connected():
            log.warning("moomoo disconnected, will run in degraded mode")
            # 続行するが、価格データの鮮度に注意

    # 4. Anthropic API 接続
    if not await anthropic.health_check():
        return False, "Anthropic API unreachable"

    # 5. Ollama 接続（必須ではない）
    if not await ollama.health_check():
        log.warning("Ollama unreachable, will use Hot Path only")

    return True, "OK"
```

失敗時 → 朝バッチ中止、ユーザーに通知。

### topics-collector

依存：なし
並列度：1（内部で並列化）
タイムアウト：10分

成功条件：少なくとも 5件のトピックスが収集される（0件は失敗扱い、ただし継続）

### universe_refresh（週次のみ）

依存：なし
並列度：1
タイムアウト：5分
スケジュール：月曜のみ実行

### screening-agent

依存：topics-collector の完了（推奨）、universe_refresh の完了（実行された場合）
並列度：1（内部で並列化）
タイムアウト：5分

成功条件：候補が 5件以上

### market-analyst × N

依存：screening-agent
並列度：**5**（同時実行）
タイムアウト：銘柄あたり 2分

- 候補が10銘柄なら、最大5並列で2バッチに分けて実行
- 1銘柄の失敗は他に影響させない（独立並列）
- 失敗銘柄はリトライせず次のバッチへ

成功条件：少なくとも 50% の銘柄が成功

### sell-recommender

依存：market-analyst（保有銘柄が candidates と被る可能性、最新の market_data 必要）
並列度：1（内部で保有銘柄を順次処理、保有数は通常 5-10件）
タイムアウト：5分

### portfolio-builder（review）

依存：sell-recommender
並列度：1
タイムアウト：1分

軽量な処理。エラーで止まっても致命的でない。

### link_topics_decisions

依存：全エージェント完了
並列度：1
タイムアウト：1分

topics.affected_tickers と decisions.ticker を結びつける後処理。

### summary_generator

依存：link_topics_decisions
並列度：1
タイムアウト：30秒

ダッシュボード用のサマリーデータを生成。「今日のサマリー」セクション用。

### notification

依存：summary_generator
並列度：1
タイムアウト：30秒

設定に応じて：
- macOS 通知（即時）
- Slack webhook（設定時）

通知内容例：
```
🌅 朝バッチ完了 (5:18)
売り推奨: 2件 (TSLA損切り, 7203利確)
買い推奨: 2件 (NVDA, 6920)
警告: 1件 (MSFT 注視)
コスト: ¥45 (累計 ¥2,225 / ¥5,000)
```

## 2.3 DAG 実装

```python
class DAGNode:
    name: str
    func: Callable
    depends_on: List[str]
    timeout_s: int
    parallel: bool = False
    parallelism: int = 1  # parallel=True の時の最大並列数

class DAGExecutor:
    def __init__(self, nodes: List[DAGNode]):
        self.nodes = {n.name: n for n in nodes}
        self.results = {}

    async def execute(self) -> Dict[str, Any]:
        executed = set()
        while len(executed) < len(self.nodes):
            # 実行可能なノード（依存関係が全て満たされている）を見つける
            ready = [
                n for name, n in self.nodes.items()
                if name not in executed
                and all(dep in executed for dep in n.depends_on)
            ]

            if not ready:
                raise OrchestrationError("Circular dependency or stuck")

            # 並列実行
            results = await asyncio.gather(*[
                self._run_node(n) for n in ready
            ], return_exceptions=True)

            for n, r in zip(ready, results):
                self.results[n.name] = r
                executed.add(n.name)

        return self.results

    async def _run_node(self, node: DAGNode):
        try:
            return await asyncio.wait_for(node.func(), timeout=node.timeout_s)
        except asyncio.TimeoutError:
            log.error(f"{node.name} timed out")
            return None
        except Exception as e:
            log.exception(f"{node.name} failed")
            return None
```

## 2.4 朝バッチの DAG 定義（コード例）

```python
morning_batch_dag = [
    DAGNode(name="pre_check", func=pre_check, depends_on=[], timeout_s=30),
    DAGNode(name="topics_collector", func=run_topics_collector,
            depends_on=["pre_check"], timeout_s=600),
    DAGNode(name="universe_refresh", func=run_universe_refresh,
            depends_on=["pre_check"], timeout_s=300),  # 月曜のみ実行されるが、DAGには毎日入れる
    DAGNode(name="screening", func=run_screening,
            depends_on=["topics_collector"], timeout_s=300),
    DAGNode(name="market_analyst", func=run_market_analyst_batch,
            depends_on=["screening"], timeout_s=600, parallel=True, parallelism=5),
    DAGNode(name="sell_recommender", func=run_sell_recommender,
            depends_on=["market_analyst"], timeout_s=300),
    DAGNode(name="portfolio_builder", func=run_portfolio_builder,
            depends_on=["sell_recommender"], timeout_s=60),
    DAGNode(name="link_topics", func=link_topics_decisions,
            depends_on=["portfolio_builder"], timeout_s=60),
    DAGNode(name="summary", func=generate_summary,
            depends_on=["link_topics"], timeout_s=30),
    DAGNode(name="notify", func=send_notification,
            depends_on=["summary"], timeout_s=30),
]
```

---

# 3. エージェント間のデータ受け渡し

## 3.1 原則：DB 経由

エージェント間のメッセージパッシングはしない。**全て DB を介して受け渡し**。

メリット：
- 状態が永続化される（途中で失敗しても復旧可能）
- デバッグが容易（DB を見ればわかる）
- エージェントを単独でテスト可能

デメリット：
- DB アクセスがボトルネックになり得る → SQLite なら問題なし（Phase 1 想定）

## 3.2 受け渡しデータの整理

| 渡す側 | 受ける側 | 媒介テーブル | 内容 |
|---|---|---|---|
| topics-collector | market-analyst, sell-recommender | topics | 最新ニュース |
| topics-collector | manual-input-analyst | topics | 過去の関連トピックス |
| screening-agent | market-analyst | screening_results | 分析対象銘柄リスト |
| market-analyst | portfolio-builder | buy_signals | 買いレコメンド |
| market-analyst | UI (dashboard) | buy_signals | 表示用 |
| sell-recommender | UI (dashboard) | sell_signals, scenarios | 表示用 |
| 全エージェント | analysis_logs | analysis_logs | 実行履歴 |
| 全 LLM 呼び出し | cost_logs | cost_logs | コスト記録 |

## 3.3 invocation_id によるトレース

```
朝バッチ全体に1つの invocation_id（例: "morning_2026-05-22"）
各エージェントは固有の sub_invocation_id を持つ

logs:
- {invocation: "morning_2026-05-22", agent: "topics_collector", ...}
- {invocation: "morning_2026-05-22", agent: "screening", parent: "morning_2026-05-22"}
- {invocation: "morning_2026-05-22", agent: "market_analyst:NVDA", parent: "morning_2026-05-22"}
- {invocation: "morning_2026-05-22", agent: "market_analyst:7203", parent: "morning_2026-05-22"}
```

これで「朝バッチ全体のコスト」「特定銘柄の分析だけ抽出」など多角的に集計可能。

---

# 4. エラー処理戦略

## 4.1 エラーの分類

| カテゴリ | 例 | 対応 |
|---|---|---|
| **致命的** | DB 破損、HALT、起動失敗 | 即停止、通知 |
| **重大** | moomoo 切断、Anthropic 全停止 | degraded モード、通知 |
| **エージェント失敗** | screening 全滅、市場データ取得失敗 | 該当エージェントスキップ、警告 |
| **個別失敗** | 1銘柄の分析失敗 | スキップして次へ |
| **軽微** | リトライで回復 | ログのみ |

## 4.2 Graceful Degradation の具体例

### moomoo 切断時

```python
async def get_current_prices(tickers):
    if await broker.is_connected():
        return await broker.get_quotes(tickers)
    else:
        # フォールバック1: メモリキャッシュ
        cached = memory_cache.get_quotes(tickers)
        if cached:
            return cached + degraded_warning("memory_cache")

        # フォールバック2: DB キャッシュ
        db_cached = await db.get_market_data_cache(tickers)
        if db_cached:
            return db_cached + degraded_warning("db_cache")

        # フォールバック3: yfinance
        yf_data = await yfinance.get_quotes(tickers)
        return yf_data + degraded_warning("yfinance_fallback")
```

UI 表示時に degraded warning を表示：
- 「価格データが古い可能性」のバナー
- 最終更新時刻を強調
- 売り買いレコメンドの実行を一時停止

### 一部エージェント失敗時

```python
# market_analyst × N で 10銘柄中 3 銘柄が失敗
# → 残り 7 銘柄分のレコメンドを buy_signals に保存
# → UI に「3 銘柄の分析に失敗しました」警告
# → 翌朝のバッチで再試行
```

### topics-collector が NewsAPI に失敗

```python
# NewsAPI 失敗 → RSS のみで継続
# → 表示件数は減るが、ダッシュボードは正常表示
# → 「ニュース取得が一部失敗」warning
```

## 4.3 エラー通知の階層

```
致命的: macOS 通知（音付き）+ Slack（@here）
重大:   macOS 通知 + Slack（通常）
警告:   UI 表示のみ
情報:   ログのみ
```

---

# 5. 価格更新フロー

## 5.1 価格更新のトリガー

| トリガー | 頻度 | スコープ |
|---|---|---|
| **市場時間中の定期更新** | 5分ごと | active な portfolio 全銘柄 + 候補（buy_signals） |
| **市場外の定期更新** | 1時間ごと | 同上 |
| **ユーザーの価格更新ボタン** | 即時 | 同上 |
| **手動投入時の参照** | 都度 | 関連銘柄のみ |

## 5.2 価格更新の流れ

```python
async def update_prices(tickers: List[str], force: bool = False) -> dict:
    # キャッシュ判定（force=True ならスキップ）
    if not force:
        recent = memory_cache.get_recent(tickers, max_age_s=300)
        if len(recent) == len(tickers):
            return recent

    # moomoo から一括取得
    quotes = await broker.get_quotes(tickers)

    # メモリキャッシュ更新
    memory_cache.update(quotes)

    # DB キャッシュ更新
    await db.upsert_market_data_cache(quotes)

    # portfolio の含み損益再計算
    for ticker, quote in quotes.items():
        if ticker in active_portfolio:
            await recalc_position_pnl(ticker, quote.price)

    return quotes
```

## 5.3 ユーザー価格更新ボタンの挙動

```
1. POST /api/refresh_prices を呼ぶ
2. orchestrator が update_prices(force=True) を実行
3. 完了後、ダッシュボードに最新データを返す
4. WebSocket で他のタブにも通知（Phase 2-）
```

---

# 6. moomoo 同期フロー

## 6.1 5分ごとの自動同期

```python
async def moomoo_sync():
    if not await broker.is_connected():
        log.warning("moomoo not connected, sync skipped")
        return

    # 1. 保有銘柄取得
    positions = await broker.get_positions()

    # 2. portfolio テーブルと照合
    db_portfolio = await db.get_active_portfolio()

    # 3. 差分検知
    new_positions = positions - db_portfolio
    closed_positions = db_portfolio - positions
    qty_changed = find_qty_changes(positions, db_portfolio)

    # 4. DB 更新
    for pos in new_positions:
        await db.insert_portfolio(pos)
        # 新しい保有 → 通知
        await notify(f"新規保有検知: {pos.ticker} {pos.qty}株")

    for pos in closed_positions:
        await db.mark_portfolio_closed(pos)
        # 売却 → decisions に評価結果を記録
        await record_decision_outcome(pos)
        await notify(f"売却検知: {pos.ticker}")

    for change in qty_changed:
        await db.update_portfolio_qty(change)

    # 5. 残高同期
    balance = await broker.get_balance()
    await db.update_cash(balance)

    # 6. 約定履歴同期
    transactions = await broker.get_transactions(since=last_sync_time)
    await db.append_transactions(transactions)
```

## 6.2 売却完了の検知

ユーザーが moomoo で売却 → moomoo_sync で検知 → 自動処理：

```python
async def on_position_closed(closed_pos):
    # 1. portfolio.status = "closed"
    # 2. 対応する decision を見つける
    decision = await db.find_decision_by_position(closed_pos)

    # 3. 結果を評価
    actual_return = (close_price - buy_price) / buy_price
    expected_return = decision.expected_return

    if actual_return >= expected_return * 0.7:
        hit_or_miss = "hit"
    elif actual_return <= -0.05:
        hit_or_miss = "miss"
    else:
        hit_or_miss = "neutral"

    # 4. decisions テーブル更新
    await db.update_decision_outcome(
        decision.id,
        actual_return=actual_return,
        hit_or_miss=hit_or_miss,
        evaluated_at=now(),
    )

    # 5. ユーザー通知
    await notify(f"{closed_pos.ticker} 売却完了: {actual_return:+.1%} ({hit_or_miss})")
```

---

# 7. 健康チェック

## 7.1 チェック対象

```python
COMPONENTS = [
    {"name": "moomoo_opend", "checker": check_moomoo, "critical": True},
    {"name": "anthropic_api", "checker": check_anthropic, "critical": True},
    {"name": "ollama", "checker": check_ollama, "critical": False},
    {"name": "newsapi", "checker": check_newsapi, "critical": False},
    {"name": "edinet", "checker": check_edinet, "critical": False},
    {"name": "tdnet_rss", "checker": check_tdnet, "critical": False},
    {"name": "disk_space", "checker": check_disk, "critical": True},
    {"name": "memory", "checker": check_memory, "critical": False},
    {"name": "db_size", "checker": check_db_size, "critical": False},
]
```

## 7.2 チェックの実行

```python
async def run_health_checks():
    results = []
    for comp in COMPONENTS:
        try:
            start = time.time()
            status = await comp["checker"]()  # "ok" / "degraded" / "down"
            duration = (time.time() - start) * 1000

            await db.insert_health_check(
                component=comp["name"],
                status=status,
                response_time_ms=int(duration),
            )

            # critical な component が down なら通知
            if comp["critical"] and status == "down":
                await notify_critical(comp["name"])

        except Exception as e:
            await db.insert_health_check(
                component=comp["name"],
                status="down",
                error_msg=str(e),
            )
```

## 7.3 UI への反映

サイドバーの "Live" インジケータの色：
- 緑：全 critical が ok
- 黄：一部 non-critical が degraded
- 赤：critical が down

---

# 8. リトライ戦略

## 8.1 共通リトライポリシー

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((NetworkError, TimeoutError)),
)
async def call_with_retry(func, *args, **kwargs):
    return await func(*args, **kwargs)
```

## 8.2 リトライ対象 / 非対象

### リトライする
- ネットワークエラー（接続失敗、タイムアウト）
- LLM のレートリミット（Retry-After ヘッダーに従う）
- moomoo の一時的な切断
- HTTP 503 / 502

### リトライしない
- 認証失敗（401, 403）
- バリデーションエラー（400）
- HALT ファイル検知
- DB の制約違反

## 8.3 リトライ後の失敗処理

3回リトライしても失敗 → エラーログ + フォールバック（キャッシュ等） + 警告通知

---

# 9. 状態管理

## 9.1 朝バッチの実行状態

```python
class BatchState(SQLModel, table=True):
    __tablename__ = "batch_states"

    invocation_id: str = Field(primary_key=True)
    batch_type: str  # "morning" / "manual_refresh" / 等
    status: str  # "running" / "success" / "failed" / "partial"
    started_at: datetime
    ended_at: Optional[datetime]
    node_status: dict = Field(sa_column=Column(JSON))
    # 例: {"topics_collector": "success", "screening": "success", "market_analyst": "partial", ...}
    summary: str
    errors: List[dict] = Field(sa_column=Column(JSON))
```

これにより：
- 「今日の朝バッチは成功したか」を即座に確認できる
- 失敗時にどのステップから再開すべきか分かる
- UI でバッチの進行状況を表示できる

## 9.2 再実行サポート（Phase 2-）

```python
async def rerun_from_step(invocation_id: str, step_name: str):
    """指定ステップから再実行"""
    # 前回の DAG の状態を取得
    prev_state = await db.get_batch_state(invocation_id)

    # 該当ステップ以降を再実行
    # 前段のデータは prev_state から再利用
```

Phase 1 では実装しない（朝バッチ全体を再実行で対応）。

---

# 10. 手動トリガー

## 10.1 ユーザーが実行できる手動アクション

| アクション | エンドポイント | 内容 |
|---|---|---|
| **価格更新** | POST /api/refresh_prices | 全銘柄の価格を即時更新 |
| **朝バッチの手動実行** | POST /api/run_morning_batch | DAG を即時実行 |
| **特定エージェントの再実行** | POST /api/run_agent/{agent_name} | 単独エージェント実行 |
| **手動投入** | POST /api/manual_input | manual-input-analyst を呼ぶ |
| **moomoo 同期** | POST /api/sync_moomoo | 即時同期 |
| **緊急停止** | POST /api/halt | HALT ファイル作成 |
| **再開** | POST /api/resume | HALT ファイル削除 |

## 10.2 レート制限

```python
# 手動アクションのレート制限
ENDPOINT_LIMITS = {
    "/api/refresh_prices": "10/minute",
    "/api/run_morning_batch": "5/hour",
    "/api/run_agent/*": "20/hour",
    "/api/manual_input": "30/hour",  # コスト発生
    "/api/sync_moomoo": "20/minute",
}
```

予算管理と組み合わせて、過剰実行を防ぐ。

---

# 11. ログとモニタリング

## 11.1 バッチ全体のログ

```json
{
  "invocation_id": "morning_2026-05-22",
  "batch_type": "morning",
  "started_at": "2026-05-22T05:00:00+09:00",
  "ended_at": "2026-05-22T05:18:23+09:00",
  "duration_s": 1103,
  "status": "success",
  "nodes": {
    "topics_collector": {"status": "success", "duration_s": 245, "topics_added": 12},
    "screening": {"status": "success", "duration_s": 132, "candidates": 18},
    "market_analyst": {"status": "partial", "duration_s": 412, "succeeded": 8, "failed": 2},
    "sell_recommender": {"status": "success", "duration_s": 78, "signals": 2},
    "portfolio_builder": {"status": "success", "duration_s": 12},
    "summary": {"status": "success", "duration_s": 5},
    "notify": {"status": "success", "duration_s": 2}
  },
  "total_llm_cost_jpy": 45.3,
  "total_llm_calls": 23,
  "warnings": ["2 banks failed: AMD, ASML (data unavailable)"]
}
```

これを毎朝 batch_states テーブルに記録 + ログファイルに出力。

## 11.2 メトリクス（Phase 2-）

- 平均朝バッチ時間（週次）
- エージェント別の成功率
- LLM コストの日次推移
- スコア精度（of decisions, evaluation 日が経過したもの）

---

# 12. Phase 1 → Phase 2 への発展

| 項目 | Phase 1 | Phase 2- |
|---|---|---|
| DAG フレームワーク | 自作（薄い） | LangGraph に移行検討 |
| 再実行 | 全体再実行のみ | 部分再実行サポート |
| 並列度 | 固定 5 | 動的調整 |
| エージェント追加 | 6個 | position-monitor, strategy-coordinator |
| メトリクス | ログ + バッチ状態 | Prometheus |
| 通知 | macOS + Slack | + メール |
| 状態管理 | DB のみ | Redis 等のキャッシュ追加検討 |

---

# 13. 次のステップ

C-4 で定義した：
- 朝バッチの DAG（依存関係 + 並列度）
- エラー処理戦略（Graceful Degradation）
- 価格更新フロー
- moomoo 同期フロー
- 健康チェック
- 状態管理
- 手動トリガー

最後に **C-5：運用設計**で：
- セットアップ手順（人間 / Claude Code）
- launchd 設定
- バックアップ
- アップグレード戦略
- トラブルシューティング

を確定する。
