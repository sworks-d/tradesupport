"""ダミーシステム自動運用レポート（autoreport/）の生成。

毎日 18:00 launchd で `scripts/build_report.py` 経由で起動し、
`autoreport/YYYY-MM-DD.html` を生成する。
"""

from trading_agent.reporting.builder import build_report_payload, render_html

__all__ = ["build_report_payload", "render_html"]
