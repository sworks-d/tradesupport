# INVESTIGELION — システム目的と運用指針

> 本書は、ユーザーから繰り返し伝えられたシステム目的・運用指針・設計原則を整理したもの。
> 個別のアーキテクチャ仕様は [ARCHITECTURE.html](ARCHITECTURE.html) / [DEPLOYMENT.md](DEPLOYMENT.md) を参照。

---

## 1. システム最終目的

**INVESTIGELION は「少額から段階的に大規模化する自動売買システム」である**。

```
¥10 万円（検証）→ ¥100 万円（試運用）→ ¥1,000 万円〜（本運用）
       paper                  paper / live tier_1〜3            live tier_4〜5
       manual                 manual / auto                     auto
```

### 段階別ゴール

| 段階 | 金額 | broker_mode | automation_mode | 目的 |
|---|---|---|---|---|
| 検証 | ¥10 万 | paper | manual | ロジック動作確認・破綻チェック |
| 試運用 | ¥100 万 | paper / live tier_1 | manual | 実際の効果測定・運用感覚の獲得 |
| 段階移行 | ¥100-500 万 | live tier_1〜3 | manual / auto | 段階的にリスク解放しながら検証 |
| 本運用 | ¥1,000 万〜 | live tier_4-5 | auto | 完全自動運用 |

**核心**：少額時にも動作し、大額時にスケールする設計を一貫して選ぶ。一方しか満たさない実装は不採用。

---

## 2. 投資ロジックの方向性

### 「成長銘柄を追う」設計

- **対象**: 中小型成長株（時価総額 ¥100B〜¥1,000B 帯を中心）
- **回避**: 大型偏向（時価総額 ¥1兆超銘柄ばかり選ばれる状態）
- **戦略**: ZEELE 7 プリセット（v_shape / theme / growth-value / momentum / growth / contrarian / pullback / value / alpha）で多様性を確保

### 過去の不具合

- v2.10 以前は sort 二次キーが `market_cap` 降順で、composite_score 同点時に大型優先 → 推奨 10 件の 70% が ¥1兆超大型に偏向。修正済（小型優先に反転、2026-05-31）。

---

## 3. 運用指針（ユーザーから繰り返し表明された方針）

### 3.1 実運用で問題ない仕様

- **致命的問題を残したまま「完了」報告しない**
- 「コード対象外」「仕様」「データ問題」というラベルで都合よく除外しない
- データ問題でも migration スクリプトで解決可能なら対処する

### 3.2 マニュアル決済 vs 自動売買のスイッチング

- **automation_mode (manual / auto) は broker_mode (paper / live) と直交する独立軸**
- manual モードでは「buy 候補の判定を人間が承認」する設計
- ただし **sell（trailing/stop loss）は両モードで自動執行**（manual で sell が永久滞留すると損失拡大）
- 「manual モードで余計な処理を ON にすると、無駄に稼働した部分がノイズになる」（ユーザー）

### 3.3 段階的実弾移行（v2.10 Phase I-12）

| tier | 1 ポジション上限 | 用途 |
|---|---|---|
| tier_1 | ¥10,000 | 動作確認・手数料負け前提 |
| tier_2 | ¥50,000 | 1 銘柄を実弾で運用 |
| tier_3 | ¥200,000 | 数銘柄に展開 |
| tier_4 | ¥1,000,000 | 通常運用に近い規模 |
| tier_5 (full) | 無制限 | 通常の lot_pct ロジック |

昇格は人間判断（連続日数・勝率・DD で評価予定）。

### 3.4 安全装置（v2.10 Phase I）

- **I-10 異常検知 + HALT**: 累計 DD ≤-15% / 日次 DD ≤-7% / 約定失敗 3 営業日連続で `~/.trading-agent/HALT` 発火、auto 系停止
- **I-11 Discord 通知**: HALT 発火 / DD ブレーキを即時通知
- **I-12 段階的実弾移行**: 上記
- **HALT 自動解除なし**: 人間判断のみ（`scripts/clear_halt.py` 経由）

### 3.5 ハルシネーション対策（11 防壁）

- データが取れない銘柄は判断しない（推測しない）
- 上場廃止銘柄は Universe.is_active=False で買い候補から除外
- 仮想 ticker / EDINET 不在 / 信用性 warn 等で MAGI が弾く
- 「予算枠にぎりぎり収まる候補」だけ通す経済的防壁

### 3.6 観測可能性

- 閾値・連続週数を spec のまま実装する前に **上流データで発火するか必ず確認**
- ノード success だけで「動いてる」と判断しない
- 閾値発火率・銘柄分布・スコア構成・テーブル整合性まで網羅的に検証

---

## 3.7 broker_provider 設計（v2.10）

**broker_mode × broker_provider × automation_mode の 3 軸直交設計**：

| 軸 | 値 | 意味 |
|---|---|---|
| `broker_mode` | `paper` / `live` | DB のみ記録 / 実弾 |
| `broker_provider` | `moomoo` / `rakuten` / `sbi` / `monex` / `kabucom` / `fractional` | 接続先 broker |
| `automation_mode` | `manual` / `auto` | 人間決済 / 機械自動 |

**実装**: `trading_agent/brokers/` 配下に各 provider のクラスを置き、`__init__.py` の dispatcher が `get_broker_provider()` で振り分け。

**将来の自動売買接続**: 新規 broker の追加は対応クラスを実装して dispatcher 分岐を 1 ブロック追加するだけ。既存パイプラインに副作用なし。

## 4. システム構成（命名と役割）

エヴァンゲリオン命名で各層が役割を持つ：

```
WILLE    : ユーザーの投資哲学・運用方針の格納
KATSURAGI: Treasury 管理・予算配分・安全装置（HALT / 上限クランプ）
AKAGI    : 候補生成（screening / ZEELE / market_analyst）
MAGI     : 3 審判（MELCHIOR / BALTHASAR / CASPER）＋ Verification + 碇の構え
ZEELE    : 戦略プール（7 preset で銘柄を分類保持）
DS 4 機   : ペーパー並行検証（REI / ASUKA / KAWORU / SHINJI）
MISATO   : DS の司令塔・予算配分・銘柄→パイロット割当・昇格判定
```

### 主要パイプライン（朝バッチ 18 ノード）

```
pre_check → anomaly_check → topics_collector / universe_refresh
  → screening → zeele_curator / market_analyst → sell_recommender
  → portfolio_builder → trailing_check → close_due → pyramid_check
  → auto_fill → materialize_decisions → magi_verify → link_topics
  → summary → notify
```

### モード分岐の動作（v2.10）

| ノード | manual モード | auto モード |
|---|---|---|
| anomaly_check | 検知のみ warning | 検知で HALT 発火 |
| trailing_check / pyramid_check | 両モードで動作（Decision 登録のみ） | 両モードで動作 |
| close_due | **両モードで自動執行**（致命候補 3 修正） | 両モードで自動執行 |
| auto_fill | skip（misato_dispatch.py で人間執行） | 自動 fill |

---

## 5. 開発・運用の標準ワークフロー

### コード変更時

1. 既存ロジックを尊重（理由なく書き換えない）
2. 変更の意図と影響範囲を明確にしてから編集
3. 複数ファイルにまたがる変更は全体の影響を先に整理
4. 推測で実装しない（仕様が曖昧なら立ち止まる）
5. 完了報告前に **致命度を厳密に再評価**

### バッチ運用

- 毎朝 07:00 JST に launchd `morning-batch.plist` が自動実行
- 結果は `data/logs/morning-batch.out.log` / `.err.log`
- HALT 発火時は `scripts/clear_halt.py` で人間判断による解除
- 完全リセットは `scripts/misato_dispatch.py --cleanup-fresh`

### ペーパー検証ループ

1. cleanup_for_fresh_run でリセット
2. Treasury に検証額を入金
3. 朝バッチ実行（quality ON）
4. 銘柄分布・スコア分布・整合性を網羅的に検証
5. 効果測定 → 次の改善へ

---

## 6. 設計原則の要約

```
✓ 少額時にも動作し、大額時にスケールする
✓ 成長銘柄を追う（大型偏向しない）
✓ manual/auto モードの直交軸を維持
✓ 致命的問題を残したまま完了報告しない
✓ 既存ロジックを尊重、独自 SQL UPDATE 禁止
✓ 観測可能性を最優先（網羅検証）
✓ ハルシネーション防壁を保つ
✓ 段階的実弾移行で安全に本番稼働
```

---

## 関連ドキュメント

- [ARCHITECTURE.html](ARCHITECTURE.html) — 技術アーキテクチャ詳細
- [DEPLOYMENT.md](DEPLOYMENT.md) — デプロイ手順
- [HALT_RECOVERY.md](HALT_RECOVERY.md) — HALT 復旧手順
- [OPERATIONS_SCHEDULING.md](OPERATIONS_SCHEDULING.md) — 朝バッチ・cron 運用
- [USER_RUNBOOK.md](USER_RUNBOOK.md) — 日常運用ガイド
