"""S7（テーマ4軸）：相対力（Relative Strength）と4象限。RESEARCH_METHODS 領域3-B。

「どのテーマ/銘柄に資金が流れているか」を**対市場の相対力**でコード検出（人間の決め打ちでなく）。
RRG（Relative Rotation Graph）の4象限：
- Leading（力◯・勢◯）／Weakening（◯・✕）／Lagging（✕・✕）／**Improving（✕・◯＝底から改善）**。
**Improving が「テーマV字」**＝中期投資と相性が良い（領域3-B）。テーマの「方向」は相対力（コード）、
「理由」はCASPER（文脈）が担う役割分担。**高回転を避ける制約**（中期）＝判定はエントリーの追い風に使う。

数値はコード（R1）。価格列は注入（テスト可能）。市場proxyは market で既定（US=^GSPC・JP=^N225）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

PriceHistory = Callable[[str], list[float]]  # ticker → 終値リスト（古→新）

# 市場proxy（相対力の分母）。出所明確・流動的なベンチマーク。
_MARKET_PROXY = {"US": "^GSPC", "JP": "^N225"}


@dataclass
class RSResult:
    quadrant: str  # leading / weakening / lagging / improving / na
    rs_long: float | None  # 長期の対市場相対リターン
    rs_short: float | None  # 短期の対市場相対リターン
    note: str = ""


def _ret(prices: list[float], window: int) -> float | None:
    """window 期前比リターン。データ不足/0除算は None。"""
    if len(prices) <= window or prices[-1 - window] == 0:
        return None
    return prices[-1] / prices[-1 - window] - 1.0


# v2.5 TASK-Z15: 期間を環境変数で上書き可能（地域別・銘柄群別で調整可能性確保）
import os as _os_rs
_DEFAULT_RS_SHORT = int(_os_rs.environ.get("RS_SHORT_DAYS", "21"))  # 1 ヶ月
_DEFAULT_RS_LONG = int(_os_rs.environ.get("RS_LONG_DAYS", "63"))   # 3 ヶ月


def compute_relative_strength(
    ticker_prices: list[float],
    market_prices: list[float],
    *,
    short: int = _DEFAULT_RS_SHORT,
    long: int = _DEFAULT_RS_LONG,
) -> RSResult:
    """対市場の相対力を長短2窓で測り、4象限に分類する。

    rs = 銘柄リターン − 市場リターン（同期間）。long で強弱、short で改善/悪化の方向。
    """
    if len(ticker_prices) <= long or len(market_prices) <= long:
        return RSResult("na", None, None, "価格履歴が不足（相対力算定不能）")

    rs_long = _diff(_ret(ticker_prices, long), _ret(market_prices, long))
    rs_short = _diff(_ret(ticker_prices, short), _ret(market_prices, short))
    if rs_long is None or rs_short is None:
        return RSResult("na", rs_long, rs_short, "リターン算定不能")

    strong = rs_long > 0  # 長期で市場を上回る
    rising = rs_short > 0  # 短期で市場を上回る（改善）
    if strong and rising:
        q, note = "leading", "相対力◯・モメンタム◯（主導）"
    elif strong and not rising:
        q, note = "weakening", "相対力◯だが短期は失速"
    elif not strong and rising:
        q, note = "improving", "相対力は弱いが底から改善＝テーマV字候補"
    else:
        q, note = "lagging", "相対力✕・モメンタム✕（出遅れ）"
    return RSResult(q, round(rs_long, 4), round(rs_short, 4), note)


def _diff(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


def market_proxy(market: str) -> str:
    """市場コード→ベンチマークのシンボル（相対力の分母）。"""
    return _MARKET_PROXY.get(market, "^GSPC")


def relative_strength_live(
    ticker: str, market: str, *, history: PriceHistory,
    short: int = _DEFAULT_RS_SHORT, long: int = _DEFAULT_RS_LONG,
) -> RSResult:
    """ライブ：history(ticker) と history(proxy) から相対力を出す。失敗は na。"""
    try:
        tp = history(ticker)
        mp = history(market_proxy(market))
    except Exception:
        return RSResult("na", None, None, "価格取得失敗")
    return compute_relative_strength(tp, mp, short=short, long=long)
