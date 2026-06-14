/**
 * Tests for the inbound escalation forwarder (Part B3).
 *
 * Verifies:
 *   - config is opt-in (null unless channel + hooks token are both set)
 *   - the correlation token is extracted from a leading prefix.<6hex>
 *   - the forward payload carries text / sender / token
 *   - the handler no-ops when unconfigured or on a different channel
 *   - a 2xx response claims the message (handled:true) with the right URL/auth/body
 *   - a non-2xx response does NOT claim (routes normally)
 *   - a transport error is fail-safe (does not claim, never throws)
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  buildInboundPayload,
  extractReplyToken,
  handleInboundClaim,
  resolveInboundConfig,
} from "../src/inbound-escalation.ts";

function event(overrides: Record<string, unknown> = {}) {
  return { content: "retry", channel: "signal", isGroup: false, ...overrides } as any;
}

test("resolveInboundConfig is opt-in (needs channel AND hooks token)", () => {
  assert.equal(resolveInboundConfig({}), null);
  assert.equal(resolveInboundConfig({ AUTODEV_ESCALATION_CHANNEL: "signal" }), null);
  assert.equal(resolveInboundConfig({ AUTODEV_HOOKS_TOKEN: "t" }), null);
});

test("resolveInboundConfig resolves channel/url/token (default url)", () => {
  assert.deepEqual(
    resolveInboundConfig({ AUTODEV_ESCALATION_CHANNEL: "signal", AUTODEV_HOOKS_TOKEN: "t" }),
    { channel: "signal", uiUrl: "http://127.0.0.1:18790", hooksToken: "t" },
  );
  const c = resolveInboundConfig({
    AUTODEV_ESCALATION_CHANNEL: "signal",
    AUTODEV_HOOKS_TOKEN: "t",
    AUTODEV_UI_URL: "http://ui:9000",
  });
  assert.equal(c?.uiUrl, "http://ui:9000");
});

test("extractReplyToken pulls a leading prefix.<6hex> token", () => {
  assert.equal(extractReplyToken("e1.ab12cd RESET_PHASE"), "e1.ab12cd");
  assert.equal(extractReplyToken("  run.00ff99 proceed"), "run.00ff99");
  assert.equal(extractReplyToken("retry"), undefined);
  assert.equal(extractReplyToken(undefined), undefined);
});

test("buildInboundPayload carries text, sender, token", () => {
  const p = buildInboundPayload(event({ content: "e1.ab12cd retry", senderName: "Op" }));
  assert.equal(p.text, "e1.ab12cd retry");
  assert.equal(p.sender, "Op");
  assert.equal(p.token, "e1.ab12cd");
});

test("handleInboundClaim no-ops when inbound unconfigured", async () => {
  let called = false;
  const fake = (async () => {
    called = true;
    return { ok: true } as Response;
  }) as typeof fetch;
  const r = await handleInboundClaim(event(), {} as any, {}, fake);
  assert.equal(r, undefined);
  assert.equal(called, false);
});

test("handleInboundClaim skips other channels", async () => {
  let called = false;
  const fake = (async () => {
    called = true;
    return { ok: true } as Response;
  }) as typeof fetch;
  const env = { AUTODEV_ESCALATION_CHANNEL: "signal", AUTODEV_HOOKS_TOKEN: "t" };
  const r = await handleInboundClaim(event({ channel: "discord" }), {} as any, env, fake);
  assert.equal(r, undefined);
  assert.equal(called, false);
});

test("handleInboundClaim claims on 2xx and posts the right url/auth/body", async () => {
  const calls: Array<{ url: string; init: any }> = [];
  const fake = (async (url: string, init: any) => {
    calls.push({ url, init });
    return { ok: true, status: 200 } as Response;
  }) as unknown as typeof fetch;
  const env = {
    AUTODEV_ESCALATION_CHANNEL: "signal",
    AUTODEV_HOOKS_TOKEN: "tok",
    AUTODEV_UI_URL: "http://ui:18790",
  };
  const r = await handleInboundClaim(
    event({ content: "e1.ab12cd retry", senderName: "Op" }),
    {} as any,
    env,
    fake,
  );
  assert.deepEqual(r, { handled: true });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://ui:18790/api/escalation/inbound");
  assert.equal(calls[0].init.headers.authorization, "Bearer tok");
  const body = JSON.parse(calls[0].init.body);
  assert.equal(body.text, "e1.ab12cd retry");
  assert.equal(body.token, "e1.ab12cd");
  assert.equal(body.sender, "Op");
});

test("handleInboundClaim does NOT claim on non-2xx (routes normally)", async () => {
  const fake = (async () => ({ ok: false, status: 404 } as Response)) as typeof fetch;
  const env = { AUTODEV_ESCALATION_CHANNEL: "signal", AUTODEV_HOOKS_TOKEN: "tok" };
  const r = await handleInboundClaim(event({ content: "hi" }), {} as any, env, fake);
  assert.equal(r, undefined);
});

test("handleInboundClaim is fail-safe on a transport error", async () => {
  const fake = (async () => {
    throw new Error("network down");
  }) as typeof fetch;
  const env = { AUTODEV_ESCALATION_CHANNEL: "signal", AUTODEV_HOOKS_TOKEN: "tok" };
  const r = await handleInboundClaim(event(), {} as any, env, fake);
  assert.equal(r, undefined);
});
