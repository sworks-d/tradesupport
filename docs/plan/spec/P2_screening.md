# P2 一次選抜（スクリーニング＋信用性）— フェーズ設計＋タスク設計

**フェーズの役割**：全市場→候補へ絞り（定量・LLM非関与）、発覚済みの危険（粉飾・上場廃止）を入口で除く。
**全体ゴール寄与**：MAGIに回す候補の質を上げ、実弾を投じる相手の信頼性を担保する。
**前からの引き継ぎ**：P1収集データ＋universe（母集団）。**次への引き渡し**：合格候補リスト＋信用性フラグ。
**フェーズのハルシネ防止方針**：R1（スコアはコード計算・LLM不使用）／R7（universe外の銘柄を持ち込まない）。
**注意**：スクリーニングの composite スコアは内部の一次選抜用。**UIに総合点として出さない**（不変原則3／D-06）。

> 状態：✅実装済 / 🟡部分 / ❌未 / 🔵要判断

### P2-1 universe 投入  〔✅ 自動定義(JP主体)・A-1〕
- 全体ゴール：候補生成の母集団を用意（NVDA決め打ちからの脱却）。
- 前からの引き継ぎ：銘柄リスト（🔵要判断：手元リスト or 自動定義＝¥1M端株前提でUS主要＋安価JP）。
- 目的：universe テーブルに銘柄(ticker/market/name/sector)を投入。
- 実装：新規 `scripts/load_universe.py`＋`models/universe.py`。
- 次への引き渡し：universe レコード群（→P2-2の入力）。
- ハルシネ防止：R7 出所の明確な銘柄のみ登録（勝手に銘柄を発明しない）。
- 受入：universe に N 件入り、`screening_agent` が母集団を読める。

### P2-2 定量スクリーニング  〔🟡 ロジック有・未起動〕
- 全体ゴール：V字回復・テーマ適合で候補を絞る。
- 前からの引き継ぎ：universe（P2-1）＋P1収集（market_data/technicals/fundamentals）。
- 目的：V字4軸＋テーマ4軸→composite→ランキング→上位を合格候補に。
- 実装：`agents/screening_agent.py` 実行＋`mcp_tools/screening.py`。`orchestrator/morning_batch.py` の universe_refresh 有効化。
- 次への引き渡し：`ScreeningResult`（v_shape/theme/composite/passed）＋上位候補ticker（→P2-3, P3）。
- ハルシネ防止：R1 スコアは pandas/コード計算（LLM不使用）／R2 入力データの出典/時点を維持。
- 受入：ScreeningResult が保存され、合格候補リストが得られる。

### P2-3 信用性ハードフィルタ（D-14）  〔✅ M/F/Z＋開示レッドフラグ＋XBRL GC/監査〕
- 全体ゴール：発覚済みの危険銘柄を候補に入れる前に物理除外（種類A）。未発覚（種類B）は警戒フラグ。
- 前からの引き継ぎ：候補ticker＋P1-5一次情報/P1-7開示（監査意見・GC注記・上場廃止・特設注意）。
- 目的：第1フィルタ＝ハード除外（D-14：上場廃止/特設注意を先行、監査意見/GC注記は段階）。第2＝ソフト警戒フラグ。
- 実装：新規 `trading_agent/screening/credibility.py`。EDINET由来データで判定。`screening` 前段に挿入。
- 次への引き渡し：候補から危険銘柄を除外したリスト＋`credibility_flag(ok/warn)`（→P3-4防御層, U表示）。
- ハルシネ防止：R1 判定は取得データの機械判定／R4 データ欠損は「判定不能」（除外も合格もしない＝保留）。
- 受入：故意に不適正な銘柄が除外され、警戒銘柄に warn が付く。
