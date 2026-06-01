"""テーマ強度定量化（v2.10 Phase 4）。

主要 中期テーマの強度を Topics の言及頻度から定量化。
「今、どのテーマが勝負すべきテーマか」を可視化。

主要テーマ（キーワード辞書・LLM 不使用の機械判定）:
  AI / 半導体 / EV-電池 / 防衛 / 円安受益 / インバウンド / DX-SaaS

定量化指標:
  - mentions_7d:  過去 7 日の言及回数
  - mentions_30d: 過去 30 日の言及回数
  - momentum:    7d ペースの 30d 拡張 vs 30d 実績 - 1.0
                 >0 でテーマ加速、<0 で減速
  - intensity:   log10(m30+1) × 40 で 0-100 に正規化

ハルシネーション対策:
  - キーワードマッチのみ（LLM 推論なし・意訳しない）
  - キーワード未ヒットはカウントしない（推測しない）
  - Topic が無い場合は status="insufficient_data"
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.topics import Topic
from trading_agent.utils.logger import get_logger

_log = get_logger("portfolio.theme_strength")

# テーマ別キーワード辞書（v2.10 主要 7 テーマ）
# 注意: キーワードは大文字小文字を区別せず、文字列の "含む" 判定のみ
_THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "AI": ("AI", "生成AI", "機械学習", "ChatGPT", "LLM", "人工知能", "OpenAI", "Claude", "Gemini"),
    "半導体": ("半導体", "セミコンダクタ", "ファウンドリ", "TSMC", "NVIDIA", "AMD", "Intel", "ロジック", "DRAM", "NAND"),
    "EV/電池": ("EV", "電気自動車", "電池", "リチウム", "再エネ", "蓄電", "テスラ", "Tesla", "充電"),
    "防衛": ("防衛", "軍事", "安全保障", "防衛費", "ミサイル", "戦闘機", "防衛省"),
    "円安受益": ("円安", "為替", "輸出", "ドル高", "通貨安", "為替差益"),
    "インバウンド": ("インバウンド", "観光", "訪日", "観光客", "旅行客", "国内旅行"),
    "DX/SaaS": ("DX", "SaaS", "クラウド", "デジタル化", "DX 推進", "サブスク"),
}


def _match_themes(text: str) -> set[str]:
    """テキストにマッチするテーマ集合を返す（大文字小文字無視・複数 OK）。"""
    if not text:
        return set()
    upper = text.upper()
    matched: set[str] = set()
    for theme, keywords in _THEME_KEYWORDS.items():
        for kw in keywords:
            if kw.upper() in upper:
                matched.add(theme)
                break
    return matched


def compute_theme_strength(engine: Engine) -> dict[str, Any]:
    """各テーマの強度を集計する。

    Returns:
        {
            "status": "active" | "no_matches" | "insufficient_data",
            "themes": {
                "AI": {"mentions_7d": int, "mentions_30d": int,
                       "momentum": float|None, "intensity": float},
                ...
            },
            "total_topics_30d": int,
            "top_themes": [テーマ名 ×3],
        }
    """
    today = dt.date.today()
    cutoff_7d_dt = dt.datetime.combine(today - dt.timedelta(days=7), dt.time.min)
    cutoff_30d_dt = dt.datetime.combine(today - dt.timedelta(days=30), dt.time.min)

    with Session(engine) as s:
        topics_30d = list(
            s.exec(
                select(Topic).where(col(Topic.collected_at) >= cutoff_30d_dt)
            ).all()
        )

    if not topics_30d:
        return {
            "status": "insufficient_data",
            "themes": {t: _empty_metrics() for t in _THEME_KEYWORDS},
            "total_topics_30d": 0,
            "top_themes": [],
            "reason": "no_topics_in_30d",
        }

    # テーマ別カウント
    counts: dict[str, dict[str, int]] = {
        t: {"mentions_7d": 0, "mentions_30d": 0} for t in _THEME_KEYWORDS
    }
    for topic in topics_30d:
        text = " ".join(
            x for x in [topic.headline, topic.summary or "", topic.impact_text or ""]
            if x
        )
        matched = _match_themes(text)
        if not matched:
            continue
        is_7d = topic.collected_at >= cutoff_7d_dt
        for theme in matched:
            counts[theme]["mentions_30d"] += 1
            if is_7d:
                counts[theme]["mentions_7d"] += 1

    # 派生指標（momentum / intensity）
    themes_out: dict[str, Any] = {}
    for theme, c in counts.items():
        m30 = c["mentions_30d"]
        m7 = c["mentions_7d"]
        if m30 == 0:
            themes_out[theme] = _empty_metrics()
            continue
        # momentum: 7d ペースを 30d 換算（× 30/7）して 30d 実績と比較
        expected_30d_at_7d_pace = m7 * (30.0 / 7.0)
        momentum = round((expected_30d_at_7d_pace / m30) - 1.0, 3) if m30 else None
        intensity = round(min(100.0, math.log10(m30 + 1) * 40.0), 1)
        themes_out[theme] = {
            "mentions_7d": m7,
            "mentions_30d": m30,
            "momentum": momentum,
            "intensity": intensity,
        }

    active_themes = [
        (t, d) for t, d in themes_out.items() if d["mentions_30d"] > 0
    ]
    top_themes = [
        t
        for t, _ in sorted(
            active_themes,
            key=lambda x: (x[1]["intensity"], x[1]["mentions_7d"]),
            reverse=True,
        )[:3]
    ]

    return {
        "status": "active" if active_themes else "no_matches",
        "themes": themes_out,
        "total_topics_30d": len(topics_30d),
        "top_themes": top_themes,
    }


def _empty_metrics() -> dict[str, Any]:
    return {
        "mentions_7d": 0,
        "mentions_30d": 0,
        "momentum": None,
        "intensity": 0.0,
    }
