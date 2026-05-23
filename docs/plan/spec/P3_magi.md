# P3 MAGI判断 — フェーズ設計＋タスク設計

**フェーズの役割**：候補を「3審判の独立検証→防御層→統合機構→碇司令」に通し、決裁材料を作る（決定はしない）。
**全体ゴール寄与**：複数AIの合意＝正しい、ではないことに構造で対処（三権独立）。嘘の数字を決裁前に遮断。
**前からの引き継ぎ**：P2合格候補＋P1収集データ（出典/時点付き）。**次への引き渡し**：
judge_verdict×3／split_pattern／verification／commander_rec（→P4 decision化、U 詳細パネル）。
**フェーズのハルシネ防止方針**：R1〜R7 全部。特に R5（LLMは読む役）・R6（決裁前ゲート）・SCORE: NONE。

> 状態：✅実装済 / 🟡部分 / ❌未 / 🔵要判断 ／ コード：`trading_agent/magi/`、`models/magi.py`

### P3-1 MELCHIOR（業績審判）  〔✅ 多面化済（成長×収益性×健全性×CF）／一次情報深掘りはP1-5(b)〕
- 全体ゴール：業績（ファンダ）の裏付けの可否を独立に出す。
- 前からの引き継ぎ：**fundamentals のみ**（P1-4/将来P1-5）。他審判の結論は受け取らない（独立）。
- 目的：増収率・営業利益率等から buy/hold/warn/na と確信度(定性)を出す。
- 実装：`magi/judges.py::melchior`。数値はコード、根拠文もコード生成（現状LLM不使用）。
- 次への引き渡し：`JudgeVerdict(judge=MELCHIOR, verdict, confidence, reason, source_refs, data_asof)`。
- ハルシネ防止：R1数値はfundamentals値のみ／R2出典・報告期を引継ぎ／R4欠損はna／R7 fundamentals以外見ない。
- 受入：強財務→buy・減収→warn・欠損→na（既存テスト green）。
- **多面化実績（2026-05-23・P1-5(a)）**：判定ロジックを2指標→多面ルーブリックに刷新。成長(増収/増益)・
  収益性(各マージン/ROE)・健全性(D/E>2・流動比率<1・FCFマイナス)を集約し、**赤が1つでもあれば warn 寄り**（財務は保守的）。
  確信度は使えた指標数と一貫性に連動。reasonは全指標を値つきで列挙（数値はコード値＝R1）。**残**：一次情報(EDGAR/EDINET)はP1-5(b)。

### P3-2 BALTHASAR（株価審判）  〔✅〕
- 全体ゴール：株価（テクニカル）の勢い・需給の可否を独立に出す。
- 前からの引き継ぎ：**technicals のみ**（P1-3）。
- 目的：シグナル/RSI等から buy/hold/warn/na。
- 実装：`magi/judges.py::balthasar`（D-10：コード計算＋将来LLM解釈）。
- 次への引き渡し：`JudgeVerdict(BALTHASAR,...)`。
- ハルシネ防止：R1 計算はコード／R7 technicals以外見ない。
- 受入：golden_cross→buy・overbought→warn・欠損→na（既存テスト green）。

### P3-3 CASPER（文脈審判）  〔✅ 入力解放（A-2）＋本格判定（P3-7 LLM=Sonnet）実装済〕
- 全体ゴール：文脈・イベント（なぜ動くか）の可否を独立に出す。
- 前からの引き継ぎ：**news/disclosure/manual/macro のみ**（P1-6/7/8/11）。
- 目的：材料の方向（ポジ/ネガ）から可否。**本格判定はLLM解釈（P3-7）で補完**。
- 実装：`magi/judges.py::casper`（現状：決定論キーワード＝確信度低）。
- 次への引き渡し：`JudgeVerdict(CASPER,...)`＋記事の出典。
- ハルシネ防止：R4 材料0なら na（埋めない＝現状そうなっている）／R2 各主張に記事URL/時点を紐付け。
- 受入：P1-6実装後、NVDA等で材料>0→naを脱し方向を出す。**今の穴の本体はP1-6**。→ **A-2で解放（2026-05-23）：NVDA53件でCASPER=buy（na脱出）**。残るのは確信度（決定論=低）→P3-7でLLM解釈に格上げ。

### P3-4 防御層（機械照合・決裁前ゲート）  〔✅〕
- 全体ゴール：混入した嘘の数字・古い時点・未照合を決裁直前に検出・遮断。
- 前からの引き継ぎ：judge_verdict×3（P3-1/2/3）＋（将来）P2-3信用性。
- 目的：数値照合/出典実在/時点照合＋割れ/na/未照合で既定保留。
- 実装：`magi/defense.py::verify`→`Verification`。
- 次への引き渡し：`Verification(figures_checked, unverified_claims, credibility_flag, time_ok, default_hold)`（→P4/U フラグ）。
- ハルシネ防止：R2/R3/R6 そのもの。LLM非関与のコード照合。
- 受入：出典なし/時点なし/割れ/na→default_hold=True（既存テスト green）。

### P3-5 統合機構（割れ方）  〔✅〕
- 全体ゴール：3審判を1点に統合せず、割れ方を類型化して可視化（推奨はしない）。
- 前からの引き継ぎ：judge_verdict×3。
- 目的：4類型（一致/業績◯株価✕/株価◯業績✕/文脈✕）＋label。**総合スコアを出さない**。
- 実装：`magi/integration.py::classify_split`→`SplitPattern`。
- 次への引き渡し：`SplitPattern(agree_count, label, interpretation)`（→P4/U フッタ・一覧）。
- ハルシネ防止：SCORE: NONE（採点しない）／R1 集計のみ。
- 受入：一致/各割れで正しい interpretation（既存テスト green）。

### P3-6 碇司令（推奨＋反対論拠）  〔🟡 コード版〕
- 全体ゴール：MAGI出力だけを根拠に推奨と反対論拠を必ず両方出す（決定はしない）。
- 前からの引き継ぎ：judge_verdict×3＋SplitPattern＋Verification（**MAGIの出力のみ**）。
- 目的：推奨（方向＋理由）＋「反対するなら：」＋根拠注記。
- 実装：`magi/commander.py::command`→`CommanderRec`（現状：決定論生成＝実費0。LLM文面化はP3-7）。
- 次への引き渡し：`CommanderRec(recommendation, counter_argument, magi_compliant, src_note)`（→P4/U 碇ゾーン）。
- ハルシネ防止：R5 MAGI外の新事実を加えない（決定論版は構造上True）。LLM版はP3-7で碇MAGI準拠を機械照合。
- 受入：常に反対論拠を併記・割れ/na時は保留推奨（既存テスト green）。

### P3-7 LLM解釈（審判の文章化・碇の文面化）  〔🟡 CASPER=Sonnet実装済（2026-05-23）／MELCHIOR・碇は未〕
- 全体ゴール：解釈文の質を上げる（数値はコードのまま）。
- 前からの引き継ぎ：各審判のコード判定＋実データ／碇のコード推奨。
- 目的：CASPER＝Sonnet(文脈解釈)、MELCHIOR＝Ollama(業績解釈)、碇＝文面化。**数値は生成させない**。
- 実装：`mcp_tools/llm_call.py` 経由（コストロガー）。審判別モデル割当（D-10/D-11）。
- 次への引き渡し：reason/recommendation の自然文（数値は元のコード値を保持）。
- ハルシネ防止：R5 LLMは渡された実データの解釈のみ／**碇MAGI準拠を機械照合（D-15）**＝LLM出力中の数値/事実がjudge_verdict範囲内かをコードで検証、外れたら`gendo_compliant=false`で赤。
- 受入：LLM接続時に解釈文が実物化し、MAGI外創作が検出される。コスト日次¥500で停止。
- **CASPER実績（2026-05-23）**：`magi/casper_llm.py::casper_llm`（非同期・オプトイン）。Anthropicキーがある時のみ
  `LLMCallTool`（予算ガード＋コスト記録）経由でSonnet解釈に格上げ。出力は厳密JSON（verdict/confidence/reason）を
  列挙値検証してパース。**材料0・予算超過・接続失敗・パース失敗は全て決定論版 `casper()` にフォールバック**。
  source_refs/data_asof は入力ニュース由来を維持（R5：出典を捏造しない）。build_snapshot が live＋キー時に自動格上げ。
  実測：NVDAでreasonが「直近N件…」→「1Q決算 売上85%増・営業益2.5倍…ポジ材料が優勢」へ実物化（数値はソース由来の引用）。
  新規12テスト（_parse 7・casper_llm 5：正常/0件/パース不可/接続失敗/予算超過）。**残**：MELCHIOR=Ollama・碇文面化・碇MAGI準拠の機械照合（D-15）。

---

## 反証層（B群）〔❌ 未／詳細は `../IMPROVEMENT_PLAN_FOR_CODE.md` B群〕
三権独立を**壊さず**、各審判が自領域データ内で「判定と逆向きの事実」を**摘出**する（創作でなくR5順守）。
TradingAgents の Bear論拠の組み立て方を翻案（別Bear体は置かない）。着手条件＝A-4（DAG接続）完了後。
- **P3-8 反証フィールド**：`JudgeVerdict.counter_within_domain: list[dict]`（claim＋source_refs）。
- **P3-9 BALTHASAR反証（コード）**：buy寄りでも過熱/ダイバージェンス/出来高減を technicals から摘出。
- **P3-10 MELCHIOR/CASPER反証（LLM摘出＋コード照合）**：「逆向きの事実をデータから選べ・創作禁止」。
  予測語を弾き source_refs 必須、防御層で実在照合（不通過は捨てる）。
- **P3-11 碇が反証を束ねる**：碇の独自生成→各審判の counter_within_domain 集約に。
- **P3-12 統合：全会一致でも内在不安**：全員が割高/過熱を摘出した時の interpretation（推奨はしない）。
