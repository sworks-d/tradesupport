"""トレーリングストップ（v2.10 Phase 3）。

含み益が拡大したら stop ラインを引き上げ、利益の取りこぼしを防ぐ。
損益分岐線を超えたら「絶対に損しない」状態に持っていく。

設計（含み益の段階に応じた stop ライン引き上げ）:
  含み益 < +5%:    元の stop_loss_pct のまま（例: -8%）
  含み益 ≥ +5%:    stop を元 stop + 5% 引き上げ（例: -8% → -3%）
  含み益 ≥ +10%:   stop を元 stop + 10% 引き上げ（例: -8% → +2% = 利益確保）
  含み益 ≥ +20%:   stop を元 stop + 15% 引き上げ（例: -8% → +7% = 利益確定線）
  含み益 ≥ +30%:   stop を元 stop + 20% 引き上げ（例: -8% → +12% = 大幅利益確定）

エントリー価格に対する閾値:
  trail_price = entry_price * (1 + effective_stop_pct)

ハルシネーション対策:
  - 含み益が None なら trailing 適用せず元 stop を返す（推測しない）
  - stop 引き上げは「上書き」のみ（下げない）= モノトニック
  - 機別の補正は呼び出し側で（このモジュールは汎用ロジック）
"""

from __future__ import annotations

from dataclasses import dataclass

# 含み益 → 上乗せする stop シフト（正値 = stop ラインを「上」に引き上げ）
_TRAIL_TIERS: list[tuple[float, float]] = [
    (0.05, 0.05),   # +5% 含み益 → stop を +5% シフト
    (0.10, 0.10),   # +10% 含み益 → stop を +10% シフト
    (0.20, 0.15),   # +20% 含み益 → stop を +15% シフト
    (0.30, 0.20),   # +30% 含み益 → stop を +20% シフト
]


@dataclass(frozen=True)
class TrailingStopResult:
    """トレーリングストップの計算結果。"""

    effective_stop_pct: float    # 適用すべき stop 比率（負値）
    trail_price: float           # 引き上げ後の絶対 stop 価格
    shift_applied: float         # 元 stop からの上乗せ（正値 = 引き上げ）
    is_break_even: bool          # 損益分岐を超えたか（stop ≥ 0）
    is_profit_locked: bool       # 利益確定線か（stop > 0）
    tier_label: str              # UI 表示用ラベル


def compute_trailing_stop(
    *,
    entry_price: float,
    current_price: float | None,
    base_stop_pct: float,
    peak_pnl_pct: float | None = None,
) -> TrailingStopResult:
    """トレーリングストップを計算する。

    v2.10 Phase 1A-Step2: 真の trailing 対応。
    peak_pnl_pct（過去の含み益ピーク）を渡せば、それを基準に stop を引き上げる。
    peak_pnl_pct=None なら従来通り「現在の含み益から都度計算」（動的 stop）。

    モノトニック性:
      - peak 基準なら、価格下落しても stop ラインは下がらない（真の trailing）
      - 引数 peak_pnl_pct は呼び出し側が「過去最大」として渡す責任

    Args:
        entry_price: エントリー価格
        current_price: 現在の価格（None なら trailing 適用せず）
        base_stop_pct: 元の stop_loss_pct（正値で渡す、内部で負値化）
        peak_pnl_pct: 過去ピーク含み益（None なら現在の含み益で代用＝動的 stop）

    Returns:
        TrailingStopResult
    """
    # 元の stop 比率（負値）
    base_stop = -abs(base_stop_pct)

    # 現在価格 None or エントリーゼロ → 元 stop のまま（推測しない）
    if current_price is None or entry_price <= 0:
        return TrailingStopResult(
            effective_stop_pct=base_stop,
            trail_price=entry_price * (1.0 + base_stop),
            shift_applied=0.0,
            is_break_even=False,
            is_profit_locked=False,
            tier_label="data n/a",
        )

    # 現在の含み益率
    pnl_pct = (current_price - entry_price) / entry_price

    # 真の trailing: peak_pnl_pct があればそれを使う（モノトニック）
    # 動的 stop: peak がなければ現在の pnl_pct を使う
    reference_pnl = (
        peak_pnl_pct if peak_pnl_pct is not None and peak_pnl_pct > pnl_pct else pnl_pct
    )

    # 段階に応じたシフト（モノトニック：最大値を採用）
    shift = 0.0
    tier_label = "未起動 (元 stop)"
    for trigger, s in _TRAIL_TIERS:
        if reference_pnl >= trigger:
            shift = s
            label_prefix = "peak " if peak_pnl_pct is not None else ""
            tier_label = f"{label_prefix}+{int(trigger * 100)}% 起動"

    effective_stop = base_stop + shift
    trail_price = entry_price * (1.0 + effective_stop)
    is_break_even = effective_stop >= 0.0
    is_profit_locked = effective_stop > 0.0

    return TrailingStopResult(
        effective_stop_pct=effective_stop,
        trail_price=trail_price,
        shift_applied=shift,
        is_break_even=is_break_even,
        is_profit_locked=is_profit_locked,
        tier_label=tier_label,
    )


def should_trigger_stop(
    *,
    current_price: float | None,
    trail_price: float,
) -> bool:
    """現在の価格がトレーリング stop 価格を下回ったかを判定。

    Args:
        current_price: 現在価格（None なら判定不能 → False を返す）
        trail_price: トレーリング後の stop 価格

    Returns:
        True なら売却推奨（stop 発動）。None or trail_price ≤ 0 は False（推測しない）。
    """
    if current_price is None or trail_price <= 0:
        return False
    return current_price <= trail_price
