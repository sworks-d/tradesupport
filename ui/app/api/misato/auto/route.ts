/**
 * POST /api/misato/auto  body: { scope: "master" | "REI" | "ASUKA" | "SHINJI" | "KAWORU", enabled: boolean, hours?: number }
 *
 * 自動売買 ON/OFF。ON 時は既定 24h、`hours` で上書き可。
 * 完了後 snapshot を再生成して UI 反映。
 */

import { spawn } from "node:child_process";
import { join } from "node:path";

import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const PROJECT_ROOT = join(process.cwd(), "..");
const VALID_SCOPES = new Set(["master", "REI", "ASUKA", "SHINJI", "KAWORU"]);

function runPython(args: string[]): Promise<{ ok: boolean; stdout: string }> {
  return new Promise((resolve) => {
    const child = spawn("uv", ["run", "python", ...args], { cwd: PROJECT_ROOT });
    let stdout = "";
    child.stdout.on("data", (b) => (stdout += b.toString()));
    child.on("close", (code) => resolve({ ok: code === 0, stdout: stdout.trim() }));
  });
}

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({}));
  const scope: string | undefined = body?.scope;
  const enabled = Boolean(body?.enabled);
  const hours = Number(body?.hours ?? 24);

  if (!scope || !VALID_SCOPES.has(scope)) {
    return NextResponse.json(
      { ok: false, error: "scope は master / REI / ASUKA / SHINJI / KAWORU のいずれか" },
      { status: 400 },
    );
  }
  if (enabled && (!Number.isFinite(hours) || hours <= 0 || hours > 168)) {
    return NextResponse.json(
      { ok: false, error: "hours は 1-168 の範囲（最大 1 週間）" },
      { status: 400 },
    );
  }

  const flag = enabled ? "--auto-on" : "--auto-off";
  const args = ["scripts/misato_dispatch.py", flag, scope];
  if (enabled) {
    args.push("--auto-hours", String(hours));
  }
  const r = await runPython(args);
  const snap = await runPython(["scripts/build_snapshot.py"]);
  return NextResponse.json({
    ok: r.ok && snap.ok,
    message: r.stdout,
  });
}

export async function GET() {
  const r = await runPython(["scripts/misato_dispatch.py", "--auto-status", "--json"]);
  try {
    return NextResponse.json({ ok: true, data: JSON.parse(r.stdout) });
  } catch {
    return NextResponse.json({ ok: false, error: r.stdout }, { status: 500 });
  }
}
