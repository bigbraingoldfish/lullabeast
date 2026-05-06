import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { handleAgentEnd } from "./agent-end-handler.ts";
import { handleBeforeAgentFinalize } from "./before-finalize-handler.ts";

export default definePluginEntry({
  id: "autodev-pipeline-signals",
  name: "AutoDev Pipeline Signals",
  description:
    "Writes agent completion sentinels via agent_end and requests structural " +
    "revision via before_agent_finalize for pipeline: sessions",

  register(api) {
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
  },
});
