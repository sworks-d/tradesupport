/**
 * POST /api/refresh-prices
 *
 * 軽量価格更新エンドポイント（v2.8）。
 * - 保有銘柄の current_price と損益のみを更新
 * - yfinance bulk 1 リクエスト / 1-2 秒で完了
 * - WILLE / MISATO / 候補プール再構築なし
 * - 30s/60s/300s の自動ループから呼ぶ想定
 *
 * 重い完全更新は /api/refresh を使う（既存ボタン）。
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
    child.on("error", (err) => resolve({ ok: false, stdout, stderr: stderr + String(err) }));
  });
}

export async function POST() {
  const started = Date.now();
  const r = await runPython(["scripts/refresh_prices.py"]);
  return NextResponse.json({
    ok: r.ok,
    elapsed_ms: Date.now() - started,
    stdout: r.stdout.slice(-2048),
    stderr: r.stderr.slice(-512),
  });
}
