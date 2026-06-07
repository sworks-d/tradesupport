# INVESTIGELION アーキテクチャ図（実コード準拠・ver 管理）

> **version: v1.1**（2026-06-07 / master 起稿・claude 配線差分修正）
> **目的**: ユーザー（CD）が全体パイプラインを一目で検証できるようにする。
> **正本ポリシー**: このファイルは実コードから導出する。コードと食い違ったら**コードが正**。
> 変更時は version を bump し、末尾の更新履歴に1行追記する。
> **owner**: UI（可視化担当）。初版は throttle 中の UI 代行で master が起稿。以後 UI が refine。

---

## 1. ゾーン構成（誰が何を担うか）

エヴァンゲリオン命名で6ゾーンが役割分担。

```mermaid
flowchart LR
    WILLE["WILLE<br/>投資哲学・運用方針"]
    KATSURAGI["KATSURAGI<br/>Treasury管理・予算配分<br/>安全装置(HALT/上限クランプ)"]
    ZEELE["ZEELE<br/>戦略プール(7 preset)<br/>銘柄分類"]
    AKAGI["AKAGI<br/>候補生成<br/>screening / market_analyst / ritsuko Brief"]
    MAGI["MAGI<br/>3審判(MELCHIOR/BALTHASAR/CASPER)<br/>+ Verification + 碇の構え"]
    MISATO["MISATO<br/>DS司令塔・予算配分<br/>銘柄→パイロット割当"]
    DS["DS 4機<br/>ペーパー並行検証<br/>(REI/ASUKA/KAWORU/SHINJI)"]

    WILLE --> KATSURAGI
    ZEELE --> AKAGI
    KATSURAGI --> AKAGI
    AKAGI --> MAGI
    MAGI --> KATSURAGI
    KATSURAGI --> MISATO
    MISATO --> DS
```

| ゾーン | 役割 | 主な実装 |
|---|---|---|
| WILLE | 投資哲学・運用方針（中期で負けない→利益を積む） | `trading_agent/wille/` |
| KATSURAGI | Treasury・予算配分・安全装置（HALT/上限クランプ）。WILLE ゾーンのオーケストレーター | `trading_agent/portfolio/`・`katsuragi_dispatch` |
| ZEELE | 戦略プール（7 preset で銘柄分類） | `trading_agent/screening/`（zeele_curator） |
| AKAGI | 候補生成（screening / market_analyst / ritsuko Brief） | `trading_agent/screening/`・`trading_agent/wille/ritsuko.py` |
| MAGI | 3審判＋Verification（判定・碇の構え） | `trading_agent/magi/` |
| MISATO | DS の司令塔（予算配分・銘柄→パイロット割当・昇格判定） | `scripts/misato_dispatch.py` |
| DS 4機 | ペーパー並行検証（本番稼働対象・将来自動決済） | DS パイロット |

---

## 2. 朝バッチ DAG（19 ノード・実コード準拠）

毎日 07:00 JST に launchd で自動実行。`trading_agent/orchestrator/morning_batch.py:903-987` の `nodes` 定義が正本。
`DAGExecutor` が `depends_on` に従い、依存の無いノードを並列実行する。

```mermaid
flowchart TD
    pre_check --> anomaly_check
    pre_check --> topics_collector
    pre_check --> universe_refresh
    topics_collector --> screening
    screening --> zeele_curator
    screening --> market_analyst
    zeele_curator --> zeele_llm_scout
    market_analyst --> materialize_decisions
    zeele_curator --> materialize_decisions
    zeele_llm_scout --> materialize_decisions
    materialize_decisions --> magi_verify
    magi_verify --> katsuragi_dispatch
    katsuragi_dispatch --> sell_recommender
    sell_recommender --> trailing_check
    trailing_check --> close_due
    close_due --> pyramid_check
    pyramid_check --> auto_fill
    auto_fill --> link_topics
    link_topics --> summary
    summary --> notify

    anomaly_check:::safety
    classDef safety fill:#fee,stroke:#c33;
```

| # | ノード | 依存 | 役割 |
|---|---|---|---|
| 1 | pre_check | — | 前処理・stale awaiting の繰越 cancel |
| 2 | anomaly_check | pre_check | 異常検知＋HALT（auto モードのみ発火）※安全装置・下流なし |
| 3 | topics_collector | pre_check | ニュース/開示トピック収集 |
| 4 | universe_refresh | pre_check | Universe 更新（並列ブランチ） |
| 5 | screening | topics_collector | 候補スクリーニング＋シグナルタグ導出 |
| 6 | zeele_curator | screening | ZEELE 7戦略への分類（決定論） |
| 7 | zeele_llm_scout | zeele_curator | screening 漏れを Haiku で発掘（BudgetGuard 日次¥10/月¥200） |
| 8 | market_analyst | screening | news_sentiment_score 供給（朝バッチ既定は決定論化＝¥0） |
| 9 | materialize_decisions | market_analyst, zeele_curator, zeele_llm_scout | 候補→Decision 実体化 |
| 10 | magi_verify | materialize_decisions | 3審判検証＋タグ合流＋(c)スコア採点（記録のみ） |
| 11 | katsuragi_dispatch | magi_verify | 候補プール統合（AKAGI Brief+DS+priority+opportunity fill） |
| 12 | sell_recommender | katsuragi_dispatch | 売却推奨 |
| 13 | trailing_check | sell_recommender | トレーリング・決算前 stop 厳格化（G-2） |
| 14 | close_due | trailing_check | 期限到来 sell Decision を即 close |
| 15 | pyramid_check | close_due | ピラミッディング判定 |
| 16 | auto_fill | pyramid_check | auto モードで approved Decision を即 fill |
| 17 | link_topics | auto_fill | Decision とトピックの紐付け |
| 18 | summary | link_topics | サマリ生成 |
| 19 | notify | summary | 発注リスト HTML 生成（＋paper auto fill シミュレーション） |

> 注: 新規 buy 候補は `materialize → magi_verify → katsuragi_dispatch` 経由で **status="awaiting"** のまま手動決済待ち（楽天 API 無しのため `automation_mode="manual"`）。`auto_fill` が即 fill するのは trailing/pyramid が登録した `approved` のみ。

---

## 3. ニュース/ファンダ → 利益 測定チェーン（mission の本線）

「ニュース・ファンダ情報を判断に接続し、勝ちエッジを前向きに測定する」中核経路。
**全段 record-only（売買・gate を一切変えない）**。下図は master が配線検証し、claude（news/feedback owner）が per-ticker news 経路・topics dead-end を差分修正した実体（2026-06-07）。

```mermaid
flowchart TD
    subgraph MAIN["本線（関数配線として繋がっている）"]
      jq["J-Quants statements<br/>構造化財務(予想/配当)"]
      pnews["per-ticker news<br/>(magi_verify の judge が ticker 毎に fetch)"]
      mat["materialize_decisions"] --> ver["magi_verify (ticker ループ)"]
      jq --> ver
      pnews --> ver
      ver --> der["derive_structured_event_tags<br/>derive_news_event_tags<br/>(persist.py:333→368)"]
      der --> tags["_apply_record_only_tags<br/>→ entry_signal_tags 合流(PIT正)"]
      tags --> score["_apply_event_score<br/>→ fundamental_event_score(全verified・無=50)"]
      score --> fill["fill 約定"]
      fill --> out["outcome<br/>hit/miss/neutral + actual_return"]
      out --> fwd["forward_diagnosis<br/>by_score_bucket(満期前 interim)"]
      out --> edge["edge_readout<br/>発火→評価→正味エッジ(mature)"]
      score --> edge
      fwd --> edge
      edge --> ui["UI ⑦ #phase-c-view"]
    end
    subgraph DEAD["topics 分岐（現状 未接続・dead-end）"]
      tc["topics_collector<br/>(汎用フィード・ticker 無)"] --> ttab["topics 表"]
      ttab --> lt["link_topics<br/>return skipped=True（no-op）"]
      lt -. 未接続 .-> xdec["decision へ未紐付<br/>supporting_topic_ids=0"]
    end

    score:::recordonly
    edge:::recordonly
    fwd:::recordonly
    lt:::dead
    classDef recordonly fill:#eef,stroke:#36c;
    classDef dead fill:#f5f5f5,stroke:#999,stroke-dasharray:4;
```

> **配線注記（claude owner 検証・差分修正）**:
> - news タグは **topics_collector からではなく magi_verify の judge が ticker 毎に引く per-ticker news** が駆動（`persist.py:333` NewsInput(tickers=[ticker]) → `:368` derive）。topics_collector は ticker 無しの**汎用フィード＝別経路**。
> - **topics_collector → topics 表 → link_topics は no-op（dead-end）**（`return {"skipped": True}`・Phase 1 で未接続）。汎用 topics は decision に未紐付（supporting_topic_ids=0）。**本線に乗るのは per-decision タグ経路のみ**。
> - 結論: 本線（財務 + per-ticker news → tag → score → fill → outcome → edge_readout）は繋がっている。未接続は **topics→decision 分岐だけ**。

### 段ごとの実体（コード参照）

| 段 | 関数 / 場所 | 入力 → 出力 |
|---|---|---|
| タグ導出 | **magi_verify 内** `magi/persist.py:333→368`（judge が per-ticker news を fetch）→ `derive_structured_event_tags` / `derive_news_event_tags` | 財務 + per-ticker news → `event_upward_revision` 等のタグ（topics_collector 経由ではない） |
| タグ合流 | `magi/persist.py:217-222` `_apply_record_only_tags` | タグ → `Decision.entry_signal_tags`（PIT正・backfillなし） |
| 採点 | `magi/persist.py:230` `_apply_event_score` → `screening/event_score.py` `compute_fundamental_event_score` | タグ → `fundamental_event_score`(0-100, 50中立) |
| バケット | `screening/event_score.py:63` `bucket_event_score` | score → low/mid/high/unscored（version ロック・誤バケットなし） |
| 前向き診断 | `reporting/forward_diagnosis.py` `by_score_bucket` | 評価済 → bucket×horizon(5/20/40/60d) 成績 |
| エッジ集計 | `scripts/phase_c_status.py:359` `_edge_readout` | 発火→評価→正味エッジを1ビュー統合 |
| 可視化 | UI `#phase-c-view` ⑦ブロック | `snapshot['edge_readout']` を忠実描画 |

### スコア式（event_score_v1・version ロック）

| タグ | 重み | 種別 |
|---|---|---|
| event_upward_revision / downward | ±25 | 構造化（J-Quants・低ノイズ） |
| event_dividend_hike / cut | ±15 | 構造化 |
| earnings_accel | +15 | 構造化 |
| news_positive / negative | ±8 | news 見出し（弱・単独では tail に届かない） |

cut point: low≤40 / mid 40-60 / high≥60（50中立帯）。**式・cut を変えたら version bump**（観測後の後出し調整=p-hacking を防ぐ）。

### 現データ状態（starved＝餌待ち・2026-06-07 時点）

本線は繋がっているが、各段はまだデータが薄い（forward runner 起動で解ける律速）：

| 段 | 現状 | 律速 |
|---|---|---|
| 構造化イベント発火 | starved（ほぼゼロ） | J-Quants rate-limit（fin_not_fetched）＋イベント希少性 |
| fundamental_event_score | (c)デプロイ後 verified のみ採点（backfill で公式28件は中立50に） | 新規イベントタグ付き decision の蓄積 |
| outcome（hit/miss） | 評価済 n=1（pending 188・主に8月満期） | 満期到達＋forward runner の interim マーク |

### small-n ガード（欺瞞防止）

評価済 n で status を明示し、UI は判定を持たずフラグを忠実描画する：

| 評価済 n | status | display_only | 意味 |
|---|---|---|---|
| < 30 | insufficient | True | 表示のみ・売買判断に使わない |
| 30–99 | exploratory | True | 探索的・売買判断に使わない |
| ≥ 100 | candidate | False | 候補（さらに期間分割の符号安定が前提） |

---

## 4. broker 構造（拡張可能 dispatcher）

```mermaid
flowchart TD
    disp["brokers/__init__.py<br/>broker_provider 別 dispatcher"]
    disp --> moomoo["moomoo.py<br/>OpenD 接続"]
    disp --> rakuten["rakuten.py<br/>DB ベース(楽天/SBI/モネックス)"]
    disp --> kabucom["kabucom.py<br/>Phase 2 placeholder"]
    disp --> standin["standin.py<br/>フォールバック"]
```

- 現状: `broker_mode="paper"`（試験運用）/ `broker_provider="rakuten"` / `automation_mode="manual"`。
- 将来の自動売買接続は対応 broker クラスを実装し dispatcher に1ブロック追加するだけ。

---

## 5. forward runner（前向き測定の起動）

- 既定 **OFF**（構築期間中の自動 yfinance 実行を防ぐ）。
- 起動: `Setting forward_diagnosis_enabled=true`（DB 書き換え＝ユーザー承認必須）＋ launchd plist load。手動実行は `scripts/forward_diagnosis.py --force`。
- 起動は**測定の時計を回し始める行為**。エッジが埋まるのは保有が満期(5/20/40/60d)を迎え評価が確定していくにつれて（現状 評価済 outcome は希少＝出力は unscored 中心が正常）。

---

## 更新履歴

| version | 日付 | 変更 | 担当 |
|---|---|---|---|
| v1 | 2026-06-06 | 初版。6ゾーン＋19ノード DAG＋news→利益チェーン＋broker＋forward runner を実コードから起稿 | master（UI 代行） |
| v1.1 | 2026-06-07 | §3 配線精度修正（claude 指摘）: news タグは per-ticker news 駆動（topics_collector 非経由）、topics→link_topics は no-op dead-end と明示、現データ状態(starved)表を追加 | master＋claude |
