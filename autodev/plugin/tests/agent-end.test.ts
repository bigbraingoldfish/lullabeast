/**
 * Tests for the agent_end hook handler.
 *
 * Verifies:
 *   - Non-pipeline sessions are ignored
 *   - Non-pipeline agent IDs are ignored
 *   - Sentinel is written when absent and artifacts dir exists
 *   - Handler is a no-op when sentinel already exists
 *   - Handler is a no-op when artifacts dir does not exist
 *   - Written sentinel is an empty file
 *   - OPENCLAW_ROOT env var used as fallback when workspaceDir absent
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { handleAgentEnd } from "../src/agent-end-handler.ts";

function makeTmpDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "autodev-plugin-test-"));
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

const baseEvent = {
  messages: [],
  success: true,
  durationMs: 1000,
};

// ─── filter: non-pipeline sessions ───────────────────────────────────────────

test("ignores sessions without pipeline: prefix", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-executor");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);

  handleAgentEnd(baseEvent, {
    agentId: "executor",
    sessionKey: "some-other-session",
    workspaceDir,
  });

  assert.equal(
    fs.existsSync(path.join(artifactsDir, "executor_output.done")),
    false,
    "must not write sentinel for non-pipeline session",
  );

  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("ignores sessions with undefined sessionKey", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-planner");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);

  handleAgentEnd(baseEvent, {
    agentId: "planner",
    sessionKey: undefined,
    workspaceDir,
  });

  assert.equal(
    fs.existsSync(path.join(artifactsDir, "planner_output.done")),
    false,
  );

  fs.rmSync(tmpDir, { recursive: true, force: true });
});

// ─── filter: non-pipeline agent IDs ──────────────────────────────────────────

test("ignores unknown agentId", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-escalation");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);

  handleAgentEnd(baseEvent, {
    agentId: "escalation",
    sessionKey: "pipeline:phase-1:CORE-1:escalation-attempt-1",
    workspaceDir,
  });

  assert.equal(
    fs.existsSync(path.join(artifactsDir, "escalation_output.done")),
    false,
  );

  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("ignores undefined agentId", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-planner");
  fs.mkdirSync(workspaceDir, { recursive: true });
  makeArtifactsDir(openclawRoot);

  handleAgentEnd(baseEvent, {
    agentId: undefined,
    sessionKey: "pipeline:phase-1:CORE-1:planner-attempt-1",
    workspaceDir,
  });

  fs.rmSync(tmpDir, { recursive: true, force: true });
});

// ─── core behavior ────────────────────────────────────────────────────────────

test("writes sentinel when absent for executor", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-executor");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);

  const donePath = path.join(artifactsDir, "executor_output.done");
  assert.equal(fs.existsSync(donePath), false, "precondition: no sentinel");

  handleAgentEnd({ ...baseEvent, success: false, error: "provider error" }, {
    agentId: "executor",
    sessionKey: "pipeline:phase-2:CORE-1:executor-attempt-1",
    workspaceDir,
  });

  assert.equal(
    fs.existsSync(donePath),
    true,
    "sentinel must be written when absent",
  );
  assert.equal(
    fs.readFileSync(donePath, "utf8"),
    "",
    "sentinel must be an empty file",
  );

  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("writes sentinel for planner", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-planner");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);

  handleAgentEnd(baseEvent, {
    agentId: "planner",
    sessionKey: "pipeline:phase-1:CORE-1:planner-attempt-1",
    workspaceDir,
  });

  assert.equal(
    fs.existsSync(path.join(artifactsDir, "planner_output.done")),
    true,
  );

  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("writes sentinel for reviewer", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-reviewer");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);

  handleAgentEnd(baseEvent, {
    agentId: "reviewer",
    sessionKey: "pipeline:phase-3:API-1:reviewer-attempt-1",
    workspaceDir,
  });

  assert.equal(
    fs.existsSync(path.join(artifactsDir, "reviewer_output.done")),
    true,
  );

  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("is no-op when sentinel already exists", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-executor");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);

  const donePath = path.join(artifactsDir, "executor_output.done");
  fs.writeFileSync(donePath, "");
  const mtimeBefore = fs.statSync(donePath).mtimeMs;

  // Allow 1 ms to pass so mtime would differ if file were rewritten
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 2);

  handleAgentEnd(baseEvent, {
    agentId: "executor",
    sessionKey: "pipeline:phase-2:CORE-1:executor-attempt-1",
    workspaceDir,
  });

  const mtimeAfter = fs.statSync(donePath).mtimeMs;
  assert.equal(
    mtimeAfter,
    mtimeBefore,
    "sentinel mtime must not change when file already existed",
  );

  fs.rmSync(tmpDir, { recursive: true, force: true });
});

test("is no-op when artifacts dir does not exist", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-executor");
  // Deliberately NOT creating the artifacts dir
  fs.mkdirSync(workspaceDir, { recursive: true });

  // Should not throw
  handleAgentEnd(baseEvent, {
    agentId: "executor",
    sessionKey: "pipeline:phase-2:CORE-1:executor-attempt-1",
    workspaceDir,
  });

  fs.rmSync(tmpDir, { recursive: true, force: true });
});

// ─── OPENCLAW_ROOT fallback ───────────────────────────────────────────────────

test("falls back to OPENCLAW_ROOT env var when workspaceDir absent", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const artifactsDir = makeArtifactsDir(openclawRoot);

  const originalEnv = process.env["OPENCLAW_ROOT"];
  process.env["OPENCLAW_ROOT"] = openclawRoot;

  try {
    handleAgentEnd(baseEvent, {
      agentId: "planner",
      sessionKey: "pipeline:phase-1:CORE-1:planner-attempt-1",
      workspaceDir: undefined,
    });

    assert.equal(
      fs.existsSync(path.join(artifactsDir, "planner_output.done")),
      true,
      "sentinel must be written using OPENCLAW_ROOT fallback",
    );
  } finally {
    if (originalEnv === undefined) {
      delete process.env["OPENCLAW_ROOT"];
    } else {
      process.env["OPENCLAW_ROOT"] = originalEnv;
    }
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});
