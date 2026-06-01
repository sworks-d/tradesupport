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


# v2.4 TASK-M7: 業種別閾値テーブル（参考値・実証データで校正予定）
# 各業種で「典型的に高い指標」が違うため、一律閾値は false positive/negative を量産する。
# - 半導体: 高 OM (20%+) が普通 → 10% では基準甘い
# - 不動産: 低 OM だが高 D/E が当たり前 → D/E 2.0 で warn は厳しすぎる
# - 銀行: D/E は機能しない（業種除外で対応・Z7）
# - 小売: OM 5% で十分（低マージン業界）
# - サービス業: 高 ROE 期待値（インフラ少）
_SECTOR_THRESHOLDS: dict[str, dict[str, float]] = {
    "Technology": {  # 半導体・IT
        "revenue_growth_strong": 0.15,
        "earnings_growth_strong": 0.15,
        "operating_margin_strong": 0.20,
        "profit_margin_strong": 0.15,
        "roe_strong": 0.20,
        "de_warn": 1.5,
    },
    "Real Estate": {  # 不動産
        "revenue_growth_strong": 0.05,
        "earnings_growth_strong": 0.05,
        "operating_margin_strong": 0.30,
        "profit_margin_strong": 0.10,
        "roe_strong": 0.08,
        "de_warn": 3.0,  # 業界的に高 D/E が標準
    },
    "Consumer Defensive": {  # 食品・日用品
        "revenue_growth_strong": 0.05,
        "earnings_growth_strong": 0.05,
        "operating_margin_strong": 0.08,
        "profit_margin_strong": 0.05,
        "roe_strong": 0.10,
        "de_warn": 2.0,
    },
    "Consumer Cyclical": {  # 小売・自動車
        "revenue_growth_strong": 0.08,
        "earnings_growth_strong": 0.08,
        "operating_margin_strong": 0.05,
        "profit_margin_strong": 0.05,
        "roe_strong": 0.12,
        "de_warn": 2.0,
    },
    "Healthcare": {  # 医療
        "revenue_growth_strong": 0.10,
        "earnings_growth_strong": 0.10,
        "operating_margin_strong": 0.15,
        "profit_margin_strong": 0.10,
        "roe_strong": 0.15,
        "de_warn": 1.8,
    },
    # フォールバック既定
    "_default": {
        "revenue_growth_strong": 0.10,
        "earnings_growth_strong": 0.10,
        "operating_margin_strong": 0.10,
        "profit_margin_strong": 0.10,
        "roe_strong": 0.15,
        "de_warn": 2.0,
    },
}


def _sector_thresholds(sector: str | None) -> dict[str, float]:
    """sector に応じた閾値テーブルを取得。未知の sector は default。"""
    if sector and sector in _SECTOR_THRESHOLDS:
        return _SECTOR_THRESHOLDS[sector]
    return _SECTOR_THRESHOLDS["_default"]


def melchior(ticker: str, fundamentals: Any, *, sector: str | None = None) -> JudgeVerdict:
    """業績審判：fundamentals の実数値だけで、成長・収益性・健全性・CFを多面評価する（P1-5）。

    数値はすべてコード取得値（R1）。欠損した指標は評価から外す（R4：推測で埋めない）。
    総合スコアは出さず、良い兆候(pos)と警戒材料(red)を集約して buy/hold/warn/na を決める。
    財務は保守的に：赤字・減収減益・過剰レバレッジ・流動性不足など赤が1つでもあれば warn 寄り。

    v2.4 TASK-M7: sector を渡すと業種別閾値が適用される（半導体は OM 20% / 不動産は D/E 3.0 等）。
    sector=None なら _default（旧来の一律閾値）。
    """
    data = getattr(fundamentals, "data", {}) or {}
    refs = _refs_to_json(getattr(fundamentals, "source_refs", []))
    asof = getattr(fundamentals, "data_asof", None)
    th = _sector_thresholds(sector)

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
        if rg >= th["revenue_growth_strong"]:
            pos.append("増収")
        elif rg < 0:
            red.append("減収")

    eg = data.get("earnings_growth")
    if eg is not None:
        seen += 1
        parts.append(f"純益成長{pct(eg)}")
        if eg >= th["earnings_growth_strong"]:
            pos.append("増益")
        elif eg < 0:
            red.append("減益")

    om = data.get("operating_margin")
    if om is not None:
        seen += 1
        parts.append(f"営業利益率{pct(om)}")
        if om >= th["operating_margin_strong"]:
            pos.append("営業利益率良好")
        elif om < 0:
            red.append("営業赤字")

    pm = data.get("profit_margin")
    if pm is not None:
        seen += 1
        parts.append(f"純利益率{pct(pm)}")
        if pm >= th["profit_margin_strong"]:
            pos.append("高純利益率")
        elif pm < 0:
            red.append("最終赤字")

    roe = data.get("roe")
    if roe is not None:
        seen += 1
        parts.append(f"ROE{pct(roe)}")
        if roe >= th["roe_strong"]:
            pos.append("高ROE")
        elif roe < 0:
            red.append("ROEマイナス")

    de = data.get("debt_to_equity")
    if de is not None:
        seen += 1
        # v2.2 TASK-M2: yfinance の D/E は 0-1 比率 or % 表記が混在する。
        # 単位判定の境界（>5）を明示し、境界値（4.x-5.x）の銘柄は不確定として na 扱い。
        # 比率 5 = 500% を一般企業の上限と仮定。これを超える場合は % 表記と判定。
        if 3.0 < de < 10.0:
            # 不確定域：% なら 300-1000%（多くは高レバレッジ）、比率なら 3-10（極端な高レバ）
            # どちらでも「高レバレッジ」扱いになるが UI には「単位不確定」を明示
            parts.append(f"D/E{de:.1f}（単位不確定）")
            red.append("高レバレッジ（単位不確定）")
        else:
            ratio = de / 100 if de >= 10 else de  # 10 以上は % 表記と確定
            parts.append(f"D/E{ratio:.1f}")
            if ratio > th["de_warn"]:  # v2.4 TASK-M7: 業種別閾値
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

    # v2.1 TASK-M1: seen<3 はデータ不足として「強気側」判定を出さない
    # 例外: 決定的赤フラグ (red≥1) があれば warn を出す（規律＝守り側は維持）
    if seen < 3 and not red:
        return JudgeVerdict(
            ticker=ticker,
            judge="MELCHIOR",
            verdict="hold",
            confidence="低",
            reason=(
                f"指標 {seen}/9 のみ取得＝データ不足で判定保留。"
                + ("・".join(parts) + "。" if parts else "")
            ),
            source_refs=refs,
            data_asof=asof,
        )

    growth_ok = (
        (rg is not None and rg >= th["revenue_growth_strong"])
        or (eg is not None and eg >= th["earnings_growth_strong"])
    )
    profit_ok = (
        (om is not None and om >= th["operating_margin_strong"])
        or (pm is not None and pm >= th["profit_margin_strong"])
        or (roe is not None and roe >= th["roe_strong"])
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


def _balthasar_counter(
    verdict: str, data: dict[str, Any], signals: list[str], refs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """BALTHASAR の判定と逆向きの事実を technicals から摘出（B-2）。

    創作はしない（R5）：実シグナル・実RSIに基づく事実のみ。無ければ空（R4）。
    """
    rsi = data.get("rsi")
    claims: list[str] = []
    if verdict == "buy":  # 買い寄りに対する弱気の事実
        if isinstance(rsi, int | float) and rsi >= 70:
            claims.append(f"RSI{rsi:.0f}が過熱圏（70超）で反落リスク")
        if "macd_bearish" in signals:
            claims.append("MACDヒストグラムが弱気（勢いの鈍化＝弱気ダイバージェンスの疑い）")
        if "death_cross" in signals:
            claims.append("デッドクロスが併存（中期トレンドは下向き）")
        if "bollinger_breakout_up" in signals:
            claims.append("ボリンジャー上限突破＝割高で平均回帰リスク")
        if "bollinger_breakout_down" in signals:
            claims.append("ボリンジャー下限割れが併存（買い判断と矛盾）")
    elif verdict in ("warn", "sell"):  # 弱気寄りに対する強気の事実
        if isinstance(rsi, int | float) and rsi <= 30:
            claims.append(f"RSI{rsi:.0f}が売られすぎ圏（30未満）で反発余地")
        if "golden_cross" in signals:
            claims.append("ゴールデンクロスが併存（上昇転換の兆し）")
        if "macd_bullish" in signals:
            claims.append("MACDヒストグラムが強気（勢いは改善）")
        if "bollinger_breakout_up" in signals:
            claims.append("ボリンジャー上限突破（強い上昇圧力）")
    return [{"claim": c, "source_refs": refs} for c in claims]


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

    # v2.2 TASK-M8: confidence をシグナル強度（signal 数 + RSI 強度）から動的算定
    rsi = data.get("rsi")
    rsi_extreme = isinstance(rsi, int | float) and (rsi >= 75 or rsi <= 25)
    bullish_count = sum(s in signals for s in ("golden_cross", "macd_bullish", "bollinger_breakout_up"))
    bearish_count = sum(s in signals for s in ("death_cross", "overbought_rsi", "macd_bearish", "bollinger_breakout_down"))

    def _dynamic_conf(signal_count: int, rsi_strong: bool) -> str:
        score = signal_count + (1 if rsi_strong else 0)
        if score >= 3:
            return "高"
        if score >= 2:
            return "中"
        return "低"

    if bearish and "golden_cross" not in signals:
        verdict = "warn"
        conf = _dynamic_conf(bearish_count, rsi_extreme)
    elif bullish and "overbought_rsi" not in signals:
        verdict = "buy"
        conf = _dynamic_conf(bullish_count, rsi_extreme)
    else:
        verdict, conf = "hold", "低"  # 旧 "中" → "低"（明確シグナル不在を反映）

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
        counter_within_domain=_balthasar_counter(verdict, data, signals, refs),
    )


# 文脈の方向を示唆するキーワード（決定論ヒューリスティック。本格判定はLLMで補完）
# v2.1 TASK-M3: 辞書拡張 + 否定文脈検出
_NEG = (
    # 既存
    "下方修正", "減益", "赤字", "訴訟", "不正", "遅延", "リコール",
    "delay", "lawsuit", "recall",
    # 業績悪化系
    "業績悪化", "業務停止", "事業撤退", "希薄化", "希望退職", "工場閉鎖",
    "減損", "特別損失", "債務超過", "資本減少", "格下げ", "減配", "無配",
    # 信用毀損系
    "監理銘柄", "上場廃止", "粉飾", "課徴金", "不適切な会計", "意見不表明",
)
_POS = (
    # 既存
    "上方修正", "最高益", "増配", "受注", "record", "beat", "surge",
    # 業績好調系
    "黒字転換", "営業益最高", "過去最高", "業績好調", "拡大", "シェア拡大",
    "特需", "新工場", "新製品", "新サービス", "売上倍増", "受注好調",
    # 戦略・資本系
    "自社株買い", "配当増", "格上げ", "戦略提携", "資本業務提携",
    "TOB", "M&A", "新規参入", "黒字回復",
)

# 前後の否定/打ち消し語（こちらが近くにあるとカウントしない）
_NEGATION_WINDOW = 10  # 前後 10 文字以内
_NEGATION_WORDS = ("脱却", "回避", "ない", "無し", "なし", "取り消し", "撤回", "解除")


def _count_with_negation(text: str, words: tuple[str, ...]) -> int:
    """キーワード出現を、近傍の否定語で打ち消した上でカウント（v2.1 TASK-M3）。"""
    import re

    count = 0
    for w in words:
        for m in re.finditer(re.escape(w.lower()), text):
            window_start = max(0, m.start() - _NEGATION_WINDOW)
            window_end = min(len(text), m.end() + _NEGATION_WINDOW)
            window = text[window_start:window_end]
            if not any(n in window for n in _NEGATION_WORDS):
                count += 1
    return count


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
    # v2.1 TASK-M3: 否定文脈を考慮したカウント
    neg = _count_with_negation(text, _NEG)
    pos = _count_with_negation(text, _POS)
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
        verdict_source="keyword",  # v2.2 TASK-M9: キーワードベース判定
    )


def run_judges(
    ticker: str,
    *,
    fundamentals: Any,
    technicals: Any,
    news: Any = None,
    disclosure: Any = None,
    sector: str | None = None,
) -> list[JudgeVerdict]:
    """3審判を独立に走らせる。

    各審判は自分のソースのみを受け取り、他審判の出力は一切渡らない（三権独立）。
    返り値は MELCHIOR / BALTHASAR / CASPER の3判定（順序固定）。

    v2.4 TASK-M7: sector を渡すと MELCHIOR が業種別閾値で判定する。
    """
    return [
        melchior(ticker, fundamentals, sector=sector),
        balthasar(ticker, technicals),
        casper(ticker, news=news, disclosure=disclosure),
    ]
