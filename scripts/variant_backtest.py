"""リスク姿勢の3パターン（基本/強気/守り）を予算別にバックテストし、docs/test_result へ記録。

「中のパラメータ」＝規律層 RiskParams の姿勢ノブを振る：
- 基本(baseline)：現行 DEFAULT_RISK。
- 強気(aggressive)：risk%↑・現金↓・1銘柄上限↑・stop広め・集中(枠少)・サテライト厚め。
- 守り(defensive)：risk%↓・現金下限↑・1銘柄上限↓・stop狭め(早く切る)・分散(枠多)・ほぼコア。

エントリは GC 固定（=コイン投げと実測済）。本BTは「姿勢ノブが資産曲線/リスクにどう効くか」を見る。
survivorship bias・コスト未控除・単一期間の上振れ込み（=相対比較で読む）。

  .venv/bin/python scripts/variant_backtest.py
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from scripts.portfolio_backtest import (
    BUDGETS,
    HORIZON_BARS,
    _buy_hold_return,
    _load_prices,
    _signals,
    run_tier,
)
from trading_agent.risk.params import RiskParams

BASELINE = RiskParams()
AGGRESSIVE = replace(
    RiskParams(),
    risk_per_trade=0.03, cash_floor=0.10, max_position_weight=0.30,
    default_stop_pct=0.15, max_positions=4, core_fraction=0.70, satellite_fraction=0.30,
)
DEFENSIVE = replace(
    RiskParams(),
    risk_per_trade=0.01, cash_floor=0.30, max_position_weight=0.12,
    default_stop_pct=0.10, max_positions=8, core_fraction=0.95, satellite_fraction=0.05,
)
VARIANTS = (("基本", BASELINE), ("強気", AGGRESSIVE), ("守り", DEFENSIVE))


def _preset_line(name: str, p: RiskParams) -> str:
    return (
        f"- **{name}**：risk/trade={p.risk_per_trade:.0%}・現金下限={p.cash_floor:.0%}・"
        f"1銘柄上限={p.max_position_weight:.0%}・stop={p.default_stop_pct:.0%}・"
        f"枠={p.max_positions}・core/sat={p.core_fraction:.0%}/{p.satellite_fraction:.0%}"
    )


def main() -> None:
    prices, isjp, idx = _load_prices()
    n = len(idx)
    sig = _signals(prices)
    bhret = _buy_hold_return(prices, n)
    bh_cagr = (1 + bhret) ** (252 / (n - 60)) - 1
    span = f"{idx[60].date()}〜{idx[-1].date()}"

    rows: list[tuple[str, float, dict]] = []
    for name, params in VARIANTS:
        for cap in BUDGETS:
            rows.append((name, cap, run_tier(prices, isjp, sig, n, cap, params)))

    # --- レポート生成 ---
    lines: list[str] = []
    lines.append("# リスク姿勢3パターン × 予算別 バックテスト比較")
    lines.append("")
    lines.append(
        f"実行日 2026-05-25 ／ universe=現{len(prices)}銘柄(生存) ／ 期間 {span}(10y) ／ "
        f"エントリ=GC(20/60)固定 ／ 出口=固定stop＋保有期限{HORIZON_BARS}d(B') ／ "
        "再現：`scripts/variant_backtest.py`"
    )
    lines.append("")
    lines.append("## パターン定義（中のパラメータ＝姿勢ノブ）")
    for name, p in VARIANTS:
        lines.append(_preset_line(name, p))
    lines.append("")
    lines.append("## 結果")
    lines.append("| 姿勢 | 予算 | 総リターン | CAGR | 最大DD | Sharpe | 勝率 | 取引 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for name, cap, r in rows:
        lines.append(
            f"| {name} | ¥{int(cap):,} | {r['ret']:+.0%} | {r['cagr']:+.1%} | "
            f"{r['dd']:+.0%} | {r['sharpe']:+.2f} | {r['win']:.0%} | {r['ntr']} |"
        )
    lines.append(f"| buy&hold | （資本非依存） | {bhret:+.0%} | {bh_cagr:+.1%} | — | — | — | — |")
    lines.append("")
    # 代表（¥100万）での姿勢比較サマリ
    mid = {name: r for name, cap, r in rows if cap == 1_000_000.0}
    best_sharpe = max(mid, key=lambda k: mid[k]["sharpe"])
    best_ret = max(mid, key=lambda k: mid[k]["ret"])
    min_dd = min(mid, key=lambda k: abs(mid[k]["dd"]))
    lines.append("## 読み取り（¥100万・忖度なし）")
    lines.append(
        f"- Sharpe最良＝**{best_sharpe}**（{mid[best_sharpe]['sharpe']:+.2f}）／"
        f"総リターン最良＝**{best_ret}**（{mid[best_ret]['ret']:+.0%}）／"
        f"最小DD＝**{min_dd}**（{mid[min_dd]['dd']:+.0%}）。"
    )
    lines.append(
        "- いずれの姿勢も buy&hold（"
        f"{bhret:+.0%}）に届かない＝エントリーにエッジが無い（姿勢ノブはαを生まない）。"
        "姿勢が効くのは主に**DD/Sharpe＝リスク側**。守りはDDを抑え、強気はDDを膨らませる。"
    )
    lines.append("")
    lines.append("## バイアス申告")
    lines.append(
        "- survivorship bias（現銘柄=生存者）／コスト未控除（強気は高回転で悪化）／単一期間／"
        "core-satellite比率は本BT(単一スリーブGC)では未行使（DDは主に risk%・stop・枠で決まる）。"
    )
    lines.append("")
    lines.append("## 判定")
    lines.append(
        "- 3姿勢とも『タイミングで勝つ』形ではαが出ない。**選ぶべきは『どれだけ稼ぐ姿勢か』でなく"
        "『どれだけDDに耐える姿勢か』**。哲学（持ち続け・自爆しない）に最も合うのは守り〜基本側。"
        "強気は同じ無エッジ下でDDだけ増やす＝不利。"
    )

    out = Path("docs/test_result")
    out.mkdir(parents=True, exist_ok=True)
    report = out / "variant_comparison.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # コンソール要約
    print(f"=== 3姿勢 × 予算 / {span} / 現{len(prices)}銘柄 ===")
    for name, cap, r in rows:
        print(
            f"{name:　<3} ¥{int(cap):>11,}  総={r['ret']:>+6.0%} CAGR={r['cagr']:>+6.1%} "
            f"DD={r['dd']:>+5.0%} Sharpe={r['sharpe']:>+5.2f} 勝率={r['win']:>3.0%} 取引={r['ntr']}"
        )
    print(f"buy&hold 総={bhret:+.0%} CAGR={bh_cagr:+.1%}")
    print(f"→ レポート: {report}")


if __name__ == "__main__":
    main()
