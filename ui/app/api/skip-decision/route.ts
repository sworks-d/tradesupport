/**
 * POST /api/skip-decision
 *
 * 決定を「見送り」= cancelled にする（買わなかった awaiting Decision を畳む）。
 * scripts/skip_decision.py のラッパ。楽天には触れない・手元DBのみ。
 *
 * body: { decision_id: number }
 */

import { spawn } from "node:child_process";
import { join } from "node:path";

import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const PROJECT_ROOT = join(process.cwd(), "..");

function runPython(args: string[]): Promise<{
  ok: boolean;
  stdout: string;
  stderr: string;
}> {
  return new Promise((resolve) => {
    const child = spawn("uv", ["run", "python", ...args], { cwd: PROJECT_ROOT });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (b) => (stdout += b.toString()));
    child.stderr.on("data", (b) => (stderr += b.toString()));
    child.on("close", (code) => resolve({ ok: code === 0, stdout, stderr }));
    child.on("error", (err) =>
      resolve({ ok: false, stdout, stderr: stderr + String(err) }),
    );
  });
}

export async function POST(req: Request) {
  const started = Date.now();
  let body: { decision_id?: number } = {};
  try {
    body = await req.json();
  } catch {
    // 下で弾く
  }
  const decisionId = body.decision_id;
  if (decisionId == null || Number.isNaN(Number(decisionId))) {
    return NextResponse.json(
      { ok: false, error: "decision_id required" },
      { status: 400 },
    );
  }
  const r = await runPython([
    "scripts/skip_decision.py",
    "--decision-id",
    String(Number(decisionId)),
  ]);
  return NextResponse.json({
    ok: r.ok,
    elapsed_ms: Date.now() - started,
    stdout: r.stdout.slice(-512),
    stderr: r.stderr.slice(-512),
  });
}
