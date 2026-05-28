import json
import os
import re as _re
import subprocess
import sys
import tempfile

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from utils import (
    ARTIFACTS_DIR,
    load_json_safe,
    phase_has_behavioral_block,
    record_error_code_only,
    PHASE_STATE_FILE,
    WORKSPACE_DIR,
)

# Vite content-hash pattern: dist/assets/<name>-<6-12 alphanum chars>.<js|css>
# Only files matching this pattern are eligible for build-artifact rotation auto-accounting.
_VITE_CONTENT_HASH_RE = _re.compile(
    r"^dist/assets/[^/]+-[A-Za-z0-9_-]{6,12}\.(js|css)$"
)


def _is_build_artifact_rotation(path: str, workspace_dir: str) -> bool:
    """Return True iff path is a vite content-hashed bundle replaced by a new hash.

    Conditions: path matches _VITE_CONTENT_HASH_RE AND the parent directory still
    contains at least one file with the same extension (proving rotation, not wipe).
    """
    if not _VITE_CONTENT_HASH_RE.match(path):
        return False
    parent = os.path.join(workspace_dir.rstrip(os.sep), os.path.dirname(path))
    if not os.path.isdir(parent):
        return False
    ext = os.path.splitext(path)[1]
    return any(f.endswith(ext) for f in os.listdir(parent))


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


def _load_current_phase():
    """Return the full ``current_phase.json`` dict, or {} on miss.

    A sibling helper used by the behavioural-artifacts check; loads the same
    file that ``phase_resolver.py`` writes after Stage D, then read by the
    reviewer gate's ``_load_current_phase`` (the two gates intentionally
    keep their own copies — see plan §7 callout: extracting to utils.py is
    deferred until a third caller appears)."""
    path = os.path.join(ARTIFACTS_DIR, "current_phase.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


_PRD_VERBATIM_PREFIX = "prd_verbatim:"
# Chunk size for grep file arguments. Keeps us well under ARG_MAX on every
# POSIX shell the pipeline targets (Linux ARG_MAX is ~128 KiB; 500 paths at an
# average 60 bytes each is ~30 KiB).
_GREP_CHUNK_SIZE = 500


def _extract_prd_verbatim_anchors(planner_data):
    """Return the ordered list of literal substrings declared by
    ``pass_criteria[].traces_to`` entries of the form
    ``"prd_verbatim:<str>"``.

    Malformed entries (non-dict, non-string traces_to, wrong prefix) are
    skipped silently — the planner gate is the authoritative shape
    validator; this consumer takes what is well-formed. Order is
    preserved so failure reports are deterministic; no deduplication
    (callers may dedupe if they care)."""
    if not isinstance(planner_data, dict):
        return []
    criteria = planner_data.get("pass_criteria") or []
    if not isinstance(criteria, list):
        return []
    anchors = []
    for entry in criteria:
        if not isinstance(entry, dict):
            continue
        traces_to = entry.get("traces_to")
        if not isinstance(traces_to, str):
            continue
        if traces_to.startswith(_PRD_VERBATIM_PREFIX):
            anchors.append(traces_to[len(_PRD_VERBATIM_PREFIX):])
    return anchors


def _tracked_files(workspace_dir):
    """Return the list of git-tracked files in ``workspace_dir`` as
    workspace-relative paths.

    Empty list when ``git ls-files`` errors or the workspace is not a git
    repo. Logs ``[GATE WARN]`` to stderr on failure rather than raising —
    the caller's job is to treat missing files as missing anchors, not
    to crash the gate."""
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=workspace_dir,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"[GATE WARN] git ls-files failed: {e}", file=sys.stderr)
        return []
    if result.returncode != 0:
        print(
            f"[GATE WARN] git ls-files rc={result.returncode}: "
            f"{result.stderr.strip()}",
            file=sys.stderr,
        )
        return []
    return [p for p in result.stdout.splitlines() if p]


def _check_prd_verbatim_anchors(planner_data, workspace_dir):
    """Return ``(True, [])`` when no anchors are declared or every anchor
    is found verbatim in at least one git-tracked file. Return
    ``(False, missing_list)`` otherwise.

    Uses ``grep -F`` so regex metacharacters in anchors are treated as
    literal characters. Files are passed to grep in chunks of
    ``_GREP_CHUNK_SIZE`` to stay under ARG_MAX on every POSIX shell."""
    anchors = _extract_prd_verbatim_anchors(planner_data)
    if not anchors:
        return (True, [])

    tracked = _tracked_files(workspace_dir)
    if not tracked:
        # No tracked files → no eligible source for any anchor. Conservative:
        # treat every anchor as missing.
        return (False, list(anchors))

    missing = []
    for anchor in anchors:
        found = False
        for i in range(0, len(tracked), _GREP_CHUNK_SIZE):
            chunk = tracked[i:i + _GREP_CHUNK_SIZE]
            try:
                result = subprocess.run(
                    ["grep", "-F", "-l", "--", anchor] + chunk,
                    cwd=workspace_dir,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                print(
                    f"[GATE WARN] grep failed for anchor={anchor!r}: {e}",
                    file=sys.stderr,
                )
                continue
            if result.returncode == 0:
                found = True
                break
            if result.returncode == 1:
                # Anchor not in this chunk; try next chunk.
                continue
            # rc >= 2 is a grep error (e.g. unreadable file). Log and treat
            # as not found in this chunk; the next chunks may still match.
            print(
                f"[GATE WARN] grep rc={result.returncode} for anchor="
                f"{anchor!r}: {result.stderr.strip()}",
                file=sys.stderr,
            )
        if not found:
            missing.append(anchor)
    if missing:
        return (False, missing)
    return (True, [])


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

        # P1 Stage C. Every pass_criteria entry whose traces_to is
        # 'prd_verbatim:<literal>' must have its literal string present in
        # at least one git-tracked file in the workspace. grep -F semantics:
        # regex metacharacters in the anchor are treated as literal
        # characters. Runs after the cheap structural tdd cross-check so
        # subprocess work is gated on the manifest already being shape-valid.
        ok, missing_anchors = _check_prd_verbatim_anchors(
            planner_data, WORKSPACE_DIR
        )
        if not ok:
            _write_executor_gate_detail({
                "gate_error": "ERR_PRD_VERBATIM_MISSING",
                "missing_anchors": missing_anchors,
            })
            record_error_code_only("executor", "ERR_PRD_VERBATIM_MISSING")
            print(
                f"[GATE FAIL] PRD-verbatim anchors missing from tracked "
                f"source: {missing_anchors}",
                file=sys.stderr,
            )
            return "FAIL"

    # ------------------------------------------------------------------
    # FIND-BEHAVIORAL-ARTIFACTS: P0 Stage F. Phases whose current_phase.json
    # carries a populated behavioral_verification block (effectively every
    # P0 phase) must report behavioral_smoke_artifacts proving the executor
    # ran the phase's how_to_check procedure. Path-safety rules are the
    # same as file_manifest: workspace-bounded via os.path.commonpath, and
    # each listed path must exist on disk.
    # ------------------------------------------------------------------
    _current_phase = _load_current_phase()
    if phase_has_behavioral_block(_current_phase):
        behavioral_artifacts = data.get("behavioral_smoke_artifacts") or []
        if not isinstance(behavioral_artifacts, list) or len(behavioral_artifacts) == 0:
            record_error_code_only("executor", "ERR_BEHAVIORAL_ARTIFACTS_MISSING")
            print(
                "[GATE FAIL] behavioral_smoke_artifacts missing or empty on a "
                "phase with a populated behavioral_verification block. "
                "Capture the output of current_phase.behavioral_verification.how_to_check "
                "under pipeline-project/.autodev/pipeline/behavioral-smoke/ and "
                "list it in executor_output.behavioral_smoke_artifacts.",
                file=sys.stderr,
            )
            return "FAIL"
        for i, entry in enumerate(behavioral_artifacts):
            if not isinstance(entry, dict):
                record_error_code_only("executor", "ERR_BEHAVIORAL_ARTIFACTS_MISSING")
                print(
                    f"[GATE FAIL] behavioral_smoke_artifacts[{i}] is not an object "
                    f"(got {type(entry).__name__}). Each entry must be "
                    f"{{path, description}}.",
                    file=sys.stderr,
                )
                return "FAIL"
            path = entry.get("path")
            if not path or not isinstance(path, str):
                record_error_code_only("executor", "ERR_BEHAVIORAL_ARTIFACTS_MISSING")
                print(
                    f"[GATE FAIL] behavioral_smoke_artifacts[{i}] missing path",
                    file=sys.stderr,
                )
                return "FAIL"
            target_abs = os.path.abspath(os.path.join(WORKSPACE_DIR, path))
            try:
                if os.path.commonpath([workspace_abs, target_abs]) != workspace_abs:
                    record_error_code_only("executor", "ERR_PATH_TRAVERSAL")
                    print(
                        f"[GATE FAIL] behavioral_smoke_artifacts[{i}] path escapes workspace: {path}",
                        file=sys.stderr,
                    )
                    return "FAIL"
            except ValueError:
                record_error_code_only("executor", "ERR_PATH_TRAVERSAL")
                print(
                    f"[GATE FAIL] behavioral_smoke_artifacts[{i}] path escapes workspace: {path}",
                    file=sys.stderr,
                )
                return "FAIL"
            if not os.path.exists(target_abs):
                record_error_code_only("executor", "ERR_BEHAVIORAL_ARTIFACTS_MISSING")
                print(
                    f"[GATE FAIL] behavioral_smoke_artifacts[{i}] path does not exist on disk: {path}",
                    file=sys.stderr,
                )
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

                _unaccounted_all = [
                    f for f in _deleted_at_base
                    if f not in _file_manifest_set and f not in _files_deleted_set
                ]
                _build_rotations = [
                    f for f in _unaccounted_all
                    if _is_build_artifact_rotation(f, WORKSPACE_DIR)
                ]
                _unaccounted = [f for f in _unaccounted_all if f not in _build_rotations]

                if _build_rotations:
                    print(
                        f"[GATE WARN] Build artifact rotation auto-accounted: "
                        f"{sorted(_build_rotations)}. "
                        "Add these to `files_deleted` in executor_output.json to suppress this warning.",
                        file=sys.stderr,
                    )

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
