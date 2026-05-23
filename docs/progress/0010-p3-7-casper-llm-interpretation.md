# 0010 P3-7 CASPER LLM解釈 — 文脈審判を「数える」から「読む」へ

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- （次の精度施策の選択で）「P3-7 CASPER LLM解釈」を選択。
- 「とりあえず、計画に基づいて自走できるところまでは自走して。完了したらまとめて報告して。
  また、セクション区切りでコミットして。コミットに関しては全て許可する」

## 判断（なぜCASPER LLMを優先）
A-2でCASPERは入力を得たが、判定は**キーワード計数（確信度=低）**のまま。
「訴訟を回避」をネガと誤読しうる粗さがあり、これは精度の穴。Sonnet解釈で方向＋根拠の質を上げる
＝D-22 精度ドライバー①の仕上げ。

## 実施内容
- 新規 `trading_agent/magi/casper_llm.py`：`casper_llm(ticker, *, news, disclosure, llm_tool)`（非同期・オプトイン）。
  - 記事/開示を集約→Sonnetに渡し、**厳密JSON**（verdict/confidence/reason）で受け取り列挙値検証してパース。
  - **R5順守**：与えられた材料だけを読む。数値・新事実は生成させない（systemで明示）。source_refs/data_asof は
    入力ニュース由来を維持（出典を捏造しない土台）。
  - **フォールバック設計**：材料0・予算超過・接続失敗・認証失敗・パース失敗は**すべて決定論版 `casper()`**に落ちる
    ＝LLMが使えない/暴れた時もCASPERは必ず素材に根ざした判定を返す。
  - `LLMCallTool` 経由＝予算ガード（日次¥500）＋cost_logs記録。CASPER=Sonnet（routing_hint="hot"）。
- `magi/__init__.py` に `casper_llm` を公開。
- `scripts/build_snapshot.py`：`_maybe_llm_tool()`（Anthropicキー＋live時のみ生成）を追加し、
  `_build_candidates` で run_judges 後に CASPER 判定だけ格上げ（キー無し時は決定論のまま・コスト0）。
  併せて当ファイルの既存E501 3件（pre-existing lint debt）も整形。
- テスト `tests/unit/test_casper_llm.py` 12件：`_parse`（正常/コードフェンス/前後文/不正verdict/不正confidence/空reason/非JSON）
  7件＋`casper_llm`（正常/0件でLLM未呼/パース不可→決定論/接続失敗→決定論/予算超過→決定論）5件。

## 実測（受入）
- ライブ build_snapshot で CASPER の reason が
  「直近N件の材料（ネガ語X・ポジ語Y）」→
  **「1Q決算 売上85%増・営業益2.5倍…増配…52週高値更新…ポジ材料が優勢」**へ実物化。
  数値はソース（株探見出し）由来の引用＝R5順守（読む・創作しない）。
- 全スイート green、ruff clean。

## docs更新
- `spec/P3_magi.md` P3-3 を ✅、P3-7 を 🟡（CASPER=Sonnet実装済／MELCHIOR=Ollama・碇文面化は未）。

## コミット
- 本md＋casper_llm.py＋__init__.py＋build_snapshot.py＋test_casper_llm.py＋spec(P3)を同一コミット。

## 状態/次
- 残るP3-7：MELCHIOR=Ollama解釈・碇の文面化・**碇MAGI準拠の機械照合（D-15）**。
- 次の自走：**P1-5 MELCHIOR財務素材の深掘り**（無料・判断不要＝精度ドライバー②）。
- 確信度（confidence）はJudgeVerdictに保持。UI出し分け（D-11 高/中/低）は後続のUI調整で。
