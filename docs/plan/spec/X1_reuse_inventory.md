# X-1 外部OSS棚卸し調査 ── 借用候補と移植先マッピング

作成日：2026-05-23
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
| ◎1 | **TradingAgents** | reflection（過去判断の振り返り注入）構造／Bear論拠の組み立て方 | P6-2/P6-3・碇/反証 | 学習の穴とBear反証に直結。原則7と両立可 |
| ◎2 | **ai-hedge-fund** | backtester の評価指標計算部分／ペルソナの財務チェックリスト | P6-1・MELCHIOR深掘り | P6の空白を最小コストで埋める |
| ○3 | **FINSABER** | 批判的バックテスト思想（survivorship/data-snooping回避） | P6設計の前提 | 「過信しない」原則の実装ガード |
| ○4 | **OpenBB** | データprovider層（EDGAR/FRED等の正規化済み接続） | P1-5/P1-8 | 一次情報・マクロの接続を自前実装せず借用 |
| △5 | **StockBench** | 評価ベンチマークの観点 | P6評価設計 | 評価軸の参考（移植でなく思想） |
| △6 | **FinRobot** | 財務分析エージェントの分業設計 | P3-1/P3-7 | MELCHIOR深掘りの補助（優先度低） |
| ✕ | ATLAS / 各種bot | 自己進化・自動売買・クリプト | （不採用） | 不変原則（自己改変不採用・Tier1手動）と衝突 |

**今すぐ着手すべき借用はない。** A群（断線解消）完了後、P6着手のタイミングで ◎1/◎2 から入る。

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
