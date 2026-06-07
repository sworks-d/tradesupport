/**
 * POST /api/refresh
 *
 * ダッシュボードの「価格を更新」ボタンが叩く。
 * フロー:
 *   1. MISATO dispatch（dry-run）で DS パイプライン状態を再計算
 *      → close_due → 割り当て案生成 → 昇格判定
 *      （実 fill は走らない＝人間承認なしで自動発注しない安全装置）
 *   2. build_snapshot.py を回して snapshot.json を再生成
 *   3. UI は再 fetch でデータを反映
 *
 * セキュリティ: ローカル運用者専用 UI なので python サブプロセス起動は許容。
 *               外部公開時は再考。
 */

import { spawn } from "node:child_process";
import { join } from "node:path";

import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const PROJECT_ROOT = join(process.cwd(), "..");
const UV = "uv";

function runPython(
  scriptRel: string,
  args: string[] = [],
  extraEnv: Record<string, string> = {},
): Promise<{
  ok: boolean;
  stdout: string;
  stderr: string;
  code: number | null;
}> {
  return new Promise((resolve) => {
    const child = spawn(UV, ["run", "python", scriptRel, ...args], {
      cwd: PROJECT_ROOT,
      env: { ...process.env, ...extraEnv },
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (b) => {
      stdout += b.toString();
    });
    child.stderr.on("data", (b) => {
      stderr += b.toString();
    });
    child.on("close", (code) => {
      resolve({ ok: code === 0, stdout, stderr, code });
    });
    child.on("error", (err) => {
      resolve({ ok: false, stdout, stderr: stderr + String(err), code: -1 });
    });
  });
}

export async function POST(req: Request) {
  const started = Date.now();
  // v2.8: UI から 1 銘柄上限を受け取る（任意）
  const body = await req.json().catch(() => ({}));
  const maxLotJpy = Number(body?.max_lot_jpy ?? NaN);
  const maxLotPct = Number(body?.max_lot_pct ?? NaN);
  const extraEnv: Record<string, string> = {};
  if (Number.isFinite(maxLotPct) && maxLotPct > 0 && maxLotPct <= 1.0) {
    extraEnv.WILLE_MAX_LOT_PCT = String(maxLotPct);
  } else if (Number.isFinite(maxLotJpy) && maxLotJpy > 0) {
    extraEnv.WILLE_MAX_LOT_JPY = String(maxLotJpy);
  }

  // 1) MISATO dispatch (dry-run)
  const misato = await runPython(
    "scripts/misato_dispatch.py",
    ["--budget", "100000", "--json"],
    extraEnv,
  );

  // 2) snapshot 再生成
  const snap = await runPython("scripts/build_snapshot.py", [], extraEnv);

  const elapsed_ms = Date.now() - started;
  return NextResponse.json({
    ok: misato.ok && snap.ok,
    elapsed_ms,
    misato: {
      ok: misato.ok,
      code: misato.code,
      // dry-run なので stdout は plan json。長すぎるとレスポンス重いので 8KB に切り詰め
      stdout: misato.stdout.slice(0, 8192),
      stderr: misato.stderr.slice(-2048),
    },
    snapshot: {
      ok: snap.ok,
      code: snap.code,
      stderr: snap.stderr.slice(-2048),
    },
  });
}
