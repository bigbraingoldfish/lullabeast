/**
 * In-session stall detection — Tier A activity signals only.
 *
 * Registers observation hooks `model_call_started`, `model_call_ended`, and
 * `after_tool_call`. Each fires → touch an activity stamp:
 *
 * - Pipeline (`pipeline:` + planner/executor/reviewer): `{agent}_activity.stamp`
 *   under `$OPENCLAW_ROOT/pipeline-project/.autodev/pipeline/`.
 * - Ideas (`ideas:` + prd-creator): `prd_creator_activity.stamp` under
 *   `$OPENCLAW_ROOT/ideas/{ideaId}/`.
 *
 * The pipeline orchestrator's `poll_for_sentinel` and the UI server's Ideas
 * poller read stamp mtimes to detect genuine stalls without relying on JSONL
 * batching heuristics.
 */

import type {
  PluginAgentEvent,
  OpenClawPluginApi,
  PluginHookAgentContext,
} from "./openclaw-types.d.ts";
import {
  extractIdeasIdFromSessionKey,
  ideasActivityStampFilename,
  isIdeasSession,
  isPipelineSession,
  PIPELINE_AGENT_IDS,
  PRD_CREATOR_AGENT_ID,
  parsePipelineAgentIdFromSessionKey,
  resolveArtifactsDir,
  resolveIdeasRootFromWorkspace,
  resolveOpenClawStateDirFromEnv,
  resolvePipelineArtifactsDirFromEnv,
  resolvePipelineSessionFromRunId,
  touchActivityStamp,
  touchIdeasPrdCreatorActivityStamp,
} from "./utils.ts";

/** Record observable activity for stall-detection (shared by all Tier A hooks). */
export function recordPipelineActivity(
  ctx: PluginHookAgentContext,
  event?: { runId?: string; sessionKey?: string },
): void {
  const sessionKey = ctx.sessionKey || event?.sessionKey;
  let agentId = ctx.agentId || parsePipelineAgentIdFromSessionKey(sessionKey);
  const workspaceDir = ctx.workspaceDir;

  if (isPipelineSession(sessionKey)) {
    if (!agentId && (ctx.runId || event?.runId)) {
      const resolved = resolvePipelineSessionFromRunId(
        ctx.runId || event?.runId,
        resolveOpenClawStateDirFromEnv(),
      );
      agentId = resolved?.agentId ?? null;
    }
    if (!agentId || !PIPELINE_AGENT_IDS.has(agentId)) return;
    const artifactsDir =
      resolveArtifactsDir(workspaceDir) || resolvePipelineArtifactsDirFromEnv();
    if (!artifactsDir) return;
    touchActivityStamp(artifactsDir, agentId);
    return;
  }

  if (ctx.runId || event?.runId) {
    const resolved = resolvePipelineSessionFromRunId(
      ctx.runId || event?.runId,
      resolveOpenClawStateDirFromEnv(),
    );
    if (resolved) {
      const artifactsDir = resolvePipelineArtifactsDirFromEnv();
      touchActivityStamp(artifactsDir, resolved.agentId);
      return;
    }
  }

  if (agentId === PRD_CREATOR_AGENT_ID && isIdeasSession(sessionKey)) {
    const key = sessionKey as string;
    const ideaId = extractIdeasIdFromSessionKey(key);
    if (!ideaId) return;
    const ideasRoot = resolveIdeasRootFromWorkspace(workspaceDir);
    if (!ideasRoot) return;
    touchIdeasPrdCreatorActivityStamp(
      ideasRoot,
      ideaId,
      ideasActivityStampFilename(key),
    );
  }
}

/**
 * Record activity from OpenClaw's host-level agent event stream.
 *
 * This is the reliability path: typed hooks are useful when OpenClaw fires them,
 * but agent events are the same stream that powers live JSONL/tool updates.
 */
export function recordPipelineActivityFromAgentEvent(
  event: PluginAgentEvent,
  env: NodeJS.ProcessEnv = process.env,
): void {
  const eventSessionKey =
    typeof event.sessionKey === "string" ? event.sessionKey : undefined;

  // Ideas branch — agent-event-stream reliability backstop for the prd-creator
  // stamp.  Without this, Ideas would go dark whenever OpenClaw fails to fire
  // one of the typed model_call/tool hooks the Ideas branch in
  // `recordPipelineActivity` relies on, while pipeline sessions would still
  // refresh via the pipeline branch below.  Mirrors the pipeline coverage.
  if (isIdeasSession(eventSessionKey)) {
    const key = eventSessionKey as string;
    const ideaId = extractIdeasIdFromSessionKey(key);
    if (!ideaId) return;
    const ideasRoot = resolveIdeasRootFromWorkspace(undefined, env);
    if (!ideasRoot) return;
    touchIdeasPrdCreatorActivityStamp(
      ideasRoot,
      ideaId,
      ideasActivityStampFilename(key),
    );
    return;
  }

  const agentFromEventSession = parsePipelineAgentIdFromSessionKey(eventSessionKey);
  let agentId = agentFromEventSession;

  if (!agentId) {
    const resolved = resolvePipelineSessionFromRunId(
      event.runId,
      resolveOpenClawStateDirFromEnv(env),
    );
    agentId = resolved?.agentId ?? null;
  }

  if (!agentId) return;
  const artifactsDir = resolvePipelineArtifactsDirFromEnv(env);
  touchActivityStamp(artifactsDir, agentId);
}

export function registerStallDetectorHooks(api: OpenClawPluginApi): void {
  api.registerAgentEventSubscription?.({
    id: "autodev-pipeline-activity",
    streams: ["lifecycle", "assistant", "tool", "item", "command_output"],
    handle: (event) => {
      recordPipelineActivityFromAgentEvent(event);
    },
  });

  api.on(
    "model_call_started",
    (event, ctx) => {
      recordPipelineActivity(ctx, event);
    },
    { priority: 50 },
  );
  api.on(
    "model_call_ended",
    (event, ctx) => {
      recordPipelineActivity(ctx, event);
    },
    { priority: 50 },
  );
  api.on(
    "after_tool_call",
    (event, ctx) => {
      recordPipelineActivity(ctx, event);
    },
    { priority: 50 },
  );
}
