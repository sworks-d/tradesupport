"""(c) fundamental_event_score: 構造化イベント + news タグ → 0-100 連続スコア（record-only）。

discrete タグ（event_upward_revision / news_positive …）の cohort 比較は、タグが立つ希少な
decision（実測 数%）が n≥20 に達するまで月単位かかる。本モジュールは同じタグから**全 decision に
連続スコア**を与え、(d) が score×outcome を全サンプルで相関測定できる土台を作る（throughput 軸ii）。

**この層が score / bucket / cut point の単一真実源**（(d)=forward_diagnosis は bucket_event_score
を import するだけ・閾値を持たない）。式・cut point は version にロックし、変えたら bump する
（codex p-hacking 防止＝観測後に閾値を後出ししない）。今は **score を記録するだけ**で
相関主張はしない（評価済 outcome n=1）。
"""

from __future__ import annotations

from dataclasses import dataclass

# 現行スコア式の version。式 or 重みを変えたら bump（過去 decision は自分の version で解釈される）。
EVENT_SCORE_VERSION = "event_score_v1"

# event_score_v1 の重み（50 中立からの加減算）。構造化イベント（硬い事実）を news 見出しより重く。
# **変更は version bump 必須**（cut point も含め version 定義の一部）。
_V1_WEIGHTS: dict[str, float] = {
    # 構造化（J-Quants 由来・低ノイズ）
    "event_upward_revision": +25.0,
    "event_downward_revision": -25.0,
    "event_dividend_hike": +15.0,
    "event_dividend_cut": -15.0,
    "earnings_accel": +15.0,
    # news 見出し（辞書・弱い）→ 単独では tail(low/high)に届かせない（±8 < neutral 帯 50±10）
    "news_positive": +8.0,
    "news_negative": -8.0,
}

# score_version → (low_cut, high_cut, cutpoint_version)。
# **過去 version の行は不変**（歴史的 bucketing を安定させ p-hacking 防止）。新 version は1行追加。
_CUTPOINT_REGISTRY: dict[str, tuple[float, float, str]] = {
    "event_score_v1": (40.0, 60.0, "cutpoint_v1"),
}


@dataclass(frozen=True)
class BucketResult:
    """bucket_event_score の戻り。bucketer 同一性 = score_version × cutpoint_version で決まる。"""

    bucket: str                    # "unscored" | "low" | "mid" | "high"
    score_version: str | None      # 入力 score の version（そのまま返す＝(d) が記録）
    cutpoint_version: str | None    # 使った cut point の version（unscored 時は None）


def compute_fundamental_event_score(tags: list[str] | None) -> float:
    """signal タグ群 → 0-100 の fundamental_event_score（event_score_v1）。

    50 = 中立（該当イベント無し）。>50 = net positive。重みは `_V1_WEIGHTS`（構造化 > news）。
    既知タグのみ加減算し、未知タグは無視。[0, 100] にクランプ。
    ※ producer が走れば必ず数値を返す（イベント無し＝50）。DB の None は (c) 未処理（legacy/未走）。
    """
    score = 50.0
    for tag in (tags or []):
        score += _V1_WEIGHTS.get(tag, 0.0)
    return max(0.0, min(100.0, score))


def bucket_event_score(
    score: float | None, score_version: str | None = None
) -> BucketResult:
    """(c) 単一真実源: score を low/mid/high にバケット（(d) はこれを import・閾値を持たない）。

    score=None（未処理）または score_version が registry に無い（未知/不一致）→ **unscored**
    （1組の閾値で誤バケットしない＝p-hacking 防止 + silent mis-bucket 回避）。
    既知 version のみ、その version の cut point で bucket する（歴史的安定）。
    """
    reg = _CUTPOINT_REGISTRY.get(score_version) if score_version is not None else None
    if score is None or reg is None:
        return BucketResult("unscored", score_version, None)
    low_cut, high_cut, cutpoint_version = reg
    if score <= low_cut:
        bucket = "low"
    elif score >= high_cut:
        bucket = "high"
    else:
        bucket = "mid"
    return BucketResult(bucket, score_version, cutpoint_version)
