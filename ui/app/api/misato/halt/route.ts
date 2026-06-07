/**
 * POST /api/misato/halt  body: { action: "on" | "off" | "status" }
 * GET  /api/misato/halt — 状態確認のみ
 *
 * 緊急停止ファイル ~/.trading-agent/HALT のトグル。
 * これが存在する間は MISATO.dispatch() が即座に halt して何も発注しない。
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
    const child = spawn(
      "uv",
      ["run", "python", "scripts/misato_dispatch.py", ...args],
      { cwd: PROJECT_ROOT },
    );
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (b) => {
      stdout += b.toString();
    });
    child.stderr.on("data", (b) => {
      stderr += b.toString();
    });
    child.on("close", (code) => {
      resolve({ ok: code === 0, stdout: stdout.trim(), stderr });
    });
  });
}

export async function GET() {
  const r = await runPython(["--halt-status"]);
  return NextResponse.json({ ok: r.ok, message: r.stdout });
}

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));
  const action = body?.action;
  if (action === "on") {
    const r = await runPython(["--halt-on"]);
    return NextResponse.json({ ok: r.ok, message: r.stdout });
  }
  if (action === "off") {
    const r = await runPython(["--halt-off"]);
    return NextResponse.json({ ok: r.ok, message: r.stdout });
  }
  if (action === "status") {
    const r = await runPython(["--halt-status"]);
    return NextResponse.json({ ok: r.ok, message: r.stdout });
  }
  return NextResponse.json(
    { ok: false, error: "action は on/off/status のいずれか" },
    { status: 400 },
  );
}
