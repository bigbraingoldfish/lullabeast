import os
import sys
import json

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from utils import (
    ARTIFACTS_DIR,
    load_json_safe,
    record_error_code_only,
    PHASE_STATE_FILE,
    WORKSPACE_DIR,
)

# Phases that produce user-visible output and therefore require a screenshot
# artifact + a reviewer visual_verification verdict. Identified by roadmap
# subsystem prefix. Convention used across AutoDev roadmaps:
#   UI-*   — surfaces visible to the end user (rendered UI, styling, themes)
#   INT-*  — final integration: full system end-to-end (always rendered if UI exists)
#
# Operators with project-specific phases that produce rendered output under a
# non-UI/INT prefix can extend coverage via AUTODEV_VISUAL_PHASE_RAW_IDS
# (comma-separated list of raw phase IDs, e.g. "CORE-E4,SETUP-E2"). This keeps
# the default rule generic while allowing per-project tightening without
# editing the gate.
import os as _os
_VISUAL_PHASE_PREFIXES = {"UI", "INT"}


def _extra_visual_raw_ids() -> set:
    raw = (_os.environ.get("AUTODEV_VISUAL_PHASE_RAW_IDS") or "").strip()
    if not raw:
        return set()
    return {item.strip().upper() for item in raw.split(",") if item.strip()}


def _is_visual_phase(phase_raw_id):
    """Return True if this phase produces user-visible output and requires
    a visual-verification artifact from the reviewer.

    Default: any phase whose subsystem prefix is UI or INT.
    Override: set AUTODEV_VISUAL_PHASE_RAW_IDS env var to extend the set with
    project-specific raw phase IDs.
    """
    if not phase_raw_id:
        return False
    raw = str(phase_raw_id).upper()
    if raw in _extra_visual_raw_ids():
        return True
    prefix = raw.split("-", 1)[0]
    return prefix in _VISUAL_PHASE_PREFIXES


def _check_visual_verification(data):
    """Return a list of problems with the reviewer's visual_verification +
    visual_smoke_artifacts fields, or [] if all present and valid.

    Required shape on visual phases:
      - visual_verification: one of "pass", "fail", "cannot_verify"
      - visual_smoke_artifacts: list with ≥1 entry when verification == "pass".
        Each entry must be a dict with a `path` key resolvable on disk under
        the workspace.

    A "fail" or "cannot_verify" verdict is not treated as a gate-script-level
    problem here — it flows through the existing blocking_issues path in
    evaluate_reviewer. This function only validates the *contract shape*.
    """
    verdict = data.get("visual_verification")
    if verdict not in ("pass", "fail", "cannot_verify"):
        return [
            f"visual_verification must be one of pass|fail|cannot_verify, got {verdict!r}"
        ]

    artifacts = data.get("visual_smoke_artifacts") or []
    if verdict == "pass":
        if not isinstance(artifacts, list) or len(artifacts) == 0:
            return ["visual_smoke_artifacts must be a non-empty list when visual_verification='pass'"]
        for i, entry in enumerate(artifacts):
            if not isinstance(entry, dict):
                return [f"visual_smoke_artifacts[{i}] must be an object"]
            path = entry.get("path")
            if not path or not isinstance(path, str):
                return [f"visual_smoke_artifacts[{i}] missing path"]
            abs_path = path if os.path.isabs(path) else os.path.join(WORKSPACE_DIR, path)
            if not os.path.exists(abs_path):
                return [f"visual_smoke_artifacts[{i}] path does not exist on disk: {path}"]
    return []


def evaluate_reviewer(output_path=None):
    if output_path is None:
        output_path = os.path.join(ARTIFACTS_DIR, "reviewer_output.json")

    # ------------------------------------------------------------------
    # FIND-DONE-CRITERIA: Deterministic pre-review artifact compliance check.
    # Runs BEFORE the reviewer output is evaluated — if mandatory completion
    # artifacts are missing the executor is re-invoked with a specific
    # instruction to produce them.  This does NOT consume reviewer_retries.
    # ------------------------------------------------------------------
    _current_phase_raw_id = _get_current_phase_raw_id()
    if _current_phase_raw_id:
        _missing = _check_done_criteria_artifacts(_current_phase_raw_id)
        if _missing:
            record_error_code_only("reviewer", "ERR_MISSING_ARTIFACTS")
            print(
                f"[GATE] MISSING_ARTIFACTS: {_missing}",
                file=sys.stderr,
            )
            return "MISSING_ARTIFACTS"

    data = load_json_safe(output_path, "reviewer")
    if data is None:
        # Missing file or JSON parse error: infrastructure failure, NOT a reviewer rejection.
        # The reviewer never examined the code — do not consume reviewer retry budget.
        # FIND-REVIEWER-INFRA: distinct from valid rejection path.
        record_error_code_only("reviewer", "ERR_INFRA_FAILURE")
        return "INFRA_FAILURE"

    # ------------------------------------------------------------------
    # FIND-VISUAL-VERIFICATION: On phases that produce user-visible output,
    # the reviewer must include a visual_verification verdict and the
    # screenshot artifact(s) they inspected. Missing or malformed → re-invoke
    # the reviewer with a specific instruction. Does NOT consume
    # reviewer_retries (this is a contract-shape failure, not a code-quality
    # rejection — the reviewer never produced the visual judgment we need).
    # ------------------------------------------------------------------
    if _is_visual_phase(_current_phase_raw_id):
        visual_problems = _check_visual_verification(data)
        if visual_problems:
            record_error_code_only("reviewer", "ERR_VISUAL_UNVERIFIED")
            print(
                f"[GATE] VISUAL_UNVERIFIED ({_current_phase_raw_id}): {visual_problems}",
                file=sys.stderr,
            )
            return "VISUAL_UNVERIFIED"

    blocking_issues = data.get("blocking_issues")
    if blocking_issues is None:
        blocking_issues = []

    # A visual_verification of "fail" or "cannot_verify" is itself a blocking
    # issue even if the reviewer didn't add an entry to blocking_issues.
    visual_verdict = data.get("visual_verification")
    visual_rejection = (
        _is_visual_phase(_current_phase_raw_id)
        and visual_verdict in ("fail", "cannot_verify")
    )

    if (len(blocking_issues) > 0 or
        not data.get("integration_tests_passing") or
        not data.get("phase_intent_validated") or
        visual_rejection):

        record_error_code_only("reviewer", "ERR_VALIDATION_FAILED")
        return apply_reviewer_routing(data)

    return "PASS"


def _get_current_phase_raw_id() -> str:
    """Return current_phase_raw_id from current_phase.json, or empty string."""
    current_phase_path = os.path.join(ARTIFACTS_DIR, "current_phase.json")
    if not os.path.exists(current_phase_path):
        return ""
    try:
        with open(current_phase_path, "r") as f:
            data = json.load(f)
        return data.get("raw_id", "")
    except Exception:
        return ""


def _check_done_criteria_artifacts(phase_raw_id: str) -> list:
    """Return a list of missing artifact descriptions, or empty list if all present.

    Checks:
    1. phases/{phase_raw_id}.md exists in the project root (WORKSPACE_DIR).
    2. metrics.jsonl exists in the project root AND its last non-empty line
       contains the current phase_raw_id.
    """
    missing = []

    # Check 1: phase archive
    phase_archive_path = os.path.join(ARTIFACTS_DIR, "phases", f"{phase_raw_id}.md")
    if not os.path.exists(phase_archive_path):
        missing.append(f"phases/{phase_raw_id}.md")

    # Check 2: metrics.jsonl with current phase entry
    metrics_path = os.path.join(ARTIFACTS_DIR, "metrics.jsonl")
    if not os.path.exists(metrics_path):
        missing.append("metrics.jsonl (file missing)")
    else:
        try:
            last_line = ""
            with open(metrics_path, "r") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped:
                        last_line = stripped
            if phase_raw_id not in last_line:
                missing.append(f"metrics.jsonl (last line does not contain {phase_raw_id!r})")
        except Exception:
            missing.append("metrics.jsonl (unreadable)")

    return missing


def apply_reviewer_routing(data):
    """Pass 1: executor, Pass 2: attr, Pass 3: escalate"""
    state_data = {}
    if os.path.exists(PHASE_STATE_FILE):
        try:
            with open(PHASE_STATE_FILE, 'r') as f:
                state_data = json.load(f)
        except Exception:
            pass

    retries = state_data.get("reviewer_retries", 0)
    pass_number = retries + 1

    if pass_number == 1:
        return "ROUTE_EXECUTOR"
    elif pass_number == 2:
        if data and data.get("blocking_issues") and len(data["blocking_issues"]) > 0:
            attr = data["blocking_issues"][0].get("attribution")
            return "ROUTE_PLANNER" if attr == "plan" else "ROUTE_EXECUTOR"
        return "ROUTE_EXECUTOR" # fallback
    else:
        return "ROUTE_ESCALATE"

if __name__ == "__main__":
    result = evaluate_reviewer(sys.argv[1] if len(sys.argv) > 1 else None)
    print(result)
