/**
 * POST /api/mark-filled
 *
 * 楽天で手動約定した Decision を「手元DB」に記録する（scripts/mark_filled.py のラッパ）。
 * ⚠ 楽天証券には一切アクセスしない。発注はユーザーが楽天アプリで手動済み。
 *    ここでやるのは「買った事実を自分のシステムの帳簿に書く」記録だけ。
 *
 * body: {
 *   decision_id: number,          // 対象 Decision
 *   price?: number,               // 実約定価格（省略時は mark_filled 側で想定価格=entry_price）
 *   shares?: number,              // 実約定株数（省略時は推奨株数）
 *   broker_mode?: "paper"|"live"  // 記録先（既定 paper。live=楽天本番）
 * }
 *
 * 既存の refresh-prices/route.ts と同一の spawn パターンを踏襲（Next 16 で動作実績あり）。
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

  let body: {
    decision_id?: number;
    price?: number;
    shares?: number;
    broker_mode?: string;
  } = {};
  try {
    body = await req.json();
  } catch {
    // 空ボディは下で弾く
  }

  const decisionId = body.decision_id;
  if (decisionId == null || Number.isNaN(Number(decisionId))) {
    return NextResponse.json(
      { ok: false, error: "decision_id required" },
      { status: 400 },
    );
  }

  // 既定は paper（安全側）。明示 live のときだけ楽天本番の帳簿へ。
  const brokerMode = body.broker_mode === "live" ? "live" : "paper";

  const args = [
    "scripts/mark_filled.py",
    "--decision-id",
    String(Number(decisionId)),
    "--broker-mode",
    brokerMode,
  ];
  if (body.price != null) args.push("--price", String(body.price));
  if (body.shares != null) args.push("--shares", String(body.shares));

  const r = await runPython(args);
  return NextResponse.json({
    ok: r.ok,
    elapsed_ms: Date.now() - started,
    broker_mode: brokerMode,
    stdout: r.stdout.slice(-2048),
    stderr: r.stderr.slice(-512),
  });
}
