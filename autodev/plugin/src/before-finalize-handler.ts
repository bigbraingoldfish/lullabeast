/**
 * before_agent_finalize hook handler — structural pre-check for pipeline agents.
 *
 * Fires when the harness is about to accept the agent's natural final answer.
 * Reads the agent's output JSON (already written to the workspace) and checks
 * for structural completeness.  If required fields are missing, returns
 * `{ action: "revise", reason: "..." }` to give the agent one bounded extra
 * pass inside the same session with its existing context intact.
 *
 * This is a SOFT pre-gate.  The hard gate scripts remain the final authority
 * on output validity.  Only pure JSON structural checks belong here — anything
 * requiring filesystem traversal, git operations, or subprocess calls stays in
 * the hard gates.
 *
 * Check classification per agent
 * ─────────────────────────────
 * Planner (plugin-safe):
 *   implementation_plan  — non-empty array
 *   tdd_test_structure   — non-empty array
 *   pass_criteria        — non-empty array, every item has a "condition" string
 *
 * Executor (plugin-safe, only when status == "complete"):
 *   test_results.all_passing — boolean present
 *   file_manifest            — array present
 *   tests_written            — array present
 *
 * Reviewer (plugin-safe):
 *   blocking_issues           — array present; each item has description, attribution, affected_file
 *   integration_tests_passing — boolean present
 *   behavioral_verification   — object with verdict (string), evidence (array),
 *                                how_to_check_followed (boolean). Structural
 *                                shape only — semantic enforcement (≥3 anchors
 *                                on verdict="pass", on-disk path existence,
 *                                workspace bounds) lives in the hard
 *                                reviewer_gate.py.
 *
 * Gate-only (NOT checked here):
 *   Executor: file existence, path traversal, TDD path match, git deletion check,
 *             behavioral_smoke_artifacts on-disk validation.
 *   Reviewer: done-criteria artifact existence (filesystem), test run,
 *             behavioral_verification semantic rules (anchor count, path safety,
 *             on-disk existence).
 */

import * as path from "node:path";
import type {
  PluginHookAgentContext,
  PluginHookBeforeAgentFinalizeEvent,
  PluginHookBeforeAgentFinalizeResult,
} from "./openclaw-types.d.ts";
import {
  isPipelineSession,
  PIPELINE_AGENT_IDS,
  resolveArtifactsDir,
  readJsonSafe,
} from "./utils.ts";

type ReviseResult = PluginHookBeforeAgentFinalizeResult & {
  action: "revise";
  reason: string;
};

function revise(reason: string): ReviseResult {
  return { action: "revise", reason };
}

function checkPlanner(
  data: Record<string, unknown>,
): string[] {
  const missing: string[] = [];

  if (
    !Array.isArray(data["implementation_plan"]) ||
    (data["implementation_plan"] as unknown[]).length === 0
  ) {
    missing.push("implementation_plan (non-empty array required)");
  }

  if (
    !Array.isArray(data["tdd_test_structure"]) ||
    (data["tdd_test_structure"] as unknown[]).length === 0
  ) {
    missing.push("tdd_test_structure (non-empty array required)");
  }

  const passCriteria = data["pass_criteria"];
  if (!Array.isArray(passCriteria) || passCriteria.length === 0) {
    missing.push("pass_criteria (non-empty array required)");
  } else {
    const badItems = (passCriteria as unknown[]).filter(
      (item) =>
        typeof item !== "object" ||
        item === null ||
        typeof (item as Record<string, unknown>)["condition"] !== "string",
    );
    if (badItems.length > 0) {
      missing.push(
        `pass_criteria[*].condition (string required; ${badItems.length} item(s) missing)`,
      );
    }
  }

  return missing;
}

function checkExecutor(data: Record<string, unknown>): string[] {
  // Only perform structural checks when the executor claims completion.
  // If status is "stuck" or "failed", the hard gate handles it.
  if (data["status"] !== "complete") return [];

  const missing: string[] = [];

  const tr = data["test_results"];
  if (
    typeof tr !== "object" ||
    tr === null ||
    typeof (tr as Record<string, unknown>)["all_passing"] !== "boolean"
  ) {
    missing.push("test_results.all_passing (boolean required)");
  }

  if (!Array.isArray(data["file_manifest"])) {
    missing.push("file_manifest (array required)");
  }

  if (!Array.isArray(data["tests_written"])) {
    missing.push("tests_written (array required)");
  }

  return missing;
}

function checkReviewer(data: Record<string, unknown>): string[] {
  const missing: string[] = [];

  if (!Array.isArray(data["blocking_issues"])) {
    missing.push("blocking_issues (array required)");
  } else {
    const bi = data["blocking_issues"] as unknown[];
    const badItems = bi.filter((item) => {
      if (typeof item !== "object" || item === null) return true;
      const obj = item as Record<string, unknown>;
      return (
        typeof obj["description"] !== "string" ||
        typeof obj["attribution"] !== "string" ||
        typeof obj["affected_file"] !== "string"
      );
    });
    if (badItems.length > 0) {
      missing.push(
        `blocking_issues[*] must have description, attribution, and affected_file strings ` +
          `(${badItems.length} item(s) non-conforming)`,
      );
    }
  }

  if (typeof data["integration_tests_passing"] !== "boolean") {
    missing.push("integration_tests_passing (boolean required)");
  }

  // P0 Stage F: structured ``behavioral_verification`` object replaces the
  // legacy ``phase_intent_validated: boolean`` field. The plugin enforces
  // only the structural shape (object with verdict + evidence + boolean
  // how_to_check_followed); the hard reviewer_gate.py enforces the
  // semantic rules (≥3 anchors on pass, on-disk path existence, workspace
  // bounds).
  const bv = data["behavioral_verification"];
  if (typeof bv !== "object" || bv === null || Array.isArray(bv)) {
    missing.push("behavioral_verification (object required)");
  } else {
    const bvObj = bv as Record<string, unknown>;
    if (typeof bvObj["verdict"] !== "string") {
      missing.push("behavioral_verification.verdict (string required)");
    }
    if (!Array.isArray(bvObj["evidence"])) {
      missing.push("behavioral_verification.evidence (array required)");
    }
    if (typeof bvObj["how_to_check_followed"] !== "boolean") {
      missing.push(
        "behavioral_verification.how_to_check_followed (boolean required)",
      );
    }
  }

  return missing;
}

const AGENT_CHECKERS: Record<
  string,
  (data: Record<string, unknown>) => string[]
> = {
  planner: checkPlanner,
  executor: checkExecutor,
  reviewer: checkReviewer,
};

export function handleBeforeAgentFinalize(
  event: PluginHookBeforeAgentFinalizeEvent,
  ctx: PluginHookAgentContext,
): PluginHookBeforeAgentFinalizeResult | void {
  const sessionKey = ctx.sessionKey ?? event.sessionKey;
  const agentId = ctx.agentId;

  if (!isPipelineSession(sessionKey)) return;
  if (!agentId || !PIPELINE_AGENT_IDS.has(agentId)) return;

  const checker = AGENT_CHECKERS[agentId];
  if (!checker) return;

  const artifactsDir = resolveArtifactsDir(ctx.workspaceDir);
  if (!artifactsDir) return;

  const outputPath = path.join(artifactsDir, `${agentId}_output.json`);
  const data = readJsonSafe(outputPath);

  if (!data) {
    // Output file absent or malformed — request a revision to produce it.
    return revise(
      `${agentId}_output.json is missing or not valid JSON. ` +
        `Write the required output file before finishing.`,
    );
  }

  const missingFields = checker(data);
  if (missingFields.length === 0) return;

  const fieldList = missingFields.join("; ");
  return revise(
    `${agentId}_output.json has structural problems: ${fieldList}. ` +
      `Rewrite the file with all required fields before finishing.`,
  );
}
