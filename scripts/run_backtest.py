"""バックテスト CLI（価格ベース・先読みなし）。「規律が過去でプラス期待だったか」を測る。

universe 全銘柄に GCシグナル＋R-mult＋損切りを過去株価で再生し、合算 Track Record を出す。
財務(MELCHIOR)は point-in-time が要るため含めない（look-ahead回避＝研究 領域4）。

  .venv/bin/python scripts/run_backtest.py [--period 5y] [--hold 60] [--stop 0.12]

注意：単一期間・現universe の再生は**生存者バイアス**を含む（今いる銘柄＝生き残り）。
最低サンプル・複数局面まで暫定。「実弾の前に規律の期待値を粗く見る」用途。
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlmodel import Session, col, select

from trading_agent.db import create_all, get_engine
from trading_agent.evaluation.backtest import backtest_signal, sma_cross_signal
from trading_agent.evaluation.metrics import EvalResult, build_track_record
from trading_agent.models.universe import Universe


def _arg(flag: str, default: str) -> str:
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def main() -> None:
    period = _arg("--period", "5y")
    hold = int(_arg("--hold", "60"))
    stop = float(_arg("--stop", "0.12"))
    target = float(_arg("--target", "0.20"))

    import yfinance as yf

    engine = get_engine(Path("data") / "trading.sqlite")
    create_all(engine)
    with Session(engine) as s:
        tickers = [
            (u.ticker, u.market)
            for u in s.exec(select(Universe).where(col(Universe.is_active)))
        ]

    print(f"=== backtest GC20/60 hold={hold} stop={stop:.0%} target={target:.0%} {period} ===")
    all_results: list[EvalResult] = []
    sig = sma_cross_signal(20, 60)
    for ticker, market in tickers:
        sym = f"{ticker}.T" if market == "JP" else ticker
        try:
            hist = yf.Ticker(sym).history(period=period)
            prices = [float(x) for x in hist["Close"].tolist()]
        except Exception:
            continue
        trades, tr = backtest_signal(
            prices, signal_fn=sig, hold_bars=hold, stop_pct=stop, target_return=target
        )
        if tr.n:
            print(f"  {ticker:6} trades={tr.n:2} 命中率={tr.hit_rate} 平均R={tr.avg_r:+.2f}")
            all_results += [
                EvalResult(
                    actual_return=t.r_multiple * stop, r_multiple=t.r_multiple,
                    outcome=t.outcome,
                )
                for t in trades
            ]

    combined = build_track_record(all_results)
    label = "暫定" if combined.provisional else "確定"
    hit = f"{combined.hit_rate:.0%}" if combined.hit_rate is not None else "—"
    print(f"\n=== 合算（{label}・n={combined.n}）：命中率 {hit} / 平均R {combined.avg_r:+.2f} / "
          f"平均リターン {combined.avg_return:+.1%} ===")
    print("※ 生存者バイアス・単一局面の粗い指標。実弾前の目安であって将来を保証しない。")


if __name__ == "__main__":
    main()
