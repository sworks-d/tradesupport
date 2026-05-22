# Trading Agent 再構築 — タスクシート

最終更新：2026-05-22
正本：`README.md`（戦略）／意思決定：`DECISIONS.md`
運用：着手時に 🟡、完了時に ✅。各フェーズは **Exit条件を満たしてから次へ**。

**凡例**：⬜ 未着手 ／ 🟡 進行中 ／ ✅ 完了 ／ ⏸ 保留 ／ 🔵 確認待ち（ユーザー）

---

## 進捗サマリ

> ※実装順は当初の B→F→C 順から、ユーザー方針「**作った所からUIに反映**」へ変更。
> その結果 moomooブローカー(Phase1.2)・スナップショット反映機構を前倒し。実態は下表。

| 段 | トラック | 内容 | 状態 |
|---|---|---|---|
| 着手前 | ゲート | 未確定論点(D-10〜D-20)＋APIコントラクト(=snapshot形) | ✅ |
| B0 | Back | 差分計画確定（→ `B0_DIFF_PLAN.md`） | ✅ |
| B1 | Back | 基盤再チューニング | 🟡 出典/時点・2ソース照合 ✅／信用性フィルタ・LLM審判割当 残 |
| B2 | Back | MAGI 3審判（独立検証・judge_verdict） | ✅ 決定論ロジック＋UI反映（一覧/詳細）。LLM解釈はオプトイン(B2c)残 |
| B3 | Back | 防御層（機械照合3本柱・決裁前ゲート） | ✅ verify()・既定保留・3フラグ反映 |
| B4 | Back | 統合機構（割れ方・SCORE: NONE） | ✅ split_pattern・4類型分類・反映 |
| B5 | Back | 碇司令（推奨＋反対論拠・MAGI準拠） | ✅ 決定論版（推奨＋必ず反対論拠）。LLM文面化(B2c)残 |
| B6 | Back | DAG挿入＆状態遷移（decisions生成→MAGI検証→保存） | ⬜ **要ユーザー判断**（decisions schema再構成・screening↔MAGI統合） |
| **P1.2** | Back | **moomooブローカー層**（positions/account・OpenD接続確認済 FUTUJP/US/SIMULATE） | ✅ 前倒し |
| F0 | Front | golden master確定・再現スコープ棚卸し | ✅（基準スクショは未取得） |
| F1 | Front | Next.js雛形＋CSS/フォント/マークアップ逐語移植 | ✅ build緑 |
| F1.5 | Front | チャート歪み補正（フル幅・文字/線幅一定 D-20） | ✅ |
| F2 | Front | コンポーネント化（視覚回帰） | ⬜ 現状は生HTML注入(dangerouslySetInnerHTML) |
| F3 | Front | データ結線 | 🟡 スナップショット経由：保有=実価格/実P&L/2ソース照合/接続状態 反映済 |
| F4 | Front | インタラクション | 🟡 詳細開閉/タブ/more展開/ホバー/更新 配線済（React state化は残） |
| F5 | Front | 新規6画面（MVP後） | ⬜ |
| **R** | 結合 | **スナップショット反映機構**（Back→`ui/public/data/snapshot.json`→UI） | ✅ 反映パターン確立 |
| C1 | 結合 | MAGI出力をUIへ結線・スコア痕跡撤去 | 🟡 B1成果(価格/照合/接続)は反映済／MAGI判定はB2後 |
| C2 | 結合 | E2E（NVDA 1銘柄・異常系） | ⬜ |
| C3 | 結合 | ペーパー運用準備 | ⬜ moomooペーパー接続済（建玉投入待ち） |

**いま走っているもの**：B2（MAGI 3審判）が次。UI側は反映機構が通っているので、作るたびに `snapshot.json` 経由で画面へ出していく。

---

## 着手前ゲート ✅（完了）

- [x] `DECISIONS.md` の未確定論点を決定（D-10〜D-20）
- [x] APIコントラクト初版＝`snapshot.json` のスキーマで確定（holdings/価格/照合/接続）
  - 注：当初想定の「TypeScript型＋モックAPI」は、まず snapshot JSON 方式で代替（ライブAPIは将来）

---

# バックエンド・トラック（MAGI判断層）

## B0 — 差分計画確定 ✅（→ `B0_DIFF_PLAN.md`）
- [x] 既存DAG（morning_batch）の MAGIゲート挿入点を図で確定
- [x] MAGI 4表のスキーマ＋ `decisions` とのFK設計（＋decisions に status/gendo_stance 追加）
- [x] 既存182テストを壊さない移行方針（再チューニング対象の洗い出し）
- [x] 既存 market_analyst 等の「LLMが数値を生成している箇所」を全列挙（9直接＋1間接）
- **重大発見**：`decisions` は現状どこでも生成されていない → `materialize_decisions` ノードを新設
- **Exit**：挿入点・追加表・テスト維持方針・違反点リストが固まる ✅

## B1 — 基盤再チューニング ⬜
- [ ] MCP8ツール出力に `source_refs`（出典）フィールドを追加
- [ ] 全数値に `data_asof`（取得時点）を付与
- [ ] 重要数値（株価等）の2ソース取得・照合（許容誤差は `DECISIONS.md`）
- [ ] 第1信用性フィルタ（EDINET：監査意見/上場廃止基準/特設注意/GC注記）を収集層に追加
- [ ] LLM層に審判別モデル割当を追加（MELCHIOR=Ollama / CASPER=Sonnet / BALTHASAR=コード＋LLM解釈）
- [ ] 既存182テスト green を維持確認
- **Exit**：1銘柄の実データが出典・時点付きでDBに入る／既存テストgreen

## B2 — MAGI 3審判（独立検証） ⬜
- [ ] `judge_verdict` テーブル追加・マイグレーション
- [ ] MELCHIOR（業績/ファンダ）：既存fundamentalsロジックを再構成。verdict＋reason＋confidence＋source_refs＋data_asof
- [ ] BALTHASAR（株価/テクニカル）：コード計算＋LLM解釈
- [ ] CASPER（文脈/イベント）：news/disclosure/topics を入力
- [ ] **独立性の担保**：3審判はプロンプト/関数を物理分離、互いの出力を入力に取らない
- [ ] 判定不能時 `verdict=na` を正直に出す（推測で埋めない）
- [ ] 単体テスト：審判が互いを入力に取らないことを検証／na が出ることを検証
- **Exit**：1銘柄で3審判が独立判定し、割れが出る

## B3 — 防御層（全層・機械照合） ⬜
- [ ] `verification` テーブル追加
- [ ] 数値照合：判定/推奨の各数値を実データと突合（一致=緑/不一致=赤＋`unverified_claims`）
- [ ] 出典実在確認：挙げた出典が実在し主張と整合するか
- [ ] 時点照合：as-of保持・古すぎ/混在で警告
- [ ] 決裁前ゲート：赤1つ→既定「保留」、割れ→既定「保留」
- [ ] 「分からない」を正直に出す（欠損を緑にしない）
- [ ] 単体テスト：故意の矛盾データ・偽出典・古い時点でフラグ赤になる
- **Exit**：故意の矛盾/偽出典/古時点でフラグ赤、決裁が保留既定になる

## B4 — 統合機構（割れ方・SCORE: NONE） ⬜
- [ ] `split_pattern` テーブル追加
- [ ] 割れ方を類型化（一致／業績◯株価✕／株価◯業績✕／文脈✕、※類型数は `DECISIONS.md`）
- [ ] 総合点を出さない（`SCORE: NONE`）
- **Exit**：割れ方が split_pattern に入る

## B5 — 碇司令（推奨＋反対論拠） ⬜
- [ ] `commander_rec` テーブル追加
- [ ] MAGI出力**だけ**を根拠に推奨を生成
- [ ] 「反対するなら：」を**必ず併記**
- [ ] 碇MAGI準拠チェック：MAGI外の新事実を述べたら `gendo_compliant=false`
- [ ] 単体テスト：碇のMAGI外創作が検出される
- **Exit**：碇がMAGI外創作した時に検出される

## B6 — DAG挿入＆状態遷移 ⬜
- [ ] morning_batch に MAGIゲートを挿入（analyst/sell-recの後、decisions公開前）
- [ ] 状態機械：verifying→verified→awaiting→approved/denied/held→order_listed→ordered→holding
- [ ] 検証完了まで decisions を「決裁待ち」にしない
- **Exit**：朝バッチが1銘柄をMAGI通過させ、decisions に検証結果が載る

---

# フロントエンド・トラック（Next.js / 狂いなく再現）

## F0 — golden master確定・スコープ棚卸し 🟡
- [x] `new_dashboard.html` を凍結基準として確定
- [x] **実装済み画面の棚卸し**（下記「F0棚卸し結果」参照）
- [x] スクリーニング/ポートフォリオ/週次/設定は **ナビのみ・実体なし**と確定（→ D-16/D-18）
- [x] 総合スコア痕跡の残存を検出（→ D-19）
- [ ] Playwright で各画面の基準スクショを取得（視覚回帰のベースライン。※F1のNext.js雛形後）
- **Exit**：再現対象スコープ確定（済）・基準スクショ取得（残）

### F0棚卸し結果（2026-05-22 確定）
**実装済み＝再現対象（ダッシュボード1枚＋詳細パネル4枚、ナビ切替JSなし）：**
- サイドバー：`sb-logo`／総資産+`sb-spark`／配分／今日のサマリー+`sb-cost-bar`／`sb-nav`(機能なし)
- アクションゾーン `.action-zone`：売り `.sell-zone`＋買い `.buy-zone`（各 `gendo-ind`／act項目／`magi-mini`／`more-toggle`+`more-list`）
- アラート `.alert`＋インフォ `.info-list`
- 保有 `.holdings-list > .hold`×5（AAPL/MSFT/TSLA/7203/6920、`hold-graph`=予測vs実績SVG、`hold-status-bar`）
- 予測の検証(Track Record)カード＋手動投入(Quick Analyze)カード
- トピックス：`topics-tab`絞込＋`.topic[data-cat]`
- 詳細パネル `data-panel`：`nvda`(買)/`sell-7203`(利確)/`sell-tsla`(損切)/`laser`(要検討)。各々 鼎立図SVG＋`magi-jrow`×3＋`magi-foot`(SCORE:NONE)＋`cmd-zone`＋`magi-decide`(flags/btns)＋reasons＋order
- JS：openPanel/closePanels、ESC/backdrop、relative time、price refresh sim、topics tab、more-toggle、hover tooltip。**ナビ/ビュー切替なし**

**実体なし＝新規設計が必要（→ F5）：** スクリーニング・ポートフォリオ・週次レポート・設定3種

**撤去/改修対象（総合スコア痕跡 → C1 / D-19）：** 詳細ヘッダ「スコア X/100」全4枚・`.mini-act-score`・Track Recordスコア相関・予測グラフ未照合バッジ欠落・topic「スコア76」表記

## F1 — Next.js雛形＋CSS/フォント逐語移植 ⬜
- [ ] Next.js プロジェクト初期化
- [ ] `<style>` 全体を globals.css へ逐語移植（Tailwind化しない・改名しない）
- [ ] フォント読込（Inter / Noto Sans JP / Noto Serif JP / JetBrains Mono、同ウェイト）
- [ ] ゴシック明示指定＋明朝3箇所のみ上書き（全面明朝化の罠回避）
- [ ] 静的描画でスクショ差分≈0 を確認
- **Exit**：静的描画で視覚回帰の差分が閾値内

## F2 — コンポーネント化（視覚回帰） ⬜
- [ ] **1コンポーネント移植ごとに視覚回帰を実行**し差分≈0を維持（DOM/クラスは逐語維持）
- [ ] Sidebar（ロゴ/総資産+spark/配分/今日のサマリー+コストバー/nav）
- [ ] ActionZone（売り `.sell-zone`／買い `.buy-zone`、gendo-ind・magi-mini・more-list 含む）
- [ ] Alerts＋Info
- [ ] Holdings（`.hold`×5、hold-graph SVG）
- [ ] TrackRecord ＋ ManualInput
- [ ] Topics（タブ絞込）
- [ ] DetailPanel（鼎立図/3審判/SCORE:NONE/碇ゾーン/検証フラグ/決裁、4種データ）
- **Exit**：全コンポーネントでスクショ差分が閾値内（スコア痕跡はこの時点では残したまま＝D-19）

## F3 — データ結線（モックAPI） ⬜
- [ ] ハードコードのモックを §5データ形（props/API）へ置換
- [ ] TypeScript型をAPIコントラクトに準拠
- [ ] モックAPIで動的描画（見た目は不変）
- **Exit**：モックAPIでUIが動的描画、見た目不変

## F4 — インタラクション再実装 ⬜
- [ ] 詳細パネル開閉（backdrop/ESC）・タブ切替・トピックフィルタ・more展開・ホバー・リフレッシュ
- [ ] 素JSの直移植でなく React state で実装
- [ ] **ナビのルーティング枠**を新設（モックにJSが無いため新規。ダッシュボードのみ実画面、他はプレースホルダ）
- **Exit**：全操作が動作、見た目不変

## F5 — 新規6画面のデザイン＋実装（MVP後） ⬜
> `new_dashboard.html` に実体がなく「再現」できない。同トンマナで新規デザイン（ver1 §5.6 / D-18）。
- [ ] スクリーニング
- [ ] ポートフォリオ
- [ ] 週次レポート
- [ ] 設定（戦略 / 口座連携 / エージェント）
- **Exit**：各画面が同トンマナで実装・データ結線される

---

# 結合フェーズ

## C1 — MAGI出力をUIへ結線 ⬜
- [ ] §5対応表に沿って実クラスへデータ流し込み
- [ ] 決裁ボタンの活性/非活性・既定保留ロジック
- [ ] **【UI変更ゲート】** スコア痕跡の撤去はユーザー再確認の上で実施（D-19。承認まではUI不変＝スコア残置）
- [ ] 総合スコア痕跡の撤去・改修（D-19）：(A)詳細ヘッダ「スコア X/100」全4枚 (B)`.mini-act-score` (C)Track Recordスコア相関 (D)予測グラフ未照合バッジ追加 (E)topic「スコア」表記
- [ ] スコア撤去後に視覚回帰を**再ベースライン**（意図的逸脱として記録）
- **Exit**：UIが実データで検証結果を表示し決裁できる

## C2 — E2E（NVDA 1銘柄・異常系） ⬜
- [ ] スクリーニング→3審判→防御層→統合→碇→UI→決裁 を通す
- [ ] 異常系：na→鼎立図点線／碇「Xが判定不能」／予算超過バナー
- **Exit**：1銘柄が全工程を通り、異常系も正しく表示

## C3 — ペーパー運用準備 ⬜
- [ ] Track Record 蓄積・週次レポート
- [ ] A/B育成は枠のみ（実装はデータ蓄積後）
- **Exit**：4週間ペーパー運用に入れる
