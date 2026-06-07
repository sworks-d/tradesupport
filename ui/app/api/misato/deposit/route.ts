/**
 * POST /api/misato/deposit  body: { amount: number }
 *
 * MISATO に入金（seed_jpy 加算）。完了後 snapshot を再生成して UI へ反映。
 */

import { spawn } from "node:child_process";
import { join } from "node:path";

import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const PROJECT_ROOT = join(process.cwd(), "..");

function runPython(args: string[]): Promise<{ ok: boolean; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    const child = spawn("uv", ["run", "python", "scripts/misato_dispatch.py", ...args], {
      cwd: PROJECT_ROOT,
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (b) => (stdout += b.toString()));
    child.stderr.on("data", (b) => (stderr += b.toString()));
    child.on("close", (code) => resolve({ ok: code === 0, stdout: stdout.trim(), stderr }));
  });
}

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));
  const amount = Number(body?.amount ?? 0);
  // v2.8: 負値も受け付ける（払い戻し）
  if (!Number.isFinite(amount) || amount === 0) {
    return NextResponse.json(
      { ok: false, error: "amount は 0 以外の数値が必要（負値は払い戻し）" },
      { status: 400 },
    );
  }
  const r = await runPython(["--deposit", String(amount)]);

  // 入金後 snapshot 再生成（treasury 反映）
  const snap = await new Promise<{ ok: boolean }>((resolve) => {
    const c = spawn("uv", ["run", "python", "scripts/build_snapshot.py"], {
      cwd: PROJECT_ROOT,
    });
    c.on("close", (code) => resolve({ ok: code === 0 }));
  });

  return NextResponse.json({
    ok: r.ok && snap.ok,
    message: r.stdout,
    stderr: r.stderr.slice(-1024),
  });
}
