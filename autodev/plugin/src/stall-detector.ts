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
  OpenClawPluginApi,
  PluginHookAgentContext,
} from "./openclaw-types.d.ts";
import {
  extractIdeasIdFromSessionKey,
  isPipelineSession,
  PIPELINE_AGENT_IDS,
  PRD_CREATOR_AGENT_ID,
  resolveArtifactsDir,
  resolveIdeasRootFromWorkspace,
  touchActivityStamp,
  touchIdeasPrdCreatorActivityStamp,
} from "./utils.ts";

/** Record observable activity for stall-detection (shared by all Tier A hooks). */
export function recordPipelineActivity(ctx: PluginHookAgentContext): void {
  const sessionKey = ctx.sessionKey;
  const agentId = ctx.agentId;
  const workspaceDir = ctx.workspaceDir;

  if (isPipelineSession(sessionKey)) {
    if (!agentId || !PIPELINE_AGENT_IDS.has(agentId)) return;
    const artifactsDir = resolveArtifactsDir(workspaceDir);
    if (!artifactsDir) return;
    touchActivityStamp(artifactsDir, agentId);
    return;
  }

  if (
    agentId === PRD_CREATOR_AGENT_ID &&
    typeof sessionKey === "string" &&
    sessionKey.startsWith("ideas:")
  ) {
    const ideaId = extractIdeasIdFromSessionKey(sessionKey);
    if (!ideaId) return;
    const ideasRoot = resolveIdeasRootFromWorkspace(workspaceDir);
    if (!ideasRoot) return;
    touchIdeasPrdCreatorActivityStamp(ideasRoot, ideaId);
  }
}

export function registerStallDetectorHooks(api: OpenClawPluginApi): void {
  api.on(
    "model_call_started",
    (_event, ctx) => {
      recordPipelineActivity(ctx);
    },
    { priority: 50 },
  );
  api.on(
    "model_call_ended",
    (_event, ctx) => {
      recordPipelineActivity(ctx);
    },
    { priority: 50 },
  );
  api.on(
    "after_tool_call",
    (_event, ctx) => {
      recordPipelineActivity(ctx);
    },
    { priority: 50 },
  );
}
