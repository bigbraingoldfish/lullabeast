import os
import sys
import json
import tempfile

# Import the shared env resolvers. Gate scripts live one directory below the
# pipeline package, so add pipeline/ to sys.path before importing.
_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

from env_resolvers import resolve_pipeline_root  # noqa: E402


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
    fd, temp_path = tempfile.mkstemp(dir=ARTIFACTS_DIR, prefix="phase_state_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(temp_path, PHASE_STATE_FILE)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def load_json_safe(filepath, agent_type):
    """Loads JSON and handles parse errors without crashing."""
    if not os.path.exists(filepath):
        # Orchestrator owns the retry increment; we only record the error code here.
        record_error_code_only(agent_type, "ERR_FILE_MISSING")
        return None
    try:
        with open(filepath, "r") as f:
            return json.load(f)
    except json.JSONDecodeError:
        # Orchestrator owns the retry increment; we only record the error code here.
        record_error_code_only(agent_type, "ERR_JSON_PARSE")
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

    fd, temp_path = tempfile.mkstemp(dir=ARTIFACTS_DIR, prefix="phase_state_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(temp_path, PHASE_STATE_FILE)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    return state[retry_key]
