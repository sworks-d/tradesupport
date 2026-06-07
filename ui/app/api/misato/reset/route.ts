/**
 * POST /api/misato/reset
 *
 * MISATO 預かり金と全機の配分をリセット。完了後 snapshot を再生成。
 */

import { spawn } from "node:child_process";
import { join } from "node:path";

import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const PROJECT_ROOT = join(process.cwd(), "..");

function runPython(args: string[]): Promise<{ ok: boolean; stdout: string }> {
  return new Promise((resolve) => {
    const child = spawn("uv", ["run", "python", ...args], { cwd: PROJECT_ROOT });
    let stdout = "";
    child.stdout.on("data", (b) => (stdout += b.toString()));
    child.on("close", (code) => resolve({ ok: code === 0, stdout: stdout.trim() }));
  });
}

export async function POST() {
  const r = await runPython(["scripts/misato_dispatch.py", "--reset-treasury"]);
  const snap = await runPython(["scripts/build_snapshot.py"]);
  return NextResponse.json({ ok: r.ok && snap.ok, message: r.stdout });
}
