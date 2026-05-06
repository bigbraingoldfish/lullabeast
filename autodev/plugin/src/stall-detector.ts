/**
 * In-session stall detection — Tier A activity signals only.
 *
 * Registers observation hooks `model_call_started`, `model_call_ended`, and
 * `after_tool_call` (OpenClaw plugin catalog). Each fires → touch
 * `{agentId}_activity.stamp` under the pipeline artifacts directory
 * (`$OPENCLAW_ROOT/pipeline-project/.autodev/pipeline/`).
 *
 * The orchestrator's `poll_for_sentinel` reads that file's mtime on every
 * 2-second tick. If `now - mtime > stall_threshold_seconds`, the poll returns
 * `False` — the same path as sentinel timeout — so existing retry /
 * `classify_executor_outcome` logic runs without a new state machine.
 *
 * Scoped to `pipeline:` sessions and planner / executor / reviewer only.
 */

import type {
  OpenClawPluginApi,
  PluginHookAgentContext,
} from "./openclaw-types.d.ts";
import {
  isPipelineSession,
  PIPELINE_AGENT_IDS,
  resolveArtifactsDir,
  touchActivityStamp,
} from "./utils.ts";

/** Record observable activity for stall-detection (shared by all Tier A hooks). */
export function recordPipelineActivity(ctx: PluginHookAgentContext): void {
  const sessionKey = ctx.sessionKey;
  if (!isPipelineSession(sessionKey)) return;

  const agentId = ctx.agentId;
  if (!agentId || !PIPELINE_AGENT_IDS.has(agentId)) return;

  const artifactsDir = resolveArtifactsDir(ctx.workspaceDir);
  if (!artifactsDir) return;

  touchActivityStamp(artifactsDir, agentId);
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
