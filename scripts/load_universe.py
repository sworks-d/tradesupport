"""universe（銘柄母集団）を投入する（A-1）。

方針（D-25「JP 90% / US 10%（ETFサテライト）」採用・2026-05-26）：**出所の明確な実在銘柄のみ**を登録し、勝手に発明しない。

- **JP主軸（90%）**：**JPX 公式 Excel から TOPIX 500（Core30 + Large70 + Mid400）を動的取得**。
  銘柄選定の主観バイアスを除外し、市場区分・規模区分・業種を JPX 公式データに準拠させる。
  既存の静的 30 銘柄リスト（`JP_TICKERS`）は JPX 取得失敗時のフォールバックとして残す。
- **US ETFサテライト（10%）**：QQQ / VOO の 2本のみ（個別米株は D-25 で禁止）。

メタ情報（社名・セクター・時価総額・出来高）は **yfinance から取得**（時価総額の動的補完）。
upsert（ticker主キー）で冪等。`screening_agent._load_universe` は JP 中小型バンド(¥100億〜¥1兆)を
時価総額 stratified sample で読む（大指針 #2・大型偏向回避）。

実行:
    .venv/bin/python scripts/load_universe.py            # 既定DBへ投入（live, JPX 取得）
    .venv/bin/python scripts/load_universe.py --static   # JPX 取得せず静的 30 銘柄のみ
    .venv/bin/python scripts/load_universe.py --dry-run  # 取得のみ・DB書き込みなし
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from trading_agent.db import create_all, get_engine
from trading_agent.models._common import utcnow
from trading_agent.models.universe import Universe
from trading_agent.utils.logger import get_logger

_log = get_logger("load_universe")

# JPX 公式「上場銘柄一覧」Excel（毎月更新）
JPX_LISTED_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"

# universe 採用対象の規模区分（v2.10: TOPIX 1000 = Core30 + Large70 + Mid400 + Small1）
# 中小型株を主戦場に含めて a/b/c + V字 を狙う（D 案の拡張）。
# Small2 は流動性低・データ薄なので除外（後で必要なら拡張）。
TOPIX500_SCALES = frozenset(
    {"TOPIX Core30", "TOPIX Large70", "TOPIX Mid400", "TOPIX Small 1"}
)

# 採用対象の市場・商品区分（v2.10: 成長株主戦場としてグロース市場も追加）
# プライム: TOPIX 規模区分でフィルタ（流動性・品質保証）
# グロース: 規模区分が付与されないため全銘柄を候補に、yfinance 取得時のリスクフィルタで篩い分け
ELIGIBLE_MARKETS = frozenset({"プライム（内国株式）", "グロース（内国株式）"})

# 入口リスク排除フィルタ（v2.10: ハルシネーション温床を Universe に入れない）
# 「データが取れない・流動性低・時価総額極小」の銘柄は MAGI/ZEELE が誤判断する元になる
MIN_MARKET_CAP_JPY = 5_000_000_000      # 時価総額 50 億円（仕手・極小株を排除）
MIN_DAILY_TURNOVER_JPY = 50_000_000     # 売買代金 5000 万円/日（moomoo 約定リスク回避）

# --- 母集団（実在・検証可能。発明しない）。D-25：JP90% / US ETFサテライト10% ---
# JP主軸（30銘柄）：TOPIX100中心、セクター分散。moomoo単元未満で1株から買える。
JP_TICKERS: tuple[str, ...] = (
    # 既存14（D-23時点）
    "7203",  # トヨタ（自動車）
    "6758",  # ソニーG（電機/娯楽）
    "9984",  # ソフトバンクG（投資/通信）
    "8306",  # 三菱UFJ（銀行）
    "9432",  # NTT（通信）
    "6501",  # 日立（総合電機/IT）
    "6098",  # リクルート（人材/サービス）
    "4063",  # 信越化学（素材）
    "8058",  # 三菱商事（商社）
    "7974",  # 任天堂（ゲーム）
    "4502",  # 武田薬品（医薬）
    "6902",  # デンソー（自動車部品）
    "8035",  # 東京エレクトロン（半導体製造装置）
    "9983",  # ファーストリテイリング（小売）
    # 追加16（D-25で拡張・2026-05-26）：セクター・配当・テーマの厚みを増やす
    "7267",  # ホンダ（自動車・トヨタ補完）
    "4452",  # 花王（日用品・配当王）
    "8316",  # 三井住友FG（銀行）
    "8411",  # みずほFG（銀行）
    "9433",  # KDDI（通信・配当）
    "4661",  # オリエンタルランド（エンタメ）
    "4543",  # テルモ（医療機器）
    "6981",  # 村田製作所（電子部品）
    "9020",  # JR東日本（運輸）
    "6594",  # ニデック（電機・モーター）
    "6857",  # アドバンテスト（半導体検査装置）
    "4503",  # アステラス製薬（医薬・配当）
    "6273",  # SMC（機械装置）
    "7741",  # HOYA（光学/医療）
    "9101",  # 商船三井（海運）
    "8001",  # 伊藤忠商事（商社）
)
# US ETFサテライト（D-25：個別株は禁止・ETFのみ・2本）
US_TICKERS: tuple[str, ...] = (
    "QQQ",   # Nasdaq 100（AI/Tech の市場曝露）
    "VOO",   # S&P 500（米国大型分散）
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

    market_cap = float(info.get("marketCap") or 0.0)
    avg_vol = float(
        info.get("averageVolume") or info.get("averageDailyVolume10Day") or 0.0
    )
    price = float(
        info.get("currentPrice")
        or info.get("regularMarketPrice")
        or info.get("previousClose")
        or 0.0
    )
    sector = info.get("sector")

    # v2.10: 入口リスク排除（ハルシネーション温床を Universe に入れない）
    # JP 株のみ厳格チェック。US ETF (QQQ/VOO) は marketCap/sector が yfinance で
    # 取得できないことが多いため、D-25 設計（サテライト）として別軸で通す。
    if market == "JP":
        # データ完全性
        if market_cap <= 0 or avg_vol <= 0 or price <= 0 or not sector:
            _log.info(
                "universe_drop_incomplete_data",
                ticker=ticker, market=market,
                has_mcap=market_cap > 0, has_vol=avg_vol > 0,
                has_price=price > 0, has_sector=bool(sector),
            )
            return None
        # 時価総額下限
        if market_cap < MIN_MARKET_CAP_JPY:
            _log.info(
                "universe_drop_low_market_cap",
                ticker=ticker, market_cap=int(market_cap),
                threshold=int(MIN_MARKET_CAP_JPY),
            )
            return None
        # 流動性下限（売買代金）
        daily_turnover = price * avg_vol
        if daily_turnover < MIN_DAILY_TURNOVER_JPY:
            _log.info(
                "universe_drop_low_liquidity",
                ticker=ticker, daily_turnover=int(daily_turnover),
                threshold=int(MIN_DAILY_TURNOVER_JPY),
            )
            return None

    return {
        "name": info.get("longName") or info.get("shortName") or ticker,
        "name_en": info.get("shortName"),
        "sector": sector or "unknown",
        "industry": info.get("industry"),
        "market_cap": market_cap,
        "avg_volume_30d": avg_vol,
    }


def _fetch_jpx_listed_topix500() -> tuple[tuple[str, str], ...] | None:
    """JPX 公式 Excel から TOPIX 500 構成銘柄を取得。失敗時は None。

    Returns:
        ((ticker, market), ...)  すべて market="JP"。証券コード 4 桁の文字列。
    """
    try:
        import io

        import httpx
        import pandas as pd

        _log.info("jpx_fetch_start", url=JPX_LISTED_URL)
        resp = httpx.get(JPX_LISTED_URL, timeout=60.0, follow_redirects=True)
        resp.raise_for_status()
        df = pd.read_excel(io.BytesIO(resp.content))
    except Exception as exc:
        _log.warning("jpx_fetch_failed", error=str(exc))
        return None

    required_cols = {"コード", "市場・商品区分", "規模区分"}
    if not required_cols.issubset(df.columns):
        _log.warning("jpx_unexpected_columns", columns=list(df.columns))
        return None

    # フィルタ（v2.10）:
    #   プライム → TOPIX 規模区分（Core30/Large70/Mid400/Small 1）でフィルタ
    #   グロース → 規模区分が付与されないため全銘柄を候補に（yfinance リスクフィルタで篩う）
    mask_prime = (df["市場・商品区分"] == "プライム（内国株式）") & df["規模区分"].isin(
        TOPIX500_SCALES
    )
    mask_growth = df["市場・商品区分"] == "グロース（内国株式）"
    filtered = df[mask_prime | mask_growth]

    tickers: list[tuple[str, str]] = []
    seen: set[str] = set()
    for code in filtered["コード"]:
        # コードは int で来ることがあるので zero-padded 4桁文字列に正規化
        try:
            code_str = str(int(code)).zfill(4)
        except (TypeError, ValueError):
            code_str = str(code).strip()
        if not code_str or code_str in seen:
            continue
        seen.add(code_str)
        tickers.append((code_str, "JP"))

    _log.info("jpx_fetch_success", count=len(tickers))
    return tuple(tickers)


def _fetch_meta_yfinance_parallel(
    entries: tuple[tuple[str, str], ...],
    max_workers: int = 16,
    max_retries: int = 2,
    retry_wait_seconds: int = 60,
) -> dict[tuple[str, str], dict]:
    """yfinance の info を並列取得 + rate limit リトライ。

    失敗銘柄はバックオフ待機後に再試行（最大 `max_retries` 回）。
    """
    import time

    results: dict[tuple[str, str], dict] = {}
    remaining: list[tuple[str, str]] = list(entries)

    for attempt in range(max_retries + 1):
        if not remaining:
            break
        if attempt > 0:
            wait = retry_wait_seconds * attempt  # 線形バックオフ: 60s, 120s
            _log.info(
                "yfinance_retry_wait",
                attempt=attempt,
                wait_seconds=wait,
                remaining=len(remaining),
            )
            time.sleep(wait)
        total = len(remaining)
        done = 0
        attempt_label = "initial" if attempt == 0 else f"retry_{attempt}"
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_fetch_meta_yfinance, t, m): (t, m) for t, m in remaining}
            for fut in as_completed(futures):
                key = futures[fut]
                done += 1
                if done % 50 == 0 or done == total:
                    _log.info(
                        "yfinance_progress", phase=attempt_label, done=done, total=total
                    )
                try:
                    meta = fut.result()
                except Exception as exc:
                    _log.warning(
                        "yfinance_meta_exception", ticker=key[0], error=str(exc)
                    )
                    continue
                if meta is not None:
                    results[key] = meta
        remaining = [e for e in entries if e not in results]
        _log.info(
            "yfinance_phase_done",
            phase=attempt_label,
            succeeded=len(results),
            still_missing=len(remaining),
        )
    return results


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


def _resolve_entries(use_static: bool) -> tuple[tuple[str, str], ...]:
    """投入対象の (ticker, market) を解決する。

    既定：JPX 公式 Excel から TOPIX 500 を取得 + US ETF 2 銘柄。
    `--static` 指定 or JPX 取得失敗時は静的 30 銘柄にフォールバック。
    """
    if not use_static:
        jp_entries = _fetch_jpx_listed_topix500()
        if jp_entries:
            return (*jp_entries, *((t, "US") for t in US_TICKERS))
        _log.warning("jpx_fetch_fallback_to_static")
    return ENTRIES


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    use_static = "--static" in sys.argv
    usdjpy = _usdjpy()

    entries = _resolve_entries(use_static)
    print(f"target: {len(entries)} tickers (mode={'static' if use_static else 'jpx-topix500'})")

    # yfinance メタを並列取得（500 銘柄 ~2-3 分）。30 銘柄なら数十秒。
    metas = _fetch_meta_yfinance_parallel(entries, max_workers=16)
    rows: list[Universe] = []
    for ticker, market in entries:
        meta = metas.get((ticker, market))
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
    print(f"fetched {len(rows)}/{len(entries)} tickers (usdjpy={usdjpy:.1f})")
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
