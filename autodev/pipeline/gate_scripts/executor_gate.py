import json
import os
import subprocess
import sys
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

EXECUTOR_GATE_DETAIL_JSON = "executor_gate_detail.json"


def _executor_gate_detail_path():
    return os.path.join(ARTIFACTS_DIR, EXECUTOR_GATE_DETAIL_JSON)


def _clear_executor_gate_detail():
    try:
        os.remove(_executor_gate_detail_path())
    except FileNotFoundError:
        pass


def _write_executor_gate_detail(payload: dict) -> None:
    """Atomic JSON write for orchestrator to merge into failure_context.json."""
    dest = _executor_gate_detail_path()
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ARTIFACTS_DIR.rstrip(os.sep), prefix="executor_gate_detail_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, dest)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def evaluate_executor(output_path=None):
    if output_path is None:
        output_path = os.path.join(ARTIFACTS_DIR, "executor_output.json")

    _clear_executor_gate_detail()

    data = load_json_safe(output_path, "executor")
    if data is None: return "FAIL"

    if data.get("status") != "complete":
        record_error_code_only("executor", "ERR_STATUS_NOT_COMPLETE")
        return "FAIL"

    test_results = data.get("test_results", {})
    if test_results.get("all_passing") is not True:
        record_error_code_only("executor", "ERR_TESTS_FAILING")
        return "FAIL"

    # Verify file manifest existences and bounds
    expected_files = data.get("file_manifest", []) + data.get("tests_written", [])
    workspace_abs = os.path.abspath(WORKSPACE_DIR)

    for relative_path in expected_files:
        target_abs = os.path.abspath(os.path.join(WORKSPACE_DIR, relative_path))

        # Absolute bounds checking
        try:
            if os.path.commonpath([workspace_abs, target_abs]) != workspace_abs:
                record_error_code_only("executor", "ERR_PATH_TRAVERSAL")
                return "FAIL"
        except ValueError:
            record_error_code_only("executor", "ERR_PATH_TRAVERSAL")
            return "FAIL"

        if not os.path.exists(target_abs):
            record_error_code_only("executor", "ERR_MANIFEST_FILE_MISSING")
            return "FAIL"

    # Cross-reference tests_written against planner's tdd_test_structure.
    # Orchestrator owns the retry increment on FAIL, so we use record_error_code_only.
    planner_output_path = os.path.join(ARTIFACTS_DIR, "planner_output.json")
    planner_data = load_json_safe(planner_output_path, "executor")
    if planner_data is not None:
        planned_tests = planner_data.get("tdd_test_structure", [])
        tests_written = data.get("tests_written", [])
        missing = [t for t in planned_tests if t not in tests_written]
        if missing:
            record_error_code_only("executor", "ERR_TDD_COVERAGE_MISMATCH")
            print(f"[GATE FAIL] Missing planned tests: {missing}", file=sys.stderr)
            return "FAIL"

    # ------------------------------------------------------------------
    # FIND-DELETION-CHECK: Detect unaccounted file deletions.
    #
    # Read phase_base_commit from pipeline_state.json (one level up from
    # WORKSPACE_DIR, since WORKSPACE_DIR is the pipeline-project symlink).
    # Run git diff against that commit to find files that existed at branch
    # point but are now gone.  Any such file must appear in either:
    #   - data["file_manifest"]  (file is still present — listed as produced)
    #   - data["files_deleted"]  (intentionally removed by this phase)
    # Anything else is ERR_UNACCOUNTED_DELETION.
    # ------------------------------------------------------------------
    pipeline_state_path = os.path.join(os.path.dirname(WORKSPACE_DIR.rstrip("/")), "pipeline_state.json")
    # Also check the canonical openclaw workspace path.
    _oc_state = os.path.expanduser("~/.openclaw/pipeline_state.json")
    if not os.path.exists(pipeline_state_path) and os.path.exists(_oc_state):
        pipeline_state_path = _oc_state

    phase_base_commit = None
    if os.path.exists(pipeline_state_path):
        try:
            with open(pipeline_state_path, "r") as _f:
                _ps = json.load(_f)
            phase_base_commit = _ps.get("phase_base_commit") or None
        except Exception:
            pass

    if not phase_base_commit:
        # Fail closed: phase_base_commit is the reference point for detecting file
        # deletions.  Without it the check is a no-op and MiniMax file deletion goes
        # undetected.  Return FAIL so the orchestrator retries with a fresh session.
        print(
            json.dumps({"error": "ERR_MISSING_BASE_COMMIT", "detail": "cannot verify deletions"}),
            file=sys.stdout,
        )
        print("[GATE FAIL] phase_base_commit unavailable — failing closed to protect deletion guard.", file=sys.stderr)
        record_error_code_only("executor", "ERR_MISSING_BASE_COMMIT")
        return "FAIL"
    else:
        try:
            _git_result = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=D", phase_base_commit, "HEAD"],
                cwd=WORKSPACE_DIR,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if _git_result.returncode == 0:
                _deleted_at_base = [
                    p.strip() for p in _git_result.stdout.splitlines() if p.strip()
                ]
                # Also include working-tree deletions not yet committed.
                _wt_result = subprocess.run(
                    ["git", "ls-files", "--deleted"],
                    cwd=WORKSPACE_DIR,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if _wt_result.returncode == 0:
                    _deleted_at_base += [
                        p.strip() for p in _wt_result.stdout.splitlines() if p.strip()
                    ]

                _file_manifest_set = set(data.get("file_manifest", []))
                # files_deleted is an optional array; absent/null treated as empty.
                _files_deleted_set = set(data.get("files_deleted") or [])

                _unaccounted = [
                    f for f in _deleted_at_base
                    if f not in _file_manifest_set and f not in _files_deleted_set
                ]
                if _unaccounted:
                    _sorted_unaccounted = sorted(set(_unaccounted))
                    _write_executor_gate_detail(
                        {
                            "gate_error": "ERR_UNACCOUNTED_DELETION",
                            "unaccounted_deletions": _sorted_unaccounted,
                        }
                    )
                    record_error_code_only("executor", "ERR_UNACCOUNTED_DELETION")
                    print(
                        f"[GATE FAIL] Unaccounted file deletion(s): {_sorted_unaccounted}. "
                        "List intentionally deleted files in `files_deleted` in executor_output.json.",
                        file=sys.stderr,
                    )
                    return "FAIL"
            else:
                print(
                    f"[GATE FAIL] git diff for deletion check failed (rc={_git_result.returncode})"
                    f" — failing closed to protect deletion guard.",
                    file=sys.stderr,
                )
                record_error_code_only("executor", "ERR_GIT_DIFF_FAILED")
                return "FAIL"
        except Exception as _del_err:
            print(f"[GATE WARN] Deletion check error: {_del_err} — skipping.", file=sys.stderr)

    # FIND-DONE-FILE: Record executor_succeeded = True so the orchestrator can distinguish
    # "executor OK, reviewer failed" from "executor failed" on restart.
    _state = {}
    if os.path.exists(PHASE_STATE_FILE):
        try:
            with open(PHASE_STATE_FILE, "r") as _f:
                _state = json.load(_f)
        except Exception:
            pass
    _state["executor_succeeded"] = True
    _phase_dir = os.path.dirname(PHASE_STATE_FILE) or "."
    os.makedirs(_phase_dir, exist_ok=True)
    _fd, _tmp = tempfile.mkstemp(dir=_phase_dir, prefix="phase_state_")
    try:
        with os.fdopen(_fd, "w") as _f:
            json.dump(_state, _f, indent=2)
        os.replace(_tmp, PHASE_STATE_FILE)
    except Exception:
        if os.path.exists(_tmp):
            os.remove(_tmp)

    _clear_executor_gate_detail()
    return "PASS"

if __name__ == "__main__":
    result = evaluate_executor(sys.argv[1] if len(sys.argv) > 1 else None)
    print(result)
