# 引き渡し指示文（このノート PC → 常駐機 / 受け取り側へ）

作成日：2026-05-26
発行：sworks-d（このノート PC・開発機）
受領：常駐機オペレータ（あなた本人 or 次の Claude Code セッション）
**読む順**：本書 → `docs/DEPLOYMENT.md` → `docs/USER_CHECKLIST.md`

---

## 0. ひとことで

**tradesupport はペーパー稼働可能な状態です**。コードは `feat/magi-rebuild` ブランチ・最新コミット `b379772`。
朝5分のダッシュボードと資金配分・規律監督・ZEELE 攻めレコメンドが揃った状態で、常駐機に持っていって `build_snapshot.py` を朝バッチで回せば運用が始まります。

**実機検証済（2026-05-26）**：`scripts/run_morning_batch.py --no-quality` を実行し、
universe → screening → MAGI 4段判定 → decisions 10件 materialize まで **end-to-end で動作確認済**。
（topics_collector のみ timeout だが graceful degradation で本流は完走）

ただし以下 3 つは**未完です**：

1. **決裁ボタンから decisions テーブルへの結線（B6）** — schema 再構成方針が要ユーザー判断。**当面は UI を見て手動で moomoo 発注、それで成立する**設計
2. **watchlist 昇格の DB 永続化** — 現状 localStorage（次セッションで thesis_store / DB 化）
3. **screening → ZEELE 実候補の流し込み（X-2A ingest）** — 現状プレースホルダ 4 件

これらは"ペーパー稼働してデータを溜める"ためには**ブロッカーではない**。優先順は本書 §5。

---

## 1. 引き渡し時点で**確定済**の判断

これらは触らない（変更したい時は DECISIONS.md を更新してから）：

| ID | 内容 |
|---|---|
| **D-23** | 規律外骨格 8 数値（risk 2% / 5 銘柄 / 30% 上限 / 現金 20% / DD−15% で停止 / 増額ゲート / 損切 12% / JP 主体） |
| **D-24** | 設計北極星 = [claude-trading-skills](https://github.com/tradermonty/claude-trading-skills)（MIT） |
| **D-25** | 市場対象 = JP 90% / US ETF 10%（個別米株は禁止） |
| **D-26** | 資金配分 = Core 60 / Satellite 20 / Cash 20 ＋ 月次 70/10/20（posture で上書き） |

8 数値の正本 = [trading_agent/risk/params.py](../trading_agent/risk/params.py)。
規律監督ロジック = [trading_agent/discipline/](../trading_agent/discipline/)。

---

## 2. 引き渡し時点で**保留**の判断（受け取り側が決める）

| 論点 | 影響 | 判断材料 |
|---|---|---|
| **B6 decisions schema 再構成** | UI 決裁ボタン → DB の結線 | spec 確定して migration を打つ／当面は手動運用で良いなら不要 |
| **増額ゲート達成** | 実弾化 / 入金 | 30 decision 評価完了・両局面・DD-15% 以内・平均R>0 を満たすまで `TRADING_MODE=paper` |
| **B6 後の C2 / C3** | E2E テスト / paper run | B6 確定後に着手 |

---

## 3. 受け取り側がまず何をするか（順序通り）

### Step 1：環境構築（30 分〜1 時間）

[DEPLOYMENT.md §1](DEPLOYMENT.md) の通り：

- [ ] 常駐機を確定（Mac mini など・スリープ無効化）
- [ ] `git clone` 又は rsync で配置（パス例：`/Users/<user>/tradesupport`）
- [ ] `uv sync`
- [ ] `.env` 設定（[USER_CHECKLIST.md](USER_CHECKLIST.md) §A の API キー）
- [ ] `uv run python scripts/init_db.py`
- [ ] `uv run python scripts/load_universe.py`（JP30 + US ETF2 が入る）
- [ ] moomoo OpenD インストール＋ペーパー口座設定＋常駐起動

### Step 2：動作確認（10 分）

```bash
# A) snapshot を生成（最重要：これが動けば UI に出る）
uv run python scripts/build_snapshot.py --demo     # ← まずこれ
uv run python scripts/build_snapshot.py            # ← yfinance 経由のライブ価格

# B) UI を立ち上げ
cd ui && npm install && npm run dev
# → http://localhost:3100 を開いて、ZEELE 攻め / MAGI 売り / 買い / 保有 / 資金配分 を確認
```

期待される画面：
- 左カラム：**ZEELE 攻め**（紫破線・候補4件・推移グラフ・推奨サイジング）
- 中央：**売り MAGI 守り**
- 右：**買い MAGI 守り**（NVDA に推奨サイジング行）
- その下：**資金配分**（teal/紫/灰の3層スタック棒・posture チップ・月次配分提案）
- さらに下：**保有**（規律 OK/WARN/REVIEW バッジ・売り注視連動バッジ）
- 末尾：**Topics**（役割タグ：MAGI 守り材料 / ZEELE 攻め材料 / 中立）

### Step 3：launchd 登録（5 分・常駐機側のみ）

[DEPLOYMENT.md §3](DEPLOYMENT.md) の通り：

```bash
# plist 内の絶対パスを常駐機の実パスに書き換えてから
cp ops/launchd/com.tradesupport.*.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradesupport.morning-batch.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradesupport.evaluation.plist
launchctl list | grep tradesupport
```

⚠ **このノート PC で動かしている開発側の launchd は登録しないこと**。一度誤登録したことがあるので注意。

### Step 4：初回ペーパー運用（朝 5 分の流れ・[DEPLOYMENT.md §4](DEPLOYMENT.md) 参照）

1. 朝バッチが 07:00 JST に走る → `data/logs/morning-batch.out.log` で確認
2. ブラウザで UI を開く
3. **資金配分パネル**で総資産と posture を確認
4. **ZEELE 攻め**を流し読み（数週単位の熟成候補・気になれば watchlist 昇格）
5. **MAGI 売り/買い**を精読 → 詳細パネル → 承認/否認/保留
6. **moomoo アプリで手動発注**（ペーパー口座）
7. 約定は自動同期（moomoo OpenD 経由）
8. 月次に追加投入したら `+ 追加投入を記録` ボタンで記録

---

## 4. 触らない / 注意

| 項目 | 理由 |
|---|---|
| `new_dashboard.html`（モック）| F0 ゴールデンマスター。視覚回帰の基準 |
| D-19 の "UI 変更ゲート"（スコア痕跡撤去）| ユーザー再確認後に実施。**まだ承認していない** |
| Ollama インストール（このノート） | 常駐機側で入れる。開発機では不要 |
| `~/Library/LaunchAgents/com.tradesupport.*.plist`（このノート） | 開発機には登録しない |
| `data/trading.sqlite` の手動編集 | スキーマ整合性が壊れる。Alembic か uv 経由で |
| 個別株 US 銘柄追加 | D-25 で禁止（増額ゲート達成後の段階拡大対象） |

---

## 5. 次セッションの優先順（claude code に投げる時の頼み方）

```
最優先：
1. B6 DAG挿入＆状態遷移
   - decisions schema 再構成案を 3案出して
   - 既存 decisions 表を壊さないマイグレーション設計
   - 決裁ボタン → decisions.status 更新の結線

中：
2. watchlist 昇格の DB 永続化
   - localStorage → thesis_store.create_thesis の bridge
   - FastAPI エンドポイント POST /api/zeele/promote

3. screening_agent → ZEELE ingest アダプタ（X-2A 後段）
   - discipline/thesis_ingest.py を新規
   - "数週連続上位入賞" の判定ロジック

低：
4. CANDIDATES マップ拡張（NVDA 以外）
5. F5 ZEELE 探索画面の中身配線
6. MAGI 売りカードの売却推奨株数表示
```

---

## 6. 引き渡し前の最終状態スナップショット

```
ブランチ      : feat/magi-rebuild
最新コミット  : 083ef06 (docs(deployment): 別 PC...)
全テスト      : 471 passed
UI build      : ✓
未コミット差分: なし（このノート上の作業はクリーン）

主要ドキュメント:
  docs/plan/DECISIONS.md          (D-01〜D-26 確定)
  docs/plan/spec/X1_reuse_inventory.md  (外部OSS借用棚卸し)
  docs/plan/spec/X2_claude_trading_skills_adoption.md  (北極星翻案スペック)
  docs/DEPLOYMENT.md              (本番機セットアップ手順)
  docs/USER_CHECKLIST.md          (ユーザー作業のみ一覧)
  docs/OPERATING_COSTS.md         (運用コスト整理)
  docs/HANDOFF.md                 (本書)

主要 UI:
  ui/app/page.tsx                 (entry)
  ui/app/dashboard.html           (golden master 準拠・3カラム化済)
  ui/app/CashFlowPanel.tsx        (資金配分 · D-26)
  ui/app/ZeelePanel.tsx           (攻めレコメンド)
  ui/app/LiveData.tsx             (DOM 結線)
  ui/app/zeele/page.tsx           (F5 探索画面 skeleton)
```

---

## 7. 緊急停止

何かおかしいと思ったら：

```bash
# A) 即停止（LLM 呼び出し全停止）
touch ~/.trading-agent/HALT

# B) launchd ジョブ停止
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.tradesupport.morning-batch.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.tradesupport.evaluation.plist

# C) UI を止める
# Ctrl+C で npm run dev を止める

# D) moomoo 接続を止めるなら OpenD を終了
```

ペーパー運用なので**お金は動かない**（`TRADING_MODE=paper`）。落ち着いて停止して、ログを見てから再開。

---

## 8. 連絡先 / 参考

- GitHub：`sworks-d/tradesupport`（プライベート）
- 北極星：[tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills)（MIT）
- JP 補完：[edinetdb/dexter-jp](https://github.com/edinetdb/dexter-jp)
- 設計議論ログ：`docs/plan/DECISIONS.md` D-01 〜 D-26

---

**最後に**：このプロジェクトの本質は memory に書いてある通り：

> 稼ぎ = 所有 × 時間 × 複利。売買で勝つ予測機ではなく Core-Satellite 規律監督ツール。

朝5分で持ち続ける規律を維持する。それが全部です。
