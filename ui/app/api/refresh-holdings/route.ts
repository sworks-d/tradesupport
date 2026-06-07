/**
 * POST /api/refresh-holdings
 *
 * 約定記録の「保有/損益への反映」用・軽量 snapshot 再生成。
 * build_snapshot.py --light = 候補生成（唯一の LLM 経路）をスキップし、
 * 口座・保有・決裁待ち等を DB + yfinance で作り直す（LLM コスト 0）。
 *
 * 重い完全更新（候補プール再構築・MAGI）は /api/refresh。
 * 軽量な価格のみは /api/refresh-prices。
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

export async function POST() {
  const started = Date.now();
  // リーン版（口座/保有/決裁待ちのみ・~3秒）。重い完全再生は build_snapshot --light / /api/refresh。
  const r = await runPython(["scripts/refresh_holdings.py"]);
  return NextResponse.json({
    ok: r.ok,
    elapsed_ms: Date.now() - started,
    stdout: r.stdout.slice(-512),
    stderr: r.stderr.slice(-512),
  });
}
