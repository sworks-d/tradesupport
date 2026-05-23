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
    """業績審判：fundamentals の実数値だけで、成長・収益性・健全性・CFを多面評価する（P1-5）。

    数値はすべてコード取得値（R1）。欠損した指標は評価から外す（R4：推測で埋めない）。
    総合スコアは出さず、良い兆候(pos)と警戒材料(red)を集約して buy/hold/warn/na を決める。
    財務は保守的に：赤字・減収減益・過剰レバレッジ・流動性不足など赤が1つでもあれば warn 寄り。
    """
    data = getattr(fundamentals, "data", {}) or {}
    refs = _refs_to_json(getattr(fundamentals, "source_refs", []))
    asof = getattr(fundamentals, "data_asof", None)

    def pct(x: float) -> str:
        return f"{x * 100:.0f}%"

    parts: list[str] = []  # 値つき所見（reason 用）
    pos: list[str] = []  # 良い兆候
    red: list[str] = []  # 警戒材料（赤）
    seen = 0  # 評価に使えた指標数（=確信度の土台）

    rg = data.get("revenue_growth")
    if rg is not None:
        seen += 1
        parts.append(f"増収率{pct(rg)}")
        if rg >= 0.10:
            pos.append("増収")
        elif rg < 0:
            red.append("減収")

    eg = data.get("earnings_growth")
    if eg is not None:
        seen += 1
        parts.append(f"純益成長{pct(eg)}")
        if eg >= 0.10:
            pos.append("増益")
        elif eg < 0:
            red.append("減益")

    om = data.get("operating_margin")
    if om is not None:
        seen += 1
        parts.append(f"営業利益率{pct(om)}")
        if om >= 0.10:
            pos.append("営業利益率良好")
        elif om < 0:
            red.append("営業赤字")

    pm = data.get("profit_margin")
    if pm is not None:
        seen += 1
        parts.append(f"純利益率{pct(pm)}")
        if pm >= 0.10:
            pos.append("高純利益率")
        elif pm < 0:
            red.append("最終赤字")

    roe = data.get("roe")
    if roe is not None:
        seen += 1
        parts.append(f"ROE{pct(roe)}")
        if roe >= 0.15:
            pos.append("高ROE")
        elif roe < 0:
            red.append("ROEマイナス")

    de = data.get("debt_to_equity")
    if de is not None:
        seen += 1
        ratio = de / 100 if de > 5 else de  # yfinance は % 表記が多い → 比率へ
        parts.append(f"D/E{ratio:.1f}")
        if ratio > 2.0:
            red.append("高レバレッジ")

    cr = data.get("current_ratio")
    if cr is not None:
        seen += 1
        parts.append(f"流動比率{cr:.1f}")
        if cr < 1.0:
            red.append("流動性不足")
        elif cr >= 1.5:
            pos.append("流動性良好")

    fcf = data.get("free_cashflow")
    if fcf is not None:
        seen += 1
        if fcf < 0:
            red.append("FCFマイナス")
        else:
            pos.append("FCF黒字")

    if seen == 0:
        return JudgeVerdict(
            ticker=ticker,
            judge="MELCHIOR",
            verdict="na",
            confidence="na",
            reason="財務数値が取得できず判定不能（データ欠損）。推測で埋めない。",
            source_refs=refs,
            data_asof=asof,
        )

    growth_ok = (rg is not None and rg >= 0.10) or (eg is not None and eg >= 0.10)
    profit_ok = (
        (om is not None and om >= 0.10)
        or (pm is not None and pm >= 0.10)
        or (roe is not None and roe >= 0.15)
    )

    if red:
        verdict = "warn"
        conf = "高" if (len(red) >= 2 or seen >= 4) else "中"
    elif growth_ok and profit_ok:
        verdict = "buy"
        conf = "高"
    elif pos:
        verdict = "hold"
        conf = "中" if seen >= 3 else "低"
    else:
        verdict = "hold"
        conf = "低"

    reason = "・".join(parts) + "。"
    if red:
        reason += f"警戒：{'／'.join(red)}。"
    elif verdict == "buy":
        reason += "成長と収益性がともに良好。"

    return JudgeVerdict(
        ticker=ticker,
        judge="MELCHIOR",
        verdict=verdict,
        confidence=conf,
        reason=reason,
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
_NEG = (
    "下方修正", "減益", "赤字", "訴訟", "不正", "遅延", "リコール",
    "delay", "lawsuit", "recall",
)
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
