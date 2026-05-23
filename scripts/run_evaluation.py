"""評価ジョブ実行 CLI（P6・測って上げる）。

評価期日が来た decision を実価格で採点し（hit/miss・R-multiple）、Track Record を表示する。
毎日 or 定期で回す（launchd/cron）。決裁はmoomoo手動だが、評価は機械的に毎日チェックでよい。

  .venv/bin/python scripts/run_evaluation.py

注意：actual_return は (exit−entry)/entry の比率。**entry と exit は同一通貨基準**で揃える必要がある
（record_entry でネイティブ価格を記録した前提＝ここもネイティブ価格で評価）。
"""

from __future__ import annotations

from pathlib import Path

from trading_agent.db import create_all, get_engine
from trading_agent.evaluation.job import evaluate_due_decisions


def _native_price(ticker: str) -> float | None:
    """yfinance の現値（ネイティブ通貨）。JPは .T。失敗は None。"""
    try:
        import yfinance as yf

        sym = f"{ticker}.T" if ticker.split(".")[0].isdigit() else ticker
        return float(yf.Ticker(sym).fast_info.last_price)
    except Exception:
        return None


def main() -> None:
    engine = get_engine(Path("data") / "trading.sqlite")
    create_all(engine)
    n, tr = evaluate_due_decisions(engine, price_lookup=_native_price)
    print(f"=== evaluation: {n} 件を採点 ===")
    label = "暫定" if tr.provisional else "確定"
    hit = f"{tr.hit_rate:.0%}" if tr.hit_rate is not None else "—"
    excess = f"{tr.avg_excess:+.1%}" if tr.avg_excess is not None else "—"
    print(f"Track Record（{label}・n={tr.n}）：命中率 {hit} / 平均R {tr.avg_r:+.2f} / "
          f"平均リターン {tr.avg_return:+.1%} / 対ベンチ超過 {excess}")
    if tr.provisional:
        print("※ サンプルが少なく暫定。複数局面・最低サンプルまでは参考値。")


if __name__ == "__main__":
    main()
