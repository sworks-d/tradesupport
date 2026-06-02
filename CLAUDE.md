# INVESTIGELION（投資自動売買システム）

このリポジトリは、ユーザーが少額（¥10 万）から始めて段階的に大規模化することを目的とした **個人向け投資自動売買システム**。エヴァンゲリオン命名で WILLE / KATSURAGI / MAGI / ZEELE / DS 4 機が役割分担。

> 詳細な目的・設計指針は [docs/SYSTEM_PURPOSE.md](docs/SYSTEM_PURPOSE.md) を参照。新規 Claude Code セッションで最初に読むこと。

---

## 🎯 大指針（ユーザーから繰り返し表明された方針）

1. **少額でも問題なく運用を始められる**（¥10 万から）→ ¥100 万 → ¥1,000 万へスケール
2. **成長銘柄を追うロジック**（大型偏向ではなく中小型成長株）
3. **実運用で問題ない仕様**（致命的不具合を残したまま完了報告しない）
4. **既存ロジック尊重**（独自判断で SQL UPDATE しない、migration スクリプト経由）
5. **観測可能性最優先**（ノード success だけで動いてると判断しない、閾値発火率まで網羅検証）
6. **試験運用と楽天本番の並行運用**（broker_mode="paper" と "live" を分離）

---

## 📌 現状（2026-05-31 時点・v2.10 完成）

| 項目 | 値 |
|---|---|
| `broker_mode` | `paper`（試験運用継続中） |
| `broker_provider` | `rakuten`（楽天かぶミニ・単元未満株対応） |
| `automation_mode` | `manual`（楽天 API なしのため auto 不可、手動発注前提） |
| paper Treasury | ¥2,963（active 12 件 fill 後・実態と整合） |
| live Treasury | ¥0（楽天実残高待ち、入金されたら mark_filled で reflect） |
| 朝バッチ | 毎日 07:00 JST に launchd で自動実行（18 ノード DAG）+ paper auto fill ノード |
| 全テスト | **793 件パス・回帰なし** |

### v2.10 で完成した拡張可能 broker 構造

```
trading_agent/brokers/
  __init__.py    ← broker_provider 別 dispatcher（自動振り分け）
  base.py        ← Position / Account / BrokerUnavailable
  moomoo.py      ← OpenD 接続（broker_provider="moomoo" の時のみ）
  rakuten.py     ← DB ベース（mark_filled 経由・楽天/SBI/モネックス共通）
  kabucom.py     ← Phase 2 placeholder（kabu STATION API 用）
  standin.py     ← フォールバック
```

**将来の自動売買 (kabu.com 等) 接続時は対応 broker クラスを実装するだけ**。dispatcher の分岐に 1 ブロック追加で接続可能。

---

## 🏗️ アーキテクチャ概要

```
WILLE    ─ ユーザーの投資哲学・運用方針
KATSURAGI ─ Treasury 管理・予算配分・安全装置（HALT/上限クランプ）
AKAGI    ─ 候補生成（screening / ZEELE / market_analyst）
MAGI     ─ 3 審判（MELCHIOR/BALTHASAR/CASPER）＋ Verification + 碇の構え
ZEELE    ─ 戦略プール（7 preset で銘柄分類）
DS 4 機  ─ ペーパー並行検証（REI / ASUKA / KAWORU / SHINJI）
MISATO   ─ DS の司令塔・予算配分・銘柄→パイロット割当
```

### 朝バッチ DAG（18 ノード）

```
pre_check → anomaly_check → topics_collector / universe_refresh
  → screening → zeele_curator / market_analyst → sell_recommender
  → portfolio_builder → trailing_check → close_due → pyramid_check
  → auto_fill → materialize_decisions → magi_verify → link_topics
  → summary → notify (発注リスト HTML 生成)
```

---

## 🔁 朝の運用フロー（半自動運用）

```
07:00 JST  launchd 朝バッチ自動実行
            ├─ paper: auto_fill で試験運用シミュレーション
            └─ live:  発注リスト HTML 生成（autoreport/orders/YYYY-MM-DD.html）

08:30      iPhone で発注リスト開く
08:45-08:55 楽天証券アプリで順次発注（コピーボタンで ticker / 株数）
09:00      寄付で約定

09:30      Claude Code で報告:
           「今朝の発注完了
            3697 14株 702
            4587 9株 1107
            ...」
           → Claude が /purchase-report スキルで一括 mark_filled
```

---

## 🛠️ 利用可能な Skills（`/<name>` で呼び出し）

| Skill | 用途 |
|---|---|
| `/purchase-report` | 楽天証券での実発注報告 → DB に反映（試験運用/本番自動振り分け） |
| `/morning-status` | 朝バッチの結果確認、推奨銘柄一覧、Treasury 残、HALT 状態 |
| `/halt-recovery` | HALT 発火時の状態確認と解除（理由表示 + Discord 通知） |
| `/system-overview` | システム全体の構造を再確認したい時 |

各 Skill の詳細は `.claude/skills/<name>/SKILL.md` 参照。

---

## ✅ 実装済機能（v2.10）

### 基盤
- Phase J-1/J-2: automation_mode 基盤（manual/auto 切替、broker_mode と直交）
- Phase I-10: 異常検知 + HALT（DD ≤-15% / 日次 ≤-7% / 約定失敗 3 営業日連続）
- Phase I-11: Discord 通知（HALT/DD ブレーキ発火時）
- Phase I-12: 段階的実弾移行（tier_1 ¥10k → tier_5 full）
- broker_provider 設計: moomoo / sbi / rakuten / monex / fractional / kabucom
- rakuten_sim: 楽天かぶミニのコスト計算（寄付 0% / リアルタイム 0.22%）

### 安全装置・ハルシネーション防壁（11 防壁）
- G-2: trailing × earnings_guard 合成（決算前 stop 厳格化）
- G-3: paper_close_approved の Universe 照合
- H-6: correlation → fill 抑制（auto モード）
- H-7: DD ブレーキ
- Universe.is_active=False 銘柄を _buy_candidate_tickers / _candidate_tickers で除外

### 運用補助
- 予算優先フィルタ（少額時に小型銘柄を優先）
- 発注リスト HTML 生成（autoreport/orders/）
- 発注リストソート可（priority / expected_return / risk_reward / stance）
- mark_filled CLI（試験運用 / 楽天本番 自動振り分け + volume 妥当性チェック）
- 翌日繰越（pre_check で stale awaiting を自動 cancel）
- paper auto fill（notify ノードで毎朝自動シミュレーション継続）
- 発注差分検知 CLI（`scripts/check_order_diff.py` 楽天買い忘れ防止）
- データ整合性自動チェック（`scripts/check_integrity.py` I1-I6 検出）
- 損益サマリ + 分布（`scripts/pnl_summary.py` broker_mode × personality × strategy × sector × histogram）
- HALT 履歴可視化（`scripts/halt_history.py` 発火・解除タイムライン）
- broker_provider 別シミュレーション（rakuten_sim 寄付 0% / リアルタイム 0.22%）
- broker_mode 別 cost 集計（_active_portfolio_cost_jpy に broker_mode フィルタ）
- PilotAllocation 複合 PK（(pilot_name, broker_mode) で paper/live 独立配分管理）
- tier 制限を broker_mode="live" 全般に適用（楽天本番でも段階的実弾移行が効く）

---

## 🚧 別タスク（将来対応）

| 項目 | 優先度 | 内容 |
|---|---|---|
| screening 内部 signal 拡充 | 中 | ZEELE 7 戦略を真に分散させる（現状 alpha 78% / contrarian 21%） |
| news_sentiment LLM 実装 | 中 | market_analyst の固定値 50 を解消 |
| kabu STATION API 連携 | 低（Phase 2 移行時） | 完全自動売買への移行 |
| 楽天 CSV インポート | 低 | 週次の実約定 vs DB 整合性チェック |
| automation_mode / live_tier UI トグル | 低 | wille_settings.json 手動編集の脱却 |
| tier 昇格判定の自動化 | 低 | 連続日数・勝率・DD で半自動昇格 |
| PilotAllocation スキーマ修正 | 低 | (pilot_name, broker_mode) 複合 UNIQUE に変更 |

---

## ⚠️ 重要な運用ルール（必ず守る）

### コード変更
- 完了報告前に **致命度を厳密に再評価**（[[feedback_dont_declare_complete_with_critical_left]]）
- ノード success だけで「動いてる」と判断しない（[[feedback_verification_must_be_exhaustive]]）
- データ書き換えは **必ず既存スクリプト or migration 経由**（[[feedback_no_arbitrary_data_mutation]]）
- 致命的変更時は **ユーザー確認必須**（独自判断で SQL UPDATE しない）

### モード切替
- `broker_mode="live"` + `automation_mode="auto"` + `broker_provider="rakuten"` は **実行不可**（楽天 API なし）
- 必ず `automation_mode="manual"` で運用
- `broker_mode` 切替は `set_broker_mode()` 経由（直接 JSON 編集は最終手段）

### HALT 機構
- HALT は **安全装置**、自動解除しない
- 解除前に `~/.trading-agent/HALT` の reason を必ず確認
- 解除は `scripts/clear_halt.py` 経由（手動 `rm` は緊急時のみ）

---

## 📂 重要なファイル・ディレクトリ

```
trading_agent/
  orchestrator/morning_batch.py    朝バッチ DAG 定義
  reporting/order_list.py          発注リスト HTML 生成
  portfolio/anomaly_detector.py    異常検知 + HALT
  portfolio/rakuten_sim.py         楽天コスト計算
  portfolio/fill_simulator.py      broker_provider 別 dispatcher
  utils/lot_size.py                broker_mode / broker_provider / lot_size
  utils/time_utils.py              UTC/JST 変換（today_jst が業務日付の正本）

scripts/
  run_morning_batch.py             朝バッチ手動実行
  mark_filled.py                   発注完了マーク（試験/本番切替 + volume チェック）
  auto_fill_paper.py               paper モード自動 fill（試験運用継続）
  generate_order_list.py           発注リスト単発生成
  clear_halt.py                    HALT 解除
  halt_history.py                  HALT 発火・解除履歴可視化（P17）
  check_integrity.py               データ整合性チェック（P8 / I1-I6）
  check_order_diff.py              発注実績 vs 推奨差分検知（P14・買い忘れ防止）
  pnl_summary.py                   損益サマリ + 分布（P5/P7・broker_mode × グループ別）
  misato_dispatch.py               DS 4 機の dispatch（--cleanup-fresh で完全リセット）

docs/
  SYSTEM_PURPOSE.md                システム目的・設計指針
  PURCHASE_REPORTING.md            購入報告フォーマット
  HALT_RECOVERY.md                 HALT 復旧手順
  OPERATIONS_SCHEDULING.md         朝バッチ・cron 運用

data/
  trading.sqlite                   メイン DB
  wille_settings.json              broker_mode / broker_provider 等の設定

autoreport/
  orders/YYYY-MM-DD.html           朝の発注リスト（スマホ最適化）
  daily/YYYY-MM-DD.html            日次レポート

.claude/skills/                    Claude Code Skills
~/.claude/projects/-Users-shotaro-tradesupport/memory/  プロジェクトメモリ
```

---

## 🔗 引き継ぎメモリ（最重要）

新規セッション開始時に以下のメモリも自動読み込みされます：

- [[project_system_purpose]] — システム最終目的（¥10 万から大規模化、成長銘柄ピック）
- [[feedback_verification_must_be_exhaustive]] — 検証は閾値発火率・分布まで網羅的に
- [[feedback_no_arbitrary_data_mutation]] — 独自 SQL UPDATE 禁止
- [[feedback_dont_declare_complete_with_critical_left]] — 致命残しで完了報告しない
- [[feedback_pipeline_observability]] — 閾値を spec のまま実装しない、上流データで発火確認
- [[jp_ticker_suffix]] — yfinance には `to_yfinance_symbol(ticker)` 経由
- [[misato_orchestrator]] — DS の司令塔、1 命令上限 ¥500k、dry-run→approve 2 段ゲート

---

## 🤝 Claude の振る舞い指針

ユーザー（クリエイティブディレクター）の方針に従う：

- お世辞・過剰な肯定は不要
- 論理の弱さ・前提の甘さ・解像度の低さがあれば率直に指摘
- 「完璧」「問題ない」と安易に言わない（改善余地・未解決点を必ず示す）
- 分析は感想で終わらせず、良し悪し・理由・改善方向性まで掘り下げる
- 修正完了報告前に **必ず** 致命度の厳密再評価
- フェーズを意識（構想・整理・設計・実装のどの段階か明確化）
