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

/**
 * Inbound message hook (`inbound_claim`) — fires when a message arrives on a
 * connector channel, BEFORE it is routed to an agent. A handler may claim the
 * message (``{ handled: true }``) to suppress normal routing, or return void /
 * ``{ handled: false }`` to let it route. Used by the inbound-escalation
 * forwarder. Only the fields this plugin reads are typed precisely; the real
 * SDK event carries more.
 */
export type PluginHookInboundClaimEvent = {
  content: string;
  body?: string;
  channel: string;
  accountId?: string;
  conversationId?: string;
  senderId?: string;
  senderName?: string;
  senderUsername?: string;
  isGroup: boolean;
  messageId?: string;
  sessionKey?: string;
  runId?: string;
  metadata?: Record<string, unknown>;
};

export type PluginHookInboundClaimContext = {
  sessionKey?: string;
  runId?: string;
  senderId?: string;
  messageId?: string;
  // The real SDK context also carries pluginBinding etc.; unused here.
};

export type PluginHookInboundClaimResult = {
  handled: boolean;
  /** Optional native reply payload; unused by this plugin (the UI server acks). */
  reply?: unknown;
};

export type OpenClawPluginApi = {
  on(
    hookName: "inbound_claim",
    handler: (
      event: PluginHookInboundClaimEvent,
      ctx: PluginHookInboundClaimContext,
    ) =>
      | Promise<PluginHookInboundClaimResult | void>
      | PluginHookInboundClaimResult
      | void,
    opts?: { priority?: number; timeoutMs?: number },
  ): void;
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
