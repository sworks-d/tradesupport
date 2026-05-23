# 0015 B-1/B-2 反証層の土台 — BALTHASARが自領域の逆向き事実を摘出

日付：2026-05-23 ／ ブランチ：`feat/magi-rebuild`

## ユーザーが与えた指示
- 「推奨で再開して」（＝前回報告の推奨＝B群反証層）。
- 「docs/reserchにmdを追加したので確認して」→ **docs/research は空**で、ディスク上に該当mdが見つからず
  （保存されていない/別名・別場所の可能性）。確認できない旨を伝え、明示指示どおりB群を進行。再共有あれば反映する。

## 判断（B群の着手順）
B群は「三権独立を壊さず、各審判が自領域データ内で『判定と逆向きの事実』を摘出」（確証バイアス対策）。
最初は**決定論・即demonstrable・LLM不要**の B-1（反証フィールド）＋B-2（BALTHASARコード反証）から。
LLMを使う B-3（MELCHIOR/CASPER反証）は繊細なため後段に分離。

## 実施内容
- `models/magi.py`：`JudgeVerdict.counter_within_domain: list[dict]`（JSON）を追加。各項目 `{claim, source_refs}`。
  無ければ空＝データ上は一貫（R4）。`persist_bundle` 経由で decision_id 付き永続化（既存経路で自動保存）。
- `magi/judges.py`：`_balthasar_counter(verdict, data, signals, refs)` を新設し `balthasar` に組込み。
  - buy寄り→ RSI過熱(≥70)／弱気ダイバージェンス(macd_bearish)／上限突破=割高／デッドクロス併存／下限割れ。
  - warn・sell寄り→ 売られすぎ(RSI≤30)＝反発余地／ゴールデンクロス／macd強気／上限突破。
  - **全てコード・実シグナル/実RSIに基づく事実のみ（R1/R5＝創作しない）**。各反証に source_refs を付与。
- テスト：`test_judges.py` +5（macd弱気ダイバージェンス・割高・売られすぎ反発・強気・一貫時は空）、
  `test_magi_persist.py` +1（counter_within_domain の decision_id 付きroundtrip）。

## 実測（受入）
- 全スイート green（287）。触ったファイル ruff clean。
- ライブ：AAPL=warn だが「MACDヒストグラムが強気（勢いは改善）」を反証提示。NVDA/7203 は一貫→反証なし。

## 設計上の注記
- 出来高トレンドは technicals ツールが未供給のため反証対象外（R4：無いものは出さない）。供給追加時に拡張可。
- buy判定はロジック上 overbought_rsi を除外するため「RSI過熱」反証は実質デッドコードだが、防御的に残置（無害）。

## docs更新
- `spec/P3_magi.md` P3-8/P3-9 を ✅。`IMPROVEMENT_PLAN_FOR_CODE.md` B-1/B-2 を ✅（実績追記）。

## コミット
- 本md＋models/magi.py＋judges.py＋test_judges.py＋test_magi_persist.py＋spec/P3＋IMPROVEMENT_PLAN＋README を同一コミット。

## 状態/次
- 次：**B-4（碇が各審判の反証を束ねる）＋B-5（統合機構：全会一致でも全員が割高/過熱を摘出→内在不安）**＝決定論で即着手可。
- その後：**B-3（MELCHIOR/CASPER のLLM反証＋防御層での実在照合）**＝LLM・繊細（創作を照合で弾く）。
- 申し送り：docs/research のmdが保存され次第、設計に反映。
