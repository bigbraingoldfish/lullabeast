import os
import sys
import json

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from utils import load_json_safe, record_error_code_only, WORKSPACE_DIR, PHASE_STATE_FILE

def evaluate_reviewer(output_path=None):
    if output_path is None:
        output_path = os.path.join(WORKSPACE_DIR, "reviewer_output.json")

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

    blocking_issues = data.get("blocking_issues")
    if blocking_issues is None:
        blocking_issues = []

    if (len(blocking_issues) > 0 or
        not data.get("integration_tests_passing") or
        not data.get("phase_intent_validated")):

        record_error_code_only("reviewer", "ERR_VALIDATION_FAILED")
        return apply_reviewer_routing(data)

    return "PASS"


def _get_current_phase_raw_id() -> str:
    """Return current_phase_raw_id from current_phase.json, or empty string."""
    current_phase_path = os.path.join(WORKSPACE_DIR, "current_phase.json")
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
    phase_archive_path = os.path.join(WORKSPACE_DIR, "phases", f"{phase_raw_id}.md")
    if not os.path.exists(phase_archive_path):
        missing.append(f"phases/{phase_raw_id}.md")

    # Check 2: metrics.jsonl with current phase entry
    metrics_path = os.path.join(WORKSPACE_DIR, "metrics.jsonl")
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
