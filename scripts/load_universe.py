"""universe（銘柄母集団）を投入する（A-1）。

方針（D-23「日本株主体」採用・2026-05-23）：**出所の明確な実在銘柄のみ**を登録し、勝手に発明しない。
- **JP主体（為替対策）**：流動性の高い主力をセクター分散で（テーマ集中規律③が効くように）。
  moomoo は JP も**単元未満（ひと株）手数料0**＝単価が高くても1株から買え、¥100kでも分散可能。
- US従：メガキャップ少数（端株可）。¥100kでは円→ドルの為替スプレッドが効くため数を絞る。
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
from sqlmodel import Session, col, select

from trading_agent.db import create_all, get_engine
from trading_agent.models._common import utcnow
from trading_agent.models.universe import Universe
from trading_agent.utils.logger import get_logger

_log = get_logger("load_universe")

# --- 母集団（実在・検証可能。発明しない）。D-23：JP主体・US従 ----------------
# JP主体：高流動の主力をセクター分散で（証券コード）。moomoo単元未満で1株から買える。
JP_TICKERS: tuple[str, ...] = (
    "7203",  # トヨタ（自動車）
    "6758",  # ソニーG（電機/娯楽）
    "9984",  # ソフトバンクG（投資/通信）
    "8306",  # 三菱UFJ（銀行）
    "9432",  # NTT（通信・低位）
    "6501",  # 日立（総合電機/IT）
    "6098",  # リクルート（人材/サービス）
    "4063",  # 信越化学（素材）
    "8058",  # 三菱商事（商社）
    "7974",  # 任天堂（ゲーム）
    "4502",  # 武田薬品（医薬）
    "6902",  # デンソー（自動車部品）
    "8035",  # 東京エレクトロン（半導体製造装置）
    "9983",  # ファーストリテイリング（小売）
)
# US従：メガキャップ少数（端株可）。為替スプレッドのため数を絞る。
US_TICKERS: tuple[str, ...] = (
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",
)

# (ticker, market) の確定リスト（JP主体なので JP を先に）
ENTRIES: tuple[tuple[str, str], ...] = (
    *((t, "JP") for t in JP_TICKERS),
    *((t, "US") for t in US_TICKERS),
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
    """ticker 主キーで upsert（冪等）。**渡した集合＝アクティブな母集団**：

    リストに無い既存銘柄は `is_active=False` にする（リストから外した銘柄が母集団に残らない）。
    返り値＝upsert（挿入/更新）した件数。
    """
    keep = {r.ticker for r in rows}
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
        # リストから外れた銘柄は母集団から外す（is_active=False）
        for u in session.exec(select(Universe).where(col(Universe.is_active))).all():
            if u.ticker not in keep:
                u.is_active = False
                u.updated_at = utcnow()
                session.add(u)
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
