# 0009 A-2 ニュース自前実装 — CASPER解放（精度最優先の最初の一手）

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「個人利用で外に出さないから、とにかく精度を優先して」
  - → 公開向け作業（LICENSE/README/免責）は後回し。精度ドライバーを優先（D-22として記録）。

## 判断（なぜA-2を最初に）
精度の最大の穴は **CASPERが盲目（銘柄別ニュース0件）→ MAGIが実質2.5脚**で立っていたこと。
ここを塞ぐのが精度に直結するため、D-22（精度最優先）の最初の実装としてA-2に着手。

## 実施内容
- `trading_agent/mcp_tools/news.py`
  - `_fetch_yf_news`：`yf.Ticker(_yf_symbol(t)).news` を取得。日本株（数字コード）は `.T` 付与。
    `_normalize_yf_item` が **旧形（フラット：providerPublishTime epoch）/新形（content入れ子：pubDate ISO）両対応**。
  - `_fetch_gnews_rss`：Google News RSS（`hl=ja&gl=JP`）。`_gnews_query(ticker, company)` で社名补完
    （`NewsInput.companies: dict[str,str]` を追加・任意。無くても ticker で動く）。`_normalize_gnews_entry` で正規化。
  - `_default_fetchers()` を `[_fetch_yf_news, _fetch_gnews_rss, _fetch_rss, _fetch_newsapi]` に
    （無料・キー不要・銘柄別を先頭）。既存の重複除去/言語判別/since/source_refs を流用。
- `tests/unit/test_news.py`：yf正規化（旧/新/空）・gnews正規化・検索語・default_fetchers構成・
  横断重複除去・CASPER結合（0件→na維持／記事>0→verdict）の新規テスト群を追加。
  併せて日付ロールオーバーに弱かった `_article` 既定 published を `utcnow()` 化（既存2件の偽陰性を解消）。
- ハルシネ防止：R2 各記事に url＋published(as_of)／R4 0件はna（捏造しない）／R5 summaryはソース提供文のまま。

## 実測（受入・計測可能）
- NVDA=**53件**・7203（トヨタ补完）=**48件**、`source_failures=0`。
- `casper('NVDA')` = **buy**（na脱出＝MAGIが3脚に）。
- 日本語ソースも取得（株探／Yahoo!ファイナンス／ダイヤモンド・オンライン／TradingKey 等）。
- 全スイート green、ruff clean。

## docs更新
- `DECISIONS.md` D-22（個人利用・精度最優先）確定。
- `spec/P1_collection.md` P1-6 を ✅、`spec/P3_magi.md` P3-3 を 🟡（入力解放・本格判定はP3-7 LLM待ち）。
- `IMPROVEMENT_PLAN_FOR_CODE.md` A-2 を ✅（実績追記）。

## コミット
- 本md＋news.py＋test_news.py＋DECISIONS(D-22)＋spec(P1-6/P3-3)＋IMPROVEMENT_PLAN を同一コミット。

## 状態/次
- CASPERの確信度は決定論キーワードゆえ「低」。本格化＝**P3-7 LLM解釈（CASPER=Sonnet）**（D-22の精度ドライバー①の仕上げ）。
- 次の貫通核は **A-4（MAGIをDAGに接続）**＝判断が実行経路に乗る（要ユーザー判断＝B6 DAG挿入）。
- 残り精度ドライバー：②MELCHIOR財務深掘り（P1-5）③評価/バックテスト（P6）④反証層（B群 P3-8〜12）。
