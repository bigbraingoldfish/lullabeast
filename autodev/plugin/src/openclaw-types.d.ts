/**
 * Minimal type declarations for the OpenClaw plugin SDK surfaces used by this
 * plugin. These are extracted from the installed SDK's .d.ts files and kept
 * here so the plugin compiles without taking `openclaw` as a package
 * dependency. The runtime types provided by OpenClaw at load time are the
 * authoritative source; these declarations must stay in sync with the SDK.
 *
 * Source: openclaw-2026.4.29 plugin-sdk hook-types.d.ts
 */

export type PluginHookAgentContext = {
  runId?: string;
  jobId?: string;
  agentId?: string;
  sessionKey?: string;
  sessionId?: string;
  workspaceDir?: string;
  modelProviderId?: string;
  modelId?: string;
};

export type PluginHookAgentEndEvent = {
  runId?: string;
  messages: unknown[];
  success: boolean;
  error?: string;
  durationMs?: number;
};

export type PluginHookBeforeAgentFinalizeEvent = {
  runId?: string;
  sessionId: string;
  sessionKey?: string;
  turnId?: string;
  provider?: string;
  model?: string;
  cwd?: string;
  transcriptPath?: string;
  stopHookActive: boolean;
  lastAssistantMessage?: string;
  messages?: unknown[];
};

export type PluginHookBeforeAgentFinalizeResult = {
  /** "revise" blocks finalization and requests another model pass. */
  action?: "continue" | "revise" | "finalize";
  reason?: string;
};

export type OpenClawPluginApi = {
  on(
    hookName: "agent_end",
    handler: (
      event: PluginHookAgentEndEvent,
      ctx: PluginHookAgentContext,
    ) => Promise<void> | void,
    opts?: { priority?: number; timeoutMs?: number },
  ): void;
  on(
    hookName: "before_agent_finalize",
    handler: (
      event: PluginHookBeforeAgentFinalizeEvent,
      ctx: PluginHookAgentContext,
    ) =>
      | Promise<PluginHookBeforeAgentFinalizeResult | void>
      | PluginHookBeforeAgentFinalizeResult
      | void,
    opts?: { priority?: number; timeoutMs?: number },
  ): void;
};

export type DefinePluginEntryOptions = {
  id: string;
  name: string;
  description: string;
  register: (api: OpenClawPluginApi) => void;
};

export declare function definePluginEntry(
  options: DefinePluginEntryOptions,
): unknown;

declare module "openclaw/plugin-sdk/plugin-entry" {
  export { definePluginEntry };
}
