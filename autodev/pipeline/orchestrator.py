import os
import sys
import fcntl
import json
import time
import tempfile
import subprocess
import traceback
from datetime import datetime, timezone
import logging
import requests

from webhook_client import invoke_agent_webhook
from sentinel_poller import cleanup_output_files, poll_for_sentinel, poll_for_sentinel_with_idle_detect
from skill_manager import SkillManager
from queue_semantics import parent_blocks_child
from env_resolvers import resolve_openclaw_root, resolve_pipeline_root

OPENCLAW_ROOT = resolve_openclaw_root()
AUTODEV_REPO_PATH = os.environ.get(
    "AUTODEV_REPO_PATH",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


# Pipeline state directory. Resolved via env_resolvers: OPENCLAW_ROOT is the
# OpenClaw hub, AUTODEV_PIPELINE_ROOT is the pipeline state directory. Operators
# who want state to live next to OpenClaw set AUTODEV_PIPELINE_ROOT=$OPENCLAW_ROOT
# explicitly — there are no legacy aliases.
AUTODEV_PIPELINE_ROOT = resolve_pipeline_root(AUTODEV_REPO_PATH)

LOCK_FILE = os.path.join(AUTODEV_PIPELINE_ROOT, "pipeline.lock")
STATE_FILE = os.path.join(AUTODEV_PIPELINE_ROOT, "pipeline_state.json")
SYMLINK_TARGET = os.path.join(AUTODEV_PIPELINE_ROOT, "pipeline-project")
# Per-project pipeline artifacts (not roadmap.md / prd.md at repo root).
PROJECT_ARTIFACTS_DIR = os.path.join(SYMLINK_TARGET, ".autodev", "pipeline")
CONFIG_FILE = os.path.join(OPENCLAW_ROOT, "openclaw.json")
PHASE_STATE_FILE = os.path.join(PROJECT_ARTIFACTS_DIR, "phase_state.json")

ORCHESTRATOR_FILENAME = "orchestrator.py"


def _watch_root_for_idle_detect() -> str:
    """Resolved project root for ``sentinel_poller`` file-mtime idle detection.

    ``poll_for_sentinel_with_idle_detect`` uses ``watch_dirs`` to reset the idle
    clock when the executor writes project files. Watching ``os.path.realpath``
    of the pipeline-project symlink ensures activity under the real checkout is
    observed consistently (symlink path alone can miss updates on some setups).
    """
    try:
        if os.path.lexists(SYMLINK_TARGET):
            return os.path.realpath(SYMLINK_TARGET)
    except OSError:
        pass
    return SYMLINK_TARGET


def _verify_symlinks_consistent(project_path: str) -> bool:
    """Warn if either pipeline-project symlink diverges from project_path.

    Both AUTODEV_PIPELINE_ROOT/pipeline-project (polled by orchestrator for
    sentinel files) and OPENCLAW_ROOT/pipeline-project (followed by agent
    workspace symlinks when writing output) must resolve to the same real
    directory as the active project.  If they diverge — which can happen when
    an operator manually runs `ln -sfn` during recovery and only updates one —
    the executor writes sentinels to a different tree than the orchestrator
    polls, causing infinite retries.

    This function is READ-ONLY: it only logs a warning and returns False.
    Callers are responsible for keeping symlinks in sync via update_symlink().
    """
    target = os.path.abspath(project_path)
    openclaw_symlink = os.path.join(OPENCLAW_ROOT, "pipeline-project")
    issues = []
    for label, path in (
        ("AUTODEV pipeline-project", SYMLINK_TARGET),
        ("OPENCLAW pipeline-project", openclaw_symlink),
    ):
        try:
            resolved = os.path.realpath(path) if os.path.lexists(path) else None
        except OSError as exc:
            issues.append(f"{label}: OSError resolving {path!r}: {exc}")
            continue
        if resolved != target:
            issues.append(
                f"{label}: {path!r} → {resolved!r} (expected {target!r})"
            )
    if issues:
        print(
            "[WARN] Symlink inconsistency detected before agent invocation — "
            "sentinel files may land in wrong directory:\n"
            + "\n".join(f"  {i}" for i in issues)
        )
        return False
    return True


def _validate_openclaw_root(root: str) -> None:
    """Validate OPENCLAW_ROOT and required workspace directories at startup.

    Prints one error line per missing item, then calls sys.exit(1) if anything
    is absent — so operators see all issues at once rather than hitting a
    confusing crash deep inside the pipeline.
    """
    errors = []

    if not os.path.isdir(root):
        errors.append(f"  OPENCLAW_ROOT does not exist or is not a directory: {root}")

    for role in ("planner", "executor", "reviewer"):
        ws = os.path.join(root, f"workspace-{role}")
        if not os.path.isdir(ws):
            errors.append(f"  Missing workspace directory: {ws}")

    config_path = os.path.join(root, "openclaw.json")
    if not os.path.exists(config_path):
        errors.append(f"  openclaw.json not found at: {config_path}")

    if errors:
        print("[ERROR] OPENCLAW_ROOT validation failed:")
        for msg in errors:
            print(msg)
        sys.exit(1)
WEBHOOK_AGENT_ID_PRD = "prd-creator"
ORCHESTRATOR_POLL_TIMEOUT = 120

# Hard cap for gate script subprocess.run — prevents hung gates from stalling the orchestrator.
GATE_SUBPROCESS_TIMEOUT = 60

VALID_STATES = [
    "RUNNING",
    "WAITING_FOR_SENTINEL",
    "WAITING_FOR_HUMAN",
    "HALTED_SILENT",
    "BLOCKED",
    "PIPELINE_COMPLETE",
    "STOPPED",
    "QUEUE_HALTED",
]

QUEUE_FILE = os.path.join(AUTODEV_PIPELINE_ROOT, "pipeline_queue.json")


def _atomic_temp_dir_for_project_writes():
    """Directory for tempfile.mkstemp before os.replace into SYMLINK_TARGET.

    When pipeline-project is missing, falling back to OPENCLAW_ROOT can raise
    FileNotFoundError if ~/.openclaw was never created — use repo-local runtime instead.
    """
    if os.path.exists(SYMLINK_TARGET):
        return SYMLINK_TARGET
    try:
        os.makedirs(AUTODEV_PIPELINE_ROOT, exist_ok=True)
    except OSError:
        pass
    return AUTODEV_PIPELINE_ROOT


# llama-server HTTP origin (scheme + host + port, no path). Set AUTODEV_LLAMA_BASE if not localhost.
_LLAMA_ORIGIN = os.environ.get("AUTODEV_LLAMA_BASE", "http://127.0.0.1:11434").rstrip("/")

# Glob patterns for mkstemp atomic-write temp files that may be stranded if the
# orchestrator was killed mid-write.  Pattern matches the 8-character random hex
# suffix produced by tempfile.mkstemp (e.g. pipeline_state_a3f7c219).
_STRANDED_TEMP_PATTERNS = [
    "pipeline_state_????????",
    "phase_state_????????",
    "current_phase_????????",
]


def cleanup_stranded_temp_files(base_dir: str) -> None:
    """Remove mkstemp orphan temp files left behind by a previous crash mid-write.

    This function ONLY removes files matching the strict 8-char-hex-suffix patterns
    in _STRANDED_TEMP_PATTERNS (e.g. pipeline_state_a3f7c219).  These are created by
    tempfile.mkstemp during atomic writes and are stranded when the process is killed
    before os.replace() completes.  They are extremely rare in practice.

    This function does NOT perform general workspace cleanup — legitimate pipeline
    artifacts (phase_state.json, planner_output.json, current_phase.json, etc.) are
    intentionally preserved here and are cleaned by cleanup_output_files() at the
    start of each agent invocation.

    Searches *base_dir* and (if it exists and is a real directory) the
    pipeline-project subdirectory for each pattern in _STRANDED_TEMP_PATTERNS.
    Safe to call before the lock is held.
    """
    import glob as _glob

    search_dirs = [base_dir]
    project_dir = os.path.join(base_dir, "pipeline-project")
    # Follow symlink only if it resolves to a real directory
    try:
        if os.path.isdir(project_dir):
            real_project = os.path.realpath(project_dir)
            if os.path.isdir(real_project) and real_project not in search_dirs:
                search_dirs.append(real_project)
    except Exception:
        pass

    removed = []
    for directory in search_dirs:
        for pattern in _STRANDED_TEMP_PATTERNS:
            for stale_path in _glob.glob(os.path.join(directory, pattern)):
                try:
                    os.remove(stale_path)
                    removed.append(stale_path)
                except Exception:
                    pass
        # Stranded mkstemp files may also live under .autodev/pipeline/.
        _ad_pipe = os.path.join(directory, ".autodev", "pipeline")
        if os.path.isdir(_ad_pipe):
            for pattern in _STRANDED_TEMP_PATTERNS:
                for stale_path in _glob.glob(os.path.join(_ad_pipe, pattern)):
                    try:
                        os.remove(stale_path)
                        removed.append(stale_path)
                    except Exception:
                        pass

    # Always log the orphan scan result so the operator can see what was cleaned.
    logging.info(
        "[startup] mkstemp orphan scan: removed %d file(s)%s",
        len(removed),
        f": {removed}" if removed else "",
    )

    # Log any pipeline artifact files present in the workspace (informational only —
    # these are by-design working files and are NOT deleted here).
    artifacts = []
    try:
        if os.path.isdir(project_dir):
            _real_pp = os.path.realpath(project_dir)
            _artifact_sub = os.path.join(_real_pp, ".autodev", "pipeline")
            for pattern in ("*_output.json", "*_output.done"):
                glob_root = _artifact_sub if os.path.isdir(_artifact_sub) else _real_pp
                for p in _glob.glob(os.path.join(glob_root, pattern)):
                    artifacts.append(os.path.basename(p))
    except Exception:
        pass
    logging.info(
        "[startup] pipeline artifacts present in workspace: %s",
        sorted(artifacts) if artifacts else [],
    )


def _detect_base_branch(directory: str) -> str:
    """Return the best candidate base branch for the target repository."""
    for branch in ("main", "master", "develop", "trunk"):
        result = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=directory,
        )
        if result.returncode == 0:
            return branch

    remote_head = subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        cwd=directory,
        capture_output=True,
        text=True,
    )
    remote_ref = (remote_head.stdout or "").strip()
    if remote_head.returncode == 0 and remote_ref.startswith("refs/remotes/origin/"):
        return remote_ref[len("refs/remotes/origin/"):]

    init_branch = subprocess.run(
        ["git", "config", "--get", "init.defaultBranch"],
        cwd=directory,
        capture_output=True,
        text=True,
    )
    configured_branch = (init_branch.stdout or "").strip()
    if init_branch.returncode == 0 and configured_branch:
        return configured_branch

    return "main"


def _check_session_dead_on_arrival(sessions_json_path: str, full_key: str):
    """Return (is_dead, error_message) for an OpenClaw session entry.

    Dead-on-arrival = session exists with runtimeMs == 0 AND stopReason == "error".
    This pattern occurs when the underlying provider rejects the request before
    the session does any work (e.g. 402 Payment Required, auth failure). The
    session is registered, terminates immediately, and waiting for sentinel
    output would just burn the idle threshold.

    A session with non-zero runtimeMs that ends in error is a real failure and
    should go through the normal sentinel path so its output (if any) is gated.
    """
    try:
        with open(sessions_json_path, "r") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False, ""

    entry = data.get(full_key)
    if not isinstance(entry, dict):
        return False, ""

    runtime_ms = entry.get("runtimeMs")
    stop_reason = entry.get("stopReason")
    if runtime_ms == 0 and stop_reason == "error":
        return True, str(entry.get("errorMessage", "") or "")
    return False, ""


def _sum_session_tokens(jsonl_path) -> dict:
    """W1-G: Sum token usage from an OpenClaw session JSONL file.

    Filters for type=="message", role=="assistant" rows and sums usage fields.
    Returns a zero dict on any error (None path, missing file, parse failure).
    Mirrors _check_session_dead_on_arrival() pattern — module-level, independently testable.
    """
    zeros = {
        "input": 0, "output": 0, "cache_read": 0,
        "cache_write": 0, "total_tokens": 0, "cost_total": 0.0,
    }
    if not jsonl_path:
        print("[W1G] WARN: jsonl_path is None — returning zeros")
        return dict(zeros)
    if not os.path.exists(jsonl_path):
        print(f"[W1G] WARN: session JSONL not found: {jsonl_path}")
        return dict(zeros)
    result = dict(zeros)
    try:
        with open(jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    if row.get("type") == "message" and row.get("role") == "assistant":
                        u = row.get("usage", {}) or {}
                        result["input"]        += u.get("input", 0)
                        result["output"]       += u.get("output", 0)
                        result["cache_read"]   += u.get("cacheRead", 0)
                        result["cache_write"]  += u.get("cacheWrite", 0)
                        result["total_tokens"] += u.get("totalTokens", 0)
                        cost = u.get("cost", {}) or {}
                        result["cost_total"]   += cost.get("total", 0.0)
                except (ValueError, AttributeError):
                    pass
    except OSError as e:
        print(f"[W1G] WARN: could not read {jsonl_path}: {e}")
    return result


def _write_pipeline_event(event_type: str, phase: str, agent: str, detail_dict) -> None:
    """W1-F: Append one structured event line to AUTODEV_PIPELINE_ROOT/pipeline_events.jsonl.

    Non-blocking: any OSError is printed and swallowed.  The UI SSE stream tails this
    file when present, making events durable across server restarts with no UI changes.
    Schema: {"ts", "event", "project", "phase", "agent", "detail"}
    """
    try:
        # Resolve the active project name from the pipeline-project symlink.
        _project = ""
        try:
            if os.path.lexists(SYMLINK_TARGET):
                _project = os.path.basename(os.path.realpath(SYMLINK_TARGET))
        except Exception:
            pass
        path = os.path.join(AUTODEV_PIPELINE_ROOT, "pipeline_events.jsonl")
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": event_type,
            "project": _project,
            "phase": phase,
            "agent": agent,
            "detail": detail_dict or {},
        }
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        print(f"[WARN] _write_pipeline_event({event_type}): {e}")


def _write_run_manifest(entry: dict) -> None:
    """W2-A: Write run_manifest.json to PROJECT_ARTIFACTS_DIR at run start.

    Called after the queue entry is committed (started_at written) and the symlink
    is updated, but before self.state is reset for the new project.  Graceful: any
    failure is logged and swallowed — it must never abort a queue advance.
    """
    try:
        project_path = entry.get("project_path", "")
        phase_count = 0
        subsystem_set = []
        total_goals_chars = 0

        import glob as _glob
        import re as _re
        roadmap_candidates = _glob.glob(os.path.join(project_path, "*oadmap*.md"))
        if roadmap_candidates:
            try:
                with open(roadmap_candidates[0], "r", errors="replace") as _rf:
                    _content = _rf.read()
                phase_ids = _re.findall(r'`([A-Z]+-[A-Z0-9]+)`', _content)
                phase_count = len(phase_ids)
                subsystem_set = sorted(set(pid.split("-")[0] for pid in phase_ids))
                for _line in _content.splitlines():
                    _parts = [p.strip() for p in _line.split("|")]
                    _non_empty = [p for p in _parts if p]
                    # Works for both table format (`| `ID` | pri | goal |`) and
                    # list format (`- [x] `ID` | pri | goal`).
                    if (len(_non_empty) >= 3
                            and any(_re.search(r'`[A-Z]+-[A-Z0-9]+`', p) for p in _parts)):
                        total_goals_chars += len(_non_empty[-1])
            except Exception as _e:
                print(f"[W2A] roadmap parse warning: {_e}")

        manifest = {
            "schema_version": 1,
            "project_path": project_path,
            "project_name": entry.get("name", ""),
            "queue_entry_id": entry.get("id", ""),
            "idea_id": entry.get("idea_id"),
            "started_at": entry.get("started_at", ""),
            "phase_count": phase_count,
            "subsystem_set": subsystem_set,
            "total_goals_chars": total_goals_chars,
        }

        os.makedirs(PROJECT_ARTIFACTS_DIR, exist_ok=True)
        _fd, _tmp = tempfile.mkstemp(dir=PROJECT_ARTIFACTS_DIR, prefix=".run_manifest_")
        try:
            with os.fdopen(_fd, "w") as _f:
                json.dump(manifest, _f)
            os.replace(_tmp, os.path.join(PROJECT_ARTIFACTS_DIR, "run_manifest.json"))
        except Exception:
            if os.path.exists(_tmp):
                os.remove(_tmp)
            raise
        print(f"[W2A] run_manifest.json written: {phase_count} phases, subsystems={subsystem_set}")
    except Exception as _e:
        print(f"[W2A] run_manifest write failed (non-fatal): {_e}")


def _write_run_summary(outcome: str, outcome_detail: str) -> None:
    """W2-B: Write run_summary.json at every terminal pipeline exit.

    Also appends one line to AUTODEV_PIPELINE_ROOT/runs_index.jsonl (O_APPEND) so
    cross-run history survives projects being removed from the queue.
    Graceful: any failure is logged and swallowed — must never block a transition_state call.
    """
    try:
        run_end = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # --- Read run_manifest.json for project identity + run_start ---
        manifest = {}
        _manifest_path = os.path.join(PROJECT_ARTIFACTS_DIR, "run_manifest.json")
        if os.path.exists(_manifest_path):
            try:
                with open(_manifest_path, "r") as _f:
                    manifest = json.load(_f)
            except Exception:
                pass

        project_path = manifest.get("project_path", "")
        project_name = manifest.get("project_name", "")
        idea_id = manifest.get("idea_id")
        run_start = manifest.get("started_at", "")

        # Fallback: read project_path from STATE_FILE
        if not project_path and os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r") as _f:
                    _ps = json.load(_f)
                project_path = _ps.get("project_path", "")
                if not run_start:
                    run_start = _ps.get("last_action_timestamp", "")
            except Exception:
                pass

        # --- Compute duration ---
        total_duration_seconds = None
        if run_start:
            try:
                _start_dt = datetime.fromisoformat(run_start.replace("Z", "+00:00"))
                _end_dt = datetime.fromisoformat(run_end.replace("Z", "+00:00"))
                total_duration_seconds = int((_end_dt - _start_dt).total_seconds())
            except Exception:
                pass

        # --- Read and deduplicate metrics.jsonl (last row per phase wins) ---
        _summary_source = os.path.join(PROJECT_ARTIFACTS_DIR, "metrics.jsonl")
        _seen_phases = {}  # phase_id -> last row dict
        if os.path.exists(_summary_source):
            try:
                with open(_summary_source, "r") as _f:
                    for _line in _f:
                        _line = _line.strip()
                        if not _line:
                            continue
                        try:
                            _row = json.loads(_line)
                            _pid = _row.get("phase", "")
                            if _pid:
                                _seen_phases[_pid] = _row
                        except json.JSONDecodeError:
                            pass
            except Exception:
                pass

        deduped_rows = list(_seen_phases.values())

        # --- Aggregate counters ---
        executor_attempts_total = sum(r.get("executor_attempts", 0) for r in deduped_rows)
        escalations_total = sum(r.get("escalations", 0) for r in deduped_rows)
        blame_fires_total = sum(r.get("blame_fires", 0) for r in deduped_rows)

        blame_attributions = [
            {"phase": r["phase"], "blame": r["blame_verdict"]}
            for r in deduped_rows
            if r.get("blame_verdict")
        ]
        skills_injected = [
            {"phase": r["phase"], "discipline": r["skill_used"]}
            for r in deduped_rows
            if r.get("skill_used")
        ]

        # --- Token aggregation across all roles and phases ---
        _tok = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                "total_tokens": 0, "cost_total": 0.0}
        for _row in deduped_rows:
            for _role_key in ("planner_tokens", "executor_tokens", "reviewer_tokens"):
                _rt = _row.get(_role_key) or {}
                _tok["input"] += _rt.get("input", 0)
                _tok["output"] += _rt.get("output", 0)
                _tok["cache_read"] += _rt.get("cache_read", 0)
                _tok["cache_write"] += _rt.get("cache_write", 0)
                _tok["total_tokens"] += _rt.get("total_tokens", 0)
                _tok["cost_total"] += _rt.get("cost_total", 0.0)
        _tok["cost_total"] = round(_tok["cost_total"], 6)

        # --- phases array ---
        phases_list = [
            {
                "phase": r.get("phase"),
                "executor_attempts": r.get("executor_attempts", 0),
                "blame": r.get("blame_verdict"),
                "skill_used": r.get("skill_used"),
                "last_error_code": r.get("last_error_code"),
                "escalation_trigger_reason": r.get("escalation_trigger_reason"),
            }
            for r in deduped_rows
        ]

        phases_attempted = len(deduped_rows)
        phases_complete = phases_attempted if outcome == "PIPELINE_COMPLETE" else 0

        summary = {
            "schema_version": 1,
            "generated_at": run_end,
            "outcome": outcome,
            "outcome_detail": outcome_detail,
            "project_path": project_path,
            "project_name": project_name,
            "idea_id": idea_id,
            "run_start": run_start,
            "run_end": run_end,
            "total_duration_seconds": total_duration_seconds,
            "phases_attempted": phases_attempted,
            "phases_complete": phases_complete,
            "executor_attempts_total": executor_attempts_total,
            "escalations_total": escalations_total,
            "blame_fires_total": blame_fires_total,
            "skills_injected": skills_injected,
            "blame_attributions": blame_attributions,
            "token_usage": _tok,
            "phases": phases_list,
        }

        # --- Atomic write of run_summary.json ---
        os.makedirs(PROJECT_ARTIFACTS_DIR, exist_ok=True)
        _fd, _tmp = tempfile.mkstemp(dir=PROJECT_ARTIFACTS_DIR, prefix=".run_summary_")
        try:
            with os.fdopen(_fd, "w") as _f:
                json.dump(summary, _f)
            os.replace(_tmp, os.path.join(PROJECT_ARTIFACTS_DIR, "run_summary.json"))
        except Exception:
            if os.path.exists(_tmp):
                os.remove(_tmp)
            raise

        # --- Append to runs_index.jsonl at AUTODEV_PIPELINE_ROOT ---
        _index_path = os.path.join(AUTODEV_PIPELINE_ROOT, "runs_index.jsonl")
        _index_entry = json.dumps({
            "ts": run_end,
            "outcome": outcome,
            "project_path": project_path,
            "project_name": project_name,
            "run_start": run_start,
            "run_end": run_end,
        })
        try:
            os.makedirs(AUTODEV_PIPELINE_ROOT, exist_ok=True)
            with open(_index_path, "a") as _fi:
                _fi.write(_index_entry + "\n")
        except OSError as _e:
            print(f"[W2B] runs_index.jsonl append failed (non-fatal): {_e}")

        print(f"[W2B] run_summary.json written: outcome={outcome}, "
              f"phases={phases_attempted}, tokens={_tok['total_tokens']}")
    except Exception as _e:
        print(f"[W2B] run_summary write failed (non-fatal): {_e}")


class Orchestrator:
    def __init__(self):
        self.lock_fd = None
        self.state = {
            "current_phase": 0,
            "current_phase_raw_id": "",  # full phase-id string e.g. "CORE-2"; avoids int-suffix collisions
            "current_agent": "planner",
            "planner_retries": 0,
            "executor_retries": 0,
            "reviewer_retries": 0,
            "last_action": "initialized",
            "last_action_timestamp": datetime.now(timezone.utc).isoformat(),
            "pipeline_status": "RUNNING"
        }
        _validate_openclaw_root(OPENCLAW_ROOT)
        self.openclaw_config = self.load_config()
        self.skill_manager = SkillManager(OPENCLAW_ROOT)

    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            print(f"[ERROR] openclaw.json not found at {CONFIG_FILE}")
            sys.exit(1)
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
        except Exception as e:
            print(f"[ERROR] Failed to parse openclaw.json: {e}")
            sys.exit(1)
        # Fail fast: without a bearer token the pipeline hits AUTH_ERROR with no clear diagnostic.
        # OpenClaw stores the token at hooks.token; older docs used top-level hooks_token.
        hooks = config.get("hooks") or {}
        token = (config.get("hooks_token") or hooks.get("token") or "").strip()
        if not token:
            print(
                "[ERROR] openclaw.json has no webhook bearer token. "
                "Set hooks.token (OpenClaw layout) or top-level hooks_token. "
                "Without it the pipeline would hit AUTH_ERROR silently."
            )
            sys.exit(1)
        # Normalize top-level keys for callers/tests; webhook_client still uses a fixed URL unless updated.
        hooks_url = (config.get("hooks_url") or "").strip()
        if not hooks_url:
            try:
                port = int((config.get("gateway") or {}).get("port") or 18789)
            except (TypeError, ValueError):
                port = 18789
            hooks_url = f"http://127.0.0.1:{port}/hooks/agent"
        config["hooks_token"] = token
        config["hooks_url"] = hooks_url
        return config

    def acquire_lock(self):
        """Acquires an exclusive, non-blocking lock using fcntl.flock."""
        try:
            os.makedirs(AUTODEV_PIPELINE_ROOT, exist_ok=True)
            self.lock_fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o666)
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            
            # Write PID + timestamp as diagnostic metadata
            metadata = {
                "pid": os.getpid(),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            os.ftruncate(self.lock_fd, 0)
            os.write(self.lock_fd, json.dumps(metadata).encode('utf-8'))
            print("[INFO] Acquired pipeline lock.")
        except BlockingIOError:
            print("[ERROR] Another orchestrator instance is already running.")
            sys.exit(1)
        except Exception as e:
            print(f"[ERROR] Failed to acquire lock: {e}")
            sys.exit(1)

    def release_lock(self):
        """Releases the lock and closes the file descriptor."""
        if self.lock_fd is not None:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            os.close(self.lock_fd)
            self.lock_fd = None
            print("[INFO] Released pipeline lock.")

    def update_symlink(self, target_project_dir: str):
        """Atomically updates the shared workspace symlink.

        Two symlinks are kept in sync:
        1. SYMLINK_TARGET (.autodev/pipeline-project) — used by the orchestrator and
           gate scripts to locate project files.
        2. OPENCLAW_ROOT/pipeline-project (~/.openclaw/pipeline-project) — followed by
           agent workspace symlinks (workspace-{agent}/pipeline-project →
           ~/.openclaw/pipeline-project). Without this second update the agent reads
           the previous project's files even though the orchestrator targets the new one.
        """
        target_project_dir = os.path.abspath(os.path.expanduser(target_project_dir))
        if not os.path.exists(target_project_dir):
            print(f"[ERROR] Target project dir doesn't exist: {target_project_dir}")
            return False

        openclaw_symlink = os.path.join(OPENCLAW_ROOT, "pipeline-project")

        try:
            subprocess.run(["ln", "-sfn", target_project_dir, SYMLINK_TARGET], check=True)
            print(f"[INFO] Updated symlink {SYMLINK_TARGET} -> {target_project_dir}")
            # Keep the OpenClaw-side symlink in sync so agent workspaces resolve correctly.
            if SYMLINK_TARGET != openclaw_symlink:
                subprocess.run(["ln", "-sfn", target_project_dir, openclaw_symlink], check=True)
                print(f"[INFO] Updated symlink {openclaw_symlink} -> {target_project_dir}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Failed to update symlink: {e}")
            return False

    def read_state(self):
        """Reads pipeline_state.json if it exists.

        On parse failure the corrupt file is quarantined (renamed to
        pipeline_state.json.corrupt.<timestamp>) and the process exits with code 1.
        Continuing with in-memory defaults risks duplicate phase work or wrong agent
        routing on restart.  Operator must manually inspect the quarantined file.
        """
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    self.state = json.load(f)
                    print(f"[INFO] Loaded state: {self.state['pipeline_status']}")
            except Exception as e:
                corrupt_path = f"{STATE_FILE}.corrupt.{int(time.time())}"
                try:
                    os.rename(STATE_FILE, corrupt_path)
                    print(f"[ERROR] pipeline_state.json is corrupt; quarantined to {corrupt_path}: {e}")
                except OSError as rename_err:
                    print(f"[ERROR] Could not quarantine corrupt state file: {rename_err}")
                print("[FATAL] Halting — manual recovery required. Inspect the quarantined file.")
                sys.exit(1)
        else:
            print("[INFO] No existing state file found. Starting fresh.")
            self.write_state()

    def write_state(self):
        """Atomically writes pipeline_state.json."""
        self.state["last_action_timestamp"] = datetime.now(timezone.utc).isoformat()
        
        # Write to temp file then atomic rename
        os.makedirs(AUTODEV_PIPELINE_ROOT, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(dir=AUTODEV_PIPELINE_ROOT, prefix="pipeline_state_")
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(self.state, f, indent=2)
            os.replace(temp_path, STATE_FILE)
            print(f"[INFO] Atomically updated state: {self.state['pipeline_status']} - {self.state['last_action']}")
        except Exception as e:
            print(f"[ERROR] Failed to write state: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    def transition_state(self, new_status, action_description):
        """Helper to cleanly transition and write state before action."""
        if new_status not in VALID_STATES:
            print(f"[ERROR] Invalid state transition requested: {new_status}")
            return
            
        self.state["pipeline_status"] = new_status
        self.state["last_action"] = action_description
        if new_status != "WAITING_FOR_SENTINEL":
            self.state.pop("sentinel_wait_started_at", None)
        self.write_state()

    def _phase_resolver_indicates_pipeline_complete(self) -> bool:
        """True iff phase_resolver reports no pending phases for the current symlink project."""
        gate_script = os.path.join(
            AUTODEV_REPO_PATH, "autodev", "pipeline", "gate_scripts", "phase_resolver.py"
        )
        if not os.path.isfile(gate_script):
            return False
        try:
            result = subprocess.run(
                [sys.executable, gate_script],
                capture_output=True,
                text=True,
                timeout=120,
            )
            output = (result.stdout or "").strip()
            return result.returncode == 0 and "PIPELINE_COMPLETE" in output
        except Exception as exc:
            print(f"[WARN] phase_resolver completion check failed: {exc}")
            return False

    # ------------------------------------------------------------------
    # Queue helpers
    # ------------------------------------------------------------------

    def _read_queue(self):
        """Read pipeline_queue.json; returns empty structure if absent.

        SINGLE-WRITER ASSUMPTION: This file is written by two parties —
        the UI server (human-initiated moments: add, reorder, parent, remove) and
        the orchestrator (state transitions: ACTIVE, COMPLETED, BLOCKED, SKIPPED).
        These writes are NOT protected by a file lock; concurrent writes are
        considered safe enough at this risk level because the UI and orchestrator
        operate in alternating windows (UI writes while idle; orchestrator writes
        while running).  If this assumption is ever violated, add an advisory flock
        or a version/ETag field before relaxing the single-writer model.

        If the file exists but is corrupt (invalid JSON or unreadable), it is
        quarantined by renaming to pipeline_queue.json.corrupt.<timestamp> and a
        RuntimeError is raised.  Callers must NOT silently fall through to
        _write_queue — doing so would overwrite the queue file with an empty
        structure, destroying all queue data.
        """
        if not os.path.exists(QUEUE_FILE):
            return {"queue": [], "queue_mode": "auto", "last_updated": ""}
        try:
            with open(QUEUE_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            corrupt_path = f"{QUEUE_FILE}.corrupt.{int(time.time())}"
            try:
                os.rename(QUEUE_FILE, corrupt_path)
                print(f"[QUEUE] Quarantined corrupt queue file to {corrupt_path}: {e}")
            except OSError as rename_err:
                print(f"[QUEUE] Could not quarantine corrupt queue file: {rename_err}")
            raise RuntimeError(
                f"[QUEUE] pipeline_queue.json is corrupt and has been quarantined to "
                f"{corrupt_path}. Manual recovery required."
            ) from e

    def _write_queue(self, data):
        """Atomically write pipeline_queue.json (mkstemp + os.replace).

        See _read_queue for the single-writer assumption that makes this safe
        without an explicit file lock.
        """
        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        os.makedirs(AUTODEV_PIPELINE_ROOT, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=AUTODEV_PIPELINE_ROOT, prefix="queue_")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, QUEUE_FILE)
        except Exception as e:
            print(f"[QUEUE] Failed to write queue file: {e}")
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def _queue_preflight(self, project_path):
        """Lightweight queue preflight: dir exists, is git repo, has roadmap*.md.

        LIGHTWEIGHT PREFLIGHT ONLY — this check is intentionally narrower than the
        server-side `_run_preflight_checks` (which also validates symlink integrity,
        .gitignore, agent workspace files, and OpenClaw config).  A project that
        passes this check may still fail mid-pipeline if the full server preconditions
        are not satisfied.  The server runs `_run_preflight_checks` at queue-add time
        and at trigger-next time; this method runs only when the orchestrator
        auto-advances between queue entries without a UI trigger.

        If you add new checks here, keep them lightweight (no network, no subprocess)
        and ensure they match a subset of `_run_preflight_checks` to avoid divergence.
        """
        if not os.path.isdir(project_path):
            return False, "directory does not exist"
        if not os.path.exists(os.path.join(project_path, ".git")):
            return False, "not a git repository"
        try:
            entries = os.listdir(project_path)
        except OSError as e:
            return False, f"path_unreadable: {e}"
        roadmap = next(
            (n for n in entries
             if n.lower().startswith("roadmap") and n.endswith(".md")),
            None
        )
        if not roadmap:
            return False, "no roadmap*.md found"
        return True, "ok"

    def _find_active_queue_entry(self, queue_data):
        """Find the ACTIVE queue entry matching the current project.

        Primary: match via SYMLINK_TARGET realpath.
        Fallback: match via pipeline_state.json["project_path"].
        Returns (index, entry) or (None, None).
        """
        proj_path = None
        if os.path.exists(SYMLINK_TARGET):
            try:
                proj_path = os.path.realpath(SYMLINK_TARGET)
            except OSError:
                pass
        if not proj_path and self.state.get("project_path"):
            try:
                proj_path = os.path.realpath(self.state["project_path"])
            except OSError:
                pass
        if not proj_path:
            return None, None
        for i, entry in enumerate(queue_data["queue"]):
            if entry.get("state") == "ACTIVE":
                try:
                    if os.path.realpath(entry["project_path"]) == proj_path:
                        return i, entry
                except OSError:
                    pass
        return None, None

    def _get_all_descendants(self, entries, entry_id):
        """Return set of all descendant IDs (recursive). Does not include entry_id itself."""
        children = {e["id"] for e in entries if e.get("parent_id") == entry_id}
        result = set(children)
        for cid in list(children):
            result |= self._get_all_descendants(entries, cid)
        return result

    def _move_group_atomically(self, entries, parent_id, new_pos):
        """Move parent + all descendants as a unit to new_pos (1-based position for parent)."""
        desc = self._get_all_descendants(entries, parent_id)
        group_ids = {parent_id} | desc
        sorted_all = sorted(entries, key=lambda e: e["position"])
        group_block = [e for e in sorted_all if e["id"] in group_ids]
        non_group = [e for e in sorted_all if e["id"] not in group_ids]
        insert_idx = max(0, min(new_pos - 1, len(non_group)))
        final = non_group[:insert_idx] + group_block + non_group[insert_idx:]
        for i, e in enumerate(final, 1):
            e["position"] = i
        entries[:] = final
        return entries

    def _select_next_queue_project(self, halt_if_no_eligible: bool = True):
        """Walk queue, find next eligible project, run preflight, start it.

        Returns True if a project was started, False if no eligible entry was found.

        When *halt_if_no_eligible* is True (default), also transitions to QUEUE_HALTED with a
        reason — used when the queue still has work but nothing can run.

        When False, returns False without changing pipeline status (used after
        PIPELINE_COMPLETE: queue row is COMPLETED but there is no next project — that is
        success, not a halt).
        """
        queue_data = self._read_queue()
        entries = queue_data["queue"]
        entries.sort(key=lambda e: e["position"])
        now = datetime.now(timezone.utc).isoformat()

        # Build parent state lookup
        state_by_id = {e["id"]: e["state"] for e in entries}

        visited_ids = set()  # prevent infinite loop if all entries keep failing
        i = 0
        while i < len(entries):
            entry = entries[i]
            if entry["id"] in visited_ids:
                i += 1
                continue
            visited_ids.add(entry["id"])

            if entry["state"] not in ("READY", "SKIPPED_PENDING"):
                i += 1
                continue

            # Dependency: skip until parent COMPLETED; only use DEPENDENCY_HOLD when parent blocks.
            if entry.get("parent_id"):
                parent_state = state_by_id.get(entry["parent_id"])
                if parent_state != "COMPLETED":
                    if parent_blocks_child(parent_state):
                        entry["state"] = "DEPENDENCY_HOLD"
                        self._write_queue(queue_data)
                    i += 1
                    continue

            # Run lightweight preflight
            ok, reason = self._queue_preflight(entry["project_path"])
            if not ok:
                print(f"[QUEUE] Preflight failed for '{entry['name']}': {reason} — skip-and-requeue")
                # Cascade SKIPPED_PENDING to all descendants before moving
                desc_ids = self._get_all_descendants(entries, entry["id"])
                for e in entries:
                    if e["id"] in desc_ids and e["state"] not in ("ACTIVE", "COMPLETED"):
                        e["state"] = "SKIPPED_PENDING"
                        e["skip_count"] = e.get("skip_count", 0) + 1
                        visited_ids.add(e["id"])  # prevent re-processing descendants
                entry["state"] = "SKIPPED_PENDING"
                entry["skip_count"] = entry.get("skip_count", 0) + 1
                entry["skip_reason"] = reason
                # Skip-and-requeue: move entire group past next independent entry
                group_size = 1 + len(desc_ids)
                new_pos = min(entry["position"] + group_size, len(entries))
                self._move_group_atomically(entries, entry["id"], new_pos)
                self._write_queue(queue_data)
                # Do NOT increment i — entry at this position shifted; visited_ids prevents re-trying
                continue

            # Pass: update symlink FIRST; only commit ACTIVE + write_state on success.
            # Committing ACTIVE before the symlink is updated leaves queue + state claiming
            # a new project is running while agents still read the old symlink target.
            print(f"[QUEUE] Starting project '{entry['name']}' at {entry['project_path']}")
            project_path = os.path.realpath(os.path.expanduser(entry["project_path"]))
            if not self.update_symlink(project_path):
                print(f"[QUEUE] Symlink update failed for '{entry['name']}' — leaving entry READY.")
                return False

            entry["state"] = "ACTIVE"
            entry["started_at"] = now
            self._write_queue(queue_data)
            _write_run_manifest(entry)  # W2-A

            self.state = {
                "current_phase": 0,
                "current_phase_raw_id": "",
                "current_agent": "planner",
                "planner_retries": 0,
                "executor_retries": 0,
                "reviewer_retries": 0,
                "last_action": f"queue auto-advance to {entry['name']}",
                "last_action_timestamp": now,
                "pipeline_status": "RUNNING",
                "project_path": project_path,
            }
            self.write_state()
            self._apply_pending_escalation_command(project_path)
            return True

        # No eligible project found — determine halted reason
        non_terminal = [e["state"] for e in entries if e["state"] not in ("COMPLETED", "FAILED")]
        _parked_states = frozenset({"BLOCKED", "ESCALATION"})
        if not non_terminal:
            reason = "all_completed"
        elif all(s in _parked_states for s in non_terminal):
            reason = "all_blocked"
        elif all(s == "DEPENDENCY_HOLD" for s in non_terminal):
            reason = "all_dependency_hold"
        else:
            reason = "mixed"
        print(f"[QUEUE] Queue exhausted — halting with reason: {reason}")
        if halt_if_no_eligible:
            self.state["queue_halted_reason"] = reason
            self.transition_state("QUEUE_HALTED", f"Queue halted: {reason}")
        else:
            # Caller owns final status (e.g. PIPELINE_COMPLETE). Clear stale halt metadata.
            self.state.pop("queue_halted_reason", None)
        return False

    def _queue_promote_children_after_parent_completed(self, parent_entry_id):
        """Set DEPENDENCY_HOLD children to READY when parent reaches COMPLETED."""
        try:
            queue_data = self._read_queue()
            changed = False
            for e in queue_data["queue"]:
                if e.get("parent_id") == parent_entry_id and e.get("state") == "DEPENDENCY_HOLD":
                    e["state"] = "READY"
                    changed = True
            if changed:
                self._write_queue(queue_data)
        except Exception as e:
            print(f"[QUEUE] Failed to promote children after parent completed: {e}")

    def _queue_update_active_entry(self, new_state, extra_fields=None):
        """Find the ACTIVE queue entry for this project and update its state."""
        try:
            queue_data = self._read_queue()
            if not queue_data["queue"]:
                return
            idx, entry = self._find_active_queue_entry(queue_data)
            if idx is None:
                return
            parent_id_completed = entry.get("id") if new_state == "COMPLETED" else None
            queue_data["queue"][idx]["state"] = new_state
            if extra_fields:
                queue_data["queue"][idx].update(extra_fields)
            self._write_queue(queue_data)
            if parent_id_completed:
                self._queue_promote_children_after_parent_completed(parent_id_completed)
        except Exception as e:
            print(f"[QUEUE] Failed to update active entry to {new_state}: {e}")

    def _queue_park_active_entry(self, queue_state, parked_reason, extra_fields=None):
        """Park the ACTIVE queue row (escalation or roadmap blocked) with metadata."""
        try:
            queue_data = self._read_queue()
            if not queue_data.get("queue"):
                return
            idx, _entry = self._find_active_queue_entry(queue_data)
            if idx is None:
                return
            now = datetime.now(timezone.utc).isoformat()
            row = queue_data["queue"][idx]
            row["state"] = queue_state
            row["parked_at"] = now
            row["parked_reason"] = parked_reason
            row["parked_pipeline_status"] = self.state.get("pipeline_status")
            if extra_fields:
                row.update(extra_fields)
            self._write_queue(queue_data)
        except Exception as e:
            print(f"[QUEUE] Failed to park active entry ({queue_state}): {e}")

    def _queue_restore_parked_entry_to_active(self):
        """Restore an ESCALATION or BLOCKED queue row back to ACTIVE for this project.

        Called at the start of every resume command (RETRY, RESET_PHASE, RESET_EXECUTION,
        RESET_REVIEWER, SKIP, PROCEED) so that downstream _queue_update_active_entry calls
        can find the row after _queue_park_active_entry set it to a non-ACTIVE state.
        """
        try:
            queue_data = self._read_queue()
            if not queue_data.get("queue"):
                return
            # Resolve the current project path.
            proj_path = None
            if os.path.exists(SYMLINK_TARGET):
                try:
                    proj_path = os.path.realpath(SYMLINK_TARGET)
                except OSError:
                    pass
            if not proj_path and self.state.get("project_path"):
                try:
                    proj_path = os.path.realpath(self.state["project_path"])
                except OSError:
                    pass
            if not proj_path:
                return
            for i, entry in enumerate(queue_data["queue"]):
                if entry.get("state") not in ("ESCALATION", "BLOCKED"):
                    continue
                try:
                    if os.path.realpath(entry["project_path"]) != proj_path:
                        continue
                except OSError:
                    continue
                entry["state"] = "ACTIVE"
                entry.pop("parked_at", None)
                entry.pop("parked_reason", None)
                entry.pop("parked_pipeline_status", None)
                self._write_queue(queue_data)
                return
        except Exception as e:
            print(f"[QUEUE] Failed to restore parked entry to ACTIVE: {e}")

    def _queue_after_park_maybe_advance(self):
        """After parking, auto-select the next project if queue_mode is auto."""
        queue_data = self._read_queue()
        if not queue_data.get("queue") or queue_data.get("queue_mode", "auto") != "auto":
            return False
        return self._select_next_queue_project()

    def _escalation_poll_roots(self):
        """Project dirs that may contain escalation_output (active symlink + parked ESCALATION rows)."""
        roots = []
        seen = set()
        if os.path.exists(SYMLINK_TARGET):
            try:
                r0 = os.path.realpath(SYMLINK_TARGET)
                if os.path.isdir(r0):
                    seen.add(r0)
                    roots.append(r0)
            except OSError:
                pass
        try:
            for e in self._read_queue().get("queue", []):
                if e.get("state") != "ESCALATION":
                    continue
                pp = e.get("project_path")
                if not pp:
                    continue
                try:
                    rp = os.path.realpath(os.path.expanduser(pp))
                except OSError:
                    continue
                if rp and os.path.isdir(rp) and rp not in seen:
                    seen.add(rp)
                    roots.append(rp)
        except Exception:
            pass
        return roots

    def _poll_escalation_output_json_path(self, timeout_seconds=10, interval=0.5):
        """Wait for escalation_output.done under any poll root; return path to escalation_output.json."""
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            for root in self._escalation_poll_roots():
                _esc = os.path.join(root, ".autodev", "pipeline")
                done_p = os.path.join(_esc, "escalation_output.done")
                json_p = os.path.join(_esc, "escalation_output.json")
                if os.path.exists(done_p):
                    return json_p
            time.sleep(interval)
        return None

    def _apply_pending_escalation_command(self, project_path):
        """If UI deferred a command while another project was active, apply it now."""
        root = os.path.realpath(os.path.expanduser(project_path))
        art = os.path.join(root, ".autodev", "pipeline")
        try:
            os.makedirs(art, exist_ok=True)
        except OSError:
            pass
        pending_json = os.path.join(art, "pending_escalation_command.json")
        if not os.path.exists(pending_json):
            return
        try:
            with open(pending_json, "r") as f:
                data = json.load(f)
            command = str(data.get("command", "STOP")).upper()
        except Exception:
            command = "STOP"
        try:
            os.remove(pending_json)
        except OSError:
            pass
        pending_done = os.path.join(art, "pending_escalation_command.done")
        try:
            if os.path.exists(pending_done):
                os.remove(pending_done)
        except OSError:
            pass
        esc_json = os.path.join(art, "escalation_output.json")
        esc_done = os.path.join(art, "escalation_output.done")
        payload = {
            "command": command,
            "source": "deferred",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            fd, tmp = tempfile.mkstemp(dir=art, prefix="esc_out_")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(payload, f)
                os.replace(tmp, esc_json)
            except Exception:
                if os.path.exists(tmp):
                    os.remove(tmp)
                raise
            with open(esc_done, "w") as f:
                f.write("")
        except OSError as e:
            print(f"[QUEUE] Failed to apply deferred escalation command: {e}")
            return
        self.state["pipeline_status"] = "WAITING_FOR_HUMAN"
        self.state["current_agent"] = "escalation"
        self.write_state()

    def _should_invoke_escalation_agent(self) -> bool:
        """True when escalation webhook should be invoked for current state.

        QUEUE_HALTED can legitimately coexist with an escalation wait context
        (parked queue row, awaiting human command). Treat it like WAITING_FOR_HUMAN
        to avoid repeatedly re-invoking escalation and flipping status back/forth.
        """
        return self.state.get("pipeline_status") not in ("WAITING_FOR_HUMAN", "QUEUE_HALTED")

    def _read_escalation_summary(self):
        """Read escalation agent's human-facing summary from project directory.

        Returns the summary string if valid, None otherwise.
        Never raises — all failures are logged and return None.
        """
        try:
            summary_path = os.path.join(PROJECT_ARTIFACTS_DIR, "escalation_summary.json")
            if not os.path.isfile(summary_path):
                return None
            with open(summary_path, "r") as f:
                data = json.load(f)
            summary = data.get("summary", "")
            if not isinstance(summary, str) or not summary.strip():
                return None
            return summary.strip()[:200]  # hard cap
        except Exception as e:
            print(f"[ESCALATION] Could not read escalation_summary.json: {e}")
            return None

    def _check_stop_requested(self) -> bool:
        """Check for the stop sentinel file written by the UI server.

        Consumes the sentinel if found (removes the file) so repeated
        loop iterations do not re-trigger the stop.

        Returns:
            True if the stop sentinel was present and consumed, False otherwise.
        """
        stop_file = os.path.join(PROJECT_ARTIFACTS_DIR, "pipeline_stop_requested")
        if os.path.exists(stop_file):
            try:
                os.remove(stop_file)
            except OSError:
                pass
            return True
        return False

    def increment_planner_retries(self):
        phase_state = {}
        if os.path.exists(PHASE_STATE_FILE):
            try:
                with open(PHASE_STATE_FILE, 'r') as f:
                    phase_state = json.load(f)
            except Exception:
                pass
        else:
            phase_state = {"planner_retries": 0, "executor_retries": 0, "reviewer_retries": 0, "reviewer_rejected": False, "escalation_resets": 0}

        phase_state["planner_retries"] = phase_state.get("planner_retries", 0) + 1
        
        target_dir = _atomic_temp_dir_for_project_writes()
        fd, temp_path = tempfile.mkstemp(dir=target_dir, prefix="phase_state_")
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(phase_state, f, indent=2)
            os.replace(temp_path, PHASE_STATE_FILE)
        except Exception as e:
            print(f"[ERROR] Failed to write phase_state: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
        
        self.state["planner_retries"] = phase_state["planner_retries"]
        self.transition_state("RUNNING", f"Incremented planner retries to {phase_state['planner_retries']}")
        return phase_state["planner_retries"]
        
    def run_planner_output_gate(self):
        gate_script = os.path.join(AUTODEV_REPO_PATH, "autodev", "pipeline", "gate_scripts", "planner_gate.py")
        json_path = os.path.join(PROJECT_ARTIFACTS_DIR, "planner_output.json")
        try:
            result = subprocess.run(
                [sys.executable, gate_script, json_path],
                capture_output=True,
                text=True,
                check=True,
                timeout=GATE_SUBPROCESS_TIMEOUT,
            )
            output = result.stdout.strip()
            return output == "PASS"
        except subprocess.TimeoutExpired as e:
            print(f"[ERROR] Planner gate subprocess timed out after {GATE_SUBPROCESS_TIMEOUT}s: {e}")
            return False
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Gate script failed: {e}")
            return False

    def check_traffic_cop_health(self):
        """Probe the traffic cop (llama-server) /health endpoint.

        Returns True when the server responds 200, False on any network error or
        non-200 status.  Returning False is intentional — callers treat it as
        "unhealthy, skip GPU-dependent work" which is the correct safe default.
        The exception is swallowed deliberately; a debug log is emitted so operators
        can see probe failures in verbose mode without cluttering normal output.
        """
        try:
            response = requests.get(f"{_LLAMA_ORIGIN}/health", timeout=5)
            return response.status_code == 200
        except requests.exceptions.RequestException as e:
            print(f"[DEBUG] check_traffic_cop_health probe failed: {e}")
            return False

    def wait_for_model_stable(self, timeout: int = 300, poll_interval: int = 5) -> bool:
        """Poll /v1/models until the GPU is in a stable state (no model mid-transition).

        Stable = all reported models have status 'loaded' or 'unloaded' (none 'loading'/'unloading').
        Returns True when stable, False if timeout expires (caller proceeds anyway).
        """
        import urllib.parse
        base_url = (
            self.openclaw_config
            .get("models", {})
            .get("providers", {})
            .get("llama-local", {})
            .get("baseUrl", f"{_LLAMA_ORIGIN}/v1")
        )
        parsed = urllib.parse.urlparse(base_url)
        models_url = f"{parsed.scheme}://{parsed.netloc}/v1/models"

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                resp = requests.get(models_url, timeout=5)
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    transitioning = [
                        m for m in data
                        if m.get("status", {}).get("value") not in ("loaded", "unloaded")
                    ]
                    if not transitioning:
                        print(f"[INFO] Traffic cop model state stable ({len(data)} model(s) reported). Proceeding to reviewer.")
                        return True
                    names = [m.get("id", "?") for m in transitioning]
                    print(f"[INFO] Waiting for model swap — still transitioning: {names}")
                else:
                    print(f"[WARN] /v1/models returned {resp.status_code}, retrying...")
            except requests.exceptions.RequestException as e:
                print(f"[WARN] /v1/models unreachable ({e}), retrying...")
            time.sleep(poll_interval)

        # Intentional: proceed on timeout rather than blocking indefinitely.
        # Returning False here is a soft signal — the caller (reviewer invocation path)
        # logs the warning and continues.  If stricter "block until stable" behaviour is
        # ever required, return a distinct sentinel (e.g. None / raise) and update the
        # caller to decide; do not change this return value without updating callers.
        print(f"[WARN] wait_for_model_stable timed out after {timeout}s — proceeding anyway.")
        return False

    # -----------------------------------------------------------------------
    # FIND-EXECUTOR-COMPLETION-DETECTION: classify executor terminal state.
    # Returns one of: "executor_succeeded", "executor_crashed", "executor_preempted".
    # -----------------------------------------------------------------------
    def classify_executor_outcome(self, sentinel_found: bool, output_path: str) -> str:
        """Classify the executor's terminal state after polling ends.

        sentinel_found=True                              → executor_succeeded
        sentinel_found=False, output absent              → executor_crashed
        sentinel_found=False, output present (any size)  → executor_preempted
          (executor was killed between writing JSON and writing .done)
        """
        if sentinel_found:
            return "executor_succeeded"
        if os.path.exists(output_path):
            return "executor_preempted"
        return "executor_crashed"

    # -----------------------------------------------------------------------
    # FIND-PLANNER-PRESERVE: check if valid planner output already exists on disk.
    # Allows restart path to skip re-invocation when output is intact.
    # -----------------------------------------------------------------------
    def planner_output_is_valid(self) -> bool:
        """Return True when planner_output.done exists AND planner_output.json passes the gate.

        Uses the planner gate script as the single source of truth for validity so the
        check is consistent with the normal execution path.
        """
        done_path = os.path.join(PROJECT_ARTIFACTS_DIR, "planner_output.done")
        json_path = os.path.join(PROJECT_ARTIFACTS_DIR, "planner_output.json")
        if not os.path.exists(done_path) or not os.path.exists(json_path):
            return False
        # Import and call the gate function directly so workspace patches in tests take effect.
        # Subprocess-based call would inherit the real OPENCLAW_ROOT and ignore test mocks.
        try:
            gate_dir = os.path.join(AUTODEV_REPO_PATH, "autodev", "pipeline", "gate_scripts")
            if gate_dir not in sys.path:
                sys.path.insert(0, gate_dir)
            import planner_gate as _pg
            return _pg.evaluate_planner(json_path) == "PASS"
        except Exception:
            return False

    # -----------------------------------------------------------------------
    # FIND-DONE-FILE: check if executor already succeeded in a prior run.
    # -----------------------------------------------------------------------
    @staticmethod
    def executor_output_already_succeeded(phase_state: dict) -> bool:
        """Return True when phase_state records a prior executor success.

        Used by the restart path: if current_agent=='executor' and this returns True,
        the orchestrator should not re-invoke the executor but instead run the gate and
        advance to reviewer.
        """
        return phase_state.get("executor_succeeded") is True

    def reset_working_tree(self):
        try:
            subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=SYMLINK_TARGET, check=True)
            subprocess.run(["git", "clean", "-fd"], cwd=SYMLINK_TARGET, check=True)
            print("[INFO] Working tree reset.")
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Failed to reset working tree: {e}")

    def read_phase_state(self):
        """Read phase_state.json, return dict (empty if file absent).

        On parse failure the corrupt file is quarantined (renamed to
        phase_state.json.corrupt.<timestamp>) and a RuntimeError is raised.
        Silently returning {} on corruption would drop planner_retries /
        executor_retries / blame attribution fields, causing misrouted retries.
        """
        if os.path.exists(PHASE_STATE_FILE):
            try:
                with open(PHASE_STATE_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                corrupt_path = f"{PHASE_STATE_FILE}.corrupt.{int(time.time())}"
                try:
                    os.rename(PHASE_STATE_FILE, corrupt_path)
                    print(f"[ERROR] phase_state.json is corrupt; quarantined to {corrupt_path}: {e}")
                except OSError as rename_err:
                    print(f"[ERROR] Could not quarantine corrupt phase_state.json: {rename_err}")
                raise RuntimeError(
                    f"[FATAL] phase_state.json is corrupt and has been quarantined to "
                    f"{corrupt_path}. Manual recovery required."
                ) from e
        return {}

    def write_phase_state_atomic(self, phase_state):
        """Atomically write phase_state.json using temp-file rename."""
        try:
            os.makedirs(PROJECT_ARTIFACTS_DIR, exist_ok=True)
        except OSError:
            pass
        target_dir = _atomic_temp_dir_for_project_writes()
        fd, temp_path = tempfile.mkstemp(dir=target_dir, prefix="phase_state_")
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(phase_state, f, indent=2)
            os.replace(temp_path, PHASE_STATE_FILE)
        except Exception as e:
            print(f"[ERROR] Failed to write phase_state: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def _record_injected_skill(self, agent_role: str) -> None:
        """Write skill_injected and skill_agent to phase_state.json after inject_skill().

        Reads the workspace skills directory to determine what was actually placed by
        skill_manager.inject_skill() — the directory is clean-then-write, so any
        subdirectory present after the call is the current skill (or empty = no skill).
        Writes skill_injected: None if injection produced no skill (disabled, not mapped,
        or source file missing).  Non-blocking: errors are logged and swallowed.
        """
        skills_dir = os.path.join(OPENCLAW_ROOT, f"workspace-{agent_role}", "skills")
        discipline = None
        try:
            entries = [
                e for e in os.listdir(skills_dir)
                if os.path.isdir(os.path.join(skills_dir, e))
            ]
            if entries:
                skill_name = entries[0]
                suffix = f"-{agent_role}"
                discipline = skill_name[:-len(suffix)] if skill_name.endswith(suffix) else skill_name
        except OSError as e:
            print(f"[SKILL] [WARN] Could not read workspace skills dir for {agent_role}: {e}")
        phase_state = self.read_phase_state()
        phase_state["skill_injected"] = discipline
        phase_state["skill_agent"] = agent_role
        self.write_phase_state_atomic(phase_state)

    def _get_agent_model(self, agent_id: str) -> str | None:
        """Return the primary model string for an agent from openclaw.json, or None."""
        for agent in self.openclaw_config.get("agents", {}).get("list", []):
            if agent.get("id") == agent_id:
                model = agent.get("model")
                if isinstance(model, dict):
                    return model.get("primary")
                if isinstance(model, str):
                    return model
        return None

    def send_signal_notification(self, message):
        """Send a raw Signal notification via the OpenClaw gateway."""
        token = self.openclaw_config.get("hooks", {}).get("token", "")
        payload = {"channel": "signal", "message": message}
        try:
            headers = {"Authorization": f"Bearer {token}"}
            r = requests.post("http://localhost:18789/hooks/agent", json=payload, headers=headers)
            r.raise_for_status()
            print(f"[INFO] Signal notification sent: {message[:80]}")
        except Exception as e:
            print(f"[ERROR] Failed to send signal notification: {e}")

    def reset_phase(self):
        """Full phase-level reset. Triggered by RESET_PHASE resume command (escalation-only).

        Sequence:
          1. git reset --hard <phase_base_commit> (pre-phase commit stored at branch creation)
          2. git checkout main (base branch)
          3. git branch -D phase/N (delete — recreated when planner re-runs)
          4. Clear all 6 output pairs + phase_state.json from workspace
          5. Re-initialize phase_state.json: agent counters → 0, escalation_resets PRESERVED
          6. Set pipeline_state: current_agent=planner, RUNNING

        IMPORTANT: escalation_resets is NOT zeroed here. It is only zeroed when the roadmap
        genuinely advances to a new phase. Zeroing it inside reset_phase() would allow the
        escalation agent to circumvent the cap by repeatedly triggering phase resets.
        """
        phase = self.state.get("current_phase", 0)
        raw_id = self.state.get("current_phase_raw_id", "")
        branch = f"phase/{raw_id}" if raw_id else f"phase/{phase}"
        phase_base = self.state.get("phase_base_commit", "")

        # Preserve escalation_resets before clearing phase state
        current_phase_state = self.read_phase_state()
        preserved_escalation_resets = current_phase_state.get("escalation_resets", 0)

        try:
            configured_base_branch = self.openclaw_config.get("pipeline", {}).get("base_branch", "").strip()
            base_branch = configured_base_branch if configured_base_branch else _detect_base_branch(SYMLINK_TARGET)
            if phase_base:
                subprocess.run(["git", "reset", "--hard", phase_base], cwd=SYMLINK_TARGET, check=True)
            subprocess.run(["git", "checkout", base_branch], cwd=SYMLINK_TARGET, check=True)
            subprocess.run(["git", "branch", "-D", branch], cwd=SYMLINK_TARGET, check=False)
            print(f"[INFO] reset_phase: reset to {phase_base or 'HEAD'}, on {base_branch}, deleted {branch}.")
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] reset_phase git operations failed: {e}")

        # Clear all six output pairs and phase_state.json
        for fname in [
            "planner_output.json", "planner_output.done",
            "executor_output.json", "executor_output.done",
            "reviewer_output.json", "reviewer_output.done",
            "phase_state.json", "failure_context.json",
            "executor_gate_detail.json",
        ]:
            try:
                os.remove(os.path.join(PROJECT_ARTIFACTS_DIR, fname))
            except FileNotFoundError:
                pass

        # Re-initialize phase_state: agent counters → 0, escalation_resets preserved (cap intact).
        # RR-4 (Phase 2): reviewer_infra_retries and reviewer_infra_recovery_attempts are
        # zeroed on phase reset — they are per-phase attempt budgets, not global caps.
        # RR-2 (Phase 4): planner_output_preserved cleared — new phase, no preserved output.
        new_phase_state = {
            "planner_retries": 0,
            "executor_retries": 0,
            "reviewer_retries": 0,
            "reviewer_rejected": False,
            "reviewer_infra_retries": 0,
            "reviewer_infra_recovery_attempts": 0,
            "planner_output_preserved": False,
            "escalation_resets": preserved_escalation_resets,
        }
        self.write_phase_state_atomic(new_phase_state)

        self.state["current_agent"] = "planner"
        self.state["planner_retries"] = 0
        self.state["executor_retries"] = 0
        self.state["reviewer_retries"] = 0
        # Clear the pipeline_state flag so the main loop doesn't skip planner re-invocation
        # due to stale preserved output that was just deleted above.
        self.state["planner_output_preserved"] = False

        # §5.3 fix: re-run roadmap_parser to refresh current_phase.json.
        # git checkout main (above) restores the tracked version of current_phase.json,
        # which may be stale from a previously completed phase.  Without this call the
        # planner reads the wrong phase context on the next invocation.
        import glob as _glob
        gate_script = os.path.join(AUTODEV_REPO_PATH, "autodev", "pipeline", "gate_scripts", "phase_resolver.py")
        _roadmap_candidates = _glob.glob(os.path.join(SYMLINK_TARGET, "*[Rr]oadmap*.md"))
        if _roadmap_candidates:
            try:
                subprocess.run(
                    [sys.executable, gate_script, _roadmap_candidates[0]],
                    cwd=OPENCLAW_ROOT, check=True
                )
                print("[INFO] reset_phase: roadmap_parser re-run, current_phase.json refreshed.")
            except Exception as _rp_err:
                print(f"[WARN] reset_phase: roadmap_parser re-run failed: {_rp_err}. current_phase.json may be stale.")
        else:
            print("[WARN] reset_phase: no roadmap file found, current_phase.json not refreshed.")

        self.transition_state("RUNNING", f"RESET_PHASE: restarting phase {raw_id or phase} from planner")

    def reset_execution(self, caller: str):
        """Partial execution-level reset. Preserves planner output. Clears executor + reviewer outputs.

        caller='auto'       — from automatic executor retry path. Increments executor_retries.
        caller='escalation' — from RESET_EXECUTION resume command. Increments escalation_resets.
        Never increments both counters.

        After this returns, the main loop (current_agent='executor', RUNNING) re-invokes the executor.
        """
        phase = self.state.get("current_phase", 0)
        raw_id = self.state.get("current_phase_raw_id", "")
        branch = f"phase/{raw_id}" if raw_id else f"phase/{phase}"

        try:
            subprocess.run(["git", "checkout", branch], cwd=SYMLINK_TARGET, check=True)
            subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=SYMLINK_TARGET, check=True)
            print(f"[INFO] reset_execution({caller}): working tree reset on {branch}.")
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] reset_execution git operations failed: {e}")

        # §5.3 fix (reset_execution path): git reset --hard HEAD restores the committed
        # version of current_phase.json, which may be stale from a prior completed phase.
        # Re-run roadmap_parser to refresh it before the executor retries.
        import glob as _re_glob
        _re_gate = os.path.join(AUTODEV_REPO_PATH, "autodev", "pipeline", "gate_scripts", "phase_resolver.py")
        _re_roadmap = _re_glob.glob(os.path.join(SYMLINK_TARGET, "*[Rr]oadmap*.md"))
        if _re_roadmap:
            try:
                subprocess.run(
                    [sys.executable, _re_gate, _re_roadmap[0]],
                    cwd=OPENCLAW_ROOT, check=True
                )
                print(f"[INFO] reset_execution({caller}): roadmap_parser re-run, current_phase.json refreshed.")
            except Exception as _re_err:
                print(f"[WARN] reset_execution({caller}): roadmap_parser re-run failed: {_re_err}. current_phase.json may be stale.")
        else:
            print(f"[WARN] reset_execution({caller}): no roadmap file found, current_phase.json not refreshed.")

        # Clear executor and reviewer outputs. Planner output is preserved.
        for fname in [
            "executor_output.json", "executor_output.done",
            "reviewer_output.json", "reviewer_output.done",
            "failure_context.json",
            "executor_gate_detail.json",
        ]:
            try:
                os.remove(os.path.join(PROJECT_ARTIFACTS_DIR, fname))
            except FileNotFoundError:
                pass

        # RR-4 (Phase 2): reset_execution zeros reviewer_retries and reviewer_rejected so
        # the next reviewer invocation starts at pass 1.  reviewer_infra_retries and
        # reviewer_infra_recovery_attempts are NOT zeroed — they survive auto retries and
        # only reset on a full phase reset (reset_phase).  executor_succeeded is cleared
        # because we are re-running execution from scratch.
        phase_state = self.read_phase_state()
        phase_state["reviewer_retries"] = 0
        phase_state["reviewer_rejected"] = False
        phase_state.pop("executor_succeeded", None)

        # Increment the correct counter — never both.
        if caller == "auto":
            phase_state["executor_retries"] = phase_state.get("executor_retries", 0) + 1
            new_count = phase_state["executor_retries"]
            self.state["executor_retries"] = new_count
            print(f"[INFO] reset_execution(auto): executor_retries now {new_count}.")
        elif caller == "escalation":
            phase_state["escalation_resets"] = phase_state.get("escalation_resets", 0) + 1
            new_count = phase_state["escalation_resets"]
            print(f"[INFO] reset_execution(escalation): escalation_resets now {new_count}.")
            # FIND-ESCALATION-CAP: log reason per reset so infra vs logic failures are
            # distinguishable when the cap is reached.
            reason = phase_state.get("last_error_code", "unknown")
            entry = {
                "reset_number": new_count,
                "command": "RESET_EXECUTION",
                "reason": reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            phase_state.setdefault("reset_log", []).append(entry)
            print(f"[INFO] reset_execution(escalation): logged reason={reason!r} for reset {new_count}.")
        self.write_phase_state_atomic(phase_state)

        # Set state so main loop routes to executor on next iteration
        self.state["current_agent"] = "executor"
        self.transition_state("RUNNING", f"reset_execution({caller}): executor reset, awaiting retry")

    def reset_reviewer(self):
        """Reviewer-only reset. Preserves planner and executor output. Clears reviewer outputs.

        Always called from the RESET_REVIEWER escalation command — increments escalation_resets.
        After this returns, the main loop (current_agent='reviewer', RUNNING) re-invokes the reviewer.
        """
        # Clear only reviewer outputs. Planner and executor outputs are preserved.
        for fname in [
            "reviewer_output.json", "reviewer_output.done",
        ]:
            try:
                os.remove(os.path.join(PROJECT_ARTIFACTS_DIR, fname))
            except FileNotFoundError:
                pass

        phase_state = self.read_phase_state()
        phase_state["reviewer_retries"] = 0
        phase_state["reviewer_rejected"] = False
        phase_state["escalation_resets"] = phase_state.get("escalation_resets", 0) + 1
        new_count = phase_state["escalation_resets"]
        reason = phase_state.get("last_error_code", "unknown")
        entry = {
            "reset_number": new_count,
            "command": "RESET_REVIEWER",
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        phase_state.setdefault("reset_log", []).append(entry)
        print(f"[INFO] reset_reviewer: escalation_resets now {new_count}, reason={reason!r}.")
        self.write_phase_state_atomic(phase_state)

        # Set state so main loop routes to reviewer on next iteration
        self.state["current_agent"] = "reviewer"
        self.transition_state("RUNNING", "reset_reviewer: reviewer reset, awaiting retry")

    def _mark_roadmap_phase(self, raw_id: str, marker: str) -> None:
        """Atomically update the roadmap.md checkbox for raw_id to [marker].

        marker is one of 'x' (complete/PROCEED) or '-' (skipped/SKIP).
        Silently logs a warning on any failure — never raises.
        """
        import glob as _glob
        import re as _re
        _roadmap_files = _glob.glob(os.path.join(SYMLINK_TARGET, "*[Rr]oadmap*.md"))
        if not _roadmap_files:
            print(f"[WARN] _mark_roadmap_phase: no roadmap file found, {raw_id} checkbox not updated.")
            return
        _roadmap_path = _roadmap_files[0]
        try:
            with open(_roadmap_path, "r") as _rf:
                _content = _rf.read()
            _new_content = _re.sub(
                r"- \[[ x\-]\] `" + _re.escape(raw_id) + r"`",
                f"- [{marker}] `{raw_id}`",
                _content,
            )
            if _new_content == _content:
                print(f"[WARN] _mark_roadmap_phase: pattern not found for {raw_id!r} in {_roadmap_path}.")
                return
            _fd, _tmp = tempfile.mkstemp(dir=os.path.dirname(_roadmap_path))
            try:
                with os.fdopen(_fd, "w") as _wf:
                    _wf.write(_new_content)
                os.replace(_tmp, _roadmap_path)
                print(f"[INFO] _mark_roadmap_phase: marked {raw_id} as [{marker}] in roadmap.")
            except Exception:
                try:
                    os.unlink(_tmp)
                except OSError:
                    pass
                raise
        except Exception as _e:
            print(f"[WARN] _mark_roadmap_phase: could not update roadmap for {raw_id!r}: {_e}")

    def increment_executor_retries(self):
        phase_state = {}
        if os.path.exists(PHASE_STATE_FILE):
            try:
                with open(PHASE_STATE_FILE, 'r') as f:
                    phase_state = json.load(f)
            except Exception:
                pass
        else:
            phase_state = {"planner_retries": 0, "executor_retries": 0, "reviewer_retries": 0, "reviewer_rejected": False, "escalation_resets": 0}

        phase_state["executor_retries"] = phase_state.get("executor_retries", 0) + 1
        
        target_dir = _atomic_temp_dir_for_project_writes()
        fd, temp_path = tempfile.mkstemp(dir=target_dir, prefix="phase_state_")
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(phase_state, f, indent=2)
            os.replace(temp_path, PHASE_STATE_FILE)
        except Exception as e:
            print(f"[ERROR] Failed to write phase_state: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
        
        self.state["executor_retries"] = phase_state["executor_retries"]
        self.transition_state("RUNNING", f"Incremented executor retries to {phase_state['executor_retries']}")
        return phase_state["executor_retries"]

    def run_executor_output_gate(self):
        gate_script = os.path.join(AUTODEV_REPO_PATH, "autodev", "pipeline", "gate_scripts", "executor_gate.py")
        try:
            result = subprocess.run(
                [sys.executable, gate_script],
                capture_output=True,
                text=True,
                check=True,
                timeout=GATE_SUBPROCESS_TIMEOUT,
            )
            output = result.stdout.strip()
            return output == "PASS"
        except subprocess.TimeoutExpired as e:
            print(f"[ERROR] Executor gate subprocess timed out after {GATE_SUBPROCESS_TIMEOUT}s: {e}")
            return False
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Gate script failed: {e}")
            return False

    def run_blame_attribution(self) -> dict:
        """Three-layer blame attribution system.

        Layer 1 (primary)  — qwen3.5-27b analyst reading failure_context.json.
                             Routes immediately on high-confidence plan/impl/infra.
                             Falls through to Layer 2 on low confidence, unknown fault,
                             empty response, timeout, or malformed JSON.

        Layer 2 (fallback) — deterministic heuristics (preserved verbatim from prior
                             implementation).  Routes on clear interface/logic signals.
                             Falls through to Layer 3 when inconclusive.

        Layer 3 (default)  — hard default: impl.  Never routes to planner without
                             evidence.

        Returns {"blame": "plan"|"impl"|"infra", "reason": "<string>"}.
        The orchestrator caller handles routing based on blame value.
        """
        phase_raw_id = self.state.get("current_phase_raw_id", "?")
        attempt = self.state.get("executor_retries", 0)
        lessons_path = os.path.join(PROJECT_ARTIFACTS_DIR, "lessons.md")

        def _append_blame_log(layer: int, fault, confidence, routing: str, reasoning: str):
            ts = datetime.now(timezone.utc).isoformat()
            entry = (
                f"\n[BLAME] ts={ts} phase={phase_raw_id} attempt={attempt} "
                f"layer={layer} fault={fault} confidence={confidence} "
                f"routing={routing} reasoning={reasoning}"
            )
            try:
                with open(lessons_path, "a") as _f:
                    _f.write(entry)
            except Exception as _le:
                print(f"[WARN] blame log write failed: {_le}")

        def _record_blame_attribution(fault: str):
            """Append this blame's fault to prior_blame_attributions in phase_state.json."""
            _ps = self.read_phase_state()
            _pba = _ps.get("prior_blame_attributions", [])
            _pba.append(fault)
            _ps["prior_blame_attributions"] = _pba
            self.write_phase_state_atomic(_ps)

        # -----------------------------------------------------------------------
        # Layer 1 — qwen3.5-27b analyst (primary path)
        # -----------------------------------------------------------------------
        failure_context_path = os.path.join(PROJECT_ARTIFACTS_DIR, "failure_context.json")
        failure_context_data = None
        if os.path.exists(failure_context_path):
            try:
                with open(failure_context_path, 'r') as f:
                    failure_context_data = json.load(f)
            except Exception:
                pass

        if failure_context_data is not None:
            _system_prompt = (
                "You are a failure analyst for an autonomous software development pipeline. "
                "You receive structured failure context from a failed executor or planner agent "
                "and must determine the root cause and recommend a routing action.\n\n"
                "You must respond with a JSON object containing exactly three fields:\n"
                "- \"fault\": one of \"plan\", \"impl\", \"infrastructure\", \"unknown\"\n"
                "- \"confidence\": one of \"high\", \"medium\", \"low\"\n"
                "- \"reasoning\": a string of one to three sentences explaining your determination\n\n"
                "Definitions:\n"
                "- \"plan\": the planner produced an ambiguous, contradictory, or incomplete "
                "specification that made correct implementation impossible\n"
                "- \"impl\": the executor had a correct specification but failed to implement it correctly\n"
                "- \"infrastructure\": the failure is caused by a system condition (model unavailability, "
                "file system error, network timeout) unrelated to plan or implementation quality\n"
                "- \"unknown\": insufficient evidence to determine fault with any confidence\n\n"
                "When failure_context.json fields are empty, null, or missing, lower your confidence "
                "accordingly. An empty or absent failure_reason with no gate error codes is not enough "
                "evidence to attribute to plan — default toward \"impl\" or \"unknown\" when evidence "
                "is thin.\n\n"
                "Do not invent evidence. Do not speculate beyond what the failure context contains."
            )
            _payload = {
                "model": "qwen3.5-27b",
                "messages": [
                    {"role": "system", "content": _system_prompt},
                    {"role": "user", "content": json.dumps(failure_context_data)},
                ],
                "response_format": {"type": "json_object"},
            }
            _llama_chat_base = (
                self.openclaw_config.get("models", {})
                .get("providers", {})
                .get("llama-local", {})
                .get("baseUrl", f"{_LLAMA_ORIGIN}/v1")
                .rstrip("/")
            )
            _chat_url = f"{_llama_chat_base}/chat/completions"
            try:
                _resp = requests.post(
                    _chat_url,
                    json=_payload, timeout=60
                )
                _resp.raise_for_status()
                _raw = _resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                if not _raw.strip():
                    raise ValueError("empty response from analyst")
                _parsed = json.loads(_raw)
                _fault = _parsed.get("fault", "unknown")
                _conf = _parsed.get("confidence", "low")
                _reasoning = _parsed.get("reasoning", "")

                if _fault == "plan" and _conf == "high":
                    _append_blame_log(1, _fault, _conf, "plan", _reasoning)
                    _record_blame_attribution("plan")
                    return {"blame": "plan", "reason": f"[L1] {_reasoning}"}

                elif _fault == "impl" and _conf == "high":
                    _append_blame_log(1, _fault, _conf, "impl", _reasoning)
                    _record_blame_attribution("impl")
                    return {"blame": "impl", "reason": f"[L1] {_reasoning}"}

                elif _fault == "infrastructure" and _conf in ("high", "medium"):
                    _append_blame_log(1, _fault, _conf, "escalate", _reasoning)
                    _record_blame_attribution("infrastructure")
                    return {"blame": "infra", "reason": f"[L1] {_reasoning}"}

                else:
                    # Low confidence or unknown — fall through to Layer 2
                    _append_blame_log(1, _fault, _conf, "fallback", _reasoning)

            except json.JSONDecodeError as _l1_json_err:
                # Malformed analyst JSON is itself an infra symptom (model truncated output).
                # Route to 'unknown' and return early rather than falling through to Layer 2
                # which would default to 'impl' — misrouting an infra failure as impl wastes
                # a full executor retry.
                _append_blame_log(1, "malformed_json", "null", "unknown",
                                  f"malformed analyst JSON: {_l1_json_err}")
                _record_blame_attribution("unknown")
                return {"blame": "unknown",
                        "reason": f"[L1] malformed analyst JSON — escalating: {_l1_json_err}"}
            except Exception as _l1_err:
                _append_blame_log(1, "null", "null", "fallback",
                                  f"analyst unavailable: {_l1_err}")
        else:
            _append_blame_log(1, "null", "null", "fallback",
                              "analyst unavailable: no failure_context.json")

        # -----------------------------------------------------------------------
        # Layer 2 — deterministic heuristics (preserved from prior implementation)
        # -----------------------------------------------------------------------
        failure_text = ""
        executor_output_path = os.path.join(PROJECT_ARTIFACTS_DIR, "executor_output.json")
        if os.path.exists(executor_output_path):
            try:
                with open(executor_output_path, 'r') as f:
                    exec_out = json.load(f)
                failure_text += str(exec_out.get("failure_reason", ""))
                failure_text += str(exec_out.get("troubleshooting_attempts", ""))
            except Exception:
                pass

        interface_errors = [
            "AttributeError", "NameError", "undefined", "has no attribute",
            "not defined", "missing 1 required positional argument",
            "missing positional argument", "unexpected keyword argument", "not found",
        ]
        logic_errors = ["AssertionError", "expected", "but got", "!=="]

        if any(err.lower() in failure_text.lower() for err in interface_errors):
            _r = "Interface mismatch or undefined schema."
            _append_blame_log(2, "plan", "high", "plan", _r)
            _record_blame_attribution("plan")
            return {"blame": "plan", "reason": f"[L2] {_r}"}

        if any(err.lower() in failure_text.lower() for err in logic_errors):
            _r = "Implementation logic failed despite correct interface."
            _append_blame_log(2, "impl", "high", "impl", _r)
            _record_blame_attribution("impl")
            return {"blame": "impl", "reason": f"[L2] {_r}"}

        # -----------------------------------------------------------------------
        # Layer 3 — hard default: impl
        # -----------------------------------------------------------------------
        _r = "Insufficient evidence for confident attribution; defaulting to impl."
        _append_blame_log(3, "impl", "low", "default", _r)
        _record_blame_attribution("impl")
        return {"blame": "impl", "reason": f"[L3] {_r}"}

    def _append_failure_history(self, failure_context_path: str) -> None:
        """W1-D: Append old failure_context.json content to failure_history.jsonl before overwrite.

        Reads phase_state for last_error_code and escalation_trigger_reason (both optional).
        Uses O_APPEND — not atomic-rename — since this is an accumulating log, not a
        single-value file.  Non-blocking: any OSError is printed and swallowed.
        """
        if not os.path.exists(failure_context_path):
            return
        try:
            with open(failure_context_path, "r") as f:
                old_context = json.load(f)
        except (OSError, ValueError):
            return

        ps = {}
        try:
            if os.path.exists(PHASE_STATE_FILE):
                with open(PHASE_STATE_FILE, "r") as f:
                    ps = json.load(f)
        except (OSError, ValueError):
            pass

        entry = {
            **old_context,
            "last_error_code": ps.get("last_error_code"),
            "escalation_trigger_reason": ps.get("escalation_trigger_reason"),
            "appended_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        history_path = os.path.join(PROJECT_ARTIFACTS_DIR, "failure_history.jsonl")
        try:
            os.makedirs(PROJECT_ARTIFACTS_DIR, exist_ok=True)
            with open(history_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as e:
            print(f"[WARN] _append_failure_history: {e}")

    def write_failure_context(self, failing_agent: str, attempt_number: int) -> None:
        """Write failure_context.json atomically under PROJECT_ARTIFACTS_DIR.

        Called at every point where an agent has failed and a routing decision is about
        to be made: planner gate fail, executor gate fail (including retry-exhausted /
        blame path), and reviewer gate fail.  Overwrites any prior failure_context.json
        — always reflects the most recent failure.  Non-blocking: errors are logged and
        swallowed so a write failure never crashes the pipeline.
        """
        if not os.path.exists(SYMLINK_TARGET):
            print("[WARN] write_failure_context: SYMLINK_TARGET not found, skipping")
            return

        phase_state = self.read_phase_state()

        # --- Agent self-report fields (executor output, if present) ---
        executor_output = {}
        executor_output_path = os.path.join(PROJECT_ARTIFACTS_DIR, "executor_output.json")
        if os.path.exists(executor_output_path):
            try:
                with open(executor_output_path, 'r') as f:
                    executor_output = json.load(f)
            except Exception:
                pass

        # --- Reviewer blocking issues (if reviewer just failed) ---
        reviewer_output = {}
        reviewer_output_path = os.path.join(PROJECT_ARTIFACTS_DIR, "reviewer_output.json")
        if os.path.exists(reviewer_output_path):
            try:
                with open(reviewer_output_path, 'r') as f:
                    reviewer_output = json.load(f)
            except Exception:
                pass

        # --- Gate error codes from phase_state (last_error_code field) ---
        gate_error_codes = []
        last_error = phase_state.get("last_error_code")
        if last_error:
            gate_error_codes = [last_error]

        # --- files_present_on_disk: raw filesystem truth for blame analyst ---
        # Walk SYMLINK_TARGET, exclude pipeline metadata files and git internals.
        _pipeline_meta = {
            "phase_state.json", "planner_output.json", "planner_output.done",
            "executor_output.json", "executor_output.done",
            "reviewer_output.json", "reviewer_output.done",
            "escalation_output.json", "escalation_output.done",
            "failure_context.json", "current_phase.json",
            "executor_gate_detail.json",
        }
        files_present_on_disk = []
        try:
            for _root, _dirs, _files in os.walk(SYMLINK_TARGET):
                _dirs[:] = [
                    d for d in _dirs
                    if d not in ('.git', '__pycache__', 'node_modules', '.autodev')
                ]
                for _fname in _files:
                    if _fname in _pipeline_meta or _fname.endswith('.done'):
                        continue
                    _rel = os.path.relpath(os.path.join(_root, _fname), SYMLINK_TARGET)
                    files_present_on_disk.append(_rel)
            files_present_on_disk.sort()
        except Exception as _walk_err:
            print(f"[WARN] write_failure_context: filesystem walk failed: {_walk_err}")

        # --- tests_passing: from executor self-report ---
        _tr = executor_output.get("test_results", {})
        tests_passing = _tr.get("all_passing") if isinstance(_tr, dict) else None

        context = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "phase_raw_id": self.state.get("current_phase_raw_id", ""),
            "failing_agent": failing_agent,
            "attempt_number": attempt_number,
            "gate_error_codes": gate_error_codes,
            "agent_status": executor_output.get("status") if failing_agent == "executor" else None,
            "agent_failure_reason": (
                executor_output.get("failure_reason") if failing_agent == "executor" else None
            ),
            "agent_troubleshooting_attempts": (
                executor_output.get("troubleshooting_attempts") or []
                if failing_agent == "executor" else []
            ),
            "blocking_issues": reviewer_output.get("blocking_issues") or [],
            "tests_written": executor_output.get("tests_written") or [],
            "tests_passing": tests_passing,
            "file_manifest": executor_output.get("file_manifest") or [],
            "files_present_on_disk": files_present_on_disk,
            "planner_retries_at_failure": self.state.get("planner_retries", 0),
            "executor_retries_at_failure": self.state.get("executor_retries", 0),
            "reviewer_retries_at_failure": self.state.get("reviewer_retries", 0),
            "prior_blame_attributions": phase_state.get("prior_blame_attributions", []),
        }

        _gate_detail_path = os.path.join(PROJECT_ARTIFACTS_DIR, "executor_gate_detail.json")
        if os.path.exists(_gate_detail_path):
            try:
                with open(_gate_detail_path, "r") as _gf:
                    _gate_detail = json.load(_gf)
                if isinstance(_gate_detail, dict):
                    context["gate_failure_detail"] = _gate_detail
                    _ud = _gate_detail.get("unaccounted_deletions")
                    if _ud:
                        context["unaccounted_deletions"] = _ud
            except Exception as _gde:
                print(f"[WARN] write_failure_context: could not read executor_gate_detail.json: {_gde}")
            try:
                os.remove(_gate_detail_path)
            except OSError:
                pass

        _failure_context_path = os.path.join(PROJECT_ARTIFACTS_DIR, "failure_context.json")
        try:
            os.makedirs(PROJECT_ARTIFACTS_DIR, exist_ok=True)
        except OSError:
            pass
        _fc_dir = os.path.dirname(_failure_context_path) or "."
        fd, temp_path = tempfile.mkstemp(dir=_fc_dir, prefix="failure_context_")
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(context, f, indent=2)
            self._append_failure_history(_failure_context_path)  # W1-D: archive before overwrite
            os.replace(temp_path, _failure_context_path)
            print(
                f"[INFO] write_failure_context: wrote failure_context.json "
                f"(phase={context['phase_raw_id']}, agent={failing_agent}, attempt={attempt_number})"
            )
        except Exception as e:
            print(f"[ERROR] write_failure_context failed: {e}")
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass

    def set_reviewer_rejected(self):
        phase_state = {}
        if os.path.exists(PHASE_STATE_FILE):
            try:
                with open(PHASE_STATE_FILE, 'r') as f:
                    phase_state = json.load(f)
            except Exception:
                pass
        phase_state["reviewer_rejected"] = True
        target_dir = _atomic_temp_dir_for_project_writes()
        fd, temp_path = tempfile.mkstemp(dir=target_dir, prefix="phase_state_")
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(phase_state, f, indent=2)
            os.replace(temp_path, PHASE_STATE_FILE)
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def increment_reviewer_retries(self):
        phase_state = {}
        if os.path.exists(PHASE_STATE_FILE):
            try:
                with open(PHASE_STATE_FILE, 'r') as f:
                    phase_state = json.load(f)
            except Exception:
                pass
        else:
            phase_state = {"planner_retries": 0, "executor_retries": 0, "reviewer_retries": 0, "reviewer_rejected": False, "escalation_resets": 0}

        phase_state["reviewer_retries"] = phase_state.get("reviewer_retries", 0) + 1
        
        target_dir = _atomic_temp_dir_for_project_writes()
        fd, temp_path = tempfile.mkstemp(dir=target_dir, prefix="phase_state_")
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(phase_state, f, indent=2)
            os.replace(temp_path, PHASE_STATE_FILE)
        except Exception as e:
            print(f"[ERROR] Failed to write phase_state: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
        
        self.state["reviewer_retries"] = phase_state["reviewer_retries"]
        self.transition_state("RUNNING", f"Incremented reviewer retries to {phase_state['reviewer_retries']}")
        return phase_state["reviewer_retries"]

    def run_reviewer_output_gate(self):
        gate_script = os.path.join(AUTODEV_REPO_PATH, "autodev", "pipeline", "gate_scripts", "reviewer_gate.py")
        try:
            result = subprocess.run(
                [sys.executable, gate_script],
                capture_output=True,
                text=True,
                check=True,
                timeout=GATE_SUBPROCESS_TIMEOUT,
            )
            output = result.stdout.strip()
            return output
        except subprocess.TimeoutExpired as e:
            print(f"[ERROR] Reviewer gate subprocess timed out after {GATE_SUBPROCESS_TIMEOUT}s: {e}")
            return "ROUTE_ESCALATE"
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Gate script failed: {e}")
            return "ROUTE_ESCALATE"

    def run_repo_init_check(self):
        """Runs repo_init_check.py as a subprocess per PIPELINE-SPEC §13.
        Returns (passed: bool, details: str). Never retries on failure."""
        gate_script = os.path.join(AUTODEV_REPO_PATH, "autodev", "pipeline", "gate_scripts", "repo_init_check.py")
        try:
            # Inherit env so repo_init_check.py sees OPENCLAW_ROOT (Docker / custom OpenClaw roots).
            result = subprocess.run(
                [sys.executable, gate_script],
                capture_output=True,
                text=True,
                env=os.environ,
                timeout=GATE_SUBPROCESS_TIMEOUT,
            )
            output = (result.stdout + result.stderr).strip()
            if result.returncode == 0:
                print("[INFO] Repo init check passed.")
                return True, output
            else:
                print(f"[ERROR] Repo init check failed:\n{output}")
                return False, output
        except subprocess.TimeoutExpired as e:
            details = f"Repo init check subprocess timed out after {GATE_SUBPROCESS_TIMEOUT}s: {e}"
            print(f"[ERROR] {details}")
            return False, details
        except Exception as e:
            details = f"Repo init check subprocess error: {e}"
            print(f"[ERROR] {details}")
            return False, details

    def _run_startup_planner_phase_zero_and_branch(self):
        """Phase-0 phase_resolver, queue completion/advance, and feature-branch checkout.

        Returns:
            "exit_run" — leave run() entirely (orchestrator stops).
            "retry_startup" — symlink/project may have changed; re-run this method.
            "enter_main_loop" — proceed to the main while True loop.
        """
        if self.state.get("current_agent", "planner") != "planner":
            return "enter_main_loop"

        if self.state.get("current_phase", 0) == 0:
            gate_script = os.path.join(
                AUTODEV_REPO_PATH, "autodev", "pipeline", "gate_scripts", "phase_resolver.py"
            )
            try:
                result = subprocess.run([sys.executable, gate_script], capture_output=True, text=True)
                output = result.stdout.strip()
                if result.returncode == 0 and "PENDING: Phase" in output:
                    cp_path = os.path.join(PROJECT_ARTIFACTS_DIR, "current_phase.json")
                    if os.path.exists(cp_path):
                        with open(cp_path) as f:
                            first_phase = json.load(f)
                        self.state["current_phase"] = first_phase.get("phase_number", 0)
                        self.state["current_phase_raw_id"] = first_phase.get("raw_id", "")
                        self.state["phase_start_time"] = datetime.now(timezone.utc).isoformat()
                        self.write_state()
                elif result.returncode == 0 and "PIPELINE_COMPLETE" in output:
                    print("[INFO] All roadmap phases already complete. Nothing to do.")
                    self.state["current_phase_raw_id"] = ""
                    self.state["current_agent"] = None
                    _write_run_summary("PIPELINE_COMPLETE", "Pipeline fully complete on startup")  # W2-B
                    self.transition_state("PIPELINE_COMPLETE", "Pipeline fully complete on startup")
                    self._queue_update_active_entry(
                        "COMPLETED",
                        {"completed_at": datetime.now(timezone.utc).isoformat()},
                    )
                    queue_data = self._read_queue()
                    if queue_data["queue"] and queue_data.get("queue_mode", "auto") == "auto":
                        if self._select_next_queue_project(halt_if_no_eligible=False):
                            self.read_state()
                            return "retry_startup"
                    return "exit_run"
                elif result.returncode == 2 and "BLOCKED" in output:
                    print("[INFO] First pending phase is blocked. Escalating.")
                    _now = datetime.now(timezone.utc).isoformat()
                    _write_run_summary("BLOCKED", "Roadmap blocked at startup")  # W2-B
                    self.transition_state("BLOCKED", "Roadmap blocked at startup")
                    self._queue_park_active_entry(
                        "BLOCKED",
                        "roadmap_blocked",
                        {"blocked_at": _now},
                    )
                    if self._queue_after_park_maybe_advance():
                        self.read_state()
                        return "retry_startup"
                    return "exit_run"
            except Exception as startup_err:
                print(f"[WARN] Startup phase identification failed: {startup_err}. Proceeding; planner must self-orient.")

        _startup_raw = self.state.get("current_phase_raw_id", "")
        _startup_num = self.state.get("current_phase", 0)
        if _startup_raw or _startup_num:
            branch = f"phase/{_startup_raw}" if _startup_raw else f"phase/{_startup_num}"
            if not self.state.get("phase_base_commit"):
                _base_result = subprocess.run(
                    ["git", "rev-parse", "HEAD"], cwd=SYMLINK_TARGET, capture_output=True, text=True
                )
                if _base_result.returncode == 0:
                    self.state["phase_base_commit"] = _base_result.stdout.strip()
                    self.write_state()
            subprocess.run(
                f"git checkout {branch} 2>/dev/null || git checkout -b {branch}",
                shell=True,
                cwd=SYMLINK_TARGET,
            )
            print(f"[INFO] Startup: checked out branch {branch} for phase {_startup_raw or _startup_num}.")

            import glob as _startup_glob

            _startup_gate = os.path.join(
                AUTODEV_REPO_PATH, "autodev", "pipeline", "gate_scripts", "phase_resolver.py"
            )
            _startup_roadmap = _startup_glob.glob(os.path.join(SYMLINK_TARGET, "*[Rr]oadmap*.md"))
            if _startup_roadmap:
                try:
                    subprocess.run(
                        [sys.executable, _startup_gate, _startup_roadmap[0]],
                        cwd=OPENCLAW_ROOT,
                        check=True,
                    )
                    print("[INFO] Startup: roadmap_parser re-run, current_phase.json refreshed.")
                except Exception as _startup_rp_err:
                    print(
                        f"[WARN] Startup: roadmap_parser re-run failed: {_startup_rp_err}. "
                        "current_phase.json may be stale."
                    )
            else:
                print("[WARN] Startup: no roadmap file found, current_phase.json not refreshed.")

        return "enter_main_loop"

    def run(self):
        """Main event loop."""
        self.acquire_lock()
        try:
            self.read_state()

            # --- Stranded temp-file cleanup (FIND-STRANDED-TEMPS) ---
            # Remove any mkstemp files left behind by a previous crash before
            # running the repo init check, so stale files don't interfere with
            # state reads or git status output.
            cleanup_stranded_temp_files(AUTODEV_PIPELINE_ROOT)

            # --- Repo Init Check (PIPELINE-SPEC §13) ---
            # Runs on every startup/resume before the phase loop. Validates workspace
            # structure (symlink, roadmap, agent dirs, support docs, .gitignore).
            # Exit 0 → proceed. Exit 1 → escalate immediately, no retry.
            init_passed, init_details = self.run_repo_init_check()
            if not init_passed:
                failure_context = f"Repo init check failed: {init_details}"
                self.state["current_agent"] = "escalation"
                self.transition_state("RUNNING", failure_context)
                phase = self.state.get("current_phase", 0)
                raw_id = self.state.get("current_phase_raw_id", "unknown")
                session_key = f"pipeline:phase-{phase}:{raw_id}:repo-init-failure"
                token = self.openclaw_config.get("hooks", {}).get("token", "")
                if os.path.exists(SYMLINK_TARGET):
                    cleanup_output_files(PROJECT_ARTIFACTS_DIR, "escalation")
                _ps = self.read_phase_state()
                _ps["escalation_trigger_reason"] = failure_context
                _ps["escalations"] = _ps.get("escalations", 0) + 1  # W1-B
                _ps["waiting_for_human_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # W1-E
                self.write_phase_state_atomic(_ps)
                _write_pipeline_event("escalation_trigger", raw_id, "escalation", {"reason": _ps.get("escalation_trigger_reason")})  # W1-F
                self.transition_state("WAITING_FOR_HUMAN", "Invoking Escalation Agent: repo init check failed")
                self._queue_park_active_entry("ESCALATION", "escalation")
                # Note: park-and-advance is not applied here — the next queued project must pass
                # repo init on a fresh orchestrator run; advancing without re-check would be unsafe.
                webhook_status = invoke_agent_webhook("escalation", session_key, token)
                if webhook_status != "SUCCESS":
                    print("[ERROR] Escalation webhook failed after repo init failure.")
                    fallback_dir = _atomic_temp_dir_for_project_writes()
                    error_data = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "phase": phase,
                        "gate": "repo_init_check",
                        "original_failure_reason": failure_context
                    }
                    try:
                        with open(os.path.join(fallback_dir, "escalation_failed.json"), "w") as f:
                            json.dump(error_data, f)
                    except Exception as write_err:
                        print(f"[ERROR] Could not write escalation_failed.json: {write_err}")
                    _write_run_summary("HALTED_SILENT", "Escalation delivery failed after repo init failure")  # W2-B
                    self.transition_state("HALTED_SILENT", "Escalation delivery failed after repo init failure")
                    self._queue_update_active_entry(
                        "FAILED",
                        {"failed_at": datetime.now(timezone.utc).isoformat()},
                    )
                return  # Do not enter phase loop; finally block will release lock

            # --- Startup Phase Identification + branch checkout (may repeat after queue auto-advance) ---
            _startup_pass = 0
            while _startup_pass < 20:
                _startup_pass += 1
                _startup_rv = self._run_startup_planner_phase_zero_and_branch()
                if _startup_rv == "exit_run":
                    return
                if _startup_rv == "retry_startup":
                    continue
                break
            else:
                print("[ERROR] Startup exceeded max iterations (queue advance loop); exiting.")
                return

            print("[INFO] Starting orchestrator loop (Phase 5 Integration)")
            while True:
                pst = self.state.get("pipeline_status")
                if pst in ["HALTED_SILENT", "BLOCKED", "PIPELINE_COMPLETE"]:
                    print(f"[INFO] Pipeline is halted/blocked/complete ({pst}). Exiting.")
                    # Stale PIPELINE_COMPLETE (e.g. prior project's state) must not mark the
                    # current queue row COMPLETED when the roadmap still has pending phases.
                    if pst == "PIPELINE_COMPLETE":
                        if self._phase_resolver_indicates_pipeline_complete():
                            self._queue_update_active_entry(
                                "COMPLETED",
                                {"completed_at": datetime.now(timezone.utc).isoformat()},
                            )
                            break
                        print(
                            "[INFO] Stale PIPELINE_COMPLETE — roadmap has pending work; "
                            "recovering to RUNNING."
                        )
                        self.transition_state(
                            "RUNNING",
                            "Recovered stale PIPELINE_COMPLETE; pending phases remain",
                        )
                        continue
                    break

                if self._check_stop_requested():
                    print("[STOP] Stop sentinel detected — halting pipeline cleanly")
                    _write_run_summary("STOPPED", "Stop sentinel consumed — clean halt requested via UI")  # W2-B
                    self.transition_state("STOPPED", "Stop sentinel consumed — clean halt requested via UI")
                    break
                    
                self.read_state()
                current_agent = self.state.get("current_agent", "planner")
                phase = self.state.get("current_phase", 0)
                # Global phase index (not subsystem-local suffix) is used in all session
                # keys so that INFRA-1, CORE-1, and UI-1 — which all have suffix "1" —
                # produce unique keys.  phase_resolver.py counts each phase's 0-based
                # position in the full roadmap list, not the local subsystem counter.
                raw_id = self.state.get("current_phase_raw_id", "unknown")

                if current_agent == "planner":
                    retries = self.state.get("planner_retries", 0)

                    # RR-2 (Phase 4): Crash-recovery skip — if the planner already produced
                    # valid output this phase (planner_output_preserved flag is True AND files
                    # pass the gate), skip re-invocation and advance directly to executor.
                    # MUST be guarded by the flag to distinguish crash-recovery from an
                    # intentional ROUTE_PLANNER re-run (which clears the flag above).
                    if (
                        retries == 0
                        and self.state.get("planner_output_preserved", False)
                        and self.planner_output_is_valid()
                    ):
                        print("[INFO] [PLANNER] Valid output from prior run preserved — skipping re-invocation.")
                        self.state["current_agent"] = "executor"
                        self.transition_state("RUNNING", "Crash recovery — planner output intact, advancing to executor")
                        time.sleep(2)
                        continue

                    session_key = f"pipeline:phase-{phase}:{raw_id}:planner-attempt-{retries + 1}"
                    sentinel_path = os.path.join(PROJECT_ARTIFACTS_DIR, "planner_output.done")
                    token = self.openclaw_config.get("hooks", {}).get("token", "")

                    cleanup_output_files(PROJECT_ARTIFACTS_DIR, "planner")
                    self.skill_manager.inject_skill(
                        self.state.get("current_phase_raw_id", ""), "planner", self.openclaw_config
                    )
                    self._record_injected_skill("planner")

                    self.state["sentinel_wait_started_at"] = datetime.now(timezone.utc).isoformat()
                    self.transition_state("WAITING_FOR_SENTINEL", "Invoking Planner via webhook")
                    webhook_status = invoke_agent_webhook(
                        "planner", session_key, token, model=self._get_agent_model("planner")
                    )
                    
                    if webhook_status != "SUCCESS":
                        self.state["current_agent"] = "escalation"
                        error_reason = "Auth Config Error" if webhook_status == "AUTH_ERROR" else "Webhook infra failure"
                        self.transition_state("RUNNING", error_reason)
                        time.sleep(5)
                        continue

                    _stop_file = os.path.join(PROJECT_ARTIFACTS_DIR, "pipeline_stop_requested")
                    sentinel_found = poll_for_sentinel(sentinel_path, timeout_seconds=600, stop_sentinel_path=_stop_file)

                    # W1-G: Resolve planner session JSONL path and capture token usage.
                    # Mirrors executor/reviewer 15-retry lookup pattern — registry may lag sentinel.
                    _planner_sessions_dir = os.path.join(OPENCLAW_ROOT, "agents", "planner", "sessions")
                    _planner_sessions_json = os.path.join(_planner_sessions_dir, "sessions.json")
                    _planner_full_key = f"agent:planner:{session_key}".lower()
                    _planner_jsonl_path = None
                    for _ in range(15):
                        try:
                            with open(_planner_sessions_json) as _sf:
                                _sd = json.load(_sf)
                            _sid = _sd.get(_planner_full_key, {}).get("sessionId")
                            if _sid:
                                _planner_jsonl_path = os.path.join(_planner_sessions_dir, f"{_sid}.jsonl")
                                break
                        except Exception:
                            pass
                        time.sleep(2)
                    _planner_tokens = _sum_session_tokens(_planner_jsonl_path)
                    _ps_plan_tok = self.read_phase_state()
                    _planner_tokens_acc = _ps_plan_tok.get("planner_tokens_acc", {})
                    for _k, _v in _planner_tokens.items():
                        _planner_tokens_acc[_k] = _planner_tokens_acc.get(_k, 0) + _v
                    _ps_plan_tok["planner_tokens_acc"] = _planner_tokens_acc
                    self.write_phase_state_atomic(_ps_plan_tok)

                    if not sentinel_found:
                        print("[ERROR] Sentinel timeout")
                        retries = self.increment_planner_retries()
                    else:
                        gate_passed = self.run_planner_output_gate()
                        if gate_passed:
                            # RR-2 (Phase 4): Record that planner output is valid and preserved.
                            # Written atomically BEFORE transition_state so crash-recovery on
                            # restart can distinguish this state from an intentional ROUTE_PLANNER.
                            _ps_pp = self.read_phase_state()
                            _ps_pp["planner_output_preserved"] = True
                            _ps_pp.pop("last_error_code", None)
                            self.write_phase_state_atomic(_ps_pp)
                            self.state["planner_output_preserved"] = True
                            self.state["current_agent"] = "executor"
                            self.transition_state("RUNNING", "Planner passed, moving to executor")
                            time.sleep(5)
                            continue
                        else:
                            print("[ERROR] Planner gate failed")
                            _write_pipeline_event("gate_fail", raw_id, "planner", {"exit_code": 1})  # W1-F
                            self.write_failure_context("planner", self.state.get("planner_retries", 0) + 1)
                            retries = self.increment_planner_retries()
                            
                    if retries >= 3:
                        self.state["current_agent"] = "escalation"
                        self.transition_state("RUNNING", "Planner retries exhausted")
                        time.sleep(5)
                    else:
                        self.transition_state("RUNNING", "Preparing planner retry")
                        time.sleep(5)
                        
                elif current_agent == "executor":
                    phase = self.state.get("current_phase", 0)
                    raw_id = self.state.get("current_phase_raw_id", "unknown")
                    retries = self.state.get("executor_retries", 0)
                    
                    if retries >= 3:
                        # EX-RR: Before blame attribution, check whether a valid executor
                        # output arrived on disk from an orphaned background session that
                        # completed AFTER the orchestrator's sentinel poll ended.  If the
                        # gate passes, advance directly to reviewer so the successful work
                        # is not discarded.  executor_retries is reset to 0 to prevent a
                        # fresh restart from immediately re-entering this exhausted block.
                        _ex_rr_sentinel = os.path.join(PROJECT_ARTIFACTS_DIR, "executor_output.done")
                        _ex_rr_json = os.path.join(PROJECT_ARTIFACTS_DIR, "executor_output.json")
                        if os.path.exists(_ex_rr_sentinel) and os.path.exists(_ex_rr_json):
                            print("[INFO] [EX-RR] Surviving executor output found — running gate before blame.")
                            if self.run_executor_output_gate():
                                print("[INFO] [EX-RR] Gate passed — advancing to reviewer instead of escalating.")
                                _ps_rr = self.read_phase_state()
                                _ps_rr.pop("last_error_code", None)
                                _ps_rr["executor_succeeded"] = True
                                self.write_phase_state_atomic(_ps_rr)
                                self.state["current_agent"] = "reviewer"
                                self.state["executor_retries"] = 0
                                self.transition_state(
                                    "RUNNING",
                                    "EX-RR: surviving executor output passed gate — advancing to reviewer",
                                )
                                time.sleep(2)
                                continue
                            print("[INFO] [EX-RR] Gate failed on surviving output — proceeding with blame attribution.")
                        print("[INFO] Executor retries exhausted. Running blame attribution.")
                        self.write_failure_context("executor", self.state.get("executor_retries", 0))
                        blame_result = self.run_blame_attribution()
                        
                        phase_state = {}
                        if os.path.exists(PHASE_STATE_FILE):
                            try:
                                with open(PHASE_STATE_FILE, 'r') as f:
                                    phase_state = json.load(f)
                            except Exception:
                                pass
                        phase_state["blame_context"] = blame_result.get("reason", "")
                        phase_state["blame_verdict"] = blame_result.get("blame", "")  # "plan"|"impl"|"infra"|"unknown"
                        phase_state["blame_fires"] = phase_state.get("blame_fires", 0) + 1  # W1-A
                        try:
                            os.makedirs(PROJECT_ARTIFACTS_DIR, exist_ok=True)
                        except OSError:
                            pass
                        fd, temp_path = tempfile.mkstemp(
                            dir=os.path.dirname(PHASE_STATE_FILE.rstrip(os.sep)) or ".",
                            prefix="phase_state_",
                        )
                        try:
                            with os.fdopen(fd, 'w') as f:
                                json.dump(phase_state, f, indent=2)
                            os.replace(temp_path, PHASE_STATE_FILE)
                        except Exception:
                            if os.path.exists(temp_path):
                                os.remove(temp_path)
                                
                        if blame_result.get("blame") == "plan":
                            print("[INFO] Blame: Planner. Re-routing to planner.")
                            self.state["current_agent"] = "planner"
                            self.state["executor_retries"] = 0
                            self.transition_state("RUNNING", f"Rerouted to planner. Reason: {blame_result.get('reason')}")
                        elif blame_result.get("blame") == "impl":
                            # Cap consecutive "impl" blame retries at 3 before escalating.
                            # Without this cap the loop runs indefinitely causing OOM crashes.
                            _pba = phase_state.get("prior_blame_attributions", [])
                            _consecutive_impl = 0
                            for _b in reversed(_pba):
                                if _b == "impl":
                                    _consecutive_impl += 1
                                else:
                                    break
                            if _consecutive_impl >= 3:
                                print(f"[INFO] Blame: Executor (impl) x{_consecutive_impl} consecutive — escalating after impl cap.")
                                self.state["current_agent"] = "escalation"
                                self.transition_state("RUNNING", f"Impl blame cap reached ({_consecutive_impl}x): {blame_result.get('reason')}")
                            else:
                                print("[INFO] Blame: Executor (impl). Re-running executor with failure context.")
                                # reset_execution sets current_agent="executor" and transitions state.
                                self.reset_execution("auto")
                        else:
                            # "infra" or any unrecognised value — escalate immediately.
                            print("[INFO] Blame: Escalating.")
                            self.state["current_agent"] = "escalation"
                            self.transition_state("RUNNING", f"Executor retries exhausted. Reason: {blame_result.get('reason')}")
                        time.sleep(5)
                        continue
                        
                    # RR-F6: Crash-recovery skip — if executor already succeeded in a prior
                    # run (executor_succeeded flag in phase_state.json), skip re-invocation
                    # and advance directly to reviewer.  Mirrors the planner_output_preserved
                    # pattern (lines 2020-2029). Only applied when retries==0 so that a
                    # deliberate ROUTE_EXECUTOR re-run (which increments executor_retries) is
                    # not short-circuited.
                    if retries == 0 and self.executor_output_already_succeeded(self.read_phase_state()):
                        print("[INFO] [EXECUTOR] executor_succeeded flag is set — skipping re-invocation, advancing to reviewer.")
                        self.state["current_agent"] = "reviewer"
                        self.transition_state("RUNNING", "Crash recovery — executor output intact, advancing to reviewer")
                        time.sleep(2)
                        continue

                    # Target Selection — OpenRouter minimax for all executor attempts
                    model = "openrouter/minimax/minimax-m2.7"
                    session_key = f"pipeline:phase-{phase}:{raw_id}:executor-attempt-{retries + 1}"
                    attempt_label = "Cloud"

                    sentinel_path = os.path.join(PROJECT_ARTIFACTS_DIR, "executor_output.done")
                    token = self.openclaw_config.get("hooks", {}).get("token", "")

                    _attempt_start_time = time.time()  # captured before cleanup for stale-sentinel guard
                    cleanup_output_files(PROJECT_ARTIFACTS_DIR, "executor")
                    self.skill_manager.inject_skill(
                        self.state.get("current_phase_raw_id", ""), "executor", self.openclaw_config
                    )
                    self._record_injected_skill("executor")
                    self.state["sentinel_wait_started_at"] = datetime.now(timezone.utc).isoformat()
                    self.transition_state("WAITING_FOR_SENTINEL", f"Invoking Executor ({attempt_label}) - Attempt {retries + 1}")

                    _verify_symlinks_consistent(self.state.get("project_path", ""))
                    webhook_status = invoke_agent_webhook("executor", session_key, token, model=model)

                    if webhook_status != "SUCCESS":
                        self.state["current_agent"] = "escalation"
                        error_reason = "Auth Config Error" if webhook_status == "AUTH_ERROR" else "Webhook infra failure"
                        self.transition_state("RUNNING", error_reason)
                        time.sleep(5)
                        continue

                    # Resolve the active executor session JSONL for idle detection.
                    # sessions.json may take a few seconds to be updated by the gateway.
                    _sessions_dir = os.path.join(OPENCLAW_ROOT, "agents", "executor", "sessions")
                    _sessions_json = os.path.join(_sessions_dir, "sessions.json")
                    _full_key = f"agent:executor:{session_key}".lower()  # openclaw normalizes session keys to lowercase
                    _jsonl_path = None
                    for _ in range(15):  # up to 30s
                        try:
                            with open(_sessions_json) as _sf:
                                _sd = json.load(_sf)
                            _sid = _sd.get(_full_key, {}).get("sessionId")
                            if _sid:
                                _jsonl_path = os.path.join(_sessions_dir, f"{_sid}.jsonl")
                                break
                        except Exception:
                            pass
                        time.sleep(2)

                    _is_dead, _dead_msg = _check_session_dead_on_arrival(_sessions_json, _full_key)
                    if _is_dead:
                        print(f"[ERROR] [EXECUTOR] Session dead on arrival: {_dead_msg}")
                        _ps_dead = self.read_phase_state()
                        _ps_dead["last_error_code"] = "ERR_SESSION_DEAD_ON_ARRIVAL"
                        _ps_dead["escalation_trigger_reason"] = (
                            f"Executor session terminated immediately (provider rejected): {_dead_msg}"
                        )
                        self.write_phase_state_atomic(_ps_dead)
                        self.state["current_agent"] = "escalation"
                        self.transition_state(
                            "RUNNING",
                            f"ERR_SESSION_DEAD_ON_ARRIVAL: {_dead_msg}",
                        )
                        continue

                    _stop_file = os.path.join(PROJECT_ARTIFACTS_DIR, "pipeline_stop_requested")
                    sentinel_found = poll_for_sentinel_with_idle_detect(
                        sentinel_path, _jsonl_path, timeout_seconds=1200,
                        watch_dirs=[_watch_root_for_idle_detect()],
                        min_sentinel_mtime=_attempt_start_time,
                        stop_sentinel_path=_stop_file,
                        idle_threshold=300,  # MiniMax M2.7 can take 2-3 min to generate large responses
                    )

                    # W1-G: Accumulate executor token usage into phase_state across retry attempts.
                    _attempt_tokens = _sum_session_tokens(_jsonl_path)
                    _ps_tok = self.read_phase_state()
                    _executor_tokens_acc = _ps_tok.get("executor_tokens_acc", {})
                    for _k, _v in _attempt_tokens.items():
                        _executor_tokens_acc[_k] = _executor_tokens_acc.get(_k, 0) + _v
                    _ps_tok["executor_tokens_acc"] = _executor_tokens_acc
                    self.write_phase_state_atomic(_ps_tok)

                    # RR-3 (Phase 3): Classify executor terminal state before deciding action.
                    # executor_output_path is .json counterpart to sentinel_path (.done).
                    executor_output_path = os.path.join(PROJECT_ARTIFACTS_DIR, "executor_output.json")
                    outcome = self.classify_executor_outcome(sentinel_found, executor_output_path)
                    print(f"[INFO] [EXECUTOR] Outcome classified: {outcome}")

                    if outcome == "executor_succeeded":
                        gate_passed = self.run_executor_output_gate()
                        if gate_passed:
                            _ps_ex = self.read_phase_state()
                            _ps_ex.pop("last_error_code", None)
                            self.write_phase_state_atomic(_ps_ex)
                            self.state["current_agent"] = "reviewer"
                            self.transition_state("RUNNING", "Executor passed, moving to reviewer")
                            self.wait_for_model_stable()
                            continue
                        else:
                            print("[ERROR] Executor gate failed")
                            _write_pipeline_event("gate_fail", raw_id, "executor", {"exit_code": 1})  # W1-F
                            self.write_failure_context("executor", self.state.get("executor_retries", 0) + 1)
                            # reset_execution("auto") owns the counter increment.
                            self.reset_execution("auto")

                    elif outcome == "executor_preempted":
                        # Output file exists but no .done — executor was interrupted externally.
                        # Attempt gate evaluation on existing output before deciding to reset.
                        print("[WARN] [EXECUTOR] Executor preempted — attempting gate on existing output.")
                        gate_passed = self.run_executor_output_gate()
                        if gate_passed:
                            print("[INFO] [EXECUTOR] Preempted executor output passed gate — treating as succeeded.")
                            _ps_ep = self.read_phase_state()
                            _ps_ep.pop("last_error_code", None)
                            self.write_phase_state_atomic(_ps_ep)
                            self.state["current_agent"] = "reviewer"
                            self.transition_state("RUNNING", "Executor preempted but output valid — moving to reviewer")
                            self.wait_for_model_stable()
                            continue
                        else:
                            # Preemption is an infrastructure event, NOT a code quality failure.
                            # Do NOT consume executor_retries — route directly to escalation.
                            print("[ERROR] [EXECUTOR] Preempted executor output failed gate — escalating (EXECUTOR_PREEMPTED_OUTPUT_INVALID).")
                            self.state["current_agent"] = "escalation"
                            self.transition_state(
                                "RUNNING",
                                "EXECUTOR_PREEMPTED_OUTPUT_INVALID: escalating without consuming executor_retries",
                            )

                    else:  # executor_crashed
                        print("[ERROR] Executor sentinel timeout — classified as executor_crashed.")
                        # reset_execution("auto") owns the counter increment — do not call
                        # increment_executor_retries() separately. Single code path for auto retry.
                        self.reset_execution("auto")
                elif current_agent == "reviewer":
                    phase = self.state.get("current_phase", 0)
                    raw_id = self.state.get("current_phase_raw_id", "unknown")
                    retries = self.state.get("reviewer_retries", 0)
                    session_key = f"pipeline:phase-{phase}:{raw_id}:reviewer-attempt-{retries + 1}"
                    
                    sentinel_path = os.path.join(PROJECT_ARTIFACTS_DIR, "reviewer_output.done")
                    token = self.openclaw_config.get("hooks", {}).get("token", "")
                    
                    _attempt_start_time = time.time()  # captured before cleanup for stale-sentinel guard
                    cleanup_output_files(PROJECT_ARTIFACTS_DIR, "reviewer")
                    self.skill_manager.inject_skill(
                        self.state.get("current_phase_raw_id", ""), "reviewer", self.openclaw_config
                    )
                    self._record_injected_skill("reviewer")
                    self.state["sentinel_wait_started_at"] = datetime.now(timezone.utc).isoformat()
                    self.transition_state("WAITING_FOR_SENTINEL", f"Invoking Reviewer - Attempt {retries + 1}")

                    _verify_symlinks_consistent(self.state.get("project_path", ""))
                    webhook_status = invoke_agent_webhook("reviewer", session_key, token, model=self._get_agent_model("reviewer"))

                    if webhook_status != "SUCCESS":
                        self.state["current_agent"] = "escalation"
                        error_reason = "Auth Config Error" if webhook_status == "AUTH_ERROR" else "Webhook infra failure"
                        self.transition_state("RUNNING", error_reason)
                        time.sleep(5)
                        continue

                    # Resolve the active reviewer session JSONL for idle detection.
                    # Mirrors the executor pattern (sessions.json may take a few seconds
                    # to be updated by the gateway).
                    _rev_sessions_dir = os.path.join(OPENCLAW_ROOT, "agents", "reviewer", "sessions")
                    _rev_sessions_json = os.path.join(_rev_sessions_dir, "sessions.json")
                    _rev_full_key = f"agent:reviewer:{session_key}".lower()
                    _jsonl_path = None
                    for _ in range(15):  # up to 30s
                        try:
                            with open(_rev_sessions_json) as _sf:
                                _sd = json.load(_sf)
                            _sid = _sd.get(_rev_full_key, {}).get("sessionId")
                            if _sid:
                                _jsonl_path = os.path.join(_rev_sessions_dir, f"{_sid}.jsonl")
                                break
                        except Exception:
                            pass
                        time.sleep(2)

                    _is_dead, _dead_msg = _check_session_dead_on_arrival(_rev_sessions_json, _rev_full_key)
                    if _is_dead:
                        print(f"[ERROR] [REVIEWER] Session dead on arrival: {_dead_msg}")
                        _ps_dead = self.read_phase_state()
                        _ps_dead["last_error_code"] = "ERR_SESSION_DEAD_ON_ARRIVAL"
                        _ps_dead["escalation_trigger_reason"] = (
                            f"Reviewer session terminated immediately (provider rejected): {_dead_msg}"
                        )
                        self.write_phase_state_atomic(_ps_dead)
                        self.state["current_agent"] = "escalation"
                        self.transition_state(
                            "RUNNING",
                            f"ERR_SESSION_DEAD_ON_ARRIVAL: {_dead_msg}",
                        )
                        continue

                    _stop_file = os.path.join(PROJECT_ARTIFACTS_DIR, "pipeline_stop_requested")
                    sentinel_found = poll_for_sentinel_with_idle_detect(
                        sentinel_path, _jsonl_path,
                        timeout_seconds=600,
                        watch_dirs=[_watch_root_for_idle_detect()],
                        min_sentinel_mtime=_attempt_start_time,
                        stop_sentinel_path=_stop_file,
                        idle_threshold=300,  # match executor: long gaps between tool/LLM events
                    )
                    
                    # W1-G: Capture reviewer token usage from the resolved session JSONL.
                    _reviewer_tokens = _sum_session_tokens(_jsonl_path)
                    _ps_rev_tok = self.read_phase_state()
                    _ps_rev_tok["reviewer_tokens_acc"] = _reviewer_tokens
                    self.write_phase_state_atomic(_ps_rev_tok)

                    if not sentinel_found:
                        print("[ERROR] Sentinel timeout")
                        _rv_retries = self.increment_reviewer_retries()
                        # Finding E: write failure context on every timeout so operators
                        # and the escalation agent see current state, not stale executor data.
                        # Use _rv_retries (post-increment return value) not state.get() to
                        # avoid a stale read if state write races with the next loop iteration.
                        self.write_failure_context(
                            "reviewer",
                            _rv_retries,
                        )
                        # Finding D: cap at 3 sentinel timeouts → escalation.
                        # Mirrors the planner's retries >= 3 guard (~line 2220).
                        # Uses the return value of increment_reviewer_retries() to
                        # avoid a re-read race with the phase_state write inside that call.
                        if _rv_retries >= 3:
                            self.state["current_agent"] = "escalation"
                            self.transition_state(
                                "RUNNING",
                                f"Reviewer sentinel timeout cap reached ({_rv_retries}): "
                                "reviewer produced no output after 3 attempts",
                            )
                            time.sleep(5)
                        continue

                    gate_result = self.run_reviewer_output_gate()

                    if gate_result != "PASS":
                        _write_pipeline_event("gate_fail", raw_id, "reviewer", {"gate_result": gate_result})  # W1-F
                        self.write_failure_context("reviewer", self.state.get("reviewer_retries", 0) + 1)

                    if gate_result == "PASS":
                        _write_pipeline_event("gate_pass", raw_id, "reviewer", {})  # W1-F
                        _ps_rv = self.read_phase_state()
                        _ps_rv.pop("last_error_code", None)
                        self.write_phase_state_atomic(_ps_rv)
                        self.transition_state("RUNNING", "Reviewer passed, entering Phase 10 Git Operations")

                        # 1. Merge & Commit
                        # Use full raw_id for branch name to avoid int-suffix collision (e.g. INFRA-1 vs UI-1 both = 1)
                        _raw_id = self.state.get("current_phase_raw_id", "")
                        branch = f"phase/{_raw_id}" if _raw_id else f"phase/{phase}"
                        try:
                            configured_base_branch = self.openclaw_config.get("pipeline", {}).get("base_branch", "").strip()
                            base_branch = configured_base_branch if configured_base_branch else _detect_base_branch(SYMLINK_TARGET)

                            subprocess.run(["git", "add", "."], cwd=SYMLINK_TARGET, check=True)

                            # Check if there are changes to commit
                            status_output = subprocess.run(["git", "status", "--porcelain"], cwd=SYMLINK_TARGET, capture_output=True, text=True)
                            if status_output.stdout.strip():
                                _raw_id = self.state.get("current_phase_raw_id", "") or f"phase-{phase}"
                                try:
                                    _cp_data = json.load(open(os.path.join(PROJECT_ARTIFACTS_DIR, "current_phase.json")))
                                    _detail = _cp_data.get("detail", "")
                                    # detail format: "Phase CORE-2: Implement core game logic..."
                                    _goal = _detail.split(": ", 1)[1] if ": " in _detail else _raw_id
                                except Exception:
                                    _goal = _raw_id
                                subprocess.run(["git", "commit", "-m", f"phase({_raw_id}): {_goal}"], cwd=SYMLINK_TARGET, check=True)

                            try:
                                subprocess.run(["git", "checkout", base_branch], cwd=SYMLINK_TARGET, check=True)
                            except subprocess.CalledProcessError:
                                subprocess.run(
                                    ["git", "stash", "push", "--include-untracked"],
                                    cwd=SYMLINK_TARGET,
                                    check=False,
                                )
                                subprocess.run(["git", "checkout", base_branch], cwd=SYMLINK_TARGET, check=True)
                                subprocess.run(["git", "stash", "pop"], cwd=SYMLINK_TARGET, check=False)

                            merge_result = subprocess.run(
                                ["git", "merge", branch, "--no-ff", "-m", f"Merge {branch}"],
                                cwd=SYMLINK_TARGET, capture_output=True
                            )

                            if merge_result.returncode != 0:
                                _merge_stderr = (merge_result.stderr or b"").decode(errors="replace").strip()
                                _merge_reason = _merge_stderr or "git merge failed (no stderr)"
                                print(f"[ERROR] git merge failed on phase {phase}: {_merge_reason}")
                                self.state["current_agent"] = "escalation"
                                self.transition_state(
                                    "RUNNING",
                                    f"Phase {phase} merge failed: {_merge_reason}",
                                )
                                time.sleep(5)
                                continue

                            # 2. Roadmap Update — fold into merge commit atomically (B5).
                            # Write [x] checkbox to roadmap.md in-place, then amend the merge
                            # commit so the checkbox is part of the merge, not a separate commit.
                            # This prevents git checkout -b phase/NEXT from reverting the checkbox.
                            import glob, re
                            roadmap_path = None
                            for ext in ['*.md', '*.yaml', '*.json']:
                                matches = glob.glob(os.path.join(SYMLINK_TARGET, f"*oadmap{ext}")) + glob.glob(os.path.join(SYMLINK_TARGET, f"*Roadmap{ext}"))
                                if matches:
                                    roadmap_path = matches[0]
                                    break

                            if roadmap_path:
                                try:
                                    with open(roadmap_path, 'r') as f:
                                        rmap_lines = f.readlines()
                                    _chk_raw_id = self.state.get("current_phase_raw_id", "")
                                    for i, rline in enumerate(rmap_lines):
                                        rmatch = re.match(r'- \[( |x|-|!)\] `([^`]+)` \|', rline.strip())
                                        if rmatch:
                                            _, phase_id = rmatch.groups()
                                            # Prefer exact raw_id match — avoids collision when multiple phases
                                            # share the same trailing integer (e.g. INFRA-1, CORE-1, UI-1 all → 1).
                                            if _chk_raw_id:
                                                if phase_id == _chk_raw_id:
                                                    rmap_lines[i] = rline.replace('- [ ]', '- [x]').replace('- [!]', '- [x]')
                                                    break
                                            else:
                                                parts = phase_id.split('-')
                                                phase_num = int(parts[-1]) if len(parts) > 1 and parts[-1].isdigit() else 0
                                                if phase_num == phase:
                                                    rmap_lines[i] = rline.replace('- [ ]', '- [x]').replace('- [!]', '- [x]')
                                                    break
                                    with open(roadmap_path, 'w') as f:
                                        f.writelines(rmap_lines)
                                    # Fold checkbox update into the merge commit atomically.
                                    subprocess.run(["git", "add", roadmap_path], cwd=SYMLINK_TARGET, check=True)
                                    subprocess.run(["git", "commit", "--amend", "--no-edit"], cwd=SYMLINK_TARGET, check=True)
                                    print(f"[INFO] Roadmap checkbox for {_chk_raw_id or phase} folded into merge commit.")
                                except subprocess.CalledProcessError:
                                    raise  # let outer except handle git failures
                                except Exception as e:
                                    print(f"[ERROR] Failed to update roadmap: {e}")
                                    # Non-blocking — tag proceeds even if roadmap file write fails

                            _tag_id = self.state.get("current_phase_raw_id", "") or phase
                            # Use --force so the tag moves to the new commit on phase re-runs
                            # rather than failing with exit 128 when the tag already exists.
                            subprocess.run(["git", "tag", "--force", f"phase-{str(_tag_id).lower()}-complete"], cwd=SYMLINK_TARGET, check=False)
                        except subprocess.CalledProcessError as e:
                            print(f"[ERROR] Git operation failed: {e}")
                            self.state["current_agent"] = "escalation"
                            self.transition_state("RUNNING", f"Git operation failed on Phase {phase}: {str(e)}")
                            time.sleep(5)
                            continue
                                
                        # 3. Suggestions Append
                        reviewer_output_path = os.path.join(PROJECT_ARTIFACTS_DIR, "reviewer_output.json")
                        if os.path.exists(reviewer_output_path):
                            try:
                                with open(reviewer_output_path, 'r') as f:
                                    rev_out = json.load(f)
                                suggestions = rev_out.get("suggestions", [])
                                if suggestions:
                                    sugg_path = os.path.join(PROJECT_ARTIFACTS_DIR, "suggestions.md")
                                    with open(sugg_path, 'a') as f:
                                        f.write(f"\n## Phase {phase} Suggestions\n")
                                        for s in suggestions:
                                            f.write(f"- {s}\n")
                            except Exception as e:
                                print(f"[ERROR] Failed to append suggestions: {e}")
                                
                        # 3.1 Canonical metrics row — deduplicate and write one authoritative row
                        # after the merge so that reviewer-rejection retries (which cause the
                        # executor to append extra rows) produce exactly one row per phase.
                        _metrics_path = os.path.join(PROJECT_ARTIFACTS_DIR, "metrics.jsonl")
                        _raw_id_for_metrics = self.state.get("current_phase_raw_id", "")
                        try:
                            # Compute duration_seconds from phase_start_time written at phase start.
                            _duration_seconds = None
                            _phase_start_time = self.state.get("phase_start_time")
                            if _phase_start_time:
                                try:
                                    _start_dt = datetime.fromisoformat(_phase_start_time)
                                    _duration_seconds = int(time.time() - _start_dt.timestamp())
                                except Exception:
                                    pass

                            # Read goal from current_phase.json (still present before step 4 cleanup).
                            # detail already contains "Phase X: ..." prefix so use it directly.
                            _goal_text = ""
                            _cp_path_m = os.path.join(PROJECT_ARTIFACTS_DIR, "current_phase.json")
                            if os.path.exists(_cp_path_m):
                                try:
                                    with open(_cp_path_m, 'r') as _f_m:
                                        _cp_data = json.load(_f_m)
                                    _goal_text = _cp_data.get('detail', '')
                                except Exception:
                                    pass

                            # Final retry counts come from pipeline_state (not phase_state which was cleared).
                            _executor_attempts = self.state.get("executor_retries", 0) + 1
                            _reviewer_passes = self.state.get("reviewer_retries", 0) + 1

                            # W1-A/B/C: Read phase_state for blame_fires, escalations, skill_used.
                            # phase_state.json is present until step 4 cleanup below.
                            _ps_m = {}
                            if os.path.exists(PHASE_STATE_FILE):
                                try:
                                    with open(PHASE_STATE_FILE, 'r') as _f_psm:
                                        _ps_m = json.load(_f_psm)
                                except Exception:
                                    pass

                            # Read existing metrics, strip rows for this phase, append canonical row.
                            _existing_rows = []
                            if os.path.exists(_metrics_path):
                                with open(_metrics_path, 'r') as _f_m:
                                    for _line in _f_m:
                                        _line = _line.strip()
                                        if not _line:
                                            continue
                                        try:
                                            _row = json.loads(_line)
                                            if _row.get("phase") != _raw_id_for_metrics:
                                                _existing_rows.append(_line)
                                        except json.JSONDecodeError:
                                            _existing_rows.append(_line)

                            _planner_tok = _ps_m.get("planner_tokens_acc", {})
                            _executor_tok = _ps_m.get("executor_tokens_acc", {})
                            _reviewer_tok = _ps_m.get("reviewer_tokens_acc", {})
                            _canonical_row = json.dumps({
                                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "phase": _raw_id_for_metrics,
                                "goal": _goal_text,
                                "executor_attempts": _executor_attempts,
                                "reviewer_passes": _reviewer_passes,
                                "blame_fires": _ps_m.get("blame_fires", 0),   # W1-A
                                "escalations": _ps_m.get("escalations", 0),   # W1-B
                                "skill_used": _ps_m.get("skill_injected"),     # W1-C
                                "blame_verdict": _ps_m.get("blame_verdict"),   # null when no blame fired
                                "planner_tokens": _planner_tok,                # W1-G
                                "executor_tokens": _executor_tok,              # W1-G
                                "reviewer_tokens": _reviewer_tok,              # W1-G
                                "cost_total": round(                           # W1-G
                                    _planner_tok.get("cost_total", 0.0)
                                    + _executor_tok.get("cost_total", 0.0)
                                    + _reviewer_tok.get("cost_total", 0.0),
                                    6,
                                ),
                                "duration_seconds": _duration_seconds,
                            })

                            _metrics_dir = os.path.dirname(_metrics_path) or PROJECT_ARTIFACTS_DIR
                            _fd_m, _tmp_m = tempfile.mkstemp(dir=_metrics_dir, prefix=".metrics_")
                            try:
                                with os.fdopen(_fd_m, 'w') as _f_m:
                                    for _row_line in _existing_rows:
                                        _f_m.write(_row_line + '\n')
                                    _f_m.write(_canonical_row + '\n')
                                os.replace(_tmp_m, _metrics_path)
                            except Exception:
                                if os.path.exists(_tmp_m):
                                    os.remove(_tmp_m)
                                raise

                            _write_pipeline_event(  # W1-F
                                "phase_complete", _raw_id_for_metrics, "reviewer",
                                {"executor_attempts": _executor_attempts, "blame_fires": _ps_m.get("blame_fires", 0)},
                            )
                            print(
                                f"[INFO] Canonical metrics row written for {_raw_id_for_metrics}: "
                                f"{_executor_attempts} executor attempt(s), "
                                f"{_reviewer_passes} reviewer pass(es), "
                                f"{_duration_seconds}s duration"
                            )
                        except Exception as _metrics_err:
                            print(f"[ERROR] Failed to write canonical metrics row: {_metrics_err}")

                        # 3.5 Audit Archive
                        import shutil
                        archive_project_name = os.path.basename(os.path.realpath(SYMLINK_TARGET)) if os.path.exists(SYMLINK_TARGET) else "unknown-project"
                        _audit_id = self.state.get("current_phase_raw_id", "") or f"phase-{phase}"
                        _audit_flag = os.environ.get("AUTODEV_AUDIT_ARCHIVE_DIR")
                        if _audit_flag is None:
                            _audit_base = os.path.join(OPENCLAW_ROOT, "pipeline-audit")
                        elif _audit_flag.strip() == "":
                            _audit_base = None
                        else:
                            _audit_base = os.path.expanduser(_audit_flag.strip())
                        archive_dir = (
                            os.path.join(_audit_base, archive_project_name, _audit_id.lower())
                            if _audit_base
                            else None
                        )
                        if archive_dir:
                            try:
                                os.makedirs(archive_dir, exist_ok=True)
                                files_to_archive = [
                                    "current_phase.json",
                                    "phase_state.json",
                                    "planner_output.json",
                                    "executor_output.json",
                                    "reviewer_output.json",
                                ]
                                for filename in files_to_archive:
                                    src = os.path.join(PROJECT_ARTIFACTS_DIR, filename)
                                    if os.path.exists(src):
                                        shutil.copy2(src, os.path.join(archive_dir, filename))
                                print(f"[INFO] Audit archive written to {archive_dir}")
                            except Exception as e:
                                error_msg = (
                                    f"[WARNING] INFORMATIONAL: Audit archive failed for phase {phase}: {e}"
                                )
                                print(error_msg)
                        else:
                            print(
                                "[INFO] Audit archive skipped "
                                "(AUTODEV_AUDIT_ARCHIVE_DIR is set to empty string)"
                            )
                            
                        # 4. Working File Cleanup and Loop Back
                        targets = [
                            "phase_state.json", "planner_output.json", "planner_output.done",
                            "executor_output.json", "executor_output.done",
                            "reviewer_output.json", "reviewer_output.done",
                            "current_phase.json", "failure_context.json",
                            "executor_gate_detail.json",
                        ]
                        for t in targets:
                            try:
                                os.remove(os.path.join(PROJECT_ARTIFACTS_DIR, t))
                            except FileNotFoundError:
                                pass
                                
                        print(f"[INFO] Phase {phase} complete. Looping back to identify next phase.")
                        self.state["current_agent"] = "planner"  # reset to start
                        self.state["current_phase"] = 0
                        self.state["current_phase_raw_id"] = ""
                        # Actually, phase identification is a pure script. Let's run it.
                        gate_script = os.path.join(AUTODEV_REPO_PATH, "autodev", "pipeline", "gate_scripts", "phase_resolver.py")
                        try:
                            # Pass nothing to use default locator
                            result = subprocess.run([sys.executable, gate_script], capture_output=True, text=True)
                            output = result.stdout.strip()
                            if result.returncode == 0 and "PENDING: Phase" in output:
                                # Start next phase correctly
                                # The current_phase.json is written by phase_resolver.py
                                if os.path.exists(os.path.join(PROJECT_ARTIFACTS_DIR, "current_phase.json")):
                                    with open(os.path.join(PROJECT_ARTIFACTS_DIR, "current_phase.json"), 'r') as f:
                                        new_phase = json.load(f)
                                    self.state["current_phase"] = new_phase.get("phase_number", 0)
                                    self.state["current_phase_raw_id"] = new_phase.get("raw_id", "")
                                    self.state["planner_retries"] = 0
                                    self.state["executor_retries"] = 0
                                    self.state["reviewer_retries"] = 0
                                    # Record start time for the new phase so post-merge can compute duration_seconds.
                                    self.state["phase_start_time"] = datetime.now(timezone.utc).isoformat()
                                    # phase_state.json is deleted at phase end; it will be
                                    # re-created with escalation_resets=0 on first use in new phase.

                                    # Capture HEAD before branch creation — stored as phase_base_commit
                                    # so reset_phase() can rewind to the pre-phase state.
                                    _base_result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=SYMLINK_TARGET, capture_output=True, text=True)
                                    if _base_result.returncode == 0:
                                        self.state["phase_base_commit"] = _base_result.stdout.strip()

                                    # Checkout new phase branch — use raw_id to avoid int-suffix collisions
                                    _next_raw = self.state.get("current_phase_raw_id", "")
                                    branch = f"phase/{_next_raw}" if _next_raw else f"phase/{self.state['current_phase']}"
                                    try:
                                        subprocess.run(f"git checkout {branch} 2>/dev/null || git checkout -b {branch}", shell=True, cwd=SYMLINK_TARGET, check=True)
                                    except subprocess.CalledProcessError as e:
                                        print(f"[ERROR] Failed to checkout new phase branch: {e}")

                                    self.transition_state("RUNNING", f"Started Phase {self.state['current_phase']}")
                                    time.sleep(2)
                                    continue
                            elif result.returncode == 0 and "PIPELINE_COMPLETE" in output:
                                print("[INFO] Pipeline fully complete!")
                                self.state["current_phase_raw_id"] = ""
                                self.state["current_agent"] = None
                                _write_run_summary("PIPELINE_COMPLETE", "Pipeline fully complete")  # W2-B
                                self.transition_state("PIPELINE_COMPLETE", "Pipeline fully complete")
                                # Queue integration: mark entry COMPLETED and auto-advance
                                self._queue_update_active_entry(
                                    "COMPLETED",
                                    {"completed_at": datetime.now(timezone.utc).isoformat()}
                                )
                                queue_data = self._read_queue()
                                if queue_data["queue"] and queue_data.get("queue_mode", "auto") == "auto":
                                    advanced = self._select_next_queue_project(halt_if_no_eligible=False)
                                    if advanced:
                                        continue  # restart loop for the new project
                                break
                            elif result.returncode == 2 and "BLOCKED" in output:
                                print(f"[INFO] Roadmap blocked. Halting.")
                                _blk = datetime.now(timezone.utc).isoformat()
                                _write_run_summary("BLOCKED", "Roadmap blocked")  # W2-B
                                self.transition_state("BLOCKED", "Roadmap blocked")
                                self._queue_park_active_entry(
                                    "BLOCKED",
                                    "roadmap_blocked",
                                    {"blocked_at": _blk},
                                )
                                if self._queue_after_park_maybe_advance():
                                    continue
                                break
                        except subprocess.CalledProcessError as e:
                            print(f"[ERROR] Roadmap parser failed: {e}")
                            
                        break
                    elif gate_result == "ROUTE_EXECUTOR":
                        self.set_reviewer_rejected()
                        self.increment_reviewer_retries()
                        self.state["current_agent"] = "executor"
                        self.state["executor_retries"] = 0
                        self.transition_state("RUNNING", "Reviewer ROUTE_EXECUTOR: re-invoking executor with blocking issues")
                        time.sleep(5)
                        continue

                    elif gate_result == "ROUTE_PLANNER":
                        self.increment_reviewer_retries()
                        # RR-2 (Phase 4): Clear planner_output_preserved so crash-recovery skip
                        # does not fire on this intentional re-run (ROUTE_PLANNER explicitly
                        # wants fresh planner output — do not re-use what the reviewer rejected).
                        _ps_rp = self.read_phase_state()
                        _ps_rp["planner_output_preserved"] = False
                        self.write_phase_state_atomic(_ps_rp)
                        self.state["planner_output_preserved"] = False
                        self.state["current_agent"] = "planner"
                        self.state["executor_retries"] = 0
                        self.state["planner_retries"] = 0
                        self.transition_state("RUNNING", "Reviewer ROUTE_PLANNER: re-invoking planner with failure context")
                        time.sleep(5)
                        continue

                    elif gate_result == "ROUTE_ESCALATE":
                        self.increment_reviewer_retries()
                        self.state["current_agent"] = "escalation"
                        self.transition_state("RUNNING", "Reviewer ROUTE_ESCALATE: escalating after 3 failed passes")
                        time.sleep(5)
                        continue

                    elif gate_result == "MISSING_ARTIFACTS":
                        # Done-criteria artifacts absent (phases/{id}.md or metrics.jsonl).
                        # Re-invoke executor with a specific instruction to produce them.
                        # Does NOT consume reviewer_retries — separate reviewer_artifacts_retries counter.
                        _ps_ma = self.read_phase_state()
                        _ma_retries = _ps_ma.get("reviewer_artifacts_retries", 0) + 1
                        _ps_ma["reviewer_artifacts_retries"] = _ma_retries
                        self.write_phase_state_atomic(_ps_ma)
                        print(f"[WARN] Reviewer gate: MISSING_ARTIFACTS (attempt {_ma_retries}/2).")
                        if _ma_retries >= 2:
                            print("[WARN] MISSING_ARTIFACTS retry cap reached — escalating.")
                            self.state["current_agent"] = "escalation"
                            self.transition_state(
                                "RUNNING",
                                f"Reviewer MISSING_ARTIFACTS: artifact retry cap reached ({_ma_retries})",
                            )
                        else:
                            # Re-invoke executor with mandatory artifact instruction.
                            _raw_id = self.state.get("current_phase_raw_id", "this phase")
                            _artifact_instruction = (
                                f"MISSING COMPLETION ARTIFACTS: Before writing executor_output.done, "
                                f"you MUST produce two mandatory artifacts: "
                                f"(1) Write the phase archive to .autodev/pipeline/phases/{_raw_id}.md using the format "
                                f"in your AGENTS.md. "
                                f"(2) Append a metrics row to .autodev/pipeline/metrics.jsonl using the format in your "
                                f"AGENTS.md. Write the archive first, metrics second, sentinel last."
                            )
                            _ps_ma["artifact_instruction"] = _artifact_instruction
                            self.write_phase_state_atomic(_ps_ma)
                            self.state["current_agent"] = "executor"
                            self.state["executor_retries"] = 0
                            self.transition_state(
                                "RUNNING",
                                f"Reviewer MISSING_ARTIFACTS: re-invoking executor to produce "
                                f".autodev/pipeline/phases/{_raw_id}.md and .autodev/pipeline/metrics.jsonl",
                            )
                        time.sleep(5)
                        continue

                    elif gate_result == "INFRA_FAILURE":
                        # RR-1 (Phase 1): Infrastructure failure — LLM did not produce valid output.
                        # Do NOT increment reviewer_retries — this is NOT a code quality rejection.
                        # Discriminate by model health: unhealthy → recovery + retry; healthy → soft retry.
                        print("[WARN] Reviewer gate returned INFRA_FAILURE — classifying by model health.")
                        _ps_if = self.read_phase_state()

                        if self.check_traffic_cop_health():
                            # Model is healthy: empty/fluke response.  Soft retry with cap.
                            _infra_soft = _ps_if.get("reviewer_infra_retries", 0) + 1
                            _ps_if["reviewer_infra_retries"] = _infra_soft
                            self.write_phase_state_atomic(_ps_if)
                            print(f"[WARN] Reviewer INFRA_FAILURE (healthy model) — soft retry {_infra_soft}/3.")
                            if _infra_soft >= 3:
                                self.state["current_agent"] = "escalation"
                                self.transition_state(
                                    "RUNNING",
                                    f"Reviewer INFRA_FAILURE: soft retry cap reached ({_infra_soft}): INFRA_FAILURE_SOFT_RETRY_EXHAUSTED",
                                )
                            else:
                                self.state["current_agent"] = "reviewer"
                                self.transition_state(
                                    "RUNNING",
                                    f"Reviewer INFRA_FAILURE soft retry {_infra_soft} — re-invoking reviewer",
                                )
                            time.sleep(5)
                            continue

                        else:
                            # Model is unhealthy.  Check recovery cooldown (10 min).
                            _attempted = _ps_if.get("reviewer_infra_recovery_attempted", False)
                            _ts_str = _ps_if.get("reviewer_infra_recovery_timestamp", "")
                            _within_cooldown = False
                            if _attempted and _ts_str:
                                try:
                                    _ts = datetime.fromisoformat(_ts_str)
                                    _elapsed = (datetime.now(timezone.utc) - _ts).total_seconds()
                                    _within_cooldown = _elapsed < 600  # 10 minutes
                                except Exception:
                                    pass

                            if _within_cooldown:
                                # Recovery was attempted recently — do not re-invoke SSH.
                                _rec_attempts = _ps_if.get("reviewer_infra_recovery_attempts", 0) + 1
                                _ps_if["reviewer_infra_recovery_attempts"] = _rec_attempts
                                self.write_phase_state_atomic(_ps_if)
                                print(f"[WARN] Reviewer INFRA_FAILURE within recovery cooldown — attempt {_rec_attempts}/2.")
                                if _rec_attempts >= 2:
                                    self.state["current_agent"] = "escalation"
                                    self.transition_state(
                                        "RUNNING",
                                        f"Reviewer INFRA_FAILURE: recovery cap reached ({_rec_attempts}): INFRA_FAILURE_RECOVERY_EXHAUSTED",
                                    )
                                else:
                                    self.wait_for_model_stable()
                                    self.state["current_agent"] = "reviewer"
                                    self.transition_state(
                                        "RUNNING",
                                        f"Reviewer INFRA_FAILURE recovery cooldown — re-invoking reviewer (attempt {_rec_attempts})",
                                    )
                                time.sleep(5)
                                continue

                            else:
                                # Recovery not attempted (or cooldown expired) — invoke recovery script.
                                _now_ts = datetime.now(timezone.utc).isoformat()
                                _ps_if["reviewer_infra_recovery_attempted"] = True
                                _ps_if["reviewer_infra_recovery_timestamp"] = _now_ts
                                self.write_phase_state_atomic(_ps_if)
                                print("[INFO] Reviewer INFRA_FAILURE — model unhealthy, invoking recovery via SSH.")

                                _rec_cfg = self.openclaw_config.get("recovery", {})
                                _key_path = _rec_cfg.get("key_path", "")
                                _user = _rec_cfg.get("user", "")
                                _host = _rec_cfg.get("host", "")
                                _recovery_exit_code = 1  # default: failed

                                try:
                                    _ssh_result = subprocess.run(
                                        [
                                            "ssh",
                                            "-i", _key_path,
                                            "-o", "StrictHostKeyChecking=no",
                                            "-o", "ConnectTimeout=10",
                                            f"{_user}@{_host}",
                                            "recovery",
                                        ],
                                        timeout=70,
                                        check=False,
                                    )
                                    _recovery_exit_code = _ssh_result.returncode
                                except subprocess.TimeoutExpired:
                                    _recovery_exit_code = 1
                                    print("[ERROR] Recovery SSH call timed out after 70s.")
                                except Exception as _ssh_err:
                                    _recovery_exit_code = 1
                                    print(f"[ERROR] Recovery SSH call failed: {_ssh_err}")

                                _ps_after = self.read_phase_state()
                                _ps_after["reviewer_infra_recovery_exit_code"] = _recovery_exit_code

                                if _recovery_exit_code == 0:
                                    print("[INFO] Recovery completed (exit 0). Waiting for model stable.")
                                    _ps_after["reviewer_infra_recovery_succeeded"] = True
                                    self.write_phase_state_atomic(_ps_after)
                                    self.wait_for_model_stable()
                                    self.state["current_agent"] = "reviewer"
                                    self.transition_state(
                                        "RUNNING",
                                        "Reviewer INFRA_FAILURE — recovery succeeded (exit 0), re-invoking reviewer",
                                    )
                                elif _recovery_exit_code == 2:
                                    print("[INFO] Recovery skipped — service already healthy (exit 2). Proceeding.")
                                    _ps_after["reviewer_infra_recovery_succeeded"] = True
                                    self.write_phase_state_atomic(_ps_after)
                                    self.wait_for_model_stable()
                                    self.state["current_agent"] = "reviewer"
                                    self.transition_state(
                                        "RUNNING",
                                        "Reviewer INFRA_FAILURE — recovery skipped/healthy (exit 2), re-invoking reviewer",
                                    )
                                else:
                                    print(f"[ERROR] Recovery failed (exit {_recovery_exit_code}). Escalating.")
                                    _ps_after["reviewer_infra_recovery_succeeded"] = False
                                    self.write_phase_state_atomic(_ps_after)
                                    self.state["current_agent"] = "escalation"
                                    self.transition_state(
                                        "RUNNING",
                                        f"Reviewer INFRA_FAILURE — recovery failed (exit {_recovery_exit_code}): INFRA_FAILURE_RECOVERY_FAILED",
                                    )

                                time.sleep(5)
                                continue

                elif current_agent == "escalation":
                    if self._should_invoke_escalation_agent():
                        phase = self.state.get("current_phase", 0)
                        raw_id = self.state.get("current_phase_raw_id", "unknown")
                        session_key = f"pipeline:phase-{phase}:{raw_id}:escalation"
                        token = self.openclaw_config.get("hooks", {}).get("token", "")
                        
                        cleanup_output_files(PROJECT_ARTIFACTS_DIR, "escalation")
                        _ps = self.read_phase_state()
                        _ps["escalation_trigger_reason"] = self.state.get("last_action", "escalation triggered")
                        _ps["escalations"] = _ps.get("escalations", 0) + 1  # W1-B
                        _ps["waiting_for_human_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # W1-E
                        self.write_phase_state_atomic(_ps)
                        _write_pipeline_event("escalation_trigger", raw_id, "escalation", {"reason": _ps.get("escalation_trigger_reason")})  # W1-F
                        self.transition_state("WAITING_FOR_HUMAN", "Invoking Escalation Agent")
                        self._queue_park_active_entry("ESCALATION", "escalation")
                        webhook_status = invoke_agent_webhook("escalation", session_key, token)

                        if webhook_status != "SUCCESS":
                            print("[ERROR] Escalation agent webhook failed. Attempting raw signal.")
                            raw_payload = {
                                "channel": "signal",
                                "message": f"Pipeline failed at Phase {phase}. Last action: {self.state.get('last_action')}"
                            }
                            try:
                                headers = {"Authorization": f"Bearer {token}"}
                                r = requests.post("http://localhost:18789/hooks/agent", json=raw_payload, headers=headers)
                                r.raise_for_status()
                            except Exception as e:
                                print(f"[ERROR] Raw signal failed: {e}")
                                error_data = {
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                    "phase": phase,
                                    "gate": "escalation",
                                    "original_failure_reason": self.state.get("last_action")
                                }
                                with open(os.path.join(PROJECT_ARTIFACTS_DIR, "escalation_failed.json"), "w") as f:
                                    json.dump(error_data, f)
                                _write_run_summary("HALTED_SILENT", "Escalation delivery failed")  # W2-B
                                self.transition_state("HALTED_SILENT", "Escalation delivery failed")
                                self._queue_update_active_entry(
                                    "FAILED",
                                    {"failed_at": datetime.now(timezone.utc).isoformat()},
                                )
                                break
                        if self._queue_after_park_maybe_advance():
                            continue
                    else:
                        out_path = self._poll_escalation_output_json_path(timeout_seconds=10)
                        if out_path:
                            try:
                                with open(out_path, "r") as f:
                                    cmd_data = json.load(f)
                                command = cmd_data.get("command", "").upper()
                            except Exception:
                                command = "STOP"

                            _summary = self._read_escalation_summary()
                            _ps = self.read_phase_state()
                            if _summary:
                                _ps["escalation_message"] = _summary
                            _ps["waiting_for_human_resolved_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # W1-E
                            self.write_phase_state_atomic(_ps)

                            _write_pipeline_event("escalation_resolve", raw_id, "escalation", {"command": command})  # W1-F
                            print(f"[INFO] Human command received: {command}")
                            _esc_root = os.path.dirname(out_path)
                            cleanup_output_files(_esc_root, "escalation")
                            
                            if command == "RETRY":
                                # Used by StoppedRecoveryPanel resume flow (STOPPED → WAITING_FOR_HUMAN → RETRY).
                                # Not shown in the escalation agent UI panel.
                                self._queue_restore_parked_entry_to_active()
                                last_action = self.state.get("last_action", "")
                                if "planner" in last_action.lower(): self.state["current_agent"] = "planner"
                                elif "executor" in last_action.lower(): self.state["current_agent"] = "executor"
                                elif "reviewer" in last_action.lower(): self.state["current_agent"] = "reviewer"
                                else: self.state["current_agent"] = "planner"
                                self.transition_state("RUNNING", "RETRY: resuming from last known agent")
                            elif command in ("RESET_PHASE", "RESTART PHASE"):
                                # RESTART PHASE is a legacy alias — remove once confirmed no
                                # in-flight Signal conversations still reference it.
                                # Both map to the same capped reset path.
                                self._queue_restore_parked_entry_to_active()
                                _ps = self.read_phase_state()
                                if _ps.get("escalation_resets", 0) >= 3:
                                    self.send_signal_notification(
                                        "Escalation reset cap reached (3). Human PROCEED required to advance past this phase."
                                    )
                                else:
                                    _ps["escalation_resets"] = _ps.get("escalation_resets", 0) + 1
                                    # FIND-ESCALATION-CAP: log reason for this reset.
                                    _reason = _ps.get("last_error_code", "unknown")
                                    _entry = {
                                        "reset_number": _ps["escalation_resets"],
                                        "command": "RESET_PHASE",
                                        "reason": _reason,
                                        "timestamp": datetime.now(timezone.utc).isoformat(),
                                    }
                                    _ps.setdefault("reset_log", []).append(_entry)
                                    self.write_phase_state_atomic(_ps)
                                    self.reset_phase()
                            elif command == "RESET_EXECUTION":
                                self._queue_restore_parked_entry_to_active()
                                _ps = self.read_phase_state()
                                if _ps.get("escalation_resets", 0) >= 3:
                                    self.send_signal_notification(
                                        "Escalation reset cap reached (3). Human PROCEED required to advance past this phase."
                                    )
                                else:
                                    # escalation_resets is incremented inside reset_execution("escalation")
                                    self.reset_execution(caller="escalation")
                            elif command == "RESET_REVIEWER":
                                self._queue_restore_parked_entry_to_active()
                                _ps = self.read_phase_state()
                                if _ps.get("escalation_resets", 0) >= 3:
                                    self.send_signal_notification(
                                        "Escalation reset cap reached (3). Human PROCEED required to advance past this phase."
                                    )
                                else:
                                    # escalation_resets is incremented inside reset_reviewer()
                                    self.reset_reviewer()
                            elif command == "SKIP":
                                self._queue_restore_parked_entry_to_active()
                                _skip_raw = self.state.get("current_phase_raw_id", "")
                                if _skip_raw:
                                    self._mark_roadmap_phase(_skip_raw, "-")
                                self.state["current_agent"] = "planner"
                                self.state["current_phase"] = 0
                                self.transition_state("RUNNING", "Manual SKIP triggered")
                            elif command == "PROCEED":
                                self._queue_restore_parked_entry_to_active()
                                _proc_raw = self.state.get("current_phase_raw_id", "") or str(self.state.get("current_phase", ""))
                                if _proc_raw:
                                    self._mark_roadmap_phase(_proc_raw, "x")
                                subprocess.run(["git", "tag", "--force", f"phase-{_proc_raw.lower()}-complete"], cwd=SYMLINK_TARGET, check=False)
                                self.state["current_agent"] = "planner"
                                self.state["current_phase"] = 0
                                self.transition_state("RUNNING", "Manual PROCEED triggered")
                            elif command == "STOP":
                                stop_file = os.path.join(PROJECT_ARTIFACTS_DIR, "pipeline_stop_requested")
                                try:
                                    with open(stop_file, 'w') as _sf:
                                        _sf.write("")
                                except OSError as _e:
                                    print(f"[WARN] STOP: could not write stop sentinel: {_e}")
                                _write_run_summary("STOPPED", "Stop command received via escalation panel")  # W2-B
                                self.transition_state("STOPPED", "Stop command received via escalation panel")
                                break
                            else:
                                _write_run_summary("HALTED_SILENT", f"Unrecognised escalation command: {command}")  # W2-B
                                self.transition_state("HALTED_SILENT", f"Unrecognised escalation command: {command}")
                                self._queue_update_active_entry(
                                    "FAILED",
                                    {"failed_at": datetime.now(timezone.utc).isoformat()},
                                )
                                break
                        else:
                            time.sleep(5)
                else:
                    print(f"[INFO] Agent {current_agent} logic not reached in this phase implementation. Breaking.")
                    break
                    
            print("\n[INFO] Run complete.")
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[CRITICAL] Unhandled exception in event loop:\n{tb}")
            exc_description = f"{type(e).__name__}: {e}"
            self.state["last_action"] = f"UNHANDLED_EXCEPTION: {exc_description}"
            self.write_state()  # atomic write; lock still held
            try:
                phase = self.state.get("current_phase", 0)
                raw_id = self.state.get("current_phase_raw_id", "unknown")
                session_key = f"pipeline:phase-{phase}:{raw_id}:exception-escalation"
                token = self.openclaw_config.get("hooks", {}).get("token", "")
                webhook_status = invoke_agent_webhook("escalation", session_key, token)
                if webhook_status == "SUCCESS":
                    _ps = self.read_phase_state()
                    _ps["escalation_trigger_reason"] = f"Escalated after unhandled exception: {exc_description}"
                    self.write_phase_state_atomic(_ps)
                    self.transition_state(
                        "WAITING_FOR_HUMAN",
                        f"Escalated after unhandled exception: {exc_description}",
                    )
                else:
                    raise RuntimeError(f"Escalation webhook failed: {webhook_status}")
            except Exception as escalation_err:
                print(f"[CRITICAL] Escalation also failed: {escalation_err}")
                error_data = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "phase": self.state.get("current_phase", 0),
                    "exception": exc_description,
                    "escalation_error": str(escalation_err),
                }
                try:
                    with open(os.path.join(OPENCLAW_ROOT, "escalation_failed.json"), "w") as f:
                        json.dump(error_data, f)
                except Exception:
                    pass
                self.transition_state(
                    "HALTED_SILENT",
                    f"HALTED after unhandled exception and escalation failure: {exc_description}",
                )
                self._queue_update_active_entry(
                    "FAILED",
                    {"failed_at": datetime.now(timezone.utc).isoformat()},
                )
        finally:
            self.release_lock()


def _realpath_safe(path: str) -> str:
    if not path or not str(path).strip():
        return ""
    try:
        return os.path.realpath(os.path.expanduser(str(path).strip()))
    except OSError:
        return os.path.abspath(os.path.expanduser(str(path).strip()))


def apply_cli_project_path(orchestrator, new_target: str) -> None:
    """Apply ``--project-path``: reset state when switching projects.

    Symlink-only comparison is insufficient: preflight/UI may already point
    ``pipeline-project`` at the new repo while ``pipeline_state.json`` still
    holds the previous ``project_path`` and terminal ``pipeline_status`` (e.g.
    PIPELINE_COMPLETE). Always compare requested path to on-disk
    ``project_path`` after loading state.
    """
    new_target = os.path.abspath(os.path.expanduser(new_target))
    new_target_real = _realpath_safe(new_target)

    disk_state: dict = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                disk_state = json.load(f)
        except Exception:
            disk_state = {}

    state_pp = (disk_state.get("project_path") or "").strip()
    state_project_real = _realpath_safe(state_pp)

    # Only compare on-disk project_path to the CLI target. Symlink may be stale while
    # state still matches — we fix the symlink below without wiping resume state.
    project_switch = state_project_real != new_target_real

    if project_switch:
        print(f"[INFO] Project switch detected (state or symlink → {new_target}).")
        print("[INFO] Resetting pipeline_state.json for new project.")
        orchestrator.state = {
            "current_phase": 0,
            "current_phase_raw_id": "",
            "current_agent": "planner",
            "planner_retries": 0,
            "executor_retries": 0,
            "reviewer_retries": 0,
            "last_action": "initialized for new project",
            "last_action_timestamp": datetime.now(timezone.utc).isoformat(),
            "pipeline_status": "RUNNING",
            "project_path": new_target,
        }
    else:
        if disk_state:
            orchestrator.state = disk_state
        orchestrator.state["project_path"] = new_target

    # Update symlink before writing state: if the symlink fails the on-disk state
    # must not be updated, otherwise the orchestrator starts with the new project
    # path in state but agents still reading the old symlink target.
    if not orchestrator.update_symlink(new_target):
        print(f"[ERROR] Symlink update failed for {new_target!r} — not committing new project state.")
        return
    orchestrator.write_state()


if __name__ == "__main__":
    # Configure logging before anything else so cleanup_stranded_temp_files()
    # and all startup INFO messages reach stdout (not silently discarded).
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler()],
    )

    # Self-diagnosis: log the resolved roots so operators can spot a
    # misconfigured env without grepping environment dumps.
    logging.info(
        "[STARTUP] OPENCLAW_ROOT=%s AUTODEV_PIPELINE_ROOT=%s STATE_FILE=%s",
        OPENCLAW_ROOT, AUTODEV_PIPELINE_ROOT, STATE_FILE,
    )

    import argparse
    parser = argparse.ArgumentParser(description="OpenClaw Pipeline Orchestrator")
    parser.add_argument(
        "--project-path",
        dest="project_path",
        help="Absolute path to the project directory to run the pipeline on. "
             "If the path differs from the current symlink target the pipeline state "
             "is automatically reset so the new project starts clean.",
    )
    args = parser.parse_args()

    orchestrator = Orchestrator()

    if args.project_path:
        apply_cli_project_path(orchestrator, args.project_path)

    orchestrator.run()
