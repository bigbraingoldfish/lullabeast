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
