"""OpenD への実接続を診断する一時スクリプト（口座開通確認用）。

security_firm=FUTUJP（moomoo JP）で、US/JP × SIMULATE/REAL の position 取得を試し、
どの組合せが通るか・保有が見えるかを表示する。確認後は削除してよい。
"""

from __future__ import annotations

import moomoo as ft

HOST, PORT = "127.0.0.1", 11111
SECURITY_FIRM = "FUTUJP"


def main() -> None:
    print(f"connect {HOST}:{PORT} security_firm={SECURITY_FIRM}")
    for market in ["US", "JP"]:
        try:
            ctx = ft.OpenSecTradeContext(
                filter_trdmarket=getattr(ft.TrdMarket, market),
                host=HOST,
                port=PORT,
                security_firm=getattr(ft.SecurityFirm, SECURITY_FIRM),
            )
        except Exception as exc:
            print(f"[{market}] CTX ERROR: {exc}")
            continue
        for env in ["SIMULATE", "REAL"]:
            try:
                ret, data = ctx.position_list_query(
                    trd_env=getattr(ft.TrdEnv, env), refresh_cache=True
                )
                if ret == ft.RET_OK:
                    cols = list(data.columns)
                    print(f"[{market}/{env}] OK rows={len(data)} cols={cols[:8]}")
                    if len(data):
                        keep = [c for c in ("code", "qty", "cost_price", "pl_ratio") if c in cols]
                        print("   ", data[keep].to_dict("records"))
                else:
                    print(f"[{market}/{env}] RET_ERROR: {data}")
            except Exception as exc:
                print(f"[{market}/{env}] QUERY EXC: {exc}")
        ctx.close()


if __name__ == "__main__":
    main()
