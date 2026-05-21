"""FastAPI エントリーポイント（SYSTEM_DESIGN.md §1.2）。

API ルーターを束ね、静的 UI（ui/static）を配信する。
起動：``uv run uvicorn trading_agent.main:app --port 8000``
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from trading_agent.api.routes_dashboard import router as dashboard_router
from trading_agent.utils.logger import configure_logging, log

_UI_DIR = Path(__file__).resolve().parents[1] / "ui" / "static"


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Trading Agent", version="0.1.0")

    # API は静的配信より先に登録（/api/* がキャッチオールに飲まれないように）
    app.include_router(dashboard_router)

    if _UI_DIR.exists():
        app.mount("/", StaticFiles(directory=_UI_DIR, html=True), name="ui")
        log.info("ui_mounted", path=str(_UI_DIR))
    else:
        log.warning("ui_dir_missing", path=str(_UI_DIR))

    return app


app = create_app()
