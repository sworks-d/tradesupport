"""ニュースヘッドラインの投資インパクト分類辞書（決定論・ゼロコスト）。

投資文脈で意味が確定している語彙のみ列挙。曖昧なものは含めず、
キーワード照合で 60-70% の即時分類を担当し、残り 30-40% は Haiku に渡す。

設計方針:
  - IR / 適時開示語彙の定型表現を優先（日本株は IR テンプレが効く）
  - 1 ヒット 1 ポイント、複数カテゴリ重なりは合算
  - "+"（positive）/ "-"（negative）/ "0"（neutral）の 3 値分類

import:
  from trading_agent.wille.news_keywords import classify_headline
"""

from __future__ import annotations

from typing import Literal

ImpactLabel = Literal["+", "-", "0"]


# ============================================================
# Positive（買い材料）
# ============================================================
POSITIVE_KEYWORDS: dict[str, list[str]] = {
    "業績": [
        "上方修正", "業績拡大", "増益", "増収", "最高益", "黒字転換",
        "好調", "好業績", "業績堅調", "営業利益増", "純利益増",
    ],
    "資本": [
        "自社株買い", "増配", "株式分割", "特別配当", "復配",
    ],
    "材料": [
        "新製品発表", "受注獲得", "業務提携", "新規開拓", "海外進出",
        "新工場", "新薬承認", "FDA承認", "特許取得", "大型契約",
    ],
    "格付": [
        "格上げ", "目標株価引き上げ", "強気判断", "OUTPERFORM",
    ],
    "M&A": [
        "TOB成立", "買収成功",
    ],
}

# ============================================================
# Negative（売り材料）
# ============================================================
NEGATIVE_KEYWORDS: dict[str, list[str]] = {
    "業績": [
        "下方修正", "業績悪化", "減益", "減収", "赤字転落",
        "不振", "営業利益減", "純利益減", "売上減",
    ],
    "資本": [
        "減配", "公募増資", "希薄化", "無配転落",
    ],
    "事故": [
        "リコール", "不祥事", "粉飾", "不正会計", "行政処分",
        "業務停止命令", "課徴金", "脱税", "情報漏えい",
    ],
    "格付": [
        "格下げ", "目標株価引き下げ", "弱気判断", "UNDERPERFORM",
    ],
    "経営": [
        "経営陣交代", "社長辞任", "辞職", "解任", "粉飾発覚",
    ],
}


# ============================================================
# 分類関数
# ============================================================


def _flatten(d: dict[str, list[str]]) -> list[tuple[str, str]]:
    """辞書を (category, keyword) のリストに展開。"""
    return [(cat, kw) for cat, kws in d.items() for kw in kws]


_FLAT_POS = _flatten(POSITIVE_KEYWORDS)
_FLAT_NEG = _flatten(NEGATIVE_KEYWORDS)


def classify_headline(headline: str) -> dict[str, object]:
    """ヘッドライン 1 件を分類。

    Returns:
        {
            "impact": "+/-/0",
            "category": "業績/材料/事故/格付/...",
            "confidence": 0.3-0.9,
            "matched_keywords": [...],
            "needs_llm": bool   # 辞書で確定しなかった → Haiku に渡すべき
        }
    """
    if not headline:
        return {"impact": "0", "category": "", "confidence": 0.0, "matched_keywords": [], "needs_llm": False}

    pos_hits: list[tuple[str, str]] = []
    neg_hits: list[tuple[str, str]] = []

    for cat, kw in _FLAT_POS:
        if kw in headline:
            pos_hits.append((cat, kw))
    for cat, kw in _FLAT_NEG:
        if kw in headline:
            neg_hits.append((cat, kw))

    if not pos_hits and not neg_hits:
        # 辞書ヒットなし → Haiku に渡すべき
        return {
            "impact": "0",
            "category": "",
            "confidence": 0.3,
            "matched_keywords": [],
            "needs_llm": True,
        }

    pos_count = len(pos_hits)
    neg_count = len(neg_hits)

    if pos_count > neg_count:
        primary_cat = pos_hits[0][0]
        conf = min(0.9, 0.55 + 0.1 * pos_count)
        return {
            "impact": "+",
            "category": primary_cat,
            "confidence": conf,
            "matched_keywords": [kw for _, kw in pos_hits],
            "needs_llm": False,
        }
    if neg_count > pos_count:
        primary_cat = neg_hits[0][0]
        conf = min(0.9, 0.55 + 0.1 * neg_count)
        return {
            "impact": "-",
            "category": primary_cat,
            "confidence": conf,
            "matched_keywords": [kw for _, kw in neg_hits],
            "needs_llm": False,
        }

    # 同点 → 曖昧（LLM に判定を委ねる候補）
    return {
        "impact": "0",
        "category": "両義",
        "confidence": 0.4,
        "matched_keywords": [kw for _, kw in (pos_hits + neg_hits)],
        "needs_llm": True,
    }
