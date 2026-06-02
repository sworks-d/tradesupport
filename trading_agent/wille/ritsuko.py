"""🧪 RITSUKO（赤木リツコ）— WILLE 組織内の調査・分析担当（v2.8 再設計）。

責務（再定義）:
  - **TickerBrief 生成**: 候補銘柄ごとに調査ブリーフを作る
    技術指標（MAGI 借用）/ ニュース / 業界トレンド / ピア比較 / イベント
  - **5 種類の中立スコア出力**:
    news_sentiment_score / industry_score / peer_score / event_score / deep_brief_score
  - **重み付けはしない**（戦略は MISATO 側の領分）
  - **市場 regime 判定**: 全体地合いの追い風 / 向かい風

責任分界:
  - RITSUKO = 客観的事実と中立スコアの提供（調査機関）
  - MISATO  = 戦略パラメータで重み付けして優先度を決定（調停機関）

旧設計との違い:
  - 旧: 銘柄状況 → 1 機指名のハードコード表（DS と重複）
  - 新: 銘柄ごとの「投資判断材料集」を提供し、機指名は MISATO に任せる
  - 旧: yfinance を二重叩きして RSI 再計算
  - 新: MAGI Technicals テーブルから読むだけ
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy.engine import Engine

from trading_agent.utils.logger import get_logger

_log = get_logger("wille.ritsuko")

# 銘柄状況の分類（technicals.situation として参照）
Situation = Literal["bullish", "bearish", "pullback", "neutral", "breakout", "unknown"]
# 市場 regime
MarketRegime = Literal["risk_on", "risk_off", "neutral", "unknown"]


# ============================================================
# データクラス
# ============================================================


@dataclass(frozen=True)
class SituationReport:
    """1 銘柄の状況判定（後方互換・MAGI Technicals から借用）。

    旧 RITSUKO の出力。新 TickerBrief.technicals.situation と同じ意味。
    """

    ticker: str
    situation: Situation
    confidence: float
    signals: list[str] = field(default_factory=list)
    recommended_pilots: list[str] = field(default_factory=list)
    reasoning: str = ""


@dataclass(frozen=True)
class MarketContext:
    """市場全体の地合い。"""

    regime: MarketRegime = "unknown"
    breadth_pct: float | None = None
    index_trend: str = "unknown"
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NewsItem:
    """ニュースヘッドライン 1 件。"""

    date: str
    headline: str
    impact: Literal["+", "-", "0"]
    category: str = ""
    confidence: float = 0.5
    source: str = ""


@dataclass(frozen=True)
class IndustryReport:
    """業界トレンド。"""

    sector: str = "unknown"
    score: float = 0.0  # -1.0 ~ +1.0
    tailwinds: list[str] = field(default_factory=list)
    headwinds: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PeerComparison:
    """ピア比較（同業内）。"""

    sector_rank: str = "—/—"
    relative_strength_30d: float = 0.0  # %
    peer_per: float | None = None
    self_per: float | None = None


@dataclass(frozen=True)
class UpcomingEvent:
    """直近イベント（決算 / 配当落ち 等）。"""

    date: str
    type: str
    note: str = ""


@dataclass(frozen=True)
class TechnicalSnapshot:
    """MAGI Technicals からの借用。"""

    rsi: float | None = None
    macd_signal: str | None = None
    trend: str | None = None
    situation: Situation = "unknown"


@dataclass(frozen=True)
class SonnetBrief:
    """Sonnet スポット投入結果（Phase C で実装）。"""

    recommendation_score: float = 0.0  # -1.0 ~ +1.0
    narrative: str = ""
    triggered_by: str = ""


@dataclass(frozen=True)
class TickerBrief:
    """RITSUKO の最終出力：銘柄ブリーフ。

    MISATO はこのオブジェクト全体を受け取り、`*_score` 5 つを戦略パラメータで
    重み付けして優先度を計算する。生データ（news/industry/peer/events）は
    例外判定・UI 表示・後の校正で使う。

    data_quality（B1 追加）: 各カテゴリの取得状況を明示
      - "measured":      実データを取得して算出（信頼性高）
      - "fallback":      仮値・デフォルトでの近似（信頼性中）
      - "unavailable":   データ取得失敗・0 を意味として持たない
      - "not_implemented": 機能未配線（industry など）
    """

    ticker: str

    # === 5 つの中立スコア（MISATO が重み付けして boost 化）===
    news_sentiment_score: float = 0.0  # -1.0 ~ +1.0
    industry_score: float = 0.0  # -1.0 ~ +1.0
    peer_score: float = 0.0  # -0.5 ~ +0.5
    event_score: float = 0.0  # -0.2 ~ 0
    deep_brief_score: float = 0.0  # -1.0 ~ +1.0

    # === 生データ ===
    current_price: float | None = None  # 直近終値（yfinance 取得時のスナップショット）
    news: list[NewsItem] = field(default_factory=list)
    industry: IndustryReport = field(default_factory=IndustryReport)
    peer: PeerComparison = field(default_factory=PeerComparison)
    upcoming_events: list[UpcomingEvent] = field(default_factory=list)
    technicals: TechnicalSnapshot = field(default_factory=TechnicalSnapshot)
    deep_brief: SonnetBrief | None = None

    # === データ品質メタ（B1: 情報透明性）===
    data_quality: dict[str, str] = field(default_factory=dict)


# ============================================================
# 後方互換: SituationReport 生成（MAGI Technicals から読むだけ）
# ============================================================


# 銘柄状況に向いている DS の親和性表（後方互換・MISATO 旧ロジックで参照）
SITUATION_PILOT_AFFINITY: dict[Situation, list[str]] = {
    "bullish": ["ASUKA", "KAWORU"],
    "breakout": ["ASUKA", "KAWORU"],
    "pullback": ["SHINJI", "REI"],
    "bearish": ["REI"],
    "neutral": ["SHINJI"],
    "unknown": [],
}


def assess_ticker_situation(
    ticker: str,
    *,
    technicals: Any | None = None,
    rsi: float | None = None,
    macd_signal: str | None = None,
    trend: str | None = None,
    price_vs_ma50: float | None = None,
    drawdown_from_high: float | None = None,
) -> SituationReport:
    """1 銘柄の状況を判定（決定論ロジック）。後方互換。

    新設計では _classify_situation を直接呼べばよいが、既存テスト互換のため残す。
    """
    # technicals オブジェクトから自動抽出
    if technicals is not None:
        data = getattr(technicals, "data", {}) or {}
        signals = getattr(technicals, "signals", []) or []
        if rsi is None:
            rsi = data.get("rsi")
        if macd_signal is None:
            if "macd_bullish" in signals:
                macd_signal = "bullish"
            elif "macd_bearish" in signals:
                macd_signal = "bearish"
        if trend is None:
            if "golden_cross" in signals:
                trend = "up"
            elif "death_cross" in signals:
                trend = "down"

    situation, conf, reasoning, signals_list = _classify_situation(
        rsi=rsi,
        macd_signal=macd_signal,
        trend=trend,
        price_vs_ma50=price_vs_ma50,
        drawdown_from_high=drawdown_from_high,
    )

    return SituationReport(
        ticker=ticker,
        situation=situation,
        confidence=conf,
        signals=signals_list,
        recommended_pilots=SITUATION_PILOT_AFFINITY.get(situation, []),
        reasoning=reasoning,
    )


def _classify_situation(
    *,
    rsi: float | None,
    macd_signal: str | None,
    trend: str | None,
    price_vs_ma50: float | None = None,
    drawdown_from_high: float | None = None,
) -> tuple[Situation, float, str, list[str]]:
    """技術指標から状況を分類（決定論）。"""
    if rsi is None and macd_signal is None and trend is None:
        return "unknown", 0.0, "技術指標データが取得できず判定不能", []

    signals_list: list[str] = []
    if rsi is not None:
        signals_list.append(f"RSI={rsi:.0f}")
    if macd_signal is not None:
        signals_list.append(f"MACD={macd_signal}")
    if trend is not None:
        signals_list.append(f"trend={trend}")
    if drawdown_from_high is not None:
        signals_list.append(f"DD from high={drawdown_from_high:+.1f}%")

    is_uptrend = trend == "up" or (price_vs_ma50 is not None and price_vs_ma50 > 0)
    is_downtrend = trend == "down" or (price_vs_ma50 is not None and price_vs_ma50 < -5)

    if rsi is not None and rsi > 70 and is_uptrend:
        return "breakout", 0.75, f"RSI {rsi:.0f}（過熱圏）+ 上昇トレンド = ブレイク追従可", signals_list
    if rsi is not None and rsi < 30 and is_downtrend:
        return "bearish", 0.70, f"RSI {rsi:.0f}（売られすぎ）+ 下降トレンド = 弱気", signals_list
    if (
        is_uptrend
        and rsi is not None
        and 30 < rsi < 50
        and (drawdown_from_high is None or drawdown_from_high < -3)
    ):
        return "pullback", 0.70, f"上昇トレンド + RSI {rsi:.0f}（押し目圏） = 押し目買い候補", signals_list
    if rsi is not None and rsi >= 55 and macd_signal == "bullish":
        return "bullish", 0.70, f"RSI {rsi:.0f} + MACD強気 = 強気フェーズ", signals_list
    return "neutral", 0.4, "明確な方向性なし（中立）", signals_list


# ============================================================
# 市場 regime 判定
# ============================================================


def assess_market_context(
    *,
    breadth_pct: float | None = None,
    nikkei_trend: str | None = None,
    advance_decline_ratio: float | None = None,
) -> MarketContext:
    """市場全体の地合いを判定（決定論）。"""
    notes: list[str] = []
    if breadth_pct is None and nikkei_trend is None and advance_decline_ratio is None:
        return MarketContext(regime="unknown", notes=["市場データなし"])

    score = 0
    if breadth_pct is not None:
        notes.append(f"市場 breadth: {breadth_pct:.0f}%")
        if breadth_pct >= 60:
            score += 1
        elif breadth_pct <= 40:
            score -= 1

    if nikkei_trend == "up":
        score += 1
        notes.append("日経: 上昇")
    elif nikkei_trend == "down":
        score -= 1
        notes.append("日経: 下落")

    if advance_decline_ratio is not None:
        notes.append(f"騰落レシオ: {advance_decline_ratio:.2f}")
        if advance_decline_ratio > 1.2:
            score += 1
        elif advance_decline_ratio < 0.8:
            score -= 1

    regime: MarketRegime
    if score >= 2:
        regime = "risk_on"
    elif score <= -2:
        regime = "risk_off"
    else:
        regime = "neutral"

    return MarketContext(
        regime=regime,
        breadth_pct=breadth_pct,
        index_trend=nikkei_trend or "unknown",
        notes=notes,
    )


# ============================================================
# TickerBrief 生成（新メイン）
# ============================================================


def build_ticker_brief(
    ticker: str,
    *,
    technicals_data: dict[str, Any] | None = None,
    news_items: list[NewsItem] | None = None,
    industry: IndustryReport | None = None,
    peer: PeerComparison | None = None,
    upcoming_events: list[UpcomingEvent] | None = None,
    deep_brief: SonnetBrief | None = None,
    news_sentiment_llm: float | None = None,
) -> TickerBrief:
    """1 銘柄の TickerBrief を組み立てる（5 中立スコアを算出）。

    各入力は事前に集めたデータ。データがない場合は 0 / 空で進める。
    """
    # === Technicals: MAGI Technicals から借用 ===
    current_price: float | None = None
    if technicals_data:
        rsi = technicals_data.get("rsi")
        macd = technicals_data.get("macd_signal")
        trend = technicals_data.get("trend")
        current_price = technicals_data.get("current_price")
        situation, _conf, _reasoning, _signals = _classify_situation(
            rsi=rsi,
            macd_signal=macd,
            trend=trend,
            price_vs_ma50=technicals_data.get("price_vs_ma50"),
            drawdown_from_high=technicals_data.get("drawdown_from_high"),
        )
        tech_snap = TechnicalSnapshot(rsi=rsi, macd_signal=macd, trend=trend, situation=situation)
    else:
        tech_snap = TechnicalSnapshot()

    # === ニュースセンチメントスコア集計 ===
    # PIPELINE v3 Phase 3 M3.1: news_sentiment_llm が渡されたら decision + LLM ブレンド
    news_list = news_items or []
    news_sentiment_score = _calc_news_sentiment_score(
        news_list, sentiment_llm=news_sentiment_llm
    )

    # === 業界スコア（IndustryReport.score をそのまま）===
    industry_obj = industry or IndustryReport()
    industry_score = industry_obj.score

    # === ピアスコア（相対力を ±0.5 にクリップ）===
    peer_obj = peer or PeerComparison()
    peer_score = max(-0.5, min(0.5, peer_obj.relative_strength_30d / 30.0))

    # === イベントスコア（決算 7 日以内なら -0.2）===
    events_list = upcoming_events or []
    event_score = _calc_event_score(events_list)

    # === Sonnet 深掘り（あれば）===
    deep_score = deep_brief.recommendation_score if deep_brief else 0.0

    # B1: 各カテゴリのデータ品質を判定（情報透明性）
    data_quality: dict[str, str] = {}
    # technicals: rsi が取れていれば measured、None なら unavailable
    data_quality["technicals"] = "measured" if tech_snap.rsi is not None else "unavailable"
    # news: 1 件以上取れていれば measured（中身が中立 0 でも）、ゼロなら unavailable
    data_quality["news"] = "measured" if news_list else "unavailable"
    # industry: report が空オブジェクトのままなら not_implemented、内容が入っていれば measured
    if industry is not None and (industry_obj.tailwinds or industry_obj.headwinds or industry_obj.score != 0.0 or industry_obj.sector not in (None, "", "unknown")):
        data_quality["industry"] = "measured"
    else:
        data_quality["industry"] = "not_implemented"
    # peer: sector_rank が "—/—" なら unavailable、数字なら measured
    rank = peer_obj.sector_rank or "—/—"
    if "/" in rank and rank.split("/")[0].strip().isdigit():
        data_quality["peer"] = "measured"
    else:
        data_quality["peer"] = "unavailable"
    # events: 1 件以上取れていれば measured（=決算予定がない or 取得失敗で 0 を区別できないため）
    data_quality["events"] = "measured" if events_list else "unavailable"
    # deep_brief: Sonnet 投入があれば measured、なければ unavailable
    data_quality["deep_brief"] = "measured" if deep_brief is not None else "unavailable"

    return TickerBrief(
        ticker=ticker,
        news_sentiment_score=news_sentiment_score,
        industry_score=industry_score,
        peer_score=peer_score,
        event_score=event_score,
        deep_brief_score=deep_score,
        current_price=current_price,
        news=news_list,
        industry=industry_obj,
        peer=peer_obj,
        upcoming_events=events_list,
        technicals=tech_snap,
        deep_brief=deep_brief,
        data_quality=data_quality,
    )


def _calc_news_sentiment_score(
    news: list[NewsItem], *, sentiment_llm: float | None = None
) -> float:
    """ニュースの impact を集計して -1.0 ~ +1.0 に正規化。

    PIPELINE v3 Phase 3 M3.1: C3 統合点。
    `sentiment_llm` (LLM 由来の -1.0〜+1.0 スコア) が渡されたら、
    decision 70% + LLM 30% でブレンド（LLM 失敗時の安全側保険）。
    """
    if not news:
        return 0.0 if sentiment_llm is None else sentiment_llm
    pos = sum(1 for n in news if n.impact == "+")
    neg = sum(1 for n in news if n.impact == "-")
    total = len(news)
    decision_score = (pos - neg) / total
    if sentiment_llm is None:
        return decision_score
    # ブレンド: decision 70% + LLM 30%（LLM の信頼性は高いが、フォールバックを保険として残す）
    return decision_score * 0.7 + sentiment_llm * 0.3


def _calc_news_sentiment_llm(
    engine: Engine | None,
    ticker: str,
    news_items: list[NewsItem],
) -> float | None:
    """C3 news_sentiment を呼び出し、-1.0 ~ +1.0 を返す。

    engine=None / 失敗時 / 重要キーワード無し時は None を返す（呼出側は decision のみ使用）。
    """
    if engine is None or not news_items:
        return None
    try:
        from trading_agent.llm.news_sentiment import NewsItem as _NSItem
        from trading_agent.llm.news_sentiment import analyze_news

        ns_items = [
            _NSItem(title=n.headline or "", body="", url=n.source or "")
            for n in news_items
            if (n.headline or "").strip()
        ]
        if not ns_items:
            return None
        result = analyze_news(engine, ticker, ns_items)
        # SentimentScore.score (0-100) → -1.0 ~ +1.0
        return (result.score - 50.0) / 50.0
    except Exception as exc:
        _log.warning(
            "ritsuko_news_sentiment_llm_failed",
            ticker=ticker,
            error_type=type(exc).__name__,
        )
        return None


def _calc_event_score(events: list[UpcomingEvent], today: date | None = None) -> float:
    """直近イベントから event_score を計算。

    決算 7 日以内なら -0.2（不確実性が高い → 一時的に下げる）。
    """
    if not events:
        return 0.0
    base = today or date.today()
    cutoff = base + timedelta(days=7)
    for ev in events:
        if ev.type != "earnings":
            continue
        try:
            ev_date = date.fromisoformat(ev.date[:10])
        except (ValueError, TypeError):
            continue
        if base <= ev_date <= cutoff:
            return -0.2
    return 0.0


# ============================================================
# 候補プール全体に Brief を付与
# ============================================================


def classify_market_cycle(closes: list[float]) -> str:
    """TOPIX 終値列から相場局面を分類（A8・純粋関数）。

    単日 risk_on/off（detect_market_regime_live）と違い、**trailing な局面**（強気/弱気）を返す。
    ゲート⑥「両局面通過」の判定に使う（単日スナップショットの弱さを解消）。

    判定（古い→新しい順の closes）:
      - bull    : 直近終値 ≥ 200日MA かつ 直近高値からの DD < 10%
      - bear    : 直近終値 < 200日MA、または 直近高値からの DD ≥ 15%
      - sideways: 上記以外（横ばい・どちらの局面でもない）
      - unknown : サンプル不足（< 60 本）
    """
    if len(closes) < 60:
        return "unknown"
    latest = closes[-1]
    ma200 = sum(closes[-200:]) / len(closes[-200:]) if len(closes) >= 200 else sum(closes) / len(closes)
    recent_high = max(closes[-120:])
    dd = (recent_high - latest) / recent_high if recent_high > 0 else 0.0
    if latest >= ma200 and dd < 0.10:
        return "bull"
    if latest < ma200 or dd >= 0.15:
        return "bear"
    return "sideways"


def detect_market_cycle(benchmark_ticker: str = "1306.T") -> dict[str, Any]:
    """TOPIX ETF の日次終値から trailing 相場局面を判定（A8）。

    Returns: {"cycle": "bull"/"bear"/"sideways"/"unknown", "close": float|None, "ma200": float|None}
    ネット失敗時は cycle="unknown"（推測しない）。
    """
    try:
        import yfinance as yf

        df = yf.Ticker(benchmark_ticker).history(period="2y", interval="1d", auto_adjust=True)
        closes = [float(x) for x in df["Close"].dropna().tolist()]
    except Exception as exc:
        _log.warning("market_cycle_fetch_failed", error_type=type(exc).__name__)
        return {"cycle": "unknown", "close": None, "ma200": None}
    cycle = classify_market_cycle(closes)
    ma200 = (
        sum(closes[-200:]) / len(closes[-200:])
        if len(closes) >= 200 else (sum(closes) / len(closes) if closes else None)
    )
    return {"cycle": cycle, "close": closes[-1] if closes else None, "ma200": ma200}


def fetch_technicals_lite(
    engine: Engine | None, tickers: list[str]
) -> dict[str, dict[str, Any]]:
    """軽量 technicals（RSI 等）のみ取得（A+: KAWORU 選定用）。

    news / peer / industry / LLM を呼ばず、MAGI Technicals → yfinance bulk フォールバックで
    RSI 等の技術指標だけを返す。選定者(DS)が AKAGI の重い検証を待たずに軽量信号で選ぶための入口。
    """
    out: dict[str, dict[str, Any]] = {}
    if engine is not None:
        try:
            out = _load_technicals_from_magi(engine, tickers)
        except Exception as exc:
            _log.warning("ritsuko_lite_magi_failed", error=str(exc))
            out = {}
    missing = [t for t in tickers if t not in out or out[t].get("rsi") is None]
    if missing:
        try:
            out.update(_fetch_technicals_yfinance_bulk(missing))
        except Exception as exc:
            _log.warning("ritsuko_lite_yf_failed", error=str(exc))
    return out


def build_briefs_from_pool(
    candidates: list[Any],
    *,
    engine: Engine | None = None,
    technicals_lookup: dict[str, dict[str, Any]] | None = None,
    news_lookup: dict[str, list[NewsItem]] | None = None,
    industry_lookup: dict[str, IndustryReport] | None = None,
    peer_lookup: dict[str, PeerComparison] | None = None,
    events_lookup: dict[str, list[UpcomingEvent]] | None = None,
) -> dict[str, TickerBrief]:
    """候補プールの全銘柄に Brief を付与（メイン入口）。

    各 lookup 引数は事前収集データの bulk。
    None なら空データで進める（後で Phase B/C で埋める）。
    """
    tickers = []
    for c in candidates:
        t = getattr(c, "ticker", None) or (c.get("ticker") if isinstance(c, dict) else None)
        if t:
            tickers.append(t)

    # technicals: 優先順 (1) overrides → (2) MAGI Technicals → (3) yfinance フォールバック
    if technicals_lookup is None:
        technicals_lookup = {}
        if engine is not None:
            technicals_lookup = _load_technicals_from_magi(engine, tickers)
        # MAGI で取れなかった銘柄は yfinance bulk で補完
        missing = [t for t in tickers if t not in technicals_lookup or technicals_lookup[t].get("rsi") is None]
        if missing:
            yf_data = _fetch_technicals_yfinance_bulk(missing)
            for t, d in yf_data.items():
                technicals_lookup[t] = d

    # ピア比較（同 sector 内 30 日リターン順位）
    if peer_lookup is None and engine is not None:
        try:
            peer_lookup = _compute_peer_scores(tickers, engine)
        except Exception as exc:
            _log.warning("ritsuko_peer_compute_failed", error=str(exc))
            peer_lookup = {}

    # ニュース（yfinance.news + キーワード辞書分類）
    if news_lookup is None:
        try:
            news_lookup = _fetch_news_yfinance(tickers)
        except Exception as exc:
            _log.warning("ritsuko_news_fetch_failed", error=str(exc))
            news_lookup = {}

    # 決算カレンダー
    if events_lookup is None:
        try:
            events_lookup = _fetch_upcoming_events_yfinance(tickers)
        except Exception as exc:
            _log.warning("ritsuko_events_fetch_failed", error=str(exc))
            events_lookup = {}

    news_lookup = news_lookup or {}
    industry_lookup = industry_lookup or {}
    peer_lookup = peer_lookup or {}
    events_lookup = events_lookup or {}

    # D2: industry_score を「候補プールのニュースを sector 別に集約」で計算
    if not industry_lookup and engine is not None:
        try:
            industry_lookup = _compute_industry_reports_from_pool(
                tickers, news_lookup, engine
            )
        except Exception as exc:
            _log.warning("ritsuko_industry_compute_failed", error=str(exc))
            industry_lookup = {}

    out: dict[str, TickerBrief] = {}
    for ticker in tickers:
        news_for_ticker = news_lookup.get(ticker)
        # PIPELINE v3 Phase 3 M3.1: C3 news_sentiment 統合
        # engine がある時のみ LLM 呼出（cost guard / cache / dedup は news_sentiment 内蔵）
        sentiment_llm = _calc_news_sentiment_llm(engine, ticker, news_for_ticker or [])
        out[ticker] = build_ticker_brief(
            ticker,
            technicals_data=technicals_lookup.get(ticker),
            news_items=news_for_ticker,
            industry=industry_lookup.get(ticker),
            peer=peer_lookup.get(ticker),
            upcoming_events=events_lookup.get(ticker),
            news_sentiment_llm=sentiment_llm,
        )
    return out


def _compute_industry_reports_from_pool(
    tickers: list[str],
    news_lookup: dict[str, list[NewsItem]],
    engine: Engine,
) -> dict[str, IndustryReport]:
    """候補プール内の銘柄ニュースを sector 別に集約 → industry_score を算出（D2）。

    実装方針:
      - 候補銘柄の Universe.sector を取得
      - 同 sector の全銘柄のニュースを集約
      - positive/negative 件数から score を算出（-1.0 ~ +1.0）
      - tailwinds / headwinds に代表ヘッドライン 3 件を格納

    各銘柄に「自分の sector の IndustryReport」が割り当てられる。
    sector 不明や news 0 件は IndustryReport を返さない（data_quality=not_implemented のまま）。
    """
    if not tickers:
        return {}

    # 銘柄 → sector を取得
    sector_by_ticker: dict[str, str] = {}
    try:
        from sqlmodel import Session, col, select as _sel
        from trading_agent.models.universe import Universe

        with Session(engine) as s:
            rows = s.exec(_sel(Universe).where(col(Universe.ticker).in_(tickers))).all()
            for r in rows:
                if r.sector:
                    sector_by_ticker[r.ticker] = r.sector
    except Exception as exc:
        _log.warning("ritsuko_industry_sector_lookup_failed", error=str(exc))
        return {}

    if not sector_by_ticker:
        return {}

    # sector ごとにニュース集約
    sector_news: dict[str, list[NewsItem]] = {}
    for ticker in tickers:
        sector = sector_by_ticker.get(ticker)
        if not sector:
            continue
        sector_news.setdefault(sector, []).extend(news_lookup.get(ticker) or [])

    # sector 単位の score を算出
    sector_reports: dict[str, IndustryReport] = {}
    for sector, news_list in sector_news.items():
        if not news_list:
            continue
        pos_items = [n for n in news_list if n.impact == "+"]
        neg_items = [n for n in news_list if n.impact == "-"]
        total = len(news_list)
        score = (len(pos_items) - len(neg_items)) / total if total else 0.0
        sector_reports[sector] = IndustryReport(
            sector=sector,
            score=score,
            tailwinds=[n.headline for n in pos_items][:3],
            headwinds=[n.headline for n in neg_items][:3],
        )

    # 各銘柄に sector_reports を割り当て
    out: dict[str, IndustryReport] = {}
    for ticker in tickers:
        sector = sector_by_ticker.get(ticker)
        if not sector or sector not in sector_reports:
            continue
        out[ticker] = sector_reports[sector]
    return out


def _load_technicals_from_magi(engine: Engine, tickers: list[str]) -> dict[str, dict[str, Any]]:
    """MAGI Technicals テーブルから RSI/MACD/trend を読む。

    存在しない・空のレコードは出力に含めない（後段で yfinance フォールバック）。
    """
    if not tickers:
        return {}
    out: dict[str, dict[str, Any]] = {}
    try:
        from sqlalchemy import text

        with engine.connect() as conn:
            for t in tickers:
                row = conn.execute(
                    text(
                        "SELECT data, signals FROM technicals "
                        "WHERE ticker = :t ORDER BY computed_at DESC LIMIT 1"
                    ),
                    {"t": t},
                ).fetchone()
                if not row:
                    continue
                import json as _json

                data = _json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
                signals_raw = row[1]
                signals = (
                    _json.loads(signals_raw)
                    if isinstance(signals_raw, str)
                    else (signals_raw or [])
                )

                rsi = data.get("rsi")
                macd_signal = None
                if "macd_bullish" in signals:
                    macd_signal = "bullish"
                elif "macd_bearish" in signals:
                    macd_signal = "bearish"
                trend = None
                if "golden_cross" in signals:
                    trend = "up"
                elif "death_cross" in signals:
                    trend = "down"

                out[t] = {
                    "rsi": rsi,
                    "macd_signal": macd_signal,
                    "trend": trend,
                    "price_vs_ma50": data.get("price_vs_ma50"),
                    "drawdown_from_high": data.get("drawdown_from_high"),
                }
    except Exception as exc:
        _log.warning("ritsuko_magi_technicals_read_failed", error=str(exc))
    return out


def _fetch_technicals_yfinance_bulk(tickers: list[str]) -> dict[str, dict[str, Any]]:
    """yfinance で RSI/MACD/trend を一括取得（MAGI Technicals が空の時のフォールバック）。"""
    if not tickers:
        return {}
    out: dict[str, dict[str, Any]] = {}
    try:
        import yfinance as yf

        from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol
    except Exception as exc:
        _log.warning("ritsuko_yf_import_failed", error=str(exc))
        return {}

    sym_map = {to_yfinance_symbol(t): t for t in tickers}
    try:
        df = yf.download(
            list(sym_map.keys()),
            period="3mo",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
        )
    except Exception as exc:
        _log.warning("ritsuko_yf_download_failed", error=str(exc))
        return {}

    if df is None or df.empty:
        return {}

    for sym, ticker in sym_map.items():
        try:
            series = df[sym]["Close"] if len(sym_map) > 1 else df["Close"]
            closes = [float(x) for x in series.dropna().tolist()]
            if len(closes) < 30:
                continue

            rsi_val = _calc_rsi_simple(closes, period=14)  # None になり得る
            ma_short = sum(closes[-14:]) / 14
            ma_long = sum(closes[-50:]) / 50 if len(closes) >= 50 else None
            trend: str | None
            if ma_long is None:
                trend = None  # データ不足は None で明示
            elif ma_short > ma_long:
                trend = "up"
            elif ma_short < ma_long * 0.97:
                trend = "down"
            else:
                trend = "flat"
            # MACD: 26 本以上ないと意味のある signal にならない
            macd_signal: str | None
            if len(closes) >= 26:
                ema_12 = _calc_ema_simple(closes, 12)
                ema_26 = _calc_ema_simple(closes, 26)
                macd_signal = "bullish" if ema_12 > ema_26 else "bearish"
            else:
                macd_signal = None
            high_90d = max(closes[-90:]) if len(closes) >= 90 else max(closes)
            current = closes[-1]
            dd = (current - high_90d) / high_90d * 100 if high_90d > 0 else 0.0
            price_vs_ma50 = (
                (current - ma_long) / ma_long * 100 if ma_long is not None and ma_long > 0 else None
            )

            out[ticker] = {
                "rsi": rsi_val,
                "macd_signal": macd_signal,
                "trend": trend,
                "drawdown_from_high": dd,
                "price_vs_ma50": price_vs_ma50,
                "current_price": current,
            }
        except Exception:
            continue
    return out


def _calc_rsi_simple(closes: list[float], period: int = 14) -> float | None:
    """簡易 RSI 計算（yfinance bulk 用）。

    データ不足時は None を返す（H1 修正・v2.8）。
    旧実装は 50.0 を返していたが「中立 RSI」と「データなし」を区別できず、
    KAWORU の RSI スイートスポット判定（50-65）に偽装値が流れ込む欺瞞だった。
    """
    if len(closes) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses += -diff
    if losses == 0:
        return 100.0
    avg_g = gains / period
    avg_l = losses / period
    rs = avg_g / avg_l if avg_l > 0 else 0
    return 100 - (100 / (1 + rs))


def _calc_ema_simple(closes: list[float], period: int) -> float:
    """簡易 EMA 計算。"""
    if len(closes) < period:
        return sum(closes) / len(closes)
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return ema


# ============================================================
# ピア比較（同 sector 内の 30 日リターン順位）
# ============================================================


def _compute_peer_scores(
    candidate_tickers: list[str],
    engine: Engine,
) -> dict[str, PeerComparison]:
    """各候補の同 sector 内のピア比較を計算。

    sector_rank: "順位/N" 形式
    relative_strength_30d: sector 平均との差 (%pt)
    """
    if not candidate_tickers:
        return {}

    # 候補の sector を Universe から取得
    try:
        from sqlmodel import Session, col, select as _sel

        from trading_agent.models.universe import Universe
    except Exception as exc:
        _log.warning("ritsuko_peer_import_failed", error=str(exc))
        return {}

    ticker_sector: dict[str, str] = {}
    try:
        with Session(engine) as s:
            rows = s.exec(_sel(Universe).where(col(Universe.ticker).in_(candidate_tickers))).all()
            for r in rows:
                ticker_sector[r.ticker] = r.sector or "unknown"
    except Exception as exc:
        _log.warning("ritsuko_peer_sector_lookup_failed", error=str(exc))
        return {}

    # 候補が属する sector 全銘柄を取得（ピア集団）
    sectors_used = set(ticker_sector.values())
    if not sectors_used:
        return {}
    sector_universe: dict[str, list[str]] = {}
    try:
        with Session(engine) as s:
            for sec in sectors_used:
                rows = s.exec(_sel(Universe).where(col(Universe.sector) == sec)).all()
                sector_universe[sec] = [r.ticker for r in rows]
    except Exception as exc:
        _log.warning("ritsuko_peer_universe_failed", error=str(exc))
        return {}

    # 全関係銘柄の 30 日リターン bulk 取得
    all_tickers = set()
    for ts in sector_universe.values():
        all_tickers.update(ts)
    returns = _fetch_30d_returns_bulk(list(all_tickers))

    out: dict[str, PeerComparison] = {}
    for ticker in candidate_tickers:
        sector = ticker_sector.get(ticker, "unknown")
        peers = sector_universe.get(sector, [])
        peers_with_ret = [(t, returns[t]) for t in peers if t in returns]
        my_ret = returns.get(ticker)
        if not peers_with_ret or my_ret is None:
            out[ticker] = PeerComparison(sector_rank=f"—/{len(peers_with_ret)}")
            continue
        peers_with_ret.sort(key=lambda x: -x[1])
        rank = sum(1 for _, r in peers_with_ret if r > my_ret) + 1
        total = len(peers_with_ret)
        avg_ret = sum(r for _, r in peers_with_ret) / total
        rs_diff = my_ret - avg_ret
        out[ticker] = PeerComparison(
            sector_rank=f"{rank}/{total}",
            relative_strength_30d=rs_diff,
        )
    return out


def _fetch_news_yfinance(tickers: list[str], max_per_ticker: int = 8) -> dict[str, list[NewsItem]]:
    """yfinance.Ticker(t).news で各銘柄の最新ニュースを取得 + 辞書分類 + Haiku 補完。

    Phase B-1: キーワード辞書で日本語ヘッドラインを 60-70% 即決
    Phase B-2: 辞書で漏れた分（英語含む）を Haiku で一括分類
               RITSUKO_HAIKU_ENABLED=1 + ANTHROPIC_API_KEY 設定時のみ有効
    """
    if not tickers:
        return {}
    from trading_agent.wille.news_keywords import classify_headline

    out: dict[str, list[NewsItem]] = {}
    try:
        import yfinance as yf

        from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol
    except Exception:
        return {}

    # 第 1 段：取得 + 辞書分類
    raw_news: list[tuple[str, str, str, str]] = []  # (ticker, headline, date, provider)
    for ticker in tickers:
        try:
            sym = to_yfinance_symbol(ticker)
            news_list = yf.Ticker(sym).news or []
        except Exception:
            continue
        for n in news_list[:max_per_ticker]:
            content = n.get("content") if isinstance(n, dict) else None
            if isinstance(content, dict):
                title = content.get("title") or n.get("title", "")
                pub = content.get("pubDate") or n.get("providerPublishTime", "")
                provider = (content.get("provider") or {}).get("displayName", "")
            else:
                title = n.get("title", "") if isinstance(n, dict) else ""
                pub = n.get("providerPublishTime", "") if isinstance(n, dict) else ""
                provider = ""
            if title:
                raw_news.append((ticker, title, str(pub)[:10] if pub else "", provider))

    # 辞書分類
    classified: dict[str, dict] = {}  # headline → result
    needs_llm: list[str] = []
    for _t, headline, _d, _p in raw_news:
        if headline in classified:
            continue
        cls = classify_headline(headline)
        classified[headline] = cls
        if cls.get("needs_llm"):
            needs_llm.append(headline)

    # 第 2 段：辞書で漏れた分を Haiku でバッチ分類
    if needs_llm:
        haiku_results = _classify_news_with_haiku(needs_llm)
        for h, r in haiku_results.items():
            classified[h] = {**classified.get(h, {}), **r, "needs_llm": False}

    # NewsItem を組み立て
    for ticker, headline, date_s, provider in raw_news:
        cls = classified.get(headline, {})
        items = out.setdefault(ticker, [])
        items.append(
            NewsItem(
                date=date_s,
                headline=headline,
                impact=cls.get("impact", "0"),  # type: ignore[arg-type]
                category=str(cls.get("category", "")),
                confidence=float(cls.get("confidence", 0.0)),
                source=provider,
            )
        )
    return out


def detect_intraday_drops(
    tickers: list[str],
    *,
    threshold_pct: float = -5.0,
) -> dict[str, float]:
    """当日 -threshold_pct 以下の急落銘柄を検知。

    Returns:
        {ticker: change_pct, ...}  threshold 以下のものだけ
    """
    if not tickers:
        return {}
    try:
        import yfinance as yf

        from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol
    except Exception:
        return {}

    sym_map = {to_yfinance_symbol(t): t for t in tickers}
    try:
        df = yf.download(
            list(sym_map.keys()),
            period="5d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
        )
    except Exception:
        return {}
    if df is None or df.empty:
        return {}

    out: dict[str, float] = {}
    for sym, ticker in sym_map.items():
        try:
            series = df[sym]["Close"] if len(sym_map) > 1 else df["Close"]
            closes = [float(x) for x in series.dropna().tolist()]
            if len(closes) < 2:
                continue
            change_pct = (closes[-1] - closes[-2]) / closes[-2] * 100
            if change_pct <= threshold_pct:
                out[ticker] = change_pct
        except Exception:
            continue
    return out


def detect_market_regime_live() -> dict[str, Any]:
    """日経 / TOPIX の当日変動から市場 regime を判定（risk_off フラグ）。

    Returns:
        {"nikkei_change_pct": float|None,
         "topix_change_pct": float|None,
         "is_risk_off": bool,
         "regime": "risk_on"|"risk_off"|"neutral"|"unknown"}
    """
    try:
        import yfinance as yf
    except Exception:
        return {"nikkei_change_pct": None, "topix_change_pct": None, "is_risk_off": False, "regime": "unknown"}

    out: dict[str, Any] = {"nikkei_change_pct": None, "topix_change_pct": None}
    for idx, key in (("^N225", "nikkei_change_pct"), ("^TPX", "topix_change_pct")):
        try:
            df = yf.Ticker(idx).history(period="5d", interval="1d", auto_adjust=True)
            closes = [float(x) for x in df["Close"].dropna().tolist()]
            if len(closes) >= 2:
                out[key] = (closes[-1] - closes[-2]) / closes[-2] * 100
        except Exception:
            continue

    nikkei = out.get("nikkei_change_pct") or 0
    topix = out.get("topix_change_pct") or 0
    is_risk_off = nikkei <= -3.0 or topix <= -3.0
    is_risk_on = nikkei >= 1.5 and topix >= 1.5
    out["is_risk_off"] = is_risk_off
    out["regime"] = "risk_off" if is_risk_off else ("risk_on" if is_risk_on else "neutral")
    return out


def detect_holdings_emergency_sell(
    holdings: list[Any],
    *,
    briefs: dict[str, TickerBrief] | None = None,
    drop_threshold_pct: float = -5.0,
    danger_categories: tuple[str, ...] = ("事故", "経営"),
) -> list[dict[str, Any]]:
    """保有銘柄の「ネガティブニュース + 急落」の組合せで sell 警告を出す。

    Args:
        holdings: Portfolio オブジェクトのリスト（ticker 属性必須）
        briefs: 銘柄ごとの TickerBrief（事故/経営カテゴリのニュース確認用）
        drop_threshold_pct: 急落判定（-5.0 = 当日 -5% 以下）
        danger_categories: 危険カテゴリ（"事故" / "経営" / "格付" など）

    Returns:
        [{"ticker", "change_pct", "danger_news": [...], "reason"}, ...]
    """
    if not holdings:
        return []
    tickers = list({getattr(h, "ticker", None) for h in holdings if getattr(h, "ticker", None)})
    if not tickers:
        return []

    drops = detect_intraday_drops(tickers, threshold_pct=drop_threshold_pct)
    if not drops:
        return []

    alerts: list[dict[str, Any]] = []
    for ticker, change in drops.items():
        danger_news: list[str] = []
        if briefs and ticker in briefs:
            for n in briefs[ticker].news:
                if n.impact == "-" and n.category in danger_categories:
                    danger_news.append(f"[{n.category}] {n.headline}")
        if danger_news:
            alerts.append(
                {
                    "ticker": ticker,
                    "change_pct": change,
                    "danger_news": danger_news,
                    "reason": f"当日 {change:+.1f}% + ネガティブニュース {len(danger_news)} 件",
                }
            )
    return alerts


# ============================================================
# Sonnet スポット投入の予算管理（C2）
# ============================================================


def get_monthly_sonnet_spend_usd(engine: Engine) -> float:
    """当月の Sonnet 系モデルへの累積支出 (USD) を集計。

    Returns:
        当月の cost_logs から model に "sonnet" を含むエントリの cost_usd 合計。
    """
    try:
        from datetime import date as _date

        from sqlmodel import Session, col, select as _sel

        from trading_agent.models.analytics import CostLog
    except Exception:
        return 0.0

    today = _date.today()
    month_start = today.replace(day=1)
    try:
        with Session(engine) as s:
            rows = s.exec(
                _sel(CostLog).where(
                    col(CostLog.date) >= month_start,
                    col(CostLog.model).contains("sonnet"),
                )
            ).all()
        return float(sum(r.cost_usd or 0.0 for r in rows))
    except Exception as exc:
        _log.warning("ritsuko_sonnet_budget_check_failed", error=str(exc))
        return 0.0


def can_invoke_sonnet_spot(engine: Engine, *, monthly_cap_usd: float = 3.0) -> tuple[bool, str]:
    """Sonnet スポット投入の予算チェック。

    Args:
        monthly_cap_usd: 月間上限（環境変数 WILLE_SONNET_MONTHLY_CAP_USD で上書き可）

    Returns:
        (allowed, reason): 投入可否と理由文字列
    """
    import os as _os

    cap = float(_os.environ.get("WILLE_SONNET_MONTHLY_CAP_USD", str(monthly_cap_usd)))
    spent = get_monthly_sonnet_spend_usd(engine)
    if spent >= cap:
        return False, f"月予算上限到達 (${spent:.2f} / ${cap:.2f})"
    if spent >= cap * 0.8:
        # ソフト警告（投入は許可するが警告ログ）
        _log.warning("sonnet_budget_warning", spent_usd=spent, cap_usd=cap)
    return True, f"OK (残 ${cap - spent:.2f})"


# ============================================================
# Sonnet スポット投入 5 節目（D1）
# ============================================================
#
# 5 つの trigger 条件で必要に応じて Sonnet を呼ぶ。
# 通常運用時は env で無効化（RITSUKO_SONNET_ENABLED=0）、コスト発生なし。
# 有効化時も C2 の予算ガード（月 $3 デフォルト）で暴走を防止。
#
# 1. new_candidate:    新規候補が初めてプールに入った時
# 2. serious_news:     ネガティブ category の重大ニュース検知
# 3. magi_zeele_conflict: MAGI と ZEELE の判定が食い違う銘柄
# 4. industry_pivot:   業界スコアが ±0.4 を超える転換点
# 5. promotion_review: DS の昇格判定で n≥20 hit≥0.55 を満たした時


def is_new_candidate(ticker: str, engine: Engine) -> bool:
    """過去 30 日に Decision として存在したか確認。新規なら True。"""
    try:
        from datetime import timedelta

        from sqlmodel import Session, col, select as _sel

        from trading_agent.models.decisions import Decision

        cutoff_date = datetime.now() - timedelta(days=30)
        with Session(engine) as s:
            row = s.exec(
                _sel(Decision)
                .where(col(Decision.ticker) == ticker)
                .where(col(Decision.created_at) >= cutoff_date)
                .limit(1)
            ).first()
            return row is None
    except Exception:
        return False


def has_serious_negative_news(brief: TickerBrief) -> bool:
    """事故/経営カテゴリのネガティブニュースを保有するか。"""
    return any(
        n.impact == "-" and n.category in ("事故", "経営")
        for n in brief.news
    )


def has_magi_zeele_conflict(brief: TickerBrief, candidate: Any | None = None) -> bool:
    """MAGI と ZEELE で source が "magi" のみ／ "zeele" のみ で食い違うかの判定。

    candidate オブジェクト（CandidatePool）の source を見る。
    """
    if candidate is None:
        return False
    src = getattr(candidate, "source", "")
    # both（MAGI+ZEELE 一致）は食い違いではない
    # 片方だけ + 反対側で否定的だった場合は trigger（ただし判定は簡易）
    return False  # 現状は実装をスキップ（将来拡張）


def has_industry_pivot(brief: TickerBrief, threshold: float = 0.4) -> bool:
    """業界スコアが ±threshold を超える転換点か。"""
    return abs(brief.industry_score) >= threshold


def maybe_invoke_sonnet_for_brief(
    ticker: str,
    brief: TickerBrief,
    engine: Engine | None = None,
    candidate: Any | None = None,
) -> SonnetBrief | None:
    """Brief 構築後、5 trigger のいずれかを満たし、予算 OK なら Sonnet 呼び出し。

    env 制御:
      - RITSUKO_SONNET_ENABLED=0  (デフォルト): 何もしない
      - RITSUKO_SONNET_ENABLED=1: trigger 判定 + 予算チェック + 投入

    Returns:
        SonnetBrief or None
    """
    import os as _os

    if _os.environ.get("RITSUKO_SONNET_ENABLED", "0") != "1":
        return None
    if engine is None:
        return None

    # 予算チェック
    allowed, _reason = can_invoke_sonnet_spot(engine)
    if not allowed:
        _log.warning("sonnet_spot_skipped_budget", ticker=ticker, reason=_reason)
        return None

    # trigger 判定（複数該当時は最優先 1 つ）
    trigger: str | None = None
    if is_new_candidate(ticker, engine):
        trigger = "new_candidate"
    elif has_serious_negative_news(brief):
        trigger = "serious_news"
    elif has_industry_pivot(brief):
        trigger = "industry_pivot"
    elif has_magi_zeele_conflict(brief, candidate):
        trigger = "magi_zeele_conflict"

    if trigger is None:
        return None

    # Sonnet 呼び出し
    return _call_sonnet_for_brief(ticker, brief, trigger)


def _call_sonnet_for_brief(
    ticker: str,
    brief: TickerBrief,
    trigger: str,
) -> SonnetBrief | None:
    """Sonnet 呼び出し本体（実 API call）。"""
    try:
        from anthropic import Anthropic
    except Exception:
        return None

    try:
        client = Anthropic()
    except Exception as exc:
        _log.warning("sonnet_client_init_failed", error=str(exc))
        return None

    # Brief サマリ（Sonnet に渡す）
    news_summary = "\n".join(
        f"- [{n.impact}] {n.headline[:120]}" for n in brief.news[:5]
    )
    industry_summary = f"score={brief.industry_score:+.2f}, tailwinds={brief.industry.tailwinds[:2]}, headwinds={brief.industry.headwinds[:2]}"
    peer_summary = f"rank={brief.peer.sector_rank}, rs_30d={brief.peer.relative_strength_30d:+.1f}%"

    prompt = (
        f"銘柄 {ticker} の投資判断を補助してください。トリガー: {trigger}\n\n"
        f"## 技術指標\n  RSI={brief.technicals.rsi}, MACD={brief.technicals.macd_signal}, trend={brief.technicals.trend}, situation={brief.technicals.situation}\n\n"
        f"## 個別ニュース (impact 付き)\n{news_summary or '  (なし)'}\n\n"
        f"## 業界 ({brief.industry.sector})\n  {industry_summary}\n\n"
        f"## ピア比較\n  {peer_summary}\n\n"
        f"以下の JSON のみを返してください：\n"
        f'{{"recommendation_score": <-1.0~1.0>, "narrative": "<60 字程度>"}}'
    )

    import json as _json
    import re as _re

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text if resp.content else ""
        m = _re.search(r"\{.*\}", text, _re.DOTALL)
        if not m:
            return None
        data = _json.loads(m.group(0))
        score = float(data.get("recommendation_score", 0.0))
        score = max(-1.0, min(1.0, score))
        narrative = str(data.get("narrative", ""))[:200]
        return SonnetBrief(
            recommendation_score=score,
            narrative=narrative,
            triggered_by=trigger,
        )
    except Exception as exc:
        _log.warning("sonnet_call_failed", ticker=ticker, error=str(exc))
        return None


def _classify_news_with_haiku(headlines: list[str]) -> dict[str, dict]:
    """辞書で分類できなかったヘッドラインを Haiku で一括分類。

    環境変数 RITSUKO_HAIKU_ENABLED=1 + ANTHROPIC_API_KEY 設定時のみ動作。
    デフォルト無効（テスト・初期検証時のコスト 0 を保証）。

    BATCH_SIZE=15 で 1 prompt にまとめてコスト圧縮（200 件 → 14 calls）。
    """
    if not headlines:
        return {}
    import os

    if os.environ.get("RITSUKO_HAIKU_ENABLED", "0") != "1":
        return {}
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {}

    try:
        from anthropic import Anthropic
    except Exception as exc:
        _log.warning("ritsuko_haiku_import_failed", error=str(exc))
        return {}

    # H2 修正: Haiku 出力の値域検証（CASPER LLM パターンに倣う）
    _ALLOWED_IMPACT = {"+", "-", "0"}
    _ALLOWED_CATEGORY = {"業績", "資本", "材料", "事故", "格付", "経営", "M&A", "その他"}

    BATCH_SIZE = 15
    out: dict[str, dict] = {}
    try:
        client = Anthropic()
    except Exception as exc:
        _log.warning("ritsuko_haiku_client_init_failed", error=str(exc))
        return {}

    import json as _json
    import re as _re

    for i in range(0, len(headlines), BATCH_SIZE):
        batch = headlines[i : i + BATCH_SIZE]
        prompt = (
            "以下の株式ニュースヘッドラインを投資インパクトで分類してください。\n"
            "  impact: '+'(買い材料) / '-'(売り材料) / '0'(中立) — この 3 値のみ\n"
            "  category: 業績/資本/材料/事故/格付/経営/M&A/その他 — この 8 値のみ\n"
            "  confidence: 0.0-1.0 の浮動小数\n\n"
            + "\n".join(f"{idx + 1}. {h}" for idx, h in enumerate(batch))
            + '\n\nJSON 配列のみ返してください: [{"idx": 1, "impact": "+", "category": "業績", "confidence": 0.8}, ...]'
        )
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text if resp.content else ""
            m = _re.search(r"\[.*\]", text, _re.DOTALL)
            if not m:
                _log.warning("ritsuko_haiku_no_json", batch_size=len(batch))
                continue
            results = _json.loads(m.group(0))
            for r in results:
                idx = int(r.get("idx", 0)) - 1
                if not (0 <= idx < len(batch)):
                    continue
                # 値域検証 — 不正値は破棄して次のヘッドラインへ
                impact_raw = r.get("impact", "0")
                if impact_raw not in _ALLOWED_IMPACT:
                    _log.warning(
                        "ritsuko_haiku_invalid_impact",
                        headline=batch[idx][:80],
                        impact=impact_raw,
                    )
                    continue
                category_raw = str(r.get("category", "")).strip()
                if category_raw not in _ALLOWED_CATEGORY:
                    category_raw = "その他"
                try:
                    confidence = float(r.get("confidence", 0.5))
                except (TypeError, ValueError):
                    confidence = 0.5
                confidence = max(0.0, min(1.0, confidence))
                out[batch[idx]] = {
                    "impact": impact_raw,
                    "category": category_raw,
                    "confidence": confidence,
                }
        except Exception as exc:
            _log.warning("ritsuko_haiku_batch_failed", error=str(exc), batch_size=len(batch))
            continue

    return out


def _fetch_upcoming_events_yfinance(tickers: list[str]) -> dict[str, list[UpcomingEvent]]:
    """yfinance.Ticker(t).calendar で決算予定を取得。"""
    if not tickers:
        return {}
    out: dict[str, list[UpcomingEvent]] = {}
    try:
        import yfinance as yf

        from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol
    except Exception:
        return {}

    for ticker in tickers:
        try:
            sym = to_yfinance_symbol(ticker)
            cal = yf.Ticker(sym).calendar
        except Exception:
            continue
        if not cal:
            continue
        events: list[UpcomingEvent] = []
        if isinstance(cal, dict):
            earnings_dates = cal.get("Earnings Date") or []
            if not isinstance(earnings_dates, list):
                earnings_dates = [earnings_dates]
            for ed in earnings_dates:
                ed_str = str(ed)[:10] if ed else ""
                if ed_str:
                    events.append(UpcomingEvent(date=ed_str, type="earnings", note="決算予定"))
        if events:
            out[ticker] = events
    return out


def _fetch_30d_returns_bulk(tickers: list[str]) -> dict[str, float]:
    """yfinance で 30 日リターン (%) を一括計算（≈22 営業日）。"""
    if not tickers:
        return {}
    try:
        import yfinance as yf

        from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol
    except Exception:
        return {}
    sym_map = {to_yfinance_symbol(t): t for t in tickers}
    try:
        df = yf.download(
            list(sym_map.keys()),
            period="2mo",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
        )
    except Exception:
        return {}
    if df is None or df.empty:
        return {}
    out: dict[str, float] = {}
    for sym, t in sym_map.items():
        try:
            series = df[sym]["Close"] if len(sym_map) > 1 else df["Close"]
            closes = series.dropna().tolist()
            if len(closes) < 22:
                continue
            ret_pct = (float(closes[-1]) - float(closes[-22])) / float(closes[-22]) * 100
            out[t] = ret_pct
        except Exception:
            continue
    return out


# ============================================================
# 後方互換: tag_candidates_with_situation
# ============================================================


def tag_candidates_with_situation(
    candidates: list[Any],
    *,
    engine: Engine | None = None,
    technicals_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, SituationReport]:
    """旧 API：SituationReport の dict を返す（後方互換）。

    新コードは build_briefs_from_pool を使うべき。
    """
    briefs = build_briefs_from_pool(
        candidates,
        engine=engine,
        technicals_lookup=technicals_overrides,
    )
    out: dict[str, SituationReport] = {}
    for ticker, brief in briefs.items():
        sit = brief.technicals.situation
        out[ticker] = SituationReport(
            ticker=ticker,
            situation=sit,
            confidence=0.7 if sit not in ("unknown", "neutral") else (0.4 if sit == "neutral" else 0.0),
            signals=[
                s
                for s in [
                    f"RSI={brief.technicals.rsi:.0f}" if brief.technicals.rsi is not None else None,
                    f"MACD={brief.technicals.macd_signal}" if brief.technicals.macd_signal else None,
                    f"trend={brief.technicals.trend}" if brief.technicals.trend else None,
                ]
                if s
            ],
            recommended_pilots=SITUATION_PILOT_AFFINITY.get(sit, []),
            reasoning=f"situation={sit}（MAGI Technicals 借用）",
        )
    return out
