/**
 * POST /api/wille/broker-mode  body: { mode: "paper" | "live" }
 * GET                          現在の broker_mode を返す
 *
 * UI サイドバートグルから呼ばれる。data/wille_settings.json に永続化。
 * 設定後 snapshot を再生成して UI に反映。
 */

import { spawn } from "node:child_process";
import { promises as fs } from "node:fs";
import { join } from "node:path";

import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const PROJECT_ROOT = join(process.cwd(), "..");
const SETTINGS_PATH = join(PROJECT_ROOT, "data/wille_settings.json");

function runPython(args: string[]): Promise<{ ok: boolean }> {
  return new Promise((resolve) => {
    const child = spawn("uv", ["run", "python", ...args], { cwd: PROJECT_ROOT });
    child.on("close", (code) => resolve({ ok: code === 0 }));
  });
}

async function readMode(): Promise<string> {
  try {
    const buf = await fs.readFile(SETTINGS_PATH, "utf-8");
    const json = JSON.parse(buf);
    return json.broker_mode === "live" ? "live" : "paper";
  } catch {
    return "paper";
  }
}

async function writeMode(mode: "paper" | "live") {
  await fs.mkdir(join(PROJECT_ROOT, "data"), { recursive: true });
  let json: Record<string, unknown> = {};
  try {
    const buf = await fs.readFile(SETTINGS_PATH, "utf-8");
    json = JSON.parse(buf);
  } catch {
    json = {};
  }
  json.broker_mode = mode;
  await fs.writeFile(SETTINGS_PATH, JSON.stringify(json, null, 2), "utf-8");
}

export async function GET() {
  const mode = await readMode();
  return NextResponse.json({ ok: true, mode });
}

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));
  const mode = body?.mode;
  if (mode !== "paper" && mode !== "live") {
    return NextResponse.json(
      { ok: false, error: "mode must be 'paper' or 'live'" },
      { status: 400 },
    );
  }
  await writeMode(mode);
  // snapshot 再生成（broker_mode 反映）
  const snap = await runPython(["scripts/build_snapshot.py"]);
  return NextResponse.json({ ok: snap.ok, mode });
}
