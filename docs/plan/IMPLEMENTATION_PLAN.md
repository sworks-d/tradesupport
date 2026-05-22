# 詳細実装計画（作業パッケージ単位）

最終更新：2026-05-23 ／ ブランチ `feat/magi-rebuild`
位置づけ：`README.md`＝戦略正本／`TASKS.md`＝進捗チェック／`DECISIONS.md`＝判断ログ／
**本書＝全工程を WP（作業パッケージ）単位で「何を・どのファイルで・どのソースで・どう作り・
何を満たせば完了か」まで落とした詳細手順**。`pipeline.html` の過不足と対応。

各WP記法：**目的 / 対象ファイル / データソース / 実装内容 / 受入条件 / 依存 / 規模(小中大)**。

---

## 0. 現状（実装済み＝触らず活かす）
- UI：Next.js逐語移植・反映機構（snapshot→UI）・チャート補正・主要インタラクション。
- 収集：相場(yfinance+stooq 2ソース)・テクニカル(コード)・財務サマリ(yfinance)・出典/時点。
- 判断：MAGI 3審判→防御層→統合(4類型)→碇（**コード生成・実費0**）。サイジング(20%上限・端株)。
- ブローカー：moomoo brokers層（OpenD接続確認・ペーパー空）。221テスト green。
- **未：文脈(ニュース/開示/マクロ)・財務一次情報・スクリーニング起動・決裁/発注/評価・外部リポジトリ借用。**

## 1. 推奨順序（依存つき）
```
WP-A1 ニュース ─┐
WP-X1 外部棚卸し ┤→ WP-A2 一次情報/信用性 ─┐
WP-B1 universe ─┴→ WP-B2 screening → WP-B3 候補→MAGI ─┐
                                                       ├→ WP-C1 decisions schema
WP-X2 外部移植(評価/backtest) ────────────────────────┤→ WP-C2 B6 永続化
                                                       └→ WP-C3 決裁→C4 発注→C5 評価
UI整備：WP-U1 コンポーネント化 / U2 新規6画面 / U3 LLM接続（随時）
```
最短で「使える」へ＝ **A1 → X1 → A2/B1/B2/B3 → C1-C5**。

---

## 2. Phase 1：情報ソース（"使える"の前提）

### WP-A1 ニュース収集（無料・銘柄別）★最優先
- **目的**：CASPERの目隠しを解除（銘柄別ニュース0件を解消）。
- **対象**：`trading_agent/mcp_tools/news.py`（fetcher追加）、`tests/unit/test_news.py`。
- **ソース**：(1) `yfinance` の `Ticker(t).news`（無料・キー不要・銘柄別）。(2) Google News RSS
  `https://news.google.com/rss/search?q={ticker}%20stock&hl=ja&gl=JP&ceid=JP:ja`（無料・銘柄別）。
- **実装**：上記2 fetcher を `_default_fetchers` に追加。既存の重複除去/言語判別/期間/source_refs を流用。
  ティッカー別クエリで NewsInput.tickers を使う。
- **受入**：NVDA・7203 で `news` が記事>0 を返し、`casper()` が `na` を脱して verdict を出す。テスト追加 green。
- **依存**：なし。**規模**：小。

### WP-A2 財務一次情報＋信用性フィルタ（D-14）
- **目的**：MELCHIORの素材を深める＋粉飾/上場廃止を入口で弾く。
- **対象**：`mcp_tools/disclosure.py`・`fundamentals.py`、新規 `trading_agent/screening/credibility.py`、tests。
- **ソース**：EDINET API（JP：監査意見/GC注記/有報訂正、要 `edinet_api_key`）、SEC EDGAR（US：8-K/10-K）。
- **実装**：第1フィルタ＝ハード除外（監査意見が無限定適正以外/上場廃止基準/特設注意/GC注記）。
  D-14 どおり**重大なもの（上場廃止・特設注意）から段階導入**。`screening` の前段に挿入。
- **受入**：故意に不適正フラグの銘柄を除外/警戒フラグ。`verification.credibility_flag` が実値で動く。
- **依存**：EDINETキー（ユーザー設定）。**規模**：中。

### WP-A3 相場の本番化（moomoo）
- **目的**：yfinance（非公式）→ moomoo 実価格/quote へ。
- **対象**：`mcp_tools/market_data.py`（fetcherにmoomoo追加）、`brokers/moomoo.py`（quote）。
- **ソース**：moomoo OpenAPI（要 OpenD＋同意②＋市場データ権限）。
- **受入**：`--moomoo` で価格が moomoo 由来になる。**依存**：moomoo同意②。**規模**：小（差替）。

---

## 3. Phase 2：候補生成（収集→MAGI を繋ぐ）

### WP-B1 universe 投入
- **目的**：母集団を入れてスクリーニングを起動可能に（NVDA決め打ち脱却）。
- **対象**：新規 `scripts/load_universe.py`、`models/universe.py`。
- **ソース**：銘柄リスト（**要判断**：手元リスト or 自動定義＝¥1M端株前提でUS主要＋安価JP）。
- **実装**：universeテーブルへ upsert（ticker/market/name/sector）。
- **受入**：universe に N 件入る。**依存**：銘柄リストの出所決定。**規模**：小。

### WP-B2 スクリーニング起動
- **目的**：定量一次選抜を実データで回す。
- **対象**：`agents/screening_agent.py`（実行）、`orchestrator/morning_batch.py`（universe_refresh有効化）。
- **ソース**：market_data/technicals/fundamentals（既存）＋ universe。
- **実装**：universe→データ収集→`screening`ツール（V字4軸＋テーマ4軸）→composite→上位抽出。
  composite は内部用（**UIに総合点は出さない**＝D-06）。
- **受入**：ScreeningResult が保存され、合格候補リストが得られる。**依存**：WP-B1。**規模**：中。

### WP-B3 候補→MAGI 接続（CANDIDATES動的化）
- **目的**：スクリーニング上位を MAGI に流し、snapshot の候補を実リスト化。
- **対象**：`scripts/build_snapshot.py`（`CANDIDATES` 固定→screening結果から生成）。
- **実装**：上位候補に `run_judges`→`classify_split`→`verify`→`command`→sizing。複数候補を snapshot.candidates に。
- **受入**：買いゾーンが実候補（複数）で埋まる。**依存**：WP-B2、WP-A1（CASPER用ニュース）。**規模**：中。
  ※UIの買いゾーンは固定カードのため、複数候補の完全表示は WP-U1（コンポーネント化）後。

---

## 4. Phase 3：外部AIエージェント投資リポジトリの借用（要件・未着手0%）

### WP-X1 棚卸し調査
- **目的**：`virattt/ai-hedge-fund`・`TradingAgents` から**何を借りるか**を確定。
- **対象**：新規 `docs/plan/EXTERNAL_REUSE.md`。
- **実装**：両リポジトリを実際に読み、借用候補を分類—(a) データadapter（ソース接続）、
  (b) 審判/アナリスト・プロンプト構造、(c) **バックテスト・評価指標**、(d) debate/反証フレーム。
  各々「本パイプラインのどのWPに効くか・採否・ライセンス」を表で。
- **受入**：借用候補リスト＋採否＋移植先WPの対応表。**依存**：なし。**規模**：小（調査）。

### WP-X2 借用部品の移植
- **目的**：採用部品を統合（特にバックテスト・評価指標・データadapter）。
- **対象**：WP-X1 の採否に従い該当モジュール。
- **受入**：移植部品が動作＋テスト。**依存**：WP-X1。**規模**：中〜大。

---

## 5. Phase 4：決裁 → 発注 → 評価（B6＋出口）

### WP-C1 decisions スキーマ再構成（B6前提）★要判断
- **目的**：ver1形（総合スコア前提）の `decisions` を MAGI に適合。
- **対象**：`models/decisions.py`、マイグレーション、`test_models.py`。
- **実装**：`status`(verifying/…/holding)・`gendo_stance` 追加。ver1必須項目
  (score/expected_return/scenarios/thesis_at_decision/evaluation_date) を **nullable化 or 別表へ分離**。
- **受入**：MAGI候補から decision を生成・保存できる。**依存**：—。**規模**：中。**要判断**：schema方針。

### WP-C2 materialize_decisions ＋ magi_verify（B6本体）
- **目的**：候補→decision生成→MAGI 4段→`judge_verdict/split_pattern/commander_rec/verification` を永続化。
- **対象**：`orchestrator/morning_batch.py`（2ノード追加）or 独立関数 `magi/persist.py`、tests。
- **実装**：B0_DIFF_PLAN §2 のDAG差分。検証完了まで decisions を決裁待ちにしない。状態遷移を実装。
- **受入**：1銘柄が DB に全MAGI付きで保存され、status が遷移する。**依存**：WP-C1, B2-B5。**規模**：中。

### WP-C3 決裁UI配線
- **目的**：承認/否認/保留ボタン→保存。
- **対象**：FastAPI route（`POST /api/decisions/{id}`）、`ui/`（決裁ボタンの onClick）。
- **実装**：押下→status更新→snapshot反映。未照合/割れは既定保留（B3）。
- **受入**：押下で decision.status が変わり画面に反映。**依存**：WP-C2。**規模**：中。

### WP-C4 発注リスト＋記録（Tier1手動）
- **目的**：承認→moomoo手動発注用の出力→発注記録→保有化。
- **対象**：発注リスト出力（CSV/画面）、`brokers/moomoo.py`（約定/保有取得）、portfolio。
- **受入**：承認→発注リスト→（手動発注後）positions反映。**依存**：moomoo, WP-C3。**規模**：中。

### WP-C5 評価（Track Record 実データ化）
- **目的**：decision の予測 vs 実績を評価し Track Record をモック→実データに。
- **対象**：評価ジョブ、`models/decisions`(actual_return/hit_or_miss)、ui Track Record。
- **受入**：評価期間経過後に hit/miss が付き、UIが実績を表示。**依存**：WP-C2＋データ蓄積。**規模**：中。

---

## 6. Phase 5：UI整備（随時）
- **WP-U1**：F2 コンポーネント化（生HTML注入→Reactコンポーネント）＋ Playwright 視覚回帰（差分≈0ゲート）。
- **WP-U2**：新規6画面（スクリーニング/ポートフォリオ/週次レポート/設定3種）を同トンマナで新規デザイン（D-18）。
- **WP-U3**：LLM接続（Ollama導入→MELCHIOR、Anthropic→CASPER/碇 の自然文化）。コストロガー経由・日次¥500/月¥5000で停止。碇MAGI準拠を機械照合（D-15）。

---

## 7. 受入・テスト方針（全WP共通）
- 既存テスト green を維持（再チューニングで壊したら原則に沿って直す）。各WPに単体テスト。
- 数値はコードのみ・LLMに生成させない（B3で機械照合）。総合スコアを出さない。
- UI変更は build green、視覚回帰（U1以降）。LLM呼び出しはコストロガー経由・予算で停止。
- 各WP完了で `docs/progress/NNNN-*.md`（指示＋作業）を作りコミット。
