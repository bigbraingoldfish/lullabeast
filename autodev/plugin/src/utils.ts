import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";

/** Pipeline agent IDs handled by this plugin. */
export const PIPELINE_AGENT_IDS = new Set(["planner", "executor", "reviewer"]);

/** prd-creator agent id for Ideas workflow hooks. */
export const PRD_CREATOR_AGENT_ID = "prd-creator";

/**
 * Return true when sessionKey belongs to the Ideas UI workflow (OpenClaw
 * session keys prefixed with "ideas:").
 */
export function isIdeasSession(sessionKey: string | undefined): boolean {
  return typeof sessionKey === "string" && sessionKey.startsWith("ideas:");
}

/**
 * Parse `ideas:{ideaId}:session-{turn}` — turn-by-turn chat only.
 * Returns null for clarity/convert/alignment keys.
 */
export function parseIdeasTurnSession(
  sessionKey: string,
): { ideaId: string; turn: number } | null {
  const m = /^ideas:([^:]+):session-(\d+)$/.exec(sessionKey);
  if (!m) return null;
  return { ideaId: m[1], turn: parseInt(m[2], 10) };
}

/**
 * Extract idea id from any `ideas:{id}:...` session key (first segment after prefix).
 */
export function extractIdeasIdFromSessionKey(sessionKey: string): string | null {
  const m = /^ideas:([^:]+):/.exec(sessionKey);
  return m ? m[1] : null;
}

/**
 * Resolve `$OPENCLAW_ROOT/ideas` from workspace-prd-creator path or OPENCLAW_ROOT env.
 */
export function resolveIdeasRootFromWorkspace(
  workspaceDir: string | undefined,
): string | null {
  let openclawRoot: string | null = null;
  if (workspaceDir) {
    openclawRoot = path.dirname(workspaceDir.replace(/\/+$/, ""));
  } else {
    const envRoot = process.env["OPENCLAW_ROOT"];
    if (envRoot) {
      openclawRoot = path.resolve(envRoot.replace(/^~/, os.homedir()));
    }
  }
  if (!openclawRoot) return null;
  return path.join(openclawRoot, "ideas");
}

/**
 * Write `turns/{n}.done` with body `done` if absent (Ideas output contract).
 * Returns false if parent `turns/` dir does not exist.
 */
export function writeIdeasTurnDoneIfAbsent(donePath: string): boolean {
  if (fs.existsSync(donePath)) return false;
  const dir = path.dirname(donePath);
  if (!fs.existsSync(dir)) return false;

  const tmpPath = `${donePath}.tmp.${process.pid}`;
  try {
    fs.writeFileSync(tmpPath, "done", { flag: "wx" });
    fs.renameSync(tmpPath, donePath);
    return true;
  } catch (err: unknown) {
    try {
      fs.unlinkSync(tmpPath);
    } catch {
      // ignore
    }
    if (
      err instanceof Error &&
      (err as NodeJS.ErrnoException).code === "EEXIST"
    ) {
      return false;
    }
    throw err;
  }
}

/**
 * Touch `{ideasRoot}/{ideaId}/prd_creator_activity.stamp` for Ideas stall detection.
 * Creates `ideaId` directory if needed.
 */
export function touchIdeasPrdCreatorActivityStamp(
  ideasRoot: string,
  ideaId: string,
): void {
  const ideaDir = path.join(ideasRoot, ideaId);
  fs.mkdirSync(ideaDir, { recursive: true });
  const stampPath = path.join(ideaDir, "prd_creator_activity.stamp");
  const tmpPath = `${stampPath}.tmp.${process.pid}`;
  try {
    fs.writeFileSync(tmpPath, "", { flag: "w" });
    fs.renameSync(tmpPath, stampPath);
  } catch (err: unknown) {
    try {
      fs.unlinkSync(tmpPath);
    } catch {
      // ignore
    }
    throw err;
  }
}

/**
 * Return true when sessionKey belongs to an AutoDev pipeline session.
 * All pipeline sessions use the prefix "pipeline:" (enforced by
 * allowedSessionKeyPrefixes in openclaw.json).
 */
export function isPipelineSession(sessionKey: string | undefined): boolean {
  return typeof sessionKey === "string" && sessionKey.startsWith("pipeline:");
}

/**
 * Derive the pipeline artifacts directory from an agent workspace path.
 *
 * The workspace dir is `$OPENCLAW_ROOT/workspace-{agent}/`.  One level up is
 * `$OPENCLAW_ROOT/`, and the artifacts live at
 * `$OPENCLAW_ROOT/pipeline-project/.autodev/pipeline/`.
 *
 * Falls back to the OPENCLAW_ROOT environment variable if workspaceDir is not
 * available.
 *
 * Returns null when no valid path can be derived.
 */
export function resolveArtifactsDir(
  workspaceDir: string | undefined,
): string | null {
  let openclawRoot: string | null = null;

  if (workspaceDir) {
    openclawRoot = path.dirname(workspaceDir.replace(/\/+$/, ""));
  } else {
    const envRoot = process.env["OPENCLAW_ROOT"];
    if (envRoot) {
      openclawRoot = path.resolve(envRoot.replace(/^~/, os.homedir()));
    }
  }

  if (!openclawRoot) return null;

  return path.join(openclawRoot, "pipeline-project", ".autodev", "pipeline");
}

/**
 * Write an empty sentinel file at the given path if it does not already exist.
 * Uses an atomic temp-file rename to avoid partial writes being observed.
 *
 * Returns true if the file was written, false if it already existed, throws on
 * unexpected errors.
 */
export function writeSentinelIfAbsent(donePath: string): boolean {
  if (fs.existsSync(donePath)) return false;

  const dir = path.dirname(donePath);
  if (!fs.existsSync(dir)) return false; // artifacts dir not present yet — agent did nothing

  // Atomic write: temp file + rename so the sentinel appears atomically.
  const tmpPath = `${donePath}.tmp.${process.pid}`;
  try {
    fs.writeFileSync(tmpPath, "", { flag: "wx" });
    fs.renameSync(tmpPath, donePath);
    return true;
  } catch (err: unknown) {
    // Another writer beat us (EEXIST on the tmp) — fine, sentinel is present.
    try {
      fs.unlinkSync(tmpPath);
    } catch {
      // ignore cleanup failure
    }
    if (
      err instanceof Error &&
      (err as NodeJS.ErrnoException).code === "EEXIST"
    ) {
      return false;
    }
    throw err;
  }
}

/**
 * Touch (create or overwrite) `{agentId}_activity.stamp` in the artifacts directory.
 * The file is empty; its mtime is the activity clock for in-session stall detection.
 * No-op if the artifacts directory does not exist yet.
 */
export function touchActivityStamp(artifactsDir: string, agentId: string): void {
  if (!fs.existsSync(artifactsDir)) return;

  const stampPath = path.join(artifactsDir, `${agentId}_activity.stamp`);
  const tmpPath = `${stampPath}.tmp.${process.pid}`;
  try {
    fs.writeFileSync(tmpPath, "", { flag: "w" });
    fs.renameSync(tmpPath, stampPath);
  } catch (err: unknown) {
    try {
      fs.unlinkSync(tmpPath);
    } catch {
      // ignore
    }
    throw err;
  }
}

/**
 * Safely parse a JSON file.  Returns null on any error (missing file, parse
 * failure, non-object root).
 */
export function readJsonSafe(
  filePath: string,
): Record<string, unknown> | null {
  try {
    const text = fs.readFileSync(filePath, "utf8");
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed))
      return null;
    return parsed as Record<string, unknown>;
  } catch {
    return null;
  }
}
