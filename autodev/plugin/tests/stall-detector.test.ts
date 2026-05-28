/**
 * Tests for pipeline session stall activity recording (Tier A hooks).
 *
 * recordPipelineActivity is invoked from model_call_started, model_call_ended,
 * and after_tool_call — it touches {agentId}_activity.stamp in the artifacts dir.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import {
  recordPipelineActivity,
  recordPipelineActivityFromAgentEvent,
  registerStallDetectorHooks,
} from "../src/stall-detector.ts";
import type { PluginHookAgentContext } from "../src/openclaw-types.d.ts";

function makeTmpDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "autodev-stall-test-"));
}

function makeArtifactsDir(openclawRoot: string): string {
  const artifactsDir = path.join(
    openclawRoot,
    "pipeline-project",
    ".autodev",
    "pipeline",
  );
  fs.mkdirSync(artifactsDir, { recursive: true });
  return artifactsDir;
}

function ctxExecutor(workspaceDir: string): PluginHookAgentContext {
  return {
    agentId: "executor",
    sessionKey: "pipeline:phase-2:CORE-1:executor-attempt-1",
    workspaceDir,
  };
}

test("writes activity stamp when sessionKey is OpenClaw-prefixed agent:{role}:pipeline:... (production format)", () => {
  // Regression test for the live-pipeline bug observed on phase-4:ui-e1
  // (and CORE-E6 / STAT-E1 before it).  In production the gateway's
  // hookCtx.sessionKey arrives with the OpenClaw ``agent:{role}:`` prefix
  // (e.g. ``agent:executor:pipeline:phase-4:ui-e1:executor-attempt-1``).
  // The plugin's ``isPipelineSession`` only matched the bare ``pipeline:``
  // prefix, so the first branch in ``recordPipelineActivity`` was skipped
  // and the runId fallback didn't fire either (model-call runIds don't
  // equal sessions.json sessionId).  Net effect: stamp never refreshed
  // during model work → orchestrator's startup_grace expired and the
  // attempt was aborted while the model was still doing real work.
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-executor");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);
  const stampPath = path.join(artifactsDir, "executor_activity.stamp");

  try {
    assert.equal(fs.existsSync(stampPath), false);
    recordPipelineActivity({
      agentId: "executor",
      // This is exactly the shape the gateway passes in production —
      // confirmed via `[diagnostic] stalled session ... sessionKey=...`
      // in /tmp/openclaw/openclaw-*.log.
      sessionKey: "agent:executor:pipeline:phase-4:ui-e1:executor-attempt-1",
      workspaceDir,
    });
    assert.equal(
      fs.existsSync(stampPath),
      true,
      "stamp must be touched when sessionKey carries the OpenClaw " +
        "``agent:{role}:`` prefix (production format)",
    );
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("writes activity stamp on model_call_started for pipeline executor session", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-executor");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);
  const stampPath = path.join(artifactsDir, "executor_activity.stamp");

  try {
    assert.equal(fs.existsSync(stampPath), false);
    recordPipelineActivity(ctxExecutor(workspaceDir));
    assert.equal(fs.existsSync(stampPath), true);
    assert.equal(fs.readFileSync(stampPath, "utf8"), "");
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("writes activity stamp on model_call_ended path (same handler)", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-planner");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);
  const stampPath = path.join(artifactsDir, "planner_activity.stamp");

  try {
    recordPipelineActivity({
      agentId: "planner",
      sessionKey: "pipeline:phase-1:CORE-1:planner-attempt-1",
      workspaceDir,
    });
    assert.equal(fs.existsSync(stampPath), true);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("writes activity stamp on after_tool_call path (same handler)", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-reviewer");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);
  const stampPath = path.join(artifactsDir, "reviewer_activity.stamp");

  try {
    recordPipelineActivity({
      agentId: "reviewer",
      sessionKey: "pipeline:phase-3:API-1:reviewer-attempt-1",
      workspaceDir,
    });
    assert.equal(fs.existsSync(stampPath), true);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("writes activity stamp when hook ctx lacks agentId but event has sessionKey", () => {
  const tmpDir = makeTmpDir();
  const artifactsDir = path.join(tmpDir, "pipeline-project", ".autodev", "pipeline");
  const workspaceDir = path.join(tmpDir, "workspace-executor");
  const stampPath = path.join(artifactsDir, "executor_activity.stamp");
  fs.mkdirSync(artifactsDir, { recursive: true });
  fs.mkdirSync(workspaceDir, { recursive: true });

  try {
    recordPipelineActivity(
      { workspaceDir },
      { sessionKey: "pipeline:phase-4:CORE-E5:executor-attempt-6" },
    );

    assert.equal(fs.existsSync(stampPath), true);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("writes activity stamp when hook only has runId resolvable from sessions", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const artifactsDir = path.join(openclawRoot, "pipeline-project", ".autodev", "pipeline");
  const sessionsDir = path.join(openclawRoot, "agents", "executor", "sessions");
  const stampPath = path.join(artifactsDir, "executor_activity.stamp");
  fs.mkdirSync(artifactsDir, { recursive: true });
  fs.mkdirSync(sessionsDir, { recursive: true });
  fs.writeFileSync(
    path.join(sessionsDir, "sessions.json"),
    JSON.stringify({
      "agent:executor:pipeline:phase-4:core-e5:executor-attempt-6": {
        sessionId: "run-only-1",
      },
    }),
  );
  const originalStateDir = process.env["OPENCLAW_STATE_DIR"];

  try {
    process.env["OPENCLAW_STATE_DIR"] = openclawRoot;
    recordPipelineActivity({}, { runId: "run-only-1" });

    assert.equal(fs.existsSync(stampPath), true);
  } finally {
    if (originalStateDir === undefined) delete process.env["OPENCLAW_STATE_DIR"];
    else process.env["OPENCLAW_STATE_DIR"] = originalStateDir;
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("does not write stamp for non-pipeline session", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-executor");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);

  try {
    recordPipelineActivity({
      agentId: "executor",
      sessionKey: "some-other-session",
      workspaceDir,
    });
    assert.equal(fs.existsSync(path.join(artifactsDir, "executor_activity.stamp")), false);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("does not write stamp for unknown agentId", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-escalation");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);

  try {
    recordPipelineActivity({
      agentId: "escalation",
      sessionKey: "pipeline:phase-1:CORE-1:escalation-attempt-1",
      workspaceDir,
    });
    assert.equal(fs.existsSync(path.join(artifactsDir, "escalation_activity.stamp")), false);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("updates stamp mtime on repeat events", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-executor");
  fs.mkdirSync(workspaceDir, { recursive: true });
  makeArtifactsDir(openclawRoot);
  const c = ctxExecutor(workspaceDir);
  const stampPath = path.join(
    path.dirname(workspaceDir),
    "pipeline-project",
    ".autodev",
    "pipeline",
    "executor_activity.stamp",
  );

  try {
    recordPipelineActivity(c);
    const m1 = fs.statSync(stampPath).mtimeMs;
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 5);
    recordPipelineActivity(c);
    const m2 = fs.statSync(stampPath).mtimeMs;
    assert.ok(m2 >= m1, "mtime must not go backwards on second touch");
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("no-op when artifacts dir does not exist", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-executor");
  fs.mkdirSync(workspaceDir, { recursive: true });
  // Deliberately NOT creating pipeline-project/.autodev/pipeline

  try {
    assert.doesNotThrow(() => {
      recordPipelineActivity(ctxExecutor(workspaceDir));
    });
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("prd-creator writes ideas activity stamp for ideas:session key", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-prd-creator");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const ideaId = "stamp-idea";
  const stampPath = path.join(openclawRoot, "ideas", ideaId, "prd_creator_activity.stamp");

  try {
    assert.equal(fs.existsSync(stampPath), false);
    recordPipelineActivity({
      agentId: "prd-creator",
      sessionKey: `ideas:${ideaId}:session-1`,
      workspaceDir,
    });
    assert.equal(fs.existsSync(stampPath), true);
    assert.equal(fs.readFileSync(stampPath, "utf8"), "");
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("prd-creator writes ideas stamp for production agent:prd-creator:ideas:... key shape (regression)", () => {
  // Regression test for the live-Ideas-chat bug observed on the Untitled
  // Balloon Popping Game session — the same gateway-prefix issue that
  // hit pipeline sessions in CORE-E6 / STAT-E1, now caught for Ideas.
  //
  // In production the gateway's hookCtx.sessionKey arrives with the
  // OpenClaw ``agent:{role}:`` prefix (confirmed via chat URL
  // ``agent:prd-creator:ideas:{id}:session-N``).  ``isIdeasSession`` and
  // ``extractIdeasIdFromSessionKey`` previously only matched the bare
  // ``ideas:`` form, so this branch in ``recordPipelineActivity`` was
  // skipped for every real chat turn.  Net effect: stamp never refreshed,
  // ``poll_for_sentinel``-equivalent in the Ideas backend fired
  // ``"no_first_activity"`` after the 30 s ``startup_grace`` even when
  // the model was actively working — the user saw a false 408 timeout
  // while the agent eventually produced a real reply.
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-prd-creator");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const ideaId = "prod-prefix-idea";
  const stampPath = path.join(openclawRoot, "ideas", ideaId, "prd_creator_activity.stamp");

  try {
    assert.equal(fs.existsSync(stampPath), false);
    recordPipelineActivity({
      agentId: "prd-creator",
      // This is exactly the shape the gateway passes in production —
      // confirmed by the chat URL the user pasted from the Untitled
      // Balloon Popping Game session.
      sessionKey: `agent:prd-creator:ideas:${ideaId}:session-13`,
      workspaceDir,
    });
    assert.equal(
      fs.existsSync(stampPath),
      true,
      "stamp must be touched when sessionKey carries the OpenClaw " +
        "``agent:{role}:`` prefix (production format for Ideas)",
    );
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("prd-creator writes ideas stamp for production agent:prd-creator:ideas:convert key shape", () => {
  // Companion to the production-prefix regression above — same fix must
  // cover the convert-flow session key too.
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-prd-creator");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const ideaId = "prod-convert-idea";
  const stampPath = path.join(openclawRoot, "ideas", ideaId, "prd_creator_activity.stamp");

  try {
    recordPipelineActivity({
      agentId: "prd-creator",
      sessionKey: `agent:prd-creator:ideas:${ideaId}:convert-1700000000001`,
      workspaceDir,
    });
    assert.equal(fs.existsSync(stampPath), true);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("prd-creator writes ideas stamp for ideas:convert key shape", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-prd-creator");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const ideaId = "conv-idea";
  const stampPath = path.join(openclawRoot, "ideas", ideaId, "prd_creator_activity.stamp");

  try {
    recordPipelineActivity({
      agentId: "prd-creator",
      sessionKey: `ideas:${ideaId}:convert-1700000000001`,
      workspaceDir,
    });
    assert.equal(fs.existsSync(stampPath), true);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("non-prd-creator does not write ideas stamp even for ideas session key", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-roadmap-converter");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const stampPath = path.join(openclawRoot, "ideas", "z", "prd_creator_activity.stamp");

  try {
    recordPipelineActivity({
      agentId: "roadmap-converter",
      sessionKey: "ideas:z:convert-1",
      workspaceDir,
    });
    assert.equal(fs.existsSync(stampPath), false);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------------
// Readiness-session stamp isolation (overlap-masking guard).
//
// The background readiness assessment (`ideas:{id}:readiness`) is auto-fired
// fire-and-forget after EVERY chat turn (ui/server.py
// `_trigger_readiness_assessment`).  It runs as the same prd-creator agent and
// carries the same ideaId as the foreground chat turn, so before this fix it
// warmed the SAME `prd_creator_activity.stamp` that the chat-turn poller
// (`_poll_sentinel_with_idle_detect`) watches — and that poller is the SOLE
// consumer of the chat stamp.  A readiness run from turn N that overlaps a new
// turn N+1 could therefore keep the stamp fresh and mask a genuinely-stalled
// foreground turn (it would survive the 300 s stall threshold and only trip
// the 900 s infra backstop).  Readiness must warm its OWN stamp so the chat
// stamp means exactly "the foreground chat turn is alive".
// ---------------------------------------------------------------------------

const IDEAS_CHAT_STAMP = "prd_creator_activity.stamp";
const IDEAS_READINESS_STAMP = "prd_creator_readiness_activity.stamp";

test("readiness session writes its own stamp, not the chat-turn stamp (bare key)", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-prd-creator");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const ideaId = "readiness-iso";
  const chatStamp = path.join(openclawRoot, "ideas", ideaId, IDEAS_CHAT_STAMP);
  const readinessStamp = path.join(
    openclawRoot,
    "ideas",
    ideaId,
    IDEAS_READINESS_STAMP,
  );

  try {
    recordPipelineActivity({
      agentId: "prd-creator",
      sessionKey: `ideas:${ideaId}:readiness`,
      workspaceDir,
    });
    assert.equal(
      fs.existsSync(readinessStamp),
      true,
      "readiness session must warm prd_creator_readiness_activity.stamp",
    );
    assert.equal(
      fs.existsSync(chatStamp),
      false,
      "readiness session must NOT warm the chat-turn stamp — a stale " +
        "readiness run would otherwise mask a stalled foreground turn",
    );
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("readiness session writes its own stamp for production agent:prd-creator:... key shape", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-prd-creator");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const ideaId = "readiness-iso-prefixed";
  const chatStamp = path.join(openclawRoot, "ideas", ideaId, IDEAS_CHAT_STAMP);
  const readinessStamp = path.join(
    openclawRoot,
    "ideas",
    ideaId,
    IDEAS_READINESS_STAMP,
  );

  try {
    recordPipelineActivity({
      agentId: "prd-creator",
      // Production gateway-normalised shape (matches the readiness webhook's
      // `ideas:{id}:readiness` sessionKey after the `agent:{role}:` prefix).
      sessionKey: `agent:prd-creator:ideas:${ideaId}:readiness`,
      workspaceDir,
    });
    assert.equal(fs.existsSync(readinessStamp), true);
    assert.equal(fs.existsSync(chatStamp), false);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("chat-turn session still writes the chat stamp, not the readiness stamp (regression)", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-prd-creator");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const ideaId = "chat-keeps-chat-stamp";
  const chatStamp = path.join(openclawRoot, "ideas", ideaId, IDEAS_CHAT_STAMP);
  const readinessStamp = path.join(
    openclawRoot,
    "ideas",
    ideaId,
    IDEAS_READINESS_STAMP,
  );

  try {
    recordPipelineActivity({
      agentId: "prd-creator",
      sessionKey: `agent:prd-creator:ideas:${ideaId}:session-7`,
      workspaceDir,
    });
    assert.equal(
      fs.existsSync(chatStamp),
      true,
      "chat turn must still warm the chat stamp the poller watches",
    );
    assert.equal(
      fs.existsSync(readinessStamp),
      false,
      "chat turn must NOT warm the readiness stamp",
    );
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("registers low-level agent event subscription for runtime activity", () => {
  const calls: Array<{ method: string; id?: string; name?: string; streams?: string[] }> = [];

  registerStallDetectorHooks({
    on(name) {
      calls.push({ method: "on", name });
    },
    registerAgentEventSubscription(subscription) {
      calls.push({
        method: "registerAgentEventSubscription",
        id: subscription.id,
        streams: subscription.streams,
      });
    },
  });

  assert.deepEqual(
    calls.find((call) => call.method === "registerAgentEventSubscription"),
    {
      method: "registerAgentEventSubscription",
      id: "autodev-pipeline-activity",
      streams: ["lifecycle", "assistant", "tool", "item", "command_output"],
    },
  );
  assert.ok(calls.some((call) => call.method === "on" && call.name === "after_tool_call"));
});

test("agent event with sessionKey touches pipeline activity stamp", () => {
  const tmpDir = makeTmpDir();
  const pipelineRoot = path.join(tmpDir, "pipeline-root");
  const artifactsDir = path.join(pipelineRoot, "pipeline-project", ".autodev", "pipeline");
  const stampPath = path.join(artifactsDir, "executor_activity.stamp");
  fs.mkdirSync(artifactsDir, { recursive: true });

  try {
    recordPipelineActivityFromAgentEvent(
      {
        runId: "run-1",
        sessionKey: "pipeline:phase-4:CORE-E5:executor-attempt-3",
        stream: "tool",
        data: { phase: "start" },
      },
      { AUTODEV_PIPELINE_ROOT: pipelineRoot },
    );

    assert.equal(fs.existsSync(stampPath), true);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("agent event with runId resolves sessions.json and touches stamp", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const artifactsDir = path.join(openclawRoot, "pipeline-project", ".autodev", "pipeline");
  const sessionsDir = path.join(openclawRoot, "agents", "executor", "sessions");
  const stampPath = path.join(artifactsDir, "executor_activity.stamp");
  fs.mkdirSync(artifactsDir, { recursive: true });
  fs.mkdirSync(sessionsDir, { recursive: true });
  fs.writeFileSync(
    path.join(sessionsDir, "sessions.json"),
    JSON.stringify({
      "agent:executor:pipeline:phase-4:core-e5:executor-attempt-3": {
        sessionId: "run-xyz",
      },
    }),
  );

  try {
    recordPipelineActivityFromAgentEvent(
      {
        runId: "run-xyz",
        stream: "assistant",
        data: { text: "working" },
      },
      { OPENCLAW_STATE_DIR: openclawRoot },
    );

    assert.equal(fs.existsSync(stampPath), true);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("agent event with ideas: sessionKey touches ideas activity stamp", () => {
  // Reliability backstop for the Ideas chat: if OpenClaw ever drops one of
  // the typed model_call/tool hooks the typed-hook Ideas branch in
  // recordPipelineActivity relies on, the agent-event-stream subscription
  // must still refresh prd_creator_activity.stamp so the UI's stamp poller
  // does not falsely time out a live conversation.  Mirrors the pipeline
  // backstop covered by the surrounding two tests.
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  fs.mkdirSync(openclawRoot, { recursive: true });
  const ideaId = "ideas-evt-id";
  const stampPath = path.join(
    openclawRoot,
    "ideas",
    ideaId,
    "prd_creator_activity.stamp",
  );

  try {
    assert.equal(fs.existsSync(stampPath), false);
    recordPipelineActivityFromAgentEvent(
      {
        runId: "ideas-run-1",
        sessionKey: `ideas:${ideaId}:session-3`,
        stream: "tool",
        data: { name: "Edit" },
      },
      { OPENCLAW_ROOT: openclawRoot },
    );
    assert.equal(
      fs.existsSync(stampPath),
      true,
      "agent-event-stream backstop must refresh the Ideas stamp when the " +
        "sessionKey is prefixed with `ideas:`",
    );
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("agent event with production agent:prd-creator:ideas:... sessionKey touches ideas stamp (regression)", () => {
  // Companion regression to the recordPipelineActivity production-prefix
  // fix above — the agent-event-stream backstop must ALSO recognise the
  // gateway-prefixed form, otherwise a typed-hook outage on Ideas takes
  // the stamp completely offline (the very scenario this backstop exists
  // to prevent).
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  fs.mkdirSync(openclawRoot, { recursive: true });
  const ideaId = "evt-prefix-idea";
  const stampPath = path.join(
    openclawRoot,
    "ideas",
    ideaId,
    "prd_creator_activity.stamp",
  );

  try {
    assert.equal(fs.existsSync(stampPath), false);
    recordPipelineActivityFromAgentEvent(
      {
        runId: "ideas-run-prod",
        sessionKey: `agent:prd-creator:ideas:${ideaId}:session-13`,
        stream: "tool",
        data: { name: "Edit" },
      },
      { OPENCLAW_ROOT: openclawRoot },
    );
    assert.equal(
      fs.existsSync(stampPath),
      true,
      "agent-event-stream backstop must refresh the Ideas stamp when " +
        "the sessionKey carries the OpenClaw `agent:{role}:` prefix " +
        "(production format)",
    );
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("agent event without OPENCLAW_ROOT falls back to HOME/.openclaw (Ideas)", () => {
  // Live regression from the Cursor MCP-browser validation run on
  // 716746ab-ea9d-4ddf-ad1a-c7bced673a87: the gateway's systemd unit has
  // ``Environment=HOME=/home/pi`` but does NOT set ``OPENCLAW_ROOT``.
  // ``recordPipelineActivityFromAgentEvent`` was therefore silently
  // skipping the Ideas branch because ``resolveIdeasRootFromWorkspace
  // (undefined, env)`` returned null, leaving the stamp stale during the
  // 2-minute model-generation window and tripping ``stall_threshold``.
  // The plugin's own ``resolveOpenClawStateDirFromEnv`` already falls
  // back to ``HOME/.openclaw`` — the Ideas resolver must do the same so
  // operators don't have to remember a second env var.
  const tmpDir = makeTmpDir();
  const fakeHome = path.join(tmpDir, "home-pi");
  fs.mkdirSync(fakeHome, { recursive: true });
  const ideaId = "no-env-idea";
  const expectedStamp = path.join(
    fakeHome,
    ".openclaw",
    "ideas",
    ideaId,
    "prd_creator_activity.stamp",
  );

  try {
    assert.equal(fs.existsSync(expectedStamp), false);
    recordPipelineActivityFromAgentEvent(
      {
        runId: "ideas-no-env",
        sessionKey: `agent:prd-creator:ideas:${ideaId}:session-1`,
        stream: "assistant",
        data: { text: "model streaming output" },
      },
      // Mirror the production systemd unit: HOME set, no OPENCLAW_ROOT,
      // no OPENCLAW_STATE_DIR.
      { HOME: fakeHome },
    );
    assert.equal(
      fs.existsSync(expectedStamp),
      true,
      "stamp must be written under HOME/.openclaw when OPENCLAW_ROOT is unset",
    );
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("agent event with ideas: sessionKey does not write pipeline stamp", () => {
  // The new Ideas branch must not fall through into the pipeline branch.
  // If it did, a pipeline-stamp file for some unrelated agent id would
  // appear under pipeline-project/.autodev/pipeline/ — easy to spot in tests
  // and easy to miss in production.
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const pipelineArtifactsDir = path.join(
    openclawRoot,
    "pipeline-project",
    ".autodev",
    "pipeline",
  );
  fs.mkdirSync(pipelineArtifactsDir, { recursive: true });

  try {
    recordPipelineActivityFromAgentEvent(
      {
        runId: "ideas-run-2",
        sessionKey: "ideas:no-leak:session-1",
        stream: "tool",
      },
      { OPENCLAW_ROOT: openclawRoot, AUTODEV_PIPELINE_ROOT: openclawRoot },
    );
    const leaked = fs.readdirSync(pipelineArtifactsDir);
    assert.deepEqual(
      leaked,
      [],
      "Ideas agent event must not also touch the pipeline stamps " +
        "(branch fell through)",
    );
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("agent-event readiness session writes its own stamp, not the chat stamp", () => {
  // The agent-event-stream backstop must apply the same readiness/chat
  // separation as the typed-hook path — otherwise a typed-hook outage would
  // re-route overlapping readiness activity back onto the chat stamp and the
  // masking risk returns through the back door.
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  fs.mkdirSync(openclawRoot, { recursive: true });
  const ideaId = "readiness-evt-iso";
  const chatStamp = path.join(
    openclawRoot,
    "ideas",
    ideaId,
    "prd_creator_activity.stamp",
  );
  const readinessStamp = path.join(
    openclawRoot,
    "ideas",
    ideaId,
    "prd_creator_readiness_activity.stamp",
  );

  try {
    recordPipelineActivityFromAgentEvent(
      {
        runId: "readiness-run-1",
        sessionKey: `agent:prd-creator:ideas:${ideaId}:readiness`,
        stream: "assistant",
        data: { text: "scoring readiness" },
      },
      { OPENCLAW_ROOT: openclawRoot },
    );
    assert.equal(
      fs.existsSync(readinessStamp),
      true,
      "agent-event readiness must warm the readiness stamp",
    );
    assert.equal(
      fs.existsSync(chatStamp),
      false,
      "agent-event readiness must not warm the chat stamp",
    );
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});
