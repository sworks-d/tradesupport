"""Forward 診断ハーネス（codex #4）の実行スクリプト.

約定済 official(paper) 決定の forward mark-to-market（対 TOPIX 超過・5/20/40/60 営業日）を
計算し、autoreport/forward/YYYY-MM-DD.json に archive + 標準出力。

**評価期日(60日)を待たない早期診断**。gate を前倒しで通すためでなく、観測空白(6月約定→8月評価)の
間に signal_tags / exposure posture の効きを仮説棄却するため。read-only（売買は変えない）。

実行: .venv/bin/python scripts/forward_diagnosis.py [--json]
コスト: yfinance バッチ取得のみ（無料・LLM 非関与）。約定 ticker + TOPIX の価格系列を取得。
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from sqlalchemy.engine import Engine

from trading_agent.db import get_engine
from trading_agent.reporting.forward_diagnosis import compute_forward_diagnosis
from trading_agent.utils.time_utils import today_jst


def forward_diagnosis_enabled(engine: Engine) -> bool:
    """定期化ゲート（A+C 監査）。Setting forward_diagnosis_enabled=true のときだけ True。

    既定 OFF。構築期間中の自動ネット実行（yfinance）を防ぐ。ユーザーが構築完了を宣言して
    Setting を true にするまで runner は no-op（--force で手動 bypass 可）。
    """
    import json as _json

    from sqlmodel import Session

    from trading_agent.models.settings import Setting

    with Session(engine) as s:
        row = s.get(Setting, "forward_diagnosis_enabled")
    if row is None:
        return False
    try:
        return bool(_json.loads(row.value))
    except Exception:
        return False


def _yfinance_series_fetcher(tickers: list[str], start: dt.date) -> dict[str, list[float]]:
    """yfinance で start 日以降の調整後終値系列を一括取得（index 0 = start 以降の最初の営業日）。"""
    import yfinance as yf

    from trading_agent.mcp_tools.fundamentals import to_yfinance_symbol

    sym_map = {to_yfinance_symbol(t): t for t in tickers}
    out: dict[str, list[float]] = {}
    try:
        df = yf.download(
            list(sym_map.keys()),
            start=start.isoformat(),
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
        )
    except Exception:
        return out
    if df is None or df.empty:
        return out
    for sym, t in sym_map.items():
        try:
            series = df[sym]["Close"] if len(sym_map) > 1 else df["Close"]
            closes = [float(v) for v in series.dropna().tolist()]
            if closes:
                out[t] = closes
        except Exception:
            continue
    return out


def main() -> None:
    as_of = today_jst()
    engine = get_engine("data/trading.sqlite")
    # A+C（監査）: 定期化は既定 OFF。Setting forward_diagnosis_enabled=true で明示 activate
    # するまで no-op（構築期間中の自動 yfinance 実行を防ぐ）。手動実行は --force で bypass。
    if "--force" not in sys.argv and not forward_diagnosis_enabled(engine):
        print(
            "forward_diagnosis: disabled。Setting forward_diagnosis_enabled=true で定期 activate、"
            "または --force で手動実行。"
        )
        return
    result = compute_forward_diagnosis(
        engine, series_fetcher=_yfinance_series_fetcher, today=as_of
    )

    out_dir = Path("autoreport/forward")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{as_of.isoformat()}.json"
    payload = {"as_of": as_of.isoformat(), **result}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if "--json" in sys.argv:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"=== Forward 診断 {as_of}（約定 {result['decisions_n']} 件・read-only）===")
    print("【全体】対 TOPIX 超過（営業日 horizon）")
    for h, d in result["overall"].items():
        if d["n"]:
            print(f"  {h}d: n={d['n']} 勝率={d['hit_rate']:.0%} 平均超過={d['avg_excess']:+.2%}")
    if result["by_tag"]:
        print("【tag 別】")
        for tag, hd in result["by_tag"].items():
            cells = [f"{h}d:n{d['n']}/{d['avg_excess']:+.1%}" for h, d in hd.items() if d["n"]]
            if cells:
                print(f"  {tag}: " + " ".join(cells))
    print(f"\n注: {result['note']}")
    print(f"archive: {out_path}")


if __name__ == "__main__":
    main()
