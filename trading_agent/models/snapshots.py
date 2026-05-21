"""portfolio_snapshots は portfolio.py に同居定義（再エクスポートのみ）。

SYSTEM_DESIGN.md §1.2 のファイル分割案では ``snapshots.py`` だが、実装では
``Portfolio`` と密接なため ``portfolio.py`` に同居させた（``models/__init__.py`` も
そちらから import する）。このファイルは設計書のファイル名で探した場合の互換のため、
``table=True`` を再定義せず再エクスポートする（テーブル二重定義を避ける）。
"""

from trading_agent.models.portfolio import PortfolioSnapshot

__all__ = ["PortfolioSnapshot"]
