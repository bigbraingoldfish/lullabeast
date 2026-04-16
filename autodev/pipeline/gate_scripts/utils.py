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
PHASE_STATE_FILE = os.path.join(WORKSPACE_DIR, "phase_state.json")


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
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=WORKSPACE_DIR, prefix="phase_state_")
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

    os.makedirs(WORKSPACE_DIR, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(dir=WORKSPACE_DIR, prefix="phase_state_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(temp_path, PHASE_STATE_FILE)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    return state[retry_key]
