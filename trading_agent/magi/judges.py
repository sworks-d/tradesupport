"""MAGI 3審判（独立検証）。B2 / MASTER §2.2 / MAGI_REQUIREMENTS §2.1。

対等な3審判が、**自分のソースだけ**を見て、互いの結論を見ずに可否を出す（三権独立）。
- MELCHIOR：業績（ファンダ）。fundamentals の実数値のみ。
- BALTHASAR：株価（テクニカル）。technicals の実計算のみ（コード計算＝LLMに計算させない）。
- CASPER：文脈（イベント）。news / disclosure。

この段では **LLMを使わず、数値・キーワードからコードが決定論的に**可否・確信度・根拠文を出す
（コスト0・再現可能）。LLMによる解釈文の付与は後続（CASPER の本格判定＝Sonnet 等）。
判定不能（データ欠損）は verdict="na" として正直に出す。総合スコアは出さない。
source_refs / data_asof は入力（MCP）から引き継ぎ、防御層(B3)の機械照合の土台にする。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from trading_agent.mcp_tools.base import SourceRef
from trading_agent.models.magi import JudgeVerdict

_VERDICT_WORD = {"buy": "買い", "warn": "慎重", "hold": "中立", "sell": "売り"}


def split_label(verdicts: list[JudgeVerdict]) -> str:
    """割れ方の簡易ラベル（一覧ミニ用）。総合スコアは出さず「割れ/一致」を示すのみ。

    本格的な割れ方類型（split_pattern・4類型）は統合機構 B4 で別途実装する。
    """
    actionable = [v for v in verdicts if v.verdict != "na"]
    if not actionable:
        return "判定不能"
    top, n = Counter(v.verdict for v in actionable).most_common(1)[0]
    word = _VERDICT_WORD.get(top, top)
    agree = "一致" if n == len(verdicts) else "割れ"
    return f"{n}/{len(verdicts)} {word}・{agree}"


def _refs_to_json(refs: list[SourceRef] | None) -> list[dict[str, Any]]:
    """SourceRef を JSON 安全な dict 列に（datetime は isoformat 文字列）。"""
    out: list[dict[str, Any]] = []
    for r in refs or []:
        out.append(
            {
                "source": r.source,
                "ref": r.ref,
                "as_of": r.as_of.isoformat() if r.as_of else None,
                "note": r.note,
            }
        )
    return out


def melchior(ticker: str, fundamentals: Any) -> JudgeVerdict:
    """業績審判：fundamentals の実数値だけで可否を出す。"""
    data = getattr(fundamentals, "data", {}) or {}
    refs = _refs_to_json(getattr(fundamentals, "source_refs", []))
    asof = getattr(fundamentals, "data_asof", None)

    rg = data.get("revenue_growth")
    om = data.get("operating_margin")
    if rg is None and om is None:
        return JudgeVerdict(
            ticker=ticker,
            judge="MELCHIOR",
            verdict="na",
            confidence="na",
            reason="財務数値が取得できず判定不能（データ欠損）。推測で埋めない。",
            source_refs=refs,
            data_asof=asof,
        )

    parts: list[str] = []
    if rg is not None:
        parts.append(f"増収率{rg * 100:.0f}%")
    if om is not None:
        parts.append(f"営業利益率{om * 100:.0f}%")

    good = (rg is not None and rg >= 0.10) and (om is not None and om >= 0.10)
    bad = (rg is not None and rg < 0) or (om is not None and om < 0)
    if good:
        verdict, conf = "buy", "高"
    elif bad:
        verdict, conf = "warn", "中"
    else:
        verdict, conf = "hold", "中"

    return JudgeVerdict(
        ticker=ticker,
        judge="MELCHIOR",
        verdict=verdict,
        confidence=conf,
        reason="・".join(parts) + "。",
        source_refs=refs,
        data_asof=asof,
    )


def balthasar(ticker: str, technicals: Any) -> JudgeVerdict:
    """株価審判：technicals のコード計算結果（シグナル/指標）だけで可否を出す。"""
    data = getattr(technicals, "data", {}) or {}
    signals = getattr(technicals, "signals", []) or []
    refs = _refs_to_json(getattr(technicals, "source_refs", []))
    asof = getattr(technicals, "data_asof", None)

    if not data and not signals:
        return JudgeVerdict(
            ticker=ticker,
            judge="BALTHASAR",
            verdict="na",
            confidence="na",
            reason="価格・テクニカルが取得できず判定不能（データ欠損）。",
            source_refs=refs,
            data_asof=asof,
        )

    bullish = ("golden_cross" in signals) or ("macd_bullish" in signals)
    bearish = ("death_cross" in signals) or ("overbought_rsi" in signals)
    if bearish and "golden_cross" not in signals:
        verdict, conf = "warn", "中"
    elif bullish and "overbought_rsi" not in signals:
        verdict, conf = "buy", "中"
    else:
        verdict, conf = "hold", "中"

    rsi = data.get("rsi")
    rsi_s = f"RSI{rsi:.0f}・" if isinstance(rsi, int | float) else ""
    sig_s = "・".join(signals) if signals else "明確なシグナルなし"
    return JudgeVerdict(
        ticker=ticker,
        judge="BALTHASAR",
        verdict=verdict,
        confidence=conf,
        reason=f"{rsi_s}{sig_s}。",
        source_refs=refs,
        data_asof=asof,
    )


# 文脈の方向を示唆するキーワード（決定論ヒューリスティック。本格判定はLLMで補完）
_NEG = ("下方修正", "減益", "赤字", "訴訟", "不正", "遅延", "リコール", "delay", "lawsuit", "recall")
_POS = ("上方修正", "最高益", "増配", "受注", "record", "beat", "surge")


def casper(ticker: str, news: Any = None, disclosure: Any = None) -> JudgeVerdict:
    """文脈審判：news / disclosure の材料から可否を出す（決定論キーワード＝確信度は低）。"""
    items: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    asof = None
    for out in (news, disclosure):
        if out is None:
            continue
        arts = getattr(out, "articles", None)
        if arts is None:
            arts = getattr(out, "disclosures", None) or []
        items.extend(arts)
        refs.extend(_refs_to_json(getattr(out, "source_refs", [])))
        a = getattr(out, "data_asof", None)
        if a is not None and (asof is None or a > asof):
            asof = a

    if not items:
        return JudgeVerdict(
            ticker=ticker,
            judge="CASPER",
            verdict="na",
            confidence="na",
            reason="文脈材料なし（ニュース・開示の取得なし）。判定不能。",
            source_refs=refs,
            data_asof=asof,
        )

    text = " ".join(
        f"{it.get('title', '')} {it.get('summary', '')}" for it in items
    ).lower()
    neg = sum(1 for k in _NEG if k.lower() in text)
    pos = sum(1 for k in _POS if k.lower() in text)
    if neg > pos:
        verdict = "warn"
    elif pos > neg:
        verdict = "buy"
    else:
        verdict = "hold"

    return JudgeVerdict(
        ticker=ticker,
        judge="CASPER",
        verdict=verdict,
        confidence="低",  # 決定論キーワードは弱い。本格判定はLLM解釈で補完予定
        reason=(
            f"直近{len(items)}件の材料（ネガ語{neg}・ポジ語{pos}）。"
            "本格的な文脈判定はLLM解釈で補完予定。"
        ),
        source_refs=refs,
        data_asof=asof,
    )


def run_judges(
    ticker: str,
    *,
    fundamentals: Any,
    technicals: Any,
    news: Any = None,
    disclosure: Any = None,
) -> list[JudgeVerdict]:
    """3審判を独立に走らせる。

    各審判は自分のソースのみを受け取り、他審判の出力は一切渡らない（三権独立）。
    返り値は MELCHIOR / BALTHASAR / CASPER の3判定（順序固定）。
    """
    return [
        melchior(ticker, fundamentals),
        balthasar(ticker, technicals),
        casper(ticker, news=news, disclosure=disclosure),
    ]
