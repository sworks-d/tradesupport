"""ピラミッディング戦略（v2.10 Phase 2）。

中期投資の「勝ちを大きく、負けを小さく」を実現する段階エントリー戦略。
含み益確認後に追加投入することで、損失は初期エントリー額に限定しつつ
勝ちトレードのリターンを拡大する。

機別段階設計（性格と整合）:
  REI    (長期低ボラ・stop -12%): 3 段階 40% → 70% → 100%（慎重・確認しながら積む）
  ASUKA  (中期攻め・stop -6%):    2 段階 50% → 100%（攻め・素早く満玉へ）
  SHINJI (中期中庸・stop -8%):    2 段階 50% → 100%（バランス）
  KAWORU (短期・stop -5%):        1 段階 100% 一括（短期回転で買い増ししない）

トリガー含み益:
  - 第 2 段階: +5%
  - 第 3 段階: +10%

ハルシネーション対策:
  - 価格データなしの場合は買い増し判定しない（None 返却・推測しない）
  - 含み益が境界ぎりぎり（0.5% の誤差マージン内）は据え置く（買い増ししない）
  - 機の性格が不明な場合は KAWORU として扱う（最も保守的）
"""

from __future__ import annotations

from dataclasses import dataclass

# トリガー判定のマージン（0.5%）：境界ぎりぎりは推測せず保留
_PYRAMID_MARGIN = 0.005


@dataclass(frozen=True)
class PyramidStage:
    """ピラミッドの 1 段階。"""

    trigger_pnl_pct: float       # この含み益率に達したら次の段階へ
    cumulative_alloc: float      # 累積投入比率（0.0-1.0）


# 機別のピラミッド段階設計
_PYRAMID_BY_PILOT: dict[str, list[PyramidStage]] = {
    "REI": [
        PyramidStage(trigger_pnl_pct=0.0, cumulative_alloc=0.40),
        PyramidStage(trigger_pnl_pct=0.05, cumulative_alloc=0.70),
        PyramidStage(trigger_pnl_pct=0.10, cumulative_alloc=1.00),
    ],
    "ASUKA": [
        PyramidStage(trigger_pnl_pct=0.0, cumulative_alloc=0.50),
        PyramidStage(trigger_pnl_pct=0.05, cumulative_alloc=1.00),
    ],
    "SHINJI": [
        PyramidStage(trigger_pnl_pct=0.0, cumulative_alloc=0.50),
        PyramidStage(trigger_pnl_pct=0.05, cumulative_alloc=1.00),
    ],
    "KAWORU": [
        # 短期回転は買い増ししない（高速エントリー・高速イグジット）
        PyramidStage(trigger_pnl_pct=0.0, cumulative_alloc=1.00),
    ],
}


def get_pyramid_stages(pilot: str) -> list[PyramidStage]:
    """機別のピラミッド設計を返す。不明な機は KAWORU として扱う（保守的）。"""
    return _PYRAMID_BY_PILOT.get(pilot, _PYRAMID_BY_PILOT["KAWORU"])


def get_initial_alloc(pilot: str) -> float:
    """機の「初期エントリー」比率を返す（0.0-1.0）。"""
    return get_pyramid_stages(pilot)[0].cumulative_alloc


def get_target_alloc(
    pilot: str, current_pnl_pct: float | None
) -> float | None:
    """現在の含み益から、目指すべき累積投入比率を返す。

    Args:
        pilot: 機の名前
        current_pnl_pct: 含み益率（0.05 = +5%）。価格データなしは None。

    Returns:
        累積投入比率（0.0-1.0）。価格データなしは None（推測しない）。
    """
    if current_pnl_pct is None:
        return None
    stages = get_pyramid_stages(pilot)
    target = stages[0].cumulative_alloc
    for stage in stages:
        if current_pnl_pct >= stage.trigger_pnl_pct:
            target = max(target, stage.cumulative_alloc)
    return target


def should_pyramid_up(
    pilot: str, current_alloc: float, current_pnl_pct: float | None
) -> tuple[bool, float]:
    """買い増しすべきかと、目指す累積比率を返す。

    Args:
        pilot: 機の名前
        current_alloc: 現在の累積投入比率（実保有比率 / 予定総量）
        current_pnl_pct: 含み益率

    Returns:
        (買い増しすべきか, 目指す累積比率)
        価格データなしは (False, current_alloc) で保留。
    """
    target = get_target_alloc(pilot, current_pnl_pct)
    if target is None:
        return False, current_alloc
    # 境界ぎりぎりは推測せず保留
    if target > current_alloc + _PYRAMID_MARGIN:
        return True, target
    return False, current_alloc


def get_stage_label(pilot: str, current_pnl_pct: float | None) -> str:
    """UI 表示用の段階ラベル（例: "1/3 (初期)", "2/3 (+5%)"）。"""
    if current_pnl_pct is None:
        return "data n/a"
    stages = get_pyramid_stages(pilot)
    total = len(stages)
    # 何段階目を超えたかカウント
    reached = sum(1 for s in stages if current_pnl_pct >= s.trigger_pnl_pct)
    if reached == 0:
        return f"未約定 (0/{total})"
    if reached == total and total > 1:
        return f"満玉 ({total}/{total})"
    return f"{reached}/{total}"
