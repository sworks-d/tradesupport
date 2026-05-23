# Trading Agent 再構築計画 — 正本

最終更新：2026-05-22
位置づけ：**現行の唯一の実装計画**。`docs/ver1〜ver3`（claude.aiで作成した設計思想の履歴・参照元）の矛盾を解消する上位ドキュメント。

## このフォルダの構成

| ファイル | 役割 |
|---|---|
| `README.md`（本書） | 戦略の正本：方針・原則・アーキテクチャ・データモデル・APIコントラクト・再現戦略 |
| **`spec/`（設計の正典）** | **パイプライン実装計画の単一の正典**。00_overview（全体設計）→ P1..P6/X/U（フェーズ＋タスク設計、各タスクに全体ゴール/前引き継ぎ/目的/次引き渡し/ハルシネ防止/受入）。`spec/X1_reuse_inventory.md`＝外部OSS借用の棚卸し。**「何を作るか」はここが優先** |
| **`IMPROVEMENT_PLAN_FOR_CODE.md`** | **実行ロードマップ**（specを実コードに突き合わせ）。A群断線解消→B群反証層→C群OSS借用の着手順。**「どの順で実装するか」はここ** |
| `IMPLEMENTATION_PLAN.md` | WP単位の旧詳細（背景）。正は `spec/`＋`IMPROVEMENT_PLAN_FOR_CODE.md` |
| `TASKS.md` | **タスクシート（運用）**：全フェーズの進捗・チェックリスト・Exit条件 |
| `DECISIONS.md` | 意思決定ログ：着手前に潰す `[未確定]` 論点と確定内容 |
| `B0_DIFF_PLAN.md` | B0（MAGIゲート挿入）の差分設計 |
| `pipeline.html` | 収集→提案の過不足判定フロー図（ブラウザで開く） |

> `docs/ver1〜ver3` は **参照元として残置**（設計思想の出典）。実装の指示は本フォルダが優先。

---

## 0. 背景：3世代の docs と矛盾

| 版 | 思想 | 本計画での扱い |
|---|---|---|
| ver1 | 1AIが5軸→**総合スコア(84点)**。16〜17表・6エージェント。**実装済み（Phase 1.4 / 182テスト通過）** | 基盤は流用、判断層・UIは破棄 |
| ver2_magi | **MAGI三権独立**・総合スコア廃止(SCORE: NONE)・防御層・碇司令・人間決裁。`dashboard.html`＝最終UI | 思想の正本。UIの収束先 |
| ver3 | MAGIを「決裁直前ゲート」として後段挿入、既存を触るな | 「触るな」は破棄。思想（4段＋防御層）は採用 |

`new_dashboard.html` ≡ ver2 の `dashboard.html`（diff一致確認済み）＝ **UIの収束先**。

---

## 1. 確定方針（今回の決定・最優先）

1. **範囲＝A案**：基盤層は残し、**判断層（agents）とUIを刷新**。
2. **基盤は「再チューニング」**：MCP／LLM予算・コスト管理／DB／DAG／データ取得を、そのまま使わず **MAGI・ハルシネーション防御が要求する形（出典・時点・2ソース・数値はコードのみ）に作り変えて活かす**。
3. **フロント＝Next.js**。
4. **最優先制約：`new_dashboard.html` を狂いなく再現**（CSS・クラス名・フォント・DOMを逐語移植、視覚回帰で差分≈0を保証＝§7）。
5. ver3 の「既存を一切触るな」は破棄。ただし MAGI 4段と「数値はコードのみ」の思想は完全採用。

### 1.1 再チューニングの本質（重大な含意）
既存 `market-analyst` は LLM に**確信度・シナリオ目標株価**を生成させており、MAGI原則「数値はコードのみ・LLMに数値を作らせない」に**違反**する。
→「生かして再チューニング」は単なる移植ではなく、**この原則に適合させる作り変え**。LLMは「読む・解釈」役に限定し、数値・スコア・比率はコードが算出/取得/照合する。

---

## 2. 不変の原則

1. 主戦場は中期投資（Core-Satellite）。
2. **判断はMAGI（3審判の独立検証）、推奨は碇司令、決裁は人間。**
3. **MAGIは総合スコアを出さない。** UIに `SCORE: NONE`。
4. **数値はコードが照合した実データのみ。LLMに数値を生成・計算させない。**
5. Tier 1（手動発注）から始める。
6. ハルシネーションは「ゼロ化」でなく「混入前提で決裁直前に検出・遮断」。
7. 学習は人間主導A/Bロジック育成のみ（自己改変不採用）。Phase後半〜2で枠のみ。

---

## 3. アーキテクチャ：3層 × 扱いマトリクス

```
外部データ → [基盤:再チューニング] → [判断層:作り直し=MAGI] → [UI:作り直し=Next.js] → 人間決裁 → 手動発注
```

| 層 | 構成要素 | 扱い | 内容 |
|---|---|---|---|
| **基盤** | MCP8ツール | **再チューニング** | 出力に `source_refs`（出典）・`data_asof`（時点）を必須化。重要数値は2ソース取得・照合 |
| 基盤 | LLM層（router/budget/cost/HALT） | **流用＋微修正** | 審判別モデル割当（MELCHIOR=Ollama / CASPER=Sonnet / BALTHASAR=コード＋LLM解釈） |
| 基盤 | DB（17表）・Alembic | **流用＋追加** | 16表無改変。MAGI 4表追加（§4） |
| 基盤 | orchestrator | **流用＋挿入** | market-analyst/sell-recommender の後・decisionsをUIに出す前にMAGIゲート挿入 |
| 基盤 | 収集系agents（screening/topics/portfolio/manual_input） | **流用＋信用性フィルタ追加** | 収集・一次選抜は維持。第1信用性フィルタを追加 |
| **判断** | market_analyst（5軸→総合スコア） | **破棄→再チューニング** | 分析ロジックを**3審判に解体・再構成**。総合スコア廃止 |
| 判断 | sell_recommender の推奨 | **MAGI対象化** | 売り（利確/損切り）もMAGI検証を通す |
| 判断 | **MAGI 4段** | **新規** | 3審判（独立）→ 防御層 → 統合機構 → 碇司令 |
| **UI** | 素HTML/JS（ui/static） | **破棄** | — |
| UI | **Next.js アプリ** | **新規** | `new_dashboard.html` を逐語移植・コンポーネント化（§7） |

捨てるのは **②market-analystの総合スコア思想** と **③旧UI** のみ。配管は捨てない。

---

## 4. データモデル差分（既存表＝設計16/実装17 は無改変、MAGI 4表を追加）

```
judge_verdict   … decision_id, judge(MELCHIOR/BALTHASAR/CASPER),
                  verdict(buy/sell/hold/warn/na), confidence, reason,
                  source_refs(JSON 出典), data_asof(時点)
split_pattern   … decision_id, agree_count, total, label, interpretation
commander_rec   … decision_id, recommendation, counter_argument,
                  magi_compliant(bool), src_note
verification    … decision_id, figures_checked(bool), unverified_claims(JSON),
                  credibility_flag(ok/warn), gendo_compliant(bool), data_asof
```

- `decisions` に MAGI検証への参照（gendo_stance / split_pattern_id 等）。FK設計は実装時確定。
- `buy_signals.score` / `sell_signals.score` は**残すがUIに総合点として出さない**（A/B育成用）。
- 予測値（scenarios の bull/base/bear）は `is_model_generated=true` で保持。UIで「未照合」バッジ、碇の根拠から除外。

---

## 5. APIコントラクト（UI↔データの結線）

`new_dashboard.html` のDOMクラスを保持し、そこへデータを流す（ver3 §4準拠）：

| UI要素（実クラス） | 対応データ |
|---|---|
| `.gendo-ind .g-word`（推し/利確/撤退/要検討/静観） | decision.gendo_stance |
| `.magi-mini-dots` / `.magi-mini-state` | judge_verdict 3行 / split_pattern.label |
| `.magi-jrow`（詳細：3審判リスト） | judge_verdict |
| `.magi-foot .mf-state` / `.mf-note`（SCORE: NONE） | split_pattern |
| `.cmd-rec` / `.cmd-counter` / `.cmd-src` | commander_rec |
| `.magi-flag` ×3（数値照合/信用性/碇MAGI準拠） | verification |
| `.magi-btn` ×3（承認/否認/保留） | decision.status |
| 予測グラフ＋未照合バッジ | forecast(is_model_generated) |

- 検証完了まで決裁ボタンは**非活性**。未照合 or 割れがある時は既定を**「保留」**に。
- まず **TypeScript型＋モックAPI** でコントラクトを固定し、フロント／バックを並行開発。
- 旧前提（モックに残る総合スコア痕跡）は結合時に改修（D-19）＝**(A)詳細ヘッダ「○○スコア X/100」全4枚撤去 (B)`.mini-act-score`撤去 (C)Track Recordスコア相関の改修 (D)予測グラフに未照合バッジ追加 (E)topic-impact「スコア」表記改修**。※`.act-score`CSSはmarkup未使用の死蔵。

---

## 6. ロードマップ（2トラック並行 → 結合）

詳細チェックリストと進捗は **`TASKS.md`**。

- **バックエンド（MAGI）**：B0 差分計画 → B1 基盤再チューニング → B2 3審判 → B3 防御層 → B4 統合機構 → B5 碇司令 → B6 DAG挿入＆状態遷移
- **フロント（UI）**：F0 golden master確定 → F1 CSS/フォント逐語移植 → F2 コンポーネント化（視覚回帰） → F3 データ結線（モック） → F4 インタラクション
- **結合**：C1 MAGI出力をUIへ結線 → C2 E2E（NVDA 1銘柄） → C3 ペーパー運用準備

---

## 7. 「狂いなくHTML再現」戦略（最優先制約）

1. **逐語移植**：CSSは `new_dashboard.html` の `<style>` を globals.css にそのままコピー。クラス名・変数・配色・角丸を改名/再設計しない。Tailwind化しない。
2. **フォント完全一致**：Inter / Noto Sans JP / Noto Serif JP / JetBrains Mono を同ウェイトで読込。ゴシックは継承任せにせず明示、明朝は3箇所（審判固有名・碇の名・碇の発言）のみ上書き（全面明朝化の罠回避）。
3. **DOM保存**：コンポーネント分割しても出力マークアップ・クラスは元と一致。
4. **視覚回帰テスト（生命線）**：`new_dashboard.html` 正本と Next.js ルートを Playwright でスクショ比較。差分が閾値（≈0）超で fail。F1〜F4 各段で必ず実行。
5. **検証は実機で**（フォント事情がモックと異なる）。

### 7.1 再現スコープ（F0確定 2026-05-22）
`new_dashboard.html` で実装済みなのは **ダッシュボード1枚＋詳細パネル4枚のみ**（ナビ切替JSは存在しない＝事実上1画面のモック）。
- **再現対象（狂いなく）**：サイドバー／アクションゾーン(売り`.sell-zone`・買い`.buy-zone`)／アラート＋インフォ／保有`.hold`×5／予測の検証(Track Record)／手動投入／トピックス／詳細パネル(`nvda`/`sell-7203`/`sell-tsla`/`laser`)。
- **実体なし（ナビのみ・新規設計が必要）**：スクリーニング／ポートフォリオ／週次レポート／設定3種（戦略・口座連携・エージェント）。「銘柄詳細」は詳細パネルが実体。「手動投入」「過去のレコメンド」はダッシュボード内セクションで実装済み。
- これら6画面は「再現」できないため **MVP後に同トンマナで新規デザイン**（D-18）。再現と新規設計を混同しない。

### 7.2 再現と原則適合の分離（D-19・UIを勝手に変えない）
モックには総合スコア痕跡（詳細ヘッダ「スコア X/100」等）が残り SCORE: NONE 原則に反する。
- **F1/F2は UI を一切変えない**（モック完全一致・スコア痕跡も残す）。
- 唯一の UI 変更＝C1のスコア撤去は、**適用前にユーザー再確認（UI変更ゲート）**してから実施・再ベースライン。承認がない限りスコアは残す。

---

## 8. 品質方針

- 既存182テストを維持（再チューニングで壊さない／壊れたら原則に沿って直す）。
- MAGI各段に単体テスト（独立性＝審判が互いを入力に取らない、防御層＝矛盾/偽出典/古時点でフラグ）。
- UIは視覚回帰＋結線E2E。ruff / black / mypy --strict 維持。
- 全LLM呼び出しはコストロガー経由（日次500円/月5,000円で停止）。HALT・paper/live分離維持。
