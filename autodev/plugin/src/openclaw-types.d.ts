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

/** Sanitized provider/model call metadata (observation hooks). */
export type PluginHookModelCallEvent = {
  runId?: string;
  callId?: string;
  provider?: string;
  model?: string;
  durationMs?: number;
  outcome?: string;
};

export type PluginHookAfterToolCallEvent = {
  toolName: string;
  runId?: string;
  toolCallId?: string;
  durationMs?: number;
  isError?: boolean;
};

export type PluginAgentEvent = {
  runId?: string;
  sessionKey?: string;
  stream?: string;
  data?: Record<string, unknown>;
};

export type PluginAgentEventSubscription = {
  id: string;
  streams?: string[];
  handle: (
    event: PluginAgentEvent,
    ctx: {
      getRunContext?: (namespace: string) => unknown;
      setRunContext?: (namespace: string, value: unknown) => void;
      clearRunContext?: (namespace: string) => void;
    },
  ) => Promise<void> | void;
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
  on(
    hookName: "model_call_started",
    handler: (
      event: PluginHookModelCallEvent,
      ctx: PluginHookAgentContext,
    ) => Promise<void> | void,
    opts?: { priority?: number; timeoutMs?: number },
  ): void;
  on(
    hookName: "model_call_ended",
    handler: (
      event: PluginHookModelCallEvent,
      ctx: PluginHookAgentContext,
    ) => Promise<void> | void,
    opts?: { priority?: number; timeoutMs?: number },
  ): void;
  on(
    hookName: "after_tool_call",
    handler: (
      event: PluginHookAfterToolCallEvent,
      ctx: PluginHookAgentContext,
    ) => Promise<void> | void,
    opts?: { priority?: number; timeoutMs?: number },
  ): void;
  registerAgentEventSubscription?(
    subscription: PluginAgentEventSubscription,
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
