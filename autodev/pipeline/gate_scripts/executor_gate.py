"""Executor verdict gate — validates the executor's output, file manifest, and TDD coverage.

Verdict gate (see ./README.md): prints ``PASS`` / ``FAIL`` on stdout and **always exits 0**;
FAIL detail rides side channels (``executor_gate_detail.json`` / ``gate_warnings.json`` /
``last_error_code``), never stdout. Includes the fail-closed unaccounted-deletion guard (the
MiniMax file-deletion defence). Deterministic — no LLM, network, or clock.
"""
import json
import os
import re as _re
import subprocess
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from utils import (
    ARTIFACTS_DIR,
    load_json_safe,
    phase_has_behavioral_block,
    read_phase_state_for_rewrite,
    record_error_code_only,
    PHASE_STATE_FILE,
    WORKSPACE_DIR,
    write_json_atomic,
    ERR_STATUS_NOT_COMPLETE,
    ERR_TESTS_FAILING,
    ERR_VALIDATION_FAILED,
    ERR_PATH_TRAVERSAL,
    ERR_MANIFEST_FILE_MISSING,
    ERR_TDD_COVERAGE_MISMATCH,
    ERR_BEHAVIORAL_ARTIFACTS_MISSING,
    ERR_MISSING_BASE_COMMIT,
    ERR_UNACCOUNTED_DELETION,
    ERR_GIT_DIFF_FAILED,
    ERR_DELETION_CHECK_CRASHED,
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
# P1 Stage F — separate advisory channel. Failure detail goes in
# ``executor_gate_detail.json`` (consumed by write_failure_context for executor
# self-heal). Advisory output goes in ``executor_advisory_detail.json``
# (consumed by _emit_reachability_advisory for events). The two channels
# never co-tenant.
EXECUTOR_ADVISORY_DETAIL_JSON = "executor_advisory_detail.json"
# Phase 3 (gate-feedback methodology) — reviewer-facing PASS channel. Three
# interpretive checks (manifest-file-missing, tdd-coverage-mismatch,
# behavioral-artifacts-missing) that used to hard-FAIL the gate are now
# NON-blocking warnings recorded here. Distinct from both sibling channels:
# executor_gate_detail.json is the executor-facing FAIL channel (drained +
# removed by write_failure_context); executor_advisory_detail.json is the
# reachability advisory (drained + removed by _emit_reachability_advisory).
# gate_warnings.json is the only PASS channel the orchestrator drains but does
# NOT remove — the reviewer reads it to adjudicate the warnings. Cleared at the
# top of every evaluate_executor() run; written only on PASS when warnings exist.
GATE_WARNINGS_JSON = "gate_warnings.json"


def _executor_gate_detail_path():
    return os.path.join(ARTIFACTS_DIR, EXECUTOR_GATE_DETAIL_JSON)


def _executor_advisory_detail_path():
    return os.path.join(ARTIFACTS_DIR, EXECUTOR_ADVISORY_DETAIL_JSON)


def _gate_warnings_path():
    return os.path.join(ARTIFACTS_DIR, GATE_WARNINGS_JSON)


def _clear_executor_gate_detail():
    try:
        os.remove(_executor_gate_detail_path())
    except FileNotFoundError:
        pass


def _clear_advisory_detail():
    """Remove executor_advisory_detail.json if it exists; ignore errors."""
    try:
        os.remove(_executor_advisory_detail_path())
    except FileNotFoundError:
        pass


def _write_executor_gate_detail(payload: dict) -> None:
    """Atomic JSON write for orchestrator to merge into failure_context.json."""
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    write_json_atomic(_executor_gate_detail_path(), payload, indent=2, raise_on_error=False)


def _write_advisory_detail(payload: dict) -> None:
    """Atomic write to executor_advisory_detail.json — same shape as
    _write_executor_gate_detail but to the advisory channel."""
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    write_json_atomic(_executor_advisory_detail_path(), payload, indent=2, raise_on_error=False)


def _clear_gate_warnings():
    """Remove gate_warnings.json if it exists; ignore errors.

    Called at the top of every evaluate_executor() run so a fresh attempt never
    inherits a prior attempt's warnings. Because the executor gate runs in every
    phase before the reviewer, this start-clear is what guarantees the reviewer
    only ever reads warnings from the current attempt — even though the
    orchestrator (unlike the FAIL/advisory channels) deliberately does NOT remove
    this file after draining it.
    """
    try:
        os.remove(_gate_warnings_path())
    except FileNotFoundError:
        pass


def _write_gate_warnings(payload: dict) -> None:
    """Atomic write to gate_warnings.json — the reviewer-facing PASS channel.

    Unlike the sibling detail writers, this file is NOT removed by the gate on
    PASS: the reviewer reads it to adjudicate the demoted warnings
    (accept-and-proceed or reject-with-specifics).
    """
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    write_json_atomic(_gate_warnings_path(), payload, indent=2, raise_on_error=False)


def _check_reachability_advisory(current_phase, executor_output, project_root):
    """Return {summary, not_applicable, diagnostics} envelope; never raises.

    Short-circuits with an all-empty envelope on every non-COMPLETE phase —
    reachability is a whole-artifact property; running it per phase would
    cry wolf on the routine add-then-wire roadmap pattern.
    """
    raw_id = (current_phase or {}).get("raw_id") or ""
    if not raw_id.startswith("COMPLETE-"):
        return {"summary": None, "not_applicable": None, "diagnostics": []}
    entry = (current_phase or {}).get("entry_point") or {}
    cmd = (entry.get("command") or "").strip() if isinstance(entry, dict) else ""
    if not cmd:
        return {"summary": None, "not_applicable": None, "diagnostics": []}
    try:
        from reachability import classify_command, get_resolver
    except Exception as e:
        return {
            "summary": None, "not_applicable": None,
            "diagnostics": [{"file": None, "reason": f"reachability import failed: {e}",
                             "kind": "resolver_error"}],
        }
    try:
        classification = classify_command(cmd)
    except Exception as e:
        return {
            "summary": None, "not_applicable": None,
            "diagnostics": [{"file": None, "reason": f"classify_command crashed: {e}",
                             "kind": "resolver_error"}],
        }
    if classification == "test_runner":
        head = cmd.split()[0] if cmd.split() else cmd
        return {
            "summary": None,
            "not_applicable": {
                "reason": f"entry point is a test runner ({head!r}); reachability check intentionally skipped"
            },
            "diagnostics": [],
        }
    if classification == "unsupported":
        return {
            "summary": None, "not_applicable": None,
            "diagnostics": [{"file": None, "reason": f"no resolver for entry command {cmd!r}",
                             "kind": "no_resolver"}],
        }
    try:
        resolver = get_resolver(cmd, project_root)
    except Exception as e:
        return {
            "summary": None, "not_applicable": None,
            "diagnostics": [{"file": None, "reason": f"resolver registry crashed: {e}",
                             "kind": "resolver_error"}],
        }
    if resolver is None:
        return {
            "summary": None, "not_applicable": None,
            "diagnostics": [{"file": None, "reason": f"no resolver instantiated for {cmd!r}",
                             "kind": "resolver_error"}],
        }
    try:
        result = resolver.resolve(project_root, cmd)
    except Exception as e:
        return {
            "summary": None, "not_applicable": None,
            "diagnostics": [{"file": None,
                             "reason": f"resolver crashed: {type(e).__name__}: {e}",
                             "kind": "resolver_error"}],
        }
    diagnostics = [
        {"file": None, "reason": lim, "kind": "resolver_limitation"}
        for lim in (result.limitations or [])
    ]
    if result.entry_resolved is None:
        diagnostics.append({
            "file": None,
            "reason": f"could not resolve entry from {cmd!r}",
            "kind": "resolver_error",
        })
        return {"summary": None, "not_applicable": None, "diagnostics": diagnostics}
    manifest = executor_output.get("file_manifest") or []
    reachable_norm = {os.path.normpath(p) for p in (result.reachable or set())}
    unreachable = [
        p for p in manifest
        if isinstance(p, str) and os.path.normpath(p) not in reachable_norm
    ]
    summary = None
    if unreachable:
        summary = {
            "files": unreachable,
            "count": len(unreachable),
            "command": cmd,
            # Copy hedge: do NOT call this "dead." Operator must read it as
            # "you may have intended this — confirm before treating as a problem."
            "reason_template": (
                "declared in manifest but not reached from entry point — orphan, "
                "or wiring landed in a different entry's path"
            ),
        }
    return {"summary": summary, "not_applicable": None, "diagnostics": diagnostics}


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


def evaluate_executor(output_path=None):
    if output_path is None:
        output_path = os.path.join(ARTIFACTS_DIR, "executor_output.json")

    _clear_executor_gate_detail()
    _clear_gate_warnings()

    # Phase 3 — demoted interpretive checks (manifest-missing, tdd-mismatch,
    # behavioral-artifacts) accumulate here as non-blocking warnings instead of
    # returning "FAIL". On PASS a non-empty list is written to gate_warnings.json
    # for the reviewer to adjudicate; hard-keep checks below still return "FAIL".
    warnings = []

    data = load_json_safe(output_path, "executor")
    if data is None: return "FAIL"

    if data.get("status") != "complete":
        record_error_code_only("executor", ERR_STATUS_NOT_COMPLETE)
        return "FAIL"

    test_results = data.get("test_results", {})
    if test_results.get("all_passing") is not True:
        record_error_code_only("executor", ERR_TESTS_FAILING)
        return "FAIL"

    # Verify file manifest existences and bounds
    # T1.1 — coerce-validate the manifest fields before concatenation. ``.get(k, [])``
    # only rescues an *absent* key; a present-but-non-list value (MiniMax emits
    # ``"foo.py"`` instead of ``["foo.py"]``) would raise TypeError here, crashing
    # the gate BEFORE the MiniMax file-deletion guard runs and writing no error
    # code. Fail closed with ERR_VALIDATION_FAILED so the self-heal feedback
    # survives and the orchestrator retries with a fresh session.
    _file_manifest = data.get("file_manifest", [])
    _tests_written = data.get("tests_written", [])
    if not isinstance(_file_manifest, list) or not isinstance(_tests_written, list):
        record_error_code_only("executor", ERR_VALIDATION_FAILED)
        return "FAIL"
    expected_files = _file_manifest + _tests_written
    workspace_real = os.path.realpath(WORKSPACE_DIR)

    _missing_manifest_files = []
    for relative_path in expected_files:
        target_real = os.path.realpath(os.path.join(WORKSPACE_DIR, relative_path))

        # Canonical (realpath) bounds checking — SECURITY guard, NOT demoted by
        # Phase 3. Both sides are symlink-resolved so an in-workspace symlink that
        # points outside the workspace cannot pass the boundary (CLAUDE.md Security
        # Constraints; do not weaken).
        try:
            if os.path.commonpath([workspace_real, target_real]) != workspace_real:
                record_error_code_only("executor", ERR_PATH_TRAVERSAL)
                return "FAIL"
        except ValueError:
            record_error_code_only("executor", ERR_PATH_TRAVERSAL)
            return "FAIL"

        # Phase 3 — a declared-but-absent (in-bounds) file is now a warning the
        # reviewer adjudicates, not a hard FAIL. The reviewer independently
        # re-verifies file_manifest existence (reviewer/AGENTS.md "What to
        # Actually Review"), so this is a focusing hint, not the sole safety net.
        if not os.path.exists(target_real):
            _missing_manifest_files.append(relative_path)

    if _missing_manifest_files:
        warnings.append({
            "code": ERR_MANIFEST_FILE_MISSING,
            "detail": (
                "File(s) listed in file_manifest/tests_written are not present "
                "on disk. The reviewer must confirm each declared file exists "
                "and contains real implementation."
            ),
            "files": _missing_manifest_files,
        })

    # Cross-reference tests_written against planner's tdd_test_structure.
    planner_output_path = os.path.join(ARTIFACTS_DIR, "planner_output.json")
    planner_data = load_json_safe(planner_output_path, "executor")
    if planner_data is not None:
        planned_tests = planner_data.get("tdd_test_structure", [])
        # T1.3 — coerce a non-list tdd_test_structure to [] so the membership
        # comprehension below does not iterate a string per-character (which
        # produces a garbage ``missing`` list and a spurious
        # ERR_TDD_COVERAGE_MISMATCH warning) or crash on a non-iterable. The
        # executor's own ``tests_written`` is already list-guaranteed by the T1.1
        # manifest-type guard above, so it needs no re-coercion here.
        if not isinstance(planned_tests, list):
            planned_tests = []
        tests_written = data.get("tests_written", [])
        missing = [t for t in planned_tests if t not in tests_written]
        if missing:
            # Phase 3 — a coverage gap is a warning the reviewer adjudicates
            # (the reviewer independently inspects tests_written for real
            # assertions), not a hard FAIL.
            print(f"[GATE WARN] Planned tests not in tests_written: {missing}", file=sys.stderr)
            warnings.append({
                "code": ERR_TDD_COVERAGE_MISMATCH,
                "detail": (
                    "Planner tdd_test_structure entries are missing from "
                    "tests_written. The reviewer must confirm coverage of the "
                    "planned behaviour."
                ),
                "missing_tests": missing,
            })

    # ------------------------------------------------------------------
    # FIND-BEHAVIORAL-ARTIFACTS: P0 Stage F + Phase 3. Phases whose
    # current_phase.json carries a populated behavioral_verification block
    # (effectively every P0 phase) are expected to report
    # behavioral_smoke_artifacts proving the executor ran the phase's
    # how_to_check procedure. As of Phase 3, a missing / empty / malformed /
    # not-on-disk artifact list is a NON-blocking warning the reviewer
    # adjudicates (the reviewer is the independent producer of
    # behavioral_verification evidence) — NOT a gate FAIL. The
    # workspace-boundary check stays a hard FAIL: a path escaping the workspace
    # is a security issue, not an interpretive one.
    # ------------------------------------------------------------------
    _current_phase = _load_current_phase()
    if phase_has_behavioral_block(_current_phase):
        behavioral_artifacts = data.get("behavioral_smoke_artifacts") or []
        if not isinstance(behavioral_artifacts, list) or len(behavioral_artifacts) == 0:
            print(
                "[GATE WARN] behavioral_smoke_artifacts missing or empty on a "
                "phase with a populated behavioral_verification block.",
                file=sys.stderr,
            )
            warnings.append({
                "code": ERR_BEHAVIORAL_ARTIFACTS_MISSING,
                "detail": (
                    "behavioral_smoke_artifacts is missing or empty on a phase "
                    "with a behavioral_verification block. The reviewer must "
                    "produce its own evidence by running "
                    "current_phase.behavioral_verification.how_to_check."
                ),
            })
        else:
            for i, entry in enumerate(behavioral_artifacts):
                if not isinstance(entry, dict):
                    print(
                        f"[GATE WARN] behavioral_smoke_artifacts[{i}] is not an "
                        f"object (got {type(entry).__name__}).",
                        file=sys.stderr,
                    )
                    warnings.append({
                        "code": ERR_BEHAVIORAL_ARTIFACTS_MISSING,
                        "detail": f"behavioral_smoke_artifacts[{i}] is not a {{path, description}} object.",
                    })
                    continue
                path = entry.get("path")
                if not path or not isinstance(path, str):
                    print(
                        f"[GATE WARN] behavioral_smoke_artifacts[{i}] missing path",
                        file=sys.stderr,
                    )
                    warnings.append({
                        "code": ERR_BEHAVIORAL_ARTIFACTS_MISSING,
                        "detail": f"behavioral_smoke_artifacts[{i}] is missing its path.",
                    })
                    continue
                target_real = os.path.realpath(os.path.join(WORKSPACE_DIR, path))
                # Workspace-boundary check — SECURITY guard, NOT demoted. Canonical
                # (realpath) resolution on both sides so a symlink escape is caught.
                try:
                    if os.path.commonpath([workspace_real, target_real]) != workspace_real:
                        record_error_code_only("executor", ERR_PATH_TRAVERSAL)
                        print(
                            f"[GATE FAIL] behavioral_smoke_artifacts[{i}] path escapes workspace: {path}",
                            file=sys.stderr,
                        )
                        return "FAIL"
                except ValueError:
                    record_error_code_only("executor", ERR_PATH_TRAVERSAL)
                    print(
                        f"[GATE FAIL] behavioral_smoke_artifacts[{i}] path escapes workspace: {path}",
                        file=sys.stderr,
                    )
                    return "FAIL"
                if not os.path.exists(target_real):
                    print(
                        f"[GATE WARN] behavioral_smoke_artifacts[{i}] path does not exist on disk: {path}",
                        file=sys.stderr,
                    )
                    warnings.append({
                        "code": ERR_BEHAVIORAL_ARTIFACTS_MISSING,
                        "detail": f"behavioral_smoke_artifacts[{i}] path does not exist on disk: {path}",
                        "files": [path],
                    })

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
    pipeline_state_path = os.path.join(os.path.dirname(WORKSPACE_DIR.rstrip(os.sep)), "pipeline_state.json")
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
            json.dumps({"error": ERR_MISSING_BASE_COMMIT, "detail": "cannot verify deletions"}),
            file=sys.stdout,
        )
        print("[GATE FAIL] phase_base_commit unavailable — failing closed to protect deletion guard.", file=sys.stderr)
        record_error_code_only("executor", ERR_MISSING_BASE_COMMIT)
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
                            "gate_error": ERR_UNACCOUNTED_DELETION,
                            "unaccounted_deletions": _sorted_unaccounted,
                        }
                    )
                    record_error_code_only("executor", ERR_UNACCOUNTED_DELETION)
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
                record_error_code_only("executor", ERR_GIT_DIFF_FAILED)
                return "FAIL"
        except Exception as _del_err:
            # Fail closed: if the deletion check itself crashes (git missing,
            # killed, timeout, OSError) we cannot verify deletions, so the MiniMax
            # deletion guard is effectively disabled. Skipping-and-PASSing would
            # silently destroy the only automated defence against the model
            # deleting project files — match the missing-base / git-rc!=0 siblings
            # and FAIL so the orchestrator retries with a fresh session.
            print(
                f"[GATE FAIL] Deletion check crashed: {_del_err!r} — failing closed "
                "to protect the MiniMax deletion guard.",
                file=sys.stderr,
            )
            record_error_code_only("executor", ERR_DELETION_CHECK_CRASHED)
            return "FAIL"

    # P1 Stage F — reachability advisory. Pure addition; never fails the gate.
    # Short-circuits on non-COMPLETE phases, so this is near-zero cost on the
    # common path. Advisory output lives on its own channel
    # (executor_advisory_detail.json) — completely independent of the FAIL-
    # channel _clear_executor_gate_detail() call below.
    _clear_advisory_detail()
    try:
        _reach_current = _load_current_phase()
        _reach_envelope = _check_reachability_advisory(_reach_current, data, WORKSPACE_DIR)
    except Exception as _rwerr:
        _reach_envelope = {
            "summary": None, "not_applicable": None,
            "diagnostics": [{"file": None,
                             "reason": f"reachability check crashed: {_rwerr!r}",
                             "kind": "resolver_error"}],
        }
    if _reach_envelope["summary"] or _reach_envelope["not_applicable"] or _reach_envelope["diagnostics"]:
        _write_advisory_detail({
            "reachability_summary": _reach_envelope["summary"],
            "reachability_not_applicable": _reach_envelope["not_applicable"],
            "reachability_diagnostics": _reach_envelope["diagnostics"],
        })
        if _reach_envelope["summary"]:
            print(
                f"[GATE INFO] reachability advisory: "
                f"{_reach_envelope['summary']['count']} file(s) not reached from entry",
                file=sys.stderr,
            )
        if _reach_envelope["not_applicable"]:
            print(
                f"[GATE INFO] reachability not applicable: "
                f"{_reach_envelope['not_applicable']['reason']}",
                file=sys.stderr,
            )
        for _d in _reach_envelope["diagnostics"]:
            print(
                f"[GATE INFO] reachability diagnostic ({_d['kind']}): {_d['reason']}",
                file=sys.stderr,
            )

    # FIND-DONE-FILE: Record executor_succeeded = True so the orchestrator can distinguish
    # "executor OK, reviewer failed" from "executor failed" on restart.
    # A present-but-corrupt phase_state.json skips the write (returns None) so
    # the governance counters aren't wiped by a rebuild-from-{} — the
    # orchestrator's quarantine path owns corrupt-state recovery.
    _state = read_phase_state_for_rewrite()
    if _state is not None:
        _state["executor_succeeded"] = True
        os.makedirs(os.path.dirname(PHASE_STATE_FILE) or ".", exist_ok=True)
        write_json_atomic(PHASE_STATE_FILE, _state, indent=2, raise_on_error=False)

    _clear_executor_gate_detail()

    # Phase 3 — surface the demoted interpretive checks to the reviewer. Written
    # only on PASS and deliberately NOT removed here (the orchestrator drains it
    # for events but preserves it; the reviewer reads it to adjudicate). The
    # start-of-run _clear_gate_warnings() already removed any stale file, so an
    # absent file unambiguously means "no warnings this attempt".
    if warnings:
        _write_gate_warnings({
            "phase_raw_id": (_current_phase or {}).get("raw_id", ""),
            "warnings": warnings,
        })

    return "PASS"

if __name__ == "__main__":
    result = evaluate_executor(sys.argv[1] if len(sys.argv) > 1 else None)
    print(result)
