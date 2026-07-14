"""Canonical `ERR_*` error-code taxonomy for the Lullabeast pipeline (LAUNCH-7).

Every code the pipeline writes to ``phase_state.json``'s ``last_error_code`` (and
thereby surfaces in ``pipeline_events.jsonl``, ``/api/state``, and the dashboard
error-title map) is defined here ONCE, as a module constant whose value equals
its name. Producers and the orchestrator's routing branches reference these names
instead of inlining the string, so:

* a typo is a ``NameError`` at import time, not a silently-wrong code that no
  reader or linter catches (the former failure mode this module exists to kill);
* the routing that keys on a code (e.g. the ``ERR_UNACCOUNTED_DELETION``
  worktree-reset branch, the ``_ERR_CODE_TO_TRIGGER_CLASS`` map) can never drift
  from the producer that emits it.

This is a **reference-only** taxonomy: the string VALUES are immovable. They are
persisted on disk, embedded in the durable event log, returned over the HTTP API,
matched by the React UI's ``P3_LAST_ERROR_CODE_TITLES`` map, and asserted on by
the test suite. Renaming a value is a breaking change to all of those surfaces;
adding a code means adding a constant here AND mapping it in the UI title map.

Import idioms (no file I/O, stdlib-only — safe to import from any context):
* orchestrator (sibling in ``pipeline/``):  ``from error_codes import ERR_...``
* gate scripts: re-exported through ``gate_scripts/utils.py`` alongside
  ``write_json_atomic`` (``from utils import ERR_...``), so the gates need no
  extra ``sys.path`` wiring.

Test-only / reserved tokens (``ERR_GATE_FAIL``, ``ERR_INFRA_FAILURE``,
``ERR_PRD_VERBATIM_MISSING``, ``ERR_ROADMAP_BLOCKED``, ``ERR_SOMETHING_NEW``,
``ERR_UNREACHABLE_MODULE``, ``ERR_X``, ``ERR_STALL_TIMEOUT``) are deliberately
NOT defined here — they are fixtures/wildcards used only by tests and are never
emitted by production code. ``tests/test_error_codes.py`` pins that boundary so a
future genuinely-emitted code cannot quietly stay an inline literal.
"""

# --- Gate util helpers (gate_scripts/utils.py: load_json_safe) ----------------
ERR_FILE_MISSING = "ERR_FILE_MISSING"          # expected output file absent
ERR_JSON_PARSE = "ERR_JSON_PARSE"              # output file present but not valid JSON

# --- Shared structural validation (planner / executor / reviewer gates) -------
ERR_VALIDATION_FAILED = "ERR_VALIDATION_FAILED"

# --- Executor gate ------------------------------------------------------------
ERR_STATUS_NOT_COMPLETE = "ERR_STATUS_NOT_COMPLETE"        # executor status != "complete"
ERR_TESTS_FAILING = "ERR_TESTS_FAILING"                    # executor reports tests not passing
ERR_PATH_TRAVERSAL = "ERR_PATH_TRAVERSAL"                  # manifest path escapes workspace (hard-fail, security)
ERR_MANIFEST_FILE_MISSING = "ERR_MANIFEST_FILE_MISSING"    # declared file not on disk (demoted warning)
ERR_TDD_COVERAGE_MISMATCH = "ERR_TDD_COVERAGE_MISMATCH"    # planner-listed test absent from tests_written (demoted warning)
ERR_BEHAVIORAL_ARTIFACTS_MISSING = "ERR_BEHAVIORAL_ARTIFACTS_MISSING"  # missing/empty behavioral_smoke_artifacts (demoted warning)
ERR_MISSING_BASE_COMMIT = "ERR_MISSING_BASE_COMMIT"        # no phase_base_commit → can't verify deletions
ERR_UNACCOUNTED_DELETION = "ERR_UNACCOUNTED_DELETION"      # MiniMax file-deletion guard (drives worktree hard-reset)
ERR_GIT_DIFF_FAILED = "ERR_GIT_DIFF_FAILED"                # git diff returned non-zero
ERR_DELETION_CHECK_CRASHED = "ERR_DELETION_CHECK_CRASHED"  # deletion check itself crashed (fail-closed)

# --- Reviewer gate ------------------------------------------------------------
ERR_MISSING_ARTIFACTS = "ERR_MISSING_ARTIFACTS"
ERR_REVIEWER_CONTRACT_FAILURE = "ERR_REVIEWER_CONTRACT_FAILURE"  # no/unparseable verdict (also routed on in orchestrator)
ERR_REVIEWER_MODEL_ERROR = "ERR_REVIEWER_MODEL_ERROR"  # CONTRACT_FAILURE whose cause is a reviewer model hard-error (stopReason:error / 500) — infra, not a give-up
ERR_VISUAL_UNVERIFIED = "ERR_VISUAL_UNVERIFIED"
ERR_BEHAVIORAL_UNVERIFIED = "ERR_BEHAVIORAL_UNVERIFIED"
ERR_REGRESSION_UNVERIFIED = "ERR_REGRESSION_UNVERIFIED"
ERR_REGRESSION_PRIOR_PHASE = "ERR_REGRESSION_PRIOR_PHASE"

# --- Orchestrator dispatch (also keyed by _ERR_CODE_TO_TRIGGER_CLASS) ---------
ERR_PROVIDER_REJECTED = "ERR_PROVIDER_REJECTED"
ERR_MODEL_OVERRIDE_REJECTED = "ERR_MODEL_OVERRIDE_REJECTED"  # gateway refused the phase override model on the session-creating patch
ERR_SESSION_DEAD_ON_ARRIVAL = "ERR_SESSION_DEAD_ON_ARRIVAL"
ERR_RESET_PHASE_GIT_FAILED = "ERR_RESET_PHASE_GIT_FAILED"
ERR_RESET_EXECUTION_GIT_FAILED = "ERR_RESET_EXECUTION_GIT_FAILED"
ERR_ROADMAP_CHECKBOX_FAILED = "ERR_ROADMAP_CHECKBOX_FAILED"
ERR_PHASE_RESOLVER_FAILED = "ERR_PHASE_RESOLVER_FAILED"
ERR_MERGE_FAILED = "ERR_MERGE_FAILED"
ERR_TOOL_LOOP = "ERR_TOOL_LOOP"  # agent spun on identical tool calls in one turn (deterministic in-turn loop catch)


# Derived from the constants above so it can never drift from them: every
# module-level ``ERR_*`` string is a member. Consumed by the drift-guard test and
# available for any future "is this a known code?" validation.
ALL_ERROR_CODES = frozenset(
    value
    for name, value in dict(globals()).items()
    if name.startswith("ERR_") and isinstance(value, str)
)

# Explicit export surface (also derived, so it stays in sync automatically).
__all__ = sorted(name for name in dict(globals()) if name.startswith("ERR_")) + [
    "ALL_ERROR_CODES",
]
