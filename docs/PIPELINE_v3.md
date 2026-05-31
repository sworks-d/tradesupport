# PIPELINE v3 — 朝バッチ統合設計

**作成日**: 2026-06-01
**Phase**: 0 設計合意（実装前）
**目的**: WILLE ゾーン全体（KATSURAGI / AKAGI / MISATO / MAGI / ZEELE / DS 4 機）を朝バッチに統合する設計図。新規実装はゼロ、既存資産の DAG 結線変更のみで実現する。

---

## 0. 背景：実装と設計意図の乖離

### v2.10 現在の朝バッチ（旧構造）

```
pre_check → anomaly_check → topics_collector / universe_refresh
  → screening → zeele_curator / market_analyst → sell_recommender
  → portfolio_builder(review) → trailing_check → close_due → pyramid_check
  → auto_fill → materialize_decisions → magi_verify → link_topics
  → summary → notify
```

問題点（実装精査で確認・引用付き）：

| 問題 | 場所 | 内容 |
|---|---|---|
| **WILLE 経路を通っていない** | [morning_batch.py:666-735](../trading_agent/orchestrator/morning_batch.py#L666) | `portfolio/misato.py:dispatch()` を呼んでいない |
| **AKAGI Brief が使われない** | 同上 | [wille/ritsuko.py](../trading_agent/wille/ritsuko.py) が朝バッチで参照されない |
| **KATSURAGI 戦略パラメータが効かない** | 同上 | [wille/misato.py](../trading_agent/wille/misato.py) `MisatoStrategy` が朝バッチで使われない |
| **DS 4 機が本番銘柄選定に関与しない** | 同上 | [portfolio/ds_scout.py](../trading_agent/portfolio/ds_scout.py) が朝バッチに繋がっていない |
| **MAGI verify が事後検証になっている** | [morning_batch.py:719](../trading_agent/orchestrator/morning_batch.py#L719) | `materialize_decisions` の **後** に `magi_verify` がある |

ただし WILLE 経路自体は別系統で稼働している：

| 系統 | WILLE 経路を通る | 状態 |
|---|---|---|
| `scripts/misato_dispatch.py`（手動 CLI） | ✅ | DS の paper 検証発注 |
| `scripts/build_snapshot.py`（UI 用） | ✅ | UI ダッシュボード snapshot 生成 |
| `ds-report-am.plist` / `ds-report-pm.plist` | ✅（推定） | DS 朝夕レポート |
| `morning-batch.plist`（朝バッチ） | ❌ | **乖離の核心** |

---

## 1. 新朝バッチ設計図（v3）

### 全体図

```
07:00 朝バッチ start
  │
  ├─ pre_check                     [既存] 異常前処理
  ├─ anomaly_check                 [既存] H-7（DD ≤-15% で HALT）
  ├─ universe_refresh              [既存] is_active 更新（H1 前段）
  │
  ┌─── 【入力層】候補発見 ───┐
  ├─ topics_collector              [既存] H2（Haiku ハルシネーション禁止指示）
  ├─ screening (ScreeningAgent)    [既存] Universe 最終照合（H1）
  │      composite_score（決定論）
  │      ├─ credibility            [既存] S5
  │      ├─ V字 turnaround         [既存] S7a（コード判定）
  │      ├─ relative_strength      [既存] S7c
  │      └─ financials / EDINET    [既存] S4
  │      → ScreeningResult
  └────────────────────────────────────┘
  │
  ┌─── 【ZEELE 車線】長期観察 ───┐
  ├─ zeele_curator                 [既存] 3 週連続入賞 → ZeeleState entry
  │      ★ Phase 4 で LLM 化予定（V字・テーマ・攻め探索）
  │      ★ 現状は決定論ベース
  │      → ZeeleState（preset / reference_score）
  └────────────────────────────────────┘
  │
  ┌─── 【MAGI 車線】3 賢者 ★ 位置変更 ───┐
  ├─ materialize_decisions ★ 前に移動
  │      [既存] candidates → Decision(verifying)
  │
  ├─ magi_verify ★ 前に移動
  │      ├─ MELCHIOR (業績)        S5 反証
  │      ├─ BALTHASAR (株価)       投票外・反証専用
  │      ├─ CASPER (文脈)          H2 値域検証
  │      ├─ 防御層                 機械照合（決裁前ゲート）
  │      ├─ 統合機構               SplitResult
  │      └─ 碇司令                 推奨 + 反対論拠（決定しない）
  │      → Decision(awaiting) + gendo_stance
  └─────────────────────────────────────┘
  │
  ┌─── 【KATSURAGI 統合層】 ★ 新ノード ───┐
  ├─ katsuragi_dispatch ← portfolio/misato.py:dispatch() のラッパ
  │      │
  │      ├─ HALT check              G-0 安全装置
  │      ├─ Treasury → 予算          G-7 逓減
  │      │
  │      │ ★ 候補プール構築
  │      │   - MAGI awaiting (stance: 推し/要検討/静観)
  │      │   - ZEELE active (preset / reference_score)
  │      │   - 重複: source="both" でウェイト 1.5x
  │      │   - ZEELE 上位 5 件厳選 (ZEELE_TOP_N_FOR_MISATO)
  │      │
  │      ├─ AKAGI Brief             H1（データ不足 None 返し）
  │      │   wille/ritsuko: TickerBrief 5 中立スコア
  │      │   news_sentiment / industry / peer / event / deep_brief
  │      │   + 市場 regime 判定
  │      │
  │      ├─ 戦略 preset 選択
  │      │   wille/misato.MisatoStrategy.from_preset
  │      │   balanced / news_focused / trend_focused / peer_focused
  │      │
  │      ├─ 市場ガード              日経/TOPIX -3% で停止
  │      │
  │      ├─ DS Scout 自発 proposal
  │      │   REI / ASUKA / SHINJI: select_from_pool
  │      │   KAWORU: select_kaworu_contrarian
  │      │
  │      ├─ priority 計算
  │      │   wille/misato.compute_priority
  │      │   priority = base_confidence + boost × 0.5
  │      │   同銘柄複数機なら priority 最高のみ（排他制御）
  │      │
  │      ├─ 例外チェック            G-2（earnings / ピア劣位）
  │      │   wille/misato.check_proposal_exceptions
  │      │
  │      ├─ opportunity_driven_fill 4 ガードレール greedy
  │      │   1 銘柄 / source / sector / 機 上限
  │      │
  │      └─ Assignment 構築         ZEELE preset×性格マッピング
  │           value/dividend → REI
  │           momentum/growth → ASUKA
  │           pullback/contrarian → SHINJI
  │           alpha/growth-value → KAWORU
  │      → DispatchPlan
  └────────────────────────────────────────┘
  │
  ┌─── 【保有運用層】既存 ───┐
  ├─ sell_recommender              [既存] S6（earnings ガード）
  ├─ trailing_check                [既存] G-2 合成
  ├─ close_due                     [既存] 両モード自動執行
  ├─ pyramid_check                 [既存] G-1 R-mult
  └─────────────────────────────────┘
  │
  ┌─── 【出力層】 ───┐
  ├─ persist_decisions ★ 新ノード
  │      DispatchPlan → Decision 状態更新
  │      （旧 portfolio_builder review は廃止 / initial は別用途で残置）
  │
  ├─ auto_fill                     [既存]
  │      paper: 自動シミュ          G-3 Universe 照合（最終防壁）
  │      live + manual: skip（ユーザー代行発注待ち）
  │      live + auto: 自動発注（kabu API 接続後）
  │
  ├─ link_topics / summary         [既存]
  └─ notify → 発注リスト HTML       G-4 R-mult / stop / 1R 損失併記
                                   autoreport/orders/YYYY-MM-DD.html
  └────────────────────┘
```

---

## 2. DAG ノード対応表（旧 → 新）

| 旧朝バッチノード | 状態 | 新朝バッチノード | 備考 |
|---|---|---|---|
| pre_check | ✅ 残す | pre_check | 変更なし |
| anomaly_check | ✅ 残す | anomaly_check | 変更なし |
| universe_refresh | ✅ 残す | universe_refresh | 変更なし |
| topics_collector | ✅ 残す | topics_collector | 変更なし |
| screening | ✅ 残す | screening | 変更なし |
| zeele_curator | ✅ 残す | zeele_curator | Phase 4 で LLM 化予定 |
| market_analyst | ⚠ 役割整理 | (削除 or AKAGI 統合) | C3 news_sentiment は wille/ritsuko 経由に移植 |
| sell_recommender | ✅ 残す | sell_recommender | 変更なし |
| portfolio_builder (review) | ❌ 削除 | (削除) | dispatch の Assignment 構築 + 例外チェックで代替 |
| portfolio_builder (initial) | ✅ 残置（不使用） | (将来用) | コード保持・朝バッチでは呼ばない |
| trailing_check | ✅ 残す | trailing_check | 変更なし |
| close_due | ✅ 残す | close_due | 変更なし |
| pyramid_check | ✅ 残す | pyramid_check | 変更なし |
| auto_fill | ✅ 残す | auto_fill | 変更なし |
| materialize_decisions | 🔄 位置変更 | materialize_decisions | **MAGI 車線の前** に移動 |
| magi_verify | 🔄 位置変更 | magi_verify | **materialize 直後** に移動（KATSURAGI dispatch の前） |
| - | ★ 新規 | **katsuragi_dispatch** | dispatch() の朝バッチラッパ |
| - | ★ 新規 | **persist_decisions** | DispatchPlan → Decision 状態更新 |
| link_topics | ✅ 残す | link_topics | 変更なし |
| summary | ✅ 残す | summary | 変更なし |
| notify | ✅ 残す | notify | 変更なし |

### 新 DAG 構造（依存関係）

```
pre_check
  ├→ anomaly_check
  ├→ universe_refresh
  └→ topics_collector
       └→ screening
            ├→ zeele_curator
            └→ materialize_decisions ★ 位置変更
                 └→ magi_verify ★ 位置変更
                      └→ katsuragi_dispatch ★ 新規
                           └→ sell_recommender
                                └→ trailing_check
                                     └→ close_due
                                          └→ pyramid_check
                                               └→ auto_fill
                                                    └→ persist_decisions ★ 新規
                                                         └→ link_topics
                                                              └→ summary
                                                                   └→ notify
```

---

## 3. 新ノード I/O 仕様（既存 dispatch シグネチャから）

### katsuragi_dispatch ノード

```python
async def run_katsuragi_dispatch() -> dict:
    """KATSURAGI 統合：dispatch() の朝バッチラッパ。"""
    from trading_agent.portfolio.misato import dispatch
    from trading_agent.utils.lot_size import get_broker_mode

    broker_mode = get_broker_mode()
    plan = dispatch(
        engine=engine,
        total_budget_jpy=None,  # treasury から自動取得
        approve=False,           # 朝バッチでは dry-run（auto_fill ノードで別途処理）
        only_personality=None,
        # price_lookup / is_jp_lookup は paper モードでは不要
    )
    return {
        "halted": plan.halted,
        "total_budget_jpy": plan.total_budget_jpy,
        "n_assignments": sum(len(a.proposals) for a in plan.assignments),
    }
```

### persist_decisions ノード

```python
async def run_persist_decisions() -> dict:
    """DispatchPlan の Assignment を Decision として確定。"""
    # dispatch 内で既に Decision(awaiting) 状態にあるものを確認
    # 必要なら追加メタデータ（priority / pilot / preset）を Decision に書き込む
    # （詳細は Phase 1 M1.2 で確定）
    pass
```

### 入出力スキーマ

| ノード | 入力 | 出力 |
|---|---|---|
| materialize_decisions | screening 上位候補（composite_score 降順） | Decision(status="verifying") |
| magi_verify | Decision(verifying) | Decision(awaiting) + gendo_stance |
| katsuragi_dispatch | Decision(awaiting) + ZeeleState(active) | DispatchPlan（Assignment / fills） |
| persist_decisions | DispatchPlan | Decision 状態確定 + メタデータ |

---

## 4. 役割と責務（v3 確定版）

| 役 | 実装ファイル | 責務 |
|---|---|---|
| **WILLE** | [wille/__init__.py](../trading_agent/wille/__init__.py) + `data/wille_settings.json` | 投資哲学・運用方針の格納、WILLE 包括組織の入口 |
| **KATSURAGI** | [portfolio/misato.py:dispatch()](../trading_agent/portfolio/misato.py) | WILLE オーケストレーター + 方針決定者。HALT → 候補プール → AKAGI Brief → 戦略 → DS scout → priority → opportunity fill → 実 fill 統合 |
| **AKAGI** | [wille/ritsuko.py](../trading_agent/wille/ritsuko.py) | 確度検証・調査分析。TickerBrief 5 中立スコア（news_sentiment / industry / peer / event / deep_brief）+ 市場 regime |
| **MAGI** | [magi/](../trading_agent/magi/) | 3 賢者（MELCHIOR/BALTHASAR/CASPER）+ 防御 + 統合 + 碇司令。stance 付与 |
| **MISATO（戦略）** | [wille/misato.py](../trading_agent/wille/misato.py) | 戦略パラメータ（MisatoStrategy）+ 優先度計算 + boost 集約 |
| **MISATO（DS 司令）** | [portfolio/misato.py](../trading_agent/portfolio/misato.py) | DS 4 機予算配分 + 銘柄→パイロット割当 + 昇格判定 |
| **DS 4 機** | [portfolio/personality.py](../trading_agent/portfolio/personality.py) + [portfolio/ds_scout.py](../trading_agent/portfolio/ds_scout.py) | REI / ASUKA / KAWORU / SHINJI 人格 + 自発 proposal |
| **ZEELE** | [agents/zeele_curator.py](../trading_agent/agents/zeele_curator.py) + ZeeleState | 戦略プール（7 preset）。現状決定論、Phase 4 で LLM 化 |

---

## 5. 各車線の役割分担

| 車線 | 時間軸 | 入力 | 出力 | 役割 |
|---|---|---|---|---|
| **入力層** | 当日 | universe + topics | ScreeningResult | 中小型成長銘柄絞り込み |
| **ZEELE 車線** | 長期（3 週連続入賞・20 回未登場で降格） | ScreeningResult | ZeeleState（preset 付き active） | **戦略プール**：preset × 性格マッピングで DS 選定に効く |
| **MAGI 車線** | 当日 | screening 上位 → verifying Decision | Decision(awaiting) + stance | **本日の確度判定**：3 賢者投票で stance を付ける |
| **KATSURAGI 統合層** | 当日 | MAGI awaiting ∪ ZEELE active | DispatchPlan | **最終発注決定**：AKAGI 確度検証 + DS 自発 proposal で最強の組み合わせ |
| **保有運用層** | 既存ポジション | Portfolio | trailing / close / pyramid 結果 | 売却・追加買付の自動執行 |
| **出力層** | 当日 | DispatchPlan | Decision + 発注リスト HTML | 永続化 + ユーザーへの提示 |

---

## 6. broker_mode × automation_mode 別の動作

| モード組み合わせ | katsuragi_dispatch | auto_fill | 発注実行者 |
|---|---|---|---|
| paper × manual | dry-run（DispatchPlan 生成のみ） | 自動シミュ fill | （シミュ） |
| paper × auto | dry-run | 自動シミュ fill | （シミュ） |
| live × manual | dry-run（DispatchPlan 生成） | skip | **ユーザー手動代行**（楽天証券アプリ） |
| live × auto（将来） | approve=True | 自動発注 | kabu API（Phase 2） |

---

## 7. C3 news_sentiment の統合先

Phase 3 M3.1 で実施：

- C3 で実装中の [llm/news_sentiment.py](../trading_agent/llm/news_sentiment.py)（[news_keywords.py](../trading_agent/wille/news_keywords.py) と整合）を
- [wille/ritsuko.py](../trading_agent/wille/ritsuko.py) の `news_sentiment_score` 算出に組み込む
- → AKAGI Brief の news スコアが固定値 50 → LLM 判定（0-100）に
- → KATSURAGI の boost 計算に乗る
- → DS scout の confidence に反映

---

## 8. Phase 4（後追い）：ZEELE LLM 化

朝バッチ統合完了後の別タスク：

| MS | 内容 |
|---|---|
| M4.1 | ZEELE 用 LLM 探索エージェント設計（V字 / テーマ / 攻め探索） |
| M4.2 | Haiku で銘柄候補発見 → zeele_curator に流入 |
| M4.3 | コスト試算 + 予算上限ガード |
| M4.4 | 既存 V字（turnaround.py）との整合 |

合計 ~12h、別タスクとして優先順位付け。

---

## 9. 実装工数

| Phase | 工数 |
|---|---|
| 0: 設計合意（本文書）+ HALLUCINATION_BARRIERS.md | ~3h |
| 1: 朝バッチノードラッパ実装 | ~4h |
| 2: 朝バッチ DAG 組換 | ~3h |
| 3: C3 統合 + 並走検証 | ~1h + 1 週間運用 |
| **合計（Phase 0-3）** | **~11h + 1 週間並走** |
| 4: ZEELE LLM 化（後追い） | ~12h |

---

## 10. 参照

- [docs/SYSTEM_PURPOSE.md](SYSTEM_PURPOSE.md) — システム最終目的
- [docs/HALLUCINATION_BARRIERS.md](HALLUCINATION_BARRIERS.md) — 11 防壁正本表
- [CLAUDE.md](../CLAUDE.md) — プロジェクト概要
- メモリ [[misato_orchestrator]] / [[katsuragi_role]] / [[ds_operation_intent]]
