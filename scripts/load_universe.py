"""universe（銘柄母集団）を投入する（A-1）。

方針（D-22「自動定義」採用・2026-05-23）：**出所の明確な実在銘柄のみ**を登録し、勝手に発明しない。
- US：流動性の高い大型株（端株可なので単価は不問）。
- JP：流動性が高く比較的安価な主力（¥100万・単元100株前提。可否はサイジングの20%上限で担保）。
メタ情報（社名・セクター・時価総額・出来高）は **yfinance から取得**（出所＝yfinance）。
upsert（ticker主キー）で冪等。`screening_agent._load_universe` が market_cap_jpy 降順で読む。

実行:
    .venv/bin/python scripts/load_universe.py            # 既定DBへ投入（live）
    .venv/bin/python scripts/load_universe.py --dry-run  # 取得のみ・DB書き込みなし
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlmodel import Session

from trading_agent.db import create_all, get_engine
from trading_agent.models._common import utcnow
from trading_agent.models.universe import Universe
from trading_agent.utils.logger import get_logger

_log = get_logger("load_universe")

# --- 母集団（実在・検証可能な大型株。発明しない） ---------------------------
# US：大型・高流動。端株前提のため単価は不問。
US_TICKERS: tuple[str, ...] = (
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "TSLA",
    "AMD", "NFLX", "ADBE", "CRM", "COST", "JPM", "V", "MA", "WMT", "KO",
)
# JP：高流動かつ比較的安価な主力（証券コード）。100株ロットが¥1M枠に収まりやすい順を意識。
JP_TICKERS: tuple[str, ...] = (
    "9432",  # NTT（超低位・高流動）
    "7267",  # ホンダ
    "8306",  # 三菱UFJ
    "6178",  # 日本郵政
    "3382",  # セブン&アイ
    "9434",  # ソフトバンク（通信）
    "6501",  # 日立
    "7203",  # トヨタ
)

# (ticker, market) の確定リスト
ENTRIES: tuple[tuple[str, str], ...] = (
    *((t, "US") for t in US_TICKERS),
    *((t, "JP") for t in JP_TICKERS),
)

# メタ取得関数の型：(ticker, market) → メタ辞書（取得不能は None）
MetaFetcher = Callable[[str, str], dict | None]


def _yf_symbol(ticker: str, market: str) -> str:
    return f"{ticker}.T" if market == "JP" else ticker


def _fetch_meta_yfinance(ticker: str, market: str) -> dict | None:
    import yfinance as yf

    try:
        info = yf.Ticker(_yf_symbol(ticker, market)).info
    except Exception as exc:  # 1銘柄の失敗で全体を止めない
        _log.warning("universe_meta_failed", ticker=ticker, error=str(exc))
        return None
    if not info:
        return None
    return {
        "name": info.get("longName") or info.get("shortName") or ticker,
        "name_en": info.get("shortName"),
        "sector": info.get("sector") or "unknown",
        "industry": info.get("industry"),
        "market_cap": float(info.get("marketCap") or 0.0),
        "avg_volume_30d": float(
            info.get("averageVolume") or info.get("averageDailyVolume10Day") or 0.0
        ),
    }


def build_rows(
    entries: tuple[tuple[str, str], ...],
    fetcher: MetaFetcher,
    usdjpy: float,
) -> list[Universe]:
    """各銘柄のメタを取得し Universe 行へ。market_cap_jpy は US のみ USDJPY 換算。"""
    rows: list[Universe] = []
    for ticker, market in entries:
        meta = fetcher(ticker, market)
        if meta is None:
            continue
        mc = meta.get("market_cap", 0.0)
        mc_jpy = mc if market == "JP" else mc * usdjpy
        rows.append(
            Universe(
                ticker=ticker,
                name=meta.get("name") or ticker,
                name_en=meta.get("name_en"),
                market=market,
                sector=meta.get("sector") or "unknown",
                industry=meta.get("industry"),
                market_cap=mc,
                market_cap_jpy=mc_jpy,
                avg_volume_30d=meta.get("avg_volume_30d", 0.0),
            )
        )
    return rows


def upsert_universe(engine: Engine, rows: list[Universe]) -> int:
    """ticker 主キーで upsert（冪等）。返り値＝書き込んだ件数。"""
    n = 0
    with Session(engine) as session:
        for row in rows:
            existing = session.get(Universe, row.ticker)
            if existing is None:
                session.add(row)
            else:
                existing.name = row.name
                existing.name_en = row.name_en
                existing.market = row.market
                existing.sector = row.sector
                existing.industry = row.industry
                existing.market_cap = row.market_cap
                existing.market_cap_jpy = row.market_cap_jpy
                existing.avg_volume_30d = row.avg_volume_30d
                existing.is_active = True
                existing.updated_at = utcnow()
                session.add(existing)
            n += 1
        session.commit()
    return n


def _usdjpy() -> float:
    try:
        import yfinance as yf

        return float(yf.Ticker("JPY=X").fast_info.last_price)
    except Exception as exc:
        _log.warning("usdjpy_failed", error=str(exc))
        return 150.0


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    usdjpy = _usdjpy()
    rows = build_rows(ENTRIES, _fetch_meta_yfinance, usdjpy)
    print(f"fetched {len(rows)}/{len(ENTRIES)} tickers (usdjpy={usdjpy:.1f})")
    for r in sorted(rows, key=lambda x: x.market_cap_jpy, reverse=True)[:5]:
        print(f"  {r.ticker:6} {r.market} {r.sector:24} cap_jpy={r.market_cap_jpy:,.0f}")
    if dry_run:
        print("dry-run: not writing to DB")
        return
    engine = get_engine(Path("data") / "trading.sqlite")
    create_all(engine)
    written = upsert_universe(engine, rows)
    total = len(inspect(engine).get_table_names())
    print(f"upserted {written} rows into universe (tables={total})")


if __name__ == "__main__":
    main()
