"""forward runner（forward_diagnosis 定期実行）の ON/OFF を settings 経由で切り替える。

状態表示: .venv/bin/python scripts/set_forward_runner.py
ON:       .venv/bin/python scripts/set_forward_runner.py --on
OFF:      .venv/bin/python scripts/set_forward_runner.py --off

Setting `forward_diagnosis_enabled`（bool）を upsert する。ON にすると launchd
com.tradesupport.forward-diagnosis.plist（16:30 JST）が forward 診断を実行し、
autoreport/forward/YYYY-MM-DD.json を更新する（read-only 測定・売買不変・LLM 非関与=¥0）。
OFF に戻せば runner は no-op。構築完了の宣言はユーザーのみ（既定 OFF）。
"""

from __future__ import annotations

import json
import sys

from sqlmodel import Session

from trading_agent.db import get_engine
from trading_agent.models._common import utcnow
from trading_agent.models.settings import Setting

_KEY = "forward_diagnosis_enabled"


def main() -> None:
    engine = get_engine("data/trading.sqlite")

    if "--on" in sys.argv or "--off" in sys.argv:
        enabled = "--on" in sys.argv
        with Session(engine) as s:
            row = s.get(Setting, _KEY)
            if row is None:
                row = Setting(
                    key=_KEY,
                    value=json.dumps(enabled),
                    value_type="bool",
                    category="schedule",
                    description="forward runner（forward_diagnosis 定期実行）の有効化フラグ（既定 OFF）",
                )
            else:
                row.value = json.dumps(enabled)
                row.updated_at = utcnow()
            row.updated_by = "user"
            s.add(row)
            s.commit()
        print(f"✅ forward_diagnosis_enabled = {enabled}")

    with Session(engine) as s:
        row = s.get(Setting, _KEY)
    state = "(未設定=OFF)" if row is None else f"{row.value} (updated_at={row.updated_at})"
    print(f"現在の forward_diagnosis_enabled: {state}")


if __name__ == "__main__":
    main()
