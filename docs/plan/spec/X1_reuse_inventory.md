# X-1 外部OSS棚卸し調査 ── 借用候補と移植先マッピング

作成日：2026-05-23
**2026-05-26 追補**：Claude Skills 投資エコシステムの追加調査。§6 を追加（claude-trading-skills / dexter-jp / stock_skills 守り系・関連兄弟群）。**結論：claude-trading-skills が思想で tradesupport の北極星に最も近い**。
位置づけ：`X_external_reuse.md` の **X-1（棚卸し調査）の成果物**。
親原則：借りても**不変原則は曲げない**（数値はコード／総合スコア出さない／決裁は人間）。
**借用方針（D-21 確定）：自前実装が基本。借りるのは「考え方・ロジック」だけ。コードは直輸入しない。**
→ 本書の「部分移植」は「考え方を蒸留→自前実装」と読み替える（出典は敬意として記録）。ライセンスゲートは大幅に軽い。
現物前提：MAGIは実装済みだが**朝バッチDAG未接続**（孤立）・news.pyは**yfinance/GNews未実装**・decisionsは**ver1形のまま**。
→ 借用は「動かないMAGIへの上乗せ」ではなく、**断線解消(A群)の後に効く**ものを優先評価する。

> ⚠ **ライセンスは全て「要確認」**。本書のライセンス欄は検索ベースの推定で、確定には各リポジトリの
> 実 LICENSE ファイル確認が必須。商用・再配布でなく「個人利用＋コード参照/部分移植」でも、
> 移植時はライセンス表記をコメント/ADRに残すこと（X-2の受入条件）。

---

## 0. 結論サマリ（先に要点）

| 優先 | OSS | 何を借りるか | 移植先 | 理由 |
|---|---|---|---|---|
| **◎0** | **claude-trading-skills**（2026-05-26 追加） | **5ワークフロー骨格＋"規律監督"スキル群（Kanchi T1-T5 / Exposure Coach / Trader Memory / Signal Postmortem / Trade Performance Coach）** | **MAGI車線（守り）／G_risk_discipline／P6 評価・学習** | **思想で tradesupport の北極星に最も近い**。「Plan→Trade→Record→Review→Improve」のループそのもの。MIT。詳細は §6 |
| ◎1 | **TradingAgents** | reflection（過去判断の振り返り注入）構造／Bear論拠の組み立て方 | P6-2/P6-3・碇/反証 | 学習の穴とBear反証に直結。原則7と両立可 |
| ◎2 | **ai-hedge-fund** | backtester の評価指標計算部分／ペルソナの財務チェックリスト | P6-1・MELCHIOR深掘り | P6の空白を最小コストで埋める |
| **◎JP** | **dexter-jp**（2026-05-26 追加・既存§1-3の virattt/dexter のJPフォーク） | **EDINET + J-Quants 自律エージェント設計（plan→tool→検証→出典付きレポート）** | **MAGI BALTHASAR（ファンダ・出典照合）／P3-7・P1-5** | 日本株専用＋tradesupport が既にEDINETを使用＝直接相性。詳細は §6 |
| ○3 | **FINSABER** | 批判的バックテスト思想（survivorship/data-snooping回避） | P6設計の前提 | 「過信しない」原則の実装ガード |
| ○4 | **OpenBB** | データprovider層（EDGAR/FRED等の正規化済み接続） | P1-5/P1-8 | 一次情報・マクロの接続を自前実装せず借用 |
| **○S** | **stock_skills（okikusan-public）**（2026-05-26 追加・別途 architecture.html §7 で詳細評価済み） | **守り系（value_trap / shareholder_yield / health_check / 調整アドバイザー17ルール / 複利シミュ / ストレステスト / HHI）** | **MAGI車線（守り）／G_risk_discipline** | 思想は古いが、決定論的計算がSCORE:NONE適合。攻め系（16プリセット探索／0-100スコア）は不採用 |
| △5 | **StockBench** | 評価ベンチマークの観点 | P6評価設計 | 評価軸の参考（移植でなく思想） |
| △6 | **FinRobot** | 財務分析エージェントの分業設計（Financial Chain-of-Thought） | P3-1/P3-7 | MELCHIOR/BALTHASAR深掘りの補助（優先度中） |
| △7 | **Claude Skills 兄弟群**（InvestSkill / ConsensusAI / staskh-trading-skills / finance_skills 等） | フレームワーク命名・出力フォーマットの参考 | 設計参考 | 思想・実装ともに claude-trading-skills が代表として吸収済。詳細は §6.6 |
| ✕ | ATLAS / 各種bot | 自己進化・自動売買・クリプト | （不採用） | 不変原則（自己改変不採用・Tier1手動）と衝突 |

**◎0 の発見で「今すぐ着手すべき借用はない」前提は更新**。claude-trading-skills の**5ワークフロー骨格と "規律監督" スキル群は、MAGI 車線のペーパー運用着手前に取り込める**（B0〜B6 と並走可）。詳細は §6.7。

---

## 1. 候補OSS 一覧（リポジトリURL付き）

### 1-1. TradingAgents（最重要・構造が最も近い）
- URL：https://github.com/TauricResearch/TradingAgents
- 論文：https://arxiv.org/abs/2412.20138
- 規模：約59k★・LangGraph製・v0.2.4（2026/04）
- 構造：Analyst Team(fundamentals/sentiment/news/technical) → Researcher(Bull/Bear討論) → Trader → Risk Mgmt(Aggressive/Conservative/Neutral) → Portfolio Manager の5段。
- ライセンス：**要確認**（Apache-2.0系の可能性／学術リポジトリ）

### 1-2. virattt/ai-hedge-fund（バックテスト＋投資家ペルソナ）
- URL：https://github.com/virattt/ai-hedge-fund
- 規模：約58k★・Poetry製・`src/backtester.py` 同梱
- 構造：投資家ペルソナ別agent（Buffett/Graham/Burry/Munger/Ackman/Wood/Damodaran）＋Risk＋Portfolio。
- データ：FINANCIAL_DATASETS_API_KEY（AAPL/GOOGL/MSFT/NVDA/TSLAは無料）・Tavily。
- ライセンス：**要確認**（MIT系の可能性）

### 1-3. virattt/dexter（深い金融リサーチ・新顔）
- URL：https://github.com/virattt（dexter リポジトリ）
- 規模：約26k★・TypeScript
- 用途：銘柄の深掘りリサーチ自律エージェント。spec群が未把握の新顔。
- ライセンス：**要確認**

### 1-4. FINSABER（批判的バックテスト・学術）
- URL：https://github.com/waylonli/FINSABER
- 論文：https://arxiv.org/abs/2505.07078 （KDD'26）
- 用途：LLM投資戦略を**長期間・100+銘柄で検証し、過大評価バイアスを暴く**バックテスト基盤。
  「LLM戦略は強気相場で保守的すぎ・弱気相場で攻撃的すぎ」と報告。
- 価値：あなたの「過信しない／実績で測る」原則の**実装ガードレール**として思想が直結。
- ライセンス：**要確認**（学術コード）

### 1-5. OpenBB（データ基盤・MCP対応）
- URL：https://github.com/OpenBB-finance/OpenBB
- 用途：equities/options/macro/fixed income を**統一API**で。providerを差し替え可
  （yfinance/SEC/FRED等）。**MCPサーバーも提供**＝あなたのMCP基盤と相性良。
- ライセンス：**要確認**（AGPL/商用デュアルの可能性＝要注意。AGPLなら個人利用でも条件確認）

### 1-6. StockBench（評価ベンチマーク）
- URL：https://stockbench.github.io/
- 用途：LLMを現実的な株取引で評価。多くのLLMがbuy-and-holdに勝てないことを示す。
- 価値：P6の**評価軸の設計参考**（移植でなく観点の借用）。
- ライセンス：**要確認**

### 1-7. FinRobot（財務分析エージェント基盤）
- URL：https://github.com/AI4Finance-Foundation/FinRobot
- 用途：LLMによる財務分析のagentプラットフォーム。
- 価値：MELCHIOR深掘り（P3-1/P3-7）の分業設計の参考。優先度低。
- ライセンス：**要確認**（AI4Financeは MIT系が多い）

### 1-8. キュレーションリスト（探索の起点）
- URL：https://github.com/georgezouq/awesome-ai-in-finance
- 用途：金融×LLM/DLの網羅リスト。zipline（バックテスト定番ライブラリ）等の起点。

### 1-9. zipline（古典的バックテストライブラリ・非LLM）
- 参照：awesome-ai-in-finance 経由
- 用途：Pythonアルゴ取引バックテストの定番。評価エンジンの枯れた選択肢。
- 注意：自動執行前提が強い。**評価計算部分のみ**参照価値。

### ✕ 不採用カテゴリ（原則と衝突）
- ATLAS（自己進化25agent・Darwinian選択）→ **原則7「自己改変不採用」と正面衝突**
- 各種crypto/forex自動売買bot → **Tier1手動・中期株式と不一致**
- InvicTrade等「勝率74%」系 → 検証不能な宣伝値。**原則「数値はコード照合」と不一致**

---

## 2. あなたの設計のどこに・どう取り込むか（フェーズ別）

> 各項目に【借用元】【何を】【どう改修して原則を守るか】【移植先WP】を明記。

### P1 データ収集

#### P1-5 財務一次情報 ← OpenBB（△4）
- **借用**：OpenBB の EDGAR/EDINET 相当 provider の正規化スキーマ。
- **どう守る**：OpenBBは数値取得層なので原則と衝突しない（LLMに数値を作らせる箇所ではない）。
  ただしAGPL懸念があるため、**丸ごと依存**せず「接続の作り方を参照し自前実装」が安全。
- **移植先**：WP-A2（`fundamentals.py`/`disclosure.py` のprovider追加）
- **注意**：あなたは無料・キー不要・moomoo中心。OpenBBの有料provider前提部分は採らない。

#### P1-8 マクロ・政府公式 ← OpenBB（△4）
- **借用**：FRED等のマクロデータ接続の構造。
- **移植先**：WP-A1延長（`news.py` のマクロRSS）／別途macro provider。
- **優先度**：低（CASPER起動が先）。

> ⚠ **P1-6 ニュース（最優先穴）は借用不要**。yfinance.news + Google News RSS で自前実装が
> 最速かつキー不要（spec WP-A1）。OSS借用より自作が勝る数少ない箇所。

### P3 MAGI判断

#### P3-1 MELCHIOR深掘り ← ai-hedge-fund ペルソナ（◎2）／FinRobot（△6）
- **借用**：Graham=安全域、Damodaran=規律ある評価、Burry=深い割安、各ペルソナが**見る財務指標の
  チェックリスト**（コードでなく「何を見るか」の体系）。
- **どう守る**：ペルソナLLMは数値を生成する作り→**採らない**。指標リスト（増収率・営業CF/純利益・
  在庫回転・PER過去比 等）だけを抽出し、**値はコードが計算**してMELCHIORに渡す。
- **移植先**：P3-1 / 反証案 B-3（MELCHIORの反証摘出ルール）。

#### P3-3 CASPER文脈判定 ← TradingAgents News/Sentiment Analyst（◎1）
- **借用**：News Analyst（マクロ・政府発表・世界イベント）と Sentiment Analyst の**役割分離の設計**と
  プロンプト構造。
- **どう守る**：彼らはLLMにsignal/confidenceを生成させる→**数値生成は採らない**。
  「材料の方向（ポジ/ネガ）の解釈」だけ借り、確信度は定性ラベル（D-11）。出典必須（R2）。
- **移植先**：P3-7（CASPER=Sonnet解釈）。

#### 反証案（各審判が自領域で反証） ← TradingAgents Bear Researcher（◎1）
- **借用**：Bear Researcherが**反対論拠を能動的に構築する**やり方（彼らは討論で収束させるが、
  あなたは収束させず割れを見せる＝borrowは「論拠の組み立て方」のみ）。
- **どう守る**：Bear体を別agentとして置かない（三権独立を壊さない）。各審判が
  **自領域データ内の逆向き事実を摘出**する形に翻案。予測創作禁止・source_refs必須（R5/R2）。
- **移植先**：models/magi.py（JudgeVerdict.counter_within_domain追加）／judges.py の各審判。

#### P3-6 碇司令 ← TradingAgents Trader/Risk の synthesis（◎1・限定）
- **借用**：Trader が複数レポートを束ねて意見を作る**構造**（束ね方）。
- **どう守る**：彼らのTraderは「決める」役→あなたの碇は「推奨するだけ・決めない」。
  **決定ロジックは採らない**。各審判の反証を束ねて「反対するなら：」を作る部分だけ翻案。
- **移植先**：commander.py（反証集約への改修＝反証案 B-4）。

### P6 評価・学習（借用の本命）

#### P6-1 decision評価ジョブ ← ai-hedge-fund backtester（◎2）
- **借用**：`backtester.py` の**評価指標計算**（累積リターン/勝率/Sharpe/最大DD）。
- **どう守る**：彼らのbacktesterは**自動執行ループ**前提→**執行部分は採らない**。
  「過去データで、もしこのdecisionで入っていたらどうなったか」の**仮想評価計算だけ**移植。
  実運用はTier1手動のまま（原則5）。
- **移植先**：WP-C5（評価ジョブ）／`models/decisions`(actual_return/hit_or_miss)。

#### P6-2/P6-3 Track Record・学習 ← TradingAgents reflection（◎1）
- **借用**：v0.2.4 の **persistent decision logs / reflection** ＝次回同一銘柄の実行時に、
  実現リターン（raw＋SPY比alpha）を取得し、過去判断の振り返りを**参照情報として注入**する仕組み。
- **どう守る・ここが原則の論点**：あなたの原則7「自己改変不採用」。
  - **採る**：判断ログの構造化蓄積＋類似ケースの**参照提示**（＝人間の意思決定支援。自己改変ではない）。
  - **採らない**：ログを使ってagentが**ロジックを自動書き換え**する部分（＝自己改変）。
  - この線引きで原則7と両立。**reflectionの「振り返り文生成」はLLMだが、数値（実現リターン）は
    コード計算**にすればR1も守れる。
- **移植先**：WP-C5延長／P6-3（A/Bは人間がGitで選ぶ枠は維持）。

#### P6 全体の設計前提 ← FINSABER（○3）／StockBench（△5）
- **借用**：思想のみ。「narrow timeframe・少数銘柄での評価は過大評価を生む」という警告を、
  あなたのTrack Record設計の**ガードレール**にする。
- **どう活かす**：P6-2で「短期間・1銘柄の好成績を過信しない」表示・評価期間の最低ライン設定。
  StockBenchの「多くのLLMがbuy-and-holdに負ける」事実を、**S&P比超過リターンを必ず併記**する
  根拠にする（既にP6-2にある）。
- **移植先**：移植コードなし。P6設計レビューの観点。

### X-2 への申し送り（実際に移植する時の順序）
1. **P6-1**：ai-hedge-fund backtesterの指標計算を、自動執行を外して移植（仮想評価）。
2. **P6-2/3**：TradingAgents reflection構造を、自己改変を外して「参照提示」として移植。
3. **P3反証**：Bear論拠の組み立て方を、各審判の自領域反証に翻案。
4. **P1-5/8**：OpenBBのprovider構造を参照（AGPL確認後・丸ごと依存しない）。

---

## 3. 借用で壊れる罠（X-2で必ずテスト担保）

| 罠 | 内容 | 守り |
|---|---|---|
| 罠1 LLM数値生成 | 借用元は軒並みLLMにconfidence/signal/target_priceを生成させる | 移植後テストで「数値はコード由来」を検証（README §1.1の違反パターンと同じ） |
| 罠2 自動執行 | backtester/botは自動でentry/exit | 評価計算のみ移植・執行ループは捨てる。Tier1手動を死守 |
| 罠3 合意収束 | 彼らはdebate→1結論に統合 | 統合・PortfolioManagerの「決める」層は採らない。SCORE:NONE維持 |
| 罠4 自己進化 | ATLAS等はロジック自動改変 | 原則7。参照提示はOK、自動書換はNGの線引き |
| 罠5 規模過剰 | 7agent×複数LLM×討論ラウンドは日次¥500で回らない | 構造を借りても呼び出し回数を削る。コストロガーで監視 |
| 罠6 ライセンス | AGPL（OpenBB懸念）・不明 | 移植前に実LICENSE確認。出典をADRに記録 |

---

## 4. 原則を「見直す」候補（あなたの今回の方針：優れた手法なら原則見直しも可）

リサーチの結果、**見直す価値があるのは1点だけ**。他は守った方が強い。

- **見直し候補：原則7「学習は人間主導A/Bのみ・自己改変不採用」の解像度**
  - TradingAgents reflectionは「過去判断の参照」を実装し、明確に成果を上げている。
  - 提案：原則7を「**ロジックの自動書換は不採用（維持）／判断ログの構造化と参照提示は採用（追加）**」
    に分解。これは原則を曲げるのではなく**解像度を上げる**。P6が経験から学べるようになる。
- **見直さない（OSSより優れている）**：SCORE:NONE・三権独立・決裁は人間・数値はコード。
  特に「合意を信じない」はTradingAgents/ai-hedge-fundの収束思想より思想的に深く、捨てると
  ただの劣化コピーになる。

---

## 5. 受入条件（このX-1の完了基準＝spec準拠）
- [x] 借用候補リストが出来、各候補にURL・採否・移植先WPが付いた
- [x] 各候補の原則適合（採る部分／採らない部分）が明記された
- [ ] 各採用候補の**実LICENSE確認**（X-2着手時の必須前提）
- [ ] 移植順序（§2 X-2申し送り）の確定

---

## 6. 追補（2026-05-26）：Claude Skills 投資エコシステム調査

### 6.0 要点（4行）

- **claude-trading-skills（tradermonty）** が tradesupport の思想（"持ち続けさせる機"／Core-Satellite規律監督）と**ほぼ完全一致**。MIT・55スキル・5ワークフロー。**北極星候補**。
- **dexter-jp（edinetdb）** が**日本株専用 EDINET+J-Quants 自律エージェント**として直接相性。既存§1-3 の virattt/dexter のJP フォーク相当。
- **stock_skills（okikusan-public）** は別途 [architecture.html](../../../architecture.html) §7 で詳細評価。**守り系のみ借用**。
- 全体として **Claude Skills 投資エコシステムが急速に成熟**。stock_skills 単独で判断していたのは見識不足だった。

### 6.1 claude-trading-skills — 最重要発見

**URL**：https://github.com/tradermonty/claude-trading-skills ／ MIT ／ Claude Skills 公式仕様準拠
**規模**：55スキル・8カテゴリ・5公式ワークフロー・skill-packages 配布・docs site あり

#### 6.1.1 思想の一致（引用）

> "The goal is **not to outsource buy/sell decisions to AI**. The goal is to structure **market review, risk management, trade planning, journaling, and continuous improvement**."
>
> "Claude Trading Skills is not a signal engine. It aims to be a **decision-process OS**."
>
> Core loop: **Plan → Trade → Record → Review → Improve**

→ memory「ツールの目的＝持ち続けさせる機（売買で勝つ予測機ではなく Core-Satellite 規律監督）」と**英訳と言える整合**。

#### 6.1.2 8カテゴリ × 55スキル → tradesupport 対応マップ

| カテゴリ（スキル数） | 代表スキル | tradesupport との関係 |
|---|---|---|
| **market-regime（10）** | exposure-coach / market-breadth-analyzer / uptrend-analyzer / macro-regime-detector / ftd-detector / ibd-distribution-day-monitor / market-top-detector / us-market-bubble-detector / downtrend-duration-analyzer / sector-analyst | MAGI BALTHASAR/CASPER の環境読み・防御材料。**exposure-coach は規律監督の中核として強借用候補** |
| **core-portfolio（5）** | **kanchi-dividend-review-monitor** / kanchi-dividend-sop / kanchi-dividend-us-tax-accounting / portfolio-manager / value-dividend-screener / dividend-growth-pullback-screener | **Kanchi T1-T5 強制見直し状態機械 = MAGI車線の保有健全性アラート設計の理想形（強借用候補）** |
| **swing-opportunity（5）** | vcp-screener / canslim-screener / breakout-trade-planner / finviz-screener / theme-detector | ZEELE車線（凍結中・解凍時に検討） |
| **trade-planning（3）** | position-sizer / technical-analyst / us-stock-analysis | portfolio/sizing.py 等の既存と重複。設計参考のみ |
| **trade-memory（4）** | **trader-memory-core** / **signal-postmortem** / **trade-performance-coach** / trade-hypothesis-ideator | **強借用候補**：投資テーゼライフサイクル管理／月次評価／プロセス遵守レビュー。P6 評価・学習の実装パターン |
| **strategy-research（9）** | backtest-expert / **edge-pipeline-orchestrator** + edge-candidate-agent / edge-hint-extractor / edge-concept-synthesizer / edge-strategy-designer / edge-strategy-reviewer / edge-signal-aggregator / scenario-analyzer / strategy-pivot-designer / stanley-druckenmiller-investment | edge-pipeline 7スキル群はZEELE車線の発議エンジン設計の参考。stanley-druckenmiller-investment は8スキル統合の synthesizer パターン |
| **advanced-satellite（5）** | earnings-trade-analyzer / pead-screener / institutional-flow-tracker / pair-trade-screener / parabolic-short-trade-planner / options-strategy-advisor | **大半は不採用**（オプション・空売り・統計裁定は中期株式と不一致）。institutional-flow-tracker（13F追跡）のみ思想参考 |
| **meta（6）** | skill-designer / skill-idea-miner / skill-integration-tester / data-quality-checker / dual-axis-skill-reviewer / trading-skills-navigator / earnings-calendar / economic-calendar-fetcher | 開発支援。trading-skills-navigator は NL でスキル選択を案内＝tradesupport の MAGI車線にも近い UX 設計参考 |

#### 6.1.3 5公式ワークフロー → tradesupport 運用想定への対応

| claude-trading-skills ワークフロー | 目的 | tradesupport の対応 | 借用方針 |
|---|---|---|---|
| **market-regime-daily** | 15分の日次市場環境チェック | 朝5分ダッシュボード（既存設計） | **構成スキル列を参考**（breadth → uptrend → exposure-coach のチェイン） |
| **core-portfolio-weekly** | 長期PF の週次レビュー | 未設計 | **そのまま骨格借用**：portfolio-manager → kanchi-dividend-review-monitor → trader-memory-core |
| **swing-opportunity-daily** | リスク許容時のみ swing 候補発掘 | ZEELE車線（凍結中） | ZEELE解凍時に参照 |
| **trade-memory-loop** | クローズト全件の記録・学習 | P6（未着手） | **強借用候補**：trader-memory-core → signal-postmortem → trade-performance-coach |
| **monthly-performance-review** | 月次のルール遵守・改善 | 未設計 | **強借用候補**：trader-memory-core → signal-postmortem → trade-performance-coach → backtest-expert |

#### 6.1.4 借用方針（原則 D-21 適合）

- **コード直輸入はしない**。Claude Skills の SKILL.md（プロンプト+ガイドライン）は**思想と手順の蒸留物**として参考にし、tradesupport 側で **MAGI 原則（数値はコード／出典必須／総合スコアなし）に適合する形に翻案**して自前実装
- **ワークフロー骨格は直接借用可能**（YAML 5本の構造そのもの）。tradesupport の朝バッチDAGに移植
- **Kanchi T1-T5 の状態機械**（OK / WARN / REVIEW）は MAGI 車線の保有健全性アラートとして**ロジックを翻案して実装**
- **Exposure Coach** の "ネット上限/Growth-vs-Value/参加幅/新規エントリー可否" の判定は、MAGI 車線の規律監督ロジックとして**翻案実装**
- **Trader Memory + Signal Postmortem + Trade Performance Coach** の 3点セットは P6 評価・学習の実装テンプレ。**特に Trade Performance Coach の "next session operating rules" 出力**は原則7（人間主導A/B）と両立可能（agent が "操作ルールの提案"を出し、人間が採否を決める）

#### 6.1.5 SCORE:NONE 原則との衝突点

- `canslim-screener` `dividend-growth-pullback-screener` `stanley-druckenmiller-investment` は **composite score（0-100）を出力**。**ZEELE車線限定**で「参考スコア・未照合」バッジ付き表示なら可。MAGI車線への持ち込み禁止。
- `market-breadth-analyzer` `market-top-detector` `us-market-bubble-detector` は **composite_score を出力**するが、**入力が公開CSVベースで決定論的計算**。値はコードで確定→「複合スコア」ではなく「環境ラベル（NORMAL/CAUTION/HIGH/SEVERE）」として MAGI 車線で扱えば SCORE:NONE と両立可能（"スコア"でなく"状態"）。

#### 6.1.6 データソース要件と無料経路

- **API なし起点パス**：5スキル（market-breadth-analyzer / uptrend-analyzer / position-sizer / trader-memory-core / signal-postmortem）は **公開CSV＋ローカルYAML** のみ。借用コストが極小
- **有料API（オプション）**：FMP（$0〜・無料枠あり）・FINVIZ Elite（$39.5/月）・Alpaca（無料・米株のみ）
- tradesupport は**有料APIに依存しない経路を選択可能**（moomoo + yfinance + EDINET で代替）

### 6.2 dexter-jp — 日本株自律エージェント（直接相性）

**URL**：https://github.com/edinetdb/dexter-jp ／ ライセンス要確認 ／ TypeScript + LangChain

#### 6.2.1 設計の要点

- **agentic loop**：plan → multi-tool 並列呼出し → データ整合性検証 → ギャップ検出時iterate → **出典付き構造化レポート**
- **meta-tool routing**：`get_financials` 内部で LLM が sub-tools（financial statements / company info / key ratios / earnings）に dispatch
- **対応**：~3,800社 日本上場企業・33業種・100+指標
- **データ**：EDINET DB（有報・四報）・J-Quants V2（公式TSE OHLC）・TDnet（決算短信、EDINET経由）・Web Search（Exa/Perplexity/Tavily オプション）
- **LLM**：Claude / GPT-4o / Gemini / Grok / Ollama 切替可能（`/model` コマンド）
- **比較対象**：US版 dexter（既存§1-3）の **EDINET 置換＋日本国債利回り(~1%) DCF 再キャリブレーション**

#### 6.2.2 tradesupport との関係

- **既に EDINET を使用**（[mcp_tools/disclosure.py](../../../trading_agent/mcp_tools/disclosure.py)、[screening/edinet_xbrl.py](../../../trading_agent/screening/edinet_xbrl.py)）→ dexter-jp の EDINET 統合パターンが**直接参考**
- **MAGI BALTHASAR（ファンダ・出典照合）の実装テンプレ**：「データ整合性検証 → 出典付きレポート」は MAGI の核と完全一致
- **meta-tool routing パターン**：[mcp_tools/](../../../trading_agent/mcp_tools/) の構造化に応用可

#### 6.2.3 借用方針

- **agentic loop の骨格**（plan → call → verify → cite）を MAGI 朝バッチ DAG に翻案
- **meta-tool dispatch** を MCP Tool Layer の上位ルーターとして思想借用
- **コードは TypeScript→Python 移植が必要**。借用＝再実装

### 6.3 stock_skills（okikusan-public）— 守り系のみ採用

詳細は [architecture.html](../../../architecture.html) §7「アーキテクチャ拡張案（草案）：二車線設計 ZEELE × MAGI」を参照。要点のみ：

- **守り系（MAGI 車線へ）**：value_trap / shareholder_yield（配当+自社株買い）/ health_check / 調整アドバイザー17ルール / 複利シミュ / ストレステスト / concentration HHI — **決定論的計算でSCORE:NONE適合**
- **攻め系（不採用）**：16プリセット探索 / 0-100 割安度スコア / Grok生成見通し — **凍結思想と衝突**
- **借用方針**：claude-trading-skills と異なり、stock_skills は**個別モジュール単位**での借用（守り系のみ）

### 6.4 Claude Skills 兄弟群（参考）

- [yennanliu/InvestSkill](https://github.com/yennanliu/InvestSkill)（21米株分析フレーム・MIT）：DCF・テクニカル・インサイダー・短期売り・配当安全性等。**汎用分析の命名・出力テンプレ参考**
- [ancs21/ai-sub-invest（ConsensusAI）](https://github.com/ancs21/ai-sub-invest)（12ペルソナ+9分析・MIT）：ai-hedge-fund 着想の Claude Skills 版。**ペルソナ集約パターンの参考**
- [JoelLewis/finance_skills](https://github.com/JoelLewis/finance_skills)（84スキル・7ドメイン）：投資管理〜コンプライアンス〜業務まで超広範。**汎用すぎて借用対象外**
- [staskh/trading_skills](https://github.com/staskh/trading_skills)：Interactive Brokers連携・Options Greeks。**中期株式と不一致**
- [samyakjain0606/awesome-stock-skills](https://github.com/samyakjain0606/awesome-stock-skills)：インド株特化・Concallトランスクリプト。**市場対象外**

→ **claude-trading-skills が代表として吸収済み**。これらは命名・出力フォーマット程度の参考に留める。

### 6.5 既存ランキングへの影響

- **TradingAgents（◎1）の地位**：reflection 構造は引き続き P6 で借用候補。**ただし claude-trading-skills の `trader-memory-core` + `signal-postmortem` + `trade-performance-coach` 3点セットの方が個人運用向けに具体的**。両者は補完関係：TradingAgents = reflection の構造／claude-trading-skills = ジャーナリングの具体手順
- **ai-hedge-fund（◎2）の地位**：backtester 評価指標は引き続き P6-1 で借用候補。**ただし claude-trading-skills の `backtest-expert` も同等以上のガイダンス**。実装の重さで使い分け（backtester は Python コード、backtest-expert は SKILL.md ガイダンス）
- **FinRobot（△6）の地位**：Financial Chain-of-Thought は MAGI BALTHASAR の思考ステップ実装の参考。**dexter-jp の方が JP 特化で具体的なので、優先度を引き上げ**

### 6.6 X-2 申し送り（移植順序・更新版）

旧X-2 申し送り：
1. P6-1: ai-hedge-fund backtester 指標計算
2. P6-2/3: TradingAgents reflection 構造
3. P3反証: Bear論拠の組み立て方
4. P1-5/8: OpenBB provider 構造

**新 X-2 申し送り（claude-trading-skills 発見後）**：

**A. B0〜B6 並走可（即着手可能・MAGI車線の規律監督）**
1. **claude-trading-skills の workflow YAML 5本**を読み込み、tradesupport の朝バッチDAGの構成を再設計（特に `core-portfolio-weekly` / `trade-memory-loop` / `monthly-performance-review`）
2. **Exposure Coach の判定ロジック**を MAGI 車線の規律監督モジュールとして翻案実装（ネット上限／参加幅／新規エントリー可否）
3. **Kanchi T1-T5 状態機械**を MAGI 車線の保有健全性アラートとして翻案実装（OK / WARN / REVIEW）
4. **Trader Memory Core**（投資テーゼ ライフサイクル管理）を tradesupport の decisions テーブル拡張として実装

**B. ペーパー運用後（P6 評価・学習）**
5. **Signal Postmortem + Trade Performance Coach** を P6-2/3 の実装テンプレとして翻案
6. **dexter-jp の agentic loop + meta-tool routing** を MAGI BALTHASAR の実装パターンとして翻案
7. （既存）ai-hedge-fund backtester 指標計算 → P6-1
8. （既存）TradingAgents reflection 構造 → P6-2/3 補強

**C. ZEELE 車線解凍時（B0〜B6 完成後）**
9. **claude-trading-skills の edge-pipeline 7スキル群**を ZEELE 車線の発議エンジン設計の参考に
10. **vcp-screener / canslim-screener / breakout-trade-planner** を ZEELE 車線の swing 系スクリーナーとして翻案
11. **stock_skills の16プリセット**を ZEELE 車線へ移植

### 6.7 補強された結論

**「stock_skills と同等以上で参考になるリポジトリ」は複数存在し、特に思想で stock_skills を超えているのが claude-trading-skills**。stock_skills は割安発見器、claude-trading-skills は規律監督OS。tradesupport が目指しているのは後者なので、**北極星を claude-trading-skills に置き換え、stock_skills は守り系モジュール提供源に格下げ**するのが筋。

ただし、claude-trading-skills は**米株中心・FMP/Alpaca 前提**なので、日本株対応は dexter-jp が補完する。tradesupport の「米株+日株 by moomoo」という現実的なデータ基盤は維持しつつ、思想・ワークフロー・規律監督ロジックは claude-trading-skills から学ぶのが最効率。
