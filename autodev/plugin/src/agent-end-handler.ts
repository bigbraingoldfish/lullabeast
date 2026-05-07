/**
 * agent_end hook handler — authoritative completion signal for pipeline sessions
 * and Ideas turn-by-turn (`ideas:{id}:session-{n}`) prd-creator sessions.
 *
 * Pipeline: when an OpenClaw agent session ends, writes missing `{agent}_output.done`
 * under the pipeline artifacts directory.
 *
 * Ideas: for `prd-creator` + `ideas:{ideaId}:session-{turn}` only, writes missing
 * `ideas/{ideaId}/turns/{turn}.done` with body `done` (UI contract).
 *
 * If the sentinel already exists (agent completed normally), this is a no-op.
 * If the target directory does not exist (agent never started), this is a no-op.
 */

import * as path from "node:path";
import type {
  PluginHookAgentContext,
  PluginHookAgentEndEvent,
} from "./openclaw-types.d.ts";
import {
  isPipelineSession,
  PIPELINE_AGENT_IDS,
  PRD_CREATOR_AGENT_ID,
  parseIdeasTurnSession,
  resolveArtifactsDir,
  resolveIdeasRootFromWorkspace,
  writeIdeasTurnDoneIfAbsent,
  writeSentinelIfAbsent,
} from "./utils.ts";

export function handleAgentEnd(
  event: PluginHookAgentEndEvent,
  ctx: PluginHookAgentContext,
): void {
  const { sessionKey, agentId, workspaceDir } = ctx;

  if (isPipelineSession(sessionKey)) {
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
          `${agentId}_output.done (agentId=${agentId}, sessionKey=${sessionKey}, success=${event.success})`,
      );
    }
    return;
  }

  if (agentId !== PRD_CREATOR_AGENT_ID || typeof sessionKey !== "string") return;

  const turnInfo = parseIdeasTurnSession(sessionKey);
  if (!turnInfo) return;

  const ideasRoot = resolveIdeasRootFromWorkspace(workspaceDir);
  if (!ideasRoot) {
    console.warn(
      `[autodev-pipeline-signals] agent_end: cannot resolve ideas root ` +
        `(sessionKey=${sessionKey}, workspaceDir=${workspaceDir}). ` +
        `Set OPENCLAW_ROOT env var as fallback.`,
    );
    return;
  }

  const donePath = path.join(
    ideasRoot,
    turnInfo.ideaId,
    "turns",
    `${turnInfo.turn}.done`,
  );
  const wrote = writeIdeasTurnDoneIfAbsent(donePath);
  if (wrote) {
    console.log(
      `[autodev-pipeline-signals] agent_end: wrote missing Ideas sentinel ` +
        `${turnInfo.ideaId}/turns/${turnInfo.turn}.done (sessionKey=${sessionKey}, success=${event.success})`,
    );
  }
}
