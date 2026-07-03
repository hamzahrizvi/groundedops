// dev.mjs — one command to run the whole app in development.
//
//   npm run dev
//
// Starts the FastAPI backend (single process, NO --reload so it can be
// killed cleanly) and the Vite frontend together, and shuts the backend
// down when you press Ctrl+C, close the terminal, or Vite exits.
//
// Config (env vars, all optional):
//   PYTHON       python executable      (default: "python")
//   BACKEND_DIR  where main.py lives     (default: ".." — frontend is in src/)
//   BACKEND_PORT backend port            (default: "8000")
//
// Examples:
//   PYTHON=.venv/Scripts/python npm run dev      (Windows venv)
//   PYTHON=.venv/bin/python npm run dev          (macOS/Linux venv)
//   BACKEND_DIR=../server npm run dev

import { spawn, execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const isWin = process.platform === "win32";

const PYTHON = process.env.PYTHON || "python";
// Your frontend/ lives inside src/, so the backend (main.py) is one level up.
// Override with BACKEND_DIR if your layout differs.
const BACKEND_DIR = path.resolve(__dirname, process.env.BACKEND_DIR || "..");
const BACKEND_PORT = process.env.BACKEND_PORT || "8000";

const children = [];
let shuttingDown = false;

function log(prefix, color, line) {
  const c = { api: "\x1b[36m", web: "\x1b[32m", sys: "\x1b[33m" }[color] || "";
  process.stdout.write(`${c}[${prefix}]\x1b[0m ${line}`);
}

function pipe(child, prefix, color) {
  child.stdout?.on("data", (d) => log(prefix, color, d.toString()));
  child.stderr?.on("data", (d) => log(prefix, color, d.toString()));
}

function killChild(child) {
  if (!child || child.killed) return;
  try {
    if (isWin) {
      // Kill the whole tree on Windows (child.kill() won't reach grandchildren).
      execSync(`taskkill /pid ${child.pid} /T /F`, { stdio: "ignore" });
    } else {
      child.kill("SIGINT");
    }
  } catch {
    /* already gone */
  }
}

function shutdown(code = 0) {
  if (shuttingDown) return;
  shuttingDown = true;
  log("sys", "sys", "shutting down…\n");
  for (const c of children) killChild(c);
  // Give processes a moment to exit, then force quit this launcher.
  setTimeout(() => process.exit(code), 400);
}

// ── Backend (no --reload → single, cleanly killable process) ─────────────
// `-u` + PYTHONUNBUFFERED so the backend's logs (and any crash traceback)
// stream immediately instead of being buffered because stdout is piped.
//
// NOTE: on Windows, mixing shell:true with an args ARRAY breaks (spawn
// cmd.exe ENOENT). So we build a single command STRING and run it in a shell.
const apiCmd = `${PYTHON} -u -m uvicorn main:app --host 127.0.0.1 --port ${BACKEND_PORT}`;
log("sys", "sys", `starting backend: ${apiCmd} (cwd ${BACKEND_DIR})\n`);
const api = spawn(apiCmd, {
  cwd: BACKEND_DIR,
  shell: true,
  env: { ...process.env, PYTHONUNBUFFERED: "1" },
});
children.push(api);
pipe(api, "api", "api");
api.on("error", (e) => {
  log("sys", "sys", `could not start backend (${e.message}). Is Python on PATH? Try PYTHON=... npm run dev\n`);
});
api.on("exit", (code) => {
  if (!shuttingDown) {
    log("sys", "sys", `backend exited (code ${code}). Stopping frontend too.\n`);
    shutdown(code ?? 0);
  }
});

// ── Frontend (Vite) ──────────────────────────────────────────────────────
const web = spawn("npm run web", { cwd: __dirname, shell: true });
children.push(web);
pipe(web, "web", "web");
web.on("exit", (code) => {
  if (!shuttingDown) {
    log("sys", "sys", `frontend exited (code ${code}). Stopping backend too.\n`);
    shutdown(code ?? 0);
  }
});

// ── Tear down on every exit path ─────────────────────────────────────────
process.on("SIGINT", () => shutdown(0));   // Ctrl+C
process.on("SIGTERM", () => shutdown(0));  // kill
process.on("SIGHUP", () => shutdown(0));   // terminal closed
process.on("exit", () => { for (const c of children) killChild(c); });
