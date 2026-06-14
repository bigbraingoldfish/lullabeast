import { handleAgentEnd } from "./agent-end-handler.ts";
import { handleBeforeAgentFinalize } from "./before-finalize-handler.ts";
import { registerInboundEscalationHook } from "./inbound-escalation.ts";
import type { OpenClawPluginApi } from "./openclaw-types.d.ts";
import { registerStallDetectorHooks } from "./stall-detector.ts";

// Export a plain object instead of depending on the SDK helper. OpenClaw's
// loader accepts `{ id, name, description, register }` directly, and this shape
// is also importable in standalone validation scripts without SDK aliasing.
export function register(api: OpenClawPluginApi): void {
  // Observation-only: fires fire-and-forget after the session closes.
  // Writes the missing .done sentinel when the agent did not write one,
  // unblocking the orchestrator's poll_for_sentinel backstop immediately
  // rather than waiting for the hard timeout.
  api.on(
    "agent_end",
    (event, ctx) => {
      handleAgentEnd(event, ctx);
    },
    { priority: 50 },
  );

  // Decision hook: fires before the harness accepts the agent's final answer.
  // Performs pure JSON structural checks and requests a revision pass when
  // required output fields are missing.  Hard gate scripts remain the final
  // authority; this is a lightweight pre-check that preserves session context
  // and avoids burning a retry on trivial structural omissions.
  api.on(
    "before_agent_finalize",
    (event, ctx) => {
      return handleBeforeAgentFinalize(event, ctx);
    },
    { priority: 50 },
  );

  registerStallDetectorHooks(api);

  // Inbound escalation forwarder: an operator's reply on the configured
  // escalation channel is forwarded to the UI server's /api/escalation/inbound
  // (opt-in via AUTODEV_ESCALATION_CHANNEL + AUTODEV_HOOKS_TOKEN). No-op when
  // unconfigured, so this is a zero-behavior-change default.
  registerInboundEscalationHook(api);
}

export default {
  id: "autodev-pipeline-signals",
  name: "Lullabeast Pipeline Signals",
  description:
    "Pipeline + Ideas signals: agent_end sentinels, before_agent_finalize structural revise, " +
    "Tier A stall activity stamps (model_call_*, after_tool_call, agent event streams), " +
    "and inbound_claim escalation-reply forwarding to the UI server",
  register,
};
