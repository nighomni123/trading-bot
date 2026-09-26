import { readFileSync, existsSync } from "node:fs";
import { execFileSync } from "node:child_process";
import path from "node:path";

/**
 * Resolve the repo root by walking up until we find pyproject.toml.
 * The dashboard lives at <repo>/dashboard, artifacts live at <repo>/docs.
 */
function findRepoRoot(start = process.cwd()): string {
  let dir = start;
  for (let i = 0; i < 8; i++) {
    if (existsSync(path.join(dir, "pyproject.toml"))) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return process.cwd();
}

export const REPO_ROOT = findRepoRoot();

/** Read a repo-relative JSON artifact. Returns null when absent. */
export function readArtifact<T = unknown>(rel: string): T | null {
  const p = path.join(REPO_ROOT, rel);
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, "utf8")) as T;
  } catch (e) {
    console.error(`[artifacts] parse failed ${rel}:`, e);
    return null;
  }
}

/** Read a repo-relative text artifact. Returns null when absent. */
export function readText(rel: string): string | null {
  const p = path.join(REPO_ROOT, rel);
  if (!existsSync(p)) return null;
  return readFileSync(p, "utf8");
}

let _commit: { hash: string; short: string; subject: string; date: string } | null = null;

/** Active git commit for the top bar + artifact traceability footers. */
export function gitCommit() {
  if (_commit) return _commit;
  const run = (args: string[]) =>
    execFileSync("git", args, {
      cwd: REPO_ROOT,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  try {
    const hash = run(["rev-parse", "HEAD"]);
    _commit = {
      hash,
      short: hash.slice(0, 7),
      subject: run(["log", "-1", "--format=%s"]),
      date: run(["log", "-1", "--format=%cI"]),
    };
  } catch {
    _commit = { hash: "unknown", short: "unknown", subject: "unavailable", date: "" };
  }
  return _commit;
}

/** Filesystem mtime of an artifact, as an ISO string (provenance freshness). */
export function artifactMtime(rel: string): string | null {
  const p = path.join(REPO_ROOT, rel);
  if (!existsSync(p)) return null;
  try {
    return execFileSync("stat", ["-f", "%Sm", "-t", "%Y-%m-%dT%H:%M:%SZ", p], {
      encoding: "utf8",
    }).trim();
  } catch {
    return null;
  }
}

/** True when the artifact exists — used to grey out modules with no data. */
export function artifactExists(rel: string): boolean {
  return existsSync(path.join(REPO_ROOT, rel));
}
