"""Shared helpers for the gate scripts — NOT a gate itself.

Imported by the verdict and resolver gates in this directory. Provides the workspace path
constants, the atomic phase-state writers (``record_error_code_only`` / ``update_phase_state_error``),
``load_json_safe``, and ``path_escapes_workspace`` — the ``os.path.realpath`` workspace-boundary
check shared by the executor and reviewer gates. See ./README.md for the gate execution model.
"""
import os
import sys
import json

# Import the shared env resolvers. Gate scripts live one directory below the
# pipeline package, so add pipeline/ to sys.path before importing.
_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

from env_resolvers import resolve_pipeline_root  # noqa: E402
from atomic_io import write_json_atomic, write_text_atomic  # noqa: E402,F401
from error_codes import (  # noqa: E402,F401  (FILE_MISSING/JSON_PARSE used here; rest re-exported to the gate scripts, same hub pattern as write_json_atomic)
    ERR_FILE_MISSING,
    ERR_JSON_PARSE,
    ERR_VALIDATION_FAILED,
    ERR_STATUS_NOT_COMPLETE,
    ERR_TESTS_FAILING,
    ERR_PATH_TRAVERSAL,
    ERR_MANIFEST_FILE_MISSING,
    ERR_TDD_COVERAGE_MISMATCH,
    ERR_BEHAVIORAL_ARTIFACTS_MISSING,
    ERR_MISSING_BASE_COMMIT,
    ERR_UNACCOUNTED_DELETION,
    ERR_GIT_DIFF_FAILED,
    ERR_DELETION_CHECK_CRASHED,
    ERR_MISSING_ARTIFACTS,
    ERR_REVIEWER_CONTRACT_FAILURE,
    ERR_VISUAL_UNVERIFIED,
    ERR_BEHAVIORAL_UNVERIFIED,
    ERR_REGRESSION_UNVERIFIED,
    ERR_REGRESSION_PRIOR_PHASE,
)


def _derive_runtime_root() -> str:
    """Return the pipeline runtime directory.

    Thin wrapper around :func:`env_resolvers.resolve_pipeline_root` that derives
    the repo path from ``AUTODEV_REPO_PATH`` or the on-disk file layout when
    unset. Preserved as a named helper so existing gate-script imports keep
    working.
    """
    repo_path = os.environ.get(
        "AUTODEV_REPO_PATH",
        # gate_scripts/ → pipeline/ → autodev/ → repo root: 4 dirname calls
        os.path.dirname(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
        ),
    )
    return resolve_pipeline_root(repo_path)


WORKSPACE_DIR = os.path.join(_derive_runtime_root(), "pipeline-project") + os.sep
# Per-project pipeline artifacts (JSON, sentinels, metrics) live under the
# symlink target at .autodev/pipeline/ — not at repo root next to roadmap.md.
AUTODEV_PIPELINE_SUBDIR = os.path.join(".autodev", "pipeline")
ARTIFACTS_DIR = os.path.join(WORKSPACE_DIR.rstrip(os.sep), AUTODEV_PIPELINE_SUBDIR) + os.sep
PHASE_STATE_FILE = os.path.join(ARTIFACTS_DIR, "phase_state.json")


def path_escapes_workspace(path):
    """Return True if ``path`` resolves outside the pipeline workspace.

    Canonical (``os.path.realpath``) workspace-boundary check shared by the
    reviewer gate's contract validators and its failure-verdict blocking-issue
    synthesisers. Symlinks are resolved on BOTH sides, so an in-workspace
    symlink pointing outside the workspace is caught (a lexical compare is not
    enough). A relative ``path`` is joined against :data:`WORKSPACE_DIR`; an
    absolute ``path`` is used as-is. A path sharing no common root with the
    workspace (``os.path.commonpath`` raises ``ValueError``) counts as an escape.

    Boundary check ONLY. On-disk existence is a separate, caller-owned concern:
    the pass-verdict validators additionally require the artifact to exist, but
    the failure-verdict synthesisers must NOT — a failed phase may legitimately
    have produced no artifact, so absence there is not an escape.

    A non-string or empty ``path`` cannot describe a traversal target and is
    reported as non-escaping (``False``); the caller decides what to store for
    such a value. Reads the module-level :data:`WORKSPACE_DIR` at call time so
    tests that patch it take effect without arguments.
    """
    if not isinstance(path, str) or not path:
        return False
    workspace_real = os.path.realpath(WORKSPACE_DIR)
    abs_path = path if os.path.isabs(path) else os.path.join(WORKSPACE_DIR, path)
    real_path = os.path.realpath(abs_path)
    try:
        return os.path.commonpath([workspace_real, real_path]) != workspace_real
    except ValueError:
        return True


def phase_has_behavioral_block(current_phase):
    """Content-driven: True iff ``current_phase`` carries a populated
    Behavioral Verification block with all three required sub-fields.

    Shared by ``reviewer_gate`` and ``executor_gate`` (P1 Stage D Hygiene H1
    extracted the prior duplicates ``_requires_behavioral_verification`` and
    ``_phase_has_behavioral_block`` into this single source of truth).

    Pre-P0 in-flight phases carry ``behavioral_verification: None`` and must
    be exempt — only phases produced after P0 ships have the block.
    """
    if not isinstance(current_phase, dict):
        return False
    block = current_phase.get("behavioral_verification")
    if not isinstance(block, dict):
        return False
    return all(block.get(k) for k in ("user_observable", "how_to_check", "failure_language"))


def requires_regression_verification(current_phase):
    """Content-driven: True iff ``current_phase`` carries a prior phase
    raw_id AND a prior phase how_to_check recipe (both truthy).

    Drives the reviewer-gate's REGRESSION_UNVERIFIED branch (P1 Stage D).
    The resolver populates these fields when the most recent completed
    phase had a behavioural recipe; the regression branch is skipped when
    either is None (first phase, all predecessors blocked/skipped, or
    predecessor had no behavioural block).
    """
    if not isinstance(current_phase, dict):
        return False
    return bool(
        current_phase.get("prior_phase_raw_id")
        and current_phase.get("prior_phase_how_to_check")
    )


def record_error_code_only(agent_type, error_code):
    """Writes last_error_code to phase_state.json without incrementing retry counters.

    Use this when the orchestrator owns the retry increment (e.g. parse errors,
    TDD coverage mismatches) to avoid double-counting.
    """
    state = {}
    if os.path.exists(PHASE_STATE_FILE):
        try:
            with open(PHASE_STATE_FILE, "r") as f:
                state = json.load(f)
        except Exception:
            pass
    state["last_error_code"] = error_code
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    write_json_atomic(PHASE_STATE_FILE, state, indent=2, raise_on_error=False)


def load_json_safe(filepath, agent_type):
    """Loads JSON and handles parse errors without crashing."""
    if not os.path.exists(filepath):
        # Orchestrator owns the retry increment; we only record the error code here.
        record_error_code_only(agent_type, ERR_FILE_MISSING)
        return None
    try:
        with open(filepath, "r") as f:
            return json.load(f)
    except json.JSONDecodeError:
        # Orchestrator owns the retry increment; we only record the error code here.
        record_error_code_only(agent_type, ERR_JSON_PARSE)
        return None


def update_phase_state_error(agent_type, error_code):
    """Safely updates phase_state.json with retry bumps and error codes using atomic writes."""
    state = {}
    if os.path.exists(PHASE_STATE_FILE):
        try:
            with open(PHASE_STATE_FILE, "r") as f:
                state = json.load(f)
        except Exception:
            pass

    retry_key = f"{agent_type}_retries"
    state[retry_key] = state.get(retry_key, 0) + 1
    state["last_error_code"] = error_code

    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    write_json_atomic(PHASE_STATE_FILE, state, indent=2, raise_on_error=False)

    return state[retry_key]
