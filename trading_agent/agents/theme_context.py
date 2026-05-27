"""screening のテーマスコア用に、セクター/市場の 30日リターンと topic 件数を集める。

screening_agent から呼び出され、テーマスコア4軸のうち以下2つを埋める：

- `keyword_match_count`：直近 N日（既定7日）の topics で `affected_tickers` に含まれた回数。
  「この銘柄名がニュースサイクルに何回出てきたか」のプロキシ（テーマ熱量の代理指標）。
- `sector_return_30d` / `market_return_30d`：セクター ETF と市場 ETF の 30日リターン。
  TOPIX 33業種に対応する Nomura NEXT FUNDS（1617-1633.T）を用いる。

sector_return_30d は universe.sector（yfinance 標準カテゴリ）を ETF にマップする。
マップに無いセクターは None を返す（screening 側でスキップ＝採点されない）。

外部 API への重複呼び出しを避けるため、`SectorReturnCache` がプロセス内で
sector/market のリターンをキャッシュする（テスト用に fetcher を注入可能）。
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Callable

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.models.topics import Topic

# 30日リターンを取る関数の型：ETF シンボル → リターン（小数。10% → 0.10）
ReturnFetcher = Callable[[str], float | None]

# 市場 ETF
_JP_MARKET_ETF = "1306.T"  # TOPIX ETF
_US_MARKET_ETF = "SPY"     # S&P 500

# yfinance 標準セクター → JP 業種別 ETF（NEXT FUNDS by Nomura）
# 対応の取れない（or 該当ETFが無い）セクターは省略＝None 扱い。
_JP_SECTOR_ETF: dict[str, str] = {
    "Technology": "1626.T",            # 情報通信業
    "Communication Services": "1626.T",
    "Healthcare": "1621.T",            # 医薬品
    "Financial Services": "1632.T",    # 銀行業
    "Industrials": "1628.T",           # 機械
    "Consumer Cyclical": "1622.T",     # 自動車・輸送機器
    "Consumer Defensive": "1617.T",    # 食品
    "Basic Materials": "1618.T",       # エネルギー資源（素材近似）
    "Energy": "1618.T",
    "Utilities": "1627.T",             # 電力・ガス
    "Real Estate": "1631.T",           # 不動産
}

# yfinance 標準セクター → SPDR セクター ETF
_US_SECTOR_ETF: dict[str, str] = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Communication Services": "XLC",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
}


def _is_jp_market(market: str) -> bool:
    return market.upper() in {"JP", "TSE", "TOPIX"}


def sector_etf(market: str, sector: str) -> str | None:
    """market と sector から対応する ETF シンボルを引く。マッピング無しは None。"""
    table = _JP_SECTOR_ETF if _is_jp_market(market) else _US_SECTOR_ETF
    return table.get(sector)


def market_etf(market: str) -> str:
    """market 種別から市場 ETF シンボルを引く。"""
    return _JP_MARKET_ETF if _is_jp_market(market) else _US_MARKET_ETF


def fetch_30d_return_yfinance(symbol: str) -> float | None:
    """yfinance から ETF の 30日リターンを取得する。失敗時 None。"""
    try:
        import yfinance as yf

        df = yf.Ticker(symbol).history(period="35d", interval="1d", auto_adjust=False)
        if df is None or getattr(df, "empty", True) or "Close" not in df.columns:
            return None
        closes = [float(v) for v in df["Close"].dropna().tolist()]
        if len(closes) < 2:
            return None
        # 「直近30日」の近似：終値 t-30 と t を比較。データが30営業日に満たなくても最古〜最新で代用
        # （祝日・週末で 35日 → 約22営業日になるのが典型）
        old = closes[0]
        new = closes[-1]
        if old <= 0:
            return None
        return (new - old) / old
    except Exception:
        return None


class SectorReturnCache:
    """sector_return_30d / market_return_30d のプロセス内キャッシュ。

    1朝バッチで同じ ETF を複数銘柄分まとめて引かないために使う。
    fetcher は注入可能（テスト時はスタブ）。
    """

    def __init__(self, fetcher: ReturnFetcher | None = None) -> None:
        self._fetcher: ReturnFetcher = fetcher or fetch_30d_return_yfinance
        self._cache: dict[str, float | None] = {}

    def get(self, symbol: str) -> float | None:
        if symbol not in self._cache:
            self._cache[symbol] = self._fetcher(symbol)
        return self._cache[symbol]

    def market_return(self, market: str) -> float | None:
        return self.get(market_etf(market))

    def sector_return(self, market: str, sector: str) -> float | None:
        sym = sector_etf(market, sector)
        if sym is None:
            return None
        return self.get(sym)


def keyword_match_counts(
    engine: Engine, tickers: list[str], *, lookback_days: int = 7
) -> dict[str, int]:
    """直近 N日の topics で affected_tickers に各 ticker が現れた件数を返す。

    1銘柄が複数 topic で言及されると count が増える＝テーマ熱量の代理。
    affected_tickers は JSON 列なので Python 側でアンパックして数える。
    """
    cutoff = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=lookback_days)
    counts: Counter[str] = Counter()
    ticker_set = set(tickers)
    with Session(engine) as session:
        for topic in session.exec(
            select(Topic).where(col(Topic.collected_at) >= cutoff)
        ):
            affected = topic.affected_tickers or []
            for t in affected:
                if t in ticker_set:
                    counts[t] += 1
    return {t: counts.get(t, 0) for t in tickers}
