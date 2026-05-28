"""ダミーシステム（DUMMY）の性格（personality）定義。

MAGI と並ぶ自動操縦機構＝ペーパー検証用の複数性格パイロット。同じ MAGI 評価結果
（推し/要検討/静観）に対して、3 ダミー（守式・攻式・中庸式）が並行で auto-approve
→ 紙約定し、30 件評価後に性格別の hit_rate / R-mult を比較する。

ダミー名は文字列キー（"defender" / "aggressor" / "balanced"）。
Portfolio.personality と Decision.personalities_filled に保存される。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Personality:
    """1 つの性格の運用ルール一式。"""

    name: str  # 内部キー（"defender" / "aggressor" / "balanced"）
    label: str  # 表示用ラベル（"守り" 等）
    icon: str  # 絵文字（UI / レポート用）
    overlay_cash_jpy: int  # この性格の初期 cash（仮想入金額）
    accept_stances: tuple[str, ...]  # auto-approve 対象の gendo_stance
    max_position_pct: float  # 1 銘柄上限（総資産比）
    horizon_days: int  # 保有期限
    stop_loss_pct: float  # 損切り（正値・portfolio 側で負値化）
    description: str  # レポート用説明


PERSONALITIES: dict[str, Personality] = {
    "REI": Personality(
        name="REI",
        label="DS/REI",
        icon="🔵",
        overlay_cash_jpy=100_000,
        # 「守りすぎて何も買わない」を防ぐため、推し が無い日は要検討まで広げる。
        # ただし fallback として用意し、本来は推し優先（builder で stance 別に評価される）。
        accept_stances=("推し", "要検討"),
        max_position_pct=0.15,  # 1 銘柄 ¥15,000 まで（高価格 JP 1 株を許容するサイズ）
        horizon_days=180,        # 長期保有は維持
        stop_loss_pct=0.15,      # 守りでも buy できるよう stop 緩和（12% → 15%）
        description=(
            "MAGI 全員一致＋機械照合 OK の「推し」を最優先。推しがない日は「要検討」も拾うが、"
            "サイジングは中庸・長期保有・stop 緩めで「動くが負けない」を狙う。"
        ),
    ),
    "ASUKA": Personality(
        name="ASUKA",
        label="DS/ASUKA",
        icon="🔴",
        overlay_cash_jpy=100_000,
        accept_stances=("推し", "要検討"),
        max_position_pct=0.20,  # 攻めの名のとおり最大級（REI 15% より大きく・KAWORU と並ぶ）
        horizon_days=60,
        stop_loss_pct=0.08,
        description=(
            "「推し」+「要検討」を大胆に採用。1 銘柄あたり最大 20% で集中投資・"
            "短期回転・stop -8% で素早く撤退する真の攻め型。"
        ),
    ),
    "SHINJI": Personality(
        name="SHINJI",
        label="DS/SHINJI",
        icon="🟣",
        overlay_cash_jpy=100_000,
        accept_stances=("推し", "要検討"),
        max_position_pct=0.07,
        horizon_days=90,
        stop_loss_pct=0.10,
        description="守りと攻めの間。「推し」+「要検討」を中サイズで採用し、中期保有。",
    ),
    "KAWORU": Personality(
        name="KAWORU",
        label="DS/KAWORU",
        icon="🌒",
        overlay_cash_jpy=100_000,
        # 全 stance（静観含む）を採用。さらに 3 機の合議銘柄を最優先で拾う「いいとこどり」。
        accept_stances=("推し", "要検討", "静観"),
        max_position_pct=0.20,
        horizon_days=21,
        stop_loss_pct=0.05,
        description=(
            "いいとこどりの第17使徒。REI/ASUKA/SHINJI の 3 機が共通で買った銘柄を最優先で"
            "拾い（皆が買うものは強い）、加えて 3 機が見ない「静観」も独自眼で組み入れる。"
            "一点集中・短期回転・極タイト stop で「合議の総取り＋異質シグナル」を狙う異形枠。"
        ),
    ),
}


def get_personality(name: str) -> Personality:
    """名前から性格を取得。未知の名前は KeyError。"""
    return PERSONALITIES[name]


def all_personalities() -> tuple[Personality, ...]:
    return tuple(PERSONALITIES.values())


def effective_max_position_pct(personality: Personality, *, pnl_pct: float) -> float:
    """累積 PnL に応じて max_position_pct を可変。

    - 累積 +5% 超: ×1.2 倍（勝ってる時は強気）
    - 累積 -5% 超: ×0.8 倍（負けてる時は守り）
    - それ以外: 据え置き
    """
    base = personality.max_position_pct
    if pnl_pct >= 5.0:
        return min(base * 1.2, 0.30)  # 上限 30% で集中しすぎ防止
    if pnl_pct <= -5.0:
        return base * 0.8
    return base


RULE_SUMMARY: dict[str, str] = {
    "REI": "推し+要検討 / 1銘柄 15% / 保有 180日 / Stop -15% / 動的: PnL±5% で ±20%",
    "ASUKA": "推し+要検討 / 1銘柄 20% / 保有 60日 / Stop -8% / 動的: PnL±5% で ±20%",
    "SHINJI": "推し+要検討 / 1銘柄 7% / 保有 90日 / Stop -10% / 動的: PnL±5% で ±20%",
    "KAWORU": "全 stance + 3 機合議優先 / 1銘柄 20% / 保有 21日 / Stop -5% / 動的: PnL±5% で ±20%",
}
