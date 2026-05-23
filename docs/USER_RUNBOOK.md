# ユーザー手順書（あなたがやること）

実装はほぼ完了。ここからは**あなたの操作・判断**で進める。上から順に。
原則：**自動化するのは判断生成と評価まで。発注・決裁は moomoo 画面で手動**（自動発注しない＝Tier1）。

---

## フェーズ0：準備（最初に一度だけ）
- [x] `.env` 設定（ANTHROPIC / MOOMOO / EDINET）← **済**
- [ ] 依存の同期：`uv sync`
- [ ] 母集団を投入：`.venv/bin/python scripts/load_universe.py`
  - JP主体（14）＋US（6）の実在大型株が `data/trading.sqlite` に入る。銘柄を足したい時は
    `scripts/load_universe.py` の `JP_TICKERS/US_TICKERS` を編集して再実行。

## フェーズ1：動作確認（紙・無料〜低コスト）
- [ ] **規律の期待値を粗く見る（無料・先読みなしバックテスト）**
  `.venv/bin/python scripts/run_backtest.py`
  → 命中率・平均R・平均リターン（※生存者バイアスあり・暫定。将来は保証しない）
- [ ] **朝バッチを1回手動で回す（pipelineの実走）**
  - 低コスト：`.venv/bin/python scripts/run_morning_batch.py --no-quality`（決定論のみ）
  - 本番相当：`.venv/bin/python scripts/run_morning_batch.py`（弾ON＝信用性/EDINET。LLM/ネット課金・**日次¥500枠**を意識）
  → 当日の「決裁待ち decision（碇の構え）」が出る
- [ ] （任意）UI確認：`.venv/bin/python scripts/build_snapshot.py` → `ui/` を起動して画面で確認

## フェーズ2：毎朝の自動化（launchd）
- [ ] `docs/OPERATIONS_SCHEDULING.md` の手順で登録（朝バッチ7:00／評価7:30）
  ```sh
  mkdir -p data/logs
  cp ops/launchd/com.tradesupport.*.plist ~/Library/LaunchAgents/
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradesupport.morning-batch.plist
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradesupport.evaluation.plist
  ```
- [ ] 数日 `data/logs/*.log` を見て正常稼働を確認（重ければ `--no-quality` に変更）

## フェーズ3：ペーパー運用（実弾の前・最も信頼できる検証）
- [ ] 毎朝、朝バッチが出した候補を見て、moomoo 画面で「買うつもりの銘柄」を判断（決裁＝手動）
- [ ] **【私に依頼】発注の記録方法を決める**：買った entry 価格をどう残すか（¥ かネイティブか）。
  決めてくれれば `record_entry` を発注フローに配線する（P6評価が実データで回るようになる）
- [ ] 評価期日まで保有想定 → `scripts/run_evaluation.py` で hit/miss・R・Track Record が貯まる
- [ ] **30 decision・強気/弱気の両局面**を通すまで Track Record は「暫定」。焦らない

## フェーズ4：実弾（増額ゲートを満たしてから）
- [ ] moomoo 同意②／OpenD 起動で**実保有を取得**（今は StandIn ¥100k・保有0）
- [ ] **増額ゲート（これを満たすまで増額しない）**：
  ①評価完了 decision ≥30 ②強気・弱気の両局面を通過 ③期間中の最大DDが −15% 以内 ④平均R > 0
- [ ] 実弾は moomoo 画面で**手動発注**。1銘柄¥20k上限・現金20%は常時残す・損切り10–15%を必ず置く

---

## あなたが「決める」とき私が実装すること
- 発注フックの通貨基準（¥ vs ネイティブ）→ `record_entry` 配線
- Track Record・反証(counter) の **UI描画**（UI変更＝要承認 D-19）
- Ollama を入れるか（MELCHIOR の LLM 解釈・任意。CASPER=Sonnet は稼働中）
- A/B ロジック育成（データが貯まってから）

## いつでも止める安全装置（既に効いている）
- 1トレード2%（¥2,000）/ 1銘柄20%上限 / 現金20%下限 / 同時5銘柄 / 1セクター2銘柄・30% / DD−15%で新規停止
- 粉飾・倒産・GC注記の疑い → credibility warn → 決裁の既定が「保留」
