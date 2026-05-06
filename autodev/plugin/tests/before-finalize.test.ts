/**
 * Tests for the before_agent_finalize hook handler.
 *
 * Verifies:
 *   - Non-pipeline sessions return void (no decision)
 *   - Non-pipeline agent IDs return void
 *   - Complete, well-formed planner output → void (no revision)
 *   - Planner with missing implementation_plan → revise
 *   - Planner with empty tdd_test_structure → revise
 *   - Planner with pass_criteria missing condition fields → revise
 *   - Executor with complete status and valid structure → void
 *   - Executor with non-"complete" status → void (hard gate handles it)
 *   - Executor missing test_results.all_passing → revise
 *   - Reviewer with well-formed output → void
 *   - Reviewer with missing blocking_issues → revise
 *   - Reviewer with malformed blocking_issues items → revise
 *   - Reviewer with missing integration_tests_passing → revise
 *   - Missing output file → revise
 *   - Malformed JSON output file → revise
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { handleBeforeAgentFinalize } from "../src/before-finalize-handler.ts";
import type { PluginHookBeforeAgentFinalizeEvent } from "../src/openclaw-types.d.ts";

function makeTmpDir(): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), "autodev-finalize-test-"));
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

function writeOutput(
  artifactsDir: string,
  agent: string,
  data: unknown,
): void {
  fs.writeFileSync(
    path.join(artifactsDir, `${agent}_output.json`),
    JSON.stringify(data),
    "utf8",
  );
}

const baseEvent: PluginHookBeforeAgentFinalizeEvent = {
  sessionId: "sess-1",
  stopHookActive: false,
};

// ─── valid planner output ────────────────────────────────────────────────────

const validPlanner = {
  implementation_plan: ["Task 1", "Task 2"],
  tdd_test_structure: ["tests/test_foo.py"],
  pass_criteria: [{ condition: "All tests pass" }],
};

// ─── valid executor output ────────────────────────────────────────────────────

const validExecutor = {
  status: "complete",
  tests_written: ["tests/test_foo.py"],
  test_results: { all_passing: true },
  file_manifest: ["src/foo.py"],
  lint_passing: true,
};

// ─── valid reviewer output ────────────────────────────────────────────────────

const validReviewer = {
  blocking_issues: [],
  integration_tests_passing: true,
  phase_intent_validated: true,
};

// ─── helpers ─────────────────────────────────────────────────────────────────

function runHandler(
  agentId: string,
  workspaceDir: string,
  sessionKey = `pipeline:phase-1:CORE-1:${agentId}-attempt-1`,
) {
  return handleBeforeAgentFinalize(
    { ...baseEvent, sessionKey },
    { agentId, sessionKey, workspaceDir },
  );
}

// ─── filter tests ─────────────────────────────────────────────────────────────

test("returns void for non-pipeline session", () => {
  const tmpDir = makeTmpDir();
  try {
    const result = handleBeforeAgentFinalize(baseEvent, {
      agentId: "planner",
      sessionKey: "some-other-session",
      workspaceDir: tmpDir,
    });
    assert.equal(result, undefined);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("returns void for escalation agentId", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-escalation");
  fs.mkdirSync(workspaceDir, { recursive: true });
  makeArtifactsDir(openclawRoot);

  try {
    const result = handleBeforeAgentFinalize(baseEvent, {
      agentId: "escalation",
      sessionKey: "pipeline:phase-1:CORE-1:escalation-attempt-1",
      workspaceDir,
    });
    assert.equal(result, undefined);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

// ─── missing output file ──────────────────────────────────────────────────────

test("requests revision when output file is missing", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-planner");
  fs.mkdirSync(workspaceDir, { recursive: true });
  makeArtifactsDir(openclawRoot); // creates dir but NOT the output file

  try {
    const result = runHandler("planner", workspaceDir);
    assert.ok(result, "must return a result");
    assert.equal(result?.action, "revise");
    assert.ok(
      result?.reason?.includes("planner_output.json"),
      "reason must mention the file",
    );
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("requests revision when output file is malformed JSON", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-executor");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);
  fs.writeFileSync(path.join(artifactsDir, "executor_output.json"), "{not valid");

  try {
    const result = runHandler("executor", workspaceDir);
    assert.equal(result?.action, "revise");
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

// ─── planner ─────────────────────────────────────────────────────────────────

test("returns void for complete planner output", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-planner");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);
  writeOutput(artifactsDir, "planner", validPlanner);

  try {
    const result = runHandler("planner", workspaceDir);
    assert.equal(result, undefined);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("requests revision when planner implementation_plan is missing", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-planner");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);
  writeOutput(artifactsDir, "planner", {
    ...validPlanner,
    implementation_plan: undefined,
  });

  try {
    const result = runHandler("planner", workspaceDir);
    assert.equal(result?.action, "revise");
    assert.ok(result?.reason?.includes("implementation_plan"));
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("requests revision when planner tdd_test_structure is empty", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-planner");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);
  writeOutput(artifactsDir, "planner", { ...validPlanner, tdd_test_structure: [] });

  try {
    const result = runHandler("planner", workspaceDir);
    assert.equal(result?.action, "revise");
    assert.ok(result?.reason?.includes("tdd_test_structure"));
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("requests revision when planner pass_criteria item lacks condition", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-planner");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);
  writeOutput(artifactsDir, "planner", {
    ...validPlanner,
    pass_criteria: [{ description: "no condition key here" }],
  });

  try {
    const result = runHandler("planner", workspaceDir);
    assert.equal(result?.action, "revise");
    assert.ok(result?.reason?.includes("pass_criteria"));
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

// ─── executor ────────────────────────────────────────────────────────────────

test("returns void for complete executor output", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-executor");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);
  writeOutput(artifactsDir, "executor", validExecutor);

  try {
    const result = runHandler("executor", workspaceDir);
    assert.equal(result, undefined);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("returns void for executor with status=stuck (hard gate handles it)", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-executor");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);
  writeOutput(artifactsDir, "executor", {
    status: "stuck",
    failure_reason: "hit tool call limit",
  });

  try {
    const result = runHandler("executor", workspaceDir);
    assert.equal(result, undefined, "non-complete status: defer to hard gate");
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("requests revision when executor test_results.all_passing is missing", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-executor");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);
  writeOutput(artifactsDir, "executor", {
    ...validExecutor,
    test_results: {},
  });

  try {
    const result = runHandler("executor", workspaceDir);
    assert.equal(result?.action, "revise");
    assert.ok(result?.reason?.includes("test_results.all_passing"));
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("requests revision when executor tests_written is missing", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-executor");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);
  const { tests_written: _, ...withoutTests } = validExecutor;
  writeOutput(artifactsDir, "executor", withoutTests);

  try {
    const result = runHandler("executor", workspaceDir);
    assert.equal(result?.action, "revise");
    assert.ok(result?.reason?.includes("tests_written"));
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

// ─── reviewer ────────────────────────────────────────────────────────────────

test("returns void for complete reviewer output", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-reviewer");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);
  writeOutput(artifactsDir, "reviewer", validReviewer);

  try {
    const result = runHandler("reviewer", workspaceDir);
    assert.equal(result, undefined);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("returns void for reviewer with blocking issues (gate decides routing)", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-reviewer");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);
  writeOutput(artifactsDir, "reviewer", {
    blocking_issues: [
      {
        description: "test fails",
        attribution: "impl",
        affected_file: "src/foo.py",
      },
    ],
    integration_tests_passing: false,
    phase_intent_validated: true,
  });

  try {
    const result = runHandler("reviewer", workspaceDir);
    assert.equal(result, undefined, "well-formed output with issues: no revision needed");
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("requests revision when reviewer blocking_issues is absent", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-reviewer");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);
  writeOutput(artifactsDir, "reviewer", {
    integration_tests_passing: true,
    phase_intent_validated: true,
  });

  try {
    const result = runHandler("reviewer", workspaceDir);
    assert.equal(result?.action, "revise");
    assert.ok(result?.reason?.includes("blocking_issues"));
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("requests revision when reviewer blocking_issues item missing attribution", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-reviewer");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);
  writeOutput(artifactsDir, "reviewer", {
    ...validReviewer,
    blocking_issues: [
      { description: "something wrong", affected_file: "src/foo.py" },
    ],
  });

  try {
    const result = runHandler("reviewer", workspaceDir);
    assert.equal(result?.action, "revise");
    assert.ok(result?.reason?.includes("blocking_issues"));
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("requests revision when reviewer integration_tests_passing is absent", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-reviewer");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);
  writeOutput(artifactsDir, "reviewer", {
    blocking_issues: [],
    phase_intent_validated: true,
  });

  try {
    const result = runHandler("reviewer", workspaceDir);
    assert.equal(result?.action, "revise");
    assert.ok(result?.reason?.includes("integration_tests_passing"));
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});

test("requests revision when reviewer phase_intent_validated is absent", () => {
  const tmpDir = makeTmpDir();
  const openclawRoot = path.join(tmpDir, "openclaw");
  const workspaceDir = path.join(openclawRoot, "workspace-reviewer");
  fs.mkdirSync(workspaceDir, { recursive: true });
  const artifactsDir = makeArtifactsDir(openclawRoot);
  writeOutput(artifactsDir, "reviewer", {
    blocking_issues: [],
    integration_tests_passing: true,
  });

  try {
    const result = runHandler("reviewer", workspaceDir);
    assert.equal(result?.action, "revise");
    assert.ok(result?.reason?.includes("phase_intent_validated"));
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
});
