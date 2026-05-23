# 0034 EDINETキー稼働＋開示をMAGI信用性に配線（S4b 完了線）

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- （EDINETキーについて）「取得して反映した」（.env に EDINET_API_KEY 設定）。
- 直前に `https://edinetdb.jp/developers` を提示し「これでいい？」→ 私の推奨は**公式 金融庁**。

## 確認（推測でなく実測）
- `.env` の EDINET_API_KEY をロード確認（値は出さず：bool=True・len=32）。
- どの提供元か実測プローブ（statusのみ）：
  - **official/header（api.edinet-fsa.go.jp v2・`Ocp-Apim-Subscription-Key`）= 200・228件** ← これ
  - official/query（Subscription-Key）= 200（Azure APIMで両形可）
  - edinetdb.jp/v1（X-API-Key）= 404
  → **取得されたのは公式 金融庁のキー**。**現コードの実装（ヘッダ形）が正しい**ことも実証（以前の懸念は解消）。
- 実データで S4b 走査を検証：2024-05-22 の228件から **`訂正有価証券報告書` を検知**（D-14レッドフラグ end-to-end OK）。

## 実施内容（live配線）
- `mcp_tools/disclosure.py`：EDINET の `docDescription` が実データで None になる事故を修正（`or ""`）。
- `magi/persist.py`：`make_live_judge_fn` の信用性ステップで **開示を call_tool("disclosure") で取得**し
  `assess_credibility(disclosures=)` に渡す（`_fetch_disclosures` 追加）。
  ＝EDINET/TDnet の **GC注記/訂正/上場廃止 等が credibility warn → default_hold（保留）** と **MELCHIOR反証** に反映。
  call_tool 経由なのでテストはモックで**ネット非依存**（既存 _mock_host の disclosure が効く）。
- テスト `tests/unit/test_magi_persist.py` +1（開示GC注記→credibility warn＋MELCHIOR反証に「開示レッドフラグ」）。

## 受入
- 全スイート green（361）。touched ファイル ruff clean。
- これで S4b の「メタデータ走査」は EDINET 実データで稼働（XBRL本文の深掘りは将来）。

## 残り（正直に）
- 監査意見/GC注記の**本文（XBRL/ZIP）抽出**は未（現状は docDescription のキーワード走査）。要・追加実装。
- 朝バッチ実運用で `run_morning_batch(financials_fetcher=fetch_financials)` を渡せば、JP候補に EDINET 信用性が乗る
  （既定はOFF＝負荷/コスト管理。ON化はあなたの運用判断）。

## architecture.html
- §0：doc同期行のハッシュ確定＋「EDINET配線」行を追加。

## コミット
- 本md＋disclosure.py＋persist.py＋test_magi_persist.py＋architecture.html＋progress/README を同一コミット。
