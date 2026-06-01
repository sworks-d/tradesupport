"""バックテスト CLI（v2.10 B1）。

使い方:
  # momentum 戦略を 2020-2024 でバックテスト
  .venv/bin/python scripts/backtest.py --start 2020-01-01 --end 2024-12-31

  # 戦略・期間・銘柄数を指定
  .venv/bin/python scripts/backtest.py \\
    --start 2019-01-01 --end 2024-12-31 \\
    --strategy momentum --n-holdings 10 \\
    --capital 1000000

  # 全戦略を一気に比較
  .venv/bin/python scripts/backtest.py --start 2019-01-01 --end 2024-12-31 --compare
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from sqlmodel import Session, col, select

from trading_agent.backtest.engine import (
    BacktestConfig,
    BacktestResult,
    fetch_price_history_bulk,
    run_backtest,
)
from trading_agent.db import get_engine
from trading_agent.models.universe import Universe


def get_universe_tickers(
    engine, max_n: int = 200, size_bucket: str = "large"
) -> list[str]:
    """Universe から active な日本株を取得。

    size_bucket:
      - "large": 時価総額上位（既存挙動）
      - "small": 時価総額下位（中小型成長検証用）
      - "all":   全 active を時価総額昇順
    """
    with Session(engine) as s:
        stmt = select(Universe).where(col(Universe.is_active)).where(
            col(Universe.market) == "JP"
        )
        if size_bucket == "large":
            stmt = stmt.order_by(col(Universe.market_cap_jpy).desc())
        elif size_bucket == "small":
            # 時価総額昇順、ただし最小値 (¥10B 未満) は出来高薄で取引困難なので除外
            stmt = stmt.where(col(Universe.market_cap_jpy) >= 1e10).order_by(
                col(Universe.market_cap_jpy).asc()
            )
        else:
            stmt = stmt.order_by(col(Universe.market_cap_jpy).asc())
        rows = list(s.exec(stmt).all())
    return [u.ticker for u in rows[:max_n]]


def print_result(result: BacktestResult, label: str = "") -> None:
    """結果を整形表示。"""
    print()
    print(f"=== {label or result.config.strategy_name.upper()} ===")
    print(f"  期間          : {result.config.start_date} 〜 {result.config.end_date}")
    print(f"  初期資本       : ¥{result.config.initial_capital:,.0f}")
    print(f"  最終資産       : ¥{result.final_equity:,.0f}")
    sign = "+" if result.total_return_pct >= 0 else ""
    print(f"  累積リターン   : {sign}{result.total_return_pct*100:.2f}%")
    print(f"  年率リターン   : {sign}{result.annual_return_pct*100:.2f}%")
    print(f"  Sharpe Ratio  : {result.sharpe:.2f}")
    print(f"  Max DD        : {result.max_drawdown_pct*100:.2f}%")
    print(f"  Calmar Ratio  : {result.calmar:.2f}")
    print(f"  取引回数       : {result.n_trades}")
    print(f"  勝率          : {result.win_rate*100:.1f}%")
    alpha = result.alpha_vs_benchmark()
    if alpha is not None:
        sign_a = "+" if alpha >= 0 else ""
        print(f"  TOPIX 超過 α  : {sign_a}{alpha*100:.2f}%/年")


def main() -> int:
    p = argparse.ArgumentParser(description="バックテスト CLI（B1）")
    p.add_argument("--start", type=str, default="2020-01-01")
    p.add_argument("--end", type=str, default="2024-12-31")
    p.add_argument(
        "--strategy",
        type=str,
        default="momentum",
        choices=["momentum", "lowvol", "reversal"],
    )
    p.add_argument("--n-holdings", type=int, default=10)
    p.add_argument("--capital", type=float, default=1_000_000)
    p.add_argument("--max-universe", type=int, default=200, help="universe 上限（時間短縮）")
    p.add_argument(
        "--size",
        type=str,
        default="large",
        choices=["large", "small", "all"],
        help="universe の size bucket（large=大型 / small=中小型成長 / all=全銘柄）",
    )
    p.add_argument("--compare", action="store_true", help="全戦略を比較")
    p.add_argument(
        "--out-of-sample",
        action="store_true",
        help="in-sample (前半) / out-of-sample (後半) 分割で過学習判定",
    )
    args = p.parse_args()

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)

    engine = get_engine(Path("data") / "trading.sqlite")
    tickers = get_universe_tickers(engine, max_n=args.max_universe, size_bucket=args.size)
    print(f"Universe: {len(tickers)} 銘柄 (size={args.size})")
    print(f"期間: {start} 〜 {end}")

    # 価格データを 1 度だけ取得して各戦略で再利用
    warmup_start = start - dt.timedelta(days=400)
    print(f"価格データ取得中 ({warmup_start} 〜 {end})...")
    price_df = fetch_price_history_bulk(tickers, warmup_start, end)
    print(f"取得済 銘柄数: {len(price_df.columns)}")

    def _run_one(strategy: str, s_date: dt.date, e_date: dt.date, label: str = "") -> None:
        cfg = BacktestConfig(
            start_date=s_date,
            end_date=e_date,
            initial_capital=args.capital,
            n_holdings=args.n_holdings,
            strategy_name=strategy,
        )
        result = run_backtest(tickers, cfg, price_df=price_df)
        print_result(result, label=label or strategy)

    strategies = ["momentum", "lowvol", "reversal"] if args.compare else [args.strategy]

    if args.out_of_sample:
        # 期間を半分に分割（M9: 過学習判定）
        mid_days = (end - start).days // 2
        mid = start + dt.timedelta(days=mid_days)
        print(f"\n>> M9: in-sample (前半 {start}-{mid}) / out-of-sample (後半 {mid}-{end}) 分割")
        for strategy in strategies:
            _run_one(strategy, start, mid, label=f"{strategy} IN-SAMPLE")
            _run_one(strategy, mid, end, label=f"{strategy} OUT-OF-SAMPLE")
    else:
        for strategy in strategies:
            _run_one(strategy, start, end, label=strategy)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
