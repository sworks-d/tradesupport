"""B1 デモ：出典(source_refs)・時点(data_asof)・2ソース照合 を人間可読で表示する。

バックエンドの「数値はコードが取得した実データのみ／出典と時点を必ず保持／重要数値は
2ソース照合」が実際にどう出力されるかを、ネットワーク無し（決定論的ダミーデータ）で見せる。

実行:
    .venv/bin/python scripts/demo_b1.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from trading_agent.db import create_all, get_engine
from trading_agent.mcp_tools.fundamentals import FundamentalsInput, FundamentalsTool
from trading_agent.mcp_tools.market_data import MarketDataInput, MarketDataTool
from trading_agent.mcp_tools.news import NewsInput, NewsTool
from trading_agent.mcp_tools.technicals import TechnicalsInput, TechnicalsTool


def _quote(cur: float, prev: float) -> dict[str, float]:
    return {
        "current_price": cur,
        "open_price": prev,
        "high_today": cur + 1,
        "low_today": prev - 1,
        "prev_close": prev,
        "volume_today": 1_000_000.0,
        "price_change_today": cur - prev,
        "price_change_pct_today": (cur - prev) / prev if prev else 0.0,
    }


def line(title: str) -> None:
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


async def main() -> None:
    eng = get_engine(Path(tempfile.gettempdir()) / "demo_b1.sqlite")
    create_all(eng)

    # ① 株価：出典・時点・2ソース照合（一致）
    line("① 株価 market_data：2ソース一致（±0.5%以内）")
    primary = lambda ts: {t: _quote(950.0, 940.0) for t in ts}  # noqa: E731
    sec_ok = lambda ts: {t: _quote(950.8, 940.0) for t in ts}  # noqa: E731 (差0.08%)
    out = await MarketDataTool(eng, fetcher=primary, reconcile_fetcher=sec_ok).execute(
        MarketDataInput(tickers=["NVDA"])
    )
    print(f"  価格    : NVDA = ${out.data['NVDA']['current_price']}")
    print(f"  時点    : data_asof = {out.data_asof}")
    print(f"  出典    : {[(r.source, r.ref) for r in out.source_refs]}")
    print(f"  2ソース照合: {out.reconciliation}  ← ok=一致")

    # ② 株価：2ソースが食い違う → mismatch ＋ フラグ
    line("② 株価 market_data：2ソース不一致（>0.5%）→ 赤フラグ候補")
    sec_bad = lambda ts: {t: _quote(980.0, 940.0) for t in ts}  # noqa: E731 (差3.2%)
    out2 = await MarketDataTool(eng, fetcher=primary, reconcile_fetcher=sec_bad).execute(
        MarketDataInput(tickers=["TSLA"])
    )
    print(f"  1ソース目: $950.0 / 2ソース目: $980.0")
    print(f"  2ソース照合: {out2.reconciliation}  ← mismatch=不一致")
    print(f"  メタ警告 : price_mismatch = {out2.metadata.get('price_mismatch')}")
    print("  → 防御層(B3)はこれを赤にし、決裁の既定を『保留』に寄せる")

    # ③ 財務：報告期(fiscal_period)を“時点”として保持（V字の生命線）
    line("③ 財務 fundamentals（MELCHIORの素材）：報告期を時点として保持")
    fund = FundamentalsTool(fetcher=lambda t: ({"per": 52.3, "eps": 2.1}, "2026-01-31"))
    fo = await fund.execute(FundamentalsInput(ticker="NVDA", fields=["per", "eps"]))
    print(f"  値      : {fo.data}")
    print(f"  報告期  : fiscal_period = {fo.fiscal_period}（出典の as_of に保持）")
    print(f"  出典    : {[(r.source, r.as_of) for r in fo.source_refs]}")
    print(f"  一次情報: {fo.source_url}")

    # ④ テクニカル：コード計算（LLMに計算させない原則の体現）
    line("④ テクニカル technicals（BALTHASARの素材）：source=computed")
    closes = [float(i) for i in range(1, 80)]
    tech = TechnicalsTool(history_provider=lambda t, d: closes)
    to = await tech.execute(TechnicalsInput(ticker="NVDA"))
    print(f"  シグナル: {to.signals}")
    print(f"  出典    : {[(r.source, r.ref) for r in to.source_refs]}  ← computed=コード計算")

    # ⑤ ニュース：記事ごとに出典(URL)・公開時点
    line("⑤ ニュース news（CASPERの素材）：記事ごとに出典URL・公開時点")
    article = {
        "title": "NVIDIA、データセンター売上が市場予想を上回る",
        "summary": "NVDA のデータセンター部門が前年比+60%。",
        "source": "Bloomberg",
        "url": "https://example.com/nvda-dc",
        "published_at": "2026-05-21T09:30:00",
    }
    no = await NewsTool(fetchers=[lambda _i: [article]]).execute(
        NewsInput(tickers=["NVDA"])
    )
    print(f"  最新時点: data_asof = {no.data_asof}")
    print(f"  出典    : {[(r.source, r.ref, str(r.as_of)) for r in no.source_refs]}")

    print("\n" + "-" * 64)
    print("要点：すべての数値に『どのソースの・いつ時点か』が付き、株価は2経路で")
    print("照合される。これが防御層(B3)が機械照合する材料。LLMは数値を作っていない。")


if __name__ == "__main__":
    asyncio.run(main())
