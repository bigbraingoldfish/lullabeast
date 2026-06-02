import os
import sys
import fcntl
import json
import shutil
import time
import tempfile
import subprocess
import traceback
from collections.abc import Callable
from datetime import datetime, timezone
import logging
import requests

from webhook_client import (
    abort_agent_session,
    invoke_agent_webhook,
    verify_session_stopped,
)
from sentinel_poller import cleanup_output_files, initialize_activity_stamp, poll_for_sentinel
from skill_manager import SkillManager
from queue_semantics import parent_blocks_child, ESCALATION_ANSWERED, REVIVABLE_ANSWERED_STATES
from env_resolvers import resolve_openclaw_root, resolve_pipeline_root

# Route module-level logging.* calls (including those from webhook_client —
# notably abort_agent_session's success/failure lines) to stdout so they land
# in the same operator-facing stream as the orchestrator's print() output.
# Without this, logging defaults to stderr without a configured handler and
# abort outcomes disappear from /tmp/orchestrator.log.  Idempotent — only
# attaches a handler if no stdout-bound INFO handler is already present
# (so tests that pre-configure logging are not clobbered).
def _ensure_stdout_logging() -> None:
    """Attach an INFO-level StreamHandler to the root logger bound to the
    *current* ``sys.stdout``.

    Idempotent in production (a no-op if a handler already targets the
    current stdout).  Safe to re-invoke if ``sys.stdout`` is swapped
    (e.g. pytest capsys) — it will attach a fresh handler bound to the
    new stream so log lines remain visible.
    """
    root = logging.getLogger()
    for h in root.handlers:
        if (
            isinstance(h, logging.StreamHandler)
            and getattr(h, "stream", None) is sys.stdout
            and h.level <= logging.INFO
        ):
            return
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    root.addHandler(handler)
    if root.level == logging.WARNING or root.level > logging.INFO:
        root.setLevel(logging.INFO)


_ensure_stdout_logging()

OPENCLAW_ROOT = resolve_openclaw_root()
AUTODEV_REPO_PATH = os.environ.get(
    "AUTODEV_REPO_PATH",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _stall_timeout_seconds(env_name: str, default_str: str) -> int:
    """Parse stall-detection threshold from env; invalid values fall back to default.

    Governs **post-first-hook** silence: once the plugin has touched the
    activity stamp at least once, any subsequent gap exceeding this
    threshold treats the attempt as stalled.  Independent from startup
    grace — see :func:`_startup_grace_seconds`.
    """
    raw = (os.environ.get(env_name) or "").strip()
    try:
        v = int(raw or default_str)
    except ValueError:
        v = int(default_str)
    return max(1, v)


def _startup_grace_seconds(env_name: str, default_str: str) -> int:
    """Parse startup grace from env; invalid values fall back to default.

    Governs the **pre-first-hook** wait: how long :func:`poll_for_sentinel`
    tolerates a non-advancing activity stamp before declaring
    ``no_first_activity``.  Separate from the post-activity stall
    threshold so operators can tune slow OpenClaw boots independently
    from mid-turn silence detection.

    Parsing semantics mirror :func:`_stall_timeout_seconds` exactly:
    invalid strings fall back to ``default_str``, and the minimum is
    clamped to 1 second (a 0 value would race poll-tick cadence).
    """
    raw = (os.environ.get(env_name) or "").strip()
    try:
        v = int(raw or default_str)
    except ValueError:
        v = int(default_str)
    return max(1, v)


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


def _verify_symlinks_consistent(
    project_path: str,
    symlink_fixer: Callable[[str], bool] | None = None,
) -> bool:
    """Verify that both pipeline-project symlinks resolve to project_path.

    Both AUTODEV_PIPELINE_ROOT/pipeline-project (polled by the orchestrator for
    sentinel files) and OPENCLAW_ROOT/pipeline-project (followed by agent
    workspace symlinks when writing output) must resolve to the same real
    directory as the active project. Divergence causes the executor or reviewer
    to write sentinels to a different tree than the orchestrator polls, producing
    infinite retries.

    When symlink_fixer is provided (pass self.update_symlink at call sites), any
    detected divergence triggers an automatic reconcile attempt before the
    function returns. On a successful fix the function re-verifies both paths
    and returns True if they now agree. If project_path is empty or whitespace,
    or if the fix fails, returns False without proceeding further.

    Args:
        project_path: Absolute path to the active project directory (from
            pipeline_state["project_path"]).
        symlink_fixer: Optional callable with signature (target: str) -> bool
            that rewrites both symlinks to target. Typically self.update_symlink.

    Returns:
        True if both symlinks resolve to project_path (after reconcile if needed).
        False if project_path is empty, symlinks diverge and no fixer is
        provided, or fixer fails or post-fix re-verification still finds
        divergence.
    """
    if not project_path.strip():
        print(
            "[WARN] _verify_symlinks_consistent: project_path is empty — cannot reconcile."
        )
        return False

    target = os.path.abspath(os.path.expanduser(project_path.strip()))
    openclaw_symlink = os.path.join(OPENCLAW_ROOT, "pipeline-project")

    def _check() -> list[str]:
        problems: list[str] = []
        for label, p in (
            ("AUTODEV pipeline-project", SYMLINK_TARGET),
            ("OPENCLAW pipeline-project", openclaw_symlink),
        ):
            try:
                resolved = os.path.realpath(p) if os.path.lexists(p) else None
            except OSError as exc:
                problems.append(f"{label}: OSError resolving {p!r}: {exc}")
                continue
            if resolved != target:
                problems.append(f"{label}: {p!r} → {resolved!r} (expected {target!r})")
        return problems

    issues = _check()
    if not issues:
        return True

    if symlink_fixer is None:
        print(
            "[WARN] Symlink inconsistency detected before agent invocation — "
            "sentinel files may land in wrong directory:\n"
            + "\n".join(f"  {i}" for i in issues)
        )
        return False

    print(
        f"[RECONCILE] Symlink divergence detected — attempting auto-reconcile to {target}"
    )
    if not symlink_fixer(target):
        print(
            f"[ERROR] _verify_symlinks_consistent: auto-reconcile failed for {target!r}"
        )
        return False

    remaining = _check()
    if remaining:
        print(
            "[WARN] Symlink inconsistency persists after reconcile attempt:\n"
            + "\n".join(f"  {i}" for i in remaining)
        )
        return False

    print(f"[RECONCILE] Both symlinks confirmed resolved to {target}")
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
    session is registered, terminates immediately, and agent_end fires to write
    the .done sentinel — but the output JSON is absent or malformed.

    A session with non-zero runtimeMs that ends in error is a real failure and
    should go through the normal gate path so its output (if any) is evaluated.
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


def _session_jsonl_last_assistant_error_message(jsonl_path: str | None) -> str:
    """Return the last assistant ``errorMessage`` from an OpenClaw session JSONL.

    OpenRouter/OpenAI-style quota failures often appear as ``stopReason == "error"``
    on the assistant row while ``sessions.json`` still shows non-zero ``runtimeMs``,
    so :func:`_check_session_dead_on_arrival` does not classify them as dead-on-arrival.
    """
    if not jsonl_path or not os.path.exists(jsonl_path):
        return ""
    last_err = ""
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("type") != "message":
                    continue
                inner = row.get("message")
                if not isinstance(inner, dict):
                    continue
                if inner.get("role") != "assistant":
                    continue
                if inner.get("stopReason") != "error":
                    continue
                em = str(inner.get("errorMessage") or "").strip()
                if em:
                    last_err = em
    except OSError:
        return ""
    return last_err


def _is_provider_rejected_error(msg: str) -> bool:
    """Heuristic: returns True if the provider rejected the request for billing, rate-limit, or auth reasons."""
    if not msg:
        return False
    lower = msg.lower()
    if "401" in msg or "unauthorized" in lower:
        return True
    if "invalid api key" in lower or "incorrect api key" in lower:
        return True
    if "402" in msg or "payment required" in lower:
        return True
    if "more credits" in lower or "can only afford" in lower or "monthly limit" in lower:
        return True
    if "insufficient" in lower and ("credit" in lower or "fund" in lower or "balance" in lower):
        return True
    if "429" in msg or "rate limit" in lower:
        return True
    return False


def _sum_session_tokens(jsonl_path) -> dict:
    """W1-G: Sum token usage from an OpenClaw session JSONL file.

    OpenClaw session row shape (real, verified against live sessions):
        {"id", "type": "message", "parentId", "timestamp",
         "message": {"role": "assistant", "usage": {...}, ...}}

    The ``role`` and ``usage`` fields live *inside* the nested ``message``
    object, not at the top level of the row.  ``usage`` uses camelCase
    keys (``cacheRead``/``cacheWrite``/``totalTokens``) and a nested
    ``cost`` sub-object with ``cost.total``.

    Field mapping (OpenClaw JSONL → accumulator):
        message.usage.input        → input
        message.usage.output       → output
        message.usage.cacheRead    → cache_read
        message.usage.cacheWrite   → cache_write
        message.usage.totalTokens  → total_tokens
        message.usage.cost.total   → cost_total

    A legacy flat shape (``usage``/``role`` at the row top level) is also
    accepted for backwards compatibility with synthetic test fixtures.

    Returns a zero dict on any error (None path, missing file, parse failure).
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
                    if row.get("type") != "message":
                        continue
                    # Real OpenClaw shape: role and usage are nested under
                    # row["message"].  Legacy flat shape (top-level role/usage)
                    # is accepted as a fallback for synthetic fixtures.
                    inner = row.get("message")
                    if isinstance(inner, dict):
                        role = inner.get("role")
                        u = inner.get("usage")
                    else:
                        role = row.get("role")
                        u = row.get("usage")
                    if role != "assistant":
                        continue
                    if not isinstance(u, dict):
                        continue
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


def _run_completion_review(orchestrator, project_basename: str) -> None:
    """W5-B: Run the completion reviewer as a best-effort post-pipeline documentation pass.

    Called immediately before the PIPELINE_COMPLETE transition when the active
    queue entry has completion_review: true.

    Hard constraints:
    - Must never raise — all exceptions are caught and logged.
    - Must never call transition_state or modify pipeline state in any way.
    - 120s sentinel timeout, no retry.
    """
    try:
        sentinel_path = os.path.join(PROJECT_ARTIFACTS_DIR, "reviewer_output.done")
        token = orchestrator.openclaw_config.get("hooks", {}).get("token", "")
        session_key = f"pipeline:completion:{project_basename}:reviewer"

        orchestrator.skill_manager.inject_skill("COMPLETE-R0", "reviewer", orchestrator.openclaw_config)
        cleanup_output_files(PROJECT_ARTIFACTS_DIR, "reviewer")

        _p = "pipeline-project/.autodev/pipeline"
        _project_abs_path = os.path.realpath(SYMLINK_TARGET)
        _completion_message = (
            f"Begin completion documentation. Read the project source and git diff to understand "
            f"what was built. Produce three artifacts at the project root: README.md updates, "
            f"a CHANGELOG.md entry, and completion_report.md.\n\n"
            f"completion_report.md must walk a non-technical user through running the project "
            f"from a fresh terminal with no prior context — assume they have not opened a shell "
            f"yet and are not in any particular directory. Structure it as:\n"
            f"  1. What was built (one short paragraph).\n"
            f"  2. How to run it — write this as numbered steps. Step 1 must be: "
            f"'Open Terminal (macOS/Linux) or PowerShell (Windows)'. Step 2 must be the command "
            f"`cd {_project_abs_path}` in its own fenced ``` code block (use the literal absolute "
            f"path shown — never substitute a generic placeholder for the real path). "
            f"Then one fenced code block per command: install dependencies, build, run, test. "
            f"Reference the actual scripts present in package.json / Makefile / pyproject.toml / "
            f"etc. — only commands a user can paste verbatim.\n"
            f"  3. Files changed (brief list).\n"
            f"  4. Suggested next steps (2–4 bullets).\n\n"
            f"Every shell command must live in its own ``` fenced code block so the UI can render "
            f"one Copy button per command. Do not group multiple commands in one block.\n\n"
            f"Then write {_p}/reviewer_output.done."
        )
        _attempt_start = time.time()
        invoke_agent_webhook(
            "reviewer",
            session_key,
            token,
            message=_completion_message,
        )

        _stop_file = os.path.join(PROJECT_ARTIFACTS_DIR, "pipeline_stop_requested")
        sentinel_found = poll_for_sentinel(
            sentinel_path=sentinel_path,
            timeout_seconds=300,  # infrastructure-failure backstop only; agent_end fires immediately on session close
            stop_sentinel_path=_stop_file,
            min_sentinel_mtime=_attempt_start,
        )

        report_path = os.path.join(SYMLINK_TARGET, "completion_report.md")
        if sentinel_found and os.path.exists(report_path):
            print(f"[W5-B] Completion report generated: {report_path}")
        else:
            print(f"[W5-B] Completion review: sentinel_found={sentinel_found}, "
                  f"report_exists={os.path.exists(report_path)} — continuing to PIPELINE_COMPLETE")
    except Exception as _e:
        print(f"[W5-B] Completion review failed (non-fatal, pipeline will still complete): {_e}")


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
        # P0 Stage H — three retry-counter fields are persisted side-by-side:
        #   * executor_retries (legacy) — per-segment budget; resets to 0 on
        #     reviewer ROUTE_EXECUTOR rejection. Drives escalation/cap logic.
        #   * executor_self_failure_retries (NEW, lifetime) — accumulates every
        #     reset_execution('auto') across the whole phase. Never reset on
        #     reviewer rejection or operator escalation.
        #   * executor_reviewer_rejection_retries (NEW, lifetime) — accumulates
        #     every reviewer ROUTE_EXECUTOR dispatch across the phase. Same
        #     reset semantics as the self-failure lifetime counter.
        # Both lifetime counters reset to 0 only inside reset_phase(). They feed
        # the canonical metrics row's executor_attempts so the invariant
        # ``executor_attempts == self_failures + rejections + 1`` holds even
        # after reviewer rejections have reset the per-segment counter.
        self.state = {
            "current_phase": 0,
            "current_phase_raw_id": "",  # full phase-id string e.g. "CORE-2"; avoids int-suffix collisions
            "current_agent": "planner",
            "planner_retries": 0,
            "executor_retries": 0,
            "executor_self_failure_retries": 0,
            "executor_reviewer_rejection_retries": 0,
            "reviewer_retries": 0,
            "last_action": "initialized",
            "last_action_timestamp": datetime.now(timezone.utc).isoformat(),
            "pipeline_status": "RUNNING"
        }
        # P0 Stage H — orchestrator-private tracker for the current attempt's
        # retry classification. Used by gate_fail and attempt_end emits to
        # label events with retry_class. Values: "initial_attempt" |
        # "executor_self_failure" | "reviewer_rejection". reset_phase() resets
        # this to "initial_attempt"; reset_execution('auto') sets it to
        # "executor_self_failure"; the ROUTE_EXECUTOR handler sets it to
        # "reviewer_rejection".
        self._current_attempt_retry_class = "initial_attempt"
        _validate_openclaw_root(OPENCLAW_ROOT)
        self.openclaw_config = self.load_config()
        self.skill_manager = SkillManager(OPENCLAW_ROOT)

    def load_config(self):
        """Parse ``openclaw.json`` and return the config dict with normalized keys.

        Adds ``hooks_token``, ``hooks_url`` (for ``POST /hooks/agent``),
        ``gateway_token`` (``gateway.auth.token``), and ``gateway_ws_url``
        (WebSocket URL for Gateway RPC such as ``sessions.abort``).
        Exits the process if the config file is missing, invalid JSON, or
        the hooks bearer token is absent.
        """
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
        # Gateway WebSocket auth — used by abort_agent_session for session control.
        # Distinct from hooks_token which is for /hooks/agent HTTP calls.
        gw = config.get("gateway") or {}
        gw_port = 18789
        try:
            gw_port = int(gw.get("port") or 18789)
        except (TypeError, ValueError):
            pass
        config["gateway_token"] = (gw.get("auth") or {}).get("token", "")
        config["gateway_ws_url"] = f"ws://127.0.0.1:{gw_port}/__openclaw__/ws"
        # Fail fast: abort_agent_session is invoked at every executor/planner/
        # reviewer retry boundary and on every detected stall.  An empty
        # gateway token or unreachable WS URL turns those into silent no-ops
        # (returns False with a swallowed exception) — meaning a stalled
        # session keeps streaming while the orchestrator launches attempt
        # N+1 against it.  Discover the misconfiguration here, before a
        # phase starts, not the first time an abort fires.
        if not config["gateway_token"]:
            print(
                "[ERROR] openclaw.json has no gateway token (gateway.auth.token). "
                "Without it abort_agent_session cannot stop stalled sessions, "
                "leaving them streaming under retried attempts. Refusing to start."
            )
            sys.exit(1)
        if not config["gateway_ws_url"]:
            print(
                "[ERROR] openclaw.json has no gateway WebSocket URL. "
                "abort_agent_session needs gateway.port to construct the WS "
                "endpoint for sessions.abort. Refusing to start."
            )
            sys.exit(1)
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

    def _promote_answered_escalations(self, queue_data) -> bool:
        """P1 Stage H — flip parked ESCALATION rows whose answer has been banked.

        The UI server banks a deferred answer by writing ``pending_escalation_command.json``
        into the parked project's ``.autodev/pipeline/`` dir (a per-project file — it never
        touches the queue). This orchestrator-owned pre-pass is the single writer that turns
        "answer banked" into "revivable": an ESCALATION row whose project has that file is
        promoted to ESCALATION_ANSWERED so the selection walk picks it up for revival. Keeping
        the flip here (not in the server) preserves the single-writer queue model with no locks.

        Returns True if any row was promoted (and the queue was rewritten).
        """
        changed = False
        for entry in queue_data.get("queue", []):
            if entry.get("state") != "ESCALATION":
                continue
            pp = entry.get("project_path")
            if not pp:
                continue
            try:
                root = os.path.realpath(os.path.expanduser(pp))
            except OSError:
                continue
            pending = os.path.join(root, ".autodev", "pipeline", "pending_escalation_command.json")
            if os.path.exists(pending):
                entry["state"] = ESCALATION_ANSWERED
                entry["answered_at"] = datetime.now(timezone.utc).isoformat()
                changed = True
        if changed:
            self._write_queue(queue_data)
        return changed

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
        # P1 Stage H — promote parked ESCALATION rows whose answer has been banked
        # (pending_escalation_command.json present) to ESCALATION_ANSWERED, so the
        # eligibility walk below admits them for revival. Orchestrator-owned flip
        # (the server only writes the per-project pending file) — single-writer safe.
        self._promote_answered_escalations(queue_data)
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

            # Two eligible classes: a FRESH start (READY/SKIPPED_PENDING -> phase-0 reset)
            # or a REVIVAL (ESCALATION_ANSWERED -> restore the parked phase pointer). P1 Stage H.
            is_revival = entry["state"] in REVIVABLE_ANSWERED_STATES
            if entry["state"] not in ("READY", "SKIPPED_PENDING") and not is_revival:
                i += 1
                continue

            # Dependency: skip until parent COMPLETED; only use DEPENDENCY_HOLD when parent blocks.
            if entry.get("parent_id"):
                parent_state = state_by_id.get(entry["parent_id"])
                if parent_state != "COMPLETED":
                    if parent_blocks_child(parent_state):
                        entry["state"] = "DEPENDENCY_HOLD"
                        self._write_queue(queue_data)
                        # Phase 2 (observability) — record the hold. Fires once per genuine
                        # READY->DEPENDENCY_HOLD transition: an already-held entry is skipped by
                        # the state gate at the top of this loop before reaching here, so no spam.
                        _write_pipeline_event(
                            "dependency_hold", "", "queue",
                            {"parent_id": entry.get("parent_id"), "entry_id": entry.get("id"),
                             "entry_name": entry.get("name")},
                        )
                    i += 1
                    continue

            # Run lightweight preflight
            ok, reason = self._queue_preflight(entry["project_path"])
            if not ok and is_revival:
                # Don't downgrade a revival entry to SKIPPED_PENDING — that would lose the
                # answered semantics and orphan the banked command. Leave it ESCALATION_ANSWERED
                # (still revivable) so the operator can repair the project dir and retry.
                print(f"[QUEUE] Preflight failed for revival '{entry['name']}': {reason} — leaving ESCALATION_ANSWERED")
                i += 1
                continue
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
            # P1 Stage H — on a revival, capture the parked snapshot into a local BEFORE
            # clearing the row, then strip all park metadata so a future re-park starts clean.
            _revival_snapshot = None
            if is_revival:
                _revival_snapshot = dict(entry.get("parked_state_snapshot") or {})
                for _stale in ("parked_state_snapshot", "parked_at", "parked_reason",
                               "parked_pipeline_status", "answered_at"):
                    entry.pop(_stale, None)
            self._write_queue(queue_data)
            _write_run_manifest(entry)  # W2-A

            # INVARIANT A: update_symlink (above) is shared and runs FIRST; only the
            # self.state write splits between revival (restore) and fresh-start (reset).
            if is_revival:
                # Restore the escalated-phase pointer so the banked command (RESET_PHASE /
                # PROCEED / SKIP / ...) acts on the right phase, not a blank phase 0.
                snap = _revival_snapshot or {}
                self.state = {
                    "current_phase": snap.get("current_phase", 0),
                    "current_phase_raw_id": snap.get("current_phase_raw_id", ""),
                    "current_agent": "escalation",
                    "planner_retries": snap.get("planner_retries", 0),
                    "executor_retries": snap.get("executor_retries", 0),
                    "executor_self_failure_retries": snap.get("executor_self_failure_retries", 0),
                    "executor_reviewer_rejection_retries": snap.get("executor_reviewer_rejection_retries", 0),
                    "reviewer_retries": snap.get("reviewer_retries", 0),
                    "last_action": f"queue revival (answered) -> {entry['name']}",
                    "last_action_timestamp": now,
                    "pipeline_status": "RUNNING",
                    "project_path": project_path,
                }
                if snap.get("phase_base_commit"):
                    self.state["phase_base_commit"] = snap["phase_base_commit"]
                if snap.get("phase_start_time"):
                    self.state["phase_start_time"] = snap["phase_start_time"]
            else:
                self.state = {
                    "current_phase": 0,
                    "current_phase_raw_id": "",
                    "current_agent": "planner",
                    "planner_retries": 0,
                    "executor_retries": 0,
                    "executor_self_failure_retries": 0,
                    "executor_reviewer_rejection_retries": 0,
                    "reviewer_retries": 0,
                    "last_action": f"queue auto-advance to {entry['name']}",
                    "last_action_timestamp": now,
                    "pipeline_status": "RUNNING",
                    "project_path": project_path,
                }
            # P0 Stage H — queue auto-advance starts a fresh project; the
            # retry-class tracker starts at "initial_attempt" same as __init__.
            # (On a revival the banked RESET_* re-zeros counters anyway.)
            self._current_attempt_retry_class = "initial_attempt"
            self.write_state()
            # Reused unchanged in both branches: converts a banked pending_escalation_command
            # into escalation_output (+ sets WAITING_FOR_HUMAN / current_agent=escalation) so the
            # next loop's escalation dispatch consumes it against the (now-restored) phase pointer.
            applied_command = self._apply_pending_escalation_command(project_path)
            # Phase 2 (observability) — record the revival of a parked project and the banked
            # command being applied. Guarded on is_revival AND a real applied command, so the
            # fresh-start path (no pending file) never emits.
            if is_revival and applied_command:
                _write_pipeline_event(
                    "queue_revived",
                    self.state.get("current_phase_raw_id", ""),
                    "queue",
                    {"entry_id": entry.get("id"), "entry_name": entry.get("name"),
                     "command": applied_command},
                )
            return True

        # No eligible project found — determine halted reason
        non_terminal = [e["state"] for e in entries if e["state"] not in ("COMPLETED", "FAILED")]
        _parked_states = frozenset({"BLOCKED", "ESCALATION"})
        if not non_terminal:
            reason = "all_completed"
        elif any(s in REVIVABLE_ANSWERED_STATES for s in non_terminal):
            # P1 Stage H — at least one parked entry has a banked answer waiting. This is
            # recoverable (relaunch/resume), NOT a dead stall — keep it distinct from all_blocked
            # so the UI offers a Resume affordance instead of the permanent-stall toast. Tested
            # before all_blocked because an answered entry that slipped past selection (e.g. a
            # revival preflight-fail) must not be miscategorised as blocked.
            reason = "answered_pending_revival"
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
            # Phase 2 (observability) — record WHEN/WHY the queue stalled, not just the
            # resulting QUEUE_HALTED status. Stays inside this branch: the else path below
            # (caller owns final status, e.g. PIPELINE_COMPLETE) is not a halt.
            _write_pipeline_event("queue_halted", "", "queue", {"reason": reason})
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
            # P1 Stage H — snapshot the GLOBAL pipeline_state fields that the queue
            # advance overwrites (selection resets to a blank phase-0/planner state),
            # so a later revival can restore the *escalated phase* pointer rather than
            # restarting from scratch. phase_base_commit is load-bearing: reset_phase()
            # guards its ``git reset --hard`` on it, so without it a revived RESET_PHASE
            # would resume on a dirty tree. escalation_resets/reset_log are deliberately
            # NOT snapshotted — they live in the per-project phase_state.json (survives
            # via the symlink) and duplicating them would diverge from the reset-cap logic.
            row["parked_state_snapshot"] = {
                "current_phase": self.state.get("current_phase", 0),
                "current_phase_raw_id": self.state.get("current_phase_raw_id", ""),
                "planner_retries": self.state.get("planner_retries", 0),
                "executor_retries": self.state.get("executor_retries", 0),
                "executor_self_failure_retries": self.state.get("executor_self_failure_retries", 0),
                "executor_reviewer_rejection_retries": self.state.get("executor_reviewer_rejection_retries", 0),
                "reviewer_retries": self.state.get("reviewer_retries", 0),
                "phase_base_commit": self.state.get("phase_base_commit", ""),
                "phase_start_time": self.state.get("phase_start_time", ""),
            }
            if extra_fields:
                row.update(extra_fields)
            self._write_queue(queue_data)
            # Phase 2 (observability) — record that the active project was set aside and
            # the queue advanced. Emitted only after a successful write (both early returns
            # above are before it), so all four call sites route through one emit, once each.
            _write_pipeline_event(
                "queue_parked",
                self.state.get("current_phase_raw_id", ""),
                "queue",
                {"reason": parked_reason, "phase": self.state.get("current_phase_raw_id", ""),
                 "entry_id": row.get("id"), "entry_name": row.get("name")},
            )
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
        """If UI deferred a command while another project was active, apply it now.

        Returns the applied command string (e.g. "RESET_PHASE") when a banked command was
        fully converted to escalation_output and state was mutated, else None (no pending
        file, or a write failure). The return value is read by the queue_revived observability
        emit in _select_next_queue_project; all other callers ignore it.
        """
        root = os.path.realpath(os.path.expanduser(project_path))
        art = os.path.join(root, ".autodev", "pipeline")
        try:
            os.makedirs(art, exist_ok=True)
        except OSError:
            pass
        pending_json = os.path.join(art, "pending_escalation_command.json")
        if not os.path.exists(pending_json):
            return None
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
            return None
        self.state["pipeline_status"] = "WAITING_FOR_HUMAN"
        self.state["current_agent"] = "escalation"
        self.write_state()
        return command

    def _should_invoke_escalation_agent(self) -> bool:
        """True when escalation webhook should be invoked for current state.

        QUEUE_HALTED can legitimately coexist with an escalation wait context
        (parked queue row, awaiting human command). Treat it like WAITING_FOR_HUMAN
        to avoid repeatedly re-invoking escalation and flipping status back/forth.
        """
        return self.state.get("pipeline_status") not in ("WAITING_FOR_HUMAN", "QUEUE_HALTED")

    def _pending_escalation_output_exists(self) -> bool:
        """True if any escalation poll root already has an unconsumed escalation_output.done.

        Used by the QUEUE_HALTED recovery hook to preserve the pre-existing
        restart-with-an-in-place-answer path: if the operator answered in place and the
        process died before the dispatch consumed it, the answer is in escalation_output
        (not pending_escalation_command), so the main loop must still get a chance to consume it.
        """
        for root in self._escalation_poll_roots():
            if os.path.exists(os.path.join(root, ".autodev", "pipeline", "escalation_output.done")):
                return True
        return False

    def _maybe_revive_on_queue_halted(self) -> bool:
        """P1 Stage H — QUEUE_HALTED recovery hook (INVARIANT B). Called once at run() startup,
        BEFORE the current_agent-gated startup function.

        A bare relaunch into QUEUE_HALTED is a silent no-op today: the state carries
        ``current_agent="escalation"`` so the startup function returns ``enter_main_loop`` without
        re-running selection, and QUEUE_HALTED is deliberately NOT in the main loop's exit set (the
        loop legitimately polls for an *in-place* escalation answer). Result: the loop polls
        ``escalation_output`` forever while a deferred banked answer sits in
        ``pending_escalation_command.json``, never consumed.

        This hook forces a recovery decision and returns whether run() should continue into the
        loop (True) or exit cleanly (False):
          (a) revived a parked-with-answer project -> True (loop consumes the banked command);
          (b) nothing revived but an in-place escalation_output.done is already pending -> True
              (preserve the legacy restart-recovery path — the loop consumes it);
          (c) nothing to consume -> False (genuinely stuck; exit instead of spinning).

        Promotion is run first so recovery does not depend on whether the orchestrator was alive
        when the answer was banked. Returns True immediately when not QUEUE_HALTED (inert).
        """
        if self.state.get("pipeline_status") != "QUEUE_HALTED":
            return True
        queue_data = self._read_queue()
        self._promote_answered_escalations(queue_data)
        if self._select_next_queue_project(halt_if_no_eligible=False):
            # Revival rewrote self.state to the escalated phase pointer; reload to be safe.
            self.read_state()
            return True
        if self._pending_escalation_output_exists():
            return True  # legacy in-place answer pending — let the loop consume it
        print("[QUEUE] QUEUE_HALTED with no banked answer to revive — exiting cleanly.")
        return False

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

    def _read_escalation_advisory(self):
        """Read escalation agent's full advisory (summary + recommended_action).

        Supersedes _read_escalation_summary() at the post-resolution call site.
        Returns {"summary": str, "recommended_action": str} or None.
        Never raises — all failures are caught, logged, and return None.
        """
        try:
            path = os.path.join(PROJECT_ARTIFACTS_DIR, "escalation_summary.json")
            if not os.path.isfile(path):
                return None
            with open(path, "r") as f:
                data = json.load(f)
            summary = data.get("summary", "")
            if not isinstance(summary, str) or not summary.strip():
                return None
            action = data.get("recommended_action", "")
            return {
                "summary": summary.strip()[:200],
                "recommended_action": action.strip()[:200] if isinstance(action, str) else "",
            }
        except Exception as e:
            print(f"[ADVISORY] Could not read escalation_summary.json: {e}")
            return None

    def _generate_escalation_advisory(self):
        """Call qwen3.5-27b to produce a plain-English advisory before the escalation webhook.

        Reads failure_context.json and phase_state.json, calls the local LLM, and
        returns {"summary": str, "recommended_action": str} on success, None on any
        failure.  Never raises — all exceptions are caught and logged with [ADVISORY].

        The result should be written to phase_state.json as escalation_message and
        escalation_recommended_action BEFORE invoke_agent_webhook is called, so the
        UI and the webhook message both carry the human-readable context.

        P1 Stage G1 (de-blame): the LLM input is grounded in failure_context, the
        project's user-voice failure_language, and the retry counts. Blame-framed
        keys (escalation_trigger_reason, prior_blame_attributions) are NOT sent, so
        the advisory cannot parrot internal blame-attribution jargon back to the
        operator. failure_language is surfaced on every escalation that carries it,
        not only reviewer-rejection ones (the old reviewer_retries >= 2 gate).
        """
        failure_context_path = os.path.join(PROJECT_ARTIFACTS_DIR, "failure_context.json")
        if not os.path.isfile(failure_context_path):
            print("[ADVISORY] No failure_context.json — skipping advisory generation")
            return None

        try:
            with open(failure_context_path, "r") as f:
                failure_context_data = json.load(f)
        except Exception as e:
            print(f"[ADVISORY] Could not read failure_context.json: {e}")
            return None

        # Read phase_state for additional context (retry counts, blame history,
        # escalation trigger reason, and available recovery commands)
        _ps = {}
        try:
            _ps = self.read_phase_state()
        except Exception:
            pass

        _resets = _ps.get("escalation_resets", 0)
        _available_commands = (
            ["Reset Phase", "Reset Execution", "Re-run Reviewer", "Abandon Phase", "Stop Pipeline"]
            if _resets < 3
            else ["Abandon Phase", "Stop Pipeline"]
        )

        _system_prompt = (
            "You are the AutoDev Escalation Reviewer. An automated software development pipeline "
            "has stopped and requires operator attention. Your task is to review the current "
            "pipeline state and produce a concise, plain-English advisory that helps the operator "
            "understand what is happening and what to do next.\n\n"
            "Analyze the failure context provided and respond with a JSON object containing "
            "exactly two fields:\n\n"
            "- \"summary\": In 1-2 sentences: (1) what the error is, and (2) what is actually "
            "happening in the pipeline — these do not always match one-to-one. Write for an "
            "operator who is not actively watching a terminal. Technical terms (error codes, "
            "agent names, gate names) are acceptable where they add real clarity. Variable or "
            "file names may be referenced when they are the essential detail, but prefer "
            "describing the concept over naming internal specifics where possible.\n\n"
            "- \"recommended_action\": The single most appropriate recovery action as one direct "
            "imperative sentence. Use only the recovery commands listed as available in the "
            "context — do not recommend commands that are not available for the current phase "
            "state.\n\n"
            "Maximum 200 characters per field. Be direct. No hedging, no filler phrases.\n\n"
            "When the user-message payload includes a non-null `behavioral_verification` block, "
            "the failure has a project-authored, user-facing description; quote the project's "
            "pre-authored `failure_language` verbatim as part of the summary so the operator reads "
            "the failure in their own product's voice. When the block is absent, do not reference "
            "`failure_language` in your output."
        )

        # P0 Stage G + P1 Stage G1: derive a compact behavioural block from
        # failure_context so the LLM has the project's pre-authored
        # failure_language available. Stage G1 LOOSENED the gate: the block is
        # built whenever failure_context carries a failure_language string,
        # regardless of reviewer_retries. Executor-self-failure escalations
        # (reviewer_retries < 2) now get the user-voice copy too — previously
        # they were denied it and the advisory parroted blame jargon instead.
        # Block stays None only when failure_context has no behavioural data; the
        # system prompt tells the LLM not to reference failure_language then.
        _behavioural_block = None
        _claimed = (failure_context_data or {}).get("current_phase_behavioral_verification")
        _observed = (failure_context_data or {}).get("behavioral_verification_evidence")
        if isinstance(_claimed, dict) and _claimed.get("failure_language"):
            _behavioural_block = {
                "failure_language": _claimed.get("failure_language"),
                "verdict": (_observed or {}).get("verdict") if isinstance(_observed, dict) else None,
                "evidence_count": (
                    len((_observed or {}).get("evidence") or [])
                    if isinstance(_observed, dict) else 0
                ),
            }

        # P1 Stage G1 (de-blame): ground the advisory in the actual failure
        # (failure_context), the project's user-voice failure_language, and the
        # retry counts — NOT in blame-attribution state. escalation_trigger_reason
        # (usually the blame-cap string) and prior_blame_attributions are
        # deliberately omitted so the summary never parrots internal jargon.
        _user_message = json.dumps({
            "failure_context": failure_context_data,
            "executor_retries": _ps.get("executor_retries", 0),
            "reviewer_retries": _ps.get("reviewer_retries", 0),
            "available_recovery_commands": _available_commands,
            "behavioral_verification": _behavioural_block,
        })

        _payload = {
            "model": "qwen3.5-27b",
            "messages": [
                {"role": "system", "content": _system_prompt},
                {"role": "user", "content": _user_message},
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
            _resp = requests.post(_chat_url, json=_payload, timeout=30)
            _resp.raise_for_status()
            _raw = _resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            if not _raw.strip():
                raise ValueError("[ADVISORY] Empty response from LLM")
            _parsed = json.loads(_raw)
            _summary = _parsed.get("summary", "")
            _action = _parsed.get("recommended_action", "")
            if not isinstance(_summary, str) or not _summary.strip():
                print("[ADVISORY] LLM returned empty summary — skipping")
                return None
            return {
                "summary": _summary.strip()[:200],
                "recommended_action": _action.strip()[:200] if isinstance(_action, str) else "",
            }
        except json.JSONDecodeError as _e:
            print(f"[ADVISORY] Malformed JSON from LLM: {_e}")
            return None
        except Exception as _e:
            print(f"[ADVISORY] Advisory generation failed: {_e}")
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
            phase_state = {"planner_retries": 0, "executor_retries": 0, "executor_self_failure_retries": 0, "executor_reviewer_rejection_retries": 0, "reviewer_retries": 0, "reviewer_rejected": False, "escalation_resets": 0, "nuclear_resets": 0}

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

    def _init_activity_stamp_or_halt(self, agent_role: str) -> bool:
        """Seed the agent activity stamp; escalate loudly on failure.

        ``initialize_activity_stamp`` returns ``False`` when the workspace
        directory is missing or unwritable.  Silently discarding that
        return value (the pre-existing bug at the three orchestrator
        call sites) means ``poll_for_sentinel`` will subsequently call
        ``os.path.exists(stall_detection_path)`` against a file that
        never gets created — the stall branch is skipped on every poll
        iteration and a hung agent is invisible until the infrastructure
        backstop fires.  Loud halt is strictly better than silent stall
        blindness.

        Returns
        -------
        bool
            ``True`` if the stamp was successfully seeded — caller may
            proceed into ``poll_for_sentinel``.  ``False`` if the helper
            escalated to ``HALTED_SILENT`` — caller MUST short-circuit
            (``return``) to avoid running with stall detection broken.
        """
        ok = initialize_activity_stamp(PROJECT_ARTIFACTS_DIR, agent_role)
        if ok:
            return True
        stamp_path = os.path.join(
            PROJECT_ARTIFACTS_DIR, f"{agent_role}_activity.stamp"
        )
        print(
            f"[FATAL] activity stamp init failed for {agent_role} at {stamp_path}. "
            f"Workspace directory missing or unwritable — stall detection would "
            f"be silently disabled.  Refusing to proceed."
        )
        _write_pipeline_event(
            "stamp_init_failed",
            self.state.get("current_phase_raw_id", ""),
            agent_role,
            {
                "agent_role": agent_role,
                "stamp_path": stamp_path,
                "reason": "workspace_unwritable_or_missing",
            },
        )
        self.transition_state(
            "HALTED_SILENT",
            f"activity stamp init failed for {agent_role}",
        )
        return False

    def _handle_stall_outcome(
        self,
        agent_role: str,
        session_key: str,
        stamp_path: str,
        reason: str,
    ) -> bool:
        """Abort and verify the just-detected stalled / startup-timeout session.

        Called by all three pipeline-agent poll sites (planner / executor /
        reviewer) when ``poll_for_sentinel`` returns ``PollResult`` with
        ``reason in {"stalled", "no_first_activity", "timeout"}``.  The
        ``"timeout"`` reason was added after the CORE-E6 cascade — the
        45-min infrastructure backstop previously bypassed abort+verify,
        letting attempt N+1 launch on top of the still-streaming N.

        Behaviour:

        1. Build the OpenClaw-namespaced abort key from ``agent_role`` and
           ``session_key`` (OpenClaw normalises session keys to lowercase
           and prefixes ``agent:{role}:``).
        2. Call ``abort_agent_session`` (now with built-in 3x retry) and
           log ``[ABORT] result=ok|FAILED ...``.
        3. If abort succeeded, call ``verify_session_stopped`` to confirm
           the agent really stopped streaming.  A False there means the
           gateway acknowledged ``sessions.abort`` but the agent kept
           writing tokens.  We emit ``abort_verify_failed`` so the
           activity feed shows it in red, then **soft-continue** — the
           orchestrator launches the next attempt anyway.  Rationale:
           90%+ of long runs eventually resolve, and a forced
           ``HALTED_SILENT`` state requires human intervention which is
           worse than letting the retry run.
        4. If abort returned False after all retries, log it and let the
           caller proceed.  Same best-effort contract.

        Returns
        -------
        bool
            Always ``True`` — every outcome lets the caller continue.
            Return value kept for backwards-compatibility with the three
            poll-site guards that still check it.
        """
        # Build the OpenClaw-namespaced key.  Callers pass the
        # pipeline-internal key (``pipeline:phase-…``); OpenClaw prefixes
        # ``agent:{role}:`` and lowercases the whole thing internally.
        full_key = session_key
        if not full_key.startswith(f"agent:{agent_role}:"):
            full_key = f"agent:{agent_role}:{session_key}"
        full_key = full_key.lower()

        gw_token = self.openclaw_config.get("gateway_token", "")
        gw_ws_url = self.openclaw_config.get(
            "gateway_ws_url", "ws://127.0.0.1:18789/__openclaw__/ws"
        )
        aborted = abort_agent_session(full_key, gw_ws_url, gw_token)
        print(
            f"[ABORT] result={'ok' if aborted else 'FAILED'} "
            f"session_key={full_key} reason={reason} agent={agent_role}"
        )
        _phase_for_event = self.state.get("current_phase_raw_id", "")
        _write_pipeline_event(
            "abort_attempted",
            _phase_for_event,
            agent_role,
            {
                "session_key": full_key,
                "result": "ok" if aborted else "FAILED",
                "reason": reason,
                "agent_role": agent_role,
                "source": "inline_stall",
            },
        )
        if not aborted:
            # Best-effort contract: abort failure is logged but does not
            # block the retry flow.  The orchestrator's pre-existing
            # next-attempt cleanup still owns the recovery path.
            self._record_phase_outcome(last_abort_result="FAILED")
            return True

        if not verify_session_stopped(stamp_path, settle_seconds=5.0):
            # Soft-continue: gateway acknowledged abort but stamp is
            # still being refreshed.  Per operator policy, do NOT halt
            # the pipeline — emit the event so the activity feed shows
            # the situation, then let the next attempt run.  90%+ of
            # long runs resolve on retry, vs. a HALTED_SILENT state
            # that always requires human intervention.
            print(
                f"[ABORT][VERIFY_FAILED] session_key={full_key} "
                f"stamp_path={stamp_path} reason={reason} — gateway acknowledged "
                f"abort but stamp is still being refreshed.  Continuing with "
                f"next attempt (soft-continue); see activity feed for details."
            )
            _write_pipeline_event(
                "abort_verify_failed",
                _phase_for_event,
                agent_role,
                {
                    "session_key": full_key,
                    "stamp_path": stamp_path,
                    "agent_role": agent_role,
                    "reason": reason,
                },
            )
            self._record_phase_outcome(last_abort_result="verify_failed")
            return True

        self._record_phase_outcome(last_abort_result="ok")
        return True

    def _escalate_if_provider_rejected(self, jsonl_path: str | None, role_label: str) -> bool:
        """If the session JSONL ends with a provider-rejection error (billing, rate-limit, or auth),
        set last_error_code=ERR_PROVIDER_REJECTED, fill escalation_trigger_reason with the provider
        message, and route to escalation.

        Returns True when escalation was triggered (caller must ``continue`` the main loop). May be
        called more than once per attempt (post-poll and post-gate) to absorb JSONL flush ordering.
        """
        msg = _session_jsonl_last_assistant_error_message(jsonl_path)
        if not msg or not _is_provider_rejected_error(msg):
            return False
        print(f"[ERROR] [{role_label}] Provider rejected request: {msg[:240]}")
        _ps = self.read_phase_state()
        _ps["last_error_code"] = "ERR_PROVIDER_REJECTED"
        _ps["escalation_trigger_reason"] = (
            f"{role_label} blocked — inference provider rejected the request: {msg[:900]}"
        )
        self.write_phase_state_atomic(_ps)
        self.state["current_agent"] = "escalation"
        self.transition_state(
            "RUNNING",
            f"ERR_PROVIDER_REJECTED ({role_label}): {msg[:240]}",
        )
        return True

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

    def reviewer_sentinel_ready_from_prior_wait(self) -> bool:
        """True when reviewer already wrote output during a prior sentinel wait (restart).

        The reviewer branch always calls ``cleanup_output_files(..., "reviewer")`` before
        each webhook.  If the orchestrator process exits while ``poll_for_sentinel`` is
        blocked (or never resumes the loop), ``pipeline_status`` remains
        ``WAITING_FOR_SENTINEL`` but ``reviewer_output.{json,done}`` already exist.  On
        restart, cleaning those up would discard a completed review and force a duplicate
        reviewer attempt.  When this method returns True, skip cleanup/webhook/poll and
        treat the sentinel as found.

        The done-file mtime must be >= ``sentinel_wait_started_at`` so an orphaned
        sentinel from an older attempt cannot short-circuit a fresh invocation.
        """
        if self.state.get("pipeline_status") != "WAITING_FOR_SENTINEL":
            return False
        done_path = os.path.join(PROJECT_ARTIFACTS_DIR, "reviewer_output.done")
        json_path = os.path.join(PROJECT_ARTIFACTS_DIR, "reviewer_output.json")
        if not (os.path.isfile(done_path) and os.path.isfile(json_path)):
            return False
        sw_raw = (self.state.get("sentinel_wait_started_at") or "").strip()
        if not sw_raw:
            return False
        try:
            if sw_raw.endswith("Z") and "+" not in sw_raw:
                sw_dt = datetime.fromisoformat(sw_raw.replace("Z", "+00:00"))
            else:
                sw_dt = datetime.fromisoformat(sw_raw)
            if sw_dt.tzinfo is None:
                sw_dt = sw_dt.replace(tzinfo=timezone.utc)
            sw_ts = sw_dt.timestamp()
        except Exception:
            return False
        try:
            done_ts = os.path.getmtime(done_path)
        except OSError:
            return False
        if done_ts + 1.0 < sw_ts:
            return False
        return True

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

    def _clean_escalation_headline(self, raw_id=None):
        """P1 Stage G1 — a clean, deterministic headline for the escalation panel.

        Returns a phase-level string the UI can render as the escalation
        headline WITHOUT ever surfacing the raw blame-attribution
        ``escalation_trigger_reason``. Derived solely from the phase id, so it is
        structurally incapable of echoing the blame-cap string. Persisted as
        ``escalation_headline`` alongside ``escalation_trigger_reason`` at every
        escalation trigger.

        Args:
            raw_id: optional explicit phase raw id (call sites pass their local).
                Falls back to ``self.state['current_phase_raw_id']``. The
                "unknown" sentinel used by the call sites is treated as absent.
        """
        rid = raw_id if raw_id is not None else self.state.get("current_phase_raw_id", "")
        rid = str(rid or "").strip()
        if rid and rid.lower() != "unknown":
            return f"Phase {rid} needs your input"
        return "This phase needs your input"

    def _record_phase_outcome(self, **fields) -> None:
        """Atomically merge outcome fields into ``phase_state.json``.

        Used by Section 6.4 to persist the last poll outcome, abort
        result, and attempt summary so a restarted orchestrator (or the
        dashboard, which already reads ``phase_state.json``) can render
        "what happened last" without scraping ``/tmp/orchestrator.log``.
        Phase 3 added ``last_phase_outcome`` (``completed`` / ``escalated``
        / ``nuclear_reset``) for the terminal phase outcome; see the
        durability caveat in CLAUDE.md (``completed`` is wiped on advance,
        so the canonical metrics row is its durable record).

        Merge semantics: existing fields are preserved; only the supplied
        keyword arguments are written/overwritten.  Repeated calls form
        a last-write-wins per field, which is the contract the call
        sites rely on (e.g. one poll-site call sets ``last_poll_reason``,
        a later ``_handle_stall_outcome`` call adds ``last_abort_result``;
        both end up in the file).

        Best-effort: errors are logged and swallowed so a phase-state
        write failure never crashes the pipeline.
        """
        if not fields:
            return
        try:
            phase_state = self.read_phase_state() if hasattr(self, "read_phase_state") else {}
        except Exception as e:
            print(f"[WARN] _record_phase_outcome: read_phase_state failed: {e}")
            phase_state = {}
        for key, value in fields.items():
            phase_state[key] = value
        try:
            self.write_phase_state_atomic(phase_state)
        except Exception as e:
            print(f"[WARN] _record_phase_outcome: write failed: {e}")

    def _emit_reachability_advisory(self, raw_id):
        """Drain executor_advisory_detail.json into one summary event + one
        not_applicable event + N diagnostic events, stash a compact summary
        onto ``phase_state.last_reachability_summary`` (Phase 3 — so the
        canonical metrics row, written later on the reviewer-PASS path after
        this file is gone, can persist the reachability outcome), then remove
        the file. The stash is best-effort and read-modify-write so it never
        breaks event emission or clobbers sibling phase_state keys."""
        advisory_path = os.path.join(PROJECT_ARTIFACTS_DIR, "executor_advisory_detail.json")
        if not os.path.exists(advisory_path):
            return
        try:
            with open(advisory_path, "r") as f:
                advisory = json.load(f)
        except Exception as e:
            print(f"[WARN] _emit_reachability_advisory: could not read advisory: {e}")
            return
        if not isinstance(advisory, dict):
            return
        summary = advisory.get("reachability_summary")
        if isinstance(summary, dict) and summary.get("files"):
            _write_pipeline_event(
                "reachability_warning",
                raw_id,
                "executor",
                {
                    "kind": "unreachable_summary",
                    "count": summary.get("count", len(summary["files"])),
                    "files": summary["files"],
                    "command": summary.get("command", ""),
                    # Hedged copy — operator must not read "unreachable" as "dead."
                    "reason": summary.get(
                        "reason_template",
                        "files declared in manifest but not reached from entry point",
                    ),
                },
            )
        not_applicable = advisory.get("reachability_not_applicable")
        if isinstance(not_applicable, dict):
            _write_pipeline_event(
                "reachability_not_applicable",
                raw_id,
                "executor",
                {"reason": not_applicable.get("reason", "")},
            )
        diagnostics = advisory.get("reachability_diagnostics")
        if isinstance(diagnostics, list):
            for d in diagnostics:
                if not isinstance(d, dict):
                    continue
                _write_pipeline_event(
                    "reachability_warning",
                    raw_id,
                    "executor",
                    {
                        "kind": d.get("kind", "resolver_error"),
                        "reason": d.get("reason", ""),
                        "file": d.get("file"),
                    },
                )
        # Phase 3 — stash a compact summary onto phase_state so the canonical
        # metrics row (written later on the reviewer-PASS path, after this file
        # is removed) can persist the reachability outcome. Read-modify-write
        # preserves all other phase_state keys; every phase_state writer between
        # here and the row write is read-modify-write, so this survives the
        # reviewer run. Best-effort: a phase_state failure must never break event
        # emission or the os.remove below. Compact (no reason_template / per-file
        # diagnostic lists) — the full detail already rode out on the events.
        try:
            if isinstance(summary, dict) and summary.get("files"):
                _reach_stash = {
                    "kind": "unreachable_summary",
                    "count": summary.get("count", len(summary["files"])),
                    "files": summary["files"],
                    "command": summary.get("command", ""),
                }
            elif isinstance(not_applicable, dict):
                _reach_stash = {"kind": "not_applicable", "reason": not_applicable.get("reason", "")}
            elif isinstance(diagnostics, list) and diagnostics:
                _reach_stash = {"kind": "diagnostics", "count": len(diagnostics)}
            else:
                _reach_stash = None
            if _reach_stash is not None:
                _ps_reach = self.read_phase_state()
                _ps_reach["last_reachability_summary"] = _reach_stash
                self.write_phase_state_atomic(_ps_reach)
        except Exception as _reach_err:
            print(f"[WARN] _emit_reachability_advisory: stash failed: {_reach_err}")
        try:
            os.remove(advisory_path)
        except OSError:
            pass

    def _record_injected_skill(self, agent_role: str) -> None:
        """Write skill_injected and skill_agent to phase_state.json after inject_skill().

        Reads the workspace skills directory to determine what was placed: the
        directory is clean-then-write, so any subdirectory present after the call
        is the current phase-prefix skill (or empty = no skill).

        Writes skill_injected: None if injection produced no skill (unmapped or
        empty phase ID, missing source file, or the role / global kill switch
        suppressed injection).  Non-blocking: errors are logged and swallowed.
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

        P1 Stage G2: nuclear_resets and reset_log are preserved here for the same reason —
        they are operator-governance state, not per-attempt state. nuclear_resets is the
        cap-bearing counter for NUCLEAR_RESET (see nuclear_reset_phase), which itself calls
        this method; without preserving it the cap could never accumulate (the increment
        would be wiped by the very reset it governs). reset_log is the append-only audit
        trail of every operator reset; preserving it stops the silent wipe that previously
        discarded the RESET_PHASE dispatch's own log entry. All three (escalation_resets,
        nuclear_resets, reset_log) survive a phase reset and zero only on genuine phase
        advance (phase_state.json is deleted and lazily re-created at 0).
        """
        phase = self.state.get("current_phase", 0)
        raw_id = self.state.get("current_phase_raw_id", "")
        branch = f"phase/{raw_id}" if raw_id else f"phase/{phase}"
        phase_base = self.state.get("phase_base_commit", "")

        # Preserve operator-governance state before clearing phase state: the reset caps
        # (escalation_resets / nuclear_resets) and the reset_log audit trail must survive
        # a phase reset so the caps accumulate and the post-mortem trail is not silently lost.
        current_phase_state = self.read_phase_state()
        preserved_escalation_resets = current_phase_state.get("escalation_resets", 0)
        preserved_nuclear_resets = current_phase_state.get("nuclear_resets", 0)
        preserved_reset_log = current_phase_state.get("reset_log", [])
        preserved_last_phase_outcome = current_phase_state.get("last_phase_outcome")

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
            # P1 Stage F — advisory channel; same per-phase artifact lifecycle.
            "executor_advisory_detail.json",
        ]:
            try:
                os.remove(os.path.join(PROJECT_ARTIFACTS_DIR, fname))
            except FileNotFoundError:
                pass

        # Re-initialize phase_state: agent counters → 0, escalation_resets preserved (cap intact).
        # RR-4 (Phase 2): reviewer_infra_retries is zeroed on phase reset — it is a
        # per-phase soft-retry budget, not a global cap.
        # RR-2 (Phase 4): planner_output_preserved cleared — new phase, no preserved output.
        # P0 Stage H — reset_phase is the canonical boundary at which the
        # lifetime counters re-zero. Reviewer rejection and operator
        # escalation reset do NOT reset them (those preserve lifetime
        # visibility into prior failures).
        new_phase_state = {
            "planner_retries": 0,
            "executor_retries": 0,
            "executor_self_failure_retries": 0,
            "executor_reviewer_rejection_retries": 0,
            "reviewer_retries": 0,
            "reviewer_rejected": False,
            "reviewer_infra_retries": 0,
            "planner_output_preserved": False,
            "escalation_resets": preserved_escalation_resets,
            # P1 Stage G2 — operator-governance state survives the reset (see docstring).
            "nuclear_resets": preserved_nuclear_resets,
            "reset_log": preserved_reset_log,
            # Phase 3 — preserve the terminal outcome across the re-init so a nuclear
            # reset's outcome survives reset_phase() (which nuclear_reset_phase calls).
            # A normal operator RESET_PHASE carries forward whatever was there; the next
            # terminal event overwrites it.
            "last_phase_outcome": preserved_last_phase_outcome,
        }
        self.write_phase_state_atomic(new_phase_state)

        self.state["current_agent"] = "planner"
        self.state["planner_retries"] = 0
        self.state["executor_retries"] = 0
        self.state["executor_self_failure_retries"] = 0
        self.state["executor_reviewer_rejection_retries"] = 0
        self.state["reviewer_retries"] = 0
        # P0 Stage H — fresh phase starts fresh; subsequent attempts will
        # update the tracker via reset_execution('auto') or the
        # ROUTE_EXECUTOR handler.
        self._current_attempt_retry_class = "initial_attempt"
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

    def nuclear_reset_phase(self):
        """P1 Stage G2 — operator escape hatch (cap 2). Destructive true-fresh-start.

        A thin wrapper over reset_phase(): it reuses reset_phase's mechanics verbatim
        (git reset --hard to the phase base commit, delete the phase branch, wipe all
        phase outputs, zero every retry counter, clear prior_blame_attributions by the
        wholesale phase_state replacement, re-plan from the planner with
        _current_attempt_retry_class = "initial_attempt"). It does NOT re-list those
        counters — they are cleared by reset_phase's fresh-dict write.

        It differs from reset_phase ONLY in governance: it increments its own
        nuclear_resets counter (cap 2, enforced by the dispatch branch and the server's
        /api/command validation) instead of being blocked by the escalation_resets cap (3),
        and appends a NUCLEAR_RESET reset_log entry. The increment + log are written
        BEFORE delegating to reset_phase(), which then PRESERVES both nuclear_resets and
        reset_log across its re-init (see reset_phase docstring) — so the final phase_state
        carries the bumped counter and the new audit entry.

        Note: the escalation_resolve pipeline event is already emitted for every command at
        the top of the dispatch loop, so this method must not re-emit it.
        """
        _ps = self.read_phase_state()
        _ps["nuclear_resets"] = _ps.get("nuclear_resets", 0) + 1
        _reason = _ps.get("last_error_code", "unknown")
        _ps.setdefault("reset_log", []).append({
            "reset_number": _ps["nuclear_resets"],
            "command": "NUCLEAR_RESET",
            "reason": _reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        _ps["last_phase_outcome"] = "nuclear_reset"  # Phase 3 — preserved across reset_phase
        self.write_phase_state_atomic(_ps)
        print(f"[INFO] nuclear_reset_phase: nuclear_resets now {_ps['nuclear_resets']}, reason={_reason!r}.")
        # Phase 2 (observability) — record the destructive ACTION on the timeline. The dispatch
        # loop already emits escalation_resolve for the *command*; this captures that the phase
        # work was actually discarded. Emit BEFORE reset_phase() wipes the phase pointer so the
        # detail carries the escalated phase, not the post-reset blank.
        _write_pipeline_event(
            "nuclear_reset",
            self.state.get("current_phase_raw_id", ""),
            "escalation",
            {"nuclear_resets": _ps["nuclear_resets"], "reason": _reason,
             "phase": self.state.get("current_phase_raw_id", "")},
        )
        self.reset_phase()

    def reset_execution(self, caller: str):
        """Partial execution-level reset. Preserves planner output. Clears executor + reviewer outputs.

        caller='auto'       — from automatic executor retry path. Increments executor_retries
                              AND the lifetime executor_self_failure_retries counter (P0 Stage H).
                              Also sets self._current_attempt_retry_class = "executor_self_failure"
                              so subsequent gate_fail / attempt_end events label the retry source.
        caller='escalation' — from RESET_EXECUTION resume command. Increments escalation_resets.
                              Resets executor_retries to 0 (fresh budget) but does NOT touch the
                              lifetime self-failure / rejection counters (operator visibility into
                              prior failures is preserved across escalation resets).
        Never increments both legacy counters in one call. The lifetime counters are independent
        of the legacy counter and tracked alongside it for the metrics-row invariant
        ``executor_attempts == executor_self_failures + executor_reviewer_rejections + 1``.

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
            # P1 Stage F — advisory channel; same per-phase artifact lifecycle.
            "executor_advisory_detail.json",
        ]:
            try:
                os.remove(os.path.join(PROJECT_ARTIFACTS_DIR, fname))
            except FileNotFoundError:
                pass

        # RR-4 (Phase 2): reset_execution zeros reviewer_retries and reviewer_rejected so
        # the next reviewer invocation starts at pass 1.  reviewer_infra_retries is NOT
        # zeroed — it survives auto retries and only resets on a full phase reset
        # (reset_phase).  executor_succeeded is cleared because we are re-running
        # execution from scratch.
        phase_state = self.read_phase_state()
        phase_state["reviewer_retries"] = 0
        phase_state["reviewer_rejected"] = False
        phase_state.pop("executor_succeeded", None)
        # State-sync invariant: ``phase_state`` and ``self.state`` track the
        # same retry counters from different read paths.  Forgetting to update
        # both was the bug behind reviewer_retries drifting across retries —
        # the gate makes pass-routing decisions off phase_state while session
        # keys are built off self.state, and the two diverging produced the
        # confusing reviewer-attempt numbering observed live on CORE-E6.
        self.state["reviewer_retries"] = 0

        # Increment the correct counter — never both.
        if caller == "auto":
            phase_state["executor_retries"] = phase_state.get("executor_retries", 0) + 1
            new_count = phase_state["executor_retries"]
            self.state["executor_retries"] = new_count
            # P0 Stage H — lifetime self-failure counter. Tracks every auto
            # retry across the phase regardless of intervening reviewer
            # rejections (which reset the per-segment executor_retries).
            phase_state["executor_self_failure_retries"] = (
                phase_state.get("executor_self_failure_retries", 0) + 1
            )
            self.state["executor_self_failure_retries"] = (
                phase_state["executor_self_failure_retries"]
            )
            # Tracker for next attempt's event labelling.
            self._current_attempt_retry_class = "executor_self_failure"
            print(
                f"[INFO] reset_execution(auto): executor_retries now {new_count} "
                f"(lifetime self_failures="
                f"{phase_state['executor_self_failure_retries']})."
            )
        elif caller == "escalation":
            # Operator-driven reset: give the executor a fresh attempt budget.  Without
            # this, executor_retries stays at the prior cap (typically 3) so the next
            # main-loop iteration re-enters the `retries >= 3` branch and runs blame
            # attribution again instead of actually re-invoking the executor.  The UI
            # attempt chips (driven by executor_retries from pipeline_state.json) also
            # remain red instead of resetting to a fresh 3-slot budget.
            phase_state["executor_retries"] = 0
            self.state["executor_retries"] = 0
            # prior_blame_attributions feeds the consecutive-impl cap at orchestrator.py
            # line 3870.  Leaving the prior 3-4 impl entries in place would cause the
            # impl cap to fire again after a single new failure, defeating the reset.
            phase_state["prior_blame_attributions"] = []
            phase_state["escalation_resets"] = phase_state.get("escalation_resets", 0) + 1
            new_count = phase_state["escalation_resets"]
            print(f"[INFO] reset_execution(escalation): executor_retries reset to 0, prior_blame_attributions cleared, escalation_resets now {new_count}.")
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

        # State-sync invariant (mirror of the fix in reset_execution):
        # ``phase_state`` and ``self.state`` track the same counter from
        # different read paths.  ``transition_state`` writes self.state to
        # ``pipeline_state.json``, which the UI's agent-attempts panel
        # reads (ui/server.py:6474).  Without this line a RESET_REVIEWER
        # leaves the stale ``reviewer_retries`` in pipeline_state — the
        # UI keeps showing 3/3 red ×'s even though the reviewer has been
        # reset.  Observed live on UI-E1 for 30+ hours.
        self.state["reviewer_retries"] = 0
        # Set state so main loop routes to reviewer on next iteration
        self.state["current_agent"] = "reviewer"
        self.transition_state("RUNNING", "reset_reviewer: reviewer reset, awaiting retry")

    def _ensure_phase_branch(self, branch: str) -> bool:
        """Guarantee HEAD is on `branch` before any git write or agent invocation.

        Called proactively at three points: executor start, reviewer start, and Phase 10
        (before git add). After a RESET_PHASE the phase branch is deleted and HEAD lands
        on main; without this guard git commit in Phase 10 targets main and the subsequent
        git merge phase/N fails because the branch has no unique commits.

        Returns True if HEAD is on branch (or was successfully corrected).
        Returns False only if all correction attempts fail — caller should escalate.
        """
        try:
            sym = subprocess.run(
                ["git", "symbolic-ref", "--short", "HEAD"],
                cwd=SYMLINK_TARGET, capture_output=True, text=True
            )
            if sym.returncode == 0 and sym.stdout.strip() == branch:
                return True
            print(f"[WARN] _ensure_phase_branch: HEAD on '{sym.stdout.strip()}', expected '{branch}' — correcting.")
        except Exception:
            pass
        try:
            subprocess.run(
                f"git checkout {branch} 2>/dev/null || git checkout -b {branch}",
                shell=True, cwd=SYMLINK_TARGET, check=True
            )
            print(f"[INFO] _ensure_phase_branch: HEAD corrected to '{branch}'.")
            return True
        except subprocess.CalledProcessError:
            print(f"[ERROR] _ensure_phase_branch: could not checkout or create '{branch}'.")
            return False

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
            phase_state = {"planner_retries": 0, "executor_retries": 0, "executor_self_failure_retries": 0, "executor_reviewer_rejection_retries": 0, "reviewer_retries": 0, "reviewer_rejected": False, "escalation_resets": 0, "nuclear_resets": 0}

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

    def _load_current_phase_block(self):
        """Return the ``behavioral_verification`` block from
        ``current_phase.json``, or None if missing/malformed. Used by
        ``write_failure_context`` to capture the *claimed* half of the
        behavioural-verification snapshot alongside the reviewer's *observed*
        half (P0 Stage G).

        Reads the file fresh — current_phase.json is small and the
        failure-context write path runs once per failure, so the I/O cost is
        negligible. Non-blocking: returns None on any error rather than raising.
        """
        cp_path = os.path.join(PROJECT_ARTIFACTS_DIR, "current_phase.json")
        if not os.path.exists(cp_path):
            return None
        try:
            with open(cp_path, "r") as f:
                cp = json.load(f)
            block = cp.get("behavioral_verification")
            return block if isinstance(block, dict) else None
        except Exception:
            return None

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
            # P1 Stage F — advisory channel; same per-phase artifact lifecycle.
            "executor_advisory_detail.json",
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
            # P0 Stage G: claimed-vs-observed behavioural verification snapshot.
            # behavioral_verification_evidence = what the reviewer recorded;
            # current_phase_behavioral_verification = what the phase contract claimed.
            # The executor's reviewer-rejection retry pass sees both halves in one
            # read; the escalation advisory (fallback consumer) reads the claimed
            # half's failure_language when reviewer_retries >= 2.
            "behavioral_verification_evidence": (
                reviewer_output.get("behavioral_verification")
                if failing_agent == "reviewer" else None
            ),
            "current_phase_behavioral_verification": self._load_current_phase_block(),
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

    def _enrich_blocking_issue_with_criterion_default(self, bi):
        """P0 Stage G: every blocking issue carries an explicit
        ``criterion_source``. Issues that arrive without one — reviewer-written
        free-form blocking issues with no anchor — get the explicit ``"free"``
        label so downstream code can branch on a complete enum without
        None-checking.

        Does NOT overwrite a populated source. The gate's synthesis path
        already sets ``"behavioral"``; the reviewer agent's direct writes can
        set ``"test"`` when anchored to a planner
        ``traces_to`` value. ``criterion_id`` is intentionally omitted on
        ``"free"`` source — there is no anchor to point at, and writing an
        empty-string or null field would force downstream consumers to
        truthiness-check instead of presence-check.
        """
        if not isinstance(bi, dict):
            return bi
        out = dict(bi)
        if "criterion_source" not in out:
            out["criterion_source"] = "free"
        return out

    def _write_reviewer_failure_context(
        self,
        blocking_issues: list,
        reviewer_summary: str | None = None,
        reviewer_pass: int | None = None,
    ) -> None:
        """Atomically augment ``failure_context.json`` with reviewer-handoff metadata.

        Called from the ROUTE_EXECUTOR branch of the reviewer-gate
        dispatch so the next executor pass sees an explicit
        ``source="reviewer"`` marker plus the canonical blocking-issue
        list.  ``write_failure_context`` (called earlier in the
        reviewer-gate consumption flow) already captures the
        comprehensive context including blocking issues; this helper
        layers reviewer-specific metadata on top so consumers can
        distinguish a reviewer-driven handoff from a generic gate fail.

        The merge is non-destructive: any existing fields are preserved,
        and only the focused-schema fields below are written/overwritten.
        Atomic via ``tempfile.mkstemp`` + ``os.replace``.

        Schema fields written:

        * ``source`` — always ``"reviewer"``.
        * ``phase_id`` — current phase raw id (e.g. ``"CORE-E6"``).
        * ``reviewer_pass`` — 1-indexed pass number when supplied.
        * ``blocking_issues`` — canonical list, overwrites any prior value
          to guarantee the executor sees the latest reviewer verdict.
        * ``reviewer_summary`` — short human-readable rationale when supplied.
        * ``written_at`` — ISO-8601 UTC timestamp of this write.

        Errors are logged and swallowed so a write failure never crashes
        the pipeline (same contract as ``write_failure_context``).
        """
        fc_path = os.path.join(PROJECT_ARTIFACTS_DIR, "failure_context.json")
        existing: dict = {}
        if os.path.exists(fc_path):
            try:
                with open(fc_path, "r") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    existing = loaded
            except Exception:
                # Treat unreadable / non-dict file as missing — the focused
                # reviewer fields are more important than legacy content.
                existing = {}

        existing["source"] = "reviewer"
        existing["phase_id"] = self.state.get("current_phase_raw_id", "")
        # P0 Stage G + P1 Stage D: every blocking issue carries an explicit
        # ``criterion_source`` enum
        # (``"behavioral" | "test" | "regression_prior_phase" | "free"``).
        # Gate-synthesised behavioural and regression issues arrive pre-tagged;
        # reviewer-written free-form issues without an anchor get the explicit
        # ``"free"`` label here.
        existing["blocking_issues"] = [
            self._enrich_blocking_issue_with_criterion_default(bi)
            for bi in blocking_issues
        ]
        if reviewer_pass is not None:
            existing["reviewer_pass"] = int(reviewer_pass)
        if reviewer_summary is not None:
            existing["reviewer_summary"] = str(reviewer_summary)
        existing["written_at"] = datetime.now(timezone.utc).isoformat()

        try:
            os.makedirs(PROJECT_ARTIFACTS_DIR, exist_ok=True)
        except OSError:
            pass
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=PROJECT_ARTIFACTS_DIR, prefix="failure_context_"
            )
            with os.fdopen(fd, "w") as f:
                json.dump(existing, f, indent=2)
            os.replace(tmp_path, fc_path)
            print(
                f"[REVIEWER_GATE] failure_context augmented "
                f"(phase={existing['phase_id']}, "
                f"blocking_issues={len(existing['blocking_issues'])})"
            )
        except Exception as e:
            print(f"[ERROR] _write_reviewer_failure_context failed: {e}")
            try:
                if "tmp_path" in locals() and os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

    def _write_canonical_metrics_row(self) -> None:
        """Write the canonical metrics row for the just-completed phase.

        Maintains an orchestrator-private append-only history file at
        ``$AUTODEV_PIPELINE_ROOT/metrics_history/<project_name>.jsonl``
        that the executor cannot reach, then mirrors the full deduped
        history into the project's ``metrics.jsonl`` (which the
        reviewer-gate ``MISSING_ARTIFACTS`` check and the UI's
        ``/api/metrics`` endpoint both read).

        Background: the executor's ``AGENTS.md`` instructs it to *append*
        a metrics row at sentinel time, but agents driven by LLM file
        tools sometimes overwrite the file instead.  The previous
        orchestrator-side dedup logic read the live ``metrics.jsonl`` to
        get "existing rows," filtered out the current-phase row, and
        wrote them back — so when the executor overwrote the file down
        to a single row the writer ended up with ``existing_rows == []``
        and silently produced a truncated metrics.jsonl.  Observed live
        on the ``solitaire`` project, where 9 prior phases of history
        were lost between the CORE-E4 and CORE-E6 audit snapshots.

        The history file lives at ``$AUTODEV_PIPELINE_ROOT/metrics_history/``
        which is outside the agent workspace; the agent cannot reach it.
        On first run after this fix is deployed, the writer bootstraps
        history from the live ``metrics.jsonl`` so existing rows are
        preserved across the upgrade.

        Idempotent within a phase: if called repeatedly for the same
        ``current_phase_raw_id`` the file ends up with exactly one row
        per phase (the latest canonical version).

        Phase 3 — the row also carries per-phase "pain signals" read from
        the fresh on-disk ``phase_state`` (``ps_m``): ``escalation_resets``,
        ``nuclear_resets``, ``reviewer_unverified_retries`` (counters),
        ``reset_log`` (the operator-reset audit-trail snapshot), and
        ``reachability_summary`` (the compact copy stashed by
        ``_emit_reachability_advisory``). This runs on the reviewer-PASS
        path *before* ``phase_state.json`` is deleted on advance, so
        ``reset_log`` + the counters are still present here — that ordering
        is load-bearing for the durable record.
        """
        raw_id = self.state.get("current_phase_raw_id", "")
        if not raw_id:
            print(
                "[WARN] _write_canonical_metrics_row: no current_phase_raw_id, "
                "skipping"
            )
            return

        # --- Compose the canonical row (schema preserved from inline writer) ---
        duration_seconds = None
        phase_start_time = self.state.get("phase_start_time")
        if phase_start_time:
            try:
                start_dt = datetime.fromisoformat(phase_start_time)
                duration_seconds = int(time.time() - start_dt.timestamp())
            except Exception:
                pass

        goal_text = ""
        cp_path = os.path.join(PROJECT_ARTIFACTS_DIR, "current_phase.json")
        if os.path.exists(cp_path):
            try:
                with open(cp_path) as f:
                    cp_data = json.load(f)
                goal_text = cp_data.get("detail", "")
            except Exception:
                pass

        reviewer_passes = self.state.get("reviewer_retries", 0) + 1

        ps_m = {}
        if os.path.exists(PHASE_STATE_FILE):
            try:
                with open(PHASE_STATE_FILE) as f:
                    ps_m = json.load(f)
            except Exception:
                pass

        # P0 Stage H — source executor_attempts from the lifetime counters,
        # not the per-segment executor_retries. The legacy expression read
        # from the per-segment counter, which under-reports total attempts
        # when reviewer rejections have reset that counter to 0 mid-phase.
        # The two new lifetime counters accumulate across rejections so the
        # invariant
        # ``executor_attempts == self_failures + rejections + 1`` holds.
        executor_self_failures = ps_m.get("executor_self_failure_retries", 0)
        executor_reviewer_rejections = ps_m.get(
            "executor_reviewer_rejection_retries", 0
        )
        executor_attempts = executor_self_failures + executor_reviewer_rejections + 1

        planner_tok = ps_m.get("planner_tokens_acc", {}) or {}
        executor_tok = ps_m.get("executor_tokens_acc", {}) or {}
        reviewer_tok = ps_m.get("reviewer_tokens_acc", {}) or {}
        canonical_row = json.dumps({
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "phase": raw_id,
            "goal": goal_text,
            "executor_attempts": executor_attempts,
            # P0 Stage H — additive breakdown fields.
            "executor_self_failures": executor_self_failures,
            "executor_reviewer_rejections": executor_reviewer_rejections,
            "reviewer_passes": reviewer_passes,
            "blame_fires": ps_m.get("blame_fires", 0),    # W1-A
            "escalations": ps_m.get("escalations", 0),    # W1-B
            "skill_used": ps_m.get("skill_injected"),      # W1-C
            "blame_verdict": ps_m.get("blame_verdict"),    # null when no blame fired
            "planner_tokens": planner_tok,                 # W1-G
            "executor_tokens": executor_tok,               # W1-G
            "reviewer_tokens": reviewer_tok,               # W1-G
            "cost_total": round(                            # W1-G
                planner_tok.get("cost_total", 0.0)
                + executor_tok.get("cost_total", 0.0)
                + reviewer_tok.get("cost_total", 0.0),
                6,
            ),
            "duration_seconds": duration_seconds,
            # Phase 3 — durable per-phase pain signals, sourced from ps_m (the
            # fresh on-disk phase_state read above). This row is written on the
            # reviewer-PASS path BEFORE phase_state.json is deleted on advance,
            # so reset_log + the reset counters are still present here. The
            # reachability_summary is the compact copy _emit_reachability_advisory
            # stashed onto phase_state (null when no advisory drained this phase).
            "escalation_resets": ps_m.get("escalation_resets", 0),
            "nuclear_resets": ps_m.get("nuclear_resets", 0),
            "reviewer_unverified_retries": ps_m.get("reviewer_unverified_retries", 0),
            "reachability_summary": ps_m.get("last_reachability_summary"),
            "reset_log": ps_m.get("reset_log", []),
        })

        # --- Resolve paths.  Two files:
        #   history_path → orchestrator-private, append-only history (source of truth)
        #   metrics_path → project-visible, mirrors history (reviewer-gate + UI read this)
        project_name = (
            os.path.basename(os.path.realpath(SYMLINK_TARGET))
            if os.path.exists(SYMLINK_TARGET)
            else "unknown-project"
        )
        history_dir = os.path.join(AUTODEV_PIPELINE_ROOT, "metrics_history")
        history_path = os.path.join(history_dir, f"{project_name}.jsonl")
        metrics_path = os.path.join(PROJECT_ARTIFACTS_DIR, "metrics.jsonl")

        try:
            os.makedirs(history_dir, exist_ok=True)
        except OSError as e:
            print(
                f"[ERROR] Could not create metrics_history dir "
                f"{history_dir}: {e}"
            )
            return

        # First-run bootstrap: if history is empty but live metrics has rows,
        # seed history from live so prior history survives the upgrade.
        if not os.path.exists(history_path) and os.path.exists(metrics_path):
            try:
                shutil.copy2(metrics_path, history_path)
                print(
                    f"[INFO] metrics history bootstrapped from "
                    f"{metrics_path} → {history_path}"
                )
            except OSError as e:
                print(f"[WARN] metrics history bootstrap failed: {e}")

        # Read existing rows from the authoritative history file.  Falls
        # back to live metrics only if history is missing AND live exists
        # (defensive — should not normally happen after bootstrap).
        read_source = (
            history_path if os.path.exists(history_path) else metrics_path
        )
        existing_rows = []
        if os.path.exists(read_source):
            try:
                with open(read_source) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                            if row.get("phase") != raw_id:
                                existing_rows.append(line)
                        except json.JSONDecodeError:
                            # Preserve unparseable lines (e.g. agent-written
                            # rows missing a newline before the next row).
                            existing_rows.append(line)
            except OSError as e:
                print(
                    f"[WARN] Could not read existing metrics from "
                    f"{read_source}: {e}"
                )

        # Compose the new content.  Trailing newline matters — both the
        # reviewer-gate and the UI parser rely on line-based reading.
        full_content = "\n".join(existing_rows + [canonical_row]) + "\n"

        # Atomic-write to both targets.  Each target gets its own
        # ``mkstemp + os.replace`` so a crash mid-write to one file does
        # not corrupt the other.
        for target in (history_path, metrics_path):
            tmpdir = os.path.dirname(target) or "."
            tmp_path = None
            try:
                os.makedirs(tmpdir, exist_ok=True)
                fd, tmp_path = tempfile.mkstemp(dir=tmpdir, prefix=".metrics_")
                with os.fdopen(fd, "w") as f:
                    f.write(full_content)
                os.replace(tmp_path, target)
                tmp_path = None  # consumed by replace
            except Exception as e:
                print(
                    f"[ERROR] Failed to write canonical metrics to "
                    f"{target}: {e}"
                )
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

        _write_pipeline_event(  # W1-F
            "phase_complete",
            raw_id,
            "reviewer",
            {
                "executor_attempts": executor_attempts,
                "blame_fires": ps_m.get("blame_fires", 0),
            },
        )
        print(
            f"[INFO] Canonical metrics row written for {raw_id}: "
            f"{executor_attempts} executor attempt(s), "
            f"{reviewer_passes} reviewer pass(es), "
            f"{duration_seconds}s duration"
        )

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
            phase_state = {"planner_retries": 0, "executor_retries": 0, "executor_self_failure_retries": 0, "executor_reviewer_rejection_retries": 0, "reviewer_retries": 0, "reviewer_rejected": False, "escalation_resets": 0, "nuclear_resets": 0}

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
                    # W5-B: completion review (opt-in via queue entry flag, never gates PIPELINE_COMPLETE)
                    _cr_queue = self._read_queue()
                    _, _cr_entry = self._find_active_queue_entry(_cr_queue)
                    if _cr_entry and _cr_entry.get("completion_review"):
                        _run_completion_review(self, project_basename=os.path.basename(SYMLINK_TARGET))
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
                # P1 Stage G1: repo-init failures are pre-phase; give the UI a clean,
                # non-blame headline. The raw reason stays in the details disclosure.
                _ps["escalation_headline"] = "Repository setup needs your attention"
                _ps["escalations"] = _ps.get("escalations", 0) + 1  # W1-B
                _ps["last_phase_outcome"] = "escalated"  # Phase 3 — terminal outcome
                _ps["waiting_for_human_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # W1-E
                _ps["escalation_advisory_status"] = "generating"
                self.write_phase_state_atomic(_ps)

                _advisory = self._generate_escalation_advisory()
                if _advisory:
                    _ps["escalation_message"] = _advisory["summary"]
                    _ps["escalation_recommended_action"] = _advisory["recommended_action"]
                    _ps["escalation_advisory_status"] = "ready"
                else:
                    _ps["escalation_advisory_status"] = "fallback"
                self.write_phase_state_atomic(_ps)

                _write_pipeline_event("escalation_trigger", raw_id, "escalation", {"reason": _ps.get("escalation_trigger_reason")})  # W1-F
                self.transition_state("WAITING_FOR_HUMAN", "Invoking Escalation Agent: repo init check failed")
                self._queue_park_active_entry("ESCALATION", "escalation")
                # Note: park-and-advance is not applied here — the next queued project must pass
                # repo init on a fresh orchestrator run; advancing without re-check would be unsafe.
                _p = PROJECT_ARTIFACTS_DIR
                _ri_webhook_msg = (
                    f"Pipeline needs operator attention.\n\n"
                    f"Advisory: {_advisory['summary']}\n"
                    f"Suggested action: {_advisory.get('recommended_action', 'See dashboard.')}\n\n"
                    f"Read {_p}/phase_state.json and relevant output files for full context. "
                    f"Send a notification to the operator via your configured channel including "
                    f"the advisory above, then write your assessment to "
                    f"{_p}/escalation_output.json and {_p}/escalation_output.done."
                ) if _advisory else None
                webhook_status = invoke_agent_webhook(
                    "escalation", session_key, token, message=_ri_webhook_msg
                )
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

            # P1 Stage H — QUEUE_HALTED recovery (INVARIANT B). A relaunch into QUEUE_HALTED
            # carries current_agent="escalation", so the startup function below returns
            # enter_main_loop without re-running selection, and QUEUE_HALTED is deliberately not
            # in the loop's exit set — the loop would poll escalation_output forever while a
            # deferred banked answer sits unconsumed. This hook promotes banked answers and
            # revives a parked project; if there is genuinely nothing to consume it returns False
            # and run() exits cleanly instead of spinning. Inert when not QUEUE_HALTED.
            if not self._maybe_revive_on_queue_halted():
                return

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

                    _attempt_start_time = time.time()  # captured before cleanup for stale-sentinel guard
                    cleanup_output_files(PROJECT_ARTIFACTS_DIR, "planner")
                    self.skill_manager.inject_skill(
                        self.state.get("current_phase_raw_id", ""), "planner", self.openclaw_config
                    )
                    self._record_injected_skill("planner")
                    _stamp_ok = self._init_activity_stamp_or_halt("planner")
                    if not _stamp_ok:
                        return

                    self.state["sentinel_wait_started_at"] = datetime.now(timezone.utc).isoformat()
                    self.transition_state("WAITING_FOR_SENTINEL", "Invoking Planner via webhook")
                    webhook_status = invoke_agent_webhook("planner", session_key, token)

                    if webhook_status != "SUCCESS":
                        self.state["current_agent"] = "escalation"
                        error_reason = "Auth Config Error" if webhook_status == "AUTH_ERROR" else "Webhook infra failure"
                        self.transition_state("RUNNING", error_reason)
                        time.sleep(5)
                        continue

                    _stop_file = os.path.join(PROJECT_ARTIFACTS_DIR, "pipeline_stop_requested")
                    _planner_stamp = os.path.join(
                        PROJECT_ARTIFACTS_DIR, "planner_activity.stamp"
                    )
                    _planner_stall = _stall_timeout_seconds(
                        "AUTODEV_STALL_TIMEOUT_PLANNER", "300"
                    )
                    _planner_grace = _startup_grace_seconds(
                        "AUTODEV_STARTUP_GRACE_PLANNER", "600"
                    )
                    _planner_backstop = 4500  # 75 min infrastructure backstop
                    print(
                        f"[POLL][CONFIG] agent=planner "
                        f"startup_grace={_planner_grace}s "
                        f"stall_threshold={_planner_stall}s "
                        f"infra_backstop={_planner_backstop}s"
                    )
                    _write_pipeline_event(
                        "poll_start", raw_id, "planner",
                        {
                            "startup_grace": _planner_grace,
                            "stall_threshold": _planner_stall,
                            "infra_backstop": _planner_backstop,
                            "session_key": session_key,
                            "attempt": retries + 1,
                        },
                    )
                    sentinel_found = poll_for_sentinel(
                        sentinel_path,
                        timeout_seconds=_planner_backstop,
                        stop_sentinel_path=_stop_file,
                        min_sentinel_mtime=_attempt_start_time,
                        stall_detection_path=_planner_stamp,
                        stall_threshold_seconds=_planner_stall,
                        startup_grace_seconds=_planner_grace,
                        heartbeat_interval_seconds=60,
                    )
                    _planner_attempt_reason = getattr(sentinel_found, "reason", "unknown")
                    _planner_attempt_duration = int(time.time() - _attempt_start_time)
                    _write_pipeline_event(
                        "poll_outcome", raw_id, "planner",
                        {
                            "reason": _planner_attempt_reason,
                            "stamp_mtime": getattr(sentinel_found, "stamp_mtime", None),
                            "duration_s": _planner_attempt_duration,
                            "session_key": session_key,
                            "attempt": retries + 1,
                        },
                    )
                    print(
                        f"[ATTEMPT_END] phase={raw_id} agent=planner "
                        f"attempt={retries + 1} reason={_planner_attempt_reason} "
                        f"duration={_planner_attempt_duration}s "
                        f"session_key={session_key}"
                    )
                    _write_pipeline_event(
                        "attempt_end", raw_id, "planner",
                        {
                            "reason": _planner_attempt_reason,
                            "duration_s": _planner_attempt_duration,
                            "attempt": retries + 1,
                            "session_key": session_key,
                            # P0 Stage H — see Orchestrator.__init__ comment
                            # for the retry_class enum.
                            "retry_class": self._current_attempt_retry_class,
                        },
                    )
                    self._record_phase_outcome(
                        last_poll_reason=_planner_attempt_reason,
                        last_attempt_summary=(
                            f"phase={raw_id} agent=planner attempt={retries + 1} "
                            f"reason={_planner_attempt_reason} "
                            f"duration={_planner_attempt_duration}s"
                        ),
                    )
                    if getattr(sentinel_found, "reason", None) in (
                        "stalled",
                        "no_first_activity",
                        "timeout",
                    ):
                        if not self._handle_stall_outcome(
                            agent_role="planner",
                            session_key=session_key,
                            stamp_path=_planner_stamp,
                            reason=sentinel_found.reason,
                        ):
                            return

                    # W1-G: Resolve planner session JSONL and capture token usage.
                    # agent_end fires after sessions.json is populated, so a single
                    # read after the sentinel is sufficient.
                    _planner_sessions_dir = os.path.join(OPENCLAW_ROOT, "agents", "planner", "sessions")
                    _planner_sessions_json = os.path.join(_planner_sessions_dir, "sessions.json")
                    _planner_full_key = f"agent:planner:{session_key}".lower()
                    _planner_jsonl_path = None
                    try:
                        with open(_planner_sessions_json) as _sf:
                            _sd = json.load(_sf)
                        _sid = _sd.get(_planner_full_key, {}).get("sessionId")
                        if _sid:
                            _planner_jsonl_path = os.path.join(_planner_sessions_dir, f"{_sid}.jsonl")
                    except Exception:
                        pass
                    _planner_tokens = _sum_session_tokens(_planner_jsonl_path)
                    _ps_plan_tok = self.read_phase_state()
                    _planner_tokens_acc = _ps_plan_tok.get("planner_tokens_acc", {})
                    for _k, _v in _planner_tokens.items():
                        _planner_tokens_acc[_k] = _planner_tokens_acc.get(_k, 0) + _v
                    _ps_plan_tok["planner_tokens_acc"] = _planner_tokens_acc
                    self.write_phase_state_atomic(_ps_plan_tok)

                    if self._escalate_if_provider_rejected(_planner_jsonl_path, "Planner"):
                        time.sleep(5)
                        continue

                    if not sentinel_found:
                        if self._escalate_if_provider_rejected(_planner_jsonl_path, "Planner"):
                            time.sleep(5)
                            continue
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
                            if self._escalate_if_provider_rejected(_planner_jsonl_path, "Planner"):
                                time.sleep(5)
                                continue
                            print("[ERROR] Planner gate failed")
                            # gate_fail detail carries last_error_code for activity feed (H-23 prose); gate wrote phase_state before emit.
                            _write_pipeline_event(
                                "gate_fail",
                                raw_id,
                                "planner",
                                {
                                    "exit_code": 1,
                                    "last_error_code": self.read_phase_state().get("last_error_code"),
                                },
                            )  # W1-F
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

                    session_key = f"pipeline:phase-{phase}:{raw_id}:executor-attempt-{retries + 1}"
                    attempt_label = "Cloud"

                    sentinel_path = os.path.join(PROJECT_ARTIFACTS_DIR, "executor_output.done")
                    token = self.openclaw_config.get("hooks", {}).get("token", "")

                    # Abort the previous executor session before invoking the next attempt.
                    # Stops the prior run from consuming tokens and from refreshing
                    # executor_activity.stamp (which can suppress stall detection).
                    #
                    # Observability invariant: the return value MUST be captured and
                    # logged.  A silent fire-and-forget call was the original CORE-E6
                    # bug — attempt #2 wrote 69,152 tokens while attempt #3 was
                    # already running because nobody knew the abort had failed.
                    # Equally, an acknowledged abort (ok=true) is not sufficient
                    # proof the session stopped — verify_session_stopped confirms
                    # the plugin is no longer touching the activity stamp.  If it
                    # still is, we emit abort_verify_failed and soft-continue
                    # (launch attempt N+1 anyway): a forced HALTED_SILENT always
                    # needs a human, whereas a retry usually resolves.  See the
                    # _handle_stall_outcome docstring for the full rationale.
                    if retries > 0:
                        _prev_session_key = (
                            f"agent:executor:pipeline:phase-{phase}:{raw_id}"
                            f":executor-attempt-{retries}"
                        ).lower()
                        _gw_token = self.openclaw_config.get("gateway_token", "")
                        _gw_ws_url = self.openclaw_config.get(
                            "gateway_ws_url", "ws://127.0.0.1:18789/__openclaw__/ws"
                        )
                        aborted = abort_agent_session(
                            _prev_session_key, _gw_ws_url, _gw_token
                        )
                        print(
                            f"[ABORT] result={'ok' if aborted else 'FAILED'} "
                            f"session_key={_prev_session_key} prior_attempt={retries}"
                        )
                        _write_pipeline_event(
                            "abort_attempted", raw_id, "executor",
                            {
                                "session_key": _prev_session_key,
                                "result": "ok" if aborted else "FAILED",
                                "agent_role": "executor",
                                "source": "retry_start",
                                "prior_attempt": retries,
                            },
                        )
                        if aborted:
                            _prev_stamp = os.path.join(
                                PROJECT_ARTIFACTS_DIR, "executor_activity.stamp"
                            )
                            if not verify_session_stopped(_prev_stamp, settle_seconds=5.0):
                                # Soft-continue: surface the situation to the
                                # activity feed but launch attempt N+1 anyway.
                                # See _handle_stall_outcome docstring for the
                                # rationale (forced halt requires human
                                # intervention; retries usually resolve).
                                print(
                                    f"[ABORT][VERIFY_FAILED] session_key={_prev_session_key} "
                                    f"stamp_path={_prev_stamp} — gateway acknowledged abort "
                                    f"but stamp is still being refreshed.  Continuing with "
                                    f"attempt {retries + 1} anyway (soft-continue)."
                                )
                                _write_pipeline_event(
                                    "abort_verify_failed", raw_id, "executor",
                                    {
                                        "session_key": _prev_session_key,
                                        "stamp_path": _prev_stamp,
                                        "agent_role": "executor",
                                        "source": "retry_start",
                                        "prior_attempt": retries,
                                    },
                                )

                    # Proactive branch guard: after RESET_PHASE the phase branch is deleted
                    # and HEAD lands on main. Correct before the executor runs so any git
                    # work it triggers (and Phase 10's commit) targets the right branch.
                    _ex_branch = f"phase/{raw_id}" if raw_id and raw_id != "unknown" else f"phase/{phase}"
                    self._ensure_phase_branch(_ex_branch)

                    _attempt_start_time = time.time()  # captured before cleanup for stale-sentinel guard
                    cleanup_output_files(PROJECT_ARTIFACTS_DIR, "executor")
                    self.skill_manager.inject_skill(
                        self.state.get("current_phase_raw_id", ""), "executor", self.openclaw_config
                    )
                    self._record_injected_skill("executor")
                    _stamp_ok = self._init_activity_stamp_or_halt("executor")
                    if not _stamp_ok:
                        return
                    self.state["sentinel_wait_started_at"] = datetime.now(timezone.utc).isoformat()
                    self.transition_state("WAITING_FOR_SENTINEL", f"Invoking Executor ({attempt_label}) - Attempt {retries + 1}")

                    _verify_symlinks_consistent(
                        self.state.get("project_path", ""), self.update_symlink
                    )
                    webhook_status = invoke_agent_webhook("executor", session_key, token)

                    if webhook_status != "SUCCESS":
                        self.state["current_agent"] = "escalation"
                        error_reason = "Auth Config Error" if webhook_status == "AUTH_ERROR" else "Webhook infra failure"
                        self.transition_state("RUNNING", error_reason)
                        time.sleep(5)
                        continue

                    _stop_file = os.path.join(PROJECT_ARTIFACTS_DIR, "pipeline_stop_requested")
                    _executor_stamp = os.path.join(
                        PROJECT_ARTIFACTS_DIR, "executor_activity.stamp"
                    )
                    _executor_stall = _stall_timeout_seconds(
                        "AUTODEV_STALL_TIMEOUT_EXECUTOR", "300"
                    )
                    _executor_grace = _startup_grace_seconds(
                        "AUTODEV_STARTUP_GRACE_EXECUTOR", "600"
                    )
                    _executor_backstop = 4500  # 75 min infrastructure backstop
                    print(
                        f"[POLL][CONFIG] agent=executor "
                        f"startup_grace={_executor_grace}s "
                        f"stall_threshold={_executor_stall}s "
                        f"infra_backstop={_executor_backstop}s"
                    )
                    _write_pipeline_event(
                        "poll_start", raw_id, "executor",
                        {
                            "startup_grace": _executor_grace,
                            "stall_threshold": _executor_stall,
                            "infra_backstop": _executor_backstop,
                            "session_key": session_key,
                            "attempt": retries + 1,
                        },
                    )
                    sentinel_found = poll_for_sentinel(
                        sentinel_path,
                        timeout_seconds=_executor_backstop,
                        stop_sentinel_path=_stop_file,
                        min_sentinel_mtime=_attempt_start_time,
                        stall_detection_path=_executor_stamp,
                        stall_threshold_seconds=_executor_stall,
                        startup_grace_seconds=_executor_grace,
                        heartbeat_interval_seconds=60,
                    )
                    _executor_attempt_reason = getattr(sentinel_found, "reason", "unknown")
                    _executor_attempt_duration = int(time.time() - _attempt_start_time)
                    _write_pipeline_event(
                        "poll_outcome", raw_id, "executor",
                        {
                            "reason": _executor_attempt_reason,
                            "stamp_mtime": getattr(sentinel_found, "stamp_mtime", None),
                            "duration_s": _executor_attempt_duration,
                            "session_key": session_key,
                            "attempt": retries + 1,
                        },
                    )
                    print(
                        f"[ATTEMPT_END] phase={raw_id} agent=executor "
                        f"attempt={retries + 1} reason={_executor_attempt_reason} "
                        f"duration={_executor_attempt_duration}s "
                        f"session_key={session_key}"
                    )
                    _write_pipeline_event(
                        "attempt_end", raw_id, "executor",
                        {
                            "reason": _executor_attempt_reason,
                            "duration_s": _executor_attempt_duration,
                            "attempt": retries + 1,
                            "session_key": session_key,
                            # P0 Stage H — see Orchestrator.__init__ comment
                            # for the retry_class enum.
                            "retry_class": self._current_attempt_retry_class,
                        },
                    )
                    self._record_phase_outcome(
                        last_poll_reason=_executor_attempt_reason,
                        last_attempt_summary=(
                            f"phase={raw_id} agent=executor attempt={retries + 1} "
                            f"reason={_executor_attempt_reason} "
                            f"duration={_executor_attempt_duration}s"
                        ),
                    )
                    if getattr(sentinel_found, "reason", None) in (
                        "stalled",
                        "no_first_activity",
                        "timeout",
                    ):
                        if not self._handle_stall_outcome(
                            agent_role="executor",
                            session_key=session_key,
                            stamp_path=_executor_stamp,
                            reason=sentinel_found.reason,
                        ):
                            return

                    # W1-G: Resolve executor session JSONL and capture token usage.
                    # agent_end fires after sessions.json is populated, so a single
                    # read after the sentinel is sufficient.
                    _sessions_dir = os.path.join(OPENCLAW_ROOT, "agents", "executor", "sessions")
                    _sessions_json = os.path.join(_sessions_dir, "sessions.json")
                    _full_key = f"agent:executor:{session_key}".lower()  # openclaw normalizes session keys to lowercase
                    _jsonl_path = None
                    try:
                        with open(_sessions_json) as _sf:
                            _sd = json.load(_sf)
                        _sid = _sd.get(_full_key, {}).get("sessionId")
                        if _sid:
                            _jsonl_path = os.path.join(_sessions_dir, f"{_sid}.jsonl")
                    except Exception:
                        pass

                    # Dead-on-arrival check: sessions.json is reliably populated by the time
                    # agent_end (or the hard timeout) unblocks the sentinel poll.
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

                    if self._escalate_if_provider_rejected(_jsonl_path, "Executor"):
                        time.sleep(5)
                        continue

                    # Accumulate executor token usage into phase_state across retry attempts.
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
                            # P1 Stage F — advisory; never affects gate verdict.
                            # Drains executor_advisory_detail.json into pipeline
                            # events so the UI shows reachability findings.
                            self._emit_reachability_advisory(raw_id)
                            _ps_ex = self.read_phase_state()
                            _ps_ex.pop("last_error_code", None)
                            self.write_phase_state_atomic(_ps_ex)
                            self.state["current_agent"] = "reviewer"
                            self.transition_state("RUNNING", "Executor passed, moving to reviewer")
                            continue
                        else:
                            print("[ERROR] Executor gate failed")
                            _write_pipeline_event(
                                "gate_fail",
                                raw_id,
                                "executor",
                                {
                                    "exit_code": 1,
                                    "last_error_code": self.read_phase_state().get("last_error_code"),
                                    # P0 Stage H — label this gate-fail with
                                    # what kicked off the failing attempt so
                                    # the activity feed can distinguish
                                    # self-failure retries from rejection
                                    # retries from initial attempts.
                                    "retry_class": self._current_attempt_retry_class,
                                },
                            )  # W1-F
                            self.write_failure_context("executor", self.state.get("executor_retries", 0) + 1)
                            if self._escalate_if_provider_rejected(_jsonl_path, "Executor"):
                                time.sleep(5)
                                continue
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
                        if self._escalate_if_provider_rejected(_jsonl_path, "Executor"):
                            time.sleep(5)
                            continue
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

                    # Proactive branch guard: ensure HEAD is on the phase branch before
                    # invoking the reviewer. Phase 10 (inside this same block) will hard-
                    # guard again before git add, but catching drift here avoids the
                    # reviewer running on the wrong branch entirely.
                    _rv_branch = f"phase/{raw_id}" if raw_id and raw_id != "unknown" else f"phase/{phase}"
                    self._ensure_phase_branch(_rv_branch)

                    resume_reviewer = self.reviewer_sentinel_ready_from_prior_wait()
                    if resume_reviewer:
                        print(
                            "[INFO] [REVIEWER] Reviewer sentinel already present for this wait — "
                            "skipping cleanup and re-invocation (crash recovery)."
                        )
                        sentinel_found = True
                    else:
                        _attempt_start_time = time.time()  # captured before cleanup for stale-sentinel guard
                        cleanup_output_files(PROJECT_ARTIFACTS_DIR, "reviewer")
                        self.skill_manager.inject_skill(
                            self.state.get("current_phase_raw_id", ""), "reviewer", self.openclaw_config
                        )
                        self._record_injected_skill("reviewer")
                        _stamp_ok = self._init_activity_stamp_or_halt("reviewer")
                        if not _stamp_ok:
                            return
                        self.state["sentinel_wait_started_at"] = datetime.now(timezone.utc).isoformat()
                        self.transition_state("WAITING_FOR_SENTINEL", f"Invoking Reviewer - Attempt {retries + 1}")

                        _verify_symlinks_consistent(
                            self.state.get("project_path", ""), self.update_symlink
                        )
                        webhook_status = invoke_agent_webhook("reviewer", session_key, token)

                        if webhook_status != "SUCCESS":
                            self.state["current_agent"] = "escalation"
                            error_reason = "Auth Config Error" if webhook_status == "AUTH_ERROR" else "Webhook infra failure"
                            self.transition_state("RUNNING", error_reason)
                            time.sleep(5)
                            continue

                        _stop_file = os.path.join(PROJECT_ARTIFACTS_DIR, "pipeline_stop_requested")
                        _reviewer_stamp = os.path.join(
                            PROJECT_ARTIFACTS_DIR, "reviewer_activity.stamp"
                        )
                        _reviewer_stall = _stall_timeout_seconds(
                            "AUTODEV_STALL_TIMEOUT_REVIEWER", "300"
                        )
                        _reviewer_grace = _startup_grace_seconds(
                            "AUTODEV_STARTUP_GRACE_REVIEWER", "600"
                        )
                        _reviewer_backstop = 4500  # 75 min infrastructure backstop
                        print(
                            f"[POLL][CONFIG] agent=reviewer "
                            f"startup_grace={_reviewer_grace}s "
                            f"stall_threshold={_reviewer_stall}s "
                            f"infra_backstop={_reviewer_backstop}s"
                        )
                        _write_pipeline_event(
                            "poll_start", raw_id, "reviewer",
                            {
                                "startup_grace": _reviewer_grace,
                                "stall_threshold": _reviewer_stall,
                                "infra_backstop": _reviewer_backstop,
                                "session_key": session_key,
                                "attempt": retries + 1,
                            },
                        )
                        sentinel_found = poll_for_sentinel(
                            sentinel_path,
                            timeout_seconds=_reviewer_backstop,
                            stop_sentinel_path=_stop_file,
                            min_sentinel_mtime=_attempt_start_time,
                            stall_detection_path=_reviewer_stamp,
                            stall_threshold_seconds=_reviewer_stall,
                            startup_grace_seconds=_reviewer_grace,
                            heartbeat_interval_seconds=60,
                        )
                        _reviewer_attempt_reason = getattr(sentinel_found, "reason", "unknown")
                        _reviewer_attempt_duration = int(time.time() - _attempt_start_time)
                        _write_pipeline_event(
                            "poll_outcome", raw_id, "reviewer",
                            {
                                "reason": _reviewer_attempt_reason,
                                "stamp_mtime": getattr(sentinel_found, "stamp_mtime", None),
                                "duration_s": _reviewer_attempt_duration,
                                "session_key": session_key,
                                "attempt": retries + 1,
                            },
                        )
                        print(
                            f"[ATTEMPT_END] phase={raw_id} agent=reviewer "
                            f"attempt={retries + 1} reason={_reviewer_attempt_reason} "
                            f"duration={_reviewer_attempt_duration}s "
                            f"session_key={session_key}"
                        )
                        _write_pipeline_event(
                            "attempt_end", raw_id, "reviewer",
                            {
                                "reason": _reviewer_attempt_reason,
                                "duration_s": _reviewer_attempt_duration,
                                "attempt": retries + 1,
                                "session_key": session_key,
                                # P0 Stage H — see Orchestrator.__init__
                                # comment for the retry_class enum. Reviewer
                                # attempts share the tracker; the value reflects
                                # what kicked off the executor attempt that
                                # produced the output now being reviewed.
                                "retry_class": self._current_attempt_retry_class,
                            },
                        )
                        self._record_phase_outcome(
                            last_poll_reason=_reviewer_attempt_reason,
                            last_attempt_summary=(
                                f"phase={raw_id} agent=reviewer "
                                f"attempt={retries + 1} "
                                f"reason={_reviewer_attempt_reason} "
                                f"duration={_reviewer_attempt_duration}s"
                            ),
                        )
                        if getattr(sentinel_found, "reason", None) in (
                            "stalled",
                            "no_first_activity",
                            "timeout",
                        ):
                            if not self._handle_stall_outcome(
                                agent_role="reviewer",
                                session_key=session_key,
                                stamp_path=_reviewer_stamp,
                                reason=sentinel_found.reason,
                            ):
                                return

                    # W1-G: Resolve reviewer session JSONL and capture token usage.
                    # agent_end fires after sessions.json is populated, so a single
                    # read after the sentinel is sufficient.
                    _rev_sessions_dir = os.path.join(OPENCLAW_ROOT, "agents", "reviewer", "sessions")
                    _rev_sessions_json = os.path.join(_rev_sessions_dir, "sessions.json")
                    _rev_full_key = f"agent:reviewer:{session_key}".lower()
                    _jsonl_path = None
                    try:
                        with open(_rev_sessions_json) as _sf:
                            _sd = json.load(_sf)
                        _sid = _sd.get(_rev_full_key, {}).get("sessionId")
                        if _sid:
                            _jsonl_path = os.path.join(_rev_sessions_dir, f"{_sid}.jsonl")
                    except Exception:
                        pass

                    # Dead-on-arrival check runs after sentinel poll (sessions.json guaranteed
                    # populated by the time agent_end or the hard timeout fires).
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

                    # Capture reviewer token usage from the resolved session JSONL.
                    _reviewer_tokens = _sum_session_tokens(_jsonl_path)
                    _ps_rev_tok = self.read_phase_state()
                    _ps_rev_tok["reviewer_tokens_acc"] = _reviewer_tokens
                    self.write_phase_state_atomic(_ps_rev_tok)

                    if self._escalate_if_provider_rejected(_jsonl_path, "Reviewer"):
                        time.sleep(5)
                        continue

                    if not sentinel_found:
                        if self._escalate_if_provider_rejected(_jsonl_path, "Reviewer"):
                            time.sleep(5)
                            continue
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
                    # Diagnostic: log every reviewer-gate verdict so operators
                    # can reconstruct routing from /tmp/orchestrator.log without
                    # reading state files.  Field shape is locked so future
                    # tooling can rely on it.
                    _rev_pass = self.state.get("reviewer_retries", 0) + 1
                    _rev_next = {
                        "PASS": "phase_advance",
                        "ROUTE_EXECUTOR": "executor",
                        "ROUTE_PLANNER": "planner",
                        "ROUTE_ESCALATE": "escalation",
                        "MISSING_ARTIFACTS": "executor",
                        "INFRA_FAILURE": "reviewer",
                        "VISUAL_UNVERIFIED": "reviewer",
                        "BEHAVIORAL_UNVERIFIED": "reviewer",
                        "REGRESSION_UNVERIFIED": "reviewer",
                    }.get(gate_result, "halted")
                    print(
                        f"[REVIEWER_GATE] verdict={gate_result} "
                        f"pass={_rev_pass} next_agent={_rev_next}"
                    )
                    _write_pipeline_event(
                        "reviewer_verdict", raw_id, "reviewer",
                        {
                            "verdict": gate_result,
                            "pass_number": _rev_pass,
                            "next_agent": _rev_next,
                        },
                    )

                    if gate_result != "PASS":
                        # P0 Stage H — only ROUTE_EXECUTOR creates an executor
                        # retry; other non-PASS verdicts (ROUTE_PLANNER,
                        # ROUTE_ESCALATE, MISSING_ARTIFACTS, INFRA_FAILURE,
                        # VISUAL_UNVERIFIED, BEHAVIORAL_UNVERIFIED) don't.
                        # retry_class is always present on the event for
                        # schema stability — None when not applicable so the
                        # UI's typeof-string check works without branching on
                        # absence.
                        _reviewer_retry_class = (
                            "reviewer_rejection"
                            if gate_result == "ROUTE_EXECUTOR"
                            else None
                        )
                        _write_pipeline_event(
                            "gate_fail",
                            raw_id,
                            "reviewer",
                            {
                                "gate_result": gate_result,
                                "last_error_code": self.read_phase_state().get("last_error_code"),
                                "retry_class": _reviewer_retry_class,
                            },
                        )  # W1-F
                        if self._escalate_if_provider_rejected(_jsonl_path, "Reviewer"):
                            time.sleep(5)
                            continue
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

                            # Hard guard: ensure HEAD is on the phase branch before staging.
                            # This is the authoritative check — commits MUST land on branch,
                            # not on base. If correction fails, escalate rather than corrupt
                            # the repository topology.
                            if not self._ensure_phase_branch(branch):
                                print(f"[ERROR] Phase {phase}: cannot ensure branch '{branch}' before commit — escalating.")
                                self.state["current_agent"] = "escalation"
                                self.transition_state(
                                    "RUNNING",
                                    f"Phase {phase} branch integrity failure: cannot checkout '{branch}'",
                                )
                                time.sleep(5)
                                continue

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

                                # Structured diagnosis: give the escalation agent concrete facts
                                # rather than only the raw git error string.
                                _head_sha = subprocess.run(
                                    ["git", "rev-parse", "HEAD"],
                                    cwd=SYMLINK_TARGET, capture_output=True, text=True
                                ).stdout.strip()
                                _branch_present = subprocess.run(
                                    ["git", "show-ref", "--verify", f"refs/heads/{branch}"],
                                    cwd=SYMLINK_TARGET, capture_output=True
                                ).returncode == 0
                                _ps_mf = self.read_phase_state()
                                _ps_mf.update({
                                    "last_error_code": "ERR_MERGE_FAILED",
                                    "merge_failure_reason": _merge_reason,
                                    "merge_failure_branch": branch,
                                    "merge_failure_branch_exists": _branch_present,
                                    "merge_failure_last_good_commit": self.state.get("phase_base_commit", "unknown"),
                                    "merge_failure_head_commit": _head_sha,
                                })
                                self.write_phase_state_atomic(_ps_mf)

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
                                
                        # 3.1 Canonical metrics row — see _write_canonical_metrics_row
                        # for full rationale.  Extracted to a method so the history-
                        # preservation logic is testable in isolation and the writer is
                        # not exposed to symbol drift inside the deep reviewer-PASS block.
                        try:
                            self._write_canonical_metrics_row()
                        except Exception as _metrics_err:
                            print(
                                f"[ERROR] Failed to write canonical metrics row: "
                                f"{_metrics_err}"
                            )

                        # Phase 3 — record the terminal outcome. phase_state.json is
                        # deleted ~50 lines below on advance, so this value is durable
                        # only via the audit archive (which copies phase_state.json just
                        # below) and a restart landing before the delete. The DURABLE
                        # completion record is the metrics row + phase_complete event;
                        # the dashboard must read completion from the metrics row.
                        self._record_phase_outcome(last_phase_outcome="completed")

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
                                    "metrics.jsonl",
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
                            # P1 Stage F — advisory channel; same per-phase artifact lifecycle.
                            "executor_advisory_detail.json",
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
                                # W5-B: completion review (opt-in via queue entry flag, never gates PIPELINE_COMPLETE)
                                _cr_queue = self._read_queue()
                                _, _cr_entry = self._find_active_queue_entry(_cr_queue)
                                if _cr_entry and _cr_entry.get("completion_review"):
                                    _run_completion_review(self, project_basename=os.path.basename(SYMLINK_TARGET))
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
                        # Augment failure_context.json with reviewer-handoff
                        # metadata so the next executor pass can distinguish
                        # a reviewer-driven retry from a generic gate fail
                        # and see the canonical blocking-issue list with
                        # ``source="reviewer"``.  The general
                        # write_failure_context above already wrote the
                        # comprehensive context including blocking_issues —
                        # this layers the focused-schema fields on top.
                        _rv_out = {}
                        _rv_out_path = os.path.join(
                            PROJECT_ARTIFACTS_DIR, "reviewer_output.json"
                        )
                        if os.path.exists(_rv_out_path):
                            try:
                                with open(_rv_out_path) as _rvf:
                                    _rv_out = json.load(_rvf) or {}
                            except Exception:
                                _rv_out = {}
                        self._write_reviewer_failure_context(
                            blocking_issues=_rv_out.get("blocking_issues") or [],
                            reviewer_summary=_rv_out.get("summary"),
                            reviewer_pass=self.state.get("reviewer_retries", 0) + 1,
                        )
                        # Clear the stale ``executor_succeeded`` flag.  If left
                        # set, the crash-recovery skip guard (~line 3823) sees
                        # ``retries == 0`` and ``executor_succeeded == True``,
                        # short-circuits to "advance to reviewer", and never
                        # actually invokes the executor — the reviewer then
                        # reviews the same rejected output, rejects again, and
                        # the loop continues until ROUTE_ESCALATE fires.  Live
                        # regression observed on UI-E1 (3 ROUTE_EXECUTORs, 0
                        # executor invocations).  The reviewer rejecting the
                        # work means the prior executor output is no longer
                        # considered "succeeded" for crash-recovery purposes.
                        _ps_re = self.read_phase_state()
                        _ps_re.pop("executor_succeeded", None)
                        # P0 Stage H — increment the lifetime
                        # executor_reviewer_rejection_retries counter. Lives at
                        # the handler site, not inside reset_execution(), because
                        # the rejection path bypasses reset_execution entirely
                        # (it manually resets executor_retries=0 below as the
                        # per-segment budget, then re-invokes the executor).
                        # Co-locating the increment with the rejection event
                        # keeps the counter accurate even if reset_execution is
                        # refactored later.
                        _ps_re["executor_reviewer_rejection_retries"] = (
                            _ps_re.get("executor_reviewer_rejection_retries", 0) + 1
                        )
                        self.write_phase_state_atomic(_ps_re)
                        # Mirror to self.state (state-sync invariant).
                        self.state["executor_reviewer_rejection_retries"] = (
                            _ps_re["executor_reviewer_rejection_retries"]
                        )
                        # Tracker for next attempt's event labelling.
                        self._current_attempt_retry_class = "reviewer_rejection"
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
                        # Infrastructure failure: the reviewer produced no parseable
                        # output (missing/malformed reviewer_output.json) — NOT a code-
                        # quality rejection, so reviewer_retries is untouched. Self-heal
                        # by re-invoking the reviewer in a fresh session (cap
                        # reviewer_infra_retries); a transient malformed-output fluke
                        # almost always clears on a clean re-run. Agent/model liveness is
                        # covered by the OpenClaw activity-stamp hooks (startup-grace /
                        # stall detection), so no model-health probe is performed.
                        _ps_if = self.read_phase_state()
                        _infra_soft = _ps_if.get("reviewer_infra_retries", 0) + 1
                        _ps_if["reviewer_infra_retries"] = _infra_soft
                        self.write_phase_state_atomic(_ps_if)
                        print(f"[WARN] Reviewer INFRA_FAILURE — soft retry {_infra_soft}/3.")
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

                    elif gate_result in (
                        "VISUAL_UNVERIFIED",
                        "BEHAVIORAL_UNVERIFIED",
                        "REGRESSION_UNVERIFIED",
                    ):
                        # P1 Stage D: pooled contract-shape handler. Visual,
                        # behavioural, and regression shape failures all
                        # re-invoke the reviewer with a verdict-specific
                        # instruction, drawing from a single pooled counter
                        # ``reviewer_unverified_retries`` (cap 2 across all
                        # three). None of these consume the main
                        # ``reviewer_retries`` budget; a legitimate
                        # ROUTE_EXECUTOR rejection on the following pass is
                        # preserved.
                        _UNVERIFIED_INSTRUCTIONS = {
                            "VISUAL_UNVERIFIED": (
                                "VISUAL VERIFICATION REQUIRED: Before writing "
                                "reviewer_output.done, you MUST attach screenshot "
                                "paths and a visual_verification block to "
                                "reviewer_output.json per your AGENTS.md.  A "
                                "phase that touches UI cannot pass without this."
                            ),
                            "BEHAVIORAL_UNVERIFIED": (
                                "BEHAVIORAL VERIFICATION REQUIRED: Before writing "
                                "reviewer_output.done, you MUST attach a "
                                "``behavioral_verification`` object to "
                                "reviewer_output.json with verdict ∈ "
                                "{pass, fail, cannot_verify}, at least three "
                                "evidence anchors when verdict='pass' (each with "
                                "claim + file_or_screenshot_or_log + method), and "
                                "how_to_check_followed as a boolean — see your "
                                "AGENTS.md. A phase whose current_phase.json "
                                "carries a Behavioral Verification block cannot "
                                "pass without this."
                            ),
                            "REGRESSION_UNVERIFIED": (
                                "REGRESSION VERIFICATION REQUIRED: Before writing "
                                "reviewer_output.done, you MUST attach a "
                                "``regression_verification`` object to "
                                "reviewer_output.json with verdict ∈ "
                                "{pass, fail, cannot_verify}, "
                                "prior_phase_raw_id matching "
                                "current_phase.prior_phase_raw_id, "
                                "prior_phase_how_to_check_followed as a boolean, "
                                "and at least three evidence anchors when "
                                "verdict='pass' and followed=True (each with "
                                "claim + file_or_screenshot_or_log + method). "
                                "Execute current_phase.prior_phase_how_to_check "
                                "against the artifact and report what you saw."
                            ),
                        }
                        _ps_uv = self.read_phase_state()
                        _uv_retries = _ps_uv.get("reviewer_unverified_retries", 0) + 1
                        _ps_uv["reviewer_unverified_retries"] = _uv_retries
                        _ps_uv["unverified_instruction"] = _UNVERIFIED_INSTRUCTIONS[gate_result]
                        self.write_phase_state_atomic(_ps_uv)
                        print(
                            f"[WARN] Reviewer gate: {gate_result} "
                            f"(unverified_retries={_uv_retries}/2)."
                        )
                        if _uv_retries >= 2:
                            print(
                                f"[WARN] {gate_result} retry cap reached — "
                                f"escalating."
                            )
                            self.state["current_agent"] = "escalation"
                            self.transition_state(
                                "RUNNING",
                                f"Reviewer {gate_result}: contract-shape retry "
                                f"cap reached ({_uv_retries})",
                            )
                        else:
                            self.state["current_agent"] = "reviewer"
                            self.transition_state(
                                "RUNNING",
                                f"Reviewer {gate_result}: re-invoking reviewer "
                                f"to fix contract-shape failure "
                                f"(attempt {_uv_retries}/2)",
                            )
                        time.sleep(5)
                        continue

                    else:
                        # Unknown / unrecognised reviewer-gate verdict.  The
                        # previous open-elif chain silently fell through here,
                        # leaving ``current_agent`` as "reviewer" — the next
                        # loop iteration would re-invoke the reviewer in a
                        # fresh session, producing the CORE-E6 reviewer→
                        # reviewer loop symptom.  Fail loudly instead so an
                        # operator sees the problem in /tmp/orchestrator.log
                        # and a future-added gate verdict cannot regress to
                        # a silent loop.
                        print(
                            f"[FATAL] Unknown reviewer-gate verdict "
                            f"{gate_result!r}.  Refusing to silently re-invoke "
                            f"the reviewer.  Escalating to HALTED_SILENT so "
                            f"an operator can add the missing handler."
                        )
                        self.transition_state(
                            "HALTED_SILENT",
                            f"Unknown reviewer-gate verdict: {gate_result!r}",
                        )
                        return

                elif current_agent == "escalation":
                    if self._should_invoke_escalation_agent():
                        phase = self.state.get("current_phase", 0)
                        raw_id = self.state.get("current_phase_raw_id", "unknown")
                        session_key = f"pipeline:phase-{phase}:{raw_id}:escalation"
                        token = self.openclaw_config.get("hooks", {}).get("token", "")
                        
                        cleanup_output_files(PROJECT_ARTIFACTS_DIR, "escalation")
                        _ps = self.read_phase_state()
                        _ps["escalation_trigger_reason"] = self.state.get("last_action", "escalation triggered")
                        # P1 Stage G1: persist a clean, non-blame headline for the UI alongside
                        # the raw trigger reason (which is demoted into the details disclosure).
                        _ps["escalation_headline"] = self._clean_escalation_headline(raw_id)
                        _ps["escalations"] = _ps.get("escalations", 0) + 1  # W1-B
                        _ps["last_phase_outcome"] = "escalated"  # Phase 3 — terminal outcome
                        _ps["waiting_for_human_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # W1-E

                        # Signal "generating" so the UI can show a spinner immediately
                        _ps["escalation_advisory_status"] = "generating"
                        self.write_phase_state_atomic(_ps)

                        # Generate LLM advisory before the webhook fires so the UI and
                        # the agent's outbound notification both carry human-readable context
                        _advisory = self._generate_escalation_advisory()
                        if _advisory:
                            _ps["escalation_message"] = _advisory["summary"]
                            _ps["escalation_recommended_action"] = _advisory["recommended_action"]
                            _ps["escalation_advisory_status"] = "ready"
                        else:
                            _ps["escalation_advisory_status"] = "fallback"
                        self.write_phase_state_atomic(_ps)

                        _write_pipeline_event("escalation_trigger", raw_id, "escalation", {"reason": _ps.get("escalation_trigger_reason")})  # W1-F
                        self.transition_state("WAITING_FOR_HUMAN", "Invoking Escalation Agent")
                        self._queue_park_active_entry("ESCALATION", "escalation")

                        # Build webhook message — include advisory so the escalation agent
                        # can relay it via the operator's configured notification channel
                        _p = PROJECT_ARTIFACTS_DIR
                        if _advisory:
                            _webhook_msg = (
                                f"Pipeline needs operator attention.\n\n"
                                f"Advisory: {_advisory['summary']}\n"
                                f"Suggested action: {_advisory.get('recommended_action', 'See dashboard.')}\n\n"
                                f"Read {_p}/phase_state.json and relevant output files for full context. "
                                f"Send a notification to the operator via your configured channel including "
                                f"the advisory above, then write your assessment to "
                                f"{_p}/escalation_output.json and {_p}/escalation_output.done."
                            )
                        else:
                            _webhook_msg = None  # falls through to default in webhook_client.py
                        webhook_status = invoke_agent_webhook(
                            "escalation", session_key, token, message=_webhook_msg
                        )

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

                            # If the escalation agent wrote a better summary during its
                            # interactive session, let it overwrite the pre-generated advisory
                            _advisory_resolved = self._read_escalation_advisory()
                            _ps = self.read_phase_state()
                            if _advisory_resolved:
                                _ps["escalation_message"] = _advisory_resolved["summary"]
                                _ps["escalation_recommended_action"] = _advisory_resolved["recommended_action"]
                                _ps["escalation_advisory_status"] = "ready"
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
                            elif command == "NUCLEAR_RESET":
                                # P1 Stage G2 — operator escape hatch, governed by its OWN
                                # nuclear_resets cap (2), independent of escalation_resets.
                                # Available precisely BECAUSE the escalation cap is spent;
                                # nuclear_reset_phase() does the increment + reset_log + the
                                # destructive reset_phase mechanics.
                                self._queue_restore_parked_entry_to_active()
                                _ps = self.read_phase_state()
                                if _ps.get("nuclear_resets", 0) >= 2:
                                    self.send_signal_notification(
                                        "Nuclear reset cap reached (2). Only Abandon Phase or Stop remain for this phase."
                                    )
                                else:
                                    self.nuclear_reset_phase()
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
                                # Empty / missing / unrecognised command. Previously dead-ended to
                                # HALTED_SILENT + queue FAILED (unrecoverable in the UI). Now emit a
                                # loud signal and default to STOP — recoverable via the Resume control
                                # — matching the JSON-parse fallback above and
                                # _apply_pending_escalation_command. See PIPELINE-CONSTRAINTS.md §5.2.
                                print(f"[WARN] Unrecognised escalation command {command!r}; defaulting to STOP.")
                                _write_pipeline_event(
                                    "escalation_command_invalid",
                                    raw_id,
                                    "escalation",
                                    {"received_command": command, "defaulted_to": "STOP"},
                                )
                                stop_file = os.path.join(PROJECT_ARTIFACTS_DIR, "pipeline_stop_requested")
                                try:
                                    with open(stop_file, 'w') as _sf:
                                        _sf.write("")
                                except OSError as _e:
                                    print(f"[WARN] STOP: could not write stop sentinel: {_e}")
                                _write_run_summary("STOPPED", f"Unrecognised escalation command {command!r}; defaulted to STOP")  # W2-B
                                self.transition_state("STOPPED", f"Unrecognised escalation command {command!r}; defaulted to STOP")
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
                    # P1 Stage G1: clean headline for the UI (raw reason stays in the disclosure).
                    _ps["escalation_headline"] = self._clean_escalation_headline(raw_id)
                    # Advisory generated AFTER webhook in the crash handler — the webhook
                    # must not be delayed by an LLM call in this last-resort path
                    _exc_advisory = self._generate_escalation_advisory()
                    if _exc_advisory:
                        _ps["escalation_message"] = _exc_advisory["summary"]
                        _ps["escalation_recommended_action"] = _exc_advisory["recommended_action"]
                        _ps["escalation_advisory_status"] = "ready"
                    else:
                        _ps["escalation_advisory_status"] = "fallback"
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
