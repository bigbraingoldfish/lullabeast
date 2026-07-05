"""Lullabeast pipeline orchestrator — the single state machine that drives a run.

This module owns the entire deterministic build loop: it resolves the next roadmap
phase, invokes the planner / executor / reviewer / escalation agents through the
OpenClaw gateway, runs the gate scripts between each handoff, performs every git
operation (phase branches, commits, merges), and handles retries, escalation, and
crash recovery. The queue logic that sequences multiple projects lives here too.

It is **intentionally one large file.** The pipeline's control flow — state
transitions, agent coordination, git side effects, and the shared mutable state they
all touch — is kept in one place so the whole loop is auditable end to end without
chasing call paths across modules. Extracting helpers requires understanding that
shared state; see CLAUDE.md ("intentional single-file design") before refactoring.

The authoritative architecture spec is autodev/docs/PIPELINE-SPEC.md; the state
machine, gate contracts, and sentinel-polling rules are summarized in CLAUDE.md.
"""
import os
import sys
try:
    import fcntl
except ModuleNotFoundError:  # pragma: no cover - native Windows lacks POSIX fcntl
    raise SystemExit(
        "AutoDev requires Linux, macOS, or WSL2. Native Windows is not "
        "supported (the pipeline uses POSIX fcntl advisory locking) — "
        "run AutoDev under WSL2."
    )
import json
import re
import secrets
import shutil
import time
import subprocess
import traceback
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
import logging
import requests

from webhook_client import (
    abort_agent_session,
    invoke_agent_webhook,
    set_session_response_usage,
    verify_session_stopped,
)
from sentinel_poller import cleanup_output_files, initialize_activity_stamp, poll_for_sentinel
from skill_manager import SkillManager
from event_log import append_pipeline_event
from queue_semantics import (
    parent_blocks_child, ESCALATION_ANSWERED, REVIVABLE_ANSWERED_STATES,
    QUEUE_MAX_CAS_RETRIES, QUEUE_VERSION_KEY, QueueAbort, QueueVersionConflict,
    bump_queue_version, mutate_queue, read_queue_version, scrub_parked_fields,
)
from env_resolvers import resolve_openclaw_root, resolve_pipeline_root, load_repo_env_file
from atomic_io import write_json_atomic, write_text_atomic
from error_codes import (
    ERR_MERGE_FAILED,
    ERR_PHASE_RESOLVER_FAILED,
    ERR_PROVIDER_REJECTED,
    ERR_RESET_EXECUTION_GIT_FAILED,
    ERR_RESET_PHASE_GIT_FAILED,
    ERR_REVIEWER_CONTRACT_FAILURE,
    ERR_REVIEWER_MODEL_ERROR,
    ERR_ROADMAP_CHECKBOX_FAILED,
    ERR_SESSION_DEAD_ON_ARRIVAL,
    ERR_TOOL_LOOP,
    ERR_UNACCOUNTED_DELETION,
)
from log_utils import configure_stream_logging

# Route module-level logging.* calls (including those from webhook_client —
# notably abort_agent_session's success/failure lines) to stdout so they land
# in the same operator-facing stream as the orchestrator's print() output.
# Without this, logging defaults to stderr without a configured handler and
# abort outcomes disappear from /tmp/orchestrator.log.  Idempotent — only
# attaches a handler if no stdout-bound INFO handler is already present
# (so tests that pre-configure logging are not clobbered).
def _ensure_stdout_logging() -> None:
    """Route module-level ``logging.*`` to stdout at INFO (root logger).

    Thin wrapper over :func:`log_utils.configure_stream_logging` — kept as a
    named function because tests import it by name and it reads clearly at the
    call site. Idempotent; re-binds to a swapped ``sys.stdout`` (pytest capsys).
    """
    configure_stream_logging(None, logging.INFO)


_ensure_stdout_logging()

OPENCLAW_ROOT = resolve_openclaw_root()
AUTODEV_REPO_PATH = os.environ.get(
    "AUTODEV_REPO_PATH",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Directory holding the deterministic gate scripts the orchestrator invokes as subprocesses
# (planner/executor/reviewer verdict gates + phase_resolver / repo_init_check). Single source
# of truth for the path so it is never rebuilt inline at each call site; see
# autodev/pipeline/gate_scripts/README.md for the gate execution-model contract.
GATE_SCRIPTS_DIR = os.path.join(AUTODEV_REPO_PATH, "autodev", "pipeline", "gate_scripts")


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _env_int(env_name: str, default_str: str, *, min_clamp: int = 1) -> int:
    """Parse an int from ``env_name``; fall back to ``default_str`` on a missing or
    non-numeric value, then clamp to a ``min_clamp`` floor.

    The shared body behind the named timeout / grace / backstop wrappers below —
    each keeps its own docstring (the *why* of its threshold) and delegates the
    parse + clamp here so the parsing discipline lives in exactly one place.
    ``min_clamp`` is 1 for the poll thresholds (a 0 would race poll-tick cadence)
    and 0 for the escalation-summary hold (0 means "disable the hold").
    """
    raw = (os.environ.get(env_name) or "").strip()
    try:
        v = int(raw or default_str)
    except ValueError:
        v = int(default_str)
    return max(min_clamp, v)


def _stall_timeout_seconds(env_name: str, default_str: str) -> int:
    """Parse stall-detection threshold from env; invalid values fall back to default.

    Governs **post-first-hook** silence: once the plugin has touched the
    activity stamp at least once, any subsequent gap exceeding this
    threshold treats the attempt as stalled.  Independent from startup
    grace — see :func:`_startup_grace_seconds`.
    """
    return _env_int(env_name, default_str)


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
    return _env_int(env_name, default_str)


def _infra_backstop_seconds(env_name: str, default_str: str) -> int:
    """Parse the infrastructure backstop from env; invalid values fall back.

    Governs ``poll_for_sentinel``'s ``timeout_seconds`` — the gateway-dead
    failsafe that bounds an attempt even while the activity stamp keeps
    refreshing.  Stall detection (:func:`_stall_timeout_seconds`) and
    startup grace remain the death-detectors; this cap exists for the
    zombie-streaming case, so it must be tunable to the hardware tier: on
    a local-model host a thorough long-context reviewer pass can
    legitimately need ~5 min *per model call* (observed live, WORLD-E1
    2026-06-11 — three consecutive 75-min timeouts while the agent was
    alive and working the whole time).

    Parsing semantics mirror :func:`_stall_timeout_seconds` exactly:
    invalid strings fall back to ``default_str``, minimum clamped to 1 s.
    """
    return _env_int(env_name, default_str)


# Tier 1 in-turn tool-loop catcher -------------------------------------------
# How often the per-poll detector closure actually re-scans the session JSONL
# (it is consulted every ~2 s poll tick but self-throttles to this interval, so
# the tail read stays negligible). A loop worth catching runs for minutes, so a
# ~15 s cadence catches it within ~threshold*call-interval while costing ~one
# small file read per quarter-minute.
_TOOL_LOOP_CHECK_INTERVAL_SECONDS = 15
# Tools that legitimately repeat with identical args (polling a long-running
# process) — never counted as a loop, mirroring OpenClaw's known-poll exclusion.
_TOOL_LOOP_EXCLUDED_TOOLS = frozenset({"process", "command_status"})
# The session-JSONL tail read scales with the threshold so it can always hold
# `limit` rows: a fixed window can't see `limit` repetitions when each looping
# call serializes large args (a write/apply_patch of a big file), which would
# silently defeat detection for the executor — the role most prone to those.
# 512 KB/row is generous headroom over a large file-write row (args + thinking),
# bounded by a hard cap so a pathologically-high limit can't read unboundedly.
_TOOL_LOOP_PER_ROW_TAIL_BYTES = 524288      # 512 KB per expected row
_TOOL_LOOP_MAX_TAIL_BYTES = 67108864        # 64 MB cap on a single scan's read


def _tool_loop_repeat_limit(env_name: str, default_str: str) -> int:
    """Parse a per-role consecutive-identical-tool-call threshold from ``env_name``.

    Returns ``0`` when the value is exactly ``"0"`` — the explicit *disable* switch
    for that role's detector (the caller passes no ``loop_detector``). Any other
    value is clamped to a floor of ``2`` (a single call can never be a "loop", so
    ``1`` reads as ``2``); garbage / missing falls back to ``default_str``. Mirrors
    the per-role :func:`_stall_timeout_seconds` knobs, unprefixed per convention.
    """
    raw = (os.environ.get(env_name) or "").strip()
    if raw == "0":
        return 0
    try:
        v = int(raw or default_str)
    except ValueError:
        v = int(default_str)
    return 0 if v == 0 else max(2, v)


def _detect_tool_loop_in_jsonl(jsonl_path, limit):
    """Scan an OpenClaw session JSONL for an in-turn tool-call loop.

    Returns ``{"tool_name", "args_excerpt", "repeat_count"}`` when the **trailing
    run of consecutive identical ``(tool_name, args)`` tool calls** is at least
    ``limit`` long, else ``None``. "Identical" is input-only — deliberately NOT the
    (input AND output) basis OpenClaw's own block uses, which jittered command
    output defeats (the failure this catcher exists for).

    Robust to both on-disk content-block shapes: llamacpp / openai-completions
    ``{"type":"toolCall","name","arguments"}`` (the shape observed looping live) and
    Anthropic ``{"type":"toolUse"/"tool_use","name","input"}``. Legitimately-repeated
    poll tools (:data:`_TOOL_LOOP_EXCLUDED_TOOLS`) never count.

    Fail-safe: a missing/unreadable/odd-shaped file (or any parse error) returns
    ``None`` — the detector must never false-abort a healthy agent. Reads only the
    file tail (the trailing run is all that matters), so cost is bounded regardless
    of how large the looping session has grown.
    """
    if not jsonl_path or not os.path.exists(jsonl_path):
        return None
    try:
        tail_bytes = min(
            _TOOL_LOOP_MAX_TAIL_BYTES,
            max(131072, limit * _TOOL_LOOP_PER_ROW_TAIL_BYTES),
        )
        with open(jsonl_path, "rb") as f:
            try:
                f.seek(-tail_bytes, os.SEEK_END)  # limit-scaled tail (holds `limit` rows)
                partial = True
            except OSError:
                f.seek(0)  # file smaller than the tail window
                partial = False
            blob = f.read().decode("utf-8", "replace")
        lines = blob.split("\n")
        if partial and lines:
            lines = lines[1:]  # drop the possibly-truncated first line

        # Ordered list of (tool_name, stable-args) across assistant tool calls.
        calls = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if row.get("type") != "message":
                continue
            inner = row.get("message")
            if not isinstance(inner, dict) or inner.get("role") != "assistant":
                continue
            content = inner.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") not in ("toolCall", "toolUse", "tool_use"):
                    continue
                name = block.get("name")
                if not name:
                    continue
                args = block.get("arguments")
                if args is None:
                    args = block.get("input")
                try:
                    args_sig = json.dumps(args, sort_keys=True)
                except (TypeError, ValueError):
                    args_sig = str(args)
                calls.append((name, args_sig))

        if not calls:
            return None
        last_name, last_args = calls[-1]
        if last_name in _TOOL_LOOP_EXCLUDED_TOOLS:
            return None
        run = 0
        for name, args_sig in reversed(calls):
            if name == last_name and args_sig == last_args:
                run += 1
            else:
                break
        if run < limit:
            return None
        return {
            "tool_name": last_name,
            "args_excerpt": last_args[:200],
            "repeat_count": run,
        }
    except OSError:
        return None


def _escalation_summary_wait_seconds() -> int:
    """Parse the escalation-summary auto-advance hold budget from env.

    Bounds how long the main escalation dispatch holds a queue auto-advance
    for the escalation agent's ``escalation_summary.json`` write — the
    advance repoints the pipeline-project symlink out from under the
    in-flight write (see ``_wait_for_escalation_summary_before_advance``).

    Parsing mirrors :func:`_stall_timeout_seconds` — garbage falls back to
    the default (300) — except the minimum clamp is **0, not 1**: 0 is a
    meaningful operator value here (disable the hold entirely).
    """
    return _env_int("AUTODEV_ESCALATION_SUMMARY_WAIT", "300", min_clamp=0)


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

# Per-verdict reviewer retry instructions for the contract-shape verdicts. Shared by
# _compose_unverified_directive (the *_UNVERIFIED handler enriches these with the gate's
# specific problem list). Module-level so the directive composition is unit-testable.
_UNVERIFIED_INSTRUCTIONS = {
    "VISUAL_UNVERIFIED": (
        "VISUAL VERIFICATION REQUIRED: Before writing reviewer_output.done, you "
        "MUST attach screenshot paths and a visual_verification block to "
        "reviewer_output.json per your AGENTS.md.  A phase that touches UI cannot "
        "pass without this."
    ),
    "BEHAVIORAL_UNVERIFIED": (
        "BEHAVIORAL VERIFICATION REQUIRED: Before writing reviewer_output.done, you "
        "MUST attach a ``behavioral_verification`` object to reviewer_output.json "
        "with verdict ∈ {pass, fail, cannot_verify}, at least three evidence anchors "
        "when verdict='pass' (each with claim + file_or_screenshot_or_log + method), "
        "and how_to_check_followed as a boolean — see your AGENTS.md. A phase whose "
        "current_phase.json carries a Behavioral Verification block cannot pass "
        "without this."
    ),
    "REGRESSION_UNVERIFIED": (
        "REGRESSION VERIFICATION REQUIRED: Before writing reviewer_output.done, you "
        "MUST attach a ``regression_verification`` object to reviewer_output.json "
        "with verdict ∈ {pass, fail, cannot_verify}, prior_phase_raw_id matching "
        "current_phase.prior_phase_raw_id, prior_phase_how_to_check_followed as a "
        "boolean, and at least three evidence anchors when verdict='pass' and "
        "followed=True (each with claim + file_or_screenshot_or_log + method). "
        "Execute current_phase.prior_phase_how_to_check against the artifact and "
        "report what you saw."
    ),
}

# Hard cap for gate script subprocess.run — prevents hung gates from stalling the orchestrator.
GATE_SUBPROCESS_TIMEOUT = 60

# Canonical pipeline_status values. transition_state() — the ONLY writer — rejects
# (raises ValueError on) anything not in this list. `IDLE` is intentionally absent:
# it is a reset/entry status written ONLY by external resetters (the UI / tooling)
# via a direct atomic write to pipeline_state.json, never a transition_state target.
# At startup the orchestrator treats IDLE as non-terminal; the first real transition
# (e.g. "Invoking Planner" -> WAITING_FOR_SENTINEL) overwrites it. Do not add IDLE here.
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

# 3-A / P1-A — statuses that mean "a run is still in flight". A same-project
# --project-path (re)start while the on-disk status is one of these is a crash/restart
# RESUME of that run, so run_started_at AND run_id are preserved; any other
# (terminal/idle) status — or no stamp at all — means a NEW run is beginning (e.g.
# queue trigger-next re-running a finished project) and both are freshly stamped. See
# apply_cli_project_path. (run_started_at = WHEN the run began; run_id = WHICH run.)
_RESUMABLE_ACTIVE_RUN_STATUSES = frozenset(
    {"RUNNING", "WAITING_FOR_SENTINEL", "WAITING_FOR_HUMAN", "QUEUE_HALTED"}
)

# P1-B — structured escalation taxonomy. Every escalation carries one of these
# classes (in the `escalation_trigger` event detail + the canonical metrics row), so
# "escalations by cause" is answerable from durable data without parsing the free-text
# reason. Resolution (Orchestrator._resolve_escalation_trigger_class) prefers an
# explicit per-chokepoint stamp, else derives from `last_error_code`, else `"unknown"`
# — so the taxonomy degrades gracefully and never silently lies.
ESCALATION_TRIGGER_CLASSES = frozenset({
    "planner_retries_exhausted",
    "executor_retries_exhausted",
    "reviewer_retries_exhausted",
    "reviewer_routed",            # reviewer gate returned ROUTE_ESCALATE
    "reviewer_verification_unmet",  # missing-artifacts / contract / unverified caps
    "provider_rejected",          # provider quota/policy reject or session dead-on-arrival
    "webhook_failure",            # /hooks/agent returned non-SUCCESS
    "resolver_failed",            # phase_resolver exit 1 / crash
    "roadmap_checkbox_failed",
    "repo_init_failed",
    "stamp_init_failed",          # activity-stamp workspace unwritable
    "reset_git_failed",           # reset_phase / reset_execution git failure
    "git_op_failed",              # branch / merge / tag op failed on the PASS path
    "preempted_output_invalid",
    "gate_crash",                 # unhandled / unrecognised gate verdict
    "unknown",                    # derivation fallback
})

# Error codes that uniquely identify a cause — the error-coded chokepoints are
# classified by derivation, so they need no explicit stamp.
_ERR_CODE_TO_TRIGGER_CLASS = {
    ERR_PROVIDER_REJECTED: "provider_rejected",
    ERR_SESSION_DEAD_ON_ARRIVAL: "provider_rejected",
    ERR_RESET_PHASE_GIT_FAILED: "reset_git_failed",
    ERR_RESET_EXECUTION_GIT_FAILED: "reset_git_failed",
    ERR_ROADMAP_CHECKBOX_FAILED: "roadmap_checkbox_failed",
    ERR_PHASE_RESOLVER_FAILED: "resolver_failed",
    ERR_MERGE_FAILED: "git_op_failed",
}


def _derive_escalation_trigger_class(last_error_code):
    """Map a distinguishing ERR_* code to its escalation_trigger_class, or
    ``"unknown"`` when the code is missing/unmapped.

    The exhaustion / reviewer-routing / webhook / stamp-init / repo-init chokepoints
    carry no distinguishing code at dispatch time and instead stamp the class
    explicitly (see ``Orchestrator._resolve_escalation_trigger_class``)."""
    return _ERR_CODE_TO_TRIGGER_CLASS.get(last_error_code or "", "unknown")

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


def _write_escalation_failed_atomic(target_dir, error_data):
    """Atomically write ``escalation_failed.json`` into ``target_dir``.

    House atomic-write rule (mkstemp + os.replace): the file is the operator's
    escalation-delivery-failure diagnostic — a reader must never observe a
    torn write. Shared by the three escalation-failure sites (repo-init
    dispatch, main dispatch, crash handler). Never raises: every caller is
    already on a failure path, and a diagnostics-write failure must not mask
    the original error or derail the HALTED_SILENT transition that follows.
    """
    try:
        write_json_atomic(
            os.path.join(target_dir, "escalation_failed.json"), error_data, indent=None)
    except Exception as e:
        print(f"[ERROR] Could not write escalation_failed.json: {e}")


# Glob patterns for mkstemp atomic-write temp files that may be stranded if the
# orchestrator was killed mid-write.  Pattern matches the 8-character random hex
# suffix produced by tempfile.mkstemp (e.g. pipeline_state_a3f7c219).
_STRANDED_TEMP_PATTERNS = [
    # Legacy mkstemp(prefix="X_") temp names (pre-LAUNCH-5) — kept so an upgrade
    # that inherits a crash-stranded temp from the old code still cleans it.
    "pipeline_state_????????",
    "phase_state_????????",
    "current_phase_????????",
    # atomic_io.write_*_atomic temp names: mkstemp(prefix="<dest>.", suffix=".tmp").
    "pipeline_state.json.*.tmp",
    "phase_state.json.*.tmp",
    "current_phase.json.*.tmp",
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
    except OSError:
        pass  # best-effort: a dangling project path just yields fewer search dirs

    removed = []
    for directory in search_dirs:
        for pattern in _STRANDED_TEMP_PATTERNS:
            for stale_path in _glob.glob(os.path.join(directory, pattern)):
                try:
                    os.remove(stale_path)
                    removed.append(stale_path)
                except OSError:
                    pass  # best-effort temp cleanup
        # Stranded mkstemp files may also live under .autodev/pipeline/.
        _ad_pipe = os.path.join(directory, ".autodev", "pipeline")
        if os.path.isdir(_ad_pipe):
            for pattern in _STRANDED_TEMP_PATTERNS:
                for stale_path in _glob.glob(os.path.join(_ad_pipe, pattern)):
                    try:
                        os.remove(stale_path)
                        removed.append(stale_path)
                    except OSError:
                        pass  # best-effort temp cleanup

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
    except OSError:
        pass  # best-effort: artifact listing is for logging only
    logging.info(
        "[startup] pipeline artifacts present in workspace: %s",
        sorted(artifacts) if artifacts else [],
    )


# T4.6 — seconds to allow each base-branch git probe before falling back to "main".
# Bounded because _detect_base_branch runs on the reset path while pipeline.lock is held.
_BASE_BRANCH_PROBE_TIMEOUT = 10

# Session-interrupt tuning (consolidated _interrupt_agent_session helper). OpenClaw's
# sessions.steer is interrupt+inject: it aborts the embedded run AND always enqueues a
# follow-up turn carrying the stop message (active or idle). So on the skip_if_idle paths we
# (1) skip the steer only when the agent's turn has PROVABLY ended — read from the session
# transcript's last assistant row (_agent_turn_still_in_flight), the same signal the verdict-hold
# acceptor trusts, NOT a stamp-movement window (the stamp is silent for a whole model call, so a
# short window read a live mid-call agent as idle and skipped the abort) — and (2) wait for the
# stamp to settle afterward, since the steer's own spawned turn refreshes the stamp and the old
# instant verify_session_stopped() false-failed on every abort.
_INTERRUPT_SETTLE_QUIET = 3.0      # post-steer: stamp must stay quiet this long to count as "settled"
_INTERRUPT_SETTLE_MAX = 45.0       # post-steer: hard ceiling on the settle wait before soft-continue


def _detect_base_branch(directory: str) -> str:
    """Return the best candidate base branch for the target repository.

    T4.6 — every git probe is bounded by ``_BASE_BRANCH_PROBE_TIMEOUT`` so a
    wedged git cannot hang the pipeline (this runs on the reset path while the
    exclusive ``pipeline.lock`` is held, so heartbeat-cron cannot restart a hung
    orchestrator). A missing git binary, a dangling/unreadable ``directory``, or
    a probe timeout all fall back to "main" — the caller (`reset_phase`) already
    treats "main" as the safe default base branch.
    """
    for branch in ("main", "master", "develop", "trunk"):
        try:
            result = subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
                cwd=directory,
                timeout=_BASE_BRANCH_PROBE_TIMEOUT,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as e:
            print(f"[WARN] _detect_base_branch: git probe failed ({e}); falling back to 'main'.")
            return "main"
        if result.returncode == 0:
            return branch

    try:
        remote_head = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=_BASE_BRANCH_PROBE_TIMEOUT,
        )
        remote_ref = (remote_head.stdout or "").strip()
        if remote_head.returncode == 0 and remote_ref.startswith("refs/remotes/origin/"):
            return remote_ref[len("refs/remotes/origin/"):]

        init_branch = subprocess.run(
            ["git", "config", "--get", "init.defaultBranch"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=_BASE_BRANCH_PROBE_TIMEOUT,
        )
        configured_branch = (init_branch.stdout or "").strip()
        if init_branch.returncode == 0 and configured_branch:
            return configured_branch
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as e:
        print(f"[WARN] _detect_base_branch: git probe failed ({e}); falling back to 'main'.")

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


def _compose_contract_failure_escalation(jsonl_path, contract_soft):
    """Return ``(reason, error_code)`` for a reviewer CONTRACT_FAILURE that has
    reached the soft-retry cap.

    CONTRACT_FAILURE = the reviewer session ended without a parseable
    ``reviewer_output.json``. Several causes are conflated under that one verdict,
    and the operator needs them distinguished:

    * the reviewer genuinely **gave up / was cut off** (no error row) — keep the
      generic message + :data:`ERR_REVIEWER_CONTRACT_FAILURE`;
    * the reviewer's **model hard-errored** *and the session ended on it* — the
      LAST assistant row is ``stopReason:"error"`` (e.g. a 500/server_error from
      GPU contention or model eviction on a shared local host; the live
      image-input-500 was this class) — the reviewer did real work and the
      inference call failed, so surface the real error +
      :data:`ERR_REVIEWER_MODEL_ERROR`.

    Two error classes are deliberately NOT treated as a model hard-error, because
    telling the operator to "check the model host" would be wrong remediation:

    * a **recoverable context overflow** (:func:`_is_recoverable_context_overflow`)
      — the reviewer ran out of context, a context-size problem, not a model-host
      failure;
    * an error the session **recovered past** — there is an error row earlier in
      the log, but the LAST assistant row is not itself an error, so that is not
      how the turn ended.

    Both fall through to the give-up label. The shared
    :func:`_session_jsonl_last_assistant_error_message` returns the last error row
    *anywhere* in the session (correct for the provider-rejection path that reuses
    it), so here it is gated on the session having *terminated* on that error
    (:func:`_session_jsonl_last_assistant_stop_reason` ``== "error"``) and on the
    error not being a recoverable overflow.

    Provider rejections (401/402/429) never reach here — they are peeled off
    upstream by :meth:`_escalate_if_provider_rejected`. Retry behaviour is
    unchanged by this helper; it only labels the *terminal* escalation honestly.
    Never raises (a missing/unreadable session reads as a give-up).
    """
    hard_err = _session_jsonl_last_assistant_error_message(jsonl_path)
    # Only a GENUINE, TERMINAL model error qualifies as ERR_REVIEWER_MODEL_ERROR:
    # the session must have ENDED on an error row (not recovered past it), and that
    # error must not be a recoverable context overflow (a context-size issue, not a
    # model-host failure). Anything else is a give-up.
    terminal_error = _session_jsonl_last_assistant_stop_reason(jsonl_path) == "error"
    if hard_err and terminal_error and not _is_recoverable_context_overflow(hard_err):
        return (
            f"Reviewer model hard-errored after {contract_soft} fresh-session retries "
            f"(ERR_REVIEWER_MODEL_ERROR) — the reviewer did not give up; its inference "
            f"call failed: {hard_err[:400]}",
            ERR_REVIEWER_MODEL_ERROR,
        )
    return (
        f"Reviewer CONTRACT_FAILURE: contract retry cap reached ({contract_soft}): "
        f"CONTRACT_FAILURE_SOFT_RETRY_EXHAUSTED — reviewer ended without a verdict "
        f"(gave up or was cut off)",
        ERR_REVIEWER_CONTRACT_FAILURE,
    )


# Non-terminal stopReason(s): the assistant turn is still in its tool loop (it
# called a tool and is awaiting the result / its next step) — i.e. NOT ended. Any
# other value, or none, means the turn has terminally ended.
_IN_FLIGHT_STOP_REASONS = frozenset({"toolUse"})


def _session_jsonl_last_assistant_stop_reason(jsonl_path: str | None) -> str:
    """Return the ``stopReason`` of the LAST assistant row in an OpenClaw session
    JSONL (``""`` when the file is absent/unreadable or has no assistant row).

    Used by the verdict-hold acceptor to distinguish "the turn is still streaming
    past its ``.done``" (``stopReason`` in ``_IN_FLIGHT_STOP_REASONS``) from "the
    turn has terminally ended" (any other value). This is a reliable signal where
    the activity stamp is not: the ``.done`` write's own ``after_tool_call`` hook
    bumps the stamp *after* ``.done`` even on a genuine no-verdict end, so
    stamp-vs-``.done`` timing cannot tell the two apart.
    """
    if not jsonl_path or not os.path.exists(jsonl_path):
        return ""
    last = ""
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
                sr = inner.get("stopReason")
                if sr is not None:
                    last = str(sr)
    except OSError:
        return ""
    return last


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


def _is_transient_provider_error(msg: str) -> bool:
    """Strict subset of :func:`_is_provider_rejected_error` that is plausibly
    *transient* — a rate-limit the provider clears on its own (HTTP 429 / "rate
    limit") — as opposed to a *terminal* billing/auth rejection (401/402, invalid
    key, insufficient credits) that retrying cannot fix.

    The signatures here MUST stay a subset of ``_is_provider_rejected_error`` (which
    recognizes a rate-limit via ``"429"`` / ``"rate limit"``): the opt-in override
    below gates on that function first, so anything it does not classify as a
    rejection already flows to the normal retry path and never reaches here. The
    override only ever *narrows* the escalate set, never widens it.

    Used only by the opt-in provider-error retry path (``PROVIDER_ERROR_RETRY``); a
    terminal rejection always escalates immediately regardless of that flag.
    """
    if not msg:
        return False
    return "429" in msg or "rate limit" in msg.lower()


def provider_error_retry_limit() -> int:
    """Max number of *transient* provider-error (rate-limit) retries before escalating.

    Read from the ``PROVIDER_ERROR_RETRY`` env var — an integer **count**, default
    ``0`` = disabled = the historical fail-fast behavior. A positive N enables the
    retry: a transient provider rejection (rate-limit; see
    :func:`_is_transient_provider_error`) re-invokes the *same* agent in place up to
    N times before escalating, each retry on a fresh OpenClaw session and **without**
    consuming the agent's own self-failure retry budget. Terminal (auth/billing)
    rejections always escalate regardless — retrying cannot fix them.

    Parsed leniently via :func:`_env_int`: a missing / non-numeric / negative value
    yields ``0`` (disabled). This is a count, not a 1/0 toggle — ``PROVIDER_ERROR_RETRY=3``
    means "retry a rate-limit up to 3 times", and a non-integer like ``true`` does
    **not** silently enable an unbounded retry (it reads as 0).

    Read at call time so a value self-loaded from ``<repo>/.env`` at orchestrator
    startup (see the ``__main__`` block) takes effect without code changes.
    """
    return _env_int("PROVIDER_ERROR_RETRY", "0", min_clamp=0)


# ---------------------------------------------------------------------------
# Layer 2 — context-overflow discarded-verdict race.
#
# An agent turn can die mid-tool-loop with stopReason="error" and a
# "Context overflow: estimated context size exceeds safe threshold during tool
# loop." errorMessage.  The autodev-pipeline-signals plugin's agent_end backstop
# writes the {agent}_output.done sentinel UNCONDITIONALLY, so poll_for_sentinel
# would return "succeeded" and the gate would read a missing verdict
# (CONTRACT_FAILURE / ERR_FILE_MISSING) and escalate.  But OpenClaw then
# auto-compacts and RESUMES the same session, which writes a valid verdict
# seconds-to-minutes later (observed live: escalated 14:40:00, valid PASS landed
# 14:43:13).  The acceptor below HOLDS such a sentinel until the real verdict
# lands (or the session genuinely stalls / a hold budget is exhausted), so a
# recovered verdict is no longer discarded.  Every NON-overflow termination is
# accepted immediately, so the common path is byte-identical to before.
# ---------------------------------------------------------------------------

# Lowercase substrings of the OpenClaw context-overflow errorMessage.  Coupled to
# the gateway's wording — guarded by test_is_recoverable_context_overflow_matches.
_OVERFLOW_HOLD_SIGNATURES = ("context overflow", "context size exceeds")


def _resolve_overflow_hold_budget() -> int:
    """Wall-clock ceiling (seconds) on a single overflow hold, independent of the
    75-min infra backstop.  Override via env ``AUTODEV_OVERFLOW_HOLD_BUDGET``."""
    v = (os.environ.get("AUTODEV_OVERFLOW_HOLD_BUDGET") or "").strip()
    try:
        return int(v) if v else 900
    except ValueError:
        return 900


_OVERFLOW_HOLD_BUDGET_SECONDS = _resolve_overflow_hold_budget()


def _is_recoverable_context_overflow(msg) -> bool:
    """True if ``msg`` is the OpenClaw context-overflow error the gateway recovers
    from via compaction + resume (so the verdict is still coming).

    Distinct from :func:`_is_provider_rejected_error`: a provider/quota rejection
    is terminal (escalate), whereas a context overflow is recoverable (hold)."""
    if not msg:
        return False
    lower = str(msg).lower()
    return any(sig in lower for sig in _OVERFLOW_HOLD_SIGNATURES)


def _resolve_session_jsonl_path(agent_role: str, session_key: str):
    """Resolve the OpenClaw session JSONL for ``agent_role`` + ``session_key``
    (sessions.json → sessionId → ``{sid}.jsonl``), or ``None`` if unresolvable.

    The overflow-aware acceptor needs this fresh on each poll tick while the
    session is still being written (the three post-poll sites inline an
    equivalent resolution — see the dedup callout in the Layer-2 plan)."""
    sdir = os.path.join(OPENCLAW_ROOT, "agents", agent_role, "sessions")
    full_key = f"agent:{agent_role}:{session_key}".lower()
    try:
        with open(os.path.join(sdir, "sessions.json")) as f:
            sid = json.load(f).get(full_key, {}).get("sessionId")
    except (OSError, ValueError):
        return None
    if not sid:
        return None
    return os.path.join(sdir, f"{sid}.jsonl")


def _verdict_is_fresh_and_parseable(verdict_path: str, min_mtime: float) -> bool:
    """True if ``verdict_path`` exists, was written at or after ``min_mtime``
    (i.e. by the current attempt — not a stale prior-phase artifact), and parses
    as JSON.  Lets the acceptor accept a real verdict the instant it lands."""
    try:
        if os.path.getmtime(verdict_path) < min_mtime:
            return False
        with open(verdict_path) as f:
            json.load(f)
        return True
    except (OSError, ValueError):
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


# Frozen sessions-map entry holding a pre-keyed {role}_tokens_acc captured
# before the keyed-by-session-path accounting was deployed (mid-phase upgrade).
# Never re-summed (there is no file behind it), never shrunk.
_TOKENS_LEGACY_SESSION_KEY = "__pre_keyed_acc__"


def _merge_token_sums(sums) -> dict:
    """Sum an iterable of per-session token dicts key-wise.

    Non-dict entries and non-numeric values are skipped (a corrupt
    phase_state must not crash token accounting)."""
    total = {}
    for s in sums:
        if not isinstance(s, dict):
            continue
        for k, v in s.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            total[k] = total.get(k, 0) + v
    return total


def _new_run_id() -> str:
    """Mint a fresh run identity (uuid4).

    A run_id travels with ``run_started_at``: it is minted wherever a NEW run
    begins and preserved across phase advance and queue revival, so events,
    metrics rows, and run_summary can be grouped per run (``jq 'group_by(.run_id)'``)
    without fragile timestamp joins."""
    return str(uuid.uuid4())


def _current_run_id():
    """Read the current run_id from pipeline_state.json (its durable home).

    Used by the two module-level event writers, which have no ``self`` to read
    ``self.state`` from. Returns ``None`` on any read/parse error — events still
    emit, just without a run_id (e.g. a run that predates this field)."""
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f).get("run_id")
    except Exception:
        return None


def _write_pipeline_event(event_type: str, phase: str, agent: str, detail_dict) -> None:
    """W1-F: Append one structured event line to AUTODEV_PIPELINE_ROOT/pipeline_events.jsonl.

    Delegates the write + size-based rotation to ``event_log.append_pipeline_event``
    (the single source of truth for the line format — see that module's docstring).
    Non-blocking: errors are swallowed. The UI SSE stream tails this file, making
    events durable across server restarts with no UI changes.
    Schema: {"ts", "event", "run_id", "project", "phase", "agent", "detail"}.
    """
    try:
        # Resolve the active project name from the pipeline-project symlink.
        _project = ""
        try:
            if os.path.lexists(SYMLINK_TARGET):
                _project = os.path.basename(os.path.realpath(SYMLINK_TARGET))
        except OSError:
            pass  # best-effort: project name in the event is decorative
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": event_type,
            "run_id": _current_run_id(),
            "project": _project,
            "phase": phase,
            "agent": agent,
            "detail": detail_dict or {},
        }
        append_pipeline_event(
            os.path.join(AUTODEV_PIPELINE_ROOT, "pipeline_events.jsonl"), entry
        )
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
        write_json_atomic(
            os.path.join(PROJECT_ARTIFACTS_DIR, "run_manifest.json"), manifest, indent=None)
        print(f"[W2A] run_manifest.json written: {phase_count} phases, subsystems={subsystem_set}")
    except Exception as _e:
        print(f"[W2A] run_manifest write failed (non-fatal): {_e}")


def webhook_failure_reason(webhook_status: str) -> str:
    """Map a non-SUCCESS ``invoke_agent_webhook`` return token to an operator-facing
    failure reason for the activity feed / ``last_action``.

    The three classes are distinguished so the operator sees an honest diagnosis:
    ``AUTH_ERROR`` (bad bearer token), ``REQUEST_ERROR`` (a deterministic 4xx —
    renamed agentId / bad payload shape, which retrying cannot fix), and everything
    else (``INFRA_ERROR`` or an unknown token) as a transient infra failure.
    """
    if webhook_status == "AUTH_ERROR":
        return "Auth Config Error"
    if webhook_status == "REQUEST_ERROR":
        return "Webhook request/config error"
    return "Webhook infra failure"


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
        orchestrator._preset_session_response_usage("reviewer", session_key)
        invoke_agent_webhook(
            "reviewer",
            session_key,
            token,
            message=_completion_message,
            url=orchestrator.openclaw_config.get("hooks_url"),
        )

        _stop_file = os.path.join(PROJECT_ARTIFACTS_DIR, "pipeline_stop_requested")
        # No sentinel_acceptor here (Layer 2): this is the optional, non-fatal
        # completion-review poll. It produces completion_report.md (not a phase
        # verdict), the except below already shrugs off a missing report, and it
        # carries no stall stamp — so a context-overflow hold would have nothing
        # to recover and no stall bound. The overflow-aware hold is wired only at
        # the three phase-agent poll sites (planner/executor/reviewer).
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
    cross-run history survives projects being removed from the queue. Both the
    summary and the index line carry ``run_id`` (P1-A) so a terminal run can be
    joined to its events/metrics by run identity rather than timestamp.
    Graceful: any failure is logged and swallowed — must never block a transition_state call.
    """
    try:
        run_end = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _run_id = _current_run_id()  # run identity; null for runs that predate this field

        # --- Read run_manifest.json for project identity + run_start ---
        manifest = {}
        _manifest_path = os.path.join(PROJECT_ARTIFACTS_DIR, "run_manifest.json")
        if os.path.exists(_manifest_path):
            try:
                with open(_manifest_path, "r") as _f:
                    manifest = json.load(_f)
            except (OSError, json.JSONDecodeError):
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
            except (OSError, json.JSONDecodeError):
                pass

        # --- Compute duration ---
        total_duration_seconds = None
        if run_start:
            try:
                _start_dt = datetime.fromisoformat(run_start.replace("Z", "+00:00"))
                _end_dt = datetime.fromisoformat(run_end.replace("Z", "+00:00"))
                total_duration_seconds = int((_end_dt - _start_dt).total_seconds())
            except (ValueError, TypeError, AttributeError):
                pass  # unparseable timestamps → duration stays None

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
            except (OSError, UnicodeDecodeError):
                pass  # best-effort: an unreadable/undecodable metrics file just skips its rows

        deduped_rows = list(_seen_phases.values())

        # --- Aggregate counters ---
        # (Historical metrics rows may still carry blame-attribution fields
        # from before that system was removed; they are simply not aggregated —
        # the reader tolerates unknown row fields.)
        executor_attempts_total = sum(r.get("executor_attempts", 0) for r in deduped_rows)
        escalations_total = sum(r.get("escalations", 0) for r in deduped_rows)

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
            "run_id": _run_id,
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
            "skills_injected": skills_injected,
            "token_usage": _tok,
            "phases": phases_list,
        }

        # --- Atomic write of run_summary.json ---
        os.makedirs(PROJECT_ARTIFACTS_DIR, exist_ok=True)
        write_json_atomic(
            os.path.join(PROJECT_ARTIFACTS_DIR, "run_summary.json"), summary, indent=None)

        # --- Append to runs_index.jsonl at AUTODEV_PIPELINE_ROOT ---
        _index_path = os.path.join(AUTODEV_PIPELINE_ROOT, "runs_index.jsonl")
        _index_entry = json.dumps({
            "ts": run_end,
            "run_id": _run_id,
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
            # run_started_at = WHEN this run began (staleness badge); run_id = WHICH
            # run it is (groups events/metrics/run_summary). Both are minted together
            # at every fresh-run start and preserved across phase advance (the advance
            # mutates in place) and queue revival (3-A / P1-A).
            "run_started_at": datetime.now(timezone.utc).isoformat(),
            "run_id": _new_run_id(),
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
        # Phase 9 — track the last-invoked pipeline-agent session so the escalation
        # chokepoint can abort it (the terminal attempt is otherwise never aborted —
        # the retry-start abort only stops the PRIOR attempt when launching the next).
        # Set by _record_active_agent at each agent invocation; consumed + cleared by
        # _abort_active_agent_session. Process-local (reset on restart; see callout).
        self._active_agent_session_key = None
        # Tier 1 tool-loop catcher — the detector closure stashes the offending
        # tool/args/count here when it trips; _note_tool_loop reads+clears it.
        self._pending_tool_loop = None
        self._active_agent_role = None
        self._active_agent_stamp = None
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
        """Acquires an exclusive, non-blocking lock using fcntl.flock.

        IDEMPOTENT (T6.1): returns immediately if this instance already holds the lock
        (``self.lock_fd`` set). This lets ``main()`` acquire the lock BEFORE the
        ``apply_cli_*`` state writes (so a losing instance exits before mutating shared
        state) while ``run()`` still calls ``acquire_lock()`` as its first statement —
        the second call is then a no-op. Without this guard the second call would
        ``os.open`` a fresh fd and ``flock`` it; fcntl locks bind to the open file
        description, so a second fd from the SAME process is denied (BlockingIOError →
        sys.exit). ``release_lock`` nulls ``lock_fd``, so a later re-acquire still runs.
        """
        if getattr(self, "lock_fd", None) is not None:
            return
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
        """Transactionally point BOTH project symlinks at *target_project_dir*.

        Two symlinks are kept in sync:
        1. SYMLINK_TARGET (.autodev/pipeline-project) — used by the orchestrator and
           gate scripts to locate project files.
        2. OPENCLAW_ROOT/pipeline-project (~/.openclaw/pipeline-project) — followed by
           agent workspace symlinks (workspace-{agent}/pipeline-project →
           ~/.openclaw/pipeline-project). Without this second update the agent reads
           the previous project's files even though the orchestrator targets the new one.

        T6.5 — the pair is updated TRANSACTIONALLY (replacing the prior two non-atomic
        ``ln -sfn`` calls that had no rollback). Each new link is first staged at a unique
        temp name, then committed with an atomic ``os.replace``; if the SECOND commit fails,
        the FIRST is rolled back to its previous target. A divergent pair (orchestrator and
        agent following different project trees) makes the executor write sentinels to one
        tree while the orchestrator polls the other → infinite retries, so "both or neither"
        is the safety property. Returns True only when both links point at the new target;
        False (prior state restored) otherwise.
        """
        target_project_dir = os.path.abspath(os.path.expanduser(target_project_dir))
        if not os.path.exists(target_project_dir):
            print(f"[ERROR] Target project dir doesn't exist: {target_project_dir}")
            return False

        openclaw_symlink = os.path.join(OPENCLAW_ROOT, "pipeline-project")
        links = [SYMLINK_TARGET]
        if SYMLINK_TARGET != openclaw_symlink:
            links.append(openclaw_symlink)

        def _prev_target(path):
            try:
                return os.readlink(path)
            except OSError:
                return None  # absent or not a symlink — nothing to restore to

        def _rollback(path, prev):
            try:
                if prev is None:
                    if os.path.lexists(path):
                        os.remove(path)  # was absent before — restore "no link"
                    print(f"[INFO] Rolled back symlink {path} (removed; was absent).")
                else:
                    rb = f"{path}.rollback.{os.getpid()}"
                    if os.path.lexists(rb):
                        os.remove(rb)
                    os.symlink(prev, rb)
                    os.replace(rb, path)
                    print(f"[INFO] Rolled back symlink {path} -> {prev}.")
            except OSError as e:
                print(f"[ERROR] Rollback of symlink {path} failed: {e}")

        # Phase 1 — stage both new links at temp names (no visible change yet).
        staged = []  # (link, tmp, prev_target)
        try:
            for link in links:
                tmp = f"{link}.tmp.{os.getpid()}"
                if os.path.lexists(tmp):
                    os.remove(tmp)
                os.symlink(target_project_dir, tmp)
                staged.append((link, tmp, _prev_target(link)))
        except OSError as e:
            print(f"[ERROR] Failed to stage symlink update: {e}")
            for _link, _tmp, _prev in staged:
                if os.path.lexists(_tmp):
                    try:
                        os.remove(_tmp)
                    except OSError:
                        pass
            return False

        # Phase 2 — commit each via atomic os.replace; on a mid-commit failure, roll back the
        # links already committed so the pair is never left permanently divergent.
        committed = []  # (link, prev_target)
        for link, tmp, prev in staged:
            try:
                os.replace(tmp, link)
            except OSError as e:
                print(f"[ERROR] Failed to commit symlink {link} -> {target_project_dir}: {e}")
                if os.path.lexists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                for done_link, done_prev in reversed(committed):
                    _rollback(done_link, done_prev)
                return False
            committed.append((link, prev))
            print(f"[INFO] Updated symlink {link} -> {target_project_dir}")
        return True

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
        """Atomically writes pipeline_state.json (mkstemp + os.replace).

        Stamps `last_action_timestamp` itself; `last_action` is set by the caller
        (`transition_state()` / init paths) and may be absent on a minimal manual
        reset, so the post-write `[INFO]` log line reads both fields via `.get()` —
        a log statement must never raise after the atomic rename has committed the
        write. Genuine write failures (mkstemp / json.dump / os.replace) still
        propagate via `except … raise`.
        """
        self.state["last_action_timestamp"] = datetime.now(timezone.utc).isoformat()
        
        # Write to temp file then atomic rename
        os.makedirs(AUTODEV_PIPELINE_ROOT, exist_ok=True)
        try:
            write_json_atomic(STATE_FILE, self.state, indent=2)
            print(f"[INFO] Atomically updated state: {self.state.get('pipeline_status', '?')} - {self.state.get('last_action', '')}")
        except Exception as e:
            print(f"[ERROR] Failed to write state: {e}")
            raise

    def transition_state(self, new_status, action_description):
        """Helper to cleanly transition and write state before action.

        Raises ValueError if ``new_status`` is not in VALID_STATES. This is a
        loud failure by design (an invalid target is a programming error): in the
        live loop the raise is caught by run()'s top-level except handler and
        routed to escalation; outside the loop (CLI/startup) it surfaces as a
        traceback. Both are strictly better than the former silent no-op, which
        left a caller's prior ``self.state`` mutation neither persisted nor
        rolled back. `IDLE` is deliberately NOT a valid target — see VALID_STATES.
        """
        if new_status not in VALID_STATES:
            raise ValueError(
                f"Invalid state transition target {new_status!r} not in VALID_STATES"
            )

        self.state["pipeline_status"] = new_status
        self.state["last_action"] = action_description
        if new_status != "WAITING_FOR_SENTINEL":
            self.state.pop("sentinel_wait_started_at", None)
        self.write_state()

    def _phase_resolver_indicates_pipeline_complete(self) -> bool:
        """True iff phase_resolver reports no pending phases for the current symlink project."""
        gate_script = os.path.join(GATE_SCRIPTS_DIR, "phase_resolver.py")
        if not os.path.isfile(gate_script):
            return False
        try:
            result = subprocess.run(
                [sys.executable, gate_script],
                capture_output=True,
                text=True,
                timeout=GATE_SUBPROCESS_TIMEOUT,
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

        CONCURRENCY (F9): this file is written by two independent processes — the UI server
        (add/delete/reorder/parent/mode) and this orchestrator (ACTIVE/COMPLETED/park/promote)
        — with NO file lock. Writes go through ``_mutate_queue`` (read → apply → compare-and-swap
        on ``queue_version`` → retry on conflict), so an interleaved UI + orchestrator write no
        longer loses an update (the stale writer re-reads and re-applies). ``os.replace`` keeps
        each write atomic; the version is the optimistic-concurrency token. A legacy file with no
        ``queue_version`` reads as version 0 (additive schema, no migration). Residual: a
        microsecond window between the pre-write version check and ``os.replace`` that lock-free
        CAS cannot fully close without a lock or a per-write nonce — negligible for two
        low-frequency writers on one host, and the deferred ``queue_write_token`` nonce is the
        documented way to close it.

        If the file exists but is corrupt (invalid JSON or unreadable) — or is valid
        JSON of the wrong shape (T6.7: ``{}`` / ``[]`` / a dict with no ``queue`` list) —
        it is quarantined by renaming to pipeline_queue.json.corrupt.<timestamp> and a
        RuntimeError is raised.  Callers must NOT silently fall through to
        _write_queue — doing so would overwrite the queue file with an empty
        structure, destroying all queue data.
        """
        if not os.path.exists(QUEUE_FILE):
            return {"queue": [], "queue_mode": "auto", "last_updated": ""}
        try:
            with open(QUEUE_FILE, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            self._quarantine_queue_file(f"unreadable / invalid JSON ({e})")
        # T6.7 — a valid-JSON wrong-shape file ({}, [], or a dict with no "queue" list) is
        # NOT caught above, so without this guard it flows out as-is and KeyErrors downstream
        # on EVERY restart; and unlike corrupt JSON it was never quarantined, so the bad file
        # is re-read and re-crashes each cycle (a heartbeat-cron restart loop). Quarantine it
        # the same way so the next read self-heals to the empty structure.
        if not (isinstance(data, dict) and isinstance(data.get("queue"), list)):
            self._quarantine_queue_file(
                f"invalid shape (expected a dict with a 'queue' list, got {type(data).__name__})"
            )
        return data

    def _quarantine_queue_file(self, why: str):
        """Rename a corrupt / wrong-shape pipeline_queue.json out of the way and raise.

        Renaming (rather than leaving the file in place) is what makes the failure
        self-heal: the next ``_read_queue`` finds the file absent and returns the empty
        structure, so a bad file cannot wedge selection in a restart loop. Shared by the
        corrupt-JSON and wrong-shape (T6.7) paths so quarantine lives in exactly one place.
        The raise is implicitly chained to any in-flight ``except`` via ``__context__``.
        """
        corrupt_path = f"{QUEUE_FILE}.corrupt.{int(time.time())}"
        try:
            os.rename(QUEUE_FILE, corrupt_path)
            print(f"[QUEUE] Quarantined queue file to {corrupt_path} ({why}).")
        except OSError as rename_err:
            print(f"[QUEUE] Could not quarantine queue file: {rename_err}")
        raise RuntimeError(
            f"[QUEUE] pipeline_queue.json {why}; quarantined to {corrupt_path}. "
            f"Manual recovery required."
        )

    def _write_queue(self, data):
        """Atomically persist pipeline_queue.json (stamp next version, mkstemp + os.replace).

        The atomic-replace primitive. ``bump_queue_version`` stamps ``base+1`` here (the single
        increment site), so a direct caller still advances the version; the compare-and-swap that
        makes concurrent writes safe lives in ``_mutate_queue`` — route queue mutations through
        that, not through bare ``_write_queue``. See ``_read_queue`` for the F9 concurrency model.
        """
        bump_queue_version(data)
        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        os.makedirs(AUTODEV_PIPELINE_ROOT, exist_ok=True)
        try:
            write_json_atomic(QUEUE_FILE, data, indent=2)
        except Exception as e:
            print(f"[QUEUE] Failed to write queue file: {e}")
            raise

    def _peek_queue_version(self):
        """Cheap read of the on-disk queue_version (the CAS "compare" half).

        Returns 0 if the file is absent. On a transient read/parse error returns -1 so the CAS
        comparison can never spuriously match a real base (>=0) — the next ``_read_queue`` in the
        retry loop quarantines a genuinely-corrupt file via the existing path. Deliberately does
        NOT quarantine here: this runs once per write attempt and must stay side-effect-free.
        """
        try:
            if not os.path.exists(QUEUE_FILE):
                return 0
            with open(QUEUE_FILE, "r") as f:
                return read_queue_version(json.load(f))
        except (json.JSONDecodeError, OSError):
            return -1

    def _mutate_queue(self, mutate_fn, *, max_retries=QUEUE_MAX_CAS_RETRIES):
        """Compare-and-swap wrapper around ``_read_queue``/``_write_queue`` for this process.

        ``mutate_fn(data)`` applies a pure, idempotent, id-keyed change to a freshly-read queue
        and returns the call's result (or raises ``QueueAbort`` to commit nothing). See
        ``queue_semantics.mutate_queue`` for the full contract.
        """
        return mutate_queue(
            self._read_queue, self._write_queue, self._peek_queue_version,
            mutate_fn, max_retries=max_retries,
        )

    def _queue_preflight(self, project_path):
        """Lightweight queue preflight: dir exists, is a git repo, has a roadmap*.md.

        LIGHTWEIGHT PREFLIGHT ONLY — this check is intentionally narrower than the
        server-side `_run_preflight_checks` (which also validates symlink integrity,
        .gitignore, agent workspace files, and OpenClaw config).  A project that
        passes this check may still fail mid-pipeline if the full server preconditions
        are not satisfied.  The server runs `_run_preflight_checks` at queue-add time
        and at trigger-next time; this method runs only when the orchestrator
        auto-advances between queue entries without a UI trigger.

        Host-tool probing was removed: a reliable present/absent verdict from an
        arbitrary declared tool name is not achievable (e.g. `Python 3.10+` / `Unity 6`
        are not PATH binaries), so it produced false-positive blocks. Keep checks here
        deterministic and bounded — no network, no LLM, no subprocess.
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
        # T6.7 — require a truthy id: a malformed row with id missing/None must not be
        # treated as a child of a None parent_id (which would wrongly match every root row).
        children = {e["id"] for e in entries if e.get("id") and e.get("parent_id") == entry_id}
        result = set(children)
        for cid in list(children):
            result |= self._get_all_descendants(entries, cid)
        return result

    def _move_group_atomically(self, entries, parent_id, new_pos):
        """Move parent + all descendants as a unit to new_pos (1-based position for parent)."""
        desc = self._get_all_descendants(entries, parent_id)
        group_ids = {parent_id} | desc
        sorted_all = sorted(entries, key=lambda e: e.get("position", 0))
        group_block = [e for e in sorted_all if e.get("id") in group_ids]
        non_group = [e for e in sorted_all if e.get("id") not in group_ids]
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

        F9: the write goes through the CAS loop (re-find each candidate by id AND re-check its
        pending file on the fresh read), then the caller's in-memory ``queue_data`` is rebased to
        the committed result so the selection walk that follows sees the promotions. The CAS round
        here and the selection's own ACTIVE-commit CAS are SEQUENTIAL, never nested.
        """
        def _has_banked_answer(entry):
            pp = entry.get("project_path")
            if not pp:
                return False
            try:
                root = os.path.realpath(os.path.expanduser(pp))
            except OSError:
                return False
            pending = os.path.join(root, ".autodev", "pipeline", "pending_escalation_command.json")
            return os.path.exists(pending)

        eligible_ids = {
            e["id"] for e in queue_data.get("queue", [])
            if e.get("state") == "ESCALATION" and _has_banked_answer(e)
        }
        if not eligible_ids:
            return False
        now = datetime.now(timezone.utc).isoformat()

        def _apply(data):
            promoted = False
            for entry in data.get("queue", []):
                if (entry.get("id") in eligible_ids
                        and entry.get("state") == "ESCALATION"
                        and _has_banked_answer(entry)):
                    entry["state"] = ESCALATION_ANSWERED
                    entry["answered_at"] = now
                    promoted = True
            if not promoted:
                raise QueueAbort()
            return data

        try:
            # CAS-pure: id-keyed mutation only, no spawn/symlink/IO — re-applied ≤QUEUE_MAX_CAS_RETRIES× on CAS retry (CLAUDE.md F9).
            committed = self._mutate_queue(_apply)
        except QueueVersionConflict as e:
            # T6.6 — perpetual queue contention must not propagate to run()'s top-level
            # handler (which would escalate). This pre-pass has callers beyond selection
            # (the _maybe_revive_on_queue_halted recovery hook), so catch here too: the
            # promotion simply retries on the next cycle.
            print(f"[QUEUE] promote-answered CAS exhausted this cycle ({e}); retry next cycle.")
            return False
        if committed is None:
            return False
        # Rebase the caller's snapshot to the authoritative post-CAS queue (in place, preserving
        # the list object the caller already aliased) so the ensuing walk sees the promotions.
        queue_data["queue"][:] = committed["queue"]
        queue_data[QUEUE_VERSION_KEY] = read_queue_version(committed)
        return True

    def _skip_and_requeue_group(self, entries, entry_id, reason, visited_ids=None):
        """Mark *entry_id* + its descendants SKIPPED_PENDING (skip_count++), set skip_reason,
        and move the whole group past the next independent entry. Mutates *entries* in place.

        Returns True if applied, False if *entry_id* is no longer present (the CAS re-apply path
        turns that False into a QueueAbort). When *visited_ids* is given, descendant ids are added
        to it so the selection walk does not try to start a child of a just-skipped parent.

        Shared by the in-memory walk update and the CAS re-apply closure so the skip logic lives
        in exactly one place (F9).
        """
        entry = next((e for e in entries if e.get("id") == entry_id), None)
        if entry is None:
            return False
        desc_ids = self._get_all_descendants(entries, entry_id)
        for e in entries:
            if e.get("id") in desc_ids and e.get("state") not in ("ACTIVE", "COMPLETED"):
                e["state"] = "SKIPPED_PENDING"
                e["skip_count"] = e.get("skip_count", 0) + 1
                if visited_ids is not None:
                    visited_ids.add(e["id"])
        entry["state"] = "SKIPPED_PENDING"
        entry["skip_count"] = entry.get("skip_count", 0) + 1
        entry["skip_reason"] = reason
        group_size = 1 + len(desc_ids)
        new_pos = min(entry.get("position", 0) + group_size, len(entries))
        self._move_group_atomically(entries, entry_id, new_pos)
        return True

    def _select_next_queue_project(self, halt_if_no_eligible: bool = True, target_entry_id: str | None = None):
        """Walk the queue and start the next eligible project; thin CAS-exhaustion guard (T6.6).

        Delegates to ``_select_next_queue_project_inner``. A ``QueueVersionConflict`` raised by
        any selection-path CAS (perpetual queue contention vs the UI writer, after the 8 retries
        in ``mutate_queue``) degrades to "couldn't commit this cycle" — return False, retry next
        cycle — instead of propagating to ``run()``'s top-level handler, which would escalate.
        """
        try:
            return self._select_next_queue_project_inner(
                halt_if_no_eligible=halt_if_no_eligible, target_entry_id=target_entry_id
            )
        except QueueVersionConflict as e:
            print(f"[QUEUE] selection CAS exhausted this cycle ({e}); retry next cycle.")
            return False

    def _select_next_queue_project_inner(self, halt_if_no_eligible: bool = True, target_entry_id: str | None = None):
        """Walk queue, find next eligible project, run preflight, start it.

        Returns True if a project was started, False if no eligible entry was found.

        When *halt_if_no_eligible* is True (default), also transitions to QUEUE_HALTED with a
        reason — used when the queue still has work but nothing can run.

        When False, returns False without changing pipeline status (used after
        PIPELINE_COMPLETE: queue row is COMPLETED but there is no next project — that is
        success, not a halt).

        When *target_entry_id* is set (F2 — ``--revive``), only that queue entry is
        considered eligible; every other entry is skipped. This makes a relaunch resume the
        SPECIFIC parked row the operator picked (restoring its escalated phase + applying any
        banked command via the existing revival path) rather than the lowest-position one.
        Default ``None`` preserves the unchanged lowest-position selection.
        """
        queue_data = self._read_queue()
        # P1 Stage H — promote parked ESCALATION rows whose answer has been banked
        # (pending_escalation_command.json present) to ESCALATION_ANSWERED, so the
        # eligibility walk below admits them for revival. Orchestrator-owned flip
        # (the server only writes the per-project pending file) — single-writer safe.
        self._promote_answered_escalations(queue_data)
        # T6.7 — sanitize the walk list. A single hand-edited / partial-write row missing a
        # required key must not KeyError the sort / state_by_id comprehension below and poison
        # selection for EVERY project. Skip + log malformed rows (not a dict, or no usable
        # id / state); the per-row access in the walk then relies on these keys being present.
        # The position sort additionally tolerates a missing position (defaults to 0).
        entries = []
        for _e in queue_data["queue"]:
            if not isinstance(_e, dict):
                print(f"[QUEUE] Skipping malformed queue row (not an object): {_e!r}")
                continue
            if not (isinstance(_e.get("id"), str) and _e.get("id")):
                print(f"[QUEUE] Skipping queue row with missing/invalid id: {_e!r}")
                continue
            if not isinstance(_e.get("state"), str):
                print(f"[QUEUE] Skipping queue row {_e.get('id')!r} with missing/invalid state.")
                continue
            if not (isinstance(_e.get("project_path"), str) and _e.get("project_path")):
                print(f"[QUEUE] Skipping queue row {_e.get('id')!r} with missing/invalid project_path.")
                continue
            entries.append(_e)
        entries.sort(key=lambda e: e.get("position", 0))
        now = datetime.now(timezone.utc).isoformat()

        # Build parent state lookup (entries are sanitized: id + state guaranteed present)
        state_by_id = {e["id"]: e["state"] for e in entries}

        visited_ids = set()  # prevent infinite loop if all entries keep failing
        i = 0
        while i < len(entries):
            entry = entries[i]
            if entry["id"] in visited_ids:
                i += 1
                continue
            visited_ids.add(entry["id"])

            # F2 (--revive): a targeted relaunch considers ONLY the requested entry.
            if target_entry_id is not None and entry["id"] != target_entry_id:
                i += 1
                continue

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
                        entry["state"] = "DEPENDENCY_HOLD"  # in-memory, for the walk

                        def _hold_cas(data, _eid=entry["id"]):
                            fresh = next((e for e in data["queue"] if e.get("id") == _eid), None)
                            if fresh is None:
                                raise QueueAbort()  # entry deleted concurrently
                            fresh["state"] = "DEPENDENCY_HOLD"
                            return True
                        # Phase 2 (observability) — record the hold. Emitted once, only if the
                        # CAS actually committed (a concurrently-deleted entry yields None). Fires
                        # once per genuine READY->DEPENDENCY_HOLD: an already-held entry is skipped
                        # by the state gate at the top of this loop before reaching here.
                        # CAS-pure: id-keyed mutation only, no spawn/symlink/IO — re-applied ≤QUEUE_MAX_CAS_RETRIES× on CAS retry (CLAUDE.md F9).
                        if self._mutate_queue(_hold_cas):
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
                # Apply in-memory for the walk's continuation (moves the group + marks descendants
                # visited so a child of a skipped parent is not started)...
                self._skip_and_requeue_group(entries, entry["id"], reason, visited_ids)

                # ...and CAS the same change to disk by id, so a concurrent UI write is merged
                # rather than clobbered. skip_count++ is applied once per committed write (on the
                # freshly-read row), so it cannot double-count.
                def _skip_cas(data, _eid=entry["id"], _reason=reason):
                    if not self._skip_and_requeue_group(data["queue"], _eid, _reason):
                        raise QueueAbort()  # entry deleted concurrently
                    return True
                # CAS-pure: id-keyed mutation only, no spawn/symlink/IO — re-applied ≤QUEUE_MAX_CAS_RETRIES× on CAS retry (CLAUDE.md F9).
                self._mutate_queue(_skip_cas)
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

            # SNAPSHOT-FIX: a fresh-start activation must not inherit a foreign
            # escalation_summary.json. A slow escalation agent from a *previous*
            # project can write its advisory through the pipeline-project symlink
            # AFTER the queue advanced and repointed it, landing in this newly
            # activated project's dir (observed live: SplitBeastDemo's CORE-E1
            # advisory surfaced under SVGPicDemo). PROJECT_ARTIFACTS_DIR follows the
            # symlink we just repointed, so this clears the *new* project's copy.
            # Idempotent and never-raises. Skipped on revival, whose
            # escalation_summary.json is the legitimate advisory for the parked
            # phase being restored (the dispatch-time clear still guards re-escalations).
            if not is_revival:
                self._clear_stale_escalation_summary()

            # F9 — commit ACTIVE via CAS (re-find by id on fresh data). update_symlink (above)
            # and write_state / manifest / banked-command (below) are the non-idempotent side
            # effects and stay OUTSIDE this closure, so a version-conflict retry never re-fires
            # them. P1 Stage H — on a revival, capture the parked snapshot from the FRESH row
            # before stripping its park metadata, so a future re-park starts clean.
            _expected = REVIVABLE_ANSWERED_STATES if is_revival else frozenset({"READY", "SKIPPED_PENDING"})
            _commit = {"snapshot": None}

            def _activate(data, _eid=entry["id"], _expected=_expected):
                fresh = next((e for e in data["queue"] if e.get("id") == _eid), None)
                if fresh is None or fresh.get("state") not in _expected:
                    raise QueueAbort()  # the picked entry vanished/changed under us
                fresh["state"] = "ACTIVE"
                fresh["started_at"] = now
                if is_revival:
                    _commit["snapshot"] = dict(fresh.get("parked_state_snapshot") or {})
                    scrub_parked_fields(fresh)
                return True

            # CAS-pure: id-keyed mutation only, no spawn/symlink/IO — re-applied ≤QUEUE_MAX_CAS_RETRIES× on CAS retry (CLAUDE.md F9).
            if self._mutate_queue(_activate) is None:
                print(f"[QUEUE] Picked entry '{entry['name']}' changed before activation — re-selecting next cycle.")
                return False
            # Keep the in-memory entry consistent for the manifest/event below.
            entry["state"] = "ACTIVE"
            entry["started_at"] = now
            _revival_snapshot = _commit["snapshot"]
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
                # A revived project is the SAME run — keep the original run start AND
                # run_id so the staleness badge stays correct and events/metrics stay
                # grouped under the original run (3-A / P1-A).
                if snap.get("run_started_at"):
                    self.state["run_started_at"] = snap["run_started_at"]
                if snap.get("run_id"):
                    self.state["run_id"] = snap["run_id"]
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
                    # A fresh queue advance is a new run — stamp run_started_at + a
                    # fresh run_id (3-A / P1-A). Same run_started_at value as the queue
                    # entry's started_at / run_manifest.
                    "run_started_at": now,
                    "run_id": _new_run_id(),
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

        # F8 — Escalation fallback. No startable (READY/SKIPPED_PENDING) or banked
        # ESCALATION_ANSWERED entry ran above (those keep priority — the walk tries them
        # first, in position order). Before halting, if a parked ESCALATION remains,
        # (re)activate the lowest-position one in WAITING_FOR_HUMAN so the operator can
        # answer it LIVE via the dashboard — instead of stranding it under QUEUE_HALTED
        # (the single/last project that just escalated) or PIPELINE_COMPLETE (an escalated
        # sibling left after the active project completed). A bare ESCALATION has no banked
        # command to apply (that is the ESCALATION_ANSWERED revival above); we restore its
        # escalated-phase pointer from parked_state_snapshot and wait for the human. This
        # supersedes the prior rule that counted a parked ESCALATION as all_blocked.
        for _esc in sorted(
            (e for e in entries if e.get("state") == "ESCALATION"),
            key=lambda e: e.get("position", 0),
        ):
            # F2 (--revive): a targeted relaunch only revives the requested entry.
            if target_entry_id is not None and _esc["id"] != target_entry_id:
                continue
            ok, reason = self._queue_preflight(_esc["project_path"])
            if not ok:
                print(
                    f"[QUEUE] Escalation revive preflight failed for '{_esc['name']}': "
                    f"{reason} — leaving ESCALATION"
                )
                continue
            project_path = os.path.realpath(os.path.expanduser(_esc["project_path"]))
            # update_symlink FIRST (shared invariant with the revival path): the project
            # must be the symlink target before its state/escalation surface is the active one.
            if not self.update_symlink(project_path):
                print(f"[QUEUE] Symlink update failed reviving escalation '{_esc['name']}'.")
                continue
            # F9 — commit ACTIVE via CAS (re-find by id on fresh data); update_symlink (above)
            # and the write_state below stay outside the closure (run once). Capture the parked
            # snapshot from the FRESH row before stripping it.
            _esc_commit = {"snapshot": {}}

            def _activate_esc(data, _eid=_esc["id"]):
                fresh = next((e for e in data["queue"] if e.get("id") == _eid), None)
                if fresh is None or fresh.get("state") != "ESCALATION":
                    raise QueueAbort()
                _esc_commit["snapshot"] = dict(fresh.get("parked_state_snapshot") or {})
                fresh["state"] = "ACTIVE"
                fresh["started_at"] = now
                scrub_parked_fields(fresh)
                return True

            # CAS-pure: id-keyed mutation only, no spawn/symlink/IO — re-applied ≤QUEUE_MAX_CAS_RETRIES× on CAS retry (CLAUDE.md F9).
            if self._mutate_queue(_activate_esc) is None:
                print(f"[QUEUE] Escalation '{_esc['name']}' changed before revive — skipping.")
                continue
            snap = _esc_commit["snapshot"]
            # Restore the escalated-phase pointer (empty snapshot -> phase 0, the pre-phase
            # escalation case) and sit in WAITING_FOR_HUMAN. No banked command to apply —
            # the operator answers via the dashboard (delivered live since status is
            # WAITING_FOR_HUMAN) and the main loop's escalation dispatch consumes it.
            self.state = {
                "current_phase": snap.get("current_phase", 0),
                "current_phase_raw_id": snap.get("current_phase_raw_id", ""),
                "current_agent": "escalation",
                "planner_retries": snap.get("planner_retries", 0),
                "executor_retries": snap.get("executor_retries", 0),
                "executor_self_failure_retries": snap.get("executor_self_failure_retries", 0),
                "executor_reviewer_rejection_retries": snap.get("executor_reviewer_rejection_retries", 0),
                "reviewer_retries": snap.get("reviewer_retries", 0),
                "last_action": f"queue: escalation awaiting human -> {_esc['name']}",
                "last_action_timestamp": now,
                "pipeline_status": "WAITING_FOR_HUMAN",
                "project_path": project_path,
            }
            if snap.get("phase_base_commit"):
                self.state["phase_base_commit"] = snap["phase_base_commit"]
            if snap.get("phase_start_time"):
                self.state["phase_start_time"] = snap["phase_start_time"]
            # A revived escalation is the SAME run — preserve the original run start
            # AND run_id (3-A / P1-A).
            if snap.get("run_started_at"):
                self.state["run_started_at"] = snap["run_started_at"]
            if snap.get("run_id"):
                self.state["run_id"] = snap["run_id"]
            self.state.pop("queue_halted_reason", None)
            self._current_attempt_retry_class = "initial_attempt"
            self.write_state()
            print(
                f"[QUEUE] Escalation '{_esc['name']}' brought up in WAITING_FOR_HUMAN "
                f"(awaiting operator)."
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
            # Re-announce the halt (transition + observability event) ONLY on a genuine
            # transition INTO QUEUE_HALTED, or when the reason changes. A caller that
            # re-runs selection while ALREADY halted with the SAME reason must be a no-op
            # here: otherwise every selection cycle re-emits an identical queue_halted
            # event and floods the activity feed (observed live: 48 consecutive identical
            # "Queue stalled" rows over one ~7 h hold) and churns the state file / log.
            # Safe to skip the write entirely — the halt branch is reached only when no
            # project was activated, so no earlier self.state mutation is pending here.
            _already_halted = (
                self.state.get("pipeline_status") == "QUEUE_HALTED"
                and self.state.get("queue_halted_reason") == reason
            )
            if not _already_halted:
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
        def _apply(data):
            changed = False
            for e in data["queue"]:
                if e.get("parent_id") == parent_entry_id and e.get("state") == "DEPENDENCY_HOLD":
                    e["state"] = "READY"
                    changed = True
            if not changed:
                raise QueueAbort()  # nothing to promote -> commit nothing
            return True
        try:
            # CAS-pure: id-keyed mutation only, no spawn/symlink/IO — re-applied ≤QUEUE_MAX_CAS_RETRIES× on CAS retry (CLAUDE.md F9).
            self._mutate_queue(_apply)
        except Exception as e:
            print(f"[QUEUE] Failed to promote children after parent completed: {e}")

    def _queue_update_active_entry(self, new_state, extra_fields=None):
        """Find the ACTIVE queue entry for this project and update its state."""
        def _apply(data):
            if not data["queue"]:
                raise QueueAbort()
            idx, entry = self._find_active_queue_entry(data)
            if idx is None:
                raise QueueAbort()
            data["queue"][idx]["state"] = new_state
            if extra_fields:
                data["queue"][idx].update(extra_fields)
            # Returned so the caller can run the children-promote as a SEPARATE sequential CAS
            # AFTER this write commits (it must not re-fire on a retry of THIS mutation).
            return entry.get("id") if new_state == "COMPLETED" else None
        try:
            # CAS-pure: id-keyed mutation only, no spawn/symlink/IO — re-applied ≤QUEUE_MAX_CAS_RETRIES× on CAS retry (CLAUDE.md F9).
            parent_id_completed = self._mutate_queue(_apply)
            if parent_id_completed:
                self._queue_promote_children_after_parent_completed(parent_id_completed)
        except Exception as e:
            print(f"[QUEUE] Failed to update active entry to {new_state}: {e}")

    def _queue_park_active_entry(self, queue_state, parked_reason, extra_fields=None):
        """Park the ACTIVE queue row (escalation or roadmap blocked) with metadata."""
        now = datetime.now(timezone.utc).isoformat()
        # P1 Stage H — snapshot the GLOBAL pipeline_state fields that the queue
        # advance overwrites (selection resets to a blank phase-0/planner state),
        # so a later revival can restore the *escalated phase* pointer rather than
        # restarting from scratch. phase_base_commit is load-bearing: reset_phase()
        # guards its ``git reset --hard`` on it, so without it a revived RESET_PHASE
        # would resume on a dirty tree. escalation_resets/reset_log are deliberately
        # NOT snapshotted — they live in the per-project phase_state.json (survives
        # via the symlink) and duplicating them would diverge from the reset-cap logic.
        # Computed once from self.state (stable for this call); copied per CAS attempt.
        snapshot = {
            "current_phase": self.state.get("current_phase", 0),
            "current_phase_raw_id": self.state.get("current_phase_raw_id", ""),
            # current_agent: display-only — the dashboard's queue table renders the
            # parked row's PHASE cell as "<raw_id> · <agent>" (GET /api/queue exposes
            # it as parked_agent). The revival restore deliberately ignores this key
            # and keeps hard-coding current_agent="escalation".
            "current_agent": self.state.get("current_agent", ""),
            "planner_retries": self.state.get("planner_retries", 0),
            "executor_retries": self.state.get("executor_retries", 0),
            "executor_self_failure_retries": self.state.get("executor_self_failure_retries", 0),
            "executor_reviewer_rejection_retries": self.state.get("executor_reviewer_rejection_retries", 0),
            "reviewer_retries": self.state.get("reviewer_retries", 0),
            "phase_base_commit": self.state.get("phase_base_commit", ""),
            "phase_start_time": self.state.get("phase_start_time", ""),
            # run_started_at / run_id: a parked project is the SAME run — snapshot both
            # so the revival restores the original run identity, not a fresh one (3-A / P1-A).
            "run_started_at": self.state.get("run_started_at", ""),
            "run_id": self.state.get("run_id", ""),
        }

        def _apply(data):
            if not data.get("queue"):
                raise QueueAbort()
            idx, _entry = self._find_active_queue_entry(data)
            if idx is None:
                raise QueueAbort()
            row = data["queue"][idx]
            row["state"] = queue_state
            row["parked_at"] = now
            row["parked_reason"] = parked_reason
            row["parked_pipeline_status"] = self.state.get("pipeline_status")
            row["parked_state_snapshot"] = dict(snapshot)
            if extra_fields:
                row.update(extra_fields)
            return {"id": row.get("id"), "name": row.get("name")}

        try:
            # CAS-pure: id-keyed mutation only, no spawn/symlink/IO — re-applied ≤QUEUE_MAX_CAS_RETRIES× on CAS retry (CLAUDE.md F9).
            parked = self._mutate_queue(_apply)
            # Phase 2 (observability) — record that the active project was set aside and
            # the queue advanced. Emitted once AFTER the commit (outside the retried closure
            # so a CAS retry cannot double-emit); the QueueAbort early-returns yield None.
            if parked is not None:
                _write_pipeline_event(
                    "queue_parked",
                    self.state.get("current_phase_raw_id", ""),
                    "queue",
                    {"reason": parked_reason, "phase": self.state.get("current_phase_raw_id", ""),
                     "entry_id": parked["id"], "entry_name": parked["name"]},
                )
        except Exception as e:
            print(f"[QUEUE] Failed to park active entry ({queue_state}): {e}")

    def _queue_restore_parked_entry_to_active(self):
        """Restore an ESCALATION or BLOCKED queue row back to ACTIVE for this project.

        Called at the start of every resume command (RETRY, RESET_PHASE, RESET_EXECUTION,
        RESET_REVIEWER, SKIP, PROCEED) so that downstream _queue_update_active_entry calls
        can find the row after _queue_park_active_entry set it to a non-ACTIVE state.
        """
        # Resolve the current project path (read-only, stable) OUTSIDE the CAS closure.
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

        def _apply(data):
            if not data.get("queue"):
                raise QueueAbort()
            for entry in data["queue"]:
                if entry.get("state") not in ("ESCALATION", "BLOCKED"):
                    continue
                try:
                    if os.path.realpath(entry["project_path"]) != proj_path:
                        continue
                except OSError:
                    continue
                entry["state"] = "ACTIVE"
                scrub_parked_fields(entry)
                return True
            raise QueueAbort()  # no matching parked row -> commit nothing

        try:
            # CAS-pure: id-keyed mutation only, no spawn/symlink/IO — re-applied ≤QUEUE_MAX_CAS_RETRIES× on CAS retry (CLAUDE.md F9).
            self._mutate_queue(_apply)
        except Exception as e:
            print(f"[QUEUE] Failed to restore parked entry to ACTIVE: {e}")

    def _queue_after_park_maybe_advance(self):
        """After parking, auto-select the next project if queue_mode is auto."""
        queue_data = self._read_queue()
        if not queue_data.get("queue") or queue_data.get("queue_mode", "auto") != "auto":
            return False
        return self._select_next_queue_project()

    def _wait_for_escalation_summary_before_advance(self, poll_interval=2.0):
        """Hold a queue auto-advance until the escalation agent's advisory lands.

        The escalation agent writes ``escalation_summary.json`` through its
        workspace ``pipeline-project`` symlink (OpenClaw sandboxes the write
        tool to the workspace — absolute-path writes are silently discarded).
        ``_select_next_queue_project`` repoints that symlink, so advancing
        while the agent's turn is in flight sends the write into the WRONG
        project and the parked row keeps the deterministic fallback message
        forever. This bounded wait closes that race; only a hung agent (the
        budget expiring) still leaves the fallback in place.

        Engages only when the advance would actually happen — queue_mode
        "auto" with a non-empty queue, the same read
        ``_queue_after_park_maybe_advance`` performs. In manual /
        single-project mode the orchestrator enters the WAITING_FOR_HUMAN
        poll loop, which already promotes a landed summary within one cycle,
        so waiting here would add nothing.

        Cost note: on this deployment the escalation agent (qwen3.6-27b) and
        the pipeline agents (darkqwen3.6-27b-mtp) share one single-GPU
        llama-swap server, so the next project's planner cannot make token
        progress during the escalation turn anyway — the hold costs ~zero
        wall-clock; the budget only bounds a hung agent. Budget via env
        ``AUTODEV_ESCALATION_SUMMARY_WAIT`` (default 300 s, 0 disables).

        A pending STOP breaks the wait early but is NOT consumed here — the
        loop-top ``_check_stop_requested()`` owns sentinel consumption.

        Returns True when the summary landed and was promoted, False
        otherwise (disabled, not auto-advancing, stop pending, or timeout —
        the caller advances with the fallback advisory either way).
        """
        wait_budget = _escalation_summary_wait_seconds()
        if wait_budget <= 0:
            return False
        try:
            queue_data = self._read_queue()
        except Exception:
            # Corrupt queue: add no new failure mode here — the
            # _queue_after_park_maybe_advance call right after owns surfacing it.
            return False
        if not queue_data.get("queue") or queue_data.get("queue_mode", "auto") != "auto":
            return False
        stop_file = os.path.join(PROJECT_ARTIFACTS_DIR, "pipeline_stop_requested")
        deadline = time.time() + wait_budget
        print(
            f"[ADVISORY] Holding queue auto-advance up to {wait_budget}s for "
            f"escalation_summary.json (poll every {poll_interval}s)"
        )
        while True:
            if self._promote_agent_escalation_summary():
                print("[ADVISORY] Escalation summary landed — promoted before queue advance.")
                return True
            if os.path.exists(stop_file):
                print("[ADVISORY] Stop requested — abandoning the summary wait.")
                return False
            if time.time() >= deadline:
                break
            time.sleep(poll_interval)
        print(
            f"[ADVISORY] escalation_summary.json did not land within {wait_budget}s — "
            "advancing with the fallback advisory."
        )
        return False

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
        except OSError:
            pass  # best-effort: a dangling root just isn't searched
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
        # T4.10 — if the queued project's directory was deleted out from under us,
        # surface it loudly + on the activity feed instead of returning None, which
        # would read identically to "no banked command" and silently drop the
        # operator's answer (write_phase_state would also fall back to the wrong dir).
        if not os.path.isdir(root):
            print(f"[ERROR] _apply_pending_escalation_command: project dir missing: {root}",
                  file=sys.stderr)
            _write_pipeline_event(
                "queue_revive_project_missing",
                self.state.get("current_phase_raw_id", ""),
                "queue",
                {"project_path": project_path, "resolved": root},
            )
            return None
        art = os.path.join(root, ".autodev", "pipeline")
        try:
            os.makedirs(art, exist_ok=True)
        except OSError as _mk_err:
            print(f"[ERROR] _apply_pending_escalation_command: cannot create {art}: {_mk_err}",
                  file=sys.stderr)
            _write_pipeline_event(
                "queue_revive_project_missing",
                self.state.get("current_phase_raw_id", ""),
                "queue",
                {"project_path": project_path, "resolved": root, "error": str(_mk_err)},
            )
            return None
        pending_json = os.path.join(art, "pending_escalation_command.json")
        if not os.path.exists(pending_json):
            return None
        try:
            with open(pending_json, "r") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError(f"banked answer is not a JSON object: {type(data).__name__}")
            command = str(data.get("command", "STOP")).upper()
        except (json.JSONDecodeError, OSError, ValueError) as _bank_err:
            # T4.5 — a corrupt banked answer must NOT silently become STOP (which
            # would quietly discard the operator's RESET_PHASE/PROCEED/SKIP intent).
            # Surface it on the activity feed and LEAVE the file in place so the
            # operator can re-bank a valid answer (the server's atomic write
            # overwrites it cleanly). Returning None here — BEFORE the os.remove
            # below — preserves the file and applies no command.
            print(f"[ERROR] corrupt banked escalation command at {pending_json}: {_bank_err}",
                  file=sys.stderr)
            _write_pipeline_event(
                "escalation_command_invalid",
                self.state.get("current_phase_raw_id", ""),
                "escalation",
                {"received_command": "<unreadable>", "defaulted_to": "none",
                 "reason": "corrupt_banked_answer", "error": str(_bank_err)},
            )
            return None
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
            write_json_atomic(esc_json, payload, indent=None)
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

    def _read_escalation_advisory(self):
        """Read the escalation agent's advisory (summary + recommended_action).

        The escalation agent composes ``escalation_summary.json`` per its
        escalation-summary skill before notifying the operator;
        ``_promote_agent_escalation_summary`` consumes this reader.
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

    def _clear_stale_escalation_summary(self):
        """Remove any prior escalation_summary.json before inviting a new one.

        The file is agent-written and load-bearing for the dashboard advisory.
        Without this guard at dispatch, a summary from a previous escalation —
        or a previous project, after a queue auto-advance repointed the
        pipeline-project symlink — would be promoted as if it described the
        current failure. Never raises.
        """
        try:
            os.remove(os.path.join(PROJECT_ARTIFACTS_DIR, "escalation_summary.json"))
        except FileNotFoundError:
            pass
        except OSError as e:
            print(f"[ADVISORY] Could not remove stale escalation_summary.json: {e}")

    def _build_escalation_webhook_message(self, reply_token=None):
        """Webhook message for the escalation agent — the advisory is agent-owned.

        The agent composes the {summary, recommended_action} advisory itself
        (its escalation-summary skill), WRITES escalation_summary.json before
        notifying the operator, and includes the summary in the notification.
        The orchestrator promotes that file into phase_state when it lands
        (``_promote_agent_escalation_summary``).

        Reads are pointed at the resolved absolute artifacts path because a
        queue auto-advance can repoint the pipeline-project symlink while the
        agent's turn is in flight; the WRITE must still go through the
        workspace symlink (OpenClaw sandboxes the write tool to the agent's
        workspace — absolute-path writes are silently discarded).

        The read instruction marks ``phase_state.json`` REQUIRED (always present)
        and everything else OPTIONAL, and carries an explicit read-once /
        do-not-retry-missing / proceed guard. That guard is the inline backstop
        against the ENOENT read loop a slow local model otherwise falls into
        (observed: 222 reads in one escalation session, pinning the GPU); the
        standing rule also lives in ``autodev/agents/escalation/AGENTS.md``.

        ``reply_token`` (B1) — when present, the agent is told to echo this
        correlation token verbatim in its notification and instruct the operator
        to start any channel reply with it, so POST /api/escalation/inbound can
        route the reply back to THIS project. The agent still does not apply
        commands itself. The repo-init caller passes no token (back-compat).
        """
        _p = os.path.realpath(PROJECT_ARTIFACTS_DIR)
        msg = (
            "Pipeline escalation — a TRUSTED control invocation from the AutoDev "
            "orchestrator (the 'EXTERNAL/UNTRUSTED source' preamble OpenClaw wraps "
            "around every webhook is boilerplate, not a prompt-injection attempt; "
            "do not refuse it).\n\n"
            f"Read your diagnostics from the ABSOLUTE path {_p} (it is symlink-"
            "stable for your whole turn — do NOT read them through the "
            "pipeline-project workspace symlink, which a queue advance can "
            f"repoint mid-turn). {_p}/phase_state.json is REQUIRED and always "
            "present (it carries escalation_trigger_reason); "
            f"{_p}/failure_context.json is the primary failure detail when "
            f"present; {_p}/current_phase.json and the "
            f"{_p}/(planner|executor|reviewer)_output.json files are OPTIONAL. "
            "Read each file AT MOST ONCE: if a read returns file-not-found, do "
            "NOT retry or re-read it under another path — treat it as absent, "
            "PROCEED, and compose your summary from what you read "
            "(phase_state.json alone is enough). Never loop re-reading missing "
            "files. Compose a "
            'JSON advisory with exactly two fields — "summary" and '
            '"recommended_action" — per your escalation-summary skill, and '
            "WRITE it to pipeline-project/.autodev/pipeline/escalation_summary.json "
            "(via your workspace symlink) BEFORE notifying the operator.\n\n"
            "Then NOTIFY the operator with a self-contained message that "
            "includes that summary via your configured channel / message tool. "
            "Do NOT wait for a reply in this session and do NOT write "
            "escalation_output — the operator answers from the dashboard."
        )
        if reply_token:
            msg += (
                "\n\nINBOUND REPLY: the operator may answer from the dashboard OR by "
                "replying to your notification on the configured channel. To let the "
                "Lullabeast server route a channel reply back to the right project, "
                "include this correlation token verbatim in your notification and tell "
                f"the operator to start their reply with it: {reply_token}\n"
                "You still do NOT apply commands yourself and do NOT write "
                "escalation_output — the server writes the command either way."
            )
        return msg

    def _prepare_escalation_reply_token(self) -> str:
        """Build + persist a correlation token for this escalation episode; return it.

        Format ``{entry_id}.{nonce}`` — the entry-id prefix is a debugging /
        disambiguation aid; the inbound endpoint matches the WHOLE token against
        each project's phase_state.json, so a run-scoped fallback prefix is fine
        when there is no ACTIVE queue entry (manual / single-project mode).

        Persisted to phase_state.json so POST /api/escalation/inbound can resolve
        an operator reply back to THIS project (the B0 boundedness guarantee).
        Best-effort on both the entry-id lookup and the write — never raises.

        Call ordering matters: invoke while the entry is still ACTIVE (before the
        park) so the entry id is captured, and while the pipeline-project symlink
        still points at this project so the phase_state write lands in its dir.
        """
        entry_id = "run"
        try:
            _idx, entry = self._find_active_queue_entry(self._read_queue())
            if entry and entry.get("id"):
                entry_id = str(entry["id"])
        except Exception:
            pass  # best-effort: entry_id falls back to "run" if the queue is unreadable
        token = f"{entry_id}.{secrets.token_hex(3)}"
        try:
            ps = self.read_phase_state()
            ps["escalation_reply_token"] = token
            ps["escalation_reply_token_at"] = datetime.now(timezone.utc).isoformat()
            self.write_phase_state_atomic(ps)
        except Exception as e:
            print(f"[WARN] Could not persist escalation_reply_token: {e}")
        return token

    def _promote_agent_escalation_summary(self):
        """Promote the agent-written escalation_summary.json into phase_state.

        Called from the WAITING_FOR_HUMAN poll loop (so the dashboard upgrades
        within one poll cycle of the summary landing) and at resolution. When a
        valid summary is present and the advisory is not already "ready",
        records escalation_message / escalation_recommended_action /
        escalation_advisory_status="ready" atomically and returns True.
        Otherwise returns False and the deterministic fallback stays in place.
        Never raises.
        """
        advisory = self._read_escalation_advisory()
        if not advisory:
            return False
        try:
            ps = self.read_phase_state()
        except Exception:
            return False
        if ps.get("escalation_advisory_status") == "ready":
            return False
        ps["escalation_message"] = advisory["summary"]
        ps["escalation_recommended_action"] = advisory["recommended_action"]
        ps["escalation_advisory_status"] = "ready"
        try:
            self.write_phase_state_atomic(ps)
        except Exception as e:
            print(f"[ADVISORY] Could not persist promoted summary: {e}")
            return False
        return True

    def _compose_fallback_reason(self, ps):
        """Deterministic, factual escalation reason for when the LLM advisory is
        unavailable (hung / timed out / failed) — NO LLM call, NO fabrication.

        Sourced only from hard signals already on hand: ``ps["last_error_code"]``,
        ``ps["escalation_trigger_reason"]`` (which 6244f3a made honest for the
        CONTRACT_FAILURE case: "reviewer ended without a verdict — gave up or was
        cut off"), and ``failure_context.json`` when present (``failing_agent``,
        ``gate_error_codes``, ``attempt_number``). It deliberately does NOT surface
        the phase's ``current_phase_behavioral_verification.failure_language`` as
        observed reality — presenting that expected-failure description as "what
        happened" is the fabrication that produced the misleading "blank white
        page" escalation message. Always points the operator at the log.

        Returns a short string; degrades to a minimal generic line when no signal
        is available. Never raises.
        """
        ps = ps or {}
        last_err = str(ps.get("last_error_code") or "").strip()
        trigger = str(ps.get("escalation_trigger_reason") or "").strip()

        # failure_context.json — same source the advisory reads; optional.
        fc = {}
        try:
            _fc_path = os.path.join(PROJECT_ARTIFACTS_DIR, "failure_context.json")
            if os.path.isfile(_fc_path):
                with open(_fc_path, "r") as f:
                    fc = json.load(f) or {}
        except Exception:
            fc = {}

        failing_agent = str(fc.get("failing_agent") or "").strip()
        codes = fc.get("gate_error_codes") or ([last_err] if last_err else [])
        code_str = codes[0] if codes else ""
        attempt = fc.get("attempt_number")

        tail = "Automated summary unavailable; see the pipeline log for full context."

        # Reviewer MODEL hard-error (server_error/500 — GPU contention / eviction on a
        # shared local host): the reviewer did NOT give up — its inference call failed.
        # last_error_code is set reliably at the escalation chokepoint, and ``trigger``
        # already carries the real inference error, so surface it verbatim.
        if last_err == ERR_REVIEWER_MODEL_ERROR:
            base = trigger or (
                "The reviewer's model returned a server error (it did not give up) "
                "across repeated fresh-session retries"
            )
            return f"{base}. {tail}"

        # CONTRACT_FAILURE: the reviewer session ended without a verdict. The trigger
        # reason is already honest (6244f3a) — surface a clean, factual version.
        if last_err == ERR_REVIEWER_CONTRACT_FAILURE or "CONTRACT_FAILURE" in trigger:
            return (
                "The reviewer ended without a usable verdict (it gave up or was cut "
                "off) after repeated fresh-session retries "
                f"(ERR_REVIEWER_CONTRACT_FAILURE). {tail}"
            )

        # General case: name the failing agent + error code + attempt from failure_context.
        if failing_agent or code_str:
            who = (failing_agent or "an agent").capitalize()
            line = f"{who} failed"
            if attempt:
                line += f" on attempt {attempt}"
            if code_str:
                line += f" ({code_str})"
            return f"{line}. {tail}"

        # Last resort: the trigger reason, else a generic line.
        if trigger:
            return f"{trigger}. {tail}"
        return f"The pipeline escalated and needs your input. {tail}"

    def _record_escalation_reason(self, ps):
        """Record the honest deterministic escalation reason into ``ps``.

        Sets ``escalation_message`` via ``_compose_fallback_reason`` and
        ``escalation_advisory_status="fallback"``, persisting atomically — so
        the dashboard shows a factual reason the instant the escalation panel
        appears, with no loader and no LLM call. The escalation agent (invoked
        via OpenClaw right after) composes the richer advisory itself and
        writes ``escalation_summary.json``; ``_promote_agent_escalation_summary``
        upgrades the status to "ready" when that file lands. Never raises.
        """
        ps["escalation_message"] = self._compose_fallback_reason(ps)
        ps["escalation_advisory_status"] = "fallback"
        try:
            self.write_phase_state_atomic(ps)
        except Exception as e:
            print(f"[ADVISORY] Could not persist escalation reason: {e}")

    def _resolve_escalation_trigger_class(self, ps):
        """P1-B — resolve the structured escalation_trigger_class at dispatch time.

        Precedence: an explicit ``self.state["escalation_trigger_class"]`` stamp (set by
        the exhaustion / reviewer-routing / webhook / stamp-init / repo-init chokepoints,
        which have no distinguishing error code) wins; otherwise derive from
        ``ps["last_error_code"]`` (the error-coded chokepoints); otherwise ``"unknown"``.
        A stamp outside the enum is ignored, so a typo can't inject a junk class."""
        explicit = self.state.get("escalation_trigger_class")
        if explicit in ESCALATION_TRIGGER_CLASSES:
            return explicit
        return _derive_escalation_trigger_class(ps.get("last_error_code"))

    def _prepare_escalation_trigger(self, ps):
        """P1-B — resolve the trigger class, persist it onto ``ps`` (so it rides
        phase_state → the metrics row), clear the consumed ``self.state`` stamp so the
        NEXT escalation re-resolves cleanly, and return the ``escalation_trigger`` event
        detail. MUST be called BEFORE ``ps`` is persisted (``_record_escalation_reason``)
        so the class is in the durable write."""
        cls = self._resolve_escalation_trigger_class(ps)
        ps["escalation_trigger_class"] = cls
        self.state.pop("escalation_trigger_class", None)
        return {
            "reason": ps.get("escalation_trigger_reason"),
            "escalation_trigger_class": cls,
            "last_error_code": ps.get("last_error_code"),
            "last_poll_reason": ps.get("last_poll_reason"),
        }

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

    def _restore_resume_target_agent(self):
        """Resume the agent that was in-flight when the operator stopped.

        Reads ``resume_target_agent`` (stashed by ``/api/resume-ready`` before it
        set ``current_agent="escalation"`` to route into the command-consumption
        branch), applies it to ``current_agent``, and clears it. Defaults to
        "planner" when the stash is absent or invalid.

        Replaces the former ``last_action`` string-match heuristic, which always
        fell through to "planner" because the STOPPED transition overwrites
        ``last_action`` before this code runs — silently re-running completed
        executor work on every stop→resume.
        """
        target = self.state.pop("resume_target_agent", None)
        self.state["current_agent"] = target if target in (
            "planner", "executor", "reviewer", "escalation") else "planner"

    @staticmethod
    def _default_phase_state() -> dict:
        """Fresh phase_state with all retry/reset counters zeroed (single source of truth)."""
        return {
            "planner_retries": 0,
            "executor_retries": 0,
            "executor_self_failure_retries": 0,
            "executor_reviewer_rejection_retries": 0,
            "reviewer_retries": 0,
            "reviewer_rejected": False,
            "escalation_resets": 0,
            "nuclear_resets": 0,
        }

    def increment_planner_retries(self):
        # LAUNCH-8: read via read_phase_state() so a *corrupt* phase_state is quarantined
        # and raises (→ escalation) instead of silently degrading to {} and writing back a
        # single-key dict that wipes escalation_resets / nuclear_resets / the executor
        # counters. An absent file reads as {} → start from the zeroed default.
        phase_state = self.read_phase_state() or self._default_phase_state()
        phase_state["planner_retries"] = phase_state.get("planner_retries", 0) + 1

        try:
            write_json_atomic(PHASE_STATE_FILE, phase_state, indent=2)
        except Exception as e:
            print(f"[ERROR] Failed to write phase_state: {e}")

        self.state["planner_retries"] = phase_state["planner_retries"]
        self.transition_state("RUNNING", f"Incremented planner retries to {phase_state['planner_retries']}")
        return phase_state["planner_retries"]
        
    def run_planner_output_gate(self):
        """Run the planner verdict gate as a subprocess; return True iff it emits ``PASS``.

        Verdict-gate convention (see gate_scripts/README.md): the gate always exits 0 and
        prints its verdict on stdout, which we read from ``result.stdout``. A gate-script
        crash or timeout is treated as a safe failure (``False``), never a pipeline crash.
        Sibling :meth:`planner_output_is_valid` evaluates the *same* gate in-process for the
        restart short-circuit; the two share one verdict contract — see its note for why both
        mechanisms exist.
        """
        gate_script = os.path.join(GATE_SCRIPTS_DIR, "planner_gate.py")
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

    def _init_activity_stamp_or_escalate(self, agent_role: str) -> bool:
        """Seed the agent activity stamp; route to escalation on failure.

        ``initialize_activity_stamp`` returns ``False`` when the workspace
        directory is missing or unwritable.  Silently discarding that
        return value (the pre-existing bug at the three orchestrator
        call sites) means ``poll_for_sentinel`` will subsequently call
        ``os.path.exists(stall_detection_path)`` against a file that
        never gets created — the stall branch is skipped on every poll
        iteration and a hung agent is invisible until the infrastructure
        backstop fires.

        On failure this routes to the escalation agent (sets
        ``current_agent = "escalation"`` + ``transition_state("RUNNING", …)``)
        rather than dead-ending at a silent ``HALTED_SILENT``.  Escalation is
        the only path that notifies the operator (advisory + Signal via the
        escalation agent) and offers a dashboard recovery — "the operator was
        notified" and "the pipeline escalated" are the same event.  If the
        workspace is so broken that the escalation dispatch's own
        ``phase_state`` write also fails, the run degrades to the existing
        escalation-delivery-failed ``HALTED_SILENT`` — an honest silent halt
        because we truly could not reach anyone (the ``stamp_init_failed``
        event is still recorded for the activity feed).

        Returns
        -------
        bool
            ``True`` if the stamp was successfully seeded — caller may
            proceed into ``poll_for_sentinel``.  ``False`` if the helper
            routed to escalation — caller MUST ``continue`` the main loop so
            the next iteration fires the escalation dispatch (a ``return``
            would exit the process before escalation runs).
        """
        ok = initialize_activity_stamp(PROJECT_ARTIFACTS_DIR, agent_role)
        if ok:
            return True
        stamp_path = os.path.join(
            PROJECT_ARTIFACTS_DIR, f"{agent_role}_activity.stamp"
        )
        print(
            f"[WARN] activity stamp init failed for {agent_role} at {stamp_path}. "
            f"Workspace directory missing or unwritable — stall detection would "
            f"be silently disabled.  Routing to escalation."
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
        self.state["current_agent"] = "escalation"
        self.state["escalation_trigger_class"] = "stamp_init_failed"  # P1-B
        self.transition_state(
            "RUNNING",
            f"activity stamp init failed for {agent_role} — workspace missing "
            f"or unwritable; escalating for operator review",
        )
        return False

    def _preset_session_response_usage(self, role: str, session_key: str) -> None:
        """Pre-seed ``responseUsage: "full"`` on the about-to-be-invoked session.

        OpenClaw's ``responseUsage`` is a per-session-entry preference (no config
        default exists), so every fresh pipeline session must be patched
        individually.  ``sessions.patch`` creates the entry when the key does not
        exist yet, so calling this *before* the webhook fires pre-seeds the
        preference race-free; the run reuses the pre-created entry and appends a
        token-usage + cost line to each reply it records.

        ``session_key`` is the bare ``pipeline:…`` key; the gateway store key is
        the ``agent:{role}:…`` lowercase form (same shape ``sessions.abort`` uses).

        Best-effort and non-blocking: a failure is logged and the invocation
        proceeds — usage display is observability, never worth failing a phase.

        Env ``AUTODEV_RESPONSE_USAGE`` overrides the mode (default ``full``);
        an empty value or ``off`` disables the patch entirely.
        """
        mode = os.environ.get("AUTODEV_RESPONSE_USAGE", "full").strip().lower()
        if mode in ("", "off"):
            return
        try:
            store_key = f"agent:{role}:{session_key}".lower()
            gw_token = self.openclaw_config.get("gateway_token", "")
            gw_ws_url = self.openclaw_config.get(
                "gateway_ws_url", "ws://127.0.0.1:18789/__openclaw__/ws"
            )
            ok = set_session_response_usage(store_key, gw_ws_url, gw_token, mode=mode)
            print(
                f"[USAGE] responseUsage={mode} {'set' if ok else 'FAILED'} "
                f"session_key={store_key}"
            )
        except Exception as exc:
            print(
                f"[USAGE] responseUsage patch failed for {role} {session_key}: {exc}"
            )

    def _record_active_agent(self, role: str, session_key: str) -> None:
        """Remember the just-invoked pipeline agent's session so a later give-up
        (escalation) can abort it.

        Stores the **bare** session key (``pipeline:phase-…:{role}-attempt-N``), the
        role, and the role's ``{role}_activity.stamp`` path (for verify_session_stopped).
        Called at every planner/executor/reviewer invocation; overwritten each time, so
        it always reflects the last-invoked (i.e. in-flight) agent. Cleared by
        :meth:`_abort_active_agent_session`. See Phase 9 (zombie-session fix).

        Also stamps the role's configured model into ``phase_state.models_used``
        (MON-1 — the metrics row copies it so the dashboard's Run Metrics model
        badge can show what ran the phase, mirroring ``skill_injected``).
        Best-effort telemetry: any failure is logged, never blocks the invocation."""
        self._active_agent_role = role
        self._active_agent_session_key = session_key
        self._active_agent_stamp = os.path.join(PROJECT_ARTIFACTS_DIR, f"{role}_activity.stamp")
        try:
            _model = self._get_agent_model(role)
            if _model:
                _ps = self.read_phase_state()
                _models = _ps.get("models_used")
                if not isinstance(_models, dict):
                    _models = {}
                if _models.get(role) != _model:
                    _models[role] = _model
                    _ps["models_used"] = _models
                    self.write_phase_state_atomic(_ps)
        except Exception as e:
            print(f"[WARN] could not record model for {role}: {e}")
        # Pre-seed responseUsage="full" on the session entry before the webhook
        # fires so the run records a token-usage + cost line on every reply.
        self._preset_session_response_usage(role, session_key)

    def _wait_for_stamp_settle(self, stamp_path: str) -> bool:
        """Poll an activity stamp until it stays quiet for ``_INTERRUPT_SETTLE_QUIET``
        seconds, bounded by ``_INTERRUPT_SETTLE_MAX``.  Returns True once settled, False on
        timeout.  Reuses :func:`verify_session_stopped` (one settle probe per iteration); a
        missing stamp settles immediately.

        Replaces the old single-shot ``verify_session_stopped`` call right after a steer,
        which **false-failed on every abort**: ``sessions.steer`` is interrupt+inject and its
        own spawned follow-up turn refreshes the stamp, so a single 5 s probe always saw
        movement.  Waiting for genuine quiet absorbs that expected ack-turn and only reports
        failure on a true runaway."""
        deadline = time.time() + _INTERRUPT_SETTLE_MAX
        while True:
            if verify_session_stopped(stamp_path, settle_seconds=_INTERRUPT_SETTLE_QUIET):
                return True
            if time.time() >= deadline:
                return False

    def _agent_turn_still_in_flight(self, role: str, session_key: str):
        """Tri-state liveness oracle for the ``skip_if_idle`` pre-check: is ``role``'s turn for
        ``session_key`` still streaming?

        Reuses the exact signal :meth:`_make_verdict_hold_acceptor` trusts — the session
        transcript's last assistant row — instead of a stamp-movement window.  This is the
        reliable oracle where the stamp is not: the activity stamp is silent for the whole
        duration of a single model call (minutes), so a short stamp window read a live
        mid-call agent as idle and skipped exactly the abort it exists to perform.

        Returns
        -------
        True
            Still in flight: the last assistant row is a non-terminal tool-loop step
            (``stopReason`` in ``_IN_FLIGHT_STOP_REASONS``, i.e. ``"toolUse"``), OR a recoverable
            context-overflow error (OpenClaw will auto-compact and resume the same run).
        False
            Provably ended: a terminal ``stopReason`` is present on the last assistant row.
        None
            Unresolvable: the session JSONL can't be read or has no assistant row yet.  Callers
            treat ``None`` like in-flight (steer) — favouring killing a possible zombie over
            skipping it.  ``sessions.json`` is populated by ``agent_end`` before any abort site
            runs, so this is rare.
        """
        jsonl = _resolve_session_jsonl_path(role, session_key)
        if not jsonl or not os.path.exists(jsonl):
            return None
        # Overflow first: an overflow turn ends on stopReason "error" (a terminal value), but the
        # gateway auto-compacts and RESUMES it, so it is still effectively in flight.
        if _is_recoverable_context_overflow(
            _session_jsonl_last_assistant_error_message(jsonl)
        ):
            return True
        sr = _session_jsonl_last_assistant_stop_reason(jsonl)
        if sr in _IN_FLIGHT_STOP_REASONS:
            return True
        if not sr:
            return None  # resolved file but no assistant row yet — terminality unknown
        return False

    def _interrupt_agent_session(
        self,
        *,
        role: str,
        session_key: str,
        stamp_path: str,
        source: str,
        skip_if_idle: bool,
        prior_attempt=None,
        reason: str = None,
    ) -> str:
        """Best-effort interrupt of an agent's still-running embedded run, with a liveness
        pre-check and a settle-wait.  The single chokepoint for every steer-abort path
        (retry-start, escalation, reviewer_retry, stall/timeout).  Returns one of
        ``"skipped_idle"`` / ``"ok"`` / ``"unconfirmed"`` / ``"failed"``.

        Why (see the ``_INTERRUPT_*`` module constants): OpenClaw's ``sessions.steer`` is
        *interrupt+inject* — it aborts the embedded run AND always enqueues a follow-up turn
        carrying the stop message, whether the session was active OR idle.  So:

          * ``skip_if_idle=True`` (retry-start / escalation / reviewer_retry): skip the steer
            ONLY when the turn has PROVABLY ended (``_agent_turn_still_in_flight`` is False — the
            transcript's last assistant row is terminal).  An already-finished session is left
            alone — no gratuitous turn, no "poking a corpse" (an agent that ended its turn must
            not be woken to process a stop message it may ignore or even act on).  Still-in-flight
            OR unresolvable both steer: skipping a live zombie corrupts the repo / races the shared
            output files, whereas steering a finished agent is a cheap no-op.
          * ``skip_if_idle=False`` (stall / no_first_activity / timeout): the agent is wedged by
            definition there, so we always steer to kill a presumed-wedged run.

        After a steer we WAIT for the stamp to settle rather than instant-verifying — the
        steer's own spawned turn refreshes the stamp, so the old single ``verify_session_stopped``
        call false-failed on every abort.  Only a settle TIMEOUT (a genuine runaway) emits
        ``abort_verify_failed``; the caller always soft-continues (a forced ``HALTED_SILENT``
        needs a human, whereas a retry usually resolves — unchanged policy).

        Emits ``abort_attempted`` with ``result`` in
        ``{skipped_idle, ok, unconfirmed, FAILED}``.  Never raises; never blocks beyond
        ``_INTERRUPT_SETTLE_MAX`` (+ the liveness window + ``abort_agent_session``'s retries)."""
        if not session_key or not role:
            return "skipped_idle"
        stamp_path = stamp_path or ""  # defensive: getmtime("") raises OSError (handled), not TypeError
        full_key = session_key
        if not full_key.startswith(f"agent:{role}:"):
            full_key = f"agent:{role}:{session_key}"
        full_key = full_key.lower()
        raw_id = self.state.get("current_phase_raw_id", "")

        def _emit_attempted(result: str) -> None:
            detail = {
                "session_key": full_key, "result": result,
                "agent_role": role, "source": source,
            }
            if prior_attempt is not None:
                detail["prior_attempt"] = prior_attempt
            if reason is not None:
                detail["reason"] = reason
            _write_pipeline_event("abort_attempted", raw_id, role, detail)

        _rsfx = f" reason={reason}" if reason else ""  # operator-facing log parity with the stall path

        # 1. Liveness pre-check — skip the steer ONLY when the agent's turn has PROVABLY ended
        #    (transcript last-row terminal). "Still in flight" (tool loop / recoverable overflow)
        #    AND "unresolvable" both fall through to the steer below: skipping a live zombie
        #    corrupts the repo / races the shared output files, whereas steering a finished agent
        #    is a cheap no-op (its [ORCHESTRATOR CONTROL] turn ends cleanly per AGENTS.md). This
        #    replaces a 3s stamp-movement probe that read a live mid-model-call agent as idle
        #    (the stamp is silent for a whole model call) and skipped the abort it exists to do.
        if skip_if_idle and self._agent_turn_still_in_flight(role, session_key) is False:
            print(
                f"[ABORT] result=skipped_idle session_key={full_key} source={source}{_rsfx} "
                f"(turn ended — no interrupt needed)"
            )
            _emit_attempted("skipped_idle")
            return "skipped_idle"

        # 2. Issue the steer interrupt (best-effort; abort_agent_session retries 3x internally).
        gw_token = self.openclaw_config.get("gateway_token", "")
        gw_ws_url = self.openclaw_config.get(
            "gateway_ws_url", "ws://127.0.0.1:18789/__openclaw__/ws"
        )
        aborted = abort_agent_session(full_key, gw_ws_url, gw_token)
        if not aborted:
            print(f"[ABORT] result=FAILED session_key={full_key} source={source}{_rsfx}")
            _emit_attempted("FAILED")
            return "failed"

        # 3. Settle-wait — absorb the steer's own spawned turn instead of instant-verifying.
        if self._wait_for_stamp_settle(stamp_path):
            print(f"[ABORT] result=ok session_key={full_key} source={source}{_rsfx} (settled)")
            _emit_attempted("ok")
            return "ok"

        # Genuine runaway: still streaming after the ceiling. Surface it; caller soft-continues.
        print(
            f"[ABORT][VERIFY_FAILED] session_key={full_key} source={source}{_rsfx} — stamp still "
            f"refreshing after {_INTERRUPT_SETTLE_MAX:.0f}s settle wait; continuing (soft-continue)."
        )
        _emit_attempted("unconfirmed")
        _vf_detail = {
            "session_key": full_key, "stamp_path": stamp_path,
            "agent_role": role, "source": source,
        }
        if prior_attempt is not None:
            _vf_detail["prior_attempt"] = prior_attempt
        if reason is not None:
            _vf_detail["reason"] = reason
        _write_pipeline_event("abort_verify_failed", raw_id, role, _vf_detail)
        return "unconfirmed"

    def _abort_active_agent_session(self, source: str) -> None:
        """Interrupt the last-invoked agent's still-running session so it can't keep mutating
        the repo (``git commit``/``tag``/edits) or stream a zombie turn after the orchestrator
        has moved on.  Thin wrapper over :meth:`_interrupt_agent_session` (``skip_if_idle=True``
        — a finished agent is left alone) that targets the ``_active_agent_*`` fields recorded by
        :meth:`_record_active_agent`, then clears them.

        Two callers:
          * ``source="escalation"`` — the orchestrator gives up on a phase and hands off to the
            human; the terminal attempt is otherwise never aborted (the retry-start abort only
            stops the *prior* attempt when *launching the next* one).
          * ``source="reviewer_retry"`` — the reviewer contract-shape retry handlers
            (CONTRACT_FAILURE / *_UNVERIFIED) kill the prior reviewer run before re-invoking, so
            the re-invoke does not reattach a still-streaming embedded run.

        No-op when nothing is in-flight.  Never blocks the caller (soft-continue)."""
        key = self._active_agent_session_key
        role = self._active_agent_role
        if not key or not role:
            return
        self._interrupt_agent_session(
            role=role,
            session_key=key,
            stamp_path=self._active_agent_stamp,
            source=source,
            skip_if_idle=True,
        )
        self._active_agent_session_key = None
        self._active_agent_role = None
        self._active_agent_stamp = None

    def _handle_stall_outcome(
        self,
        agent_role: str,
        session_key: str,
        stamp_path: str,
        reason: str,
    ) -> bool:
        """Interrupt the just-detected stalled / startup-timeout session, then record the
        outcome.

        Called by all three pipeline-agent poll sites (planner / executor / reviewer) when
        ``poll_for_sentinel`` returns ``PollResult`` with
        ``reason in {"stalled", "no_first_activity", "timeout", "tool_loop"}`` (the ``"timeout"``
        reason was added after the CORE-E6 cascade — the infra backstop previously bypassed
        abort+verify, letting attempt N+1 launch on top of the still-streaming N; ``"tool_loop"``
        reuses this abort to kill a live in-turn loop before the agent's self-failure retry).

        Delegates to :meth:`_interrupt_agent_session` with ``skip_if_idle=False``: on a stall the
        activity stamp is quiet *by definition* (and on a tool_loop the session is actively
        spinning), so we always steer to kill a presumed-wedged / looping run (the liveness
        pre-check would otherwise skip exactly the sessions we mean to stop).  The
        settle-wait inside the helper replaces the old single-shot ``verify_session_stopped`` that
        false-failed on the steer's own spawned follow-up turn.

        Always returns ``True`` — every outcome lets the caller continue (soft-continue contract,
        kept for the three poll-site guards that still check it)."""
        result = self._interrupt_agent_session(
            role=agent_role,
            session_key=session_key,
            stamp_path=stamp_path,
            source="inline_stall",
            skip_if_idle=False,
            reason=reason,
        )
        self._record_phase_outcome(
            last_abort_result={
                "ok": "ok",
                "failed": "FAILED",
                "unconfirmed": "verify_failed",
                "skipped_idle": "ok",
            }.get(result, "ok")
        )
        return True

    def _maybe_tool_loop_detector(self, agent_role, session_key, env_name, default):
        """Build the per-role tool-loop ``loop_detector`` for ``poll_for_sentinel``,
        or ``None`` when that role's detector is disabled.

        ``env_name`` is the per-role threshold knob (e.g.
        ``TOOL_LOOP_REPEAT_LIMIT_EXECUTOR``, default 15 for headroom on the executor's
        varied legitimate tool use; planner / reviewer default 8).
        ``_tool_loop_repeat_limit`` returns ``0`` for an
        explicit ``0`` — the operator's per-role OFF switch — and clamps any other
        value to a floor of 2; a sub-2 limit yields ``None`` so the poll runs without
        the hook.  One chokepoint shared by all three poll sites (the inline form was
        identical at each).
        """
        limit = _tool_loop_repeat_limit(env_name, default)
        if limit < 2:
            return None
        return self._make_tool_loop_detector(agent_role, session_key, limit)

    def _make_tool_loop_detector(self, agent_role, session_key, limit):
        """Return a ``poll_for_sentinel`` ``loop_detector`` predicate that reports the
        agent spinning on identical tool calls inside one turn.

        Mirrors :meth:`_make_verdict_hold_acceptor`'s closure shape (zero-arg,
        ``_resolve_session_jsonl_path`` + a mutable for cross-call state).  The poll
        consults it every ~2 s tick; it self-throttles to
        ``_TOOL_LOOP_CHECK_INTERVAL_SECONDS`` and caches its last verdict between
        scans, so the JSONL tail read stays negligible.  On a trip it stashes the
        offending ``{tool_name, args_excerpt, repeat_count}`` onto
        ``self._pending_tool_loop`` (read+cleared by :meth:`_note_tool_loop`) and
        returns ``True``; the orchestrator then aborts the live session and routes
        the ``tool_loop`` outcome through the agent's self-failure retry path.
        """
        state = {"last_check": 0.0, "verdict": False}

        def _detector() -> bool:
            now = time.time()
            if now - state["last_check"] < _TOOL_LOOP_CHECK_INTERVAL_SECONDS:
                return state["verdict"]
            state["last_check"] = now
            detail = _detect_tool_loop_in_jsonl(
                _resolve_session_jsonl_path(agent_role, session_key), limit
            )
            if detail:
                self._pending_tool_loop = detail
                state["verdict"] = True
            else:
                state["verdict"] = False
            return state["verdict"]

        return _detector

    def _note_tool_loop(self, agent_role, raw_id):
        """Record a detected in-turn tool-loop as the model self-failure it is.

        Emits the ``tool_loop_detected`` event and stamps honest ``ERR_TOOL_LOOP``
        attribution onto ``phase_state`` (``last_error_code`` +
        ``escalation_trigger_reason``).  It does **not** abort, retry, or escalate:
        the caller routes the falsy ``tool_loop`` poll result through the agent's
        existing self-failure path (abort via :meth:`_handle_stall_outcome`, then the
        site's increment-cap-escalate), which consumes one of the agent's
        self-failure retries — a fresh session often resamples a good trajectory —
        and escalates carrying this attribution once the budget is spent.  One-shot:
        the stashed detail is read and cleared.
        """
        detail = getattr(self, "_pending_tool_loop", None) or {}
        self._pending_tool_loop = None
        tool_name = detail.get("tool_name", "?")
        count = detail.get("repeat_count", 0)
        # Make the live log point at the real cause: the self-failure path this falls
        # through to otherwise prints "[ERROR] Sentinel timeout", which reads as a dead
        # gateway at debug time.
        print(
            f"[TOOL_LOOP] {agent_role} repeated {tool_name} x{count} with identical "
            "input — aborting the session and retrying it as a self-failure."
        )
        _write_pipeline_event(
            "tool_loop_detected",
            raw_id,
            agent_role,
            {
                "tool_name": tool_name,
                "repeat_count": count,
                "args_excerpt": detail.get("args_excerpt", ""),
            },
        )
        try:
            ps = self.read_phase_state()
            ps["last_error_code"] = ERR_TOOL_LOOP
            base = (
                f"{agent_role.capitalize()} stuck in a tool-call loop: repeated "
                f"{tool_name} with identical input {count}x consecutively, making no "
                "progress."
            )
            # The how_to_check hypothesis is only meaningful for the reviewer — it is
            # the role that runs verification checks; a looping planner / executor has
            # nothing to do with a roadmap how_to_check, so don't misdirect the operator.
            if agent_role == "reviewer":
                hint = (
                    " The check it exercises is likely subjective/unverifiable — "
                    "review its how_to_check."
                )
            else:
                hint = (
                    " It was aborted and retried in a fresh session; escalated after "
                    "the loop persisted."
                )
            ps["escalation_trigger_reason"] = base + hint
            self.write_phase_state_atomic(ps)
        except Exception as e:  # phase_state write is best-effort; the event already fired
            print(f"[WARN] _note_tool_loop: phase_state write failed: {e}")

    def _make_verdict_hold_acceptor(
        self, agent_role: str, session_key: str, attempt_start_time: float
    ):
        """Return a ``poll_for_sentinel`` ``sentinel_acceptor`` predicate that HOLDS
        a premature ``.done`` until the agent's real verdict lands, instead of
        accepting it into a false CONTRACT_FAILURE that also spawns a concurrent
        retry (Layer 2 — see the module-level ``_is_recoverable_context_overflow``
        cluster for the original overflow rationale).

        The returned zero-arg predicate is consulted only while ``.done`` exists.
        It returns:

        * ``True`` immediately if a fresh, parseable ``{role}_output.json`` is
          already present — never discard a verdict the turn produced;
        * ``True`` if the hold budget (``_OVERFLOW_HOLD_BUDGET_SECONDS``) is spent —
          stop holding and let the gate adjudicate the still-missing verdict;
        * ``False`` (HOLD) when there is no fresh verdict AND the turn is still in
          flight past ``.done`` — by either of two signals:
            (a) the session's last assistant row is a recoverable context-overflow
                error (OpenClaw auto-compacts + resumes) → ``sentinel_overflow_hold``;
            (b) the session's last assistant row is a non-terminal tool-loop step
                (``stopReason`` in ``_IN_FLIGHT_STOP_REASONS``, i.e. ``"toolUse"``) →
                the agent wrote ``.done`` but is still acting and may yet write the
                verdict (observed: reviewer writes ``.done`` / the agent_end backstop
                fires, then keeps streaming and writes a valid PASS minutes later) →
                ``sentinel_verdict_hold``;
        * ``True`` otherwise — a terminally-ended turn (any other / absent
          ``stopReason``) with no verdict → the gate's CONTRACT_FAILURE / FAIL path
          runs with **no stall-window latency**.

        Terminality, not stamp-vs-``.done`` timing, drives (b): the ``.done`` write's
        own ``after_tool_call`` hook bumps the activity stamp *after* ``.done`` even
        on a genuine no-verdict end, so the stamp cannot distinguish "ended" from
        "still streaming" — it would false-hold every real CONTRACT_FAILURE until
        stall/budget. Bounded so a held sentinel can never hang the poll: the hold
        budget above, plus the poll's own stall detection (a turn that stops touching
        its session goes silent > stall threshold → abort).
        """
        verdict_path = os.path.join(PROJECT_ARTIFACTS_DIR, f"{agent_role}_output.json")
        phase_raw_id = (getattr(self, "state", {}) or {}).get("current_phase_raw_id", "")
        emitted = {"overflow": False, "streaming": False}

        def _acceptor() -> bool:
            # 1. A real verdict already landed (the turn won the race).
            if _verdict_is_fresh_and_parseable(verdict_path, attempt_start_time):
                return True
            # 2. Hold budget exhausted — stop waiting; the gate decides.
            if time.time() - attempt_start_time > _OVERFLOW_HOLD_BUDGET_SECONDS:
                return True
            # 3. Recoverable overflow with no verdict yet → HOLD (compact + resume).
            err = _session_jsonl_last_assistant_error_message(
                _resolve_session_jsonl_path(agent_role, session_key)
            )
            if _is_recoverable_context_overflow(err):
                if not emitted["overflow"]:
                    emitted["overflow"] = True
                    _write_pipeline_event(
                        "sentinel_overflow_hold",
                        phase_raw_id,
                        agent_role,
                        {
                            "agent_role": agent_role,
                            "session_key": session_key,
                            "error_excerpt": str(err)[:200],
                            "elapsed_s": int(time.time() - attempt_start_time),
                        },
                    )
                return False
            # 3b. Still streaming past .done: HOLD only while the session's last
            #     assistant row is a non-terminal tool-loop step (stopReason
            #     "toolUse") — the agent wrote .done but is still acting and may yet
            #     write the verdict. A terminally-ended turn (any other / absent
            #     stopReason, incl. a clean stop or an unresolvable session) is NOT
            #     held: it falls through to (4) → accept → the gate's
            #     CONTRACT_FAILURE path, with no stall-window latency. Terminality is
            #     used here, NOT stamp-vs-.done timing: the .done write's own
            #     after_tool_call hook bumps the stamp after .done even on a genuine
            #     end, so the stamp cannot tell "ended" from "still streaming".
            if _session_jsonl_last_assistant_stop_reason(
                _resolve_session_jsonl_path(agent_role, session_key)
            ) in _IN_FLIGHT_STOP_REASONS:
                if not emitted["streaming"]:
                    emitted["streaming"] = True
                    _write_pipeline_event(
                        "sentinel_verdict_hold",
                        phase_raw_id,
                        agent_role,
                        {
                            "agent_role": agent_role,
                            "session_key": session_key,
                            "reason": "turn_in_flight",
                            "elapsed_s": int(time.time() - attempt_start_time),
                        },
                    )
                return False
            # 4. Turn ended (terminal stopReason) or terminality unknown, no verdict
            #    → accept (the gate handles it as today).
            return True

        return _acceptor

    def _escalate_if_provider_rejected(self, jsonl_path: str | None, role_label: str) -> bool:
        """If the session JSONL ends with a provider-rejection error (billing, rate-limit, or auth),
        set last_error_code=ERR_PROVIDER_REJECTED, fill escalation_trigger_reason with the provider
        message, and route to escalation.

        Returns True when escalation was triggered (caller must ``continue`` the main loop). May be
        called more than once per attempt (post-poll and post-gate) to absorb JSONL flush ordering.

        Opt-in override (``PROVIDER_ERROR_RETRY=N``): a *transient* provider rejection
        (rate-limit) re-invokes the same agent in place up to N times before escalating
        (current_agent unchanged → the caller's ``continue`` re-runs it). See
        :func:`provider_error_retry_limit` and :meth:`_provider_retry_suffix`.
        """
        msg = _session_jsonl_last_assistant_error_message(jsonl_path)
        if not msg or not _is_provider_rejected_error(msg):
            # Not a provider rejection: the agent produced real output (or failed for a
            # non-provider reason), so the provider worked this turn and the consecutive
            # transient-error streak is broken — clear the retry budget for a later 429.
            self._reset_provider_error_retries()
            return False

        # Opt-in (PROVIDER_ERROR_RETRY=N): retry a *transient* provider rejection
        # (rate-limit) up to N times before escalating, by re-invoking the SAME agent in
        # place — current_agent is left unchanged, so the caller's `continue` re-runs the
        # loop on the same agent. The agent block appends _provider_retry_suffix() to the
        # session key, so each retry runs on a fresh OpenClaw session (never resuming the
        # rate-limited one) and the per-agent self-failure attempt counter is NOT consumed
        # (a flaky provider must not burn the executor's "bad code, try again" budget).
        # Terminal (auth/billing) rejections fall through and always escalate — retrying
        # cannot fix them.
        limit = provider_error_retry_limit()
        if limit > 0 and _is_transient_provider_error(msg):
            used = self._provider_error_retries()
            if used < limit:
                _ps = self.read_phase_state()
                _ps["provider_error_retries"] = used + 1
                self.write_phase_state_atomic(_ps)
                print(
                    f"[PROVIDER-RETRY] [{role_label}] Transient provider rejection "
                    f"(retry {used + 1}/{limit}) — re-invoking instead of escalating: "
                    f"{msg[:200]}"
                )
                return True  # caller continues; current_agent unchanged → same agent re-runs
            print(
                f"[PROVIDER-RETRY] [{role_label}] Transient provider rejection — retry "
                f"budget exhausted ({used}/{limit}); escalating: {msg[:200]}"
            )

        print(f"[ERROR] [{role_label}] Provider rejected request: {msg[:240]}")
        _ps = self.read_phase_state()
        _ps["last_error_code"] = ERR_PROVIDER_REJECTED
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

    def _provider_error_retries(self) -> int:
        """Consecutive transient-provider-error retries already used this phase
        (``provider_error_retries`` in phase_state; auto-resets when phase_state is
        deleted on advance / reset_phase). 0 on any read error."""
        try:
            return int(self.read_phase_state().get("provider_error_retries", 0) or 0)
        except (ValueError, TypeError):
            return 0

    def _provider_retry_suffix(self) -> str:
        """Session-key suffix (``-pr{N}``) that makes an in-place PROVIDER_ERROR_RETRY
        re-invoke use a fresh OpenClaw session instead of resuming the rate-limit-killed
        one — without bumping the per-agent attempt counter that the base key encodes.
        Empty on the common path (no provider retry in flight), so non-retry session
        keys stay byte-identical to the legacy shape. Mirrors the reviewer ``-c{N}``
        contract-retry discriminator."""
        n = self._provider_error_retries()
        return f"-pr{n}" if n else ""

    def _reset_provider_error_retries(self) -> None:
        """Clear the consecutive provider-retry counter (writes only when set). Called
        whenever an attempt ends in a non-(transient-provider) outcome, so the budget is
        per *consecutive* rate-limit streak: a success or a genuine code failure means
        the provider worked, so a later 429 gets the full N retries again."""
        _ps = self.read_phase_state()
        if _ps.get("provider_error_retries", 0):
            _ps["provider_error_retries"] = 0
            self.write_phase_state_atomic(_ps)

    def _reviewer_session_key(self, phase, raw_id, reviewer_retries,
                              contract_retries, unverified_retries=0):
        """Build the reviewer session key for an attempt.

        The base key encodes the code-quality pass (``reviewer-attempt-N``). Neither a
        CONTRACT_FAILURE soft-retry nor a contract-shape ``*_UNVERIFIED`` retry bumps
        ``reviewer_retries``, so without a discriminator successive retries of either
        kind would reuse one key. Append:
          * ``-c{N}`` when ``contract_retries > 0`` (CONTRACT_FAILURE soft-retry), and
          * ``-u{N}`` when ``unverified_retries > 0`` (VISUAL/BEHAVIORAL/REGRESSION_
            UNVERIFIED retry).
        so each retry gets a deterministic, distinct key and therefore a **fresh
        OpenClaw session**. The common, non-retry path stays byte-identical to the
        legacy shape.

        Fresh-per-retry is deliberate (not just hygiene): re-using the prior session on
        OpenClaw 2026.6.x reattaches a possibly-still-streaming embedded run and re-enters
        the agent's prior (rejected / context-overflowed) context — both observed to
        produce confused or repeat-failing retries. A fresh session + the enriched
        ``reviewer_retry_directive`` (which states exactly what the gate flagged) is the
        reliable path. Matches the CONTRACT_FAILURE ``-c{N}`` behaviour.
        """
        base = f"pipeline:phase-{phase}:{raw_id}:reviewer-attempt-{reviewer_retries + 1}"
        if contract_retries:
            base = f"{base}-c{contract_retries}"
        if unverified_retries:
            base = f"{base}-u{unverified_retries}"
        return base

    def _invoke_reviewer(self, session_key, token):
        """Invoke the reviewer webhook, delivering a one-shot corrective directive.

        The unified ``reviewer_retry_directive`` phase-state field — written by the
        CONTRACT_FAILURE branch and the UNVERIFIED handler — carries a concise,
        self-contained instruction for the *next* reviewer session. Deliver it here as
        the webhook ``message=`` (overriding the reviewer's default message, which only
        the prompt can do) and clear it immediately so it is one-shot: a later normal
        pass must not re-inject a stale directive. When absent, the reviewer receives
        its default message.

        This is the reviewer's single directive channel — the delivered counterpart of
        the executor's file-based ``failure_context.json``. Structured data the agent
        *analyzes* goes in a file (``failure_context.json`` / ``gate_warnings.json``); a
        one-shot directive that *frames* the invocation goes in ``message=``. (See
        PIPELINE-SPEC.md §7.) For ``*_UNVERIFIED`` retries the directive is enriched by
        :meth:`_compose_unverified_directive` with the gate's specific problem list so
        the reviewer sees exactly what failed, not just the generic contract reminder.
        """
        directive = None
        try:
            _ps = self.read_phase_state()
            directive = _ps.get("reviewer_retry_directive")
            if directive:
                _ps.pop("reviewer_retry_directive", None)
                self.write_phase_state_atomic(_ps)
        except Exception:
            directive = None
        return invoke_agent_webhook(
            "reviewer", session_key, token, message=directive or None,
            url=self.openclaw_config.get("hooks_url"),
        )

    def _compose_unverified_directive(self, gate_result, detail):
        """Build the reviewer retry directive for a contract-shape verdict.

        Returns the generic per-verdict instruction from :data:`_UNVERIFIED_INSTRUCTIONS`,
        enriched with the gate's specific problem list (``detail`` — the value the gate
        stashed in ``phase_state['reviewer_unverified_detail']``) when present, so the
        re-invoked reviewer is told exactly what failed (e.g. "evidence must have at
        least 3 entries"). Pure function — the inline handler that calls it lives in
        ``run()``.

        The appended specifics are bounded (first 3 problems, ~500 chars) so a
        pathological problem list can't bloat the webhook message. ``detail`` may be
        ``None``/empty (no enrichment) — a normal first-pass verdict carries no detail.
        """
        base = _UNVERIFIED_INSTRUCTIONS[gate_result]
        if detail:
            specifics = "; ".join(str(d) for d in detail[:3])[:500]
            if specifics:
                base = (
                    f"{base} Specifically, the prior reviewer output had: {specifics}"
                )
        return base

    def _invoke_executor(self, session_key, token):
        """Invoke the executor webhook, delivering a one-shot corrective directive.

        The ``executor_retry_directive`` phase-state field — written by the
        reviewer-gate MISSING_ARTIFACTS handler — carries a concise, self-contained
        instruction for the *next* executor session (produce the missing completion
        artifacts). Deliver it here as the webhook ``message=`` (overriding the
        executor's default message, which only the prompt can do) and clear it
        immediately so it is one-shot: a later normal/self-failure retry must not
        re-inject a stale directive. When absent, the executor receives its default
        message and reads ``failure_context.json`` from disk as usual — the file-based
        self-failure channel is independent of this message-side directive channel.

        Counterpart of the reviewer's ``reviewer_retry_directive``: structured data the
        agent *analyzes* goes in a file (``failure_context.json`` / ``gate_warnings.json``);
        a one-shot directive that *frames* the invocation goes in ``message=``.
        (See PIPELINE-SPEC.md §7.)
        """
        directive = None
        try:
            _ps = self.read_phase_state()
            directive = _ps.get("executor_retry_directive")
            if directive:
                _ps.pop("executor_retry_directive", None)
                self.write_phase_state_atomic(_ps)
        except Exception:
            directive = None
        return invoke_agent_webhook(
            "executor", session_key, token, message=directive or None,
            url=self.openclaw_config.get("hooks_url"),
        )

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
        # Deliberately in-process (NOT the subprocess path used by run_planner_output_gate):
        # importing and calling the gate function directly lets a test's workspace patches take
        # effect, whereas a subprocess would inherit the real OPENCLAW_ROOT and ignore the mocks.
        # The two paths share one verdict contract (planner_gate.evaluate_planner) and are kept
        # as two mechanisms ON PURPOSE — run_planner_output_gate wants process isolation on the
        # normal loop; this restart-detection helper wants test-mockability. Not reconciled. See
        # gate_scripts/README.md.
        try:
            gate_dir = GATE_SCRIPTS_DIR
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
        executor_retries / escalation fields, causing misrouted retries.
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
        try:
            write_json_atomic(PHASE_STATE_FILE, phase_state, indent=2)
        except Exception as e:
            print(f"[ERROR] Failed to write phase_state: {e}")

    def _accumulate_role_tokens(self, role: str, jsonl_path: str) -> None:
        """Record this attempt's token usage, keyed by its session JSONL path.

        Maintains ``{role}_tokens_sessions`` (per-attempt sums keyed by the
        session JSONL path) and rebuilds ``{role}_tokens_acc`` as the sum over
        all entries. Replacing by key gives two properties the old
        add-each-call accumulator lacked:

        - Re-reading the SAME session (a RETRY after restart resumes the
          attempt-1 session key → same JSONL) replaces its earlier
          contribution instead of double-counting it, while distinct attempts
          (distinct session keys → distinct JSONLs) still sum — the T4.8
          guarantee that reviewer/planner/executor re-invocations accumulate.
        - ``_refresh_role_token_accumulators`` can re-sum each file at
          metrics-row-write time, picking up usage rows the agent streamed
          AFTER writing ``.done`` (the sentinel-time snapshot under-counted
          by ~32% on LLN-1 CORE-E2: +565k reviewer tokens post-sentinel).

        A pre-keyed ``{role}_tokens_acc`` found without a sessions map (a
        phase in flight across the deploy of this change) is frozen into the
        map under ``_TOKENS_LEGACY_SESSION_KEY`` so its contribution survives.
        Both keys are dropped by ``reset_phase``'s fresh phase_state dict, so
        the totals stay per-phase and zero on genuine phase advance.

        Degraded-capture signal (observability integrity finding 1): an
        attempt whose session JSONL is missing — or whose session path never
        resolved (``jsonl_path`` is None) — contributes silent zeros that are
        indistinguishable from a genuinely free attempt, the same observable
        failure as the historic usage-field-name bug. Such an attempt latches
        ``phase_state.token_capture_degraded`` (copied onto the canonical
        metrics row, never cleared mid-phase — the zeros are already baked
        into the totals) and emits one ``token_capture_warning`` event per
        degraded attempt so the activity feed shows it live.
        """
        _ps = self.read_phase_state()
        if not jsonl_path or not os.path.exists(jsonl_path):
            _ps["token_capture_degraded"] = True
            _write_pipeline_event(
                "token_capture_warning",
                self.state.get("current_phase_raw_id", ""),
                role,
                {
                    "session_jsonl": jsonl_path or None,
                    "reason": "no_session_path" if not jsonl_path else "missing_session_file",
                },
            )
        _sessions = _ps.get(f"{role}_tokens_sessions")
        if not isinstance(_sessions, dict):
            _sessions = {}
            _legacy = _ps.get(f"{role}_tokens_acc")
            if isinstance(_legacy, dict) and _legacy:
                _sessions[_TOKENS_LEGACY_SESSION_KEY] = dict(_legacy)
        if jsonl_path:
            _sessions[jsonl_path] = _sum_session_tokens(jsonl_path)
        _ps[f"{role}_tokens_sessions"] = _sessions
        _ps[f"{role}_tokens_acc"] = _merge_token_sums(_sessions.values())
        self.write_phase_state_atomic(_ps)

    def _refresh_role_token_accumulators(self) -> None:
        """Re-sum every recorded session JSONL and rebuild the accumulators.

        Called by ``_write_canonical_metrics_row`` before it reads the
        ``{role}_tokens_acc`` fields, so the durable metrics row reflects each
        session file's FINAL contents rather than the sentinel-time snapshot
        (agents routinely keep streaming after writing ``.done``). Covers all
        recorded attempts, not just the last — a zombie attempt that streamed
        past its own sentinel is recounted too.

        Per-entry safety: the legacy frozen entry has no file and is kept
        as-is; a missing file (session pruned) keeps its stored sum; and since
        session JSONLs are append-only, a re-sum smaller than the stored
        ``total_tokens`` (truncated/rotated/unreadable file) is discarded in
        favour of the snapshot — the refresh never shrinks a contribution.
        """
        _ps = self.read_phase_state()
        _changed = False
        for role in ("planner", "executor", "reviewer"):
            _sessions = _ps.get(f"{role}_tokens_sessions")
            if not isinstance(_sessions, dict) or not _sessions:
                continue
            for _path, _stored in list(_sessions.items()):
                if _path == _TOKENS_LEGACY_SESSION_KEY:
                    continue
                if not _path or not os.path.exists(_path):
                    continue
                _new = _sum_session_tokens(_path)
                _stored_total = (
                    _stored.get("total_tokens", 0) if isinstance(_stored, dict) else 0
                )
                if _new.get("total_tokens", 0) >= _stored_total:
                    _sessions[_path] = _new
            _ps[f"{role}_tokens_acc"] = _merge_token_sums(_sessions.values())
            _changed = True
        if _changed:
            self.write_phase_state_atomic(_ps)

    def _clean_escalation_headline(self, raw_id=None):
        """P1 Stage G1 — a clean, deterministic headline for the escalation panel.

        Returns a phase-level string the UI can render as the escalation
        headline WITHOUT ever surfacing the raw internal
        ``escalation_trigger_reason``. Derived solely from the phase id, so it is
        structurally incapable of echoing internal error strings. Persisted as
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

    def _emit_gate_warnings(self, raw_id):
        """Drain gate_warnings.json (the executor gate's reviewer-facing PASS
        channel) into one summarising ``gate_warning`` event and a compact
        ``last_gate_warnings`` stash on phase_state — but, unlike
        ``_emit_reachability_advisory``, **do NOT remove the file**. The reviewer
        reads it next to adjudicate the demoted warnings (accept-and-proceed or
        reject-with-specifics). The executor gate's start-of-run clear is what
        prevents stale warnings; removing it here would starve the reviewer.

        On the clean-pass common case (no file) this clears any stale
        ``last_gate_warnings`` so the metrics row never reports a prior attempt's
        warnings. Best-effort + read-modify-write throughout so a phase_state
        hiccup never breaks the PASS path or clobbers sibling keys."""
        warnings_path = os.path.join(PROJECT_ARTIFACTS_DIR, "gate_warnings.json")
        if not os.path.exists(warnings_path):
            # Clean pass — drop any stale stash so the row's gate_warnings is null.
            try:
                _ps = self.read_phase_state()
                if _ps.pop("last_gate_warnings", None) is not None:
                    self.write_phase_state_atomic(_ps)
            except Exception as _gw_err:
                print(f"[WARN] _emit_gate_warnings: stale-stash clear failed: {_gw_err}")
            return
        try:
            with open(warnings_path, "r") as f:
                doc = json.load(f)
        except Exception as e:
            print(f"[WARN] _emit_gate_warnings: could not read gate_warnings: {e}")
            return
        if not isinstance(doc, dict):
            return
        warns = doc.get("warnings")
        if not isinstance(warns, list) or not warns:
            return
        codes = sorted({
            w.get("code") for w in warns
            if isinstance(w, dict) and w.get("code")
        })
        files = []
        for w in warns:
            if not isinstance(w, dict):
                continue
            for _k in ("files", "missing_tests"):
                _v = w.get(_k)
                if isinstance(_v, list):
                    files.extend(str(x) for x in _v)
        _write_pipeline_event(
            "gate_warning",
            raw_id,
            "executor",
            {"count": len(warns), "codes": codes, "files": files},
        )
        # Stash a compact summary so the canonical metrics row (written later on
        # the reviewer-PASS path) can persist the warning outcome. Read-modify-
        # write preserves sibling phase_state keys. The file is intentionally
        # left on disk for the reviewer.
        try:
            _ps = self.read_phase_state()
            _ps["last_gate_warnings"] = {"count": len(warns), "codes": codes}
            self.write_phase_state_atomic(_ps)
        except Exception as _gw_err:
            print(f"[WARN] _emit_gate_warnings: stash failed: {_gw_err}")

    # Max characters of planner scope_warning text carried into the event /
    # phase_state stash. The planner contract is "one sentence"; this is a
    # defensive bound so a runaway string can't bloat the event log or the row.
    _SCOPE_WARNING_MAX_CHARS = 500

    def _emit_scope_warning(self, raw_id):
        """Drain a ``scope_warning`` string from ``planner_output.json`` into one
        ``scope_warning`` event and a compact ``last_scope_warning`` stash on
        phase_state — the read-side consumer for the planner's descope signal.

        The planner emits an OPTIONAL top-level ``scope_warning`` string when a
        phase exceeds a single executor pass and it descoped rather than produce
        an over-broad plan (see planner AGENTS.md). The verdict gate tolerates the
        field but does not surface it; without this the one signal that says "this
        phase was too big and I shrank it" evaporated. Called on every planner-PASS
        (mirrors ``_emit_gate_warnings`` on executor-PASS): the stash is picked up
        by ``_write_canonical_metrics_row`` (``scope_warning`` field) so the
        durable per-phase history records it.

        On the clean common case (no field) this clears any stale
        ``last_scope_warning`` so the metrics row never reports a prior attempt's
        warning. Best-effort + read-modify-write throughout so a phase_state
        hiccup never breaks the PASS path or clobbers sibling keys. Unlike
        ``gate_warnings.json`` there is no separate file to preserve — the signal
        lives inside ``planner_output.json``, which the executor reads next."""
        planner_path = os.path.join(PROJECT_ARTIFACTS_DIR, "planner_output.json")
        warning = None
        try:
            with open(planner_path, "r") as f:
                doc = json.load(f)
            if isinstance(doc, dict):
                _w = doc.get("scope_warning")
                if isinstance(_w, str) and _w.strip():
                    warning = _w.strip()[: self._SCOPE_WARNING_MAX_CHARS]
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[WARN] _emit_scope_warning: could not read planner_output: {e}")
            return

        if warning is None:
            # Clean pass — drop any stale stash so the row's scope_warning is null.
            try:
                _ps = self.read_phase_state()
                if _ps.pop("last_scope_warning", None) is not None:
                    self.write_phase_state_atomic(_ps)
            except Exception as _sw_err:
                print(f"[WARN] _emit_scope_warning: stale-stash clear failed: {_sw_err}")
            return

        _write_pipeline_event(
            "scope_warning",
            raw_id,
            "planner",
            {"warning": warning},
        )
        try:
            _ps = self.read_phase_state()
            _ps["last_scope_warning"] = warning
            self.write_phase_state_atomic(_ps)
        except Exception as _sw_err:
            print(f"[WARN] _emit_scope_warning: stash failed: {_sw_err}")

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

    def _resolve_notification_channel(self, agent_id: str = "escalation") -> str | None:
        """Resolve the operator-notification channel for raw gateway POSTs, or None.

        Provider-agnostic: the channel is whatever the operator has bound in
        OpenClaw, never a hardcoded literal. Resolution order:
          1. an explicit ``notification_channel`` key in the merged config;
          2. the OpenClaw binding whose ``agentId`` is *agent_id* — its
             ``match.channel`` (the live shape is
             ``{"agentId": "escalation", "match": {"channel": "signal"}}``);
          3. ``None`` — the caller must skip the POST and log, not guess a channel.

        Never raises (mirrors ``_get_agent_model``).
        """
        explicit = self.openclaw_config.get("notification_channel")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
        for binding in self.openclaw_config.get("bindings", []) or []:
            if isinstance(binding, dict) and binding.get("agentId") == agent_id:
                channel = (binding.get("match") or {}).get("channel")
                if isinstance(channel, str) and channel.strip():
                    return channel.strip()
        return None

    def _post_raw_notification(self, message) -> bool:
        """Resolve the operator channel and POST one raw notification to the gateway.

        Returns ``True`` on a delivered 2xx; ``False`` when the channel cannot be
        resolved (logs + skips the POST — never guesses a channel) or the POST
        fails. Never raises; callers branch on the bool. This is the single
        raw-notification POST implementation, shared by the reset-cap notices
        (via ``send_raw_notification``) and the escalation-webhook-failed fallback.
        """
        channel = self._resolve_notification_channel()
        if not channel:
            print(
                "[WARN] No operator notification channel resolved (no "
                "notification_channel config key and no escalation binding "
                "match.channel); skipping raw notification."
            )
            return False
        token = self.openclaw_config.get("hooks", {}).get("token", "")
        url = self.openclaw_config.get("hooks_url") or "http://localhost:18789/hooks/agent"
        payload = {"channel": channel, "message": message}
        try:
            headers = {"Authorization": f"Bearer {token}"}
            # timeout bounds the call so it can never hang the orchestrator while it
            # holds pipeline.lock (heartbeat-cron could not then restart it).
            r = requests.post(url, json=payload, headers=headers, timeout=15)
            r.raise_for_status()
            print(f"[INFO] Operator notification sent via {channel}: {message[:80]}")
            return True
        except Exception as e:
            print(f"[ERROR] Failed to send operator notification via {channel}: {e}")
            return False

    def send_raw_notification(self, message):
        """Send a raw operator notification over the configured channel (fire-and-forget).

        Thin wrapper over ``_post_raw_notification`` for the reset-cap notices,
        which do not branch on delivery success.
        """
        self._post_raw_notification(message)

    def reset_phase(self):
        """Full phase-level reset. Triggered by RESET_PHASE resume command (escalation-only).

        Returns ``True`` on a successful reset, ``False`` when the git operations failed.

        T4.1 (Decision #4) — fail closed: if step 1/2 below raises
        ``CalledProcessError`` (dirty tree, missing base, repo lock), the method does
        **not** proceed to wipe outputs / re-init phase_state / transition to
        planner-RUNNING (which would "succeed" while leaving a corrupt tree). It instead
        records ``ERR_RESET_PHASE_GIT_FAILED``, routes to escalation, and returns
        ``False`` — so callers (the RESET_PHASE dispatch handler, ``nuclear_reset_phase``)
        gate their ``escalation_resets++`` / ``nuclear_resets++`` on confirmed success.

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
            # T4.1 (Decision #4) — fail closed: do NOT proceed to wipe outputs /
            # re-init phase_state / transition to planner-RUNNING (which would
            # "succeed" while leaving a corrupt tree). Route to escalation and
            # return False so the caller gates its escalation_resets++ /
            # nuclear_resets++ on confirmed success.
            print(f"[ERROR] reset_phase git operations failed: {e}")
            _ps_fail = self.read_phase_state()
            _ps_fail["last_error_code"] = ERR_RESET_PHASE_GIT_FAILED
            _ps_fail["escalation_trigger_reason"] = f"reset_phase git operations failed: {e}"
            self.write_phase_state_atomic(_ps_fail)
            self.state["current_agent"] = "escalation"
            self.transition_state("RUNNING", f"RESET_PHASE git failure on phase {raw_id or phase}: {e}")
            return False

        # Clear all six output pairs and phase_state.json
        for fname in [
            "planner_output.json", "planner_output.done",
            "executor_output.json", "executor_output.done",
            "reviewer_output.json", "reviewer_output.done",
            "phase_state.json", "failure_context.json",
            "executor_gate_detail.json",
            # P1 Stage F — advisory channel; same per-phase artifact lifecycle.
            "executor_advisory_detail.json",
            # Phase 3 — reviewer-facing gate-warnings channel; same per-phase
            # artifact lifecycle. _emit_gate_warnings preserves it for the
            # reviewer on the PASS path; these reset/exclude sites wipe it.
            "gate_warnings.json",
        ]:
            try:
                os.remove(os.path.join(PROJECT_ARTIFACTS_DIR, fname))
            except FileNotFoundError:
                pass

        # Re-initialize phase_state: agent counters → 0, escalation_resets preserved (cap intact).
        # RR-4 (Phase 2): reviewer_contract_retries is zeroed on phase reset — it is a
        # per-phase soft-retry budget, not a global cap.
        # RR-2 (Phase 4): planner_output_preserved cleared — new phase, no preserved output.
        # P0 Stage H — reset_phase is the canonical boundary at which the
        # lifetime counters re-zero. Reviewer rejection and operator
        # escalation reset do NOT reset them (those preserve lifetime
        # visibility into prior failures).
        new_phase_state = {
            # NB: the base counters are spelled out literally here (not spread from
            # _default_phase_state()) on purpose — test_p0_stage_h_phase_state_defaults
            # asserts each executor counter appears at *every* phase_state init site, which
            # guards the metrics-row invariant that the keys exist on the first write of a
            # new phase. Keep them inline if you touch this block.
            "planner_retries": 0,
            "executor_retries": 0,
            "executor_self_failure_retries": 0,
            "executor_reviewer_rejection_retries": 0,
            "reviewer_retries": 0,
            "reviewer_rejected": False,
            "reviewer_contract_retries": 0,
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
        gate_script = os.path.join(GATE_SCRIPTS_DIR, "phase_resolver.py")
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
        return True

    def nuclear_reset_phase(self):
        """P1 Stage G2 — operator escape hatch (cap 2). Destructive true-fresh-start.

        A thin wrapper over reset_phase(): it reuses reset_phase's mechanics verbatim
        (git reset --hard to the phase base commit, delete the phase branch, wipe all
        phase outputs, zero every retry counter by the wholesale phase_state
        replacement, re-plan from the planner with
        _current_attempt_retry_class = "initial_attempt"). It does NOT re-list those
        counters — they are cleared by reset_phase's fresh-dict write.

        It differs from reset_phase ONLY in governance: it increments its own
        nuclear_resets counter (cap 2, enforced by the dispatch branch and the server's
        /api/command validation) instead of being blocked by the escalation_resets cap (3),
        and appends a NUCLEAR_RESET reset_log entry. T4.1 (Decision #4): the increment +
        log are written AFTER a CONFIRMED reset_phase() — a git-failed reset returns False,
        routes to escalation, and charges NO nuclear budget. reset_phase() PRESERVES both
        nuclear_resets and reset_log across its re-init (see reset_phase docstring), so
        incrementing after success carries the bumped counter and the new audit entry into
        the final phase_state.

        Note: the escalation_resolve pipeline event is already emitted for every command at
        the top of the dispatch loop, so this method must not re-emit it.
        """
        _ps = self.read_phase_state()
        _reason = _ps.get("last_error_code", "unknown")
        # Capture the escalated phase pointer before reset_phase() runs so the
        # observability event carries it rather than any post-reset value.
        _phase_at_reset = self.state.get("current_phase_raw_id", "")
        print(f"[INFO] nuclear_reset_phase: attempting nuclear reset, reason={_reason!r}.")
        # T4.1 (Decision #4) — charge the nuclear budget + record the destructive
        # action ONLY on a CONFIRMED reset. reset_phase() returns False and routes to
        # escalation (tree intact) on a git failure; in that case leave nuclear_resets
        # untouched and emit nothing. reset_phase preserves nuclear_resets / reset_log
        # across its re-init, so increment AFTER success.
        if not self.reset_phase():
            return
        _ps2 = self.read_phase_state()
        _ps2["nuclear_resets"] = _ps2.get("nuclear_resets", 0) + 1
        _ps2.setdefault("reset_log", []).append({
            "reset_number": _ps2["nuclear_resets"],
            "command": "NUCLEAR_RESET",
            "reason": _reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        _ps2["last_phase_outcome"] = "nuclear_reset"  # Phase 3 — survives reset_phase's re-init
        self.write_phase_state_atomic(_ps2)
        print(f"[INFO] nuclear_reset_phase: nuclear_resets now {_ps2['nuclear_resets']}, reason={_reason!r}.")
        # Phase 2 (observability) — record the destructive ACTION on the timeline. The
        # dispatch loop already emits escalation_resolve for the *command*; this captures
        # that the phase work was actually discarded.
        _write_pipeline_event(
            "nuclear_reset",
            _phase_at_reset,
            "escalation",
            {"nuclear_resets": _ps2["nuclear_resets"], "reason": _reason,
             "phase": _phase_at_reset},
        )

    def reset_execution(self, caller: str):
        """Partial execution-level reset. Preserves planner output. Clears executor + reviewer outputs.

        Returns ``True`` on a successful reset, ``False`` when the git operations failed.

        T4.1 (Decision #4) — fail closed: if the ``git checkout`` / ``git reset --hard``
        below raises ``CalledProcessError``, the method does **not** proceed to clear
        outputs, change counters (including the ``escalation_resets++`` for
        ``caller="escalation"``), or transition to executor-RUNNING. It records
        ``ERR_RESET_EXECUTION_GIT_FAILED``, routes to escalation, and returns ``False`` —
        so no reset budget is charged on a failed reset (the internal increments are
        skipped by the early return).

        Phase 2 — self-failure feedback parity: on an ordinary gate failure the
        executor's WORKING TREE is PRESERVED (the hard ``git reset --hard HEAD``
        runs only for ``ERR_UNACCOUNTED_DELETION``), and ``failure_context.json``
        is kept (not cleared) so the fresh executor session reads the gate's note
        (``source: "gate"`` + ``retry_guidance``) and makes a targeted fix —
        mirroring the reviewer-rejection ROUTE_EXECUTOR path.

        caller='auto'       — from automatic executor retry path. Increments executor_retries
                              AND the lifetime executor_self_failure_retries counter (P0 Stage H).
                              Also sets self._current_attempt_retry_class = "executor_self_failure"
                              so subsequent gate_fail / attempt_end events label the retry source.
        caller='escalation' — from RESET_EXECUTION resume command. Increments escalation_resets.
                              Resets executor_retries to 0 (fresh budget) but does NOT touch the
                              lifetime self-failure / rejection counters (operator visibility into
                              prior failures is preserved across escalation resets). Also restores
                              a fresh reviewer pooled budget (reviewer_contract_retries /
                              reviewer_unverified_retries / reviewer_artifacts_retries → 0),
                              mirroring reset_reviewer — the reviewer reviews brand-new executor
                              output, so a stale maxed pool must not re-escalate it on first verdict.
        Never increments both legacy counters in one call. The lifetime counters are independent
        of the legacy counter and tracked alongside it for the metrics-row invariant
        ``executor_attempts == executor_self_failures + executor_reviewer_rejections + 1``.

        After this returns, the main loop (current_agent='executor', RUNNING) re-invokes the executor.
        """
        phase = self.state.get("current_phase", 0)
        raw_id = self.state.get("current_phase_raw_id", "")
        branch = f"phase/{raw_id}" if raw_id else f"phase/{phase}"

        # Phase 2 — gate-failure feedback parity. On an ordinary self-failure
        # retry we PRESERVE the executor's working tree so the fresh session
        # iterates on its prior work (mirrors the reviewer-rejection
        # ROUTE_EXECUTOR path, which never resets). The full ``git reset --hard
        # HEAD`` is kept ONLY for ERR_UNACCOUNTED_DELETION, where it restores
        # committed files MiniMax deleted under context pressure.
        _preserve_worktree = (
            self.read_phase_state().get("last_error_code") != ERR_UNACCOUNTED_DELETION
        )
        try:
            subprocess.run(["git", "checkout", branch], cwd=SYMLINK_TARGET, check=True)
            if _preserve_worktree:
                print(f"[INFO] reset_execution({caller}): working tree PRESERVED on {branch} (self-failure feedback path).")
            else:
                subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=SYMLINK_TARGET, check=True)
                print(f"[INFO] reset_execution({caller}): hard reset on {branch} (unaccounted deletion — restoring deleted files).")
        except subprocess.CalledProcessError as e:
            # T4.1 (Decision #4) — fail closed: do NOT proceed to clear outputs /
            # change counters (including the escalation_resets++ below) / transition
            # to executor-RUNNING on a failed reset. Route to escalation and return
            # False; the internal increments are skipped by this early return, so no
            # reset budget is charged.
            print(f"[ERROR] reset_execution git operations failed: {e}")
            _ps_fail = self.read_phase_state()
            _ps_fail["last_error_code"] = ERR_RESET_EXECUTION_GIT_FAILED
            _ps_fail["escalation_trigger_reason"] = f"reset_execution({caller}) git operations failed: {e}"
            self.write_phase_state_atomic(_ps_fail)
            self.state["current_agent"] = "escalation"
            self.transition_state("RUNNING", f"RESET_EXECUTION git failure on phase {raw_id or phase}: {e}")
            return False

        # §5.3 fix (reset_execution path): git reset --hard HEAD restores the committed
        # version of current_phase.json, which may be stale from a prior completed phase.
        # Re-run roadmap_parser to refresh it before the executor retries.
        import glob as _re_glob
        _re_gate = os.path.join(GATE_SCRIPTS_DIR, "phase_resolver.py")
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
            # Phase 2: failure_context.json is intentionally NOT cleared here — it
            # carries the gate's note (source:"gate" + retry_guidance) into the
            # next, FRESH executor session (which has no memory of this attempt).
            # write_failure_context overwrites it on the next failure; reset_phase
            # clears it on phase advance.
            "executor_gate_detail.json",
            # P1 Stage F — advisory channel; same per-phase artifact lifecycle.
            "executor_advisory_detail.json",
            # Phase 3 — reviewer-facing gate-warnings channel; same per-phase
            # artifact lifecycle. _emit_gate_warnings preserves it for the
            # reviewer on the PASS path; these reset/exclude sites wipe it.
            "gate_warnings.json",
        ]:
            try:
                os.remove(os.path.join(PROJECT_ARTIFACTS_DIR, fname))
            except FileNotFoundError:
                pass

        # RR-4 (Phase 2): reset_execution zeros reviewer_retries and reviewer_rejected so
        # the next reviewer invocation starts at pass 1.  The reviewer POOLED counters
        # (reviewer_contract_retries / reviewer_unverified_retries / reviewer_artifacts_retries)
        # are preserved on the 'auto' path (per-phase auto budget — a flapping executor must
        # not farm the reviewer an infinite budget) but ZEROED in the 'escalation' branch
        # below: an operator RESET_EXECUTION is an explicit decision to re-run from scratch,
        # so the reviewer (reviewing brand-new output) gets a fresh pooled budget, mirroring
        # reset_reviewer. (Both also reset on a full phase reset / reset_phase.)
        # executor_succeeded is cleared because we are re-running execution from scratch.
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
            # main-loop iteration re-enters the `retries >= 3` exhausted branch and
            # escalates again instead of actually re-invoking the executor.  The UI
            # attempt chips (driven by executor_retries from pipeline_state.json) also
            # remain red instead of resetting to a fresh 3-slot budget.
            phase_state["executor_retries"] = 0
            self.state["executor_retries"] = 0
            # Restore a FRESH reviewer pooled budget (mirrors reset_reviewer). Without
            # this, an already-maxed reviewer_contract_retries (cap 3) /
            # reviewer_unverified_retries (cap 2) / reviewer_artifacts_retries (cap 2)
            # survives the operator reset, so the reviewer — re-invoked against the
            # fresh executor output — re-escalates on its FIRST verdict with zero real
            # retries (the "fast fail, no retries" symptom) and the attempt dots render
            # 3 red the instant review begins. These are phase_state-only (read via
            # read_phase_state by the gate handlers and _reviewer_session_key), so no
            # self.state mirror is needed (unlike reviewer_retries above).
            phase_state["reviewer_contract_retries"] = 0
            phase_state["reviewer_unverified_retries"] = 0
            phase_state["reviewer_artifacts_retries"] = 0
            # One-shot *_UNVERIFIED problem list must not bleed into the fresh cycle.
            phase_state.pop("reviewer_unverified_detail", None)
            phase_state["escalation_resets"] = phase_state.get("escalation_resets", 0) + 1
            new_count = phase_state["escalation_resets"]
            print(f"[INFO] reset_execution(escalation): executor_retries reset to 0, reviewer pooled budget reset, escalation_resets now {new_count}.")
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
        return True

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
        # Defense-in-depth: drop any pending *_UNVERIFIED problem list so a reviewer
        # reset cannot carry a stale directive-enrichment detail into the next pass.
        # (reset_phase already drops it by rebuilding the dict; this path read-modify-
        # writes, so clear it explicitly. reset_execution does not need it — the
        # *_UNVERIFIED/CONTRACT handlers always read-and-pop the detail before any
        # executor flow could run.)
        phase_state.pop("reviewer_unverified_detail", None)
        # Operator-driven reset: restore a FRESH reviewer retry budget, mirroring
        # reset_execution('escalation') for the executor (which zeros the same three pools).
        # These pooled counters are deliberately preserved by reset_execution('auto') and
        # only otherwise cleared by reset_phase, so without this an already-maxed counter
        # survives the operator reset and the next reviewer failure re-escalates immediately
        # (the live "fast fail, no retries" symptom: the contract counter climbing 3->4->5
        # across three RESET_REVIEWERs). reviewer_artifacts_retries (cap 2) matters here too:
        # left maxed, the next MISSING_ARTIFACTS escalates instantly; zeroed, it drops back
        # under cap so the handler re-invokes the executor to actually produce the missing
        # artifacts. All three are phase_state-only (the CONTRACT_FAILURE / *_UNVERIFIED /
        # MISSING_ARTIFACTS handlers and _reviewer_session_key read them via read_phase_state),
        # so no self.state mirror is needed (unlike reviewer_retries).
        phase_state["reviewer_contract_retries"] = 0
        phase_state["reviewer_unverified_retries"] = 0
        phase_state["reviewer_artifacts_retries"] = 0
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
        except (OSError, subprocess.SubprocessError):
            pass  # best-effort branch check; the checkout below is the real guard
        try:
            self._checkout_or_create_branch(branch)
            print(f"[INFO] _ensure_phase_branch: HEAD corrected to '{branch}'.")
            return True
        except subprocess.CalledProcessError:
            print(f"[ERROR] _ensure_phase_branch: could not checkout or create '{branch}'.")
            return False

    def _checkout_or_create_branch(self, branch: str, *, check: bool = True):
        """Check out ``branch``, creating it if absent — without invoking a shell.

        Replaces the former
        ``subprocess.run(f"git checkout {b} 2>/dev/null || git checkout -b {b}", shell=True)``
        idiom at its three call sites (``_ensure_phase_branch``, the phase-advance
        block, and startup). Passing argv as a **list** means ``branch`` is handed to
        git as a single argument and is never parsed by a shell, closing the
        command-injection path from a roadmap-supplied ``raw_id`` (LAUNCH-3). The id
        is *also* charset-validated at parse time in ``phase_resolver`` — this is the
        sink-side guard, defense-in-depth.

        Behaviour mirrors the old idiom: try ``git checkout <branch>``; on failure
        fall back to ``git checkout -b <branch>``. ``check`` defaults to ``True``
        (matching the two former ``check=True`` sites) so a branch that can be
        neither checked out nor created raises ``CalledProcessError``; the startup
        site passes ``check=False`` to preserve its best-effort behaviour. Output is
        captured so the first checkout's "pathspec did not match" noise stays hidden,
        as the old ``2>/dev/null`` did.

        If the ``-b`` creation itself fails, git's stderr is logged once here before
        raising (``check=True``) or returning (``check=False``) — the old idiom left
        that fallback's stderr un-redirected on the console, so capturing it without
        surfacing it would silently drop the failure reason at every call site
        (including the best-effort startup site, which inspects no return value).
        The raised ``CalledProcessError`` also carries ``stderr`` for callers that
        want it.
        """
        existing = subprocess.run(
            ["git", "checkout", branch],
            cwd=SYMLINK_TARGET, capture_output=True, text=True,
        )
        if existing.returncode == 0:
            return existing
        created = subprocess.run(
            ["git", "checkout", "-b", branch],
            cwd=SYMLINK_TARGET, capture_output=True, text=True,
        )
        if created.returncode != 0:
            _stderr = (created.stderr or "").strip()
            if _stderr:
                print(f"[ERROR] _checkout_or_create_branch: git could not check out "
                      f"or create '{branch}': {_stderr}")
            if check:
                raise subprocess.CalledProcessError(
                    created.returncode, created.args,
                    output=created.stdout, stderr=created.stderr,
                )
        return created

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
            write_text_atomic(_roadmap_path, _new_content)
            print(f"[INFO] _mark_roadmap_phase: marked {raw_id} as [{marker}] in roadmap.")
        except Exception as _e:
            print(f"[WARN] _mark_roadmap_phase: could not update roadmap for {raw_id!r}: {_e}")

    def _flip_roadmap_checkbox_or_escalate(self, roadmap_path, phase) -> bool:
        """Flip the just-completed phase's roadmap checkbox ``[ ]``→``[x]`` and fold
        it into the merge commit. Returns ``True`` on success.

        T4.4 — on a NON-git failure (read-only roadmap, encoding error) this routes
        to escalation (``ERR_ROADMAP_CHECKBOX_FAILED``, Decision #5 operator
        message) and returns ``False`` rather than swallowing the error and letting
        the caller tag + advance: the merge commit has already landed, but the
        roadmap still shows the phase incomplete, so the resolver would re-return it
        → silent re-run → ``ERR_MERGE_FAILED`` on the now-empty branch. A git
        ``CalledProcessError`` is re-raised for the caller's outer git handler
        (which already escalates). Extracted from the reviewer-PASS merge block so
        this fail-closed decision is unit-testable in isolation.
        """
        try:
            with open(roadmap_path, 'r') as f:
                rmap_lines = f.readlines()
            _chk_raw_id = self.state.get("current_phase_raw_id", "")
            _flipped = False
            for i, rline in enumerate(rmap_lines):
                rmatch = re.match(r'- \[( |x|-|!)\] `([^`]+)` \|', rline.strip())
                if rmatch:
                    _, phase_id = rmatch.groups()
                    # Prefer exact raw_id match — avoids collision when multiple phases
                    # share the same trailing integer (e.g. INFRA-1, CORE-1, UI-1 all → 1).
                    if _chk_raw_id:
                        if phase_id == _chk_raw_id:
                            _new = rline.replace('- [ ]', '- [x]').replace('- [!]', '- [x]')
                            if _new != rline:
                                rmap_lines[i] = _new
                                _flipped = True
                            break
                    else:
                        parts = phase_id.split('-')
                        phase_num = int(parts[-1]) if len(parts) > 1 and parts[-1].isdigit() else 0
                        if phase_num == phase:
                            _new = rline.replace('- [ ]', '- [x]').replace('- [!]', '- [x]')
                            if _new != rline:
                                rmap_lines[i] = _new
                                _flipped = True
                            break
            # T6.4/B3 — idempotent re-entry: if the checkbox is already [x] (no change), do NOT
            # rewrite the file or `git commit --amend`. An amend with no content delta still
            # rewrites the merge-commit SHA (and moves the --force phase-complete tag) on every
            # restart, churning history and risking an amend of the wrong commit on a double restart.
            if not _flipped:
                print(f"[INFO] Roadmap checkbox for {_chk_raw_id or phase} already set; no amend needed.")
                return True
            with open(roadmap_path, 'w') as f:
                f.writelines(rmap_lines)
            # Fold checkbox update into the merge commit atomically.
            subprocess.run(["git", "add", roadmap_path], cwd=SYMLINK_TARGET, check=True)
            subprocess.run(["git", "commit", "--amend", "--no-edit"], cwd=SYMLINK_TARGET, check=True)
            print(f"[INFO] Roadmap checkbox for {_chk_raw_id or phase} folded into merge commit.")
            return True
        except subprocess.CalledProcessError:
            raise  # let the caller's outer except handle git failures
        except Exception as e:
            # T4.4 — fail closed: the merge landed but the checkbox didn't flip.
            reason = (
                f"merge succeeded, roadmap checkbox couldn't be flipped — fix and "
                f"resume (Phase {self.state.get('current_phase_raw_id', '') or phase}): {e}"
            )
            print(f"[ERROR] {reason} — routing to escalation.", file=sys.stderr)
            _ps = self.read_phase_state()
            _ps["last_error_code"] = ERR_ROADMAP_CHECKBOX_FAILED
            _ps["escalation_trigger_reason"] = reason
            self.write_phase_state_atomic(_ps)
            self.state["current_agent"] = "escalation"
            self.transition_state("RUNNING", reason)
            return False

    def _advance_to_next_pending_phase(self, *, trigger: str) -> str:
        """Resolve the roadmap and advance to the next pending phase.

        Shared by the reviewer-PASS phase-complete path and the SKIP / PROCEED
        escalation commands so the three sites cannot drift (F3).  The caller
        must have ALREADY recorded phase closure in the roadmap (PASS via the
        merge commit; PROCEED marks ``[x]`` + git-tags; SKIP marks ``[-]``);
        this helper only advances pipeline state.

        It wipes the per-phase artifacts, clears ``current_phase_raw_id``, re-runs
        ``phase_resolver``, and acts on the outcome:

          * PENDING            — load the resolver-written ``current_phase.json``,
                                 set the new phase/raw_id, zero the planner /
                                 executor / reviewer retry counters, stamp
                                 ``phase_start_time``, capture ``phase_base_commit``
                                 (``git rev-parse HEAD`` — ``reset_phase`` rewinds
                                 to it), checkout ``phase/{raw}`` and transition to
                                 RUNNING.
          * PIPELINE_COMPLETE  — clear ``current_agent``, run the opt-in completion
                                 review, mark the active queue entry COMPLETED, and
                                 auto-advance the queue if eligible.  On an in-process
                                 advance, re-run startup init (``_run_startup_loop``)
                                 so the new project resolves its phase + captures
                                 ``phase_base_commit`` before any agent runs (Phase 8;
                                 otherwise the executor hits ``ERR_MISSING_BASE_COMMIT``).
          * BLOCKED (rc 2)     — park the active queue entry BLOCKED and try to
                                 advance the queue; an in-process advance likewise
                                 re-runs ``_run_startup_loop`` for the new project.
          * resolver error / unexpected rc / unrecognised output — route to the
                                 escalation agent (``current_agent="escalation"``,
                                 an honest ``escalation_trigger_reason`` recording
                                 the rc + stderr, ``last_error_code=
                                 ERR_PHASE_RESOLVER_FAILED``, transition RUNNING)
                                 and return ``"continue"`` so the caller re-enters
                                 the loop and the main-loop escalation dispatch
                                 fires the webhook + advisory + queue-park (F4).

        Returns
        -------
        str
            ``"continue"`` — the caller must ``continue`` the main loop (next
            phase started, the queue advanced to a new project / parked entry, or
            a resolver failure routed to escalation).
            ``"break"``    — the caller must ``break`` the main loop (pipeline
            complete with nothing queued, or blocked with no queue advance).

        ``trigger`` (``"phase_complete"`` / ``"skip"`` / ``"proceed"``) is used
        only for the log line so post-mortems can tell which path advanced.
        """
        phase = self.state.get("current_phase", 0)
        # Working-file cleanup: close out the just-finished phase's artifacts so
        # no stale failure_context / outputs leak into the next phase.
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

        print(f"[INFO] Phase {phase} closed (trigger={trigger}). Resolving next pending phase.")
        self.state["current_agent"] = "planner"  # reset to start
        self.state["current_phase"] = 0
        self.state["current_phase_raw_id"] = ""
        # Phase identification is a pure script.
        gate_script = os.path.join(GATE_SCRIPTS_DIR, "phase_resolver.py")
        result = None
        output = ""
        try:
            # Pass nothing to use default locator
            result = subprocess.run([sys.executable, gate_script], capture_output=True, text=True, timeout=GATE_SUBPROCESS_TIMEOUT)
            output = result.stdout.strip()
            if result.returncode == 0 and "PENDING: Phase" in output:
                # Start next phase correctly.
                # The current_phase.json is written by phase_resolver.py
                _cp_path = os.path.join(PROJECT_ARTIFACTS_DIR, "current_phase.json")
                if os.path.exists(_cp_path):
                    # T4.3 — guard the resolver-written phase file. A truncated /
                    # corrupt file (crash mid-write, disk-full) raises
                    # JSONDecodeError — uncaught, since the outer except only
                    # catches CalledProcessError / TimeoutExpired; a valid-but-shapeless file
                    # (no raw_id) would silently advance to current_phase=0,
                    # raw_id="" (branch "phase/", colliding session keys). Route
                    # either to the same F4 ERR_PHASE_RESOLVER_FAILED escalation
                    # as a missing verdict rather than crashing or advancing blind.
                    try:
                        with open(_cp_path, 'r') as f:
                            new_phase = json.load(f)
                        if not isinstance(new_phase, dict) or not new_phase.get("raw_id"):
                            raise ValueError(f"current_phase.json missing raw_id: {new_phase!r}")
                    except (json.JSONDecodeError, OSError, ValueError) as _cp_err:
                        reason = (f"current_phase.json unreadable on advance "
                                  f"(trigger={trigger}): {_cp_err}")
                        print(f"[ERROR] {reason} — routing to escalation.", file=sys.stderr)
                        _ps = self.read_phase_state()
                        _ps["last_error_code"] = ERR_PHASE_RESOLVER_FAILED
                        _ps["escalation_trigger_reason"] = reason
                        self.write_phase_state_atomic(_ps)
                        self.state["current_agent"] = "escalation"
                        self.transition_state("RUNNING", reason)
                        return "continue"
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
                        self._checkout_or_create_branch(branch)
                    except subprocess.CalledProcessError as e:
                        print(f"[ERROR] Failed to checkout new phase branch: {e}")

                    self.transition_state("RUNNING", f"Started Phase {self.state['current_phase']}")
                    time.sleep(2)
                    return "continue"
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
                        # The advanced-to project was activated at a blank
                        # phase-0/planner state by _select_next_queue_project. Re-run
                        # the startup phase-resolution + branch checkout +
                        # phase_base_commit capture (the same routine a fresh launch
                        # runs) BEFORE re-entering the main loop — otherwise the new
                        # project's executor runs against an empty raw_id with no
                        # phase_base_commit → permanent ERR_MISSING_BASE_COMMIT
                        # (Phase 8). A revival activation (current_agent="escalation")
                        # makes _run_startup_loop a no-op, so this is safe for both.
                        if self._run_startup_loop() == "exit_run":
                            return "break"
                        return "continue"
                return "break"
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
                    # Same Phase-8 re-init as the PIPELINE_COMPLETE arm: the queue
                    # advanced in-process to a fresh-start project — resolve its
                    # phase + capture phase_base_commit before re-entering the loop.
                    if self._run_startup_loop() == "exit_run":
                        return "break"
                    return "continue"
                return "break"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            # TimeoutExpired: the gate hit GATE_SUBPROCESS_TIMEOUT. result stays None,
            # so the F4 block below escalates via the "raised before returning a verdict"
            # path — the same routing a CalledProcessError gets.
            print(f"[ERROR] phase_resolver subprocess raised: {e}")

        # F4 — reached only when the resolver produced no actionable verdict:
        # rc 1 (roadmap not found / non-absolute path / write failure), an
        # unexpected rc/stdout, a CalledProcessError, or a PENDING verdict with no
        # current_phase.json on disk. Route to escalation rather than dead-ending
        # at a silent "break" that would leave the orchestrator RUNNING with no
        # phase and no operator signal. Returning "continue" re-enters the main
        # loop, whose escalation dispatch (current_agent == "escalation") fires the
        # webhook + advisory + queue-park.
        if result is not None:
            _detail = (
                f"rc={result.returncode} output={output!r} "
                f"stderr={(result.stderr or '')[-500:]!r}"
            )
        else:
            _detail = "phase_resolver subprocess raised before returning a verdict"
        reason = f"phase_resolver produced no actionable verdict (trigger={trigger}): {_detail}"
        print(f"[ERROR] {reason} — routing to escalation.", file=sys.stderr)
        _ps = self.read_phase_state()
        _ps["last_error_code"] = ERR_PHASE_RESOLVER_FAILED
        _ps["escalation_trigger_reason"] = reason
        self.write_phase_state_atomic(_ps)
        self.state["current_agent"] = "escalation"
        self.transition_state("RUNNING", reason)
        return "continue"

    def increment_executor_retries(self):
        # LAUNCH-8: read via read_phase_state() — see increment_planner_retries. A corrupt
        # phase_state quarantines + raises (→ escalation) rather than clobbering counters.
        phase_state = self.read_phase_state() or self._default_phase_state()
        phase_state["executor_retries"] = phase_state.get("executor_retries", 0) + 1

        try:
            write_json_atomic(PHASE_STATE_FILE, phase_state, indent=2)
        except Exception as e:
            print(f"[ERROR] Failed to write phase_state: {e}")

        self.state["executor_retries"] = phase_state["executor_retries"]
        self.transition_state("RUNNING", f"Incremented executor retries to {phase_state['executor_retries']}")
        return phase_state["executor_retries"]

    def run_executor_output_gate(self):
        """Run the executor verdict gate as a subprocess; return True iff it emits ``PASS``.

        Verdict-gate convention (see gate_scripts/README.md): exits 0 with the verdict on
        stdout; FAIL detail rides side channels (``executor_gate_detail.json`` /
        ``gate_warnings.json`` / ``last_error_code``), not the return value. A gate-script
        crash or timeout is treated as a safe failure (``False``).
        """
        gate_script = os.path.join(GATE_SCRIPTS_DIR, "executor_gate.py")
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
        to be made: planner gate fail, executor gate fail (including the
        retry-exhausted escalation path), and reviewer gate fail.  Overwrites any prior failure_context.json
        — always reflects the most recent failure.  Non-blocking: errors are logged and
        swallowed so a write failure never crashes the pipeline.

        Phase 2: tags the context ``source: "gate"`` and adds a concise
        ``retry_guidance`` note (parallel to ``_write_reviewer_failure_context``'s
        ``source: "reviewer"``); the executor reads both on a preserved-work
        self-failure retry — see executor ``AGENTS.md`` Scenario A.
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
            except (OSError, json.JSONDecodeError):
                pass

        # --- Reviewer blocking issues (if reviewer just failed) ---
        reviewer_output = {}
        reviewer_output_path = os.path.join(PROJECT_ARTIFACTS_DIR, "reviewer_output.json")
        if os.path.exists(reviewer_output_path):
            try:
                with open(reviewer_output_path, 'r') as f:
                    reviewer_output = json.load(f)
            except (OSError, json.JSONDecodeError):
                pass

        # --- Gate error codes from phase_state (last_error_code field) ---
        gate_error_codes = []
        last_error = phase_state.get("last_error_code")
        if last_error:
            gate_error_codes = [last_error]

        # --- files_present_on_disk: raw filesystem truth for failure review ---
        # Walk SYMLINK_TARGET, exclude pipeline metadata files and git internals.
        # Compared against file_manifest by the escalation agent / operator:
        # missing files indicate deletion or failed write; unexpected files
        # indicate scope creep.
        _pipeline_meta = {
            "phase_state.json", "planner_output.json", "planner_output.done",
            "executor_output.json", "executor_output.done",
            "reviewer_output.json", "reviewer_output.done",
            "escalation_output.json", "escalation_output.done",
            "failure_context.json", "current_phase.json",
            "executor_gate_detail.json",
            # P1 Stage F — advisory channel; same per-phase artifact lifecycle.
            "executor_advisory_detail.json",
            # Phase 3 — reviewer-facing gate-warnings channel; same per-phase
            # artifact lifecycle. _emit_gate_warnings preserves it for the
            # reviewer on the PASS path; these reset/exclude sites wipe it.
            "gate_warnings.json",
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

        # Phase 2 — gate-failure feedback parity. Tag the deterministic-gate
        # failure context (parallel to _write_reviewer_failure_context's
        # ``source: "reviewer"``) and synthesise a concise, high-signal note so
        # the FRESH executor session — which has no memory of the prior attempt —
        # makes a targeted fix instead of rebuilding blind.
        context["source"] = "gate"
        _codes = ", ".join(gate_error_codes) if gate_error_codes else "(none reported)"
        if ERR_UNACCOUNTED_DELETION in gate_error_codes:
            _work_note = (
                "Files you deleted without declaring them have been RESTORED from the "
                "last commit; redo your change without removing tracked files (list any "
                "intentional deletions in files_deleted)."
            )
        else:
            _work_note = (
                "Your prior work is PRESERVED on the branch — do not rebuild from "
                "scratch; make a TARGETED fix to the specific failure."
            )
        context["retry_guidance"] = (
            f"Your previous attempt was rejected by the deterministic gate ({_codes}). "
            f"{_work_note} Read the detail in this file (gate_error_codes, "
            f"agent_failure_reason, tests_passing, files_present_on_disk, "
            f"gate_failure_detail)."
        )

        _failure_context_path = os.path.join(PROJECT_ARTIFACTS_DIR, "failure_context.json")
        try:
            os.makedirs(PROJECT_ARTIFACTS_DIR, exist_ok=True)
        except OSError:
            pass
        try:
            # _append_failure_history archives the currently-committed file, so it
            # must run BEFORE write_json_atomic replaces it (W1-D: archive before
            # overwrite). The shared helper does temp-write + os.replace atomically.
            self._append_failure_history(_failure_context_path)
            write_json_atomic(_failure_context_path, context, indent=2)
            print(
                f"[INFO] write_failure_context: wrote failure_context.json "
                f"(phase={context['phase_raw_id']}, agent={failing_agent}, attempt={attempt_number})"
            )
        except Exception as e:
            print(f"[ERROR] write_failure_context failed: {e}")

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
            write_json_atomic(fc_path, existing, indent=2)
            print(
                f"[REVIEWER_GATE] failure_context augmented "
                f"(phase={existing['phase_id']}, "
                f"blocking_issues={len(existing['blocking_issues'])})"
            )
        except Exception as e:
            print(f"[ERROR] _write_reviewer_failure_context failed: {e}")

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

        P1-A — the row also carries ``run_id`` (from ``self.state``) so
        completed-phase history is groupable per run.
        """
        raw_id = self.state.get("current_phase_raw_id", "")
        if not raw_id:
            print(
                "[WARN] _write_canonical_metrics_row: no current_phase_raw_id, "
                "skipping"
            )
            return

        # Re-sum each attempt's session JSONL before reading the token
        # accumulators below — agents keep streaming after writing .done, so
        # the sentinel-time snapshot under-counts (LLN-1 CORE-E2: +565k
        # reviewer tokens, ~32%, landed after the snapshot). Writes the
        # refreshed accumulators to phase_state, which ps_m re-reads.
        self._refresh_role_token_accumulators()

        # --- Compose the canonical row (schema preserved from inline writer) ---
        duration_seconds = None
        phase_start_time = self.state.get("phase_start_time")
        if phase_start_time:
            try:
                start_dt = datetime.fromisoformat(phase_start_time)
                duration_seconds = int(time.time() - start_dt.timestamp())
            except (ValueError, TypeError):
                pass  # unparseable phase_start_time → duration stays None

        goal_text = ""
        cp_path = os.path.join(PROJECT_ARTIFACTS_DIR, "current_phase.json")
        if os.path.exists(cp_path):
            try:
                with open(cp_path) as f:
                    cp_data = json.load(f)
                goal_text = cp_data.get("detail", "")
            except (OSError, json.JSONDecodeError):
                pass

        reviewer_passes = self.state.get("reviewer_retries", 0) + 1

        ps_m = {}
        if os.path.exists(PHASE_STATE_FILE):
            try:
                with open(PHASE_STATE_FILE) as f:
                    ps_m = json.load(f)
            except (OSError, json.JSONDecodeError):
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
            "run_id": self.state.get("run_id"),  # run identity (null pre-deploy)
            "phase": raw_id,
            "goal": goal_text,
            "executor_attempts": executor_attempts,
            # P0 Stage H — additive breakdown fields.
            "executor_self_failures": executor_self_failures,
            "executor_reviewer_rejections": executor_reviewer_rejections,
            "reviewer_passes": reviewer_passes,
            "escalations": ps_m.get("escalations", 0),    # W1-B
            "skill_used": ps_m.get("skill_injected"),      # W1-C
            # MON-1 — {role: model} stamped per invocation by
            # _record_active_agent; null for pre-deploy rows.
            "models_used": ps_m.get("models_used"),
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
            # METRICS-E1 — durable degraded-capture marker: True when any
            # attempt this phase had a missing/unresolved session JSONL, so
            # the token fields above may silently under-count (latched by
            # _accumulate_role_tokens; absent pre-deploy rows read as False).
            "token_capture_degraded": bool(ps_m.get("token_capture_degraded", False)),
            "reachability_summary": ps_m.get("last_reachability_summary"),
            # Phase 3 (gate-feedback methodology) — compact copy of the demoted
            # gate warnings, stashed onto phase_state by _emit_gate_warnings on
            # the executor-PASS path (null when the attempt that passed raised no
            # warnings). Durable record of "what the gate flagged for the reviewer."
            "gate_warnings": ps_m.get("last_gate_warnings"),
            # v0.2.1 — the planner's descope signal, stashed onto phase_state by
            # _emit_scope_warning on the planner-PASS path (null when the passing
            # plan raised none). Durable record that a phase was too big for one
            # executor pass and the planner narrowed it.
            "scope_warning": ps_m.get("last_scope_warning"),
            "reset_log": ps_m.get("reset_log", []),
            # P1-B — structured cause of this phase's escalation(s), resolved at the
            # escalation dispatch and persisted to phase_state. null when the phase
            # never escalated.
            "escalation_trigger_class": ps_m.get("escalation_trigger_class"),
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
            try:
                os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
                write_text_atomic(target, full_content)
            except Exception as e:
                print(
                    f"[ERROR] Failed to write canonical metrics to "
                    f"{target}: {e}"
                )

        _write_pipeline_event(  # W1-F
            "phase_complete",
            raw_id,
            "reviewer",
            {
                "executor_attempts": executor_attempts,
            },
        )
        print(
            f"[INFO] Canonical metrics row written for {raw_id}: "
            f"{executor_attempts} executor attempt(s), "
            f"{reviewer_passes} reviewer pass(es), "
            f"{duration_seconds}s duration"
        )

    def set_reviewer_rejected(self):
        # LAUNCH-8: read via read_phase_state() so a corrupt phase_state is quarantined
        # rather than silently degrading to {} and writing back a single-key dict that
        # wipes the other counters. This path is contractually never-raise (ROUTE_EXECUTOR
        # must not crash), so a corrupt read is caught here: skip the write (don't clobber)
        # and surface it; the flag is reconstructed on the next clean phase_state read.
        try:
            phase_state = self.read_phase_state()
        except RuntimeError as e:
            print(f"[WARN] set_reviewer_rejected: corrupt phase_state quarantined, skipping write — {e}")
            return
        phase_state["reviewer_rejected"] = True
        # Best-effort, never-raise: matches the pre-LAUNCH-5 contract
        # (_atomic_temp_dir_for_project_writes() + swallowed os.replace). The
        # makedirs guarantees the temp dir exists so write_json_atomic's mkstemp
        # cannot raise FileNotFoundError *before* its raise_on_error=False try
        # block; the surrounding except swallows a makedirs/mkstemp OSError so a
        # missing/unwritable artifacts dir can never crash the ROUTE_EXECUTOR path.
        try:
            os.makedirs(os.path.dirname(PHASE_STATE_FILE) or ".", exist_ok=True)
            write_json_atomic(PHASE_STATE_FILE, phase_state, indent=2, raise_on_error=False)
        except OSError:
            pass

    def increment_reviewer_retries(self):
        # LAUNCH-8: read via read_phase_state() — see increment_planner_retries. A corrupt
        # phase_state quarantines + raises (→ escalation) rather than clobbering counters.
        phase_state = self.read_phase_state() or self._default_phase_state()
        phase_state["reviewer_retries"] = phase_state.get("reviewer_retries", 0) + 1

        try:
            write_json_atomic(PHASE_STATE_FILE, phase_state, indent=2)
        except Exception as e:
            print(f"[ERROR] Failed to write phase_state: {e}")

        self.state["reviewer_retries"] = phase_state["reviewer_retries"]
        self.transition_state("RUNNING", f"Incremented reviewer retries to {phase_state['reviewer_retries']}")
        return phase_state["reviewer_retries"]

    def run_reviewer_output_gate(self):
        """Run the reviewer verdict gate as a subprocess; return its raw verdict/route token.

        Verdict-gate convention (see gate_scripts/README.md): exits 0 and prints one of
        ``PASS`` or a route token (``ROUTE_EXECUTOR`` / ``ROUTE_PLANNER`` / ``ROUTE_ESCALATE``
        / ``*_UNVERIFIED`` / ``MISSING_ARTIFACTS`` / ``CONTRACT_FAILURE``), returned verbatim
        for the caller to dispatch on. A gate-script crash or timeout fails safe to
        ``ROUTE_ESCALATE`` (never parsed as a PASS).
        """
        gate_script = os.path.join(GATE_SCRIPTS_DIR, "reviewer_gate.py")
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
        gate_script = os.path.join(GATE_SCRIPTS_DIR, "repo_init_check.py")
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

        If phase_resolver produces no actionable verdict — rc 1 (roadmap not found
        / non-absolute path / write failure), an unexpected rc/stdout, or the
        subprocess crashing — the method routes to the escalation agent
        (``current_agent="escalation"`` + honest ``escalation_trigger_reason``,
        ``last_error_code=ERR_PHASE_RESOLVER_FAILED``, transition RUNNING) and
        returns ``"enter_main_loop"`` so the main-loop escalation dispatch fires,
        rather than proceeding to a blind planner run with an empty raw_id (F4).

        Driven by :meth:`_run_startup_loop` (which honors the ``"retry_startup"``
        re-entry below) — called by :meth:`run` at launch AND after an in-process
        queue auto-advance, so a queued project is resolved + branched the same way
        a fresh launch is (Phase 8). Self-guards: returns ``"enter_main_loop"``
        immediately when ``current_agent != "planner"`` (e.g. a revival), so
        re-running it on a non-fresh-start activation is a safe no-op.

        Returns:
            "exit_run" — leave run() entirely (orchestrator stops).
            "retry_startup" — symlink/project may have changed; re-run this method.
            "enter_main_loop" — proceed to the main while True loop (also the
                resolver-failure escalation path; current_agent is then
                "escalation").
        """
        if self.state.get("current_agent", "planner") != "planner":
            return "enter_main_loop"

        if self.state.get("current_phase", 0) == 0:
            gate_script = os.path.join(GATE_SCRIPTS_DIR, "phase_resolver.py")
            # F4 — set by the rc-1/unexpected ``else`` or the crash ``except``; a
            # non-None value triggers the shared escalation block after the try.
            startup_resolver_reason = None
            try:
                result = subprocess.run([sys.executable, gate_script], capture_output=True, text=True, timeout=GATE_SUBPROCESS_TIMEOUT)
                output = result.stdout.strip()
                if result.returncode == 0 and "PENDING: Phase" in output:
                    cp_path = os.path.join(PROJECT_ARTIFACTS_DIR, "current_phase.json")
                    if os.path.exists(cp_path):
                        with open(cp_path) as f:
                            first_phase = json.load(f)
                        # T4.3 (mirror of the advance-path guard) — a valid-but-
                        # shapeless current_phase.json (no raw_id) would advance blind
                        # to phase 0 / a "phase/" branch with colliding session keys.
                        # Treat it as an unactionable verdict and route to the shared
                        # F4 escalation below. (A *corrupt* file is already caught by
                        # this method's broad `except` and routed to the same place.)
                        if not isinstance(first_phase, dict) or not first_phase.get("raw_id"):
                            startup_resolver_reason = (
                                f"Startup current_phase.json is shapeless "
                                f"(no raw_id): {first_phase!r}"
                            )
                        else:
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
                else:
                    # F4 — resolver produced no actionable verdict (rc 1: roadmap
                    # not found / non-absolute path / write failure; or an
                    # unexpected rc/stdout). Record the reason and fall to the
                    # escalation block below instead of proceeding to a blind
                    # planner run with an empty raw_id and no current_phase.json.
                    startup_resolver_reason = (
                        f"Startup phase_resolver produced no actionable verdict: "
                        f"rc={result.returncode} output={output!r} "
                        f"stderr={(result.stderr or '')[-500:]!r}"
                    )
            except Exception as startup_err:
                # F4 (B2) — the resolver subprocess crashed or hit GATE_SUBPROCESS_TIMEOUT.
                # Escalate too: proceeding here would invoke the planner blind (empty
                # raw_id, no current_phase.json), the same dead condition one layer over.
                startup_resolver_reason = f"Startup phase_resolver crashed: {startup_err}"

            if startup_resolver_reason is not None:
                # F4 — escalate via the established idiom: record an honest reason,
                # route current_agent to escalation, transition RUNNING, and return
                # "enter_main_loop" so the main-loop escalation dispatch fires the
                # webhook + advisory + queue-park. Do NOT fall through to the
                # branch-checkout / blind-planner path below.
                print(f"[ERROR] {startup_resolver_reason} — routing to escalation.", file=sys.stderr)
                _ps = self.read_phase_state()
                _ps["last_error_code"] = ERR_PHASE_RESOLVER_FAILED
                _ps["escalation_trigger_reason"] = startup_resolver_reason
                self.write_phase_state_atomic(_ps)
                self.state["current_agent"] = "escalation"
                self.transition_state("RUNNING", startup_resolver_reason)
                return "enter_main_loop"

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
            self._checkout_or_create_branch(branch, check=False)
            print(f"[INFO] Startup: checked out branch {branch} for phase {_startup_raw or _startup_num}.")

            import glob as _startup_glob

            _startup_gate = os.path.join(GATE_SCRIPTS_DIR, "phase_resolver.py")
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

    def _run_startup_loop(self) -> str:
        """Run :meth:`_run_startup_planner_phase_zero_and_branch` to a settled
        verdict, honoring its ``"retry_startup"`` re-entry (the startup fn emits it
        when it detects PIPELINE_COMPLETE and auto-advances the queue to a fresh
        project — that new project must then itself be resolved + branched).

        This is the single canonical "bring the current project up from a blank
        phase-0/planner state to a resolved phase + ``phase/<raw_id>`` branch +
        captured ``phase_base_commit``" routine. It is called by :meth:`run` once
        at launch **and** by :meth:`_advance_to_next_pending_phase` (PIPELINE_COMPLETE
        / BLOCKED arms) and the main-loop escalation-park advance after an
        **in-process** queue auto-advance. That re-use closes the Phase-8 gap where
        an in-process advance re-entered the main loop with NO startup init, so the
        newly-activated project's planner ran at ``Phase=NONE``/empty ``raw_id`` and
        the executor hit a permanent ``ERR_MISSING_BASE_COMMIT`` (no
        ``phase_base_commit`` was ever captured). It is a safe no-op for a revival
        activation (``current_agent="escalation"``), on which the startup fn
        early-returns ``"enter_main_loop"`` without touching state.

        Returns:
            "exit_run" — the orchestrator should stop (startup said so, or the
                20-pass queue-advance cap was hit).
            "enter_main_loop" — startup settled; proceed to / re-enter the main loop.
        """
        _startup_pass = 0
        while _startup_pass < 20:
            _startup_pass += 1
            _startup_rv = self._run_startup_planner_phase_zero_and_branch()
            if _startup_rv == "exit_run":
                return "exit_run"
            if _startup_rv == "retry_startup":
                continue
            return "enter_main_loop"
        print("[ERROR] Startup exceeded max iterations (queue advance loop); exiting.")
        return "exit_run"

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
                self.state["escalation_trigger_class"] = "repo_init_failed"  # P1-B
                self.transition_state("RUNNING", failure_context)
                phase = self.state.get("current_phase", 0)
                raw_id = self.state.get("current_phase_raw_id", "unknown")
                session_key = f"pipeline:phase-{phase}:{raw_id}:repo-init-failure"
                token = self.openclaw_config.get("hooks", {}).get("token", "")
                if os.path.exists(SYMLINK_TARGET):
                    cleanup_output_files(PROJECT_ARTIFACTS_DIR, "escalation")
                    # Staleness guard: never promote a summary left by a prior
                    # escalation (or project) as this one's advisory.
                    self._clear_stale_escalation_summary()
                _ps = self.read_phase_state()
                _ps["escalation_trigger_reason"] = failure_context
                # P1 Stage G1: repo-init failures are pre-phase; give the UI a clean,
                # operator-facing headline. The raw reason stays in the details disclosure.
                _ps["escalation_headline"] = "Repository setup needs your attention"
                _ps["escalations"] = _ps.get("escalations", 0) + 1  # W1-B
                _ps["last_phase_outcome"] = "escalated"  # Phase 3 — terminal outcome
                _ps["waiting_for_human_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # W1-E
                # P1-B — resolve + persist the structured trigger class onto _ps before
                # _record_escalation_reason persists phase_state; returns the event detail.
                _esc_detail = self._prepare_escalation_trigger(_ps)

                # Record the honest deterministic reason BEFORE transitioning, so
                # the escalation panel shows a factual message the instant it
                # renders; the escalation agent's own summary upgrades it to
                # "ready" when escalation_summary.json lands.
                self._record_escalation_reason(_ps)

                _write_pipeline_event("escalation_trigger", raw_id, "escalation", _esc_detail)  # W1-F
                self.transition_state("WAITING_FOR_HUMAN", "Invoking Escalation Agent: repo init check failed")
                self._queue_park_active_entry("ESCALATION", "escalation")

                # Note: park-and-advance is not applied here — the next queued project must pass
                # repo init on a fresh orchestrator run; advancing without re-check would be unsafe.
                self._preset_session_response_usage("escalation", session_key)
                webhook_status = invoke_agent_webhook(
                    "escalation", session_key, token,
                    message=self._build_escalation_webhook_message(),
                    url=self.openclaw_config.get("hooks_url"),
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
                    _write_escalation_failed_atomic(fallback_dir, error_data)
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

            # --- Startup Phase Identification + branch checkout. The same routine
            #     is re-run after an in-process queue auto-advance (via
            #     _run_startup_loop, called from _advance_to_next_pending_phase and
            #     the main-loop escalation-park advance) so a queued project resolves
            #     its real phase + phase_base_commit before any agent is dispatched. ---
            if self._run_startup_loop() == "exit_run":
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

                    session_key = f"pipeline:phase-{phase}:{raw_id}:planner-attempt-{retries + 1}" + self._provider_retry_suffix()
                    self._record_active_agent("planner", session_key)  # Phase 9 — abort-on-escalation target
                    sentinel_path = os.path.join(PROJECT_ARTIFACTS_DIR, "planner_output.done")
                    token = self.openclaw_config.get("hooks", {}).get("token", "")

                    _attempt_start_time = time.time()  # captured before cleanup for stale-sentinel guard
                    cleanup_output_files(PROJECT_ARTIFACTS_DIR, "planner")
                    self.skill_manager.inject_skill(
                        self.state.get("current_phase_raw_id", ""), "planner", self.openclaw_config
                    )
                    self._record_injected_skill("planner")
                    _stamp_ok = self._init_activity_stamp_or_escalate("planner")
                    if not _stamp_ok:
                        # Workspace unwritable — helper routed to escalation;
                        # let the loop fire the escalation dispatch next iteration.
                        continue

                    self.state["sentinel_wait_started_at"] = datetime.now(timezone.utc).isoformat()
                    self.transition_state("WAITING_FOR_SENTINEL", "Invoking Planner via webhook")
                    webhook_status = invoke_agent_webhook(
                        "planner", session_key, token,
                        url=self.openclaw_config.get("hooks_url"),
                    )

                    if webhook_status != "SUCCESS":
                        self.state["current_agent"] = "escalation"
                        self.state["escalation_trigger_class"] = "webhook_failure"  # P1-B
                        error_reason = webhook_failure_reason(webhook_status)
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
                    _planner_backstop = _infra_backstop_seconds("AUTODEV_INFRA_BACKSTOP_PLANNER", "4500")  # gateway-dead failsafe; env-tunable for slow local-model hosts
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
                        sentinel_acceptor=self._make_verdict_hold_acceptor(
                            "planner", session_key, _attempt_start_time
                        ),
                        loop_detector=self._maybe_tool_loop_detector(
                            "planner", session_key, "TOOL_LOOP_REPEAT_LIMIT_PLANNER", "8"
                        ),
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
                    if getattr(sentinel_found, "reason", None) == "tool_loop":
                        self._note_tool_loop(agent_role="planner", raw_id=raw_id)
                    if getattr(sentinel_found, "reason", None) in (
                        "stalled",
                        "no_first_activity",
                        "timeout",
                        "tool_loop",
                    ):
                        if not self._handle_stall_outcome(
                            agent_role="planner",
                            session_key=session_key,
                            stamp_path=_planner_stamp,
                            reason=sentinel_found.reason,
                        ):
                            return

                    if getattr(sentinel_found, "reason", None) == "stopped":
                        # Operator stop: the stop sentinel is still on disk; let the
                        # loop-top _check_stop_requested() halt cleanly. Do not misread
                        # it as a sentinel timeout and burn an agent retry.
                        continue

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
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError):
                        # best-effort: a missing file, bad JSON, or an odd-shaped session entry
                        # (a null/non-dict value → .get on None) leaves a None jsonl_path, which
                        # _accumulate_role_tokens handles (latches token_capture_degraded + emits
                        # token_capture_warning). It must NOT escape to the escalation path.
                        pass
                    self._accumulate_role_tokens("planner", _planner_jsonl_path)

                    if self._escalate_if_provider_rejected(_planner_jsonl_path, "Planner"):
                        time.sleep(5)
                        continue

                    if not sentinel_found:
                        if self._escalate_if_provider_rejected(_planner_jsonl_path, "Planner"):
                            time.sleep(5)
                            continue
                        if getattr(sentinel_found, "reason", None) == "tool_loop":
                            print("[TOOL_LOOP] planner poll ended in a detected tool-loop — retrying as self-failure")
                        else:
                            print("[ERROR] Sentinel timeout")
                        retries = self.increment_planner_retries()
                    else:
                        gate_passed = self.run_planner_output_gate()
                        if gate_passed:
                            # Drain an optional planner scope_warning into a
                            # scope_warning event + last_scope_warning stash (the
                            # read-side consumer for the descope signal; surfaces
                            # in the canonical metrics row). Mirrors
                            # _emit_gate_warnings on the executor-PASS path. Best-
                            # effort: never blocks the PASS.
                            self._emit_scope_warning(raw_id)
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
                                    # P1-B — uniform retry-source label across all three
                                    # gate_fail emits; the planner has a single retry source.
                                    "retry_class": "planner_retry",
                                },
                            )  # W1-F
                            self.write_failure_context("planner", self.state.get("planner_retries", 0) + 1)
                            retries = self.increment_planner_retries()
                            
                    if retries >= 3:
                        self.state["current_agent"] = "escalation"
                        self.state["escalation_trigger_class"] = "planner_retries_exhausted"  # P1-B
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
                        # EX-RR: Before escalating, check whether a valid executor
                        # output arrived on disk from an orphaned background session that
                        # completed AFTER the orchestrator's sentinel poll ended.  If the
                        # gate passes, advance directly to reviewer so the successful work
                        # is not discarded.  executor_retries is reset to 0 to prevent a
                        # fresh restart from immediately re-entering this exhausted block.
                        _ex_rr_sentinel = os.path.join(PROJECT_ARTIFACTS_DIR, "executor_output.done")
                        _ex_rr_json = os.path.join(PROJECT_ARTIFACTS_DIR, "executor_output.json")
                        if os.path.exists(_ex_rr_sentinel) and os.path.exists(_ex_rr_json):
                            print("[INFO] [EX-RR] Surviving executor output found — running gate before escalating.")
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
                            print("[INFO] [EX-RR] Gate failed on surviving output — proceeding to escalation.")
                        # Exhaustion escalates directly, mirroring the planner-exhaustion
                        # pattern. The transition action string carries the last gate
                        # error code — the escalation dispatch copies it into
                        # escalation_trigger_reason, so the operator sees the honest
                        # deterministic signal instead of a coarse attribution label.
                        # Fresh executor budgets come only from reviewer rejections or
                        # the operator's RESET_EXECUTION (which resets the counter).
                        print("[INFO] Executor retries exhausted. Escalating.")
                        self.write_failure_context("executor", self.state.get("executor_retries", 0))
                        _last_err = ""
                        try:
                            _last_err = str(self.read_phase_state().get("last_error_code") or "")
                        except RuntimeError:
                            pass  # best-effort: a corrupt phase_state must not crash escalation
                        self.state["current_agent"] = "escalation"
                        self.state["escalation_trigger_class"] = "executor_retries_exhausted"  # P1-B
                        self.transition_state(
                            "RUNNING",
                            f"Executor retries exhausted after {retries} attempts"
                            + (f" (last error: {_last_err})" if _last_err else ""),
                        )
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

                    session_key = f"pipeline:phase-{phase}:{raw_id}:executor-attempt-{retries + 1}" + self._provider_retry_suffix()
                    self._record_active_agent("executor", session_key)  # Phase 9 — abort-on-escalation target
                    attempt_label = "Cloud"

                    sentinel_path = os.path.join(PROJECT_ARTIFACTS_DIR, "executor_output.done")
                    token = self.openclaw_config.get("hooks", {}).get("token", "")

                    # Interrupt the previous executor session before launching the next attempt,
                    # so the prior run can't keep consuming tokens, refreshing
                    # executor_activity.stamp, or writing the shared executor_output.* files.
                    # Routed through the consolidated _interrupt_agent_session helper: it SKIPS
                    # the steer when the prior attempt has already finished (skip_if_idle — no
                    # gratuitous "stop" turn on a done agent, which OpenClaw's interrupt+inject
                    # steer would otherwise spawn) and otherwise steers + waits for the stamp to
                    # settle before we proceed (the old single-shot verify false-failed on the
                    # steer's own follow-up turn). Best-effort + soft-continue.
                    if retries > 0:
                        self._interrupt_agent_session(
                            role="executor",
                            session_key=(
                                f"pipeline:phase-{phase}:{raw_id}:executor-attempt-{retries}"
                            ),
                            stamp_path=os.path.join(
                                PROJECT_ARTIFACTS_DIR, "executor_activity.stamp"
                            ),
                            source="retry_start",
                            skip_if_idle=True,
                            prior_attempt=retries,
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
                    _stamp_ok = self._init_activity_stamp_or_escalate("executor")
                    if not _stamp_ok:
                        # Workspace unwritable — helper routed to escalation;
                        # let the loop fire the escalation dispatch next iteration.
                        continue
                    self.state["sentinel_wait_started_at"] = datetime.now(timezone.utc).isoformat()
                    self.transition_state("WAITING_FOR_SENTINEL", f"Invoking Executor ({attempt_label}) - Attempt {retries + 1}")

                    _verify_symlinks_consistent(
                        self.state.get("project_path", ""), self.update_symlink
                    )
                    # _invoke_executor delivers (and clears) any one-shot
                    # executor_retry_directive as the webhook message; otherwise the
                    # executor's default message applies.
                    webhook_status = self._invoke_executor(session_key, token)

                    if webhook_status != "SUCCESS":
                        self.state["current_agent"] = "escalation"
                        self.state["escalation_trigger_class"] = "webhook_failure"  # P1-B
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
                    _executor_backstop = _infra_backstop_seconds("AUTODEV_INFRA_BACKSTOP_EXECUTOR", "4500")  # gateway-dead failsafe; env-tunable for slow local-model hosts
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
                        sentinel_acceptor=self._make_verdict_hold_acceptor(
                            "executor", session_key, _attempt_start_time
                        ),
                        loop_detector=self._maybe_tool_loop_detector(
                            "executor", session_key, "TOOL_LOOP_REPEAT_LIMIT_EXECUTOR", "15"
                        ),
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
                    if getattr(sentinel_found, "reason", None) == "tool_loop":
                        self._note_tool_loop(agent_role="executor", raw_id=raw_id)
                    if getattr(sentinel_found, "reason", None) in (
                        "stalled",
                        "no_first_activity",
                        "timeout",
                        "tool_loop",
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
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError):
                        # best-effort: missing/bad/odd-shaped session entry → None jsonl_path →
                        # token_capture_warning downstream; must not escape to escalation.
                        pass

                    # Dead-on-arrival check: sessions.json is reliably populated by the time
                    # agent_end (or the hard timeout) unblocks the sentinel poll.
                    _is_dead, _dead_msg = _check_session_dead_on_arrival(_sessions_json, _full_key)
                    if _is_dead:
                        print(f"[ERROR] [EXECUTOR] Session dead on arrival: {_dead_msg}")
                        _ps_dead = self.read_phase_state()
                        _ps_dead["last_error_code"] = ERR_SESSION_DEAD_ON_ARRIVAL
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
                    self._accumulate_role_tokens("executor", _jsonl_path)

                    # RR-3 (Phase 3): Classify executor terminal state before deciding action.
                    # executor_output_path is .json counterpart to sentinel_path (.done).
                    executor_output_path = os.path.join(PROJECT_ARTIFACTS_DIR, "executor_output.json")
                    outcome = self.classify_executor_outcome(sentinel_found, executor_output_path)
                    # A detected tool_loop is an agent malfunction, not an external preemption.
                    # The executor writes executor_output.json (step 11) BEFORE the archive /
                    # metrics / .done finalization steps, so a loop in that window leaves
                    # executor_output.json on disk and classify_executor_outcome would label it
                    # "executor_preempted": on a failing gate that escalates WITHOUT consuming a
                    # retry, and on a passing gate it advances incomplete work to the reviewer.
                    # Force the self-failure path so the loop gets the documented fresh-session
                    # retry (reset_execution("auto")), exactly like the no-output crash case.
                    if getattr(sentinel_found, "reason", None) == "tool_loop":
                        outcome = "executor_crashed"
                    print(f"[INFO] [EXECUTOR] Outcome classified: {outcome}")

                    if outcome == "executor_succeeded":
                        gate_passed = self.run_executor_output_gate()
                        if gate_passed:
                            # P1 Stage F — advisory; never affects gate verdict.
                            # Drains executor_advisory_detail.json into pipeline
                            # events so the UI shows reachability findings.
                            self._emit_reachability_advisory(raw_id)
                            # Phase 3 — drain gate_warnings.json into a gate_warning
                            # event + phase_state stash. Preserves the file (the
                            # reviewer reads it next); clears a stale stash on a
                            # clean pass. Never affects the gate verdict.
                            self._emit_gate_warnings(raw_id)
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
                            self.state["escalation_trigger_class"] = "preempted_output_invalid"  # P1-B
                            self.transition_state(
                                "RUNNING",
                                "EXECUTOR_PREEMPTED_OUTPUT_INVALID: escalating without consuming executor_retries",
                            )

                    else:  # executor_crashed
                        if getattr(sentinel_found, "reason", None) == "tool_loop":
                            print("[TOOL_LOOP] executor poll ended in a detected tool-loop — retrying as self-failure (executor_crashed path)")
                        else:
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
                    # Neither a CONTRACT_FAILURE soft-retry nor a contract-shape
                    # *_UNVERIFIED retry bumps reviewer_retries, so mix BOTH counters
                    # into the key for a fresh, distinct session per retry of either
                    # kind (see _reviewer_session_key).
                    _ps_rk = self.read_phase_state()
                    _contract_retries = _ps_rk.get("reviewer_contract_retries", 0)
                    _unverified_retries = _ps_rk.get("reviewer_unverified_retries", 0)
                    session_key = self._reviewer_session_key(
                        phase, raw_id, retries, _contract_retries, _unverified_retries
                    ) + self._provider_retry_suffix()
                    self._record_active_agent("reviewer", session_key)  # Phase 9 — abort-on-escalation target

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
                        _stamp_ok = self._init_activity_stamp_or_escalate("reviewer")
                        if not _stamp_ok:
                            # Workspace unwritable — helper routed to escalation;
                            # let the loop fire the escalation dispatch next iteration.
                            continue
                        self.state["sentinel_wait_started_at"] = datetime.now(timezone.utc).isoformat()
                        self.transition_state("WAITING_FOR_SENTINEL", f"Invoking Reviewer - Attempt {retries + 1}")

                        _verify_symlinks_consistent(
                            self.state.get("project_path", ""), self.update_symlink
                        )
                        # _invoke_reviewer delivers (and clears) any one-shot
                        # reviewer_retry_directive as the webhook message; otherwise the
                        # reviewer's default message applies.
                        webhook_status = self._invoke_reviewer(session_key, token)

                        if webhook_status != "SUCCESS":
                            self.state["current_agent"] = "escalation"
                            self.state["escalation_trigger_class"] = "webhook_failure"  # P1-B
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
                        _reviewer_backstop = _infra_backstop_seconds("AUTODEV_INFRA_BACKSTOP_REVIEWER", "4500")  # gateway-dead failsafe; env-tunable for slow local-model hosts
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
                            sentinel_acceptor=self._make_verdict_hold_acceptor(
                                "reviewer", session_key, _attempt_start_time
                            ),
                            loop_detector=self._maybe_tool_loop_detector(
                                "reviewer", session_key, "TOOL_LOOP_REPEAT_LIMIT_REVIEWER", "8"
                            ),
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
                        if getattr(sentinel_found, "reason", None) == "tool_loop":
                            self._note_tool_loop(agent_role="reviewer", raw_id=raw_id)
                        if getattr(sentinel_found, "reason", None) in (
                            "stalled",
                            "no_first_activity",
                            "timeout",
                            "tool_loop",
                        ):
                            if not self._handle_stall_outcome(
                                agent_role="reviewer",
                                session_key=session_key,
                                stamp_path=_reviewer_stamp,
                                reason=sentinel_found.reason,
                            ):
                                return

                    if getattr(sentinel_found, "reason", None) == "stopped":
                        # Operator stop: the stop sentinel is still on disk; let the
                        # loop-top _check_stop_requested() halt cleanly. Do not misread
                        # it as a sentinel timeout and burn an agent retry.
                        continue

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
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError, TypeError):
                        # best-effort: missing/bad/odd-shaped session entry → None jsonl_path →
                        # token_capture_warning downstream; must not escape to escalation.
                        pass

                    # Dead-on-arrival check runs after sentinel poll (sessions.json guaranteed
                    # populated by the time agent_end or the hard timeout fires).
                    _is_dead, _dead_msg = _check_session_dead_on_arrival(_rev_sessions_json, _rev_full_key)
                    if _is_dead:
                        print(f"[ERROR] [REVIEWER] Session dead on arrival: {_dead_msg}")
                        _ps_dead = self.read_phase_state()
                        _ps_dead["last_error_code"] = ERR_SESSION_DEAD_ON_ARRIVAL
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

                    # Accumulate reviewer token usage across reviewer re-invocations
                    # (T4.8 — was a bare assignment that overwrote prior totals).
                    self._accumulate_role_tokens("reviewer", _jsonl_path)

                    if self._escalate_if_provider_rejected(_jsonl_path, "Reviewer"):
                        time.sleep(5)
                        continue

                    if not sentinel_found:
                        if self._escalate_if_provider_rejected(_jsonl_path, "Reviewer"):
                            time.sleep(5)
                            continue
                        if getattr(sentinel_found, "reason", None) == "tool_loop":
                            print("[TOOL_LOOP] reviewer poll ended in a detected tool-loop — retrying as self-failure")
                        else:
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
                            self.state["escalation_trigger_class"] = "reviewer_retries_exhausted"  # P1-B
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
                        "CONTRACT_FAILURE": "reviewer",
                        "VISUAL_UNVERIFIED": "reviewer",
                        "BEHAVIORAL_UNVERIFIED": "reviewer",
                        "REGRESSION_UNVERIFIED": "reviewer",
                    }.get(gate_result, "escalation")  # F10(b): unknown verdict now escalates (was a silent halt)
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
                        # ROUTE_ESCALATE, MISSING_ARTIFACTS, CONTRACT_FAILURE,
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
                        _marker_id = _raw_id or phase
                        # T6.4 — crash-window idempotency. A kill after the merge commit lands but
                        # before the phase advances re-enters here (status RUNNING + reviewer →
                        # reviewer re-PASSes). The durable phase_merged marker (written below only
                        # after a confirmed rc-0 merge, self-cleared when phase_state is deleted on
                        # advance/reset) lets us skip the already-done merge + roadmap flip. The read
                        # is wrapped because read_phase_state raises on a corrupt file — degrade to
                        # "marker absent" and fall through to the merge-base --is-ancestor backstop.
                        try:
                            _ps_guard = self.read_phase_state()
                        except Exception:
                            _ps_guard = {}
                        already_merged_marker = (_ps_guard.get("phase_merged") == _marker_id)
                        _persisted_base = _ps_guard.get("merge_base_branch")
                        try:
                            configured_base_branch = self.openclaw_config.get("pipeline", {}).get("base_branch", "").strip()
                            if already_merged_marker and _persisted_base:
                                # Re-entry: use the base the merge actually landed on (guards against
                                # base-branch auto-detect drift between the original run and restart).
                                base_branch = _persisted_base
                            else:
                                base_branch = configured_base_branch if configured_base_branch else _detect_base_branch(SYMLINK_TARGET)

                            if already_merged_marker:
                                # The merge for this phase already committed in a prior run. Re-assert
                                # HEAD on base for the tag + advance below; do NOT re-stage / re-merge
                                # or re-run the roadmap flip (already folded into the merge commit).
                                subprocess.run(["git", "checkout", base_branch], cwd=SYMLINK_TARGET, check=False)
                                merge_result = subprocess.CompletedProcess(
                                    args=["git", "merge", branch], returncode=0, stdout=b"", stderr=b"")
                            else:
                                # Hard guard: ensure HEAD is on the phase branch before staging.
                                # This is the authoritative check — commits MUST land on branch,
                                # not on base. If correction fails, escalate rather than corrupt
                                # the repository topology.
                                if not self._ensure_phase_branch(branch):
                                    print(f"[ERROR] Phase {phase}: cannot ensure branch '{branch}' before commit — escalating.")
                                    self.state["current_agent"] = "escalation"
                                    self.state["escalation_trigger_class"] = "git_op_failed"  # P1-B
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

                                # T6.4 — idempotent-merge backstop. If branch is already an ancestor of
                                # base (the merge landed but the marker didn't persist — crash between
                                # the merge and the marker write), treat it as success instead of
                                # re-running git merge. A re-merge would FAIL when _ensure_phase_branch
                                # recreated an empty phase branch (the branch-recreated-empty sub-case)
                                # → a false ERR_MERGE_FAILED on already-complete work.
                                _already_ancestor = subprocess.run(
                                    ["git", "merge-base", "--is-ancestor", branch, base_branch],
                                    cwd=SYMLINK_TARGET, capture_output=True
                                ).returncode == 0
                                if _already_ancestor:
                                    merge_result = subprocess.CompletedProcess(
                                        args=["git", "merge", branch], returncode=0, stdout=b"", stderr=b"")
                                else:
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
                                    "last_error_code": ERR_MERGE_FAILED,
                                    "merge_failure_reason": _merge_reason,
                                    "merge_failure_branch": branch,
                                    "merge_failure_branch_exists": _branch_present,
                                    "merge_failure_last_good_commit": self.state.get("phase_base_commit", "unknown"),
                                    "merge_failure_head_commit": _head_sha,
                                })
                                self.write_phase_state_atomic(_ps_mf)

                                self.state["current_agent"] = "escalation"
                                self.state["escalation_trigger_class"] = "git_op_failed"  # P1-B
                                self.transition_state(
                                    "RUNNING",
                                    f"Phase {phase} merge failed: {_merge_reason}",
                                )
                                time.sleep(5)
                                continue

                            # T6.4 — durable marker: the merge for this phase has landed (rc 0).
                            # Record it (and the base it landed on) BEFORE the roadmap flip /
                            # advance, so a crash in this window re-enters via already_merged_marker
                            # instead of re-merging an empty branch → false ERR_MERGE_FAILED.
                            # phase_state.json is deleted on advance/reset, so the marker self-clears.
                            _ps_merged = self.read_phase_state()
                            _ps_merged["phase_merged"] = _marker_id
                            _ps_merged["merge_base_branch"] = base_branch
                            self.write_phase_state_atomic(_ps_merged)

                            # 2. Roadmap Update — fold into merge commit atomically (B5).
                            # Write [x] checkbox to roadmap.md in-place, then amend the merge
                            # commit so the checkbox is part of the merge, not a separate commit.
                            # This prevents git checkout -b phase/NEXT from reverting the checkbox.
                            # Skipped on confirmed re-entry (already folded into the merge commit);
                            # the flip helper also self-skips its amend when the box is already [x].
                            if not already_merged_marker:
                                import glob, re
                                roadmap_path = None
                                for ext in ['*.md', '*.yaml', '*.json']:
                                    matches = glob.glob(os.path.join(SYMLINK_TARGET, f"*oadmap{ext}")) + glob.glob(os.path.join(SYMLINK_TARGET, f"*Roadmap{ext}"))
                                    if matches:
                                        roadmap_path = matches[0]
                                        break

                                if roadmap_path:
                                    # T4.4 — fail closed. On a non-git flip failure the
                                    # helper routes to escalation and returns False; we
                                    # must NOT fall through to tag + advance (that would
                                    # silently re-run the merged phase). A git failure
                                    # re-raises to the outer handler below.
                                    if not self._flip_roadmap_checkbox_or_escalate(roadmap_path, phase):
                                        time.sleep(5)
                                        continue

                            _tag_id = self.state.get("current_phase_raw_id", "") or phase
                            # Use --force so the tag moves to the new commit on phase re-runs
                            # rather than failing with exit 128 when the tag already exists.
                            subprocess.run(["git", "tag", "--force", f"phase-{str(_tag_id).lower()}-complete"], cwd=SYMLINK_TARGET, check=False)
                        except subprocess.CalledProcessError as e:
                            print(f"[ERROR] Git operation failed: {e}")
                            self.state["current_agent"] = "escalation"
                            self.state["escalation_trigger_class"] = "git_op_failed"  # P1-B
                            self.transition_state("RUNNING", f"Git operation failed on Phase {phase}: {str(e)}")
                            time.sleep(5)
                            continue
                                
                        # 3. Suggestions Append — skipped on confirmed re-entry: the append is NOT
                        # idempotent (it would duplicate the "## Phase N Suggestions" block on every
                        # restart), and the suggestions were already recorded on the first pass (B6).
                        if not already_merged_marker:
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
                            
                        # 4. Identify and advance to the next pending phase
                        #    (shared with SKIP / PROCEED via
                        #    _advance_to_next_pending_phase so the three sites cannot drift — F3).
                        _sig = self._advance_to_next_pending_phase(trigger="phase_complete")
                        if _sig == "continue":
                            continue
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
                        self.state["escalation_trigger_class"] = "reviewer_routed"  # P1-B
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
                            self.state["escalation_trigger_class"] = "reviewer_verification_unmet"  # P1-B
                            self.transition_state(
                                "RUNNING",
                                f"Reviewer MISSING_ARTIFACTS: artifact retry cap reached ({_ma_retries})",
                            )
                        else:
                            # Re-invoke executor; the directive is delivered as the
                            # webhook message= by _invoke_executor (one-shot). It must be
                            # self-contained because message= replaces the executor's
                            # default prompt — so it re-asserts that prior work is
                            # preserved and points the fresh session at what to record.
                            _raw_id = self.state.get("current_phase_raw_id", "this phase")
                            _executor_directive = (
                                f"MISSING COMPLETION ARTIFACTS: Your implementation for this phase is already "
                                f"complete and PRESERVED on the branch — do NOT re-implement or rebuild it. This "
                                f"is a fresh session re-invoked solely to add the two completion artifacts the "
                                f"reviewer found missing. Read current_phase.json and your existing work on the "
                                f"branch for what to record, then, before writing executor_output.done, you MUST "
                                f"produce both: "
                                f"(1) Write the phase archive to .autodev/pipeline/phases/{_raw_id}.md using the "
                                f"format in your AGENTS.md. "
                                f"(2) Append a metrics row to .autodev/pipeline/metrics.jsonl using the format in "
                                f"your AGENTS.md. Write the archive first, metrics second, sentinel last."
                            )
                            _ps_ma["executor_retry_directive"] = _executor_directive
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

                    elif gate_result == "CONTRACT_FAILURE":
                        # Contract failure: the reviewer session ended without a parseable
                        # reviewer_output.json (the agent_end backstop wrote .done on a
                        # give-up OR an abort/crash). NOT a code-quality rejection, so
                        # reviewer_retries is untouched. Genuine transport/provider
                        # failures are peeled off upstream (stall / dead-on-arrival /
                        # provider-rejected); a cheap defensive re-check here catches the
                        # recognizable-provider-error subset before we treat this as a
                        # contract breach. Otherwise self-heal: re-invoke the reviewer in a
                        # FRESH session with a self-contained corrective directive (cap
                        # reviewer_contract_retries). Recovery is identical for give-up vs
                        # abort, so the cause is diagnostic-not-routing — no model-health
                        # probe (agent/model liveness is owned by the activity-stamp hooks).
                        if self._escalate_if_provider_rejected(_jsonl_path, "Reviewer"):
                            time.sleep(5)
                            continue
                        _ps_if = self.read_phase_state()
                        # No problem-list to surface (the reviewer wrote nothing
                        # parseable); drop any stale *_UNVERIFIED detail so it cannot
                        # bleed into an unrelated contract directive.
                        _ps_if.pop("reviewer_unverified_detail", None)
                        _contract_soft = _ps_if.get("reviewer_contract_retries", 0) + 1
                        _ps_if["reviewer_contract_retries"] = _contract_soft
                        if _contract_soft < 3:
                            # One-shot directive delivered to the fresh reviewer session by
                            # _invoke_reviewer as the webhook message (overrides the default).
                            # Self-contained: it must re-assert the inputs to read AND the
                            # output contract, because it replaces the default message.
                            _ps_if["reviewer_retry_directive"] = (
                                "Your previous reviewer session ended without a parseable "
                                "reviewer_output.json (none written, or missing/malformed). "
                                "This is a fresh session. Read your standard inputs — "
                                "pipeline-project/prd.md, pipeline-project/verification.md, and "
                                "current_phase.json — review the phase, then emit a complete "
                                "reviewer_output.json, and only then write reviewer_output.done "
                                "(JSON first, sentinel second)."
                            )
                        self.write_phase_state_atomic(_ps_if)
                        print(f"[WARN] Reviewer CONTRACT_FAILURE — contract retry {_contract_soft}/3.")
                        if _contract_soft >= 3:
                            # Honest attribution: a CONTRACT_FAILURE whose underlying
                            # cause is a reviewer MODEL hard-error (stopReason:"error" /
                            # 500 — GPU contention, model eviction) is infrastructure,
                            # not a give-up. The composer reads the session's last error
                            # row and, in that case, upgrades last_error_code to
                            # ERR_REVIEWER_MODEL_ERROR and surfaces the real inference
                            # error. Retry behaviour above is untouched — only this
                            # terminal escalation label changes.
                            _reason, _err_code = _compose_contract_failure_escalation(
                                _jsonl_path, _contract_soft
                            )
                            if _err_code != ERR_REVIEWER_CONTRACT_FAILURE:
                                _ps_err = self.read_phase_state()
                                _ps_err["last_error_code"] = _err_code
                                self.write_phase_state_atomic(_ps_err)
                            self.state["current_agent"] = "escalation"
                            self.state["escalation_trigger_class"] = "reviewer_verification_unmet"  # P1-B
                            self.transition_state("RUNNING", _reason)
                            time.sleep(5)
                        else:
                            # Kill the prior reviewer run before re-invoking. A
                            # CONTRACT_FAILURE often means the reviewer hard-errored or
                            # was cut off — its embedded run may still be streaming, and
                            # re-invoking (even on a fresh -c{N} key) on top of it spawns
                            # a concurrent zombie. Now liveness-gated (skip_if_idle via
                            # _interrupt_agent_session): it steers only when the prior reviewer
                            # is genuinely still streaming and is a clean no-op (skipped_idle)
                            # once that run has ended.
                            self._abort_active_agent_session("reviewer_retry")
                            self.state["current_agent"] = "reviewer"
                            self.transition_state(
                                "RUNNING",
                                f"Reviewer CONTRACT_FAILURE contract retry {_contract_soft} — re-invoking reviewer with corrective directive",
                            )
                            # REVIEWER-RELIABILITY backoff: on a shared local-model host a
                            # CONTRACT_FAILURE is usually a transient remote-inference error
                            # (llama-swap model eviction / GPU contention surfacing as
                            # stop=error with zero tokens), not a content problem — the fresh
                            # session above already drops the prior context. Re-firing after
                            # 5s tends to collide with the same contention; back off
                            # progressively (30s, then 60s) so the model server can recover
                            # before the next reviewer attempt. Cost-neutral; bounded by the
                            # cap above and the per-attempt infra backstop.
                            _contract_backoff = min(30 * _contract_soft, 90)
                            print(
                                f"[INFO] Reviewer CONTRACT_FAILURE — backing off "
                                f"{_contract_backoff}s before contract retry {_contract_soft + 1} "
                                f"(transient-inference recovery window)."
                            )
                            time.sleep(_contract_backoff)
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
                        #
                        # Phase 4: the verdict-specific instruction is written to the
                        # unified ``reviewer_retry_directive`` field and DELIVERED to the
                        # next reviewer session by ``_invoke_reviewer`` as the webhook
                        # message (overriding the default). The prior write-only field
                        # this replaced was never read or delivered — a dead write that
                        # left these retries blind.
                        #
                        # The directive is ENRICHED with the gate's specific problem list
                        # (stashed by reviewer_gate.py in ``reviewer_unverified_detail``)
                        # via ``_compose_unverified_directive`` so the reviewer is told
                        # exactly what failed. The detail is read-and-popped here (one-shot
                        # — it must not bleed into a later pass/phase; ``reset_phase``
                        # rebuilds the dict and ``reset_reviewer`` also clears it).
                        _ps_uv = self.read_phase_state()
                        _uv_detail = _ps_uv.pop("reviewer_unverified_detail", None)
                        _uv_retries = _ps_uv.get("reviewer_unverified_retries", 0) + 1
                        _ps_uv["reviewer_unverified_retries"] = _uv_retries
                        _ps_uv["reviewer_retry_directive"] = self._compose_unverified_directive(
                            gate_result, _uv_detail
                        )
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
                            self.state["escalation_trigger_class"] = "reviewer_verification_unmet"  # P1-B
                            self.transition_state(
                                "RUNNING",
                                f"Reviewer {gate_result}: contract-shape retry "
                                f"cap reached ({_uv_retries})",
                            )
                        else:
                            # Kill the prior reviewer run before re-invoking. The bumped
                            # reviewer_unverified_retries gives the next invocation a fresh
                            # -u{N} session key (see _reviewer_session_key), so the retry
                            # never reattaches a still-streaming embedded run or re-enters
                            # the rejected/overflowed prior context. The steer-abort
                            # additionally stops any zombie still streaming past .done so it
                            # can't keep writing the shared reviewer_output.* files. Now
                            # liveness-gated (skip_if_idle via _interrupt_agent_session): it
                            # steers only when the prior reviewer is genuinely still streaming
                            # and is a clean no-op (skipped_idle) once that run has ended —
                            # avoiding a gratuitous interrupt turn on a finished session.
                            self._abort_active_agent_session("reviewer_retry")
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
                        # original open-elif chain silently fell through here,
                        # leaving ``current_agent`` as "reviewer" — the next
                        # loop iteration re-invoked the reviewer in a fresh
                        # session (the CORE-E6 reviewer→reviewer loop symptom).
                        # Route to the escalation agent instead — the same idiom
                        # the CONTRACT_FAILURE and *_UNVERIFIED retry caps use
                        # above.  The next loop iteration fires the escalation
                        # dispatch, which reads this honest reason as
                        # escalation_trigger_reason and surfaces it to the
                        # operator (advisory + Signal notification via the
                        # escalation agent + a dashboard answer path).  The
                        # former HALTED_SILENT dead-end gave the operator no
                        # notification and no recovery short of git-recover.
                        print(
                            f"[WARN] Unknown reviewer-gate verdict "
                            f"{gate_result!r}.  Routing to escalation rather "
                            f"than silently re-invoking the reviewer."
                        )
                        self.state["current_agent"] = "escalation"
                        self.state["escalation_trigger_class"] = "gate_crash"  # P1-B
                        self.transition_state(
                            "RUNNING",
                            f"Unknown reviewer-gate verdict: {gate_result!r} — "
                            f"no handler; escalating for operator review",
                        )
                        time.sleep(5)
                        continue

                elif current_agent == "escalation":
                    if self._should_invoke_escalation_agent():
                        phase = self.state.get("current_phase", 0)
                        raw_id = self.state.get("current_phase_raw_id", "unknown")
                        session_key = f"pipeline:phase-{phase}:{raw_id}:escalation"
                        token = self.openclaw_config.get("hooks", {}).get("token", "")

                        # Phase 9 — the orchestrator is giving up on this phase; abort the
                        # last-invoked pipeline-agent session (the terminal attempt is never
                        # aborted by the retry-start path) so a zombie can't keep committing
                        # to the repo after hand-off. Runs once per escalation (this block is
                        # guarded by _should_invoke_escalation_agent(), which flips
                        # RUNNING->WAITING_FOR_HUMAN below). Best-effort, never blocks.
                        self._abort_active_agent_session("escalation")

                        cleanup_output_files(PROJECT_ARTIFACTS_DIR, "escalation")
                        # Staleness guard: a summary left by a previous escalation
                        # (or project) must never be promoted as this one's advisory.
                        self._clear_stale_escalation_summary()
                        _ps = self.read_phase_state()
                        _ps["escalation_trigger_reason"] = self.state.get("last_action", "escalation triggered")
                        # P1 Stage G1: persist a clean, operator-facing headline for the UI alongside
                        # the raw trigger reason (which is demoted into the details disclosure).
                        _ps["escalation_headline"] = self._clean_escalation_headline(raw_id)
                        _ps["escalations"] = _ps.get("escalations", 0) + 1  # W1-B
                        _ps["last_phase_outcome"] = "escalated"  # Phase 3 — terminal outcome
                        _ps["waiting_for_human_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # W1-E

                        # P1-B — resolve + persist the structured trigger class onto _ps
                        # BEFORE _record_escalation_reason writes phase_state (so the
                        # metrics row sees it); returns the enriched event detail.
                        _esc_detail = self._prepare_escalation_trigger(_ps)

                        # Record the honest deterministic reason BEFORE transitioning,
                        # so the escalation panel shows a factual message the instant it
                        # renders. The escalation agent composes the richer advisory
                        # itself (escalation-summary skill) and the WAITING_FOR_HUMAN
                        # poll loop promotes it to "ready" when it lands.
                        self._record_escalation_reason(_ps)

                        _write_pipeline_event("escalation_trigger", raw_id, "escalation", _esc_detail)  # W1-F
                        self.transition_state("WAITING_FOR_HUMAN", "Invoking Escalation Agent")
                        # B1 — generate the inbound-reply correlation token AFTER the transition
                        # but BEFORE the park: the queue entry is still ACTIVE (so the entry-id
                        # prefix is captured) and the symlink still points at this project (so the
                        # token persists into THIS project's phase_state.json). Threaded into the
                        # agent webhook message so the operator can reply on the channel.
                        _reply_token = self._prepare_escalation_reply_token()
                        self._queue_park_active_entry("ESCALATION", "escalation")

                        # Webhook message — framed as a TRUSTED control invocation and
                        # NOTIFY-only (F13): the agent composes + writes its advisory
                        # (escalation_summary.json) before notifying the operator, and
                        # must not write escalation_output — the operator answers from
                        # the dashboard.
                        self._preset_session_response_usage("escalation", session_key)
                        webhook_status = invoke_agent_webhook(
                            "escalation", session_key, token,
                            message=self._build_escalation_webhook_message(_reply_token),
                            url=self.openclaw_config.get("hooks_url"),
                        )

                        if webhook_status != "SUCCESS":
                            print("[ERROR] Escalation agent webhook failed. Attempting raw notification.")
                            raw_message = (
                                f"Pipeline failed at Phase {phase}. "
                                f"Last action: {self.state.get('last_action')}"
                            )
                            # Shared raw-POST helper (provider-agnostic): resolves the
                            # operator channel and returns False if it cannot be resolved
                            # OR the bounded (timeout=15) POST fails. Either way the
                            # escalation could not be delivered, so the pipeline halts with
                            # the unchanged terminal sequence below. An unresolved channel
                            # now halts honestly (with an escalation_failed breadcrumb)
                            # instead of POSTing to a guessed "signal" connector.
                            if not self._post_raw_notification(raw_message):
                                error_data = {
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                    "phase": phase,
                                    "gate": "escalation",
                                    "original_failure_reason": self.state.get("last_action")
                                }
                                _write_escalation_failed_atomic(PROJECT_ARTIFACTS_DIR, error_data)
                                _write_run_summary("HALTED_SILENT", "Escalation delivery failed")  # W2-B
                                self.transition_state("HALTED_SILENT", "Escalation delivery failed")
                                self._queue_update_active_entry(
                                    "FAILED",
                                    {"failed_at": datetime.now(timezone.utc).isoformat()},
                                )
                                break
                        if webhook_status == "SUCCESS":
                            # Hold the auto-advance for the in-flight agent's
                            # advisory: _select_next_queue_project repoints the
                            # pipeline-project symlink, and the agent writes
                            # escalation_summary.json through that symlink
                            # (OpenClaw write sandbox) — advancing now would send
                            # the write into the wrong project. The dispatch just
                            # cleared any stale summary, so any appearance is
                            # fresh (no mtime guard needed). Bounded + promoting;
                            # no-op outside auto-queue mode — see the helper.
                            self._wait_for_escalation_summary_before_advance()
                        if self._queue_after_park_maybe_advance():
                            # Phase-8 re-init: the auto-advance just activated a
                            # fresh-start project — resolve its phase + capture
                            # phase_base_commit before re-entering the main loop.
                            # exit_run (nothing left / 20-pass cap) → leave cleanly.
                            if self._run_startup_loop() == "exit_run":
                                break
                            continue
                    else:
                        # Promote the escalation agent's summary as soon as it lands —
                        # the dashboard upgrades from the deterministic fallback to the
                        # agent's advisory within one poll cycle, without waiting for
                        # the operator command. No-op once status is "ready".
                        self._promote_agent_escalation_summary()
                        out_path = self._poll_escalation_output_json_path(timeout_seconds=10)
                        if out_path:
                            try:
                                with open(out_path, "r") as f:
                                    cmd_data = json.load(f)
                                command = cmd_data.get("command", "").upper()
                            except Exception:
                                command = "STOP"

                            # Late-arriving agent summary: promote before recording
                            # the resolution (no-op when already promoted above).
                            self._promote_agent_escalation_summary()
                            _ps = self.read_phase_state()
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
                                self._restore_resume_target_agent()
                                self.transition_state("RUNNING", "RETRY: resuming in-flight agent")
                            elif command in ("RESET_PHASE", "RESTART PHASE"):
                                # RESTART PHASE is a legacy alias — remove once confirmed no
                                # in-flight Signal conversations still reference it.
                                # Both map to the same capped reset path.
                                self._queue_restore_parked_entry_to_active()
                                _ps = self.read_phase_state()
                                if _ps.get("escalation_resets", 0) >= 3:
                                    self.send_raw_notification(
                                        "Escalation reset cap reached (3). Human PROCEED required to advance past this phase."
                                    )
                                else:
                                    # T4.1 (Decision #4) — charge escalation_resets only on a
                                    # CONFIRMED reset. reset_phase() returns False + routes to
                                    # escalation on a git failure (tree intact), in which case
                                    # the budget is NOT charged. Capture the reason before the
                                    # reset (reset_phase clears last_error_code on success) and
                                    # increment after — reset_phase preserves escalation_resets
                                    # across its re-init.
                                    _reason = _ps.get("last_error_code", "unknown")
                                    if self.reset_phase():
                                        _ps = self.read_phase_state()
                                        _ps["escalation_resets"] = _ps.get("escalation_resets", 0) + 1
                                        _entry = {
                                            "reset_number": _ps["escalation_resets"],
                                            "command": "RESET_PHASE",
                                            "reason": _reason,
                                            "timestamp": datetime.now(timezone.utc).isoformat(),
                                        }
                                        _ps.setdefault("reset_log", []).append(_entry)
                                        self.write_phase_state_atomic(_ps)
                            elif command == "NUCLEAR_RESET":
                                # P1 Stage G2 — operator escape hatch, governed by its OWN
                                # nuclear_resets cap (2), independent of escalation_resets.
                                # Available precisely BECAUSE the escalation cap is spent;
                                # nuclear_reset_phase() does the increment + reset_log + the
                                # destructive reset_phase mechanics.
                                self._queue_restore_parked_entry_to_active()
                                _ps = self.read_phase_state()
                                if _ps.get("nuclear_resets", 0) >= 2:
                                    self.send_raw_notification(
                                        "Nuclear reset cap reached (2). Only Abandon Phase or Stop remain for this phase."
                                    )
                                else:
                                    self.nuclear_reset_phase()
                            elif command == "RESET_EXECUTION":
                                self._queue_restore_parked_entry_to_active()
                                _ps = self.read_phase_state()
                                if _ps.get("escalation_resets", 0) >= 3:
                                    self.send_raw_notification(
                                        "Escalation reset cap reached (3). Human PROCEED required to advance past this phase."
                                    )
                                else:
                                    # escalation_resets is incremented inside reset_execution("escalation")
                                    self.reset_execution(caller="escalation")
                            elif command == "RESET_REVIEWER":
                                self._queue_restore_parked_entry_to_active()
                                _ps = self.read_phase_state()
                                if _ps.get("escalation_resets", 0) >= 3:
                                    self.send_raw_notification(
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
                                # Re-resolve and advance via the shared helper so the
                                # planner targets the NEXT pending phase, not the just-
                                # skipped one (F3). continue → next phase; break → done.
                                _sig = self._advance_to_next_pending_phase(trigger="skip")
                                if _sig == "continue":
                                    continue
                                break
                            elif command == "PROCEED":
                                self._queue_restore_parked_entry_to_active()
                                _proc_raw = self.state.get("current_phase_raw_id", "") or str(self.state.get("current_phase", ""))
                                if _proc_raw:
                                    self._mark_roadmap_phase(_proc_raw, "x")
                                subprocess.run(["git", "tag", "--force", f"phase-{_proc_raw.lower()}-complete"], cwd=SYMLINK_TARGET, check=False)
                                # Re-resolve and advance via the shared helper so PROCEED
                                # genuinely moves PAST this phase (not a re-run that can loop
                                # back to escalation) (F3). continue → next phase; break → done.
                                _sig = self._advance_to_next_pending_phase(trigger="proceed")
                                if _sig == "continue":
                                    continue
                                break
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
                self._preset_session_response_usage("escalation", session_key)
                webhook_status = invoke_agent_webhook(
                    "escalation", session_key, token,
                    url=self.openclaw_config.get("hooks_url"),
                )
                if webhook_status == "SUCCESS":
                    _ps = self.read_phase_state()
                    _ps["escalation_trigger_reason"] = f"Escalated after unhandled exception: {exc_description}"
                    # P1 Stage G1: clean headline for the UI (raw reason stays in the disclosure).
                    _ps["escalation_headline"] = self._clean_escalation_headline(raw_id)
                    # Webhook already fired (default escalation message instructs the
                    # agent to compose + write its summary). Record the honest
                    # deterministic reason for the dashboard; the WAITING_FOR_HUMAN
                    # poll loop promotes the agent's summary when it lands.
                    self._record_escalation_reason(_ps)
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
                _write_escalation_failed_atomic(OPENCLAW_ROOT, error_data)
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

    ``run_started_at`` (3-A): a project switch stamps a fresh run-start; a
    same-project (re)start stamps fresh too UNLESS the on-disk status shows the
    run is still in flight (``_RESUMABLE_ACTIVE_RUN_STATUSES`` — a crash/restart
    resume), in which case the existing stamp is preserved. This is what gives the
    queue ``trigger-next`` path (which spawns ``--project-path`` on a finished
    project, hitting the same-project branch) a run-start marker for the staleness
    badge — without it, queue-launched runs left ``run_started_at`` null.

    ``run_id`` (P1-A) follows the exact same mint/preserve rule (the two travel
    together): a switch / re-run mints a fresh run identity while a crash-resume
    keeps the original, so events and metrics can be grouped per run.
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
            # A CLI project switch is a new run — stamp run_started_at + a fresh run_id (3-A / P1-A).
            "run_started_at": datetime.now(timezone.utc).isoformat(),
            "run_id": _new_run_id(),
            "pipeline_status": "RUNNING",
            "project_path": new_target,
        }
    else:
        if disk_state:
            orchestrator.state = disk_state
        orchestrator.state["project_path"] = new_target
        # 3-A — run_started_at on a same-project (re)start. The else branch is reached
        # both by a crash/restart RESUME of an in-flight run (preserve the original
        # stamp) and by a fresh re-run of a finished/idle project — the queue
        # trigger-next path, which spawns `--project-path` and so never reset state.
        # Stamp a new run-start unless the on-disk status shows the run is still active
        # AND a stamp already exists; otherwise the queue-launched/re-run case would
        # leave run_started_at null and the staleness badge dead (live-validation gap).
        if (orchestrator.state.get("pipeline_status") not in _RESUMABLE_ACTIVE_RUN_STATUSES
                or not orchestrator.state.get("run_started_at")):
            orchestrator.state["run_started_at"] = datetime.now(timezone.utc).isoformat()
            # A fresh run-start ⇒ a fresh run_id (P1-A). The resume branch (condition
            # False) keeps the run_id already carried in disk_state → same run.
            orchestrator.state["run_id"] = _new_run_id()

    # Update symlink before writing state: if the symlink fails the on-disk state
    # must not be updated, otherwise the orchestrator starts with the new project
    # path in state but agents still reading the old symlink target.
    if not orchestrator.update_symlink(new_target):
        print(f"[ERROR] Symlink update failed for {new_target!r} — not committing new project state.")
        return
    orchestrator.write_state()


def apply_cli_revive(orchestrator, entry_id: str) -> None:
    """Apply ``--revive <entry_id>`` (F2): resume a parked queue entry through the revival path.

    Unlike :func:`apply_cli_project_path` (which resets to a blank phase 0 when the target
    differs from the on-disk project), this restores the entry's escalated-phase pointer from
    its ``parked_state_snapshot`` and applies any banked operator command — so relaunching a
    parked-and-answered entry resumes the ESCALATED phase rather than restarting from scratch
    (the bug F2 fixes).

    Reuses the existing targeted selection/revival machinery: a banked ESCALATION is promoted to
    ESCALATION_ANSWERED inside ``_select_next_queue_project(target_entry_id=...)``, which then
    restores the snapshot and (for an answered entry) writes ``escalation_output`` via
    ``_apply_pending_escalation_command``; the subsequent ``run()`` loop consumes it.

    Returns ``True`` if the entry was revived/started, ``False`` otherwise (unknown id, or a
    not-revivable entry such as a crashed ACTIVE project). The ``__main__`` caller falls back to
    :func:`apply_cli_project_path` on ``False`` so a non-parked relaunch still refreshes the
    symlink and resumes from the persisted state.
    """
    try:
        queue_data = orchestrator._read_queue()
    except Exception as e:
        print(f"[REVIVE] Could not read queue ({e}); resuming from persisted state.")
        return False
    entry = next((e for e in queue_data.get("queue", []) if e.get("id") == entry_id), None)
    if entry is None:
        print(f"[REVIVE] Queue entry {entry_id!r} not found — resuming from persisted state.")
        return False
    started = orchestrator._select_next_queue_project(
        halt_if_no_eligible=False, target_entry_id=entry_id
    )
    if started:
        orchestrator.read_state()  # reload the state the revival just persisted
        print(f"[REVIVE] Resumed queue entry {entry_id!r} ({entry.get('name')}).")
        return True
    print(
        f"[REVIVE] Entry {entry_id!r} ({entry.get('name')}, state={entry.get('state')}) "
        f"not revivable — resuming from persisted state."
    )
    return False


if __name__ == "__main__":
    # Self-load <repo>/.env so operator knobs declared there (e.g. PROVIDER_ERROR_RETRY)
    # reach the orchestrator even when the spawning process (UI server / CLI) did not
    # `source .env`. setdefault semantics: an already-set env var (a sourced .env or a
    # test override) always wins. Entry-point only — never at import time, because the
    # test suite imports this module and load_repo_env_file()'s contract forbids an
    # import-time side effect for library importers. The module-level OPENCLAW_ROOT /
    # AUTODEV_PIPELINE_ROOT constants are already resolved by now (they rely on the
    # spawning env); only call-time reads like provider_error_retry_limit() depend on
    # this self-load.
    load_repo_env_file(AUTODEV_REPO_PATH)

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
    parser.add_argument(
        "--revive",
        dest="revive_entry_id",
        help="Queue entry id to RESUME via the revival path (restore the escalated phase + "
             "apply any banked operator command) instead of a phase-0 reset. Takes precedence "
             "over --project-path; used by the dashboard 'Resume' control for a parked entry.",
    )
    args = parser.parse_args()

    orchestrator = Orchestrator()

    # T6.1 — acquire the pipeline lock BEFORE any apply_cli_* call. apply_cli_revive /
    # apply_cli_project_path rewrite pipeline_state.json, the queue, and the project symlink;
    # acquiring the exclusive lock first means a second (losing) orchestrator started during
    # the boot window exits(1) here WITHOUT mutating that shared state, closing the TOCTOU
    # window where it could rewind an in-flight pipeline to phase 0. acquire_lock() is
    # idempotent, so run()'s own first-statement acquire_lock() is a no-op once this holds.
    orchestrator.acquire_lock()

    # --revive takes precedence: a relaunch of a parked entry must resume its escalated phase,
    # not reset to phase 0 (which would orphan the banked command). If the entry turns out not
    # to be revivable (unknown id, or a crashed ACTIVE project), fall back to --project-path so
    # the symlink is still refreshed and the persisted state resumes.
    if args.revive_entry_id:
        if not apply_cli_revive(orchestrator, args.revive_entry_id) and args.project_path:
            apply_cli_project_path(orchestrator, args.project_path)
    elif args.project_path:
        apply_cli_project_path(orchestrator, args.project_path)

    orchestrator.run()
