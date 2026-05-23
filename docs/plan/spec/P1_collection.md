# P1 データ収集 — フェーズ設計＋タスク設計

**フェーズの役割**：全市場の実データを、**出典(source_refs)・時点(data_asof)付きで**集める。
**全体ゴール寄与**：MAGI判断・サイジング・評価の"餌"を、後段が機械照合できる形で供給する。
**前からの引き継ぎ**：対象 ticker（P2/P3が指定）。**次への引き渡し**：相場/財務/文脈/口座データ＋出典/時点。
**フェーズのハルシネ防止方針**：R1(数値はコード取得)・R2(出典/時点必須)・R3(2ソース)・R4(欠損はna)。
LLMはこのフェーズで一切使わない（収集はすべてAPI/コード）。

> 状態：✅実装済 / 🟡部分 / ❌未 / 🔵要判断

### P1-1 リアルタイム株価・出来高  〔✅（yfinance）/ moomoo差替は🟡〕
- 全体ゴール：BALTHASAR・サイジング・評価の基準価格を供給。
- 前からの引き継ぎ：ticker リスト。
- 目的：現在値・出来高・前日終値等を取得。
- 実装：`mcp_tools/market_data.py`。設計の主＝moomoo（同意②後）、現状＝yfinance＋stooq。MarketDataCache に保存。
- 次への引き渡し：`{ticker:{current_price,...}}`＋`data_asof`＋`source_refs(source=yfinance/memory/db_cache)`＋`reconciliation`。
- ハルシネ防止：R1取得値のみ／R3 stooqと±0.5%照合→mismatch記録／R2 ticker毎の時点付与。
- 受入：1銘柄で価格＋時点＋出典＋reconciliation を返す（既存テスト green）。

### P1-2 過去K線（履歴）  〔🟡 US実用／JP未〕
- 全体ゴール：テクニカル計算・バックテストの素材。
- 前からの引き継ぎ：ticker, period。
- 目的：終値系列（古→新）を取得。
- 実装：`mcp_tools/technicals.py` の history_provider（現状yfinance）。設計：moomoo K線/J-Quants(JP)。
- 次への引き渡し：終値list（→P1-3が消費）。
- ハルシネ防止：R1実価格のみ／R4 欠損時は空→指標na。
- 受入：US銘柄で履歴取得→指標計算が成立。JPは J-Quants 設定後。

### P1-3 テクニカル指標  〔✅〕
- 全体ゴール：BALTHASARの数値根拠（コード計算＝LLM非関与）。
- 前からの引き継ぎ：終値系列（P1-2）。
- 目的：RSI/MACD/SMA/Bollinger＋シグナル名（golden_cross等）を計算。
- 実装：`mcp_tools/technicals.py`（numpy/pandas自前計算）。
- 次への引き渡し：`{rsi,macd,...}`＋`signals[]`＋`data_asof`＋`source_refs(source=computed)`。
- ハルシネ防止：R1全てコード計算（LLMに計算させない）／R2 computed＋時点を付与。
- 受入：固定系列で既知値、シグナル名が出る（既存テスト green）。

### P1-4 財務サマリ  〔🟡 浅い〕
- 全体ゴール：MELCHIORの数値根拠。
- 前からの引き継ぎ：ticker。
- 目的：PER/PBR/EPS/ROE/増収率/営業利益率/配当/売上/純利益を取得。
- 実装：`mcp_tools/fundamentals.py`（yfinance .info）。`fiscal_period` を時点に。
- 次への引き渡し：`{revenue_growth,operating_margin,...}`＋`fiscal_period`＋`source_refs(as_of=報告期)`。
- ハルシネ防止：R1取得値のみ／R2 報告期(fiscal_period)を as_of に保持（四半期取り違え対策）。
- 受入：実数値＋報告期が返る（既存テスト green）。**過不足**：一次情報未解析＝浅い→P1-5で補完。

### P1-5 財務 一次情報（深掘り）  〔❌ 未解析〕
- 全体ゴール：MELCHIORの深掘り＋P2-3信用性の判定材料。
- 前からの引き継ぎ：ticker（市場でUS/JP分岐）。
- 目的：SEC EDGAR(10-K/10-Q/8-K)・EDINET(有報/四報/監査意見/GC注記)を取得・解析。
- 実装：`fundamentals.py`/`disclosure.py` に EDGAR/EDINET provider 追加（XBRL/書類）。要 EDINETキー。
- 次への引き渡し：深掘り財務＋監査意見/GC注記フラグ＋出典URL＋時点（→P2-3, P3-1）。
- ハルシネ防止：R2 出典URL実在確認／R5 LLMで要約する場合も数値はXBRL値のみ／R4 取得不能はna。
- 受入：1銘柄でEDGAR/EDINETの一次情報が取得され、監査意見/GC注記が判定できる。

### P1-6 ニュース収集（銘柄別）  〔✅ A-2実装済（yfinance＋GoogleNews自前）／★本書の"深さ"の見本〕
- **全体ゴール**：CASPER（文脈審判）に"なぜ動くか"の一次材料を供給し、MAGIを3脚で立たせる。
- **前からの引き継ぎ（入力契約）**：
  `NewsInput{ tickers:list[str], topics:list[str]|None, since:datetime|None(既定 now-72h), sources:list[str]|None }`。
  銘柄→社名補完に `universe.name`（あれば。無くても ticker で動く）。
- **目的**：銘柄別ニュースを**無料・キー不要**で取得→正規化→重複除去→期間/銘柄フィルタ。
- **詳細仕様（ソース別）**：
  1. **yfinance**：`yf.Ticker(t).news` → `[{title,publisher,link,providerPublishTime(epoch),type,relatedTickers}]`。
     正規化 `Article{title, summary="", source=publisher, url=link, published_at=epoch→ISO}`。関数 `_fetch_yf_news(inp)->list[Article]`。
  2. **Google News RSS**：`https://news.google.com/rss/search?q={q}&hl=ja&gl=JP&ceid=JP:ja`、
     `q=f"{ticker} {company} 株 OR stock"`。feedparserで entries→`Article{title, summary=entry.summary, source=entry.source.title or "GoogleNews", url=entry.link, published_at=entry.published}`。関数 `_fetch_gnews_rss(inp)->list[Article]`。
  3. 既定fetchersに上記2を追加（NewsAPIはキーがある時のみ）。
- **正規化・重複除去**：既存 `dedupe_articles`（URL一致／見出しmd5／difflib類似>0.90）＋`detect_language`＋`since`フィルタ＋published降順。
- **次への引き渡し（出力契約）**：
  `NewsOutput{ articles:list[Article], total_before_dedupe:int, data_asof=max(published), source_refs=[SourceRef(source, ref=url, as_of=published)] }`（→P3-3 CASPER、topics_collector→Topic保存）。
- **アルゴリズム（関連度）**：title+summary に ticker か company を含む記事を残す（`_matches_filters`）。重要度は topics_collector のルール（決算/FOMC=高）。
- **エッジ・失敗**：記事0→`articles=[]`（CASPERはna）。JP記事→language=ja。要約空(paywall)→titleのみで判定。yf/gnews片方失敗→他方で継続(graceful)。両方失敗→NetworkError→baseリトライ。
- **テスト（tests/unit/test_news.py 追加）**：①`_fetch_yf_news` 正規化 ②`_fetch_gnews_rss` 正規化 ③銘柄フィルタがNVDA記事を残す ④yf+gnens横断の重複除去 ⑤0件→CASPER na維持 ⑥(結合)news>0→CASPER verdict。
- **受入条件（計測可能）**：NVDAで記事≥1、7203（社名トヨタ補完）で記事≥1、`casper()` が na を脱し方向を返す。新規6テスト＋既存 green。
  - **実測（2026-05-23 A-2）**：NVDA=53件・7203=48件（source_failures=0）、`casper('NVDA')`=buy（na脱出）。新規テスト17・全スイート green。日本語ソース（株探/Yahoo!ファイナンス/ダイヤモンド等）も取得。
- **ハルシネ防止**：R2 各記事に url＋published(as_of)必須／R4 0件はna（記事を捏造しない）／R5 summaryはソース提供文のまま（LLM生成しない）。
- **依存**：なし（無料・キー不要）。**規模**：小〜中。**想定差分**：`mcp_tools/news.py`, `tests/unit/test_news.py`。

### P1-7 適時開示  〔❌ 0件〕
- 全体ゴール：CASPERのイベント材料＋P2-3信用性。
- 前からの引き継ぎ：ticker（JP中心）。
- 目的：TDnet(JP)/SEC 8-K(US) の適時開示を取得。
- 実装：`mcp_tools/disclosure.py`（TDnet RSS＋EDINET、要キー）。
- 次への引き渡し：`disclosures[]`＋出典/時点（→P3-3, P2-3）。
- ハルシネ防止：R2 開示URL＋時点／R4 取得不能はna。
- 受入：JP銘柄で開示>0（キー設定後）。

### P1-8 マクロ・政府公式  〔❌ 未配線〕
- 全体ゴール：地合い・政策の文脈（CASPER/重要度判定）。
- 前からの引き継ぎ：（銘柄非依存・全体）。
- 目的：FRB/財務省/ホワイトハウス等の公式RSS・FOMC/金利/CPIを収集。
- 実装：`news.py` にマクロ用RSSソース追加＋重要度ルール。
- 次への引き渡し：マクロトピック＋出典/時点（→P3-3 文脈の地合い）。
- ハルシネ防止：R2 出典/時点／R5 解釈はLLMでも事実は出典に紐付け。
- 受入：FOMC等のマクロが収集され重要度高で出る。

### P1-9 決算・イベントカレンダー  〔🟡 枠のみ〕
- 全体ゴール：決算前後の判断タイミング・注視。
- 前からの引き継ぎ：ticker。
- 目的：決算日・配当・イベントを取得。
- 実装：`models/earnings_calendar`＋取得provider（yfinance/moomoo）。
- 次への引き渡し：イベント日付（→P3-3/P5注視）。
- ハルシネ防止：R1日付は取得値のみ／R2出典。
- 受入：銘柄の次回決算日が入る。

### P1-10 口座（保有・残高・約定）  〔🟡 接続済・空〕
- 全体ゴール：含み損益・配分・サイジング・発注記録。
- 前からの引き継ぎ：（口座）。
- 目的：moomoo positions/account を取得（口座未接続は StandIn ¥1M・保有0）。
- 実装：`brokers/`（moomoo broker_read／StandIn）。`load_positions`。
- 次への引き渡し：positions（code/qty/cost_price/pl_ratio）＋account（cash/total）（→P4サイジング, U保有）。
- ハルシネ防止：R1 取得値のみ／空はサンプルと**明示**（誤認防止＝接続状態表示）／R4 未接続はStandIn。
- 受入：StandInで¥1M・空、moomoo接続時に実保有（同意②後）。

### P1-11 手動投入（X代替）  〔🟡 UIのみ〕
- 全体ゴール：速報の人手補完（X直接利用は不採用）。
- 前からの引き継ぎ：人間の貼り付けテキスト。
- 目的：貼られた情報をCASPER相当が即分析。
- 実装：`agents/manual_input_analyst.py`＋UI配線（現状UIモック）。
- 次への引き渡し：解釈（影響/方向/対象ticker）＋出典（人手）（→P3-3補助）。
- ハルシネ防止：R5 LLMは貼付内容の解釈のみ・新事実禁止／R4 不明は不明。
- 受入：テキスト投入→影響/方向/対象が出る。

### P1-12 2ソース照合・出典/時点（横断）  〔✅〕
- 全体ゴール：防御層(P3-4)の機械照合の土台。
- 前からの引き継ぎ：各収集ツールの生データ。
- 目的：重要数値の2ソース照合＋全出力に出典/時点を付与。
- 実装：`mcp_tools/base.py`(SourceRef/data_asof)＋market_dataの reconcile。
- 次への引き渡し：`source_refs`/`data_asof`/`reconciliation` を全データに付帯（→全下流）。
- ハルシネ防止：R2/R3 そのもの（この仕組みが防止の中核）。
- 受入：価格 ±0.5%照合、全ツールが出典/時点を返す（既存テスト green）。
