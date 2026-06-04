# セッション引き継ぎ：UI 改修（発注・ポートフォリオ・リアルタイム損益）

**発行**: 2026-06-02 / **対象**: UI 改修を担当する次セッション（別セッションで並行実施）
**前提コミット**: `834b37e`（計測基盤 + A+ + backtest 基盤。UI とは独立）
**並行作業**: バックテスト（J-Quants PIT・別トラック）は backend 側で進行中。UI とは疎結合。

---

## 0. 目的（ユーザーが明示した核心ニーズ）

INVESTIGELION の朝の運用を **シンプル・低ミス**にする UI。ユーザー（＝発注代行者）の言葉：

- 「**最終的に俺がなんの銘柄を何件、いくらで売る・買うがわかるように**して」
- 「**俺のタスクが増えればミスの可能性が増える**ので、なるべくシンプルに」
- 「**購入から価格反映、検証までがスムーズに**いくように、共有もなるべくシンプルに」
- 「**ポートフォリオをわかりやすく整理**して」
- 「**リアルタイムでの損益**も見えるように」

---

## 1. UI の役割 = 3 局面を最小手数で繋ぐ

```
① 発注ビュー   何を・何株・いくらで 買う/売る が一目（朝・発注前）
       ↓ ユーザーが楽天証券アプリで発注
② 購入報告     約定を最小手数で伝える（数値入力を極小化）
       ↓
③ 反映→検証    mark_filled → 価格反映 → evaluation が自動で流れる
＋ ポートフォリオ + リアルタイム損益（常時・保有と成績が一目）
```

---

## 2. 設計原則（リジェクト回避のため厳守）

1. **ユーザーのアクションを最小化**：見る → 発注 → 1 タップ報告。判断・株数計算・数値入力はシステム側に寄せる
2. **1 画面完結**：発注リスト＝報告画面＝ポートフォリオを行き来させない（コンテキスト切替がミス源）
3. **[[ui_f0_master_fidelity]] 厳守**：動的拡張時は `ui/app/dashboard.html` の HTML 構造／クラス／forecast グラフを必ず踏襲（過去に複数回リジェクト）
4. **paper / live 分離を明示**：broker_mode で試験運用と楽天本番を混ぜない

---

## 3. 既存資産（再発明しない・これを土台に）

### backend（データはほぼ揃っている）
| 機能 | 実装 | 備考 |
|---|---|---|
| 発注リスト生成 | `trading_agent/reporting/order_list.py`（`build_order_items` / `generate_order_list`） | ticker/推奨株数/現在価格/stop/1R損失（G-4）を計算済。出力 `autoreport/orders/YYYY-MM-DD.html` |
| 購入報告→DB反映 | `scripts/mark_filled.py` + `.claude/skills/purchase-report/` | **A7 実装済**：fill 時に entry_date / entry_market_regime / filled_via を自動で刻む → evaluation にそのまま乗る |
| ポートフォリオ + 損益 | `scripts/build_snapshot.py`（L315-343 付近） | **per-holding の cost / 現在値 / unrealized / unrealized_pct / stage、broker_mode 別の合計 pnl / pnl_pct を計算済**。current_price 取得済 |
| 損益集計 | `trading_agent/reporting/pnl_analytics.py`（`aggregate_pnl`）+ `scripts/pnl_summary.py` | broker_mode × personality × strategy × sector × histogram |
| 評価/ゲート | `trading_agent/evaluation/job.py` / `scripts/check_gate.py` | track record・増額ゲート⑥（n≥30/命中≥50%/平均R≥0.5/DD≤15%/コスト後α>0/両局面） |

### frontend（未コミットの WIP・要レビュー）
- `ui/app/`（Next.js）：`LiveData.tsx` / `DashboardInteractions.tsx` / `CashFlowPanel.tsx` / `DisciplineBanner.tsx` / `ZeelePanel.tsx` / `DummySystemPanel.tsx` / `BrokerModeToggle.tsx` / `dashboard.html` / `api/`
- **これらは `834b37e` に含めていない**（UI は別セッション・私は未レビュー）。UI セッションが内容確認の上で土台にするか判断
- 5 分自動更新 + 手動ボタン（CLAUDE.md 記載）

→ **「ポートフォリオ整理 + リアルタイム損益」は backend が既に計算しているので、主にフロントの見せ方 + 自動更新の課題**。ゼロから損益計算を書かない。

---

## 4. 未確定の設計判断（UI セッション開始時にユーザーと確定）

1. **発注の実行場所**：楽天証券アプリで手動発注（現行）のままか
2. **購入報告の入口**：発注ビューに「✓約定」ボタンを付け、リスト価格通りなら **1 タップ（数値入力ゼロ）**で mark_filled する案 vs 現行 /purchase-report（チャット報告）を残す/簡素化
   - 理想：全部リスト通りなら「**全約定**」1 ボタン、価格が違った注文だけ実価格を修正
3. **土台**：Next.js ダッシュボード（`ui/app`）を磨くか、軽量 HTML（`autoreport/orders/`）を主にするか
4. **リアルタイム損益の価格ソース**：**yfinance（現在値）を使う**。⚠ J-Quants Free は 12 週遅延でリアルタイム不可（backtest 専用）。既存 build_snapshot は current_price 取得済なのでそれを 5 分更新で

---

## 5. ポートフォリオ + 損益ビューの推奨項目（build_snapshot のデータで作れる）

- **per-holding**：🟢/🔴・ticker・社名・株数・取得単価・現在値・**含み損益(¥/%)**・stop価格・target・保有日数・pilot/strategy
- **合計**：取得額・現在評価額・**総含み損益(¥/%)**・現金・総 equity
- **paper / live を分けて表示**（並行運用）
- リアルタイム：5 分自動更新（既存）+ 手動更新ボタン

---

## 6. このセッションでやらなかったこと（UI セッションへ）

- 上記 UI 改修は**未着手**（要件整理のみ）。
- `ui/app/*` の未コミット WIP は内容未レビュー。コミット方針は UI セッションで判断。

## 7. 並行トラック（backtest・参考）

- backtest は別途進行：BT-0/BT-1 + 価格キャッシュ完成（`834b37e`）。J-Quants Free レート回復後に `--max-fetch 5 --throttle 3` で cache 蓄積 → `--cache-only` で初の有効値。詳細は別ハンドオフ/会話。
- UI と backtest は疎結合なので**並行で衝突しない**。

---

## 8. 関連メモリ（UI セッションは先に読む）

- [[ui_f0_master_fidelity]] — dashboard.html の構造/クラス踏襲（最重要・リジェクト回避）
- [[project_system_purpose]] — ¥10万から段階大規模化・実運用品質
- [[ds_operation_intent]] — ユーザーは DS の代理として発注代行（ユーザー≠KATSURAGI）
- [[feedback_proposal_format]] / [[feedback_proposal_self_review]] — UI 提案時のメリデメ・自己レビュー

---

## 9. 【2026-06-03 追記】paper/live 役割分担 + ¥10万→¥100万 段階大規模化（UI 反映必須）

backend で broker_mode 分離 + Phase C 段階大規模化を実装済（commit 予定）。UI は以下を**正しく反映**すること。

### 9-1. 確定した役割分担（ユーザー方針）

| | paper（試験） | live（本番） |
|---|---|---|
| 何 | DS が「ユーザーが手動売買した想定」を**自動 fill** してシステムの edge を検証 | ユーザーが**実楽天で手動発注**→ `/purchase-report` で報告 |
| 約定経路 | 原則 `filled_via=ds_dispatch`（手動 paper 報告は `broker_mode=paper`+`manual`・`--force` 原則禁止） | `filled_via=manual` |
| 自動売買 | あり（auto_trade ON） | なし（手動のみ） |
| 口座・運用枠 | **口座総額 ¥100万 固定** / **deploy 解放枠 ¥10万 start** → ゲート⑥通過で段階的に ¥100万 へ解放（unlock 方式・§9-3） | 楽天実残高 |
| 意味 | システムの強さの証明 | 実運用の実績 |

⚠ **paper と live は別トラック。UI で絶対に混ぜない**。ゲート⑥（増額判断）も別々に見せる。
⚠ paper は「口座資本 ¥10万」ではなく「**口座 ¥100万・初期 deploy 上限 ¥10万**」（混同しない・codex 指摘）。

### 9-2. ゲート⑥は broker_mode 別に表示（混在汚染防止・codex 指摘）

- `official_gate_evaluation(engine, broker_mode="paper")` と `(..., broker_mode="live")` を**別パネル**で表示。
- combined（`combined_gate_reference(engine)`）は **「参考のみ・増額不可」ラベル必須**（`actionable=False`）。combined の passed を増額根拠に見せてはいけない。
- CLI: `scripts/check_gate.py`（paper+live+combined 表示）、`--paper` / `--live` で個別。
- 各 `GateResult` に `.broker_mode` / `.actionable` フィールドあり。

### 9-3. 段階大規模化ウィジェット（paper 専用・**unlock 方式**・founding purpose の可視化）

⚠ 設計確定（codex 推奨・2026-06-03）：**deposit ではなく unlock 方式**。paper は口座総額 ¥100万 を
最初から固定で置き（資本注入で equity 曲線を歪めない）、実際に deploy してよい上限だけを段階解放する。

`treasury_view(engine, "paper")` が返す（実装済）:
- `account_capital_jpy` — 口座総額（¥100万・固定）
- `current_risk_budget_jpy` — **解放済み deploy 上限**（¥10万 start → gate⑥通過で解放）
- `active_exposure_jpy` — 使用中（active paper 取得コスト）
- `deployable_jpy` — 今 deploy 可能（= 解放上限 − exposure）
- `target_ceiling_jpy` — 解放上限（¥100万）/ `ceiling_progress_pct` — 解放率（%）

→ **「口座 ¥100万 / 解放済み ¥X（Y%）/ 使用中 ¥Z / deploy可能 ¥W」** を出す。ladder = 10→30→60→100万（unlock）。
ゲート⑥(paper)通過ごとに `scripts/misato_dispatch.py --advance-paper` で次 tier を解放（履歴は `treasury_injection` 台帳：amount=解放差分 / tier_after=解放後上限 / reason）。

### 9-4. レポートボタン（サイドカラム・ユーザー指示 2026-06-03）

ユーザー要望：**サイドカラムに「レポート」ボタンを設置し、押すと Phase C 現況ビューへ遷移**して中身が分かるようにする。**常に最新ステータスが反映**されること（2026-06-03 追加指示）。

- **データソース（確定）：`snapshot.json` の `phase_c` キー**。`build_snapshot.py` が **phase_c を常駐で載せる**実装済（朝バッチ + **5分自動更新** + 手動更新で再生成 → UI は常に最新）。**UI は別 fetch 不要・snapshot を読むだけ**。
  - 単発確認/archive 用に `scripts/phase_c_status.py --json`（純 JSON・**コスト0・DB のみ・価格 fetch なし**）/ `--archive`（`autoreport/phase_c/YYYY-MM-DD.json` 日次履歴）もある。
  - `snapshot.json.phase_c` の中身は `build_phase_c_status` と同一 dict（下記キー）。
- JSON 構造（`snapshot.json.phase_c`）：
  - `intent`（treasury paper/live・auto_trade・halt）
  - `pnl_realized`（broker_mode 別・**`official` と `legacy_reference` に分離**＝legacy closed を確定取引と混ぜない）
  - `gates`（paper・live・combined_reference 各 criteria・`actionable`）
  - `pilot_performance_paper` / `fix_direction_paper`（failing_criteria・promotions）
  - **`decisions_detail_paper`**（公式 paper 評価済み明細：ticker/action/filled_via/entry・exit/stop・target/actual_return/R/benchmark/hit_or_miss/regime/thesis）
  - **`open_positions_paper`**（評価前の active 保有：ticker/qty/buy_price/target_date/evaluation_date/stop/thesis/personality・価格 fetch なし）
  - **`breakdowns_paper`**（exit理由別 / stance別 / 局面別 の n・命中・avgR）
  - **`feedback_transparency`**（pilot_multipliers の multiplier/accuracy/evaluated/reason＝なぜこの機体に予算が寄るか）
  - `data_breakdown`（status/filled_via/broker_mode 内訳・`None`→`legacy(None)`）
- 遷移ビューのブロック（CLI と同構成）：
  ① 実行意図 ② 損益（official/legacy 分離）③ 分析（ゲート⑥ paper/live）④ 修正の方向性 ⑤ 要素データ ⑥ FB 透明性（配分根拠）⑦ 成績パターン（exit/stance/局面別）+ Decision 明細・評価前保有。
- combined は `actionable=false` を**「参考・増額不可」明示**で。`fix_direction_paper.failing_criteria` を「次にやること」として目立たせる。
- [[ui_f0_master_fidelity]] 厳守：サイドカラム/ボタン/遷移は dashboard.html の既存構造・クラスを踏襲（新規ページでなく既存パネル遷移が無難）。
- 含み損益・現在評価額は従来どおり build_snapshot（要価格）側。phase_c_status は確定値のみ（cost0）。

### 9-5. データソース早見

| 表示したいもの | 取得元 |
|---|---|
| Phase C 統合現況（推奨） | `scripts/phase_c_status.py --json` / `build_phase_c_status(engine)` |
| paper/live ゲート⑥ | `official_gate_evaluation(engine, broker_mode=...)` → `GateResult.summary()` / `.criteria` |
| combined 参考 | `combined_gate_reference(engine)`（actionable=False を明示） |
| paper unlock 状態 | `treasury_view(engine, "paper")` の account_capital / current_risk_budget / deployable / ceiling |
| unlock 履歴 | `treasury_injection` テーブル（amount_jpy=解放差分 / tier_after_jpy / reason / created_at） |
| 保有・含み損益（既存） | `build_snapshot.py`（broker_mode 別に既に分離・§3 参照・要価格） |

### 9-6. 注意

- `filled_via=manual` は **live 専用ではない**（paper/manual もあり得る）。トラック判定は **broker_mode** で行う（filled_via 単独で live と判定しない）。
- unlock は資本注入ではない（口座総額固定）。だが解放上限内の deployed capital に対する R/return で edge を見る。口座総額 ¥100万 全体の P&L で勝ち判定すると未解放 cash で希薄化するので使わない（gate⑥ は解放枠内の decisions で判定）。
- 実 equity DD は backend で別途実装予定（現状ゲートは評価列 proxy）。

### 9-7. 既知の暫定/未配線（UI で「実データ」と誤読させない・2026-06-03 監査 by codex）

backend 監査で判明。UI はこれらを「最新の実データ」として出さず、注記/ラベルを付ける：

- **`pnl_realized[mode]`**：`official`/`legacy_reference` の入れ子。**後方互換でフラット `n/pnl_jpy/wins`（=official）も併存**。UI は official（公式 Phase C 確定）と legacy_reference（cleanup 等・参考・公式対象外）を**別表示**。`official_decision_n`(=gate n 基準) と `official_fill_record_n`(=明細粒度) を区別。
- **`candidates`**：card_id→候補の**純 map**（メタは入れない）。5分自動更新（`--light`）では**前回（朝バッチ）生成を再利用**。stale 情報は **`candidates_meta: {stale, source_generated_at}`**（candidates dict とは別キー）にあるので、UI は `candidates_meta.stale=true` 時に「候補は朝の生成（as of `source_generated_at`）」と明示。価格/MAGI/judge は最新ではない。⚠ 件数は `candidates` のキー数で数える（メタは混入しない）。
- **`allocation.posture_used` / `posture_source`**：posture は exposure_decision 由来に配線済だが、exposure 入力（breadth/uptrend 等）が**未配線で LOW confidence** の間は保守側（REDUCE_ONLY 寄り）。`posture_source` が `fixed_safe_default` なら「暫定」と表示し意思決定根拠にしない。
- **`exposure`**：5入力中 portfolio_dd_pct のみ配線・他は未配線（X-2C 待ち）。`inputs_missing` を UI で「暫定/低信頼」と明示。
- **`holding_health`**：T2(配当)/T5(決算サプライズ)は**暫定スキップ**（X-2B 待ち）。T1/T3/T4 のみ。UI で coverage を明示。
- **`news_sentiment`（market_analyst 経路）**：news MCP の見出しを keyword 分類(+/-/0)→(pos-neg)/total を 0-100 にマップした**決定論スコア**（cost0）。**ニュース無し/取得失敗/方向性なし時のみ中立 50**。LLM ブレンドは将来拡張（旧「固定50ダミー」は解消済）。
- **`usdjpy`**：fetch 失敗時 0.0 になり US 銘柄が ¥0 化し得る（backend 修正候補）。UI は usdjpy=0/null 時に US 金額を信用しない。

### 9-8. データ鮮度/更新伝播（codex 監査 2026-06-04・「価格が反映されない」の同種問題）

snapshot は複数経路で部分更新される。UI は「どのセクションがいつ更新されたか」を区別すること：

- **更新経路と範囲**（backend 修正済の前提）：
  - `/api/refresh-prices`（`refresh_prices.py`）= **top-level holdings + dummy_system holdings の価格/含み損益** + `generated_at`/`prices_refreshed_at` 更新。**account 総資産・candidates・phase_c・allocation は更新しない**。
  - `/api/refresh-holdings`（`refresh_holdings.py`）= holdings/pending/account + **phase_c/scaling/gates 再生成（約定後の Phase C レポート更新・追加済）**。dummy_system は更新しない。
  - `/api/refresh`（`build_snapshot.py`）= full（重い・LLM 含み得る）。「完全再生成」専用に。通常は refresh-prices/holdings。
  - 朝バッチ 07:00 / daily-report 18:00 = full 再生成。**それ以外の intraday 定期 full 再生成は無い**。
- **`generated_at` の意味が経路で混在**（full生成時刻 / 価格更新時刻 / 保有更新時刻を上書き共有）。UI は **価格鮮度は `prices_refreshed_at`、全体鮮度は `generated_at`** と分けて表示し、「全体が最新」と誤認させない。
- **`candidates_meta: {stale, source_generated_at}`** に分離済（候補件数バグ回避）。`candidates` は card_id→候補の純 map。`--light` 時は `candidates_meta.stale=true`＝「候補は朝の生成」と表示。
- **自動価格ループの伝播**：`DummySystemPanel` の自動更新は自 state のみ。**メイン holdings/サイドバーに伝播しない**（LiveData は mount 時 fetch）。→ 自動更新後に共有 store/イベントで LiveData も再描画 or 軽量更新を central fetch/reload に統一。
- **account 総資産が時価連動でない**：価格は動くのに総資産が動かない見え方。`account` に時価ベース総資産/含み損益を追加するか「取得原価ベース」とラベル明示。
- **更新失敗/クールダウン**：refresh-prices が no-holdings/cooldown で snapshot を書かない → UI に「更新失敗中」が出ない。API response の status をUIで表示。
