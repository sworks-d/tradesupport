# リサーチ → 実装 統合ロードマップ（現状反映版）

作成日：2026-05-23 ／ 対象コミット：`ec4cd9a feat(A-4): wire MAGI into the DAG`
位置づけ：3波分のリサーチ（`RESEARCH_METHODS.md`）と**最新リポジトリの実コード**を突き合わせ、
「**この研究を、今のコードのどこに、どの順で入れるか**」を確定する1枚。
知識の正＝`RESEARCH_METHODS.md`／OSS借用＝`X1_reuse_inventory.md`／本書＝接続と順序。

---

## 0. 現状の事実（実コード確認 2026-05-23）

> ✅=実装され実行経路に乗る ／ 🟡=実装あるが起動条件未達 ／ ❌=未

| 項目 | 状態 | 実コードの事実 |
|---|---|---|
| **MAGI の DAG 接続（A-4）** | ✅ | `portfolio_builder → materialize_decisions → magi_verify → link_topics`。persist.py は冪等・status遷移(verifying→awaiting)・default_hold保留分岐つき。専用テスト有 |
| **decisions schema（A-3）** | ✅ | `status / gendo_stance / verified_at` 追加済（確定方針どおり） |
| **universe 投入（A-1）** | 🟡 | `load_universe.py` は存在（upsert冪等）。だが **DAGの `universe_refresh` は `{"skipped":True}`** ＝Phase1では別タスク投入前提。**実投入されたかは要確認** |
| **screening 起動** | 🟡 | DAGノードは有る。だが universe未投入だと母集団ゼロ＝**実質起動していない** |
| **買い候補の供給源** | 🟡 | `_buy_candidate_tickers`＝active buy_signals優先・無ければscreening上位。**screening死んでいると旧market_analyst依存＝NVDA寄りのまま** |
| **snapshot の候補** | ❌ | `build_snapshot.py` の `CANDIDATES={"nvda":"NVDA"}` **固定のまま**。DAGはMAGIを通すがUI表示はNVDA決め打ち |
| **A-5 決裁→発注（出口）** | ❌ | 決裁API（`POST /api/decisions/{id}`）も発注リスト出力も無し。decisionは `awaiting` 止まり＝**決裁できない** |
| **P1-5 一次情報（深掘り財務）** | 🟡→実質❌ | `fundamentals.py` は **yfinance単期のみ**（2期分取れない）。`disclosure.py` はTDnet/EDINET fetcher構造は有るがEDINETキー必須・実質0件 |
| **信用性フィルタ（P2-3）** | ❌ | `defense.py` の `credibility_flag` は **`"ok"` 固定**（D-14未実装）。M/F/Z-Score実装なし |
| **審判ごとの反証（B群）** | ❌ | `magi.py` にあるのは碇の `counter_argument` のみ。**JudgeVerdict に反証フィールドなし** |

### 一文要約
**シャフトは繋がった（MAGIがDAGに乗った）。だが ①入口（universe/screening）が起動していない
②出口（決裁→発注）が無い ③MAGIの中身（信用性・反証）と餌（一次情報）が薄い。**
＝「銃は組み上がったが、弾倉が空・引き金が未接続・銃身が短い」。

---

## 1. リサーチ結論 × 現状コード ＝ 何が言えるか

第2波の最終結論「**手法は弾、構造が銃。弾は劣化するが銃は残る**」を、現状に当てると：

- **銃（構造）はほぼ完成した**：MAGI3審判→防御→統合→碇→decision保存→default_hold保留。
  これは前回まで「孤立コード」だったものが実行経路に乗った＝**最大の前進**。
- **だが弾を込める前に、銃を撃てる状態にする**必要がある＝**貫通の完成（入口起動＋出口接続）が先**。
  貫通していない銃にM-Score等の弾を込めても、撃って当たったか確認できない（評価基盤が無い）。
- **リサーチが指摘した律速＝P1-5が、現状コードでも律速**：fundamentalsが単期yfinanceのみ＝
  M-Score/F-Score/earnings acceleration（全て2期分必須）が**物理的に動かせない**。
  → 信用性・反証・V字の手法群は、**P1-5を実装するまで全部ペンディング**になる構造。

---

## 2. 提案する順序（現状反映）

```
【第1段：貫通の完成】← 今ここを最優先。研究の前に銃を撃てる状態に
  S1 universe実投入＋screening起動    （入口：候補をNVDA固定から脱却）
  S2 snapshot候補の動的化            （UI表示をawaiting decisionから生成）
  S3 A-5 決裁API＋発注リスト          （出口：人間が決裁→発注リスト）
       └→ ここで「1銘柄が端から端まで通る」最初の貫通が完成

【第2段：餌を太らせる】← 貫通後。研究成果の前提
  S4 P1-5 一次情報（2期分財務＋EDINET/EDGAR）  ★リサーチが指摘した共通律速
       └→ これが入って初めて M/F/Z-Score・earnings acceleration が動く

【第3段：弾を込める】← S4後。リサーチ成果の実装
  S5 信用性フィルタ（P2-3）：M/F/Z を複数独立warnフラグ（一致を求めない）
  S6 審判の反証（B群）：BALTHASARコード反証＋MELCHIORコード反証
  S7 V字スクリーニング精緻化：3審判合議＋Improving象限＋Why cheap質チェック

【横断・随時】
  P6 評価基盤（バイアス回避）＋ A/B育成（複数局面で選ぶ）
```

**鉄則：第1段 → 第2段 → 第3段 の順を崩さない。**
研究で見つけた手法（第3段）は魅力的だが、**入口・出口（第1段）と餌（第2段）が無ければ机上の空論**。

---

## 3. 各ステップ（現状コードへの接続点つき）

### 第1段：貫通の完成 ★最優先

#### S1 universe実投入＋screening起動
- **現状**：`load_universe.py` 有・`universe_refresh={"skipped":True}`・screening母集団ゼロ。
- **やること**：①`load_universe.py` を実行しDBに銘柄投入 ②DAGの `universe_refresh` を実投入 or
  「投入済み前提でskip」を明示 ③screeningが母集団を読んで `ScreeningResult` を生む状態に。
- **[要判断]**：universe銘柄リストの出所（手元 or ¥1M端株前提の自動定義＝US主要＋安価JP）。
- **接続点**：`scripts/load_universe.py`、`orchestrator/morning_batch.py::universe_refresh/run_screening`。
- **受入**：universe N件、screeningがDAGで `ScreeningResult` を保存、`_buy_candidate_tickers` が
  screening上位を返す（buy_signals依存＝NVDA寄りから脱却）。

#### S2 snapshot候補の動的化
- **現状**：`CANDIDATES={"nvda":"NVDA"}` 固定。バックエンドはMAGIを通すがUIはNVDA。
- **やること**：`build_snapshot.py` の固定CANDIDATESを廃し、**DBの `awaiting` decision から候補生成**。
- **接続点**：`scripts/build_snapshot.py`（49行・227行）。
- **受入**：buyゾーンが実候補（複数）。**UIの「NVDA決め打ち」が完全終了**。
- **注意**：UIの買いゾーンは固定カード→複数候補の完全表示はコンポーネント化(U-2)後。当面は上位N枚。

#### S3 A-5 決裁API＋発注リスト
- **現状**：decisionは `awaiting` 止まり。決裁API・発注リストとも無し。
- **やること**：①`POST /api/decisions/{id}`（approved/denied/held＋理由、status更新）
  ②UI決裁ボタンonClick配線（未照合/割れは既定「保留」を選択状態に＝R6）
  ③承認分の発注リスト出力（銘柄/数量＝サイジングのコード値/想定価格）。**自動発注しない（Tier1）**。
- **接続点**：新規 `api/routes_decisions.py`、`ui/`、サイジングは既存 `portfolio/sizing.py`。
- **受入**：押下でstatus保存・画面反映、承認→発注リストに正しい数量。
- **★ここで「最初の貫通」完成**：universe→screening→3審判→防御→統合→碇→decision→決裁→発注リスト。

### 第2段：餌を太らせる

#### S4 P1-5 一次情報 ★リサーチが指摘した共通律速
- **現状**：fundamentalsはyfinance単期のみ。disclosureはEDINETキー必須で実質0件。
- **やること**：①fundamentalsに**2期分の財務取得**（売掛金/在庫/COGS/営業CF/純利益/総資産…）
  ②US＝SEC EDGAR(XBRL)、JP＝EDINET(有報/監査意見/GC注記) provider追加 ③出典URL・報告期を付与。
- **接続点**：`mcp_tools/fundamentals.py`（単期→複数期）、`mcp_tools/disclosure.py`（EDINET実装）。
- **[要判断]**：EDINET APIキーの設定（ユーザー作業）。
- **受入**：1銘柄で2期分財務＋（JP）監査意見/GC注記が取れる。**これが S5/S6/S7 全部の前提**。
- **規模**：中〜大。**リサーチ根拠**：RESEARCH_METHODS 領域1〜3が全て「2期分必須」＝ここが律速。

### 第3段：弾を込める（リサーチ成果の実装）

#### S5 信用性フィルタ（P2-3）＝ RESEARCH_METHODS 領域1
- **現状**：`credibility_flag="ok"` 固定。
- **やること**：M-Score / F-Score / Z-Score をコード計算し、**複数独立のwarnフラグ**として
  `defense.py::verify` の `credibility_flag` を実値化。**一致を求めない**（一致率30%＝別物を見る）。
  ハード除外でなくwarn（誤検出17%）。閾値は見逃しコスト高→厳しめ。**日本株は米国閾値流用せず参考**。
- **接続点**：新規 `trading_agent/screening/credibility.py`、`magi/defense.py`（credibility_flag）。
- **受入**：粉飾疑い銘柄に warn。第1波1-1〜1-3＋第2波1-V1〜1-V4 の方針どおり。
- **依存**：S4。**派生候補**：Dechow F-Score（補完）。係数フリーML版は**不採用**（説明可能性優先）。

#### S6 審判の反証（B群）＝ RESEARCH_METHODS 領域2
- **現状**：碇の counter_argument のみ。JudgeVerdict に反証フィールド無し。
- **やること**：①`models/magi.py::JudgeVerdict` に `counter_within_domain: list[dict]` 追加
  ②BALTHASAR反証＝**コード**（出来高乖離/RSIダイバージェンス/ATR未達/リテスト失敗）
  ③MELCHIOR反証＝**大半コード**（CFO/純利益<1・DSO悪化・在庫>売上・Sloan比率・GAAP乖離）
  ④碇がcounter_within_domainを束ねる。
- **★リサーチの発見を反映**：B-3は当初「LLM摘出」だったが、領域1・2で**大半コード計算可能**と判明
  → **コード摘出＋LLM文章化**に再設計（R5を構造的に強化）。
- **接続点**：`magi/judges.py`（balthasar/melchior）、`magi/defense.py`（反証照合）、`models/magi.py`。
- **受入**：buyでも自領域の反証が出る。創作（データに無い懸念）が照合で弾かれる。
- **依存**：S4（MELCHIOR反証は2期分財務必須）。

#### S7 V字スクリーニング精緻化＝ RESEARCH_METHODS 領域3
- **やること**：①V字＝「底にいる」でなく「**底から反転の点火**」（earnings acceleration）
  ②**Value×Momentum両立**のAND条件 ③Improving象限（未話題の底打ち・テーマは従、V字が主）
  ④**Why cheap質チェック**でvalue trap除外（genuine recovery＝かつて優良→一時不振→反転を狙う）
  ⑤V字は**3審判合議**で判定（業績の底と反転×なぜ×株価転換）。
- **接続点**：`mcp_tools/screening.py`（V字4軸/テーマ4軸の中身）。
- **受入**：value trap除外・Improving象限抽出。第1波領域3＋第2波3-V1〜3-V3の方針どおり。
- **依存**：S4、S1。

### 横断：P6 評価＋A/B育成 ＝ RESEARCH_METHODS 領域4
- **やること**：①decision評価（data_asof厳守＝先読み防止、評価期日までpending）
  ②Track Record（S&P比超過併記、最低サンプル数まで暫定明示、強気/弱気の局面分け）
  ③A/B育成（**複数局面の安定性で選ぶ**＝recency bias回避・ファクター劣化監視、walk-forward、t-stat≥3）。
- **接続点**：新規評価ジョブ、`models/decisions`(actual_return/hit_or_miss)、ui Track Record。
- **依存**：S3（decisionが決裁・記録される）＋データ蓄積。

---

## 4. 残る [要ユーザー判断]
1. **S1 universe銘柄リストの出所**：手元リスト or 自動定義（¥1M端株前提US主要＋安価JP）。
2. **S4 EDINET APIキー**：取得・設定（ユーザー作業）。これが無いとJP一次情報が動かない。
3. **C-2 原則7の解像度化**（OSS判断ログ参照を採るか・X1参照）：A/B育成設計時。

## 5. 結論 ── 今やるべきこと
**研究の実装（第3段）に飛びつかない。** リサーチは弾を揃えたが、現状コードは
**入口（universe/screening）が起動しておらず、出口（決裁/発注）が無い**。
→ **まず第1段（S1〜S3）で貫通を完成させる。** 1銘柄が端から端まで通り、決裁できる状態に。
→ 次に第2段（S4 P1-5）で餌を太らせる。これがM-Score等の物理的前提。
→ 最後に第3段（S5〜S7）でリサーチ成果を込める。

**「貫通 → 餌 → 弾」。順序を逆にしない。** これが現状を踏まえた最適解。
