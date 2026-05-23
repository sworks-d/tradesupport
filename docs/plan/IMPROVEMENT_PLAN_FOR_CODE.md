# 現状改善 実装ロードマップ（code投入用）

作成日：2026-05-23 ／ ブランチ想定：`feat/magi-rebuild`
正本：`docs/plan/spec/`（00_overview〜P6/X/U）。本書はそれを**現状の実コードに突き合わせた実行ロードマップ**。
OSS借用の詳細：**別ファイル `spec/X1_reuse_inventory.md` を正**とする（本書C群はその要約＋移植先のみ）。

---

## 0. 着手前に固定する「現状の事実」（実コード確認済み 2026-05-23）

> spec群の状態フラグ（✅🟡❌）には実装と**ズレがある**。下記の実コード事実を正とする。
> 古い自己申告（ver1/PROGRESS.md等）より、この事実を優先せよ。

| 事実 | 実コードでの確認内容 | 影響 |
|---|---|---|
| **MAGIがDAG未接続（最大の断線）** | `trading_agent/magi/`（judges/defense/integration/commander）は実装済・テスト有。だが `orchestrator/morning_batch.py` のDAGは `market_analyst→sell_recommender→portfolio_builder` の**旧5軸型のまま**。`materialize_decisions`/`magi_verify` ノードが無く、**MAGIは一度も実行経路に乗っていない孤立コード**。 | MAGIは単体では動くがシステムに繋がっていない。最優先で接続 |
| **decisions は孤立テーブル** | `Decision` を import するのは `models/__init__.py` のみ。**生成箇所が皆無**（B0_DIFF_PLAN §1の指摘どおり）。 | schema変更の後方互換リスクは最小。安全に再構成できる |
| **news.py が spec要求と不一致** | `_default_fetchers()` は `_fetch_rss`+`_fetch_newsapi` のみ。**yfinance.news / Google News RSS が未実装**。NewsAPIはキー無で空→ニュース0件→CASPER常にna | CASPER目隠しの直接原因。最優先穴 |
| **decisions が ver1形のまま** | `models/decisions.py` は `score:int`（必須）/`expected_return`/`scenarios`/`thesis_at_decision`/`evaluation_date` 必須。**status / gendo_stance が無い** | 下流（提案→決裁→発注→評価）全部の前提。§A-3で再構成（**方針は確定済・下記**） |
| **MAGI 4表は定義済み** | `models/magi.py` に JudgeVerdict/SplitPattern/CommanderRec/Verification が**全て定義済み**。decision_id FK も準備済み | 4表追加は不要。decisionsとの接続だけ |
| **防御層に拡張フックあり** | `defense.py` に `credibility_flag`（信用性D-14用）・`gendo_compliant`（碇B5用）が既にあり既定値で動く | 反証層・信用性フィルタを受け入れる準備済み |
| **universe空・候補NVDA固定** | `universe` 定義済・`screening_agent` は読む準備済だが**投入スクリプト無く空**。`scripts/build_snapshot.py` の `CANDIDATES={"nvda":"NVDA"}` 固定 | screening未起動＝NVDA決め打ちの正体 |

**最初の到達目標（このmdのゴール）**：
**1銘柄が「収集→スクリーニング→3審判→防御→統合→碇→decision保存→決裁可能」まで端から端まで通る（最初の貫通）。**
その後、反証層(B)で判断を立体化し、OSS借用(C)で評価基盤を入れる。

---

## 1. 厳守事項（不変原則 ＋ 借用の線引き）

`00_overview.md §3` の不変原則7つ・`§4` のR1〜R7を全タスクで守る。特に：
- **数値はコードが取得/計算/照合した実データのみ。LLMに数値を生成・計算させない（R1/原則4）。**
- **総合スコアを出さない（SCORE: NONE・原則3）。** screeningのcompositeは内部用、UIに出さない（D-06）。
- **決裁は人間（原則2/5）。** 自動発注しない。Tier1手動。
- **引き継ぎ契約を守る（R7）。** 各タスクは前タスクの出力だけを入力にする。入力に無い銘柄・数値を作らない。
- **OSS借用の線引き（X1準拠）**：借用元は軒並み「LLMが数値生成・合意で自動決定・自己進化」。
  **収束/自動決定/自己改変の層は借りない。分析の質・評価計算・データ接続の層だけ借りる。**

設計分岐は「いい感じに」で進めない。`[要ユーザー判断]` 印は選択肢を提示して止まる。

---

## 2. ロードマップ全体（A>B>C・依存と着手条件つき）

```
A群：断線解消（最優先・この順で「最初の貫通」を達成）
  A-3 decisions schema再構成［方針確定済］─┬─→ A-4 MAGI DAG接続 ─→ A-5 決裁→発注リスト
  A-1 universe投入 ───────────────────────┤        ↑
  A-2 news実装(yf+GNews) ─────────────────┘  （A-1/A-2でscreening・CASPER起動→候補とデータが揃う）

B群：反証層（着手条件＝A-4完了。MAGIが動く土台が要る）
  B-1 反証フィールド → B-2 BALTHASAR反証(コード) → B-3 MELCHIOR/CASPER反証(LLM+照合)
                                              → B-4 碇が反証を束ねる → B-5 内在不安の可視化

C群：OSS借用（着手条件＝A-5後。decisionが生成される状態。詳細は spec/X1_reuse_inventory.md）
  C-0 実LICENSE確認 → C-1 P6評価ジョブ(ai-hedge-fund) → C-2 判断ログ参照(TradingAgents)
                                                      → C-3 MELCHIOR深掘り指標(ai-hedge-fund)
```

**禁止：C群をA群より先に着手すること。** 評価対象(decision)が無い段階でbacktesterを移植しても回す相手がいない。
**順序：** A-3 →（A-1 / A-2）→ A-4 → A-5 → B-1〜B-5 → C-0 → C-1〜C-3。

---

# A群：断線解消（最優先）

## A-3 decisions スキーマ再構成 ★最初にやる ［方針確定済 2026-05-23］
- **目的**：ver1形（総合スコア前提）の `decisions` をMAGIに適合させ、提案を保存可能に。下流全部の前提。
- **対象**：`models/decisions.py`、`alembic/versions/`、`tests/unit/test_models.py`（＋`test_sell_recommender.py`）。
- **確定方針（ユーザー確定。叩き台ではない）**：
  - **既存カラムは1つも消さない。** 別表分離はしない（Phase1は貫通優先。分割はPhase2以降）。
  - **追加**：
    - `status: str = "verifying"`（遷移：verifying→verified→awaiting→approved/denied/held→order_listed→ordered→holding）
    - `gendo_stance: str | None`（推し/利確/撤退/要検討/静観。碇/割れから導出）
    - `verified_at: dt.datetime | None`
  - **nullable化**（MAGI候補生成時=verifyingではまだ確定しないため）：
    - `score: int | None` … **残すがUIに総合点として出さない（D-06）。A/B育成用データ。**
    - `expected_return: float | None` / `target_period_days: int | None`
    - `thesis_at_decision: str | None` / `evaluation_date: dt.date | None`
  - **scenarios**：別表に出さず既存JSONのまま。**予測値は `is_model_generated=true` 相当で扱い、UIで「未照合」バッジ・碇の根拠から除外**（README §4・場所でなくフラグで分離）。
- **次への引き渡し**：MAGI候補を `status="verifying"` で保存できる decision スキーマ（→A-4）。
- **ハルシネ防止**：R7 既存カラム無改変（追加とnullable化のみ）。
- **受入**：MAGI候補から decision を生成・保存できる。既存テスト green（更新分は意図をコミットに記録）。
- **依存**：なし。**規模**：中。**後方互換**：Decision生成箇所が皆無のため影響最小。

## A-1 universe 投入  [要ユーザー判断：銘柄リストの出所]
- **目的**：母集団を入れてスクリーニングを起動可能に（NVDA決め打ち脱却）。
- **対象**：新規 `scripts/load_universe.py`。`models/universe.py`（定義済）。
- **データソース**：銘柄リスト。**[要ユーザー判断]**＝手元リスト or 自動定義（¥1M端株前提でUS主要＋安価JP）。
- **実装**：universeへ upsert（ticker/market/name/sector/market_cap/avg_volume_30d…）。`screening_agent._load_universe` が既に読む。
- **ハルシネ防止**：R7 出所の明確な銘柄のみ登録。勝手に発明しない。
- **受入**：universe に N 件入り、`screening_agent` が母集団を読める。
- **依存**：なし。**規模**：小。

## A-2 news 実装（yfinance + Google News RSS）★最優先穴
- **目的**：CASPERの目隠しを解除（銘柄別ニュース0件を解消）。
- **対象**：`mcp_tools/news.py`（fetcher追加）、`tests/unit/test_news.py`。
- **データソース（無料・キー不要）**：
  1. `yfinance` の `Ticker(t).news`
  2. Google News RSS `https://news.google.com/rss/search?q={ticker}+{company}+株+OR+stock&hl=ja&gl=JP&ceid=JP:ja`
- **実装**：`_fetch_yf_news`/`_fetch_gnews_rss` を新設し `_default_fetchers()` に追加。既存の重複除去/言語判別/期間/source_refs を流用。
- **ハルシネ防止**：R2 各記事に url＋published(as_of)必須／R4 0件はna（捏造しない）／R5 summaryはソース提供文のまま。
- **受入**：NVDA・7203 で記事≥1、`casper()` が na を脱して方向を返す。新規テスト＋既存 green。
- **依存**：なし。**規模**：小。

## A-4 MAGI を DAG に接続（materialize_decisions + magi_verify）★貫通の核
- **目的**：孤立しているMAGIを実行経路に繋ぐ。候補→decision生成→3審判→防御→統合→碇→保存。
- **対象**：`orchestrator/morning_batch.py`（2ノード追加）、新規 `magi/persist.py`、`tests/unit/test_morning_batch.py`。
- **実装（B0_DIFF_PLAN §2 のDAG差分）**：
  - `portfolio_builder` の後に2ノードを直列追加：
    - **materialize_decisions**：active な buy/sell signals（or screening上位）→ `Decision(status="verifying")` 生成。
    - **magi_verify**：各decisionで `run_judges()→classify_split()→verify()→command()` を実行し、
      `judge_verdict×3 / split_pattern / verification / commander_rec` を decision_id 付きで保存。
      完了で `status: verifying→verified→awaiting`。`verification.default_hold` を decision に反映。
  - 既存 `market_analyst` は当面残す（5軸はscoreとしてnullable保持・UI非表示）。**MAGIが判断の正**。
- **ハルシネ防止**：R6 default_hold を status/既定決裁に反映／R2 全行に出典・時点／R7 候補に無い銘柄を作らない。
- **受入**：1銘柄がDBに decision＋MAGI 4表付きで保存され status 遷移。`magi_verify` 完了まで「決裁待ち」にしない。
- **依存**：A-3。A-1/A-2（候補・データが揃うと中身が出る）。**規模**：中。

## A-5 決裁 → 発注リスト → 記録
- **目的**：承認/否認/保留を保存し、承認分をmoomoo手動発注用に出力、結果を保有化。
- **対象**：FastAPI `POST /api/decisions/{id}`、`ui/`（決裁ボタンonClick）、発注リスト出力（CSV/画面）、`brokers/moomoo.py`。
- **実装**：押下→`decision.status`更新→snapshot反映。未照合/割れは既定「保留」を選択状態に（R6）。
  承認→発注リスト（銘柄/数量＝サイジングのコード値/想定価格/メモ）。**自動発注しない**（Tier1）。
- **ハルシネ防止**：R1 数量はサイジングのコード値のみ／R6 既定保留／承認時「未照合のまま承認」を画面明示。
- **受入**：押下でstatus保存・画面反映。承認→発注リストに正しい数量で出る。
- **依存**：A-4。**規模**：中。

### ▼ A群完了の判定（最初の貫通）
1銘柄が universe→screening→3審判→防御→統合→碇→decision(awaiting)→決裁→発注リスト まで通る。
ここで初めて「動くパイプライン」になる。B/Cはこの上に乗せる。

---

# B群：反証層（各審判が自領域で反証）— 着手条件：A-4完了

> 設計意図：三権独立を**壊さず**、各審判が自分の領域データ内で「判定と逆向きの事実」を摘出する。
> TradingAgents の Bear Researcher（反対論拠の組み立て方）を翻案。**別Bear体は置かない**（領域横断で独立性を壊す）。
> 反証は「予測・懸念の創作」ではなく「**既存データ内の不都合な事実の摘出**」＝R5を守る。

## B-1 反証フィールド追加
- **対象**：`models/magi.py`（JudgeVerdict）。
- **実装**：`counter_within_domain: list[dict]`（各項目に claim と source_refs）。
- **受入**：JudgeVerdict が反証配列を保持・保存できる。**規模**：小。**依存**：A-3/A-4。

## B-2 BALTHASAR 反証（コード・LLM不使用）
- **対象**：`magi/judges.py::balthasar`。
- **実装**：buy寄りでも「RSI過熱/ダイバージェンス/出来高減少」等の逆向きシグナルを既存technicalsから**コードで**摘出。
- **ハルシネ防止**：R1 全てコード／無ければ「反証なし（データ上は一貫）」とR4で正直に。
- **受入**：golden_crossでもoverbought等があれば反証に出る。**規模**：小。**依存**：B-1。

## B-3 MELCHIOR / CASPER 反証（LLM摘出＋コード照合）★原則的に最も繊細
- **対象**：`magi/judges.py`（melchior/casper）、`magi/defense.py`（反証照合）、`mcp_tools/llm_call.py` 経由。
- **実装**：取得済みデータをLLMに渡し「**この判定と逆向きの事実をデータから選べ。新情報・予測を足すな**」。
  摘出後、各項目がデータに実在するか防御層で照合。MELCHIORの「何を見るか」は **C-3と統合**。
- **ハルシネ防止**：R5 LLMは選択のみ・創作禁止／"〜だろう"等の予測語を弾く／source_refs必須／照合不通過の反証は捨てる。
- **受入**：実データ内の逆向き事実のみ反証に出る。**創作（データに無い懸念）が照合で弾かれる**。**規模**：中。**依存**：B-1、A-2。

## B-4 碇司令が反証を束ねる
- **対象**：`magi/commander.py::command`。
- **実装**：碇の独自生成から、各審判の `counter_within_domain` の集約に変える。
- **ハルシネ防止**：R5 碇はMAGI（反証含む）の範囲内のみ。新事実を足さない（gendo_compliant維持しやすい）。
- **受入**：碇の反対論拠が各審判の反証に紐づく。**規模**：小。**依存**：B-2/B-3。

## B-5 統合機構で「全会一致でも内在不安」を可視化
- **対象**：`magi/integration.py::classify_split`。
- **実装**：各審判の反証数を見て「3審判一致だが全員が割高/過熱を摘出」等の interpretation を追加。**推奨はしない**。
- **ハルシネ防止**：集計のみ。総合スコアを出さない。
- **受入**：全会一致＋全員反証ありで内在不安の interpretation が出る。**規模**：小。**依存**：B-2/B-3。

---

# C群：外部OSS借用（精度向上）— 着手条件：A-5後

> **詳細・原則適合の判断は `spec/X1_reuse_inventory.md` を正とする。** 本書は移植先と着手条件のみ。
> 共通の線引き：**収束/自動決定/自己改変の層は借りない。分析の質・評価計算・データ接続だけ借りる。**

## C-0 実LICENSE確認 ★C群着手の必須前提 [要ユーザー判断]
- TradingAgents / ai-hedge-fund / FINSABER / OpenBB の実LICENSEを確認しADRに記録。
- **OpenBBはAGPL/商用デュアルの可能性**＝丸ごと依存せず「接続の作り方を参照し自前実装」を既定。
- **受入**：各候補のライセンスと取り込み方針（部分移植/参照のみ）が記録される。**規模**：小。

## C-1 P6評価ジョブ（ai-hedge-fund backtester の指標計算のみ）
- **借用元**：https://github.com/virattt/ai-hedge-fund `src/backtester.py`
- **借りる/捨てる**：累積リターン/勝率/Sharpe/最大DDの**計算だけ移植。自動執行ループは捨てる**（Tier1死守）。
- **対象**：新規 評価ジョブ、`models/decisions`(actual_return/hit_or_miss/evaluated_at)、`mcp_tools/market_data`。
- **受入**：評価期日後に hit/miss が付き、UIが実績＋S&P比超過を表示。移植部にライセンス出典。
- **依存**：A-5、C-0。**規模**：中。詳細：X1 §2「P6」。

## C-2 判断ログの参照提示（TradingAgents reflection・自己改変除外）[要ユーザー判断：原則7の解像度化]
- **借用元**：https://github.com/TauricResearch/TradingAgents （reflection＝実現リターン+SPY比alphaを次回注入）
- **借りる/捨てる**：**判断ログの構造化蓄積＋類似ケース参照は採用。ロジック自動書換は捨てる**（原則7）。振り返り文はLLM可だが**数値はコード**。
- **[要ユーザー判断]**：原則7を「自己改変=不採用維持／参照提示=採用追加」に解像度化するか（X1 §4）。
- **受入**：同一銘柄の再評価時に過去判断と実績が参照提示される（自動書換なし）。
- **依存**：C-1。**規模**：中。詳細：X1 §2「P6-2/3」。

## C-3 MELCHIOR 深掘り指標（ai-hedge-fund ペルソナの財務チェックリスト）
- **借用元**：https://github.com/virattt/ai-hedge-fund （Graham/Damodaran/Burry等が見る指標体系）
- **借りる/捨てる**：**指標リストだけ抽出（増収率・営業CF/純利益・在庫回転・PER過去比等）。ペルソナLLMの数値生成は捨てる。値はコード計算**。
- **対象**：`mcp_tools/fundamentals.py`、`magi/judges.py::melchior`。
- **受入**：MELCHIORが従来より多い財務観点で判定・反証できる。
- **依存**：B-3と統合、P1-5（一次情報）。**規模**：中。詳細：X1 §2「P3-1」。

---

## 3. 進め方・報告（全タスク共通）
- **1セッション1タスク**。着手時に「どのタスク/依存は満たすか/受入は何か」を宣言。
- 既存テスト green を維持（壊したら原則に沿って直す）。各タスクに単体テスト。
- `[要ユーザー判断]`（A-1銘柄リスト/C-0ライセンス/C-2原則7）は**選択肢を提示して止まる**。勝手に決めない。
  ※A-3は方針確定済（このmd §A-3）。確認不要。
- 各タスク完了で `docs/progress/NNNN-*.md`（指示＋作業＋判断）を残しコミット。
- **C群をA群より先にやらない。**

## 4. このmdの初手（codeへ）
```
1. docs/plan/spec/ と本書 と spec/X1_reuse_inventory.md を読む。
2. §0「現状の事実」を実コードで再確認（morning_batch.py に MAGI が無い／news.py に yf/GNews が無い／
   models/decisions.py が ver1形／Decision生成箇所が皆無）。
3. A-3（decisions schema再構成）から着手。方針は §A-3 で確定済（既存カラム保持＋必須5項目nullable化＋
   status/gendo_stance/verified_at追加・別表分離なし）。確認不要、そのまま実装。
4. 以降 §2 の依存順（A→B→C）で進める。
```
