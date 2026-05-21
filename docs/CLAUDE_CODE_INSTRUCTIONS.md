# Trading Agent — Claude Code 実装指示書

最終更新：2026-05-22
ステータス：**STEP D：実装着手指示書（マスター）**

このファイルは Claude Code が **このプロジェクトを実装するために最初に読む** ドキュメント。

---

# 0. このプロジェクトは何か

## 0.1 一文要約

**moomoo OpenAPI を介して米国株・日本株の中期投資を AI マルチエージェントが支援する、ローカル完結型の個人向けトレードアシスタント。**

## 0.2 表のテーマ
- 10万円の運用元本を中期投資（数ヶ月〜1年）で育てる
- 朝5分のダッシュボード確認で「今日何をすべきか」が分かる
- 売り推奨（利確・損切り）と買い推奨を主役にする

## 0.3 裏のテーマ
- AI エージェント技術の体得（実装プロセスで自然に習得）
- ローカル完結、個人利用、macOS 専用

## 0.4 ユーザーの背景
- クリエイティブディレクター（実装経験あり、非金融エンジニア）
- FX 経験あり（感情に振られた経験）
- 株式投資はこれから本格化
- 学習は副産物、実用ツールを毎日触る中で深まることを期待

---

# 1. 実装の原則（Claude Code への重要指示）

## 1.1 守るべき5原則

### 原則1：設計書を信じる、勝手に解釈しない
設計書（STEP_A〜C）は **27時間以上の議論で確定** したもの。
「もっと良い方法があるかも」と勝手に変えない。
変更したいなら、必ずユーザーに確認してから動く。

### 原則2：論理は設計書、コードはあなたの責任
設計書には算出式・判定ロジック・データ構造が定義されている。
**具体的なコード実装はあなたの判断**。Pythonic に、テスタブルに、メンテナブルに書くこと。
設計書で「【Claude Code 判断】」とマークされた部分は完全にあなたの裁量。

### 原則3：Phase 1 のスコープを超えない
Phase 2- の機能は **絶対に先回りで実装しない**。
- broker_order MCP は実装しない（Phase 2）
- position-monitor エージェントは実装しない（Phase 2）
- 高度なポートフォリオ最適化は実装しない（Phase 3）

「ついでに」「将来必要だから」で機能を増やすな。**Phase 1 で動くことが最優先**。

### 原則4：仮設定値は必ず仮として実装
設計書の「【たたき台】」とマークされた数値は **settings テーブル or 設定ファイルで変更可能** にすること。
ハードコードしない。Phase 1 で4週間運用後にユーザーが調整する前提。

### 原則5：エラー時も止まらない設計
Graceful Degradation（ORCHESTRATION.md セクション4）を徹底。
- 1つのエージェント失敗で全停止しない
- moomoo 切断時は degraded モードで継続
- API 失敗時はキャッシュで継続
- ユーザーには「どこが動いて、どこが止まったか」を明示

## 1.2 やってはいけないこと

- ❌ Phase 2- の機能を先回り実装
- ❌ 設計書にない MCP ツール・エージェントを追加
- ❌ 「もっとシンプルだから」で構造を変える
- ❌ ハードコードされた数値（特に閾値・重み）
- ❌ プロンプトに勝手な工夫を追加（A/Bテスト的な追加は OK、設計書の意図を変えるのは NG）
- ❌ ai-hedge-fund 等の特定 OSS を「ベース」として丸ごと採用
- ❌ X (Twitter) 公式 API の直接利用
- ❌ スクレイピングをコアロジックに組み込む
- ❌ マーコウィッツ等の高度ポートフォリオ最適化（Phase 1 では不要）

## 1.3 困った時の判断ルール

判断に迷ったら、以下の優先順位で：

1. **設計書（STEP_A〜C）に書いてあるか確認**
2. **書いてあれば、それに従う**
3. **書いてなければ、Phase 1 スコープか確認**
4. **Phase 1 スコープなら、簡潔に実装**
5. **Phase 2- なら、TODO コメントで明示して実装しない**
6. **どうしても判断できなければ、ユーザーに確認**

---

# 2. 設計書の読み方

## 2.1 ドキュメント階層

```
STEP A（要件定義）
├── STEP_A_FINAL.md          ← まずこれを読む
├── architecture.html         ← システム全体像（図中心）

STEP B（UI設計）
├── STEP_B_FINAL.md          ← ダッシュボードの設計思想
├── dashboard.html            ← UI の完成形（参考実装）

STEP C（技術設計）← 実装時に最も参照
├── PANEL_SPECS.md           ← C-1：パネル別ロジック
├── SYSTEM_DESIGN.md         ← C-2：データモデル・MCP・接続
├── AGENT_SPECS.md           ← C-3：エージェント設計
├── ORCHESTRATION.md         ← C-4：実行フロー
├── OPERATIONS.md            ← C-5：運用

STEP D（このフェーズ）
├── CLAUDE_CODE_INSTRUCTIONS.md  ← このファイル（マスター）
└── IMPLEMENTATION_PHASES.md     ← フェーズ別タスクリスト
```

## 2.2 読む順番（推奨）

実装に着手する前に、以下の順で読む：

1. **このファイル（CLAUDE_CODE_INSTRUCTIONS.md）** — 全体方針
2. **STEP_A_FINAL.md** — 何を作るか、なぜ作るか
3. **dashboard.html を実際に開いて触る** — UI の完成形を理解
4. **architecture.html** — システム全体像
5. **SYSTEM_DESIGN.md** — データモデル、MCP、接続管理（実装の基盤）
6. **PANEL_SPECS.md** — UI の各パネルが何を計算するか
7. **AGENT_SPECS.md** — エージェントの I/O とロジック
8. **ORCHESTRATION.md** — エージェントの統合方法
9. **OPERATIONS.md** — 運用フロー、launchd
10. **IMPLEMENTATION_PHASES.md** — フェーズ別タスク

## 2.3 設計書の主従関係

矛盾した場合の優先順位：

```
高 ← STEP_A_FINAL.md > SYSTEM_DESIGN.md > AGENT_SPECS.md
                                              ↓
                                        PANEL_SPECS.md
                                              ↓
                                        ORCHESTRATION.md
                                              ↓
                                        OPERATIONS.md
                                              ↓
低 ← dashboard.html （UI 参考実装、ロジック実装の正典ではない）
```

矛盾を見つけたら、ユーザーに報告（黙って判断しない）。

---

# 3. 実装着手の流れ

## 3.1 全体ロードマップ

```
Phase 1.0：環境構築          ← まずここ
Phase 1.1：MCP ツール基盤
Phase 1.2：moomoo 連携
Phase 1.3：エージェント基盤
Phase 1.4：エージェント実装
Phase 1.5：オーケストレーター
Phase 1.6：UI（Next.js）
Phase 1.7：常駐化と運用
Phase 1.8：4週間運用 + パラメータ調整
```

詳細は **IMPLEMENTATION_PHASES.md** を参照。

## 3.2 各フェーズで守ること

- **完了基準を満たしてから次のフェーズに進む**
- **各フェーズで動作確認 + ユーザーへの報告**
- **テストコードを書く**（特にスコア算出・判定ロジック）
- **コミットは小さく、メッセージは明確に**

## 3.3 セッション単位の作業粒度

1 セッション = 1 つの明確なタスクを完結させる：

例：
- ✅ "MCP ツール market_data を実装して単体テスト"
- ✅ "screening-agent を実装、テスト含む"
- ✅ "保有銘柄カードの UI を実装"
- ❌ "MCP ツール全部実装"（粒度が大きすぎる）
- ❌ "エージェント基盤と最初のエージェント"（複合的）

## 3.4 各タスク開始時の確認

セッション開始時、Claude Code は以下を確認：

1. **どのフェーズの、どのタスクか**
2. **関連する設計書セクションは読んだか**
3. **完了基準は何か**
4. **既存のコードベースとの整合性は取れているか**

---

# 4. プロジェクト構造（再掲）

詳細は SYSTEM_DESIGN.md セクション 1.2 参照。

```
~/Projects/trading-agent/
├── pyproject.toml
├── .env
├── .env.example
├── README.md
├── alembic.ini
├── alembic/
│   └── versions/
│
├── trading_agent/          # メインパッケージ
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   │
│   ├── models/             # SQLModel
│   ├── mcp_tools/          # MCP ツール
│   ├── agents/             # エージェント
│   ├── orchestrator/       # オーケストレーター
│   ├── brokers/            # ブローカー接続
│   ├── llm/                # LLM 呼び分け
│   ├── api/                # FastAPI ルーター
│   └── utils/
│
├── ui/                     # Next.js
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
├── scripts/
│   ├── init_db.py
│   ├── setup_launchd.py
│   ├── backup.py
│   └── health_check.py
│
└── docs/                   # 設計書（このプロジェクト）
    ├── STEP_A_FINAL.md
    ├── STEP_B_FINAL.md
    ├── PANEL_SPECS.md
    ├── SYSTEM_DESIGN.md
    ├── AGENT_SPECS.md
    ├── ORCHESTRATION.md
    ├── OPERATIONS.md
    ├── CLAUDE_CODE_INSTRUCTIONS.md
    └── IMPLEMENTATION_PHASES.md
```

---

# 5. 技術スタック確定版

| 領域 | 採用 | バージョン |
|---|---|---|
| 言語（バックエンド） | Python | 3.12+ |
| 言語（フロントエンド） | TypeScript | 5.x |
| パッケージ管理 | uv | 最新 |
| Web フレームワーク | FastAPI | 0.115+ |
| ASGI サーバー | uvicorn | 最新 |
| ORM | SQLModel | 0.0.20+ |
| DB | SQLite | 3.x（macOS 同梱）|
| マイグレーション | Alembic | 最新 |
| エージェント | LangChain | 0.3+ |
| LLM クライアント | langchain-anthropic, langchain-ollama | 最新 |
| LLM（Hot Path） | Claude Sonnet 4.6 | API |
| LLM（Critical） | Claude Opus 4.7 | API |
| LLM（Cold Path） | Ollama (llama3.1:8b) | ローカル |
| スケジューラ | APScheduler | 最新 |
| HTTP クライアント | httpx | 最新 |
| ロギング | structlog | 最新 |
| リトライ | tenacity | 最新 |
| バリデーション | Pydantic | 2.x |
| テクニカル指標 | TA-Lib | 最新 |
| RSS パーサー | feedparser | 最新 |
| 価格データ（補助） | yfinance, J-Quants | 最新 |
| ブローカー | moomoo-api | 公式 SDK |
| UI フレームワーク | Next.js | 14+ |
| UI 配信 | 静的ビルド | (next build && next export) |
| プロセス管理 | launchd | macOS 標準 |

**禁止：**
- 他のフレームワーク（Django, Flask, FastAPI 以外）
- 別の ORM（SQLAlchemy 単体は NG、SQLModel 経由のみ）
- Docker（Phase 1 では使わない）
- Redis（Phase 1 では使わない、メモリキャッシュで対応）

---

# 6. コーディング規約

## 6.1 全般

- **型ヒント必須**（mypy --strict 通る程度）
- **docstring 必須**（Google スタイル）
- **テスト必須**（純粋関数は 100% カバレッジ目標）
- **import 順序**（標準ライブラリ → サードパーティ → 自プロジェクト）

## 6.2 Python

- **black** でフォーマット（行長 100）
- **ruff** で lint
- **async** を基本（同期処理はブロッキングする時のみ）
- **Pydantic v2** で全データ構造を定義（dict は最小限）

## 6.3 SQL

- **SQLModel** 経由のみ
- raw SQL を書くなら **textwrap.dedent + 名前付きパラメータ**
- インデックスは SYSTEM_DESIGN.md セクション 2.4 を参照

## 6.4 命名

| 種類 | 規約 | 例 |
|---|---|---|
| クラス | PascalCase | `BrokerConnection` |
| 関数・変数 | snake_case | `calculate_score` |
| 定数 | UPPER_SNAKE | `MAX_RETRY_ATTEMPTS` |
| ファイル | snake_case.py | `market_data.py` |
| エージェント名 | snake_case | `screening_agent` |
| MCP ツール名 | snake_case | `market_data` |
| DB テーブル | snake_case 複数形 | `decisions` |
| DB カラム | snake_case | `current_price` |
| API エンドポイント | kebab-case | `/api/refresh-prices` |

## 6.5 エラーハンドリング

```python
# ❌ 悪い例
try:
    result = await api.call()
except Exception:
    pass

# ✅ 良い例
try:
    result = await api.call()
except NetworkError as e:
    log.warning("API call failed, falling back to cache", error=str(e))
    result = await cache.get()
except AuthError:
    raise  # 認証エラーは上位に伝播、止めるべき
```

## 6.6 ログ

```python
# ✅ structlog で構造化ログ
log.info(
    "agent_execution_started",
    agent="screening_agent",
    invocation_id=invocation_id,
    universe_size=500,
)

# ❌ プレーンテキストログは禁止
print("screening started")  # NG
logging.info("screening started")  # NG
```

## 6.7 環境変数・設定

```python
# ✅ config.py を経由
from trading_agent.config import get_settings
settings = get_settings()
api_key = settings.anthropic_api_key

# ❌ 直接読まない
import os
api_key = os.environ["ANTHROPIC_API_KEY"]  # NG
```

---

# 7. テスト戦略

## 7.1 テストの分類

| 種類 | 対象 | 必須度 |
|---|---|---|
| **単体テスト** | 純粋関数（スコア算出、判定ロジック） | **必須** |
| **統合テスト** | MCP ツール、エージェント | 推奨 |
| **エンドツーエンド** | 朝バッチ全体 | Phase 1 後半 |

## 7.2 単体テストのカバレッジ目標

| モジュール | 目標 |
|---|---|
| スコア算出関数 | 100% |
| 判定ロジック（状態判定等） | 100% |
| ユーティリティ（時刻・通貨変換） | 100% |
| MCP ツール | 80%+ |
| エージェント | 70%+ |
| API ルーター | 70%+ |

## 7.3 モック方針

- **外部 API はモック**（unit test では LLM、moomoo を呼ばない）
- **DB はインメモリ SQLite**（テスト用）
- **時刻はフリーズ**（pytest-freezegun 等）

## 7.4 テストデータ

`tests/fixtures/` に固定データ：
- `sample_portfolio.json`
- `sample_news.json`
- `sample_screening_results.json`
- `sample_llm_responses.json`

---

# 8. ドキュメント

## 8.1 コード内のドキュメント

```python
def calculate_profit_taking_score(
    target_achievement: float,
    scenario_achievement: float,
    technical_warning: float,
    ai_confidence: float,
) -> float:
    """利確スコアを 4 軸で算出する。

    PANEL_SPECS.md セクション C-1.2.3.1 に従う。
    重み: 目標達成度 40%, シナリオ達成度 30%, テクニカル 20%, AI 10%

    Args:
        target_achievement: 0.0-1.0、目標到達度
        scenario_achievement: 0.0-1.0、仮説達成度
        technical_warning: 0.0-1.0、テクニカル悪化度
        ai_confidence: 0.0-1.0、AI 確信度

    Returns:
        0-100 のスコア
    """
    return (
        target_achievement * 0.4
        + scenario_achievement * 0.3
        + technical_warning * 0.2
        + ai_confidence * 0.1
    ) * 100
```

## 8.2 README.md

プロジェクトルートの README.md は **OPERATIONS.md セクション 1（セットアップ）** を要約。

詳細は docs/ の各設計書へ誘導。

## 8.3 変更履歴

`CHANGELOG.md` を維持。Keep a Changelog 形式：
- Added / Changed / Fixed / Removed

---

# 9. コミュニケーション

## 9.1 ユーザーへの報告タイミング

以下のタイミングで、必ず簡潔な報告：

| タイミング | 内容 |
|---|---|
| フェーズ完了 | 何ができるようになったか、確認方法 |
| 大きな判断が必要 | 設計書にない決定が必要な時 |
| 設計書と矛盾発見 | 矛盾の内容、影響範囲、選択肢 |
| 想定外のエラー | エラー内容、対処方針 |
| Phase 1 スコープを超える要望 | スコープ外であることを明示 |

## 9.2 報告の形式

```
✅ 完了：[何ができるようになったか]
📋 動作確認方法：[具体的なコマンド or URL]
⚠️ 注意点：[あれば]
🔄 次のタスク：[次は何をするか]
```

## 9.3 質問の形式

設計書を読んでも判断できない時：

```
❓ 質問：[判断が必要な事項]
📚 関連設計書：[どの設計書のどこを見たか]
💭 私の解釈：[現時点での解釈]
🎯 選択肢：
  - A. [選択肢A] — メリット・デメリット
  - B. [選択肢B] — メリット・デメリット
👉 推奨：[私が推す選択肢と理由]
```

---

# 10. 進捗管理

## 10.1 タスク状態

各タスクの状態：

- `[ ]` 未着手
- `[wip]` 着手中
- `[done]` 完了
- `[blocked]` 何か待ち
- `[skip]` スキップ（理由を明記）

## 10.2 進捗ファイル

`docs/PROGRESS.md` を Claude Code が維持：

```markdown
# Trading Agent 実装進捗

## Phase 1.0：環境構築
- [done] プロジェクト初期化
- [done] pyproject.toml セットアップ
- [done] .env.example 作成
- [wip] 設定モジュール（config.py）

## Phase 1.1：MCP ツール基盤
- [ ] base.py（基底クラス）
- [ ] market_data
...
```

各セッション開始時に PROGRESS.md を確認、終了時に更新。

---

# 11. Phase 1 完了の判定

Phase 1 が「完了」と言える条件（OPERATIONS.md セクション 11 を引用）：

- [ ] ペーパー口座で 4 週間以上の朝バッチ実行実績
- [ ] 全エージェントが安定稼働
- [ ] moomoo 連携（読み取り）が動作
- [ ] ダッシュボードの全パネルが表示
- [ ] 月次 API 予算（¥5,000）内で運用
- [ ] バックアップ・復旧手順が確認済み
- [ ] 健康チェックが機能
- [ ] 緊急停止が機能
- [ ] ユーザーが trace を通読してシステムに納得

これらが揃って初めて Phase 2 に進む判断ができる。

---

# 12. よくある誤解と回避

## 12.1 「LangChain で全部解決できそう」

❌ LangChain の高度な機能（LangGraph 等）を使うのは Phase 2-。
✅ Phase 1 は LangChain の基本機能（Agent, Tool, Memory）のみ。

## 12.2 「LLM に判断任せすぎ」

❌ プロンプトに「判断してください」と丸投げ。
✅ 構造化された出力（JSON Schema）で **何を出すか明確に指示**。

## 12.3 「とりあえずデフォルト値で」

❌ ハードコードした閾値・重み。
✅ settings テーブル or 設定ファイルで **必ず変更可能**にする。

## 12.4 「エラー処理は後で」

❌ try-except なしで先に進む。
✅ 各 MCP ツール・エージェントは **エラー処理を含めて完成**させる。

## 12.5 「テストは後で」

❌ 全部実装してからテスト。
✅ 各モジュール実装時にテストを書く（特に純粋関数）。

## 12.6 「UI は最後」

❌ バックエンド完成してから UI に着手。
✅ Phase 1.6 で UI に取り組むが、API は Phase 1.4-1.5 で並行整備。

---

# 13. 用語集

| 用語 | 定義 |
|---|---|
| **朝バッチ** | JST 5:00 に実行される一連のエージェント実行 |
| **Core-Satellite** | コア戦略 80%、サテライト戦略 20% の配分管理 |
| **V字回復** | 業績反転 + 株価底打ち反転の中期パターン |
| **テーマ戦略** | マクロトレンド（AI、半導体等）に紐づく上昇候補 |
| **Hot Path** | 重要判断、Claude Sonnet を使う処理 |
| **Cold Path** | 軽量処理、Ollama を使う処理 |
| **Critical** | 深掘り分析、Claude Opus を使う処理 |
| **Tier 1/2/3** | 自動化の段階（手動 → ワンクリック → 完全自動） |
| **ペーパー口座** | moomoo のシミュレーション口座 |
| **実弾** | 実際の取引、Phase 2 以降 |
| **MCP** | Model Context Protocol、エージェントとツールの接続規格 |
| **moomoo OpenD** | moomoo の API ゲートウェイ（GUI アプリ） |
| **invocation_id** | 1つの実行を追跡する一意の ID |
| **HALT** | 緊急停止フラグファイル |

---

# 14. 始め方

## 14.1 最初のタスク

**IMPLEMENTATION_PHASES.md を開き、Phase 1.0 のタスクから順に着手する。**

各タスクの完了基準を満たしたら次へ。
迷ったらこのファイル（CLAUDE_CODE_INSTRUCTIONS.md）に戻る。

## 14.2 第一声

最初のセッションで、Claude Code がユーザーに伝えるべき内容：

```
こんにちは、Claude Code です。

Trading Agent プロジェクトの実装を始めます。

📚 確認済みドキュメント：
- CLAUDE_CODE_INSTRUCTIONS.md
- STEP_A_FINAL.md
- IMPLEMENTATION_PHASES.md

🎯 これから着手するタスク：
Phase 1.0：環境構築
  - Task 1.0.1：プロジェクト初期化（pyproject.toml）
  - Task 1.0.2：ディレクトリ構造の作成
  - Task 1.0.3：.env.example の作成
  - Task 1.0.4：config.py の実装
  - Task 1.0.5：基本的なロギング設定

📋 完了基準：
- pyproject.toml で uv sync が成功する
- config.py から settings を読み込める
- .env.example をコピーすれば起動できる状態

着手していいですか？
```

## 14.3 ユーザーが「はい」と言ったら

実装を開始。各タスク完了ごとに報告。
不明点が出たら、設計書を再確認 → それでも判断できなければユーザーに質問。

---

# 15. このプロジェクトに関わる人への感謝

このプロジェクトの設計は、**ユーザーとの 27時間以上のブレスト** から生まれた。

設計書群（STEP A〜C）は、迷い、議論、方向転換、合意の積み重ね。
特に：

- 「投資補助ツール」→「AIエージェント学習が本懐」→「実用優先」への揺れ戻し
- ベースリポジトリ選定の撤回と「機能別の部分採用」への転換
- ブローカー：auカブコム → moomoo OpenAPI への変更
- レコメンドの「売り」が抜けていたことの発見と再構築
- ダッシュボードの 9回イテレーション（装飾的 UI → Linear + Bloomberg）

これらの議論を経て、現在の設計に至った。

**設計書を信じ、Phase 1 を完成させ、運用しながら磨いていく。**

これが、このプロジェクトの哲学。

---

# 次に読むファイル

→ **IMPLEMENTATION_PHASES.md**

そこにフェーズごとのタスクが定義されている。Phase 1.0 から順に着手する。
