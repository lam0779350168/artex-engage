// ARTEX auto-start + real-time tool-call hook.
// On start: ensure ARTEX is up. On every tool execution: append raw event to the
// current engagement's hash-chained JSONL and mirror summary rows into Postgres.
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const PTR = "/root/ARTEX/data/.current_engagement";
const ERR = "/root/ARTEX/data/hook-error.log";
const STORE = path.join(path.dirname(fileURLToPath(import.meta.url)), "bin", "artex_store.py");

function logErr(msg) {
  try {
    fs.appendFileSync(ERR, new Date().toISOString() + " " + msg + "\n");
  } catch {}
}

function currentEng() {
  try {
    const e = fs.readFileSync(PTR, "utf8").trim();
    if (e) return e;
  } catch {}
  return "live-" + new Date().toISOString().slice(0, 10);
}

function safe(v) {
  try {
    return JSON.parse(JSON.stringify(v));
  } catch {
    return String(v);
  }
}

function persist(event) {
  const payload = JSON.stringify({
    type: "tool_call",
    ts: new Date().toISOString(),
    ...event,
  });
  const r = spawn("python3", [STORE, "record", currentEng()], {
    stdio: ["pipe", "ignore", "pipe"],
  });
  r.stdin.write(payload);
  r.stdin.end();
  r.stderr.on("data", (d) => logErr(String(d)));
  r.on("error", (e) => logErr(String(e)));
}

// V2: ctx.tool.hook("execute.after", e => ...)
function afterV2(e) {
  try {
    persist({
      summary: `${e.tool || "tool"} ${e.status || ""}`.trim(),
      tool: e.tool,
      status: e.status,
      session_id: e.sessionID || e.sessionId || null,
      call_id: e.id || e.callID || null,
      input: safe(e.input),
      result: safe(e.result ?? e.output),
      error: e.error ? safe(e.error) : null,
    });
  } catch (err) {
    logErr("afterV2: " + err.message);
  }
}

// V1 fallback: "tool.execute.after"(input, output)
function afterV1(input, output) {
  try {
    persist({
      summary: `${input.tool || "tool"}`.trim(),
      tool: input.tool,
      status: null,
      session_id: input.sessionID || null,
      call_id: input.callID || null,
      input: safe(output.args ?? input.args),
      result: safe(output.output),
      metadata: safe(output.metadata),
    });
  } catch (err) {
    logErr("afterV1: " + err.message);
  }
}

export default {
  id: "artex-engage",
  async setup(ctx) {
    try {
      const r = spawnSync("python3", [STORE, "init"], {
        encoding: "utf8",
        timeout: 180000,
      });
      console.log(
        "[artex-engage] " + ((r.stdout || r.stderr || "").trim() || "init ok"),
      );
    } catch (e) {
      logErr("init: " + e.message);
      console.error("[artex-engage] startup failed:", e.message);
    }
    try {
      await ctx.tool?.hook?.("execute.after", afterV2);
    } catch (e) {
      logErr("hook-v2: " + e.message);
    }
  },
  "tool.execute.after": afterV1,
};
