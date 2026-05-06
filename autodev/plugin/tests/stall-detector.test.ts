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
import { recordPipelineActivity } from "../src/stall-detector.ts";
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
