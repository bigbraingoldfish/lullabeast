import os
import sys
import json
import tempfile

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


_REQUIRED_BEHAVIORAL_EVIDENCE_KEYS = ("claim", "file_or_screenshot_or_log", "method")
_MIN_BEHAVIORAL_EVIDENCE_ANCHORS = 3


def _requires_behavioral_verification(current_phase):
    """Content-driven: True iff ``current_phase.json`` carries a populated
    Behavioral Verification block with all three required sub-fields.

    Mirrors the *intent* of :func:`_is_visual_phase` but is content-driven
    rather than prefix-driven — under P0 effectively every phase has a block,
    but legacy/in-flight phases queued before P0 ship with
    ``behavioral_verification: None`` and must be exempt (see parent plan
    §2.9 transitional rule)."""
    if not current_phase:
        return False
    block = current_phase.get("behavioral_verification")
    if not isinstance(block, dict):
        return False
    return all(block.get(k) for k in ("user_observable", "how_to_check", "failure_language"))


def _check_behavioral_verification(data):
    """Return a list of problems with the reviewer's ``behavioral_verification``
    object, or [] if shape is valid.

    Mirror of :func:`_check_visual_verification`. Required shape on phases
    whose ``current_phase.behavioral_verification`` is populated:

      - ``verdict``: one of ``"pass"``, ``"fail"``, ``"cannot_verify"``.
      - ``how_to_check_followed``: boolean.
      - ``evidence``: list. On ``verdict == "pass"`` it MUST contain at least
        :data:`_MIN_BEHAVIORAL_EVIDENCE_ANCHORS` entries; each entry is a dict
        with ``claim``, ``file_or_screenshot_or_log``, and ``method`` keys.
        The ``file_or_screenshot_or_log`` path is workspace-bounded (via
        ``os.path.commonpath`` — same guard pattern as the file_manifest
        validator in :mod:`executor_gate`) and must resolve on disk.

    A ``"fail"`` or ``"cannot_verify"`` verdict is not treated as a
    gate-script-level problem here — it flows through the validation block in
    :func:`evaluate_reviewer` (as ``behavioral_rejection``). This function
    only validates the *contract shape*."""
    block = data.get("behavioral_verification")
    if not isinstance(block, dict):
        return ["behavioral_verification missing or not an object"]
    verdict = block.get("verdict")
    if verdict not in ("pass", "fail", "cannot_verify"):
        return [
            f"behavioral_verification.verdict must be pass|fail|cannot_verify, got {verdict!r}"
        ]
    if not isinstance(block.get("how_to_check_followed"), bool):
        return ["behavioral_verification.how_to_check_followed must be a boolean"]
    evidence = block.get("evidence") or []
    if verdict != "pass":
        # fail / cannot_verify: shape OK without evidence anchors; the
        # rejection signal itself is the verdict.
        return []
    if not isinstance(evidence, list) or len(evidence) < _MIN_BEHAVIORAL_EVIDENCE_ANCHORS:
        return [
            f"behavioral_verification.evidence must have at least "
            f"{_MIN_BEHAVIORAL_EVIDENCE_ANCHORS} entries when verdict='pass'"
        ]
    workspace_abs = os.path.abspath(WORKSPACE_DIR)
    for i, entry in enumerate(evidence):
        if not isinstance(entry, dict):
            return [f"behavioral_verification.evidence[{i}] must be an object"]
        for key in _REQUIRED_BEHAVIORAL_EVIDENCE_KEYS:
            if not entry.get(key):
                return [
                    f"behavioral_verification.evidence[{i}] missing required key {key!r}"
                ]
        path = entry["file_or_screenshot_or_log"]
        abs_path = path if os.path.isabs(path) else os.path.join(WORKSPACE_DIR, path)
        try:
            if os.path.commonpath([workspace_abs, os.path.abspath(abs_path)]) != workspace_abs:
                return [
                    f"behavioral_verification.evidence[{i}] path escapes workspace: {path}"
                ]
        except ValueError:
            return [
                f"behavioral_verification.evidence[{i}] path escapes workspace: {path}"
            ]
        if not os.path.exists(abs_path):
            return [
                f"behavioral_verification.evidence[{i}] path does not exist on disk: {path}"
            ]
    return []


def _synthesize_behavioral_blocking_issues(data):
    """When the reviewer recorded a behavioural failure verdict but did not
    populate ``blocking_issues``, synthesise one entry per evidence claim so
    the executor's self-heal feedback context (next reviewer-rejection retry)
    is never empty.

    Mutates ``data`` in place. Idempotent: a non-empty ``blocking_issues``
    is left untouched (the reviewer agent populated per AGENTS.md). Defensive
    fallback — primary contract is the agent populates the list; this is the
    floor that keeps the executor's targeted self-heal path armed even when
    the agent's structured output omits per-criterion entries.

    Contract: one evidence entry → one blocking issue with

      description     = claim
      attribution     = "impl"        (behavioural failures are impl failures
                                       by definition — the artifact did not
                                       exhibit the claimed behaviour)
      affected_file   = file_or_screenshot_or_log
      criterion_source = "behavioral"
      criterion_id    = f"behavioral_evidence[{i}]"

    The ``criterion_id`` shape mirrors a JSON-pointer-ish notation so an
    operator can `jq '.behavioral_verification.evidence[<i>]'` the source
    reviewer_output.json and retrieve the original claim. Distinct from the
    planner's ``pass_criteria[].traces_to`` anchor (which is a planning-time
    link); the two should not be conflated. See ASSUMPTIONS.md §J.
    """
    block = data.get("behavioral_verification") or {}
    if block.get("verdict") not in ("fail", "cannot_verify"):
        return
    if data.get("blocking_issues"):
        return
    evidence = block.get("evidence") or []
    if not isinstance(evidence, list) or not evidence:
        return
    synthesised = []
    for i, entry in enumerate(evidence):
        if not isinstance(entry, dict):
            continue
        synthesised.append({
            "description": entry.get("claim", ""),
            "attribution": "impl",
            "affected_file": entry.get("file_or_screenshot_or_log", ""),
            "criterion_source": "behavioral",
            "criterion_id": f"behavioral_evidence[{i}]",
        })
    data["blocking_issues"] = synthesised


def _load_current_phase():
    """Return the full ``current_phase.json`` dict, or {} on miss.

    A sibling of :func:`_get_current_phase_raw_id` that returns the full
    payload instead of just the raw_id. Used by
    :func:`_requires_behavioral_verification` and the
    ``behavioral_rejection`` branch in :func:`evaluate_reviewer`."""
    current_phase_path = os.path.join(ARTIFACTS_DIR, "current_phase.json")
    if not os.path.exists(current_phase_path):
        return {}
    try:
        with open(current_phase_path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


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

    See :func:`_check_behavioral_verification` for the parallel content-driven
    contract that validates the universal P0 behavioural-verification block."""
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

    # ------------------------------------------------------------------
    # FIND-BEHAVIORAL-VERIFICATION: P0 Stage F. Any phase whose
    # current_phase.json carries a populated behavioral_verification block
    # requires a structured ``behavioral_verification`` object on the
    # reviewer output. Content-driven (effectively universal under P0).
    # Missing or malformed → re-invoke the reviewer (NON-retry-consuming,
    # mirrors VISUAL_UNVERIFIED — the reviewer never produced the
    # judgment we need, so a "code-quality" retry should not be burned).
    # ------------------------------------------------------------------
    _current_phase = _load_current_phase()
    if _requires_behavioral_verification(_current_phase):
        behavioral_problems = _check_behavioral_verification(data)
        if behavioral_problems:
            record_error_code_only("reviewer", "ERR_BEHAVIORAL_UNVERIFIED")
            print(
                f"[GATE] BEHAVIORAL_UNVERIFIED ({_current_phase_raw_id}): {behavioral_problems}",
                file=sys.stderr,
            )
            return "BEHAVIORAL_UNVERIFIED"

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

    # P0 Stage F: a behavioral_verification verdict of "fail" or
    # "cannot_verify" on a behavioural phase is a code-quality rejection
    # (legitimately consumes a reviewer_retries slot). This replaces the
    # ``not data.get("phase_intent_validated")`` trigger that lived here
    # before — the boolean was self-attested and unverifiable; the
    # structured verdict is anchored to evidence.
    behavioral_verdict = (data.get("behavioral_verification") or {}).get("verdict")
    behavioral_rejection = (
        _requires_behavioral_verification(_current_phase)
        and behavioral_verdict in ("fail", "cannot_verify")
    )

    if (len(blocking_issues) > 0 or
        not data.get("integration_tests_passing") or
        visual_rejection or
        behavioral_rejection):

        record_error_code_only("reviewer", "ERR_VALIDATION_FAILED")

        # P0 Stage G: synthesise per-evidence-entry blocking_issues when the
        # reviewer recorded a behavioural failure with an empty list. Persist
        # the augmented payload back to reviewer_output.json (atomic mkstemp +
        # os.replace) so the orchestrator's downstream read sees the canonical
        # list and reviewer_output.json on disk matches what failure_context.json
        # carries. apply_reviewer_routing stays pure routing.
        if behavioral_rejection:
            _synthesize_behavioral_blocking_issues(data)
            try:
                _out_dir = os.path.dirname(output_path) or "."
                _fd, _tmp = tempfile.mkstemp(dir=_out_dir, prefix="reviewer_output_")
                with os.fdopen(_fd, "w") as _wf:
                    json.dump(data, _wf, indent=2)
                os.replace(_tmp, output_path)
            except Exception as _e:
                print(f"[GATE] synthesise write-back failed: {_e}", file=sys.stderr)

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
    """Pass 1: executor, Pass 2: any-plan pivot, Pass 3: escalate.

    Pass-2 routing (P0 Stage H — folded-in Stage G callout #1): if ANY
    blocking_issue carries ``attribution: "plan"``, route to planner.
    Otherwise route to executor. The legacy pivot only inspected
    ``blocking_issues[0].attribution``, making the routing decision
    ordering-sensitive — a valid plan-attributed issue at index 1+ would
    silently route to executor and the planner-spec problem would never
    get fixed.

    Defensive ``isinstance(bi, dict)`` coalesce guards against pathological
    non-dict entries (the legacy fixture in
    ``test_route_executor_writes_failure_context_atomically`` passes
    string-shaped issues; this pivot survives them by treating non-dict
    entries as carrying no attribution).

    This is *not* the orchestrator's separate ``run_blame_attribution()``
    AI-driven attribution system — that lives elsewhere and is
    untouched.
    """
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
        issues = data.get("blocking_issues") if data else None
        if issues:
            any_plan = any(
                (bi if isinstance(bi, dict) else {}).get("attribution") == "plan"
                for bi in issues
            )
            return "ROUTE_PLANNER" if any_plan else "ROUTE_EXECUTOR"
        return "ROUTE_EXECUTOR"  # fallback
    else:
        return "ROUTE_ESCALATE"

if __name__ == "__main__":
    result = evaluate_reviewer(sys.argv[1] if len(sys.argv) > 1 else None)
    print(result)
