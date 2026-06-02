# v2.4 TASK-P6/P7: confidence の重みと AFFINITY テーブルを設定外出し
# 評価データが ≥30 件 揃ったら calibration ユーティリティで自動校正する設計。
# 現状は手動の暫定値だが、変更可能性を明示するため module 末尾に校正ヘルパーを追加。


"""DS Scout：各 DS 機（REI/ASUKA/SHINJI/KAWORU）が候補 pool から自分で銘柄を選ぶ。

設計：DS 主導フロー（2026-05-28 設計変更）
  - 従来: MISATO が assign_to_pilot で機を決定（受動）
  - 新規: 各 DS が自分の preset / horizon / ボラ基準で pool を覗き、申請を出す（能動）

各機の選定基準:
  🔵 REI    守り長期: value / dividend / long-horizon / 低ボラ
  🔴 ASUKA  攻め中期: momentum / growth / mid-short / 高ボラ
  🟣 SHINJI 中庸:    pullback / contrarian / mid / 中ボラ
  🌒 KAWORU 短期合議: alpha / 静観・合議銘柄 / 短期 / タイト stop

各申請には confidence (0.0-1.0) が付与され、MISATO の予算配分の入力になる。
高 confidence = 「この機の性格にドンピシャ」、低 confidence = 「拾えるけど確度低」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from trading_agent.portfolio.misato import CandidatePool


@dataclass(frozen=True)
class PilotProposal:
    """1 機が「この銘柄を買いたい」と MISATO に申請する 1 件。

    `confidence` は 0.0〜1.0 で機の確信度。MISATO がこれを集約して予算配分する。
    `min_budget_jpy` はその銘柄の単価（=最低 1 株買うのに必要な金額）。
    `reason` は人間が読む理由文字列（UI / レポートで使う）。
    """

    pilot_name: str
    ticker: str
    confidence: float        # 0.0-1.0
    min_budget_jpy: float    # 1 株分の参考予算（実 fill 時の最低単位）
    reason: str
    source: str = "magi"     # "magi" / "zeele" / "both"
    preset: str | None = None
    score: float = 0.0       # 候補そのものの score（pool から引き継ぐ）


# 機別の「採用できる preset」と confidence の重み付け。
# 1.0 = ドンピシャ、0.5 = 拾えるが弱い、0.0 = スコープ外。
_PRESET_AFFINITY: dict[str, dict[str, float]] = {
    "REI": {
        "value": 1.0,
        "dividend": 1.0,
        "contrarian": 0.6,   # 売られすぎ拾いは守りに近い側面
        "pullback": 0.3,     # 押し目買いは少し攻め気味なので低
        "growth-value": 0.5,
    },
    "ASUKA": {
        "momentum": 1.0,
        "growth": 1.0,
        "alpha": 0.6,
        "pullback": 0.5,     # 上昇中の押し目買いは攻めの中継戦略
        "growth-value": 0.7,
    },
    "SHINJI": {
        "pullback": 1.0,
        "contrarian": 1.0,
        "growth-value": 0.7,
        "value": 0.5,
        "growth": 0.4,
    },
    "KAWORU": {
        "alpha": 1.0,
        "growth": 0.7,
        "momentum": 0.7,
        "pullback": 0.5,
        # KAWORU は静観も合議も拾う「いいとこどり」型なので幅広い
    },
}


def _horizon_affinity(pilot: str, target_days: int | None) -> float:
    """horizon が機の好みに合うか（0.0-1.0）。"""
    if target_days is None:
        return 0.5
    if pilot == "REI":     # 長期 (>=90d) 寄り
        return 1.0 if target_days >= 90 else (0.6 if target_days >= 60 else 0.3)
    if pilot == "ASUKA":   # 中期 (30-90d) 寄り
        return 1.0 if 30 <= target_days <= 90 else (0.6 if target_days < 30 else 0.5)
    if pilot == "SHINJI":  # 中庸 (30-120d) 広め
        return 1.0 if 30 <= target_days <= 120 else 0.5
    if pilot == "KAWORU":  # 短期 (<=30d) 寄り
        return 1.0 if target_days <= 30 else (0.5 if target_days <= 60 else 0.2)
    return 0.5


def _vol_affinity(pilot: str, stop_pct: float) -> float:
    """ボラティリティ (stop_pct) が機の好みに合うか。"""
    if pilot == "REI":     # 低ボラ (<0.08) 〜 中ボラ (~0.12)
        return 1.0 if stop_pct <= 0.10 else (0.6 if stop_pct <= 0.15 else 0.3)
    if pilot == "ASUKA":   # 中〜高ボラ (0.08-0.15)
        return 1.0 if 0.08 <= stop_pct <= 0.15 else 0.5
    if pilot == "SHINJI":  # 中ボラ (0.08-0.12)
        return 1.0 if 0.08 <= stop_pct <= 0.12 else (0.7 if stop_pct <= 0.15 else 0.4)
    if pilot == "KAWORU":  # タイト stop (≤0.08) or 高ボラ (≥0.13) 短期決着
        if stop_pct <= 0.06:
            return 1.0
        if stop_pct >= 0.13:
            return 0.7
        return 0.5
    return 0.5


def _stance_affinity(pilot: str, stance: str) -> float:
    """GENDO stance が機の採用基準に合うか。"""
    if pilot == "REI":
        if stance == "推し":
            return 1.0
        if stance == "要検討":
            return 0.7
        return 0.0
    if pilot == "ASUKA":
        if stance in ("推し", "要検討"):
            return 1.0
        return 0.0
    if pilot == "SHINJI":
        if stance in ("推し", "要検討"):
            return 0.9
        if stance == "ZEELE":
            return 0.8
        return 0.0
    if pilot == "KAWORU":
        # KAWORU は全 stance 採用（静観・合議含む）
        if stance == "静観":
            return 1.0   # 静観専門
        if stance == "ZEELE":
            return 0.8
        if stance in ("推し", "要検討"):
            return 0.6
        return 0.3
    return 0.5


# v2.4 TASK-P6: confidence 重み（4 軸）。実証データで校正予定。
# 環境変数で上書き可。
import os as _os_p6
_CONF_W_PRESET = float(_os_p6.environ.get("DS_CONF_W_PRESET", "0.4"))
_CONF_W_HORIZON = float(_os_p6.environ.get("DS_CONF_W_HORIZON", "0.2"))
_CONF_W_VOL = float(_os_p6.environ.get("DS_CONF_W_VOL", "0.2"))
_CONF_W_STANCE = float(_os_p6.environ.get("DS_CONF_W_STANCE", "0.2"))


def _compute_confidence(
    pilot: str,
    cand: "CandidatePool",
    *,
    consensus: bool = False,
) -> float:
    """1 機の 1 候補に対する confidence (0.0-1.0)。

    式: 0.4 × preset + 0.2 × horizon + 0.2 × vol + 0.2 × stance + ダブル推奨ボーナス
    consensus（REI/ASUKA/SHINJI 全員保有銘柄）の KAWORU 用ボーナスは +0.3
    v2.4 TASK-P6: 重みは _CONF_W_* （環境変数で上書き可能）から取得

    v2.1 TASK-P3: stop_pct / target_period_days が真に欠損なら confidence をディスカウント。
    ※ CandidatePool は欠損時に fallback 値を持つため、ここでは「fallback 値そのまま」を
       簡易検出（horizon=mid 既定値 + stop_pct=0.10 既定値）。完全には判別できないので
       上流（_build_candidate_pool）で欠損フラグを持つのが理想（次回改善）。
    """
    # H3 修正: preset の取得状況を明示分岐（0.5 偽装を廃止）
    # - preset 既知 + AFFINITY に登録: その値（0.0-1.0）
    # - preset 既知 + AFFINITY 未登録: 0.0（スコープ外）
    # - preset 不明（None）: 0.2 ディスカウント（"判定材料なし" を明示・旧 0.5 偽装を廃止）
    if cand.preset:
        preset_aff = _PRESET_AFFINITY.get(pilot, {}).get(cand.preset, 0.0)
        preset_is_unknown = False
    else:
        preset_aff = 0.2  # 旧 0.5（中立偽装）→ 0.2 にディスカウント
        preset_is_unknown = True
    horizon_aff = _horizon_affinity(pilot, cand.target_period_days)
    vol_aff = _vol_affinity(pilot, cand.stop_pct)
    stance_aff = _stance_affinity(pilot, cand.gendo_stance)

    # v2.8: REI/ASUKA/SHINJI は stance + preset の両方がスコープ外なら申請しない
    # - stance "推し/要検討" 等で stance_aff > 0 なら申請（MAGI 由来）
    # - stance "ZEELE" は preset で判定（preset_aff > 0.5 なら申請）
    # - 両方該当しない（"静観" + preset 不明 等）は申請しない → KAWORU contrarian が拾う
    if pilot in ("REI", "ASUKA", "SHINJI") and stance_aff == 0 and preset_aff <= 0.5:
        return 0.0

    base = (
        _CONF_W_PRESET * preset_aff
        + _CONF_W_HORIZON * horizon_aff
        + _CONF_W_VOL * vol_aff
        + _CONF_W_STANCE * stance_aff
    )
    if cand.source == "both":
        base += 0.15  # MAGI + ZEELE 両方推奨はボーナス
    if pilot == "KAWORU" and consensus:
        base += 0.25  # 合議銘柄は KAWORU の最も得意分野

    # v2.1 TASK-P3: データ不足ディスカウント（horizon / stop が fallback 既定値だと割引）
    if cand.target_period_days is None:
        base *= 0.85  # horizon 不明：15% ディスカウント
    if cand.stop_pct == 0.10 and cand.source == "magi":
        # 0.10 は fallback の代表値。screening 由来で 0.10 ちょうどは少ない
        base *= 0.90  # vol 不明 fallback の疑い：10% ディスカウント
    # H3 修正 (v2.8): preset 不明はさらに 10% ディスカウント（データ品質を confidence に反映）
    if preset_is_unknown:
        base *= 0.90

    return min(max(base, 0.0), 1.0)


def _fetch_min_budgets(
    tickers: list[str],
    *,
    available_budget_jpy: float | None = None,
) -> dict[str, float | None]:
    """v2.1 TASK-P1: ticker 群の最低必要予算を yfinance bulk で取得。

    失敗銘柄は None を返し（fallback しない＝欺瞞回避）、呼び出し側でスキップする。

    v2.8: 実弾モード時は単元株（JP 100 株）× 価格を返す。
    Paper モードは 1 株価格そのまま（既存挙動）。

    Args:
        available_budget_jpy: 購入可能金額（treasury 残高）。実弾モード時に
            「1 単元コスト > available_budget × WILLE_MAX_LOT_PCT」の銘柄を除外する。
    """
    if not tickers:
        return {}
    from trading_agent.utils.lot_size import effective_lot_size, get_max_lot_cost_jpy, is_live_mode

    out: dict[str, float | None] = {}
    try:
        import yfinance as yf
    except Exception:
        return {t: None for t in tickers}

    # USD/JPY を 1 回だけ取得（米株を JPY 換算）
    try:
        usdjpy = float(yf.Ticker("JPY=X").fast_info.last_price)
    except Exception:
        usdjpy = None

    live = is_live_mode()
    max_lot_cost = get_max_lot_cost_jpy(available_budget_jpy) if live else float("inf")

    from trading_agent.mcp_tools.fundamentals import (
        is_jp_ticker as _is_jp,
        to_yfinance_symbol,
    )

    for t in tickers:
        # v2.10: 新型 ticker (141A 等) も含めた判定・付与に統一
        is_jp = _is_jp(t)
        sym = to_yfinance_symbol(t)
        try:
            p = float(yf.Ticker(sym).fast_info.last_price)
            if is_jp:
                price_jpy = p
            elif usdjpy is not None:
                price_jpy = p * usdjpy
            else:
                out[t] = None  # 為替取れず → 換算不能
                continue
            # v2.8: 実弾モードなら単元株 × 価格、Paper なら 1 株価格
            lot = effective_lot_size(t)
            lot_cost = price_jpy * lot
            # v2.8: 実弾モードで「1 単元コスト > 上限」の銘柄は申請対象から除外
            if live and lot_cost > max_lot_cost:
                out[t] = None
                continue
            out[t] = lot_cost
        except Exception:
            out[t] = None  # fallback しない
    return out


def select_from_pool(
    pilot: str,
    pool: list["CandidatePool"],
    *,
    engine: "Engine | None" = None,
    confidence_threshold: float = 0.4,
    min_budgets: dict[str, float | None] | None = None,
) -> list[PilotProposal]:
    """機 `pilot` が pool から自分の判断で買いたい銘柄を選んで申請を返す。

    confidence ≥ threshold の候補のみ採用。これにより「俺向きじゃない」候補は機が降りる。

    v2.1 TASK-P1: `min_budgets` で実価格を渡せる（dispatch が一括取得して全機で共有）。
    `min_budgets[ticker] is None` の銘柄は申請しない（価格不明 = 買えない）。
    """
    # v2.8: KAWORU の合議銘柄経路は廃止（select_kaworu_contrarian を使う）
    # select_from_pool は REI/ASUKA/SHINJI 用。KAWORU が呼ばれた場合も consensus 加点なし
    consensus_set: set[str] = set()

    # 価格マップ（外部から渡されなければ取得）
    if min_budgets is None:
        min_budgets = _fetch_min_budgets([c.ticker for c in pool])

    proposals: list[PilotProposal] = []
    for cand in pool:
        is_consensus = cand.ticker in consensus_set
        conf = _compute_confidence(pilot, cand, consensus=is_consensus)
        if conf < confidence_threshold:
            continue

        # v2.1 TASK-P1: 実価格取得 / 取得不能なら申請しない（欺瞞回避）
        min_budget = min_budgets.get(cand.ticker)
        if min_budget is None or min_budget <= 0:
            continue

        # 申請理由文字列を組み立て
        bits: list[str] = []
        if cand.source == "both":
            bits.append("★ MAGI∩ZEELE")
        elif cand.source == "zeele":
            bits.append(f"ZEELE {cand.preset or ''}")
        else:
            bits.append(f"MAGI {cand.gendo_stance}")
        if is_consensus and pilot == "KAWORU":
            bits.append("3 機合議")
        bits.append(f"conf={conf:.2f}")
        reason = " / ".join(bits)

        proposals.append(
            PilotProposal(
                pilot_name=pilot,
                ticker=cand.ticker,
                confidence=conf,
                min_budget_jpy=min_budget,
                reason=reason,
                source=cand.source,
                preset=cand.preset,
                score=cand.score,
            )
        )

    # confidence 降順でソート（高確信から処理しやすく）
    proposals.sort(key=lambda p: (-p.confidence, -p.score, p.ticker))
    return proposals


# ============================================================
# 🌒 KAWORU: コントラリアン短期機（v2.8 再設計）
# ============================================================
#
# 旧 KAWORU: 他機が保有している銘柄に乗っかる「合議」型
#   → 機跨ぎ重複の原因 + 独立した価値なし
# 新 KAWORU: 他機が proposal を出さなかった銘柄を、短期エッジで拾う
#   → 重複ゼロ + 独立した役割（落ち穂拾い）
#
# 短期エッジ:
#   1. RSI 50-65（過熱手前のスイートスポット）
#   2. 業界トレンドが追い風（Brief.industry_score > 0）
#   3. ニュース sentiment が +（Phase B 以降に有効化）
#   4. horizon が短期向け（KAWORU は ≤21 日）


def _compute_kaworu_short_term_confidence(
    cand: "CandidatePool",
    brief: object | None = None,
    *,
    rsi: float | None = None,
    industry_s: float = 0.0,
    news_s: float = 0.0,
) -> float:
    """KAWORU の短期エッジ confidence (0.0-1.0)。

    重み配分:
      - RSI スイートスポット: 0.30
      - 業界トレンド +     : 0.20
      - ニュース +         : 0.20（Phase B 後に効く）
      - 短期 horizon 適性  : 0.30

    A+（DS-first）: 値（rsi/industry_s/news_s）を直接渡せる。`brief` を渡すと従来通り
    brief から抽出する（後方互換）。dispatch の A+ 経路では選定段階で RSI のみ渡し、
    業界/ニュースは AKAGI 検証後の priority で効かせる（選定段階では 0）。
    """
    score = 0.0

    # brief が渡されたら brief から抽出（後方互換）。明示の値があればそちらを優先しない
    # （brief 経路と値経路は排他的に使う想定）。
    if brief is not None:
        tech = getattr(brief, "technicals", None)
        rsi = getattr(tech, "rsi", None) if tech is not None else None
        industry_s = float(getattr(brief, "industry_score", 0.0) or 0.0)
        news_s = float(getattr(brief, "news_sentiment_score", 0.0) or 0.0)

    # RSI スイートスポット（50-65 = 上昇トレンド中で過熱手前）
    if rsi is not None:
        if 50 <= rsi <= 65:
            score += 0.30
        elif 45 <= rsi < 50 or 65 < rsi <= 70:
            score += 0.15
    else:
        # 技術指標が不明な場合は中立加点（Phase B で MAGI Technicals が埋まれば改善）
        score += 0.12

    # 業界スコア
    if industry_s > 0.2:
        score += 0.20
    elif industry_s > 0:
        score += 0.10

    # ニュース sentiment（Phase B 後に実値が入る）
    if news_s > 0.2:
        score += 0.20
    elif news_s > 0:
        score += 0.10

    # 短期 horizon 適性
    score += _horizon_affinity("KAWORU", cand.target_period_days) * 0.30

    return min(max(score, 0.0), 1.0)


def select_kaworu_contrarian(
    pool: list["CandidatePool"],
    *,
    excluded_tickers: set[str],
    briefs: dict[str, object] | None = None,
    technicals_lookup: dict[str, dict[str, object]] | None = None,
    engine: "Engine | None" = None,  # noqa: ARG001  予約（将来パラメータ）
    confidence_threshold: float = 0.35,
    min_budgets: dict[str, float | None] | None = None,
) -> list[PilotProposal]:
    """🌒 KAWORU 専用：他機が選ばなかった銘柄から短期エッジで拾う。

    Args:
        pool: 候補プール全体
        excluded_tickers: REI/ASUKA/SHINJI が proposal を出した銘柄（除外対象）
        briefs: RITSUKO TickerBrief（従来経路。渡すと RSI/業界/ニュースを選定に使う）
        technicals_lookup: A+（DS-first）経路。{ticker: {"rsi": float}} の軽量 technicals。
            渡された場合は **RSI のみで選定**し、業界/ニュースは選定に使わない（AKAGI 検証後の
            priority で効かせる）。briefs より優先。選定者(DS)と検証者(AKAGI)を分離する。
        confidence_threshold: 採用閾値（contrarian なので少し厳しめ 0.35）
        min_budgets: 1 株分の参考予算

    Returns:
        KAWORU の PilotProposal リスト（confidence 降順）
    """
    briefs = briefs or {}
    use_technicals = technicals_lookup is not None
    technicals_lookup = technicals_lookup or {}

    # 除外後の対象銘柄のみ価格取得
    target_pool = [c for c in pool if c.ticker not in excluded_tickers]
    if not target_pool:
        return []

    if min_budgets is None:
        min_budgets = _fetch_min_budgets([c.ticker for c in target_pool])

    proposals: list[PilotProposal] = []
    for cand in target_pool:
        if use_technicals:
            # A+ 経路: 軽量 RSI のみで選定（業界/ニュースは選定に使わない）
            rsi_val = technicals_lookup.get(cand.ticker, {}).get("rsi")
            rsi = float(rsi_val) if rsi_val is not None else None
            conf = _compute_kaworu_short_term_confidence(cand, rsi=rsi)
        else:
            # 従来経路: brief から RSI/業界/ニュースを抽出
            brief = briefs.get(cand.ticker)
            conf = _compute_kaworu_short_term_confidence(cand, brief)
            rsi = getattr(getattr(brief, "technicals", None), "rsi", None) if brief else None
        if conf < confidence_threshold:
            continue

        min_budget = min_budgets.get(cand.ticker)
        if min_budget is None or min_budget <= 0:
            continue

        # 申請理由
        bits = ["🌒 KAWORU contrarian"]
        if rsi is not None:
            bits.append(f"RSI={rsi:.0f}")
        if not use_technicals:
            brief = briefs.get(cand.ticker)
            industry_s = float(getattr(brief, "industry_score", 0.0) or 0.0) if brief else 0.0
            if industry_s > 0.2:
                bits.append(f"業界+{industry_s:.2f}")
        bits.append(f"conf={conf:.2f}")

        proposals.append(
            PilotProposal(
                pilot_name="KAWORU",
                ticker=cand.ticker,
                confidence=conf,
                min_budget_jpy=min_budget,
                reason=" / ".join(bits),
                source=cand.source,
                preset=cand.preset,
                score=float(cand.score or 0.0),
            )
        )

    proposals.sort(key=lambda p: (-p.confidence, -p.score, p.ticker))
    return proposals
