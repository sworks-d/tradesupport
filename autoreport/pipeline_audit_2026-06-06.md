# INVESTIGELION パイプライン監査レポート

- **監査者**: pipelineCK（Claude Code・investigelion team）
- **日付**: 2026-06-06
- **評価軸（北極星）**: 中期株式投資で「負けない・利益をあげる」= **勝つこと**。全タスク・全ノードがこの一点に接続しているかで判定。
- **手法**: read-only 静的解析（5領域に分けて並列深掘り）+ DB SELECT 実測 + 主要主張のコード/データ直接再検証。**バッチ実行・LLM呼び出し・DB書き換え・ファイル編集は一切なし。**
- **対象**: 朝バッチ DAG 実16ノード（`trading_agent/orchestrator/morning_batch.py` 正本）+ ds_dispatch（launchd 07:15）

## 確信度の表記
- **✔確証(再検証済)**: pipelineCK が DB+コードで独立に再検証
- **◐ 証拠あり**: サブ監査が file:line 提示・整合的、pipelineCK 未再検証
- **? 疑い**: 要追加検証（ネット/snapshot 依存等で未裏取り）

---

## エグゼクティブサマリ

骨格は妥当（fill は ds_dispatch で機能、MAGI 質ゲート、安全装置は存在）。だが「勝つこと」への接続で **3 系統の致命的断線**がある。

1. **勝率を検証する土台が壊れている（観測性）** — 勝っているか負けているかを*そもそも測れない*。
   - analysis_logs が共有 invocation_id で壊れ、6エージェントが success 0・永久 running（X-2 ✔）
   - 評価ループが 2026-08月まで凍結、track record が空（E5 ✔）
   - sell_signals が一度も生成されていない＝質的売り判断が死（E1 ✔）

2. **「負けない」の出口が live で断線（損失回避）** — 損切り機構が実口座に届かない。
   - 発注リストが買いのみ、売り指示が出ない（E2 ✔）
   - HALT 中は close_due が止まり損切りが執行されない（E9 ✔）
   - trailing/pyramid が broker_mode="paper" ハードコードで live 保有を監視しない（E3 ◐）

3. **入口の精度頭打ち + 勝ちに寄与しないコスト（利益・無駄）** — 良い銘柄を拾えず、悪い銘柄にコストを払う。
   - screening が composite 40 頭打ちで value_trap を上位推奨（EPS未配線が根因）（U-02/U-04）
   - ZEELE 7戦略が実態 alpha+contrarian 82% に縮退、3戦略は 0 件（Z-1 ✔）
   - market_analyst が全課金78%（¥908）を使うが、詳細分析の大部分が最終判断に還元されない高コスト経路（候補順序には接続あり・B-4 codex 補正済）
   - zeele_llm_scout が構築期間中も毎日課金、主張される ¥10/¥200 ガードは実在しない（Z-3/Z-4 ✔）

---

## 【ゴール接続マップ】16ノード × 「勝つこと」への接続

| # | ノード | 接続 | 一行根拠 |
|---|---|---|---|
| 1 | pre_check | ○間接 | API設定確認 + 前日 awaiting auto-cancel（衛生）|
| 2 | anomaly_check | ◎直結 | DD/日次ブレーキ。「負けない」一次防衛（※paper では HALT 不発 U-05）|
| 3 | topics_collector | △弱 | 課金¥240/812call。出力 Topic の per-ticker 紐付けがほぼ死（U-01）|
| 4 | universe_refresh | ✕無 | `{"skipped":True}` 完全 no-op |
| 5 | screening | ◎直結 | 候補生成の起点。だが composite 40 頭打ち（U-02/U-04）|
| 6 | zeele_curator | ○間接 | 連続入賞を ZeeleState 化。pool に流入（接続は健全）|
| 7 | zeele_llm_scout | ○間接 | 探索 upsert は pool に乗る。ただし構築期間中課金 + 虚偽ガード（Z-3/Z-4）|
| 8 | market_analyst | △弱 | **¥908（全課金78%）。BuySignal.score は候補10件の選定・順序に効くが、詳細分析（シナリオ等）は最終判断で未使用**（B-4）|
| 9 | materialize_decisions | ◎直結 | 候補を Decision(verifying) 化 |
| 10 | magi_verify | ◎直結 | 3審判質ゲート。LLM課金0は確証（V-03）。業種別閾値は死（V-04）|
| 11 | katsuragi_dispatch | ◎直結 | 買い意思決定の中核。接続健全 |
| 12 | sell_recommender | △弱 | SellSignal 生成するが執行に未配線・テーブル0件（E1）|
| 13 | trailing_check | ◎直結 | trailing stop。「負けない」中核。だが paper ハードコード（E3）|
| 14 | close_due | ◎直結 | 損切り実行。HALT 中 skip が問題（E9）|
| 15 | pyramid_check | ○間接 | 利伸ばし。paper ハードコード（E3）|
| 16 | auto_fill | △弱(死) | automation_mode=manual で常時 skip（X-6）|
| (link_topics) | ✕無 | `{"skipped":True}` no-op。thesis 追跡データ死蔵 |
| (notify) | ○間接 | 発注リスト生成（live 執行の唯一の出口）+ paper_auto_fill（Phase C で正しく skip）|

**実 fill は DAG 外の ds_dispatch（launchd 07:15, `misato_dispatch.py --auto-only`）が担う（E4 ✔）。** ただし auto_trade トグル（pilot_allocation.auto_trade_until = **2026-07-04**）失効後は dry-run 化し fill 停止。

---

## A. 致命（勝敗の根幹を壊す）

### [A-1 / X-2] analysis_logs が壊れ、勝率検証の台帳が機能していない ✔確証
- **分類**: 観測性 / **重大度: 致命**
- **証拠**: `agents/base.py:94-95` `_log_end` は `select(AnalysisLog).where(invocation_id==X).first()` で**先頭1行のみ更新**。朝バッチは全エージェントが同一 `invocation_id="morning_<date>"`（`morning_batch.py:251`）を共有。→ 全 agent の `_log_end` が先頭（topics_collector）の行を上書き。
- **DB 実測**: market_analyst running22/success0、screening_agent running25/success0、sell_recommender running21/success0、zeele_curator running18/success0、zeele_llm_scout running3/success0、portfolio_builder running16/success0。**topics_collector のみ success10**（DAG 先頭のため）。
- **勝敗影響**: 個別ノードの成否・コスト・出力・エラーが per-agent で追えない。ユーザー最重視の「ノード success だけで動作判定しない／網羅検証」（[[feedback_verification_must_be_exhaustive]]）の土台そのものが嘘をつく。batch_states の "全 success" は DAG 戻り値であってエージェント内部成否を保証しない。**勝てているかを検証する手段が壊れている。**
- **推奨**: `_log_end` を `invocation_id + agent` で絞る、または `_log_start` が返す row id を引き回して id で update。1関数の修正で解消。

### [A-2 / E2 + E9] 「負けない」の出口が live で二重断線 ✔確証
- **分類**: 接続断 / **重大度: 致命（live 運用時）**
- **証拠**:
  - E2: `reporting/order_list.py:112` `.where(col(Decision.action) == "buy")` — 発注リストは買いのみ。trailing が sell Decision を作っても**ユーザーは「何を売れ」と一度も指示されない**。
  - E9: `morning_batch.py:543-544` close_due は `is_halted()` で強制 skip。HALT は DD≤-15% 等で発火＝保有が崩れている局面。そこで損切りが止まり含み損が放置される。trailing_check は HALT でも sell Decision を作るが close_due が skip → approved のまま滞留・未執行。
- **勝敗影響**: paper は ds_dispatch/close_due で閉じられるが、**live（楽天実弾）では損切りが現実の口座に届かない**。さらに最も損切りが必要な HALT 局面で執行が止まる。中期投資の北極星「負けない」が live で最も脆い。買い忘れ防止（check_order_diff.py）はあるが「売り忘れ防止」が無い。
- **推奨**: (1) order_list に売り推奨ブロック（sell_loss/time_exit Decision + SellSignal）を追加。(2) HALT 中も損切り close は許可し、HALT の意味を「新規エントリ停止」に限定。

### [A-3 / Z-3 + Z-4] 構築期間中の毎日課金 + 虚偽の安全装置 ✔確証
- **分類**: 無駄コスト時間 / 欺瞞（ドキュメントが実在しない安全装置を主張）/ **重大度: 致命（ガバナンス）**
- **証拠**:
  - Z-3: zeele_llm_scout は DAG 正式ノード（`morning_batch.py:849-854`）で毎朝30回 Haiku 実行。DB cost_logs 実測: 6/4 ¥4.15・6/5 ¥4.07・6/6 ¥4.04（毎日）。**同ノードの docstring（`morning_batch.py:362`）は「構築完了前は実バッチで動かさない」と明記**。実装と方針が矛盾、[[feedback_no_batch_during_construction]] 抵触。
  - Z-4: 入力 `daily_budget_jpy=10.0`（`zeele_llm_scout.py:101`）は execute() で**一度も参照されない dead parameter**。実際に効くのは `BudgetGuard(engine).can_proceed(estimated)`（同267,287）で Setting を読む。**settings テーブルに budget キーは存在せず**（実測で空）、デフォルト `_DEFAULT_DAILY_JPY=500`/`_DEFAULT_MONTHLY_JPY=5000`（`llm/budget.py:24-25`）が適用。docstring/CLAUDE.md が繰り返す「日次¥10/月次¥200」は**コードのどこにも実装されていない**。
- **勝敗影響**: 直接損失ではないが、(1) ユーザー判断なしに課金ノードが構築期間中稼働、(2)「¥10で止まる」という誤認で運用すると実上限は他ノード共有の¥500/¥5000＝最大50倍の課金余地。安全装置の信頼性が崩れる。
- **推奨**: (1) 構築期間中は DAG から外すか max_calls=0。(2) docstring/CLAUDE.md の「¥10/¥200」を実態（共有¥500/¥5000・Setting未投入）に修正、または scout 専用 budget を can_proceed に渡す設計に。

---

## B. 高（勝ちへの寄与を大きく削る）

### [B-1 / E5 + E1] 評価ループ 2ヶ月凍結・売りシグナル0で学習サイクル未始動 ✔確証
- **分類**: 観測性 / **重大度: 高**
- **証拠**: ds_dispatch 43件の evaluation_date = **2026-08-04〜08-05**（90日 horizon）。評価済み実質1件。197 pending。sell_signals テーブルは **0 行**（10日間27保有でも空）。unlock 上限 ¥100,000 で停滞。
- **勝敗影響**: 「勝ったか負けたか」を測れるのが満期（8月）or 早期 stop 時のみ。(a)¥10万→段階解放が約2ヶ月停滞、(b)pilot 重み付け・昇格が無データ、(c)勝率改善ループが回らない。さらに sell_recommender が検知する「シナリオ崩壊・ネガティブニュース」が売り執行に反映されず、質的な早期撤退（負け回避の要）が死んでいる。
- **codex 補正**: 評価日が8月中心なのは 60/90日 horizon 設計の**正当な帰結**であり「凍結（バグ）」ではない。真の欠落は**満期を待つ間の interim（公式ゲート外）評価が無い**こと。
- **【再調査で撤回】interim 評価は既に実装済**: `trading_agent/reporting/forward_diagnosis.py`（codex #4）が **current_mtm + 5/20/40/60日 対TOPIX超過を record-only** で算出し、`scripts/forward_diagnosis.py` → `autoreport/forward/*.json` → `phase_c_status._latest_forward_diagnosis` で消費まで通っている（tag with/without control も併設）。よって「interim 評価が無い」は不正確。**残るのは launchd 未登録（構築中で実 yfinance 未実行）だけ**で、これは構築明けの運用判断。→ B-1 はコード実装不要。
- **推奨**: (1) interim 評価は再実装しない（forward_diagnosis 既出）。構築明けに forward_diagnosis を launchd/morning_batch に組み込む。(2) SellSignal を close_due 入力に配線（sell_recommender が SellSignal を出すようになってから・現状0件）。

### [B-2 / U-01] topics → screening のテーマ経路が断線 ✔確証
- **分類**: 接続断 / **重大度: 高**
- **証拠**: DB 実測 topics affected_tickers 充足率 — **TDnet 2249件中 0件充足（100%空）**、EDINET 866中205充足、他（9to5Mac/Bloomberg/TechCrunch）全空。原因は `mcp_tools/disclosure.py:140` が yanoshin RSS の非提供フィールド `tdnet_companycode` を読む + TDnet 見出しが社名表記でコード抽出器に掛からない。連鎖で theme_score の最大要素（keyword_match_count）が死に、screening が実質 v_shape 単独で動く。
- **勝敗影響**: 「中小型成長株を追う」テーマ熱量シグナルが無効。topics_collector の¥240コストが順位付けにほぼ還元されていない。
- **推奨**: TDnet 見出しの社名→ticker 逆引き（universe.name 照合）を追加。最低限 keyword_match 充足率を summary でログ化し断線を可観測に。

### [B-3 / U-02 + U-04] screening が value_trap を上位推奨・EPS未配線で頭打ち ◐証拠あり
- **分類**: 低精度 / 目的不整合 / **重大度: 高**
- **証拠**: composite 最高40で頭打ち（DB実測）。40点群の v_shape_details は全て `value_trap:true`（増収+点火なし底打ち+RSI+MACD=40）。業績反転の本命（赤字→黒字25pt等）は EPS未配線で発火不能。`agents/screening_agent.py:6-8` docstring 自認「四半期EPS・出来高はツール側配線が必要、現状スキップ」。`ScreeningTickerData.volume_5d/30d_avg` も埋まらず出来高サージ10pt常時0。min_score を50→30 に緩めたが質改善でなく v_shape=30 群の数稼ぎ。
- **勝敗影響**: 「成長株ピック」に反し、自システムが value_trap と警告した“割安だが点火なし”銘柄を上位推奨。中期で勝つための入口の質が構造的に頭打ち。
- **推奨**: J-Quants statements の EPS と日次出来高を配線（最優先の質改善）。value_trap=true を減点。配線後 min_score を分位点ベースに再校正。

### [B-4] market_analyst（全課金78%・¥908）の詳細分析が最終判断で未使用の高コスト経路 ✔確証（codex 補正反映）
- **分類**: 無駄コスト / **重大度: 高**
- **当初の「完全 dead/未消費」は誤り（codex 反証・コードで裁定）**: `_buy_candidate_tickers`（`morning_batch.py:166-187`）は active BuySignal を `score` 降順で並べ、これが materialize_decisions の候補10件の選定・順序を決める。**DB 実測で active BuySignal は10件存在（2026-06-05）→ この主経路が実際に発火している**（screening fallback は active 無し時のみ）。つまり market_analyst は「どの銘柄を MAGI 検証にかけるか」を実際に左右しており、接続は**ある**。
- **正確な問題（接続はあるが割に合わない）**: cost_logs 実測 ¥908.48/146call（全ノード最大・直近¥62/日）をかけて算出する詳細出力（scenarios / ai_confidence / expected_return / sentiment ナラティブ）は、materialize で `score` 以外捨てられ MAGI が一から再採点。実 fill pool（`misato._build_candidate_pool`）は MAGI Decision + ZeeleState で再構築され BuySignal を参照しない。**生き残るのは整数 `score` による順序付けだけ**で、Sonnet の高コスト分析の大部分は最終判断に還元されていない。
- **勝敗影響**: 月¥1,860 のうち「候補順序」に必要なのは安価な score のみ。詳細分析への支払いが過大。[[feedback_api_cost_disclosure]] の趣旨に照らしコスト対効果が悪い。
- **推奨**: **market_analyst を廃止しない**（候補順序の供給源のため）。deep LLM judgment（Sonnet・scenarios 生成）を切り、score を安価な決定論 or Haiku 最小で算出する形に格下げ。将来は ritsuko 統合で順序付け自体を移管。

### [B-5 / Z-1] ZEELE 7戦略が実態 2.5色に縮退・3戦略は完全な死に戦略 ✔確証
- **分類**: 目的不整合 / **重大度: 高**
- **証拠**: DB 実測 preset 分布 — alpha 197 / contrarian 115 / pullback 62 / growth-value 3 / momentum 2。**value/dividend/growth は 0件**。alpha+contrarian で82%。`dividend` は curator の分岐に物理的に存在せず（`zeele_curator.py:123-177`）構造上永遠に0。V字 fallback が contrarian、軸無し fallback が alpha に退化。根因は上流 screening signal の貧弱さ（B-3 と同根）。
- **勝敗影響**: 「7戦略で中小型成長株を多様に分散」というゴール中核が設計上の虚飾。alpha 不発局面に弱い＝分散による負け回避が効かない。
- **推奨**: 上流 signal 拡充（B-3）を律速として格上げ。実際に発火する4-5戦略に正直に縮小し死に戦略を削除。

---

## C. 中（精度・整合）

| ID | 所見 | 分類 | 確信度 | 勝敗影響 |
|---|---|---|---|---|
| C-1 / V-04 | MELCHIOR 業種別閾値が magi_verify で死（`persist.py:272` run_judges に sector 未渡し、sector は _apply_credibility のみ）→ 全銘柄 default 閾値判定 | 低精度 | ✔ | Tech/不動産で false buy/warn、判定精度↓ |
| C-2 / E3 | trailing_check/pyramid_check が broker_mode="paper" ハードコード（`morning_batch.py:409,417`、close_due だけ mode-aware） | 目的不整合 | ◐ | live 移行時に損切り監視が paper 保有のみ |
| C-3 / V-08 | 発注リストが予算外候補を "0株" カードで全件描画 + `_treasury_for_lot` が seed¥100万基準（解放枠¥10万でない） | 目的不整合 | ◐ | 少額運用で買える推奨が埋もれる、live化前の潜在バグ |
| C-4 / Z-2 | qualification_weeks=1 が恒久ハードコード（本来3週）、解除条件・期限が未明文 | 低精度 | ✔ | 低継続性銘柄が pool 上位に。ZEELE 存在理由を無効化 |
| C-5 / X-6 | auto_fill が automation_mode=manual で常時 skip。fill ゲートが `is_auto_mode()` と `master_auto_trade_until` の2系統に分裂 | 観測性 | ✔ | DAG だけ見ると fill 主体が追えない |
| C-6 / E4注 | ds_dispatch fill は auto_trade_until=**2026-07-04** 失効後 dry-run 化 | 観測性 | ✔ | トグル失効後 Phase C 中でも新規 build 停止 |
| C-7 / U-05 | paper では anomaly_check の HALT が原理的に不発（manual で warning 止まり） | 観測性 | ◐ | 安全装置の実発火を本番前に検証できない |
| C-8 / Z-6 | news_sentiment 二重実装（market_analyst キーワード版=dead / ritsuko 実LLM版=live）。CLAUDE.md「固定値50」は古い | 低精度/接続断 | ◐ | sentiment 系統が宙に浮く |

---

## D. 低 / 掃除

- **D-1 / X-3**: universe_refresh / link_topics は完全 no-op。link_topics 未実装で thesis 根拠ニュースが decision に残らず（勝ち分析に効くデータの死蔵）。
- **D-2 / X-4**: ドキュメント乖離 — `morning_batch.py:6-10` docstring は9ノード旧順、`CLAUDE.md` は18ノード旧順（廃止済 portfolio_builder 含む・katsuragi_dispatch/zeele_llm_scout 欠落）。v3 再編（事前検証化）未反映。引き継ぎが嘘の地図で動く。
- **D-3 / X-5**: `morning_batch.py:23,395-400` PortfolioBuilderAgent import と run_portfolio が DAG 未使用（到達不能）。
- **D-4 / V-02**: `DECISION_STATUSES` の "verified" が死に状態（代入箇所ゼロ）。
- **D-5 / E6**: 旧 paper_auto fill 12件が evaluation_date NULL で永久 pending（track record を歪める）。migration で遡及 stamp 推奨。
- **D-6 / E7 ?**: ds_dispatch 43件中15件が entry_price/evaluation_date 欠落（filled_via だけ付いて record_entry 未走）疑い。

---

## E. 健全（反証で確認・過剰警戒しない）

- **fill は機能している（E4 ✔）**: 「manual + Phase C で誰も fill しない＝永遠 awaiting」懸念は否定。ds_dispatch が現に build（filled_via=ds_dispatch 43件、paper active 27件）。awaiting 滞留は7件のみ。paper_auto は Phase C unlock 有効で正しく skip（二重 fill 回避済）。
- **magi_verify は LLM課金0（V-03 ✔）**: `make_live_judge_fn` に llm_tool 未注入（`morning_batch.py:588-593`）、`persist.py:273 if llm_tool is not None:` が False。"live" はデータ取得（J-Quants/NewsAPI/yfinance/EDINET）の意でトークン課金ではない。
- **earnings_sink shadow計測は死んでいない（V-05 ✔）**: `reporting/feedback.py:278-307` の with/without cohort（control n≥20 ゲート）に接続済。ただし評価成立に target_period 経過+n≥20 が要り当面は結論が出ない。
- **status 遷移チェーン健全（V-01 ✔）**: verifying→awaiting→（approve時）approved。当日フィルタ一貫、前日繰越 cancel あり。
- **broker_mode 分離（paper）健全（X-7 ✔）**: `_treasury_for_lot` の seed¥100万懸念は paper では lot 上限が inf で無害。実 fill は解放枠¥10万 − active exposure で物理キャップ。

---

## 優先是正リスト（codex レビュー反映・確定版）

1. **A-1 analysis_logs を修正** — `_log_start` が row id を返し `_log_end` を id 更新に（codex 推奨：invocation_id+agent より将来安全）。勝率を測れる土台。最優先。
2. **A-2 live 売り出口を繋ぐ** — order_list に売りブロック追加 + 「HALT は新規 buy 停止・risk-reduction sell は許可」へ意味統一 + trailing/sell_signal/order_list を E2E で固定。live 移行ブロッカー。
3. **A-3 zeele_llm_scout の構築期間停止 + 予算実装**（codex が入口品質より前に格上げ）— dead param 廃止 or 実装、構築中は max_calls=0 / feature flag。**低工数で無駄課金と虚偽ガードを即停止し信頼を回復。**
4. **B-3 / B-2 screening 入力品質** — EPS・出来高配線 + TDnet 社名→ticker mapping。「勝てる銘柄」を拾う根幹。B-3 が律速。value_trap=true は最低限 減点/昇格抑制。
5. **B-5 ZEELE preset 再校正** — 上流（B-3）修正後に実施（先に分岐をいじらない）。
6. **B-4 market_analyst 格下げ** — 廃止せず（候補順序の供給源）、score を Haiku 最小/決定論に落として Sonnet 詳細分析を停止。
7. **B-1 interim 評価 + 掃除**（C-1 sector 1行修正、D-2 DAG図更新、D-3/D-4 dead code）。

> 当初 pipelineCK 案は「無駄掃除を最後」に置いたが、codex の「A-3 は低工数 × 無駄課金/虚偽ガードが信頼を削る」を採用し #3 に格上げ。それ以外の順序は一致。

---

## 監査の限界（誠実な開示）
- read-only（DB は SELECT のみ）。バッチ実行・LLM呼び出し・DB書き換え・ファイル編集は一切なし。
- **? 疑い**（未裏取り）: U-06（PortfolioSnapshot 蓄積件数）、U-09（RSS 枯死 URL の実 HTTP）、E7（ds_dispatch 15件欠落の原因）。本番移行判断前に別途確認推奨。
- 接続関係の dead 判定は grep で消費側を追って確認したが、動的 import/間接参照を取りこぼす可能性は残る。codex の反証レビューを歓迎する。
