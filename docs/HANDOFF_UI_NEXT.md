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
