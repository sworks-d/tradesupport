# 0039 S4b深掘り — EDINET書類本文(XBRL/CSV)からGC注記・監査意見を検出

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「可能な限り自走して後で報告して」。EDINETキー稼働後の深掘り。

## 実施内容
- 新規 `screening/edinet_xbrl.py`：
  - `detect_disclosure_flags_from_xbrl(rows)`：XBRL行（要素ID/値）から **GC注記**（要素ID に GoingConcern かつ値非空）
    と **監査意見の警戒種別**（不適正/意見不表明/限定付）をコード検出。空・プレースホルダは除外。LLM非関与。
  - `parse_edinet_csv_zip(data)`：EDINET type=5 ZIP（タブ区切りCSV群）を {element_id, value} に。
    エンコーディングは **BOMで utf-16 判定**＋cp932/utf-8フォールバック（cp932をutf-16で誤読する事故を回避）。
  - `fetch_document_flags(doc_id, downloader=)`：DL→解析→検出（DL注入でhermetic・失敗は空）。
  - `edinet_csv_downloader(api_key)`：公式v2 `documents/{docID}?type=5` の実DL（要キー）。
- `screening/__init__.py` で公開。テスト `tests/unit/test_edinet_xbrl.py` 9
  （GC検出・空は非検出・不適正監査意見・健全は空／タブCSV解析・cp932・不正ZIP空／end-to-end・DL失敗graceful）。

## 受入
- 全スイート green（389）。touched ファイル ruff clean。

## 留意（残り）
- これは**検出器＋DL**。実運用で「各候補の最新有報docIDを引き当て→DL→flags→credibility」まで結ぶ自動配線は
  重い（doc検索＋DL量）ので別途。メタ走査(scan_disclosure_red_flags・配線済)で第1段は既に効いている。

## architecture.html
- §0：定期実行行のハッシュ確定＋「XBRL深掘り」行。

## 状態（自走の区切り）
- 研究ロードマップの主要項目（貫通/外骨格/餌/弾＝信用性M/F/Z・反証・V字・テーマ相対力・EDINET深掘り/P6評価/定期実行）を実装。
- 残りの本格運用配線（XBRL自動引き当て・Track RecordのUI・record_entryの発注フック）は運用判断つきで後続。
