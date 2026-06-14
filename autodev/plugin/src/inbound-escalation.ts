/**
 * Inbound escalation forwarder (Part B3).
 *
 * Subscribes to OpenClaw's `inbound_claim` hook so an operator's reply on the
 * configured escalation channel is forwarded to the UI server's
 * `POST /api/escalation/inbound`, where it becomes a pipeline command (written
 * through the same files the dashboard uses). The escalation agent never applies
 * commands; the UI server is the only writer.
 *
 * Behavior:
 *   - Opt-in: no-op unless BOTH `AUTODEV_ESCALATION_CHANNEL` and
 *     `AUTODEV_HOOKS_TOKEN` are set (so the default is zero behavior change).
 *   - Only intercepts messages on the configured escalation channel.
 *   - On a 2xx from the server (the reply was accepted — a command written, or a
 *     clarification requested), CLAIMS the message (`{ handled: true }`) so it
 *     does NOT also spawn a dead-end escalation agent session. The server sends
 *     its own operator ack.
 *   - On any non-2xx (e.g. 404 = no waiting escalation) or a transport error,
 *     returns void so the message routes normally — fail-safe: it never loses a
 *     message and never double-handles.
 *   - Never throws.
 *
 * The functions take `env` / `fetchImpl` as parameters so they are unit-testable
 * without monkey-patching globals (matching the pattern in utils.ts).
 */

import type {
  OpenClawPluginApi,
  PluginHookInboundClaimContext,
  PluginHookInboundClaimEvent,
  PluginHookInboundClaimResult,
} from "./openclaw-types.d.ts";

export type InboundConfig = {
  channel: string;
  uiUrl: string;
  hooksToken: string;
};

const DEFAULT_UI_URL = "http://127.0.0.1:18790";
const FORWARD_TIMEOUT_MS = 10_000;

/**
 * Resolve inbound-escalation config from env, or null when not configured.
 * Requires the escalation channel + the hooks bearer token; the UI base URL
 * defaults to loopback:18790.
 */
export function resolveInboundConfig(
  env: NodeJS.ProcessEnv = process.env,
): InboundConfig | null {
  const channel = (env["AUTODEV_ESCALATION_CHANNEL"] ?? "").trim();
  const hooksToken = (env["AUTODEV_HOOKS_TOKEN"] ?? "").trim();
  if (!channel || !hooksToken) return null;
  const uiUrl = (env["AUTODEV_UI_URL"] ?? "").trim() || DEFAULT_UI_URL;
  return { channel, uiUrl, hooksToken };
}

/**
 * Extract a leading `prefix.<6hex>` correlation token from the operator's reply,
 * or undefined. The token couples the reply to the escalated project so the
 * server routes it back there (the B0 boundedness guarantee).
 */
export function extractReplyToken(text: string | undefined): string | undefined {
  if (typeof text !== "string") return undefined;
  const m = text.trim().match(/^(\S+\.[0-9a-f]{6})\b/);
  return m ? m[1] : undefined;
}

export type InboundPayload = {
  sender?: string;
  text: string;
  token?: string;
};

/** Build the `{sender, text, token?}` payload the server endpoint expects. */
export function buildInboundPayload(
  event: PluginHookInboundClaimEvent,
): InboundPayload {
  const text = typeof event.content === "string" ? event.content : "";
  const payload: InboundPayload = { text };
  const sender = event.senderName || event.senderId;
  if (sender) payload.sender = sender;
  const token = extractReplyToken(text);
  if (token) payload.token = token;
  return payload;
}

/**
 * Forward an inbound escalation-channel reply to the UI server. Returns
 * `{ handled: true }` to claim+suppress when the server accepts (2xx), else void
 * so the message routes normally. Never throws.
 */
export async function handleInboundClaim(
  event: PluginHookInboundClaimEvent,
  _ctx: PluginHookInboundClaimContext,
  env: NodeJS.ProcessEnv = process.env,
  fetchImpl: typeof fetch = fetch,
): Promise<PluginHookInboundClaimResult | void> {
  const config = resolveInboundConfig(env);
  if (!config) return; // inbound not configured -> route normally
  if (!event || event.channel !== config.channel) return; // not our channel
  const text = typeof event.content === "string" ? event.content.trim() : "";
  if (!text) return;

  const url = `${config.uiUrl.replace(/\/+$/, "")}/api/escalation/inbound`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FORWARD_TIMEOUT_MS);
  try {
    const res = await fetchImpl(url, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${config.hooksToken}`,
      },
      body: JSON.stringify(buildInboundPayload(event)),
      signal: controller.signal,
    });
    if (res.ok) {
      // The server accepted the reply (wrote a command or asked for
      // clarification) and acks the operator itself — claim the message so it
      // does not also spawn a dead-end escalation agent session.
      return { handled: true };
    }
    // Non-2xx (e.g. 404 no waiting escalation) -> let it route normally.
    return;
  } catch (err) {
    console.error(
      `[autodev-inbound] forward failed: ${(err as Error)?.message ?? String(err)}`,
    );
    return; // fail-safe: route normally
  } finally {
    clearTimeout(timer);
  }
}

/** Register the inbound_claim hook on the plugin API. */
export function registerInboundEscalationHook(api: OpenClawPluginApi): void {
  api.on(
    "inbound_claim",
    (event, ctx) => handleInboundClaim(event, ctx),
    { priority: 50 },
  );
}
