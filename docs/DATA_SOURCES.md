# データソース優先順位（v2.10）

正本日：2026-05-30
親原則：D-25「JP 90% / US ETF 10%」+ v2.10「成長株への投資・短期中期収益・MAGI+ZEELE でリスク分析」

---

## 0. 概観

| 層 | データ種別 | 主ソース | フォールバック | 補強 |
|---|---|---|---|---|
| 銘柄マスタ | listed_info | JPX 公式 Excel | yfinance.info | （J-Quants listed_info は将来） |
| 株価 (Universe 投入時) | 時価総額・出来高 | yfinance | — | — |
| 株価 (screening / MAGI BALTHASAR) | OHLC・履歴 | yfinance | — | （J-Quants Pro Light で改善可能） |
| 財務 (statements) | 売上/利益/EPS/BPS/CFO | **J-Quants** | yfinance.statements | EDINET（フラグ・反証） |
| 財務 (fundamentals) | 時価総額・PER・PBR 等 | yfinance | — | （J-Quants で取れる範囲は将来） |
| ニュース (CASPER / topics) | 記事タイトル・要約 | RSS + Google News RSS + yfinance.news | — | **NewsAPI**（v2.10 で補強） |
| 開示 (disclosure) | TDnet・EDINET 開示 | TDnet RSS + EDINET API | — | — |

---

## 1. 株価データソースの判断ロジック

```
[load_universe.py]
  JPX Excel から TOPIX 1000 + グロース市場の銘柄リスト
    ↓
  yfinance で時価総額・出来高・セクター取得
    ↓
  リスクフィルタ（市況>50億・売買代金>5000万円/日）で Universe テーブルに upsert

[screening_agent / MAGI BALTHASAR / yfinance 用途]
  yfinance.Ticker(symbol).info → 株価・出来高
  失敗時：銘柄を screening 対象から除外（自然脱落）
  → J-Quants の daily_quotes は Free Plan で 2 年遅延のため、現状利用しない
  → 将来：moomoo OpenD で約定価格と統一する案（コスト 0）
```

---

## 2. 財務データソースの判断ロジック

```
[screening/financials.py: fetch_financials]
  1. fetcher 注入あり? → そのまま使う（テスト互換）
  2. JP 株 (4桁ticker or 末尾英字) ? →
       a. J-Quants client があれば statements を取得
       b. revenue が None でないなら J-Quants 経由で Financials を返す（source="jquants"）
       c. それ以外（取れない/J-Quants 未設定）→ yfinance fallback
  3. US 株 → yfinance のみ

[J-Quants の statements で取れるフィールド]
  ✅ revenue (Sales) / net_income (NP) / ebit (OP) / total_assets (TA) / CFO
  ✅ EPS / BPS（shares は EPS から逆算）
  ❌ cogs / sga / gross_profit / depreciation / receivables / inventory / etc

[J-Quants で取れないフィールドの扱い]
  - PeriodFinancials の該当フィールドは None のまま（推測しない）
  - credibility (Beneish M / Altman Z) は欠損フィールドが多いと "warn" / "na" を返す
  - これは設計通り（データ不足 → 判定不能を明示）
```

### 末尾英字の成長株（新規上場暫定コード）への効果

```
yfinance: 末尾英字（KIOXIA/ASTROSCALE/SORACOM 等）は info / statements が 404
J-Quants: 末尾英字も含めて全 JP 銘柄の財務取得可能
→ v2.10 で「成長株の財務評価」が J-Quants 経由で動くようになった
```

---

## 3. ニュースデータソースの判断ロジック

```
[mcp_tools/news.py: _default_fetchers]
  1. yfinance.Ticker.news        ← 銘柄別・常時
  2. Google News RSS            ← 銘柄別・無料・日本語可
  3. 汎用 RSS (日経/Bloomberg/Reuters/TechCrunch/政府公式)
  4. NewsAPI                    ← key 設定時のみ・補強

[topics_collector + CASPER への影響]
  - NewsAPI 補強で「キーワード検索の網羅率」が上がる
  - 各 fetcher は独立に動き、失敗しても他は続行
  - CASPER の LLM 呼び出しには「材料以外の事実を生成しない」抑止プロンプトを既に明示
```

---

## 4. 認証情報の .env マッピング

| 環境変数 | 用途 | 必須 |
|---|---|---|
| `ANTHROPIC_API_KEY` | LLM（Claude） | ✅ 必須 |
| `JQUANTS_REFRESH_TOKEN` | J-Quants V2 API key (V1 名残のフィールド名・実態は API key) | 推奨 |
| `EDINET_API_KEY` | EDINET API v2 | 推奨 |
| `NEWSAPI_KEY` | NewsAPI (config 側は `newsapi_key`) | 任意 |
| `MOOMOO_TRADING_PWD` | moomoo OpenD 取引パスワード | 自動売買時必須 |
| `MOOMOO_ACCOUNT_ID` | moomoo 口座 ID | 自動売買時必須 |

---

## 5. ハルシネーション排除原則（v2.10）

| ルール | 実装場所 |
|---|---|
| データ取れない時は **None / 空 list** を返す（推測しない） | financials.py / jquants.py / news.py / 全 mcp_tools |
| API 失敗の例外文字列にトークンを含めない | jquants.py（`error_type=type(exc).__name__` のみログ） |
| LLM プロンプトに「材料以外の事実を生成しない」を明示 | casper_llm.py / topics_collector.py |
| screening 出力 / paper fill 直前に Universe 照合 | screening_agent.py / paper_exec.py |
| ticker 抽出は正規表現のみ（LLM 不使用） | topics_collector.py |
| Code 正規化（5桁⇄4桁）は想定外入力で無変換 | ticker_normalize.py |

---

## 6. 今後の拡張候補

| 候補 | 効果 | 工数 |
|---|---|---|
| moomoo OpenD で株価取得 | 自動売買と統一・無料 | 中（権限解決必要）|
| J-Quants Pro Light | 翌営業日の最新株価 | 小（プラン切替のみ）|
| load_universe.py に J-Quants listed_info | 末尾英字銘柄も Universe に取り込む | 中 |
| fundamentals.py に J-Quants | 時価総額・PER 等の補強 | 大（J-Quants の指数 API 別途）|
