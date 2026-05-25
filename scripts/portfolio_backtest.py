"""ポートフォリオ・バックテスト（「GENDOの推奨を全部実行したら資産曲線がどうなるか」）。

規律層ごと（R-mult サイジング・枠数上限・現金下限・固定stop＋保有期限の出口=B'）を実運用に
忠実に1本の資産曲線で評価する。予算3水準で `params_for_account()`（逓減）を効かせる。

正直な限界（レポートにも明記）：
- エントリは価格ベース（GC=ゴールデンクロス）＝既に「コイン投げ（勝率≈50%）」と実測済。本BTは
  「規律と出口が、エッジ無しエントリーでも予算別にどう振る舞うか」を測る（αの測定ではない）。
- 現universe=今日まで生存した銘柄＝survivorship bias で全数値が上振れ。コスト未控除。単一期間。

  .venv/bin/python scripts/portfolio_backtest.py            # 標準（10y・GC・stop12%・horizon120d）
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
from sqlmodel import Session, col, select

from trading_agent.db import create_all, get_engine
from trading_agent.models.universe import Universe
from trading_agent.portfolio.sizing import recommend_position
from trading_agent.risk.params import params_for_account

warnings.filterwarnings("ignore")

PERIOD = "10y"
STOP_PCT = 0.12
HORIZON_BARS = 120  # 中期：保有期限（B' time-exit）
BUDGETS = (100_000.0, 1_000_000.0, 10_000_000.0)


def _load_prices() -> tuple[dict[str, np.ndarray], dict[str, bool], list]:
    import yfinance as yf

    engine = get_engine(Path("data") / "trading.sqlite")
    create_all(engine)
    with Session(engine) as s:
        rows = s.exec(select(Universe).where(col(Universe.is_active)))
        uni = [(u.ticker, u.market) for u in rows]
    syms = [f"{t}.T" if m == "JP" else t for t, m in uni]
    mkt = {(f"{t}.T" if m == "JP" else t): m for t, m in uni}
    close = yf.download(syms, period=PERIOD, auto_adjust=True, progress=False)["Close"]
    fx = yf.download("JPY=X", period=PERIOD, auto_adjust=True, progress=False)["Close"]
    usdjpy = fx.squeeze().reindex(close.index).ffill().bfill()
    syms = [s for s in syms if s in close.columns]
    pj = close.copy()
    for s in syms:
        if mkt[s] == "US":
            pj[s] = close[s].values * usdjpy.values
    pj = pj.ffill()  # 保有中の欠損は直前値で評価（NaNで建玉を消さない＝Config Bバグ修正）
    prices = {s: pj[s].to_numpy() for s in syms}
    isjp = {s: mkt[s] == "JP" for s in syms}
    return prices, isjp, pj.index


def _signals(prices: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    import pandas as pd

    sig = {}
    for s, arr in prices.items():
        ser = pd.Series(arr)
        s20, s60 = ser.rolling(20).mean(), ser.rolling(60).mean()
        sig[s] = ((s20.shift(1) <= s60.shift(1)) & (s20 > s60)).to_numpy()
    return sig


def _mtm(pos: dict, prices: dict, i: int) -> float:
    """保有の時価（NaNは直前値ffill済なので基本出ない／念のため除外）。"""
    return sum(p["sh"] * prices[s][i] for s, p in pos.items() if not np.isnan(prices[s][i]))


def run_tier(prices, isjp, sig, n, capital, params) -> dict:
    """指定 RiskParams で規律フルのポートフォリオを再生し、指標を返す。"""
    stop = params.default_stop_pct  # 出口stop＝サイジングstop＝variantのdefault_stop_pct
    cash = capital
    pos: dict[str, dict] = {}
    eqs: list[float] = []
    trades: list[float] = []
    syms = list(prices)
    for i in range(60, n):
        eq = cash + _mtm(pos, prices, i)
        # 出口：固定stop or 保有期限（B'）
        for s in list(pos):
            px = prices[s][i]
            if np.isnan(px):
                continue
            p = pos[s]
            if px <= p["stop"] or (i - p["i"]) >= HORIZON_BARS:
                cash += p["sh"] * px
                trades.append(px / p["ent"] - 1.0)
                del pos[s]
        # 入口：GC→G層サイジング（指定params）→枠/現金が許せば建てる
        for s in syms:
            if len(pos) >= params.max_positions:
                break
            if s in pos:
                continue
            px = prices[s][i]
            if np.isnan(px) or not sig[s][i]:
                continue
            rec = recommend_position(
                price_jpy=float(px), total_assets_jpy=eq, cash_jpy=cash,
                is_jp=isjp[s], stop_pct=stop, params=params,
            )
            cost = rec.shares * px
            if rec.shares > 0 and cost <= cash:
                cash -= cost
                pos[s] = {"sh": rec.shares, "ent": px, "i": i, "stop": px * (1 - stop)}
        eqs.append(cash + _mtm(pos, prices, i))
    e = np.array(eqs)
    days = len(e)
    peak = np.maximum.accumulate(e)
    dd = float(((e - peak) / peak).min())
    dr = np.diff(e) / e[:-1]
    sharpe = float(dr.mean() / dr.std() * np.sqrt(252)) if dr.std() > 0 else 0.0
    win = sum(t > 0 for t in trades) / len(trades) if trades else 0.0
    return {
        "capital": capital, "risk_pct": params.risk_per_trade, "max_pos": params.max_positions,
        "ret": float(e[-1] / capital - 1),
        "cagr": float((e[-1] / capital) ** (252 / days) - 1),
        "dd": dd, "sharpe": sharpe, "win": win, "ntr": len(trades),
    }


def _buy_hold_return(prices: dict, n: int) -> float:
    bh = []
    for a in prices.values():
        j0 = 60
        while j0 < n and np.isnan(a[j0]):
            j0 += 1
        if j0 < n and not np.isnan(a[-1]):
            bh.append(a[-1] / a[j0] - 1)
    return float(np.mean(bh)) if bh else 0.0


def main() -> None:
    prices, isjp, idx = _load_prices()
    n = len(idx)
    sig = _signals(prices)
    bhret = _buy_hold_return(prices, n)
    span = f"{idx[60].date()}〜{idx[-1].date()}"
    print(f"universe={len(prices)} 期間={span} stop={STOP_PCT:.0%} horizon={HORIZON_BARS}d")
    head = f"{'予算':>12} {'risk%':>6} {'枠':>3} {'総ﾘﾀｰﾝ':>8} {'CAGR':>7} "
    print(head + f"{'最大DD':>7} {'Sharpe':>7} {'勝率':>5} {'取引':>5}")
    for cap in BUDGETS:
        r = run_tier(prices, isjp, sig, n, cap, params_for_account(cap))
        row = f"{int(cap):>12,} {r['risk_pct']:>5.1%} {r['max_pos']:>3} "
        row += f"{r['ret']:>+8.0%} {r['cagr']:>+7.1%} {r['dd']:>+7.0%} "
        print(row + f"{r['sharpe']:>+7.2f} {r['win']:>5.0%} {r['ntr']:>5}")
    bh_cagr = (1 + bhret) ** (252 / (n - 60)) - 1
    print(f"[ベンチ]等加重buy&hold 総={bhret:+.0%} CAGR={bh_cagr:+.1%}（資本非依存）")
    print("注：survivorship bias で上振れ／コスト未控除／価格onlyエントリー=コイン投げ／単一期間。")


if __name__ == "__main__":
    main()
