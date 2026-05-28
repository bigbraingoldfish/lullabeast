import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";

/** Pipeline agent IDs handled by this plugin. */
export const PIPELINE_AGENT_IDS = new Set(["planner", "executor", "reviewer"]);

/** prd-creator agent id for Ideas workflow hooks. */
export const PRD_CREATOR_AGENT_ID = "prd-creator";

/**
 * Return true when sessionKey belongs to the Ideas UI workflow.
 *
 * Accepts both the bare ``ideas:`` prefix and the OpenClaw gateway-normalised
 * form ``agent:{role}:ideas:`` (e.g. ``agent:prd-creator:ideas:abc:session-1``),
 * mirroring :func:`isPipelineSession` below.  Without the gateway-prefix
 * branch, production hookCtx.sessionKey values fail this check and the
 * stamp-touch path is silently skipped — the live bug observed on
 * Untitled Balloon Popping Game (chat session-13/14) where ``startup_grace``
 * fired at 30 s while the agent was genuinely working.
 */
export function isIdeasSession(sessionKey: string | undefined): boolean {
  if (typeof sessionKey !== "string") return false;
  return (
    sessionKey.startsWith("ideas:") ||
    /^agent:[a-z0-9_-]+:ideas:/i.test(sessionKey)
  );
}

/**
 * Parse `ideas:{ideaId}:session-{turn}` — turn-by-turn chat only.
 * Returns null for clarity/convert/alignment keys.
 *
 * Tolerates the gateway's ``agent:{role}:`` prefix so callers (e.g.
 * ``agent-end-handler``) see the same parse result whether they receive a
 * bare or normalised session key.
 */
export function parseIdeasTurnSession(
  sessionKey: string,
): { ideaId: string; turn: number } | null {
  const m = /^(?:agent:[a-z0-9_-]+:)?ideas:([^:]+):session-(\d+)$/i.exec(sessionKey);
  if (!m) return null;
  return { ideaId: m[1], turn: parseInt(m[2], 10) };
}

/**
 * Extract idea id from any ``ideas:{id}:...`` session key (first segment after prefix).
 *
 * Tolerates the gateway's ``agent:{role}:`` prefix; returns the bare id in
 * both shapes.
 */
export function extractIdeasIdFromSessionKey(sessionKey: string): string | null {
  const m = /^(?:agent:[a-z0-9_-]+:)?ideas:([^:]+):/i.exec(sessionKey);
  return m ? m[1] : null;
}

/** Activity stamp watched by the foreground chat-turn poller (the SOLE consumer). */
export const IDEAS_CHAT_ACTIVITY_STAMP = "prd_creator_activity.stamp";

/** Separate stamp for the background readiness assessment (see selector below). */
export const IDEAS_READINESS_ACTIVITY_STAMP = "prd_creator_readiness_activity.stamp";

/**
 * Return true when sessionKey is the background readiness-assessment session
 * (`ideas:{id}:readiness`), as opposed to a chat turn / clarity / convert /
 * format-correction key.
 *
 * Tolerates the gateway's ``agent:{role}:`` prefix, mirroring
 * :func:`parseIdeasTurnSession`.  Anchored on ``:readiness$`` so only the
 * readiness session matches.
 */
export function isIdeasReadinessSession(
  sessionKey: string | undefined,
): boolean {
  if (typeof sessionKey !== "string") return false;
  return /^(?:agent:[a-z0-9_-]+:)?ideas:[^:]+:readiness$/i.test(sessionKey);
}

/**
 * Pick the Ideas activity-stamp filename for a session key.
 *
 * The readiness assessment is auto-fired fire-and-forget after every chat turn
 * (ui/server.py ``_trigger_readiness_assessment``) and runs as the same
 * prd-creator agent with the same ideaId as the foreground chat turn.  If it
 * shared the chat stamp, a readiness run that overlapped a new chat turn would
 * keep that stamp fresh and mask a genuinely-stalled foreground turn from the
 * chat poller (``_poll_sentinel_with_idle_detect``).  Routing readiness to its
 * own stamp keeps the two liveness signals independent; the chat stamp then
 * means exactly "the foreground chat turn is alive".
 */
export function ideasActivityStampFilename(sessionKey: string): string {
  return isIdeasReadinessSession(sessionKey)
    ? IDEAS_READINESS_ACTIVITY_STAMP
    : IDEAS_CHAT_ACTIVITY_STAMP;
}

/**
 * Resolve `$OPENCLAW_ROOT/ideas`.
 *
 * Resolution order (highest → lowest):
 *   1. ``workspaceDir`` parent (the typed-hook path always has this).
 *   2. ``env["OPENCLAW_ROOT"]`` (operator-set override).
 *   3. ``env["HOME"]/.openclaw`` (the production default; mirrors
 *      ``resolveOpenClawStateDirFromEnv``).
 *   4. ``os.homedir()/.openclaw`` (final fallback for when even HOME is
 *      missing from the env).
 *
 * The HOME fallback is what makes the agent-event-stream backstop
 * (``recordPipelineActivityFromAgentEvent``) work in production: that path
 * has no ``workspaceDir`` because the event stream is host-level, and the
 * gateway's systemd unit sets ``HOME=/home/pi`` but does NOT set
 * ``OPENCLAW_ROOT``.  Without the HOME fallback the resolver returned null
 * and the Ideas stamp went stale during long model generations — the live
 * bug observed on the Untitled Balloon Popping Game session (Cursor MCP
 * validation, see CHANGELOG).  The pipeline equivalent
 * (``resolvePipelineArtifactsDirFromEnv`` via
 * ``resolveOpenClawStateDirFromEnv``) already had this fallback; Ideas
 * didn't.
 *
 * Accepts an explicit ``env`` so tests can drive the fallback chain
 * deterministically without monkey-patching ``process.env``.
 */
export function resolveIdeasRootFromWorkspace(
  workspaceDir: string | undefined,
  env: NodeJS.ProcessEnv = process.env,
): string | null {
  let openclawRoot: string | null = null;
  if (workspaceDir) {
    openclawRoot = path.dirname(workspaceDir.replace(/\/+$/, ""));
  } else {
    const envRoot = env["OPENCLAW_ROOT"];
    if (envRoot?.trim()) {
      openclawRoot = path.resolve(envRoot.trim().replace(/^~/, os.homedir()));
    } else {
      // HOME fallback — production gateways usually have HOME set but not
      // OPENCLAW_ROOT.  os.homedir() is the last-resort if HOME is also
      // missing (it reads HOME under the hood on POSIX so it usually
      // matches anyway).
      const home = env["HOME"]?.trim() || os.homedir();
      openclawRoot = path.join(home, ".openclaw");
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
 * Touch `{ideasRoot}/{ideaId}/{stampFilename}` for Ideas stall detection.
 * Creates `ideaId` directory if needed.
 *
 * ``stampFilename`` defaults to the chat-turn stamp; pass
 * :const:`IDEAS_READINESS_ACTIVITY_STAMP` (via
 * :func:`ideasActivityStampFilename`) for the background readiness session so
 * its activity cannot mask a stalled foreground chat turn.
 */
export function touchIdeasPrdCreatorActivityStamp(
  ideasRoot: string,
  ideaId: string,
  stampFilename: string = IDEAS_CHAT_ACTIVITY_STAMP,
): void {
  const ideaDir = path.join(ideasRoot, ideaId);
  fs.mkdirSync(ideaDir, { recursive: true });
  const stampPath = path.join(ideaDir, stampFilename);
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
 * allowedSessionKeyPrefixes in openclaw.json).  The OpenClaw gateway
 * additionally normalises session keys to ``agent:{role}:{key}`` at
 * the protocol surface — accept both shapes here because production
 * hookCtx.sessionKey arrives with the ``agent:`` prefix.
 */
export function isPipelineSession(sessionKey: string | undefined): boolean {
  if (typeof sessionKey !== "string") return false;
  return (
    sessionKey.startsWith("pipeline:") ||
    /^agent:[a-z0-9_-]+:pipeline:/i.test(sessionKey)
  );
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

/** Resolve OpenClaw's mutable state directory from the gateway environment. */
export function resolveOpenClawStateDirFromEnv(
  env: NodeJS.ProcessEnv = process.env,
): string {
  const explicit = env["OPENCLAW_STATE_DIR"] || env["OPENCLAW_ROOT"];
  if (explicit?.trim()) {
    return path.resolve(explicit.trim().replace(/^~/, os.homedir()));
  }
  return path.join(os.homedir(), ".openclaw");
}

/** Resolve the AutoDev pipeline artifacts dir from gateway-visible env/path state. */
export function resolvePipelineArtifactsDirFromEnv(
  env: NodeJS.ProcessEnv = process.env,
): string {
  const pipelineRoot = env["AUTODEV_PIPELINE_ROOT"];
  if (pipelineRoot?.trim()) {
    return path.join(
      path.resolve(pipelineRoot.trim().replace(/^~/, os.homedir())),
      "pipeline-project",
      ".autodev",
      "pipeline",
    );
  }
  return path.join(
    resolveOpenClawStateDirFromEnv(env),
    "pipeline-project",
    ".autodev",
    "pipeline",
  );
}

export function parsePipelineAgentIdFromSessionKey(
  sessionKey: string | undefined,
): string | null {
  if (!sessionKey || !isPipelineSession(sessionKey)) return null;
  const m = /:([^:]+)-attempt-\d+$/i.exec(sessionKey);
  const agentId = m?.[1]?.toLowerCase();
  return agentId && PIPELINE_AGENT_IDS.has(agentId) ? agentId : null;
}

export function resolvePipelineSessionFromRunId(
  runId: string | undefined,
  stateDir: string = resolveOpenClawStateDirFromEnv(),
): { sessionKey: string; agentId: string } | null {
  if (!runId?.trim()) return null;

  for (const agentId of PIPELINE_AGENT_IDS) {
    const sessionsPath = path.join(
      stateDir,
      "agents",
      agentId,
      "sessions",
      "sessions.json",
    );
    const sessions = readJsonSafe(sessionsPath);
    if (!sessions) continue;

    for (const [fullKey, value] of Object.entries(sessions)) {
      if (typeof value !== "object" || value === null) continue;
      const sessionId = (value as Record<string, unknown>)["sessionId"];
      if (sessionId !== runId) continue;
      const prefix = `agent:${agentId}:`;
      const sessionKey = fullKey.toLowerCase().startsWith(prefix)
        ? fullKey.slice(prefix.length)
        : fullKey;
      if (!isPipelineSession(sessionKey)) continue;
      return { sessionKey, agentId };
    }
  }

  return null;
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
