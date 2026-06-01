"""autoreport/index.html を生成する：日次レポート一覧 + 累積パフォーマンス推移。

build_report.py の実行後に呼ばれる想定。autoreport/ 直下の *.html を走査して、
日付順に並べ、各日の MISATO 報告から抽出した性格別 PnL を時系列でプロット。
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


def scan_daily_reports(autoreport_dir: Path) -> list[dict[str, Any]]:
    """autoreport/daily/*.html を走査して、各日のメタデータ + 性格別 PnL を抽出。

    Returns: [{"date": "2026-05-28", "file": "daily/2026-05-28.html", "personalities": [...]}, ...]
    """
    out: list[dict[str, Any]] = []
    pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})\.html$")
    daily_dir = autoreport_dir / "daily"
    if not daily_dir.exists():
        return out
    for f in sorted(daily_dir.glob("*.html")):
        m = pattern.match(f.name)
        if not m:
            continue
        date = m.group(1)
        text = f.read_text(encoding="utf-8", errors="ignore")
        # ランキング HTML から 性格別 PnL を抽出（簡易・テキストパース）
        # <td>DS/REI</td> ... <td><b>+1.23%</b></td> のような部分を狙う
        personalities: list[dict[str, Any]] = []
        for label in ("DS/REI", "DS/ASUKA", "DS/SHINJI", "DS/KAWORU"):
            pat = re.compile(
                rf"{re.escape(label)}.*?([+-]?\d+\.\d+)%", re.DOTALL
            )
            mm = pat.search(text)
            if mm:
                try:
                    personalities.append(
                        {"label": label, "pnl_pct": float(mm.group(1))}
                    )
                except ValueError:
                    pass
        out.append(
            {"date": date, "file": f"daily/{f.name}", "personalities": personalities}
        )
    return out


def build_index_html(autoreport_dir: Path) -> str:
    """インデックス HTML を文字列で返す。"""
    reports = scan_daily_reports(autoreport_dir)
    # 性格別の時系列データを Chart.js 用に整形
    series_by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in reports:
        for p in r["personalities"]:
            series_by_label[p["label"]].append(
                {"x": r["date"], "y": p["pnl_pct"]}
            )

    # フィードバック集計（あれば読み込む・新パス data/feedback_log.json + 旧パスも fallback）
    feedback_records: list[dict[str, Any]] = []
    for fp in (autoreport_dir / "data" / "feedback_log.json", autoreport_dir / "feedback_log.json"):
        if fp.exists():
            try:
                feedback_records = json.loads(fp.read_text(encoding="utf-8"))
                break
            except Exception:
                continue

    chart_payload = json.dumps(
        {
            "datasets": [
                {
                    "label": label,
                    "data": data,
                    "borderColor": {
                        "DS/REI": "#4a90e2",
                        "DS/ASUKA": "#e74c3c",
                        "DS/SHINJI": "#9b59b6",
                        "DS/KAWORU": "#5d3fd3",
                    }.get(label, "#888"),
                    "backgroundColor": "transparent",
                    "tension": 0.2,
                }
                for label, data in series_by_label.items()
            ]
        },
        ensure_ascii=False,
    )

    rows_html = "\n".join(
        f'<tr><td><a href="{r["file"]}">{r["date"]}</a></td>'
        + "".join(
            f'<td class="{"up" if p["pnl_pct"] >= 0 else "down"}">{p["pnl_pct"]:+.2f}%</td>'
            for p in r["personalities"]
        )
        + "</tr>"
        for r in reversed(reports)
    )

    fb_summary_html = ""
    if feedback_records:
        from trading_agent.reporting.feedback import summarize_feedback

        s = summarize_feedback(feedback_records)
        fb_rows = "\n".join(
            f'<tr><td>DS/{p}</td><td>{stats["n"]}</td><td>{stats["hit_rate"]*100:.0f}%</td>'
            f'<td>{stats["avg_r"]:+.2f}</td></tr>'
            for p, stats in s["by_personality"].items()
        )
        fb_summary_html = f"""
<div class="card">
  <h2>📊 累積フィードバック（評価期日到来分・MAGI 学習データ）</h2>
  <div style="font-size:12px;color:#888;margin-bottom:10px;">
    累計 {s["total_records"]} 件の評価データ。30 件達成で増額ゲート判定へ。
  </div>
  <table>
    <tr><th>機体</th><th>評価件数</th><th>命中率</th><th>平均 R</th></tr>
    {fb_rows}
  </table>
</div>
"""

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>ダミーシステム 検証履歴インデックス</title>
<style>
  body {{ background: #1a1a1f; color: #ece9de; font-family: -apple-system, "Hiragino Sans", sans-serif; padding: 32px 24px; }}
  .wrap {{ max-width: min(96vw, 1400px); margin: 0 auto; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  .subtitle {{ color: #888; font-size: 12px; margin-bottom: 24px; }}
  .card {{ background: #252530; border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 18px 20px; margin-bottom: 16px; }}
  h2 {{ font-size: 14px; letter-spacing: 1px; color: #a8a89e; margin: 0 0 12px; font-weight: 600; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  th, td {{ padding: 8px 10px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.08); }}
  th {{ color: #a8a89e; font-weight: 600; letter-spacing: 0.5px; font-size: 10px; }}
  td a {{ color: #ff8c42; text-decoration: none; }}
  td a:hover {{ text-decoration: underline; }}
  .up {{ color: #4ade80; font-weight: 600; }}
  .down {{ color: #f87171; font-weight: 600; }}
  canvas {{ width: 100% !important; height: 320px !important; max-height: 320px; }}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head>
<body>
<div class="wrap">

<h1>🤖 ダミーシステム — 検証履歴インデックス</h1>
<div class="subtitle">
  生成 {dt.datetime.now().strftime('%Y-%m-%d %H:%M')} ・ 計 {len(reports)} 日のレポート蓄積
</div>

<div class="card">
  <h2>📈 性格別 累積 PnL 推移（%）</h2>
  <canvas id="pnlChart"></canvas>
</div>

{fb_summary_html}

<div class="card">
  <h2>📅 日次レポート一覧</h2>
  <table>
    <tr><th>日付</th><th>DS/REI</th><th>DS/ASUKA</th><th>DS/SHINJI</th><th>DS/KAWORU</th></tr>
    {rows_html}
  </table>
</div>

<script>
const ctx = document.getElementById('pnlChart').getContext('2d');
const data = {chart_payload};
new Chart(ctx, {{
  type: 'line',
  data: data,
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    scales: {{
      x: {{ ticks: {{ color: '#a8a89e' }}, grid: {{ color: 'rgba(255,255,255,0.05)' }} }},
      y: {{ ticks: {{ color: '#a8a89e', callback: v => v + '%' }}, grid: {{ color: 'rgba(255,255,255,0.05)' }} }},
    }},
    plugins: {{
      legend: {{ labels: {{ color: '#ece9de' }} }},
    }},
  }},
}});
</script>

</div>
</body>
</html>"""


def write_index(autoreport_dir: Path) -> Path:
    """index.html を出力。"""
    html = build_index_html(autoreport_dir)
    out = autoreport_dir / "index.html"
    out.write_text(html, encoding="utf-8")
    return out
