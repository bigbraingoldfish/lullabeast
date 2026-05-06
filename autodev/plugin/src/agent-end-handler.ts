/**
 * agent_end hook handler — authoritative completion signal for pipeline sessions.
 *
 * When an OpenClaw agent session ends for any reason (normal completion, crash,
 * or zero-runtime rejection), this hook writes the expected `.done` sentinel
 * file if the agent did not write one itself.  The orchestrator's
 * `poll_for_sentinel` call then detects the file and proceeds to gate
 * evaluation.
 *
 * Filter conditions (both must hold):
 *   - ctx.sessionKey starts with "pipeline:"
 *   - ctx.agentId is "planner", "executor", or "reviewer"
 *
 * If the sentinel already exists (agent completed normally), this is a no-op.
 * If the artifacts directory itself is absent (agent never started), this is
 * also a no-op — the sentinel poll will time out, which is the correct outcome.
 */

import * as path from "node:path";
import type {
  PluginHookAgentContext,
  PluginHookAgentEndEvent,
} from "./openclaw-types.d.ts";
import {
  isPipelineSession,
  PIPELINE_AGENT_IDS,
  resolveArtifactsDir,
  writeSentinelIfAbsent,
} from "./utils.ts";

export function handleAgentEnd(
  _event: PluginHookAgentEndEvent,
  ctx: PluginHookAgentContext,
): void {
  const { sessionKey, agentId, workspaceDir } = ctx;

  if (!isPipelineSession(sessionKey)) return;
  if (!agentId || !PIPELINE_AGENT_IDS.has(agentId)) return;

  const artifactsDir = resolveArtifactsDir(workspaceDir);
  if (!artifactsDir) {
    console.warn(
      `[autodev-pipeline-signals] agent_end: cannot resolve artifacts dir ` +
        `(agentId=${agentId}, sessionKey=${sessionKey}, workspaceDir=${workspaceDir}). ` +
        `Set OPENCLAW_ROOT env var as fallback.`,
    );
    return;
  }

  const donePath = path.join(artifactsDir, `${agentId}_output.done`);
  const wrote = writeSentinelIfAbsent(donePath);
  if (wrote) {
    console.log(
      `[autodev-pipeline-signals] agent_end: wrote missing sentinel ` +
        `${agentId}_output.done (agentId=${agentId}, sessionKey=${sessionKey}, success=${_event.success})`,
    );
  }
}
