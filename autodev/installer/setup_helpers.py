#!/usr/bin/env python3
"""Installer helpers: refresh exec-approvals paths, merge .env keys, hooks baseline.

Callable from install.sh and from tests (TDD).

NOTE: openclaw.json is intentionally NOT created by Lullabeast.  Its absence
means OpenClaw is not installed or is broken beyond what Lullabeast can fix
(gateway process, auth-profiles, agent session management all depend on it).
install.sh fails fast when openclaw.json is missing rather than generating a
stub that would give a false sense of success.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Any

# Webhook session keys used by the pipeline and idea-to-PRD flows.
_REQUIRED_SESSION_KEY_PREFIXES: tuple[str, ...] = ("pipeline:", "ideas:")

# --- Lullabeast context-limit / truncation seeds -------------------------------
# Canonical source of truth for the openclaw.json truncation keys Lullabeast tunes.
#
# Why these values:
#   * bootstrapMaxChars=32000 — OpenClaw truncates each injected bootstrap file
#     (AGENTS.md, SOUL.md, …) at a 12k per-file default. Every pipeline role's
#     AGENTS.md exceeds that (planner 15.5k, executor 20.5k, reviewer 23k) and
#     its Stage A ``## Always-Apply: …`` rules begin past byte ~10k, so the
#     default silently drops the universal rules. 32k clears the largest file
#     with headroom for the living docs to grow.
#   * postCompactionMaxChars=8000 — after a context compaction OpenClaw re-injects
#     only the AGENTS.md sections named in postCompactionSections (below), capped
#     by this per-agent value (OpenClaw default 1800). The two Always-Apply
#     sections measure <=4.6k combined today; 8k holds them with headroom.
#   * postCompactionSections — OpenClaw's default ["Session Startup","Red Lines"]
#     names sections our AGENTS.md does not contain, so by default NOTHING of our
#     rules survives a compaction. We point it at our real H2 header names and
#     keep OpenClaw's defaults too (harmless for agents that lack those sections).
#
# register_agent.py duplicates the per-agent values because it runs as a
# standalone script and cannot import this module at runtime;
# test_register_agent.py::test_register_agent_seed_matches_setup_helpers_constants
# is the drift guard that keeps the two in sync.
AUTODEV_BOOTSTRAP_MAX_CHARS = 32000
AUTODEV_POSTCOMPACTION_MAX_CHARS = 8000
AUTODEV_BOOTSTRAP_AGENT_IDS: tuple[str, ...] = (
    "planner",
    "executor",
    "reviewer",
    "escalation",
    "prd-creator",
    "roadmap-converter",
)
# Only the pipeline coding roles carry the Stage A ``## Always-Apply: …``
# sections, so only they need the post-compaction cap.
AUTODEV_POSTCOMPACTION_AGENT_IDS: tuple[str, ...] = ("planner", "executor", "reviewer")
# Verbatim the H2 headers pinned by test_agents_md_universal_rules.py; OpenClaw's
# own defaults are appended so any future agent that adds them still benefits.
AUTODEV_POSTCOMPACTION_SECTIONS: tuple[str, ...] = (
    "Always-Apply: Integration Wiring",
    "Always-Apply: Testing Quality",
    "Always-Apply: Orchestrator Control",
    "Session Startup",
    "Red Lines",
)


def openclaw_hooks_issues(openclaw_json_path: str) -> list[str]:
    """Return human-readable issue codes for Lullabeast hook expectations (read-only).

    Empty list means the hooks block matches the baseline this installer enforces.
    """
    path = os.path.abspath(openclaw_json_path)
    if not os.path.isfile(path):
        return ["no_file"]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return ["invalid_json"]
    if not isinstance(data, dict):
        return ["invalid_root"]
    hooks = data.get("hooks")
    if hooks is None:
        return ["no_hooks_object"]
    if not isinstance(hooks, dict):
        return ["hooks_not_object"]
    issues: list[str] = []
    if hooks.get("enabled") is not True:
        issues.append("enabled")
    tok = hooks.get("token")
    if not isinstance(tok, str) or not tok.strip():
        issues.append("token")
    if hooks.get("allowRequestSessionKey") is not True:
        issues.append("allowRequestSessionKey")
    prefs = hooks.get("allowedSessionKeyPrefixes")
    if not isinstance(prefs, list):
        issues.append("allowedSessionKeyPrefixes")
    else:
        if "pipeline:" not in prefs:
            issues.append("prefix_pipeline")
        if "ideas:" not in prefs:
            issues.append("prefix_ideas")
    return issues


def _normalize_hooks_object(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("hooks")
    if not isinstance(raw, dict):
        data["hooks"] = {}
    return data["hooks"]


def _merge_required_prefixes(prefs: Any) -> list[str]:
    """Preserve order of string entries; append any missing required prefixes."""
    base: list[str] = []
    seen: set[str] = set()
    if isinstance(prefs, list):
        for p in prefs:
            if isinstance(p, str) and p not in seen:
                base.append(p)
                seen.add(p)
    for req in _REQUIRED_SESSION_KEY_PREFIXES:
        if req not in seen:
            base.append(req)
            seen.add(req)
    return base


def patch_openclaw_hooks_baseline(
    openclaw_json_path: str,
    *,
    token_if_missing: str | None = None,
) -> str:
    """Ensure Lullabeast-compatible ``hooks`` keys in openclaw.json (atomic write).

    Sets ``enabled`` and ``allowRequestSessionKey`` to True, merges required
    session-key prefixes, and sets ``token`` only when it is missing/empty and
    ``token_if_missing`` is a non-blank string.

    Returns: updated | unchanged | error:<msg>
    """
    path = os.path.abspath(openclaw_json_path)
    if not os.path.isfile(path):
        return "error:file not found"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return f"error:{e}"
    if not isinstance(data, dict):
        return "error:root must be an object"
    hooks = _normalize_hooks_object(data)
    before = json.dumps(data, sort_keys=True)

    if hooks.get("enabled") is not True:
        hooks["enabled"] = True
    if hooks.get("allowRequestSessionKey") is not True:
        hooks["allowRequestSessionKey"] = True

    new_prefs = _merge_required_prefixes(hooks.get("allowedSessionKeyPrefixes"))
    hooks["allowedSessionKeyPrefixes"] = new_prefs

    tok = hooks.get("token")
    existing_ok = isinstance(tok, str) and bool(tok.strip())
    if not existing_ok and token_if_missing is not None and str(token_if_missing).strip():
        hooks["token"] = str(token_if_missing).strip()

    after = json.dumps(data, sort_keys=True)
    if before == after:
        return "unchanged"
    parent = os.path.dirname(path)
    try:
        fd, tmp = tempfile.mkstemp(dir=parent, prefix="openclaw_hooks_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        return "updated"
    except Exception as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return f"error:{e}"


def _patch_gate_paths_in_obj(obj: Any, repo_path: str, gate_dir: str) -> int:
    """Recursively rewrite gate_scripts/*.py paths in dict keys and string values."""
    changed = 0
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            ck, k2 = _maybe_patch_path_value(k, repo_path, gate_dir)
            changed += ck
            cv, v2 = _maybe_patch_path_value(v, repo_path, gate_dir)
            changed += cv
            if ck:
                del obj[k]
                obj[k2] = v2
            else:
                obj[k] = v2
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            c, v2 = _maybe_patch_path_value(v, repo_path, gate_dir)
            changed += c
            obj[i] = v2
    return changed


def _maybe_patch_value_str(s: str, repo_path: str, gate_dir: str) -> tuple[int, str]:
    if "gate_scripts" not in s or not s.endswith(".py"):
        return 0, s
    if s.startswith(repo_path):
        return 0, s
    base = os.path.basename(s)
    new_p = os.path.join(gate_dir, base)
    if new_p != s and os.path.isfile(new_p):
        return 1, new_p
    return 0, s


def _maybe_patch_path_value(v: Any, repo_path: str, gate_dir: str) -> tuple[int, Any]:
    if isinstance(v, str):
        c, s2 = _maybe_patch_value_str(v, repo_path, gate_dir)
        return c, s2
    if isinstance(v, (dict, list)):
        return _patch_gate_paths_in_obj(v, repo_path, gate_dir), v
    return 0, v


def refresh_exec_approvals_gate_paths(exec_approvals_path: str, repo_path: str) -> str:
    """Rewrite stale gate script absolute paths to repo gate_scripts. Atomic replace.

    Returns: skipped_no_file | unchanged | updated | error:<msg>
    """
    path = os.path.abspath(exec_approvals_path)
    repo_path = os.path.abspath(repo_path)
    gate_dir = os.path.join(repo_path, "autodev", "pipeline", "gate_scripts")
    if not os.path.isfile(path):
        return "skipped_no_file"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return f"error:{e}"
    before = json.dumps(data, sort_keys=True)
    _patch_gate_paths_in_obj(data, repo_path, gate_dir)
    after = json.dumps(data, sort_keys=True)
    if before == after:
        return "unchanged"
    parent = os.path.dirname(path)
    try:
        fd, tmp = tempfile.mkstemp(dir=parent, prefix="exec_approvals_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        return "updated"
    except Exception as e:
        return f"error:{e}"


# Appended once per .env by install.sh so operators discover stall overrides.
DOTENV_STALL_HINT_MARKER = "# --- AutoDev: Tier A stall timeouts (optional) ---"

# Appended once per .env by install.sh so operators discover Ideas poll overrides.
DOTENV_IDEAS_IDLE_HINT_MARKER = "# --- AutoDev: Ideas UI poll thresholds (optional) ---"

# Independent marker so existing .env files (that already have the idle-hints block)
# still gain the new history-budget placeholder on next install.sh run.
DOTENV_IDEAS_HISTORY_BUDGET_HINT_MARKER = (
    "# --- AutoDev: Ideas chat history budget (optional) ---"
)


def ensure_dotenv_stall_timeout_hints(env_path: str) -> str:
    """Append a comment-only block about AUTODEV_STALL_TIMEOUT_* if not present.

    Lines are fully commented so they are not live configuration. Idempotent
    via ``DOTENV_STALL_HINT_MARKER``.

    Returns: appended | unchanged | error:<msg>
    """
    path = os.path.abspath(env_path)
    if not os.path.isfile(path):
        return "unchanged"
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return f"error:{e}"
    if DOTENV_STALL_HINT_MARKER in content:
        return "unchanged"
    block = f"""
{DOTENV_STALL_HINT_MARKER}
# Two-knob stall detection (autodev/pipeline/sentinel_poller.py:poll_for_sentinel).
# STARTUP_GRACE bounds the pre-first-hook wait; STALL_TIMEOUT bounds the
# post-first-hook silence.  They are independent — tune cold OpenClaw boot
# time with STARTUP_GRACE and mid-turn-silence detection with STALL_TIMEOUT.
# Uncomment a line and set an integer to override the built-in default.
#
# Built-in defaults if these stay unset:
#   STALL_TIMEOUT_*  (post-first-hook silence)  — planner/executor/reviewer = 300 s
#   STARTUP_GRACE_*  (pre-first-hook wait)      — planner/executor/reviewer = 600 s
# AUTODEV_STALL_TIMEOUT_PLANNER=
# AUTODEV_STALL_TIMEOUT_EXECUTOR=
# AUTODEV_STALL_TIMEOUT_REVIEWER=
# AUTODEV_STARTUP_GRACE_PLANNER=
# AUTODEV_STARTUP_GRACE_EXECUTOR=
# AUTODEV_STARTUP_GRACE_REVIEWER=
"""
    try:
        with open(path, "a", encoding="utf-8") as f:
            if content and not content.endswith("\n"):
                f.write("\n")
            f.write(block.lstrip("\n"))
    except OSError as e:
        return f"error:{e}"
    return "appended"


def ensure_dotenv_ideas_idle_hints(env_path: str) -> str:
    """Append a comment-only block about AUTODEV_IDEAS_* if not present.

    Returns: appended | unchanged | error:<msg>
    """
    path = os.path.abspath(env_path)
    if not os.path.isfile(path):
        return "unchanged"
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return f"error:{e}"
    if DOTENV_IDEAS_IDLE_HINT_MARKER in content:
        return "unchanged"
    block = f"""
{DOTENV_IDEAS_IDLE_HINT_MARKER}
# UI server Ideas chat: seconds of stamp silence (after first activity) before the
# chat turn is declared a definitive stall. Env override wins over ui/config.json.
# Default if unset: 300. (No startup-grace knob — the chat send waits for a
# definitive stall/backstop verdict rather than fast-failing a slow first hook.)
# AUTODEV_IDEAS_IDLE_THRESHOLD=
"""
    try:
        with open(path, "a", encoding="utf-8") as f:
            if content and not content.endswith("\n"):
                f.write("\n")
            f.write(block.lstrip("\n"))
    except OSError as e:
        return f"error:{e}"
    return "appended"


def ensure_dotenv_ideas_history_budget_hint(env_path: str) -> str:
    """Append a comment-only block about AUTODEV_IDEAS_HISTORY_CHAR_BUDGET.

    Uses an independent marker from the idle-hints block so existing installs
    that already appended the idle block still gain this placeholder on
    upgrade. Idempotent via ``DOTENV_IDEAS_HISTORY_BUDGET_HINT_MARKER``.

    Returns: appended | unchanged | error:<msg>
    """
    path = os.path.abspath(env_path)
    if not os.path.isfile(path):
        return "unchanged"
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return f"error:{e}"
    if DOTENV_IDEAS_HISTORY_BUDGET_HINT_MARKER in content:
        return "unchanged"
    block = f"""
{DOTENV_IDEAS_HISTORY_BUDGET_HINT_MARKER}
# UI server Ideas chat: hard cap on characters in the inline [CONVERSATION HISTORY]
# block prepended to each prd-creator webhook. The most recent 3 (user, assistant)
# pairs are kept inline; once that block exceeds this budget the oldest pair is
# dropped, and if a single remaining pair still exceeds the budget its content is
# truncated with a [...truncated...] marker. Older context stays available via
# ~/.openclaw/ideas/{{id}}/conversation_log.md, which the agent can Read on demand.
# Default if unset: 20000.
# AUTODEV_IDEAS_HISTORY_CHAR_BUDGET=
"""
    try:
        with open(path, "a", encoding="utf-8") as f:
            if content and not content.endswith("\n"):
                f.write("\n")
            f.write(block.lstrip("\n"))
    except OSError as e:
        return f"error:{e}"
    return "appended"


def merge_dotenv_missing_keys(env_path: str, pairs: dict[str, str]) -> str:
    """Append KEY=value lines for keys not already present (non-destructive).

    Returns: created | updated | unchanged | error:<msg>
    """
    path = os.path.abspath(env_path)
    existing_keys: set[str] = set()
    lines: list[str] = []
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    lines.append(line)
                    s = line.strip()
                    if s and not s.startswith("#") and "=" in s:
                        existing_keys.add(s.split("=", 1)[0].strip())
        except Exception as e:
            return f"error:{e}"
    to_add = [(k, v) for k, v in pairs.items() if k not in existing_keys]
    if not to_add and lines:
        return "unchanged"
    if not lines and not os.path.isfile(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("# AutoDev — generated by install.sh\n")
                for k, v in pairs.items():
                    f.write(f"{k}={v}\n")
            return "created"
        except Exception as e:
            return f"error:{e}"
    if to_add:
        try:
            with open(path, "a", encoding="utf-8") as f:
                if lines and not lines[-1].endswith("\n"):
                    f.write("\n")
                f.write("\n# Added by install.sh (missing keys)\n")
                for k, v in to_add:
                    f.write(f"{k}={v}\n")
            return "updated"
        except Exception as e:
            return f"error:{e}"
    return "unchanged"


def force_dotenv_keys(env_path: str, pairs: dict[str, str]) -> str:
    """Set or replace each ``KEY=value`` in ``.env`` (single atomic write).

    The overwrite counterpart of :func:`merge_dotenv_missing_keys`, for keys
    whose value is environment-owned truth (the container's path and token
    keys) rather than operator-tunable knobs. Existing values for the given
    keys are replaced in place; every other line is preserved verbatim.

    Returns: created | updated | unchanged | error:<msg>
    """
    path = os.path.abspath(env_path)
    clean = {k.strip(): str(v) for k, v in pairs.items() if k.strip()}
    if not clean:
        return "error:no keys"
    existed = os.path.isfile(path)
    lines: list[str] = []
    if existed:
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError as e:
            return f"error:{e}"
    out: list[str] = []
    replaced: set[str] = set()
    for line in lines:
        s = line.lstrip()
        if s and not s.startswith("#") and "=" in s:
            key = s.split("=", 1)[0].strip()
            if key in clean:
                if key not in replaced:
                    out.append(f"{key}={clean[key]}\n")
                    replaced.add(key)
                continue
        out.append(line)
    missing = [k for k in clean if k not in replaced]
    if missing:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        if not out:
            out.append("# Lullabeast environment (written by the container entrypoint)\n")
        for k in missing:
            out.append(f"{k}={clean[k]}\n")
    body = "".join(out)
    if existed and body == "".join(lines):
        return "unchanged"
    parent = os.path.dirname(path) or "."
    tmp = None
    try:
        os.makedirs(parent, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent, prefix="env_", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp, path)
        return "created" if not existed else "updated"
    except Exception as e:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return f"error:{e}"


def read_openclaw_hooks_token(openclaw_json_path: str) -> str | None:
    """Return ``hooks.token`` from openclaw.json, or None if missing/invalid/empty."""
    path = os.path.abspath(openclaw_json_path)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return None
    tok = hooks.get("token")
    if not isinstance(tok, str):
        return None
    t = tok.strip()
    return t if t else None


def parse_dotenv_value(env_path: str, key: str) -> str | None:
    """Return the last non-comment assignment for ``key``, or None if unset/empty."""
    path = os.path.abspath(env_path)
    if not os.path.isfile(path):
        return None
    last: str | None = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if "=" not in s:
                    continue
                k, v = s.split("=", 1)
                if k.strip() != key:
                    continue
                val = v.strip().strip("'").strip('"')
                last = val if val else None
    except OSError:
        return None
    return last


@dataclass(frozen=True)
class WebhookSecretSync:
    """Compare ``hooks.token`` to Lullabeast UI config and optional ``.env`` (no secrets logged)."""

    expected_token: str | None
    ui_config_path: str
    ui_config_exists: bool
    ui_hooks_token: str | None
    env_path: str
    env_hooks_token: str | None
    ui_needs_sync: bool
    env_key_missing_or_empty: bool
    env_wrong: bool

    def summary_code(self) -> str:
        if not self.expected_token:
            return "no_hooks_token"
        if self.env_wrong and self.ui_needs_sync:
            return "mismatch_both"
        if self.env_wrong:
            return "mismatch_env"
        if self.ui_needs_sync:
            return "mismatch_ui"
        return "ok"


def webhook_secret_sync_assess(
    openclaw_json_path: str,
    ui_config_path: str,
    env_path: str,
) -> WebhookSecretSync:
    """Assess whether ``ui/config.json`` and ``.env`` match ``hooks.token``."""
    oc = os.path.abspath(openclaw_json_path)
    ui_p = os.path.abspath(ui_config_path)
    env_p = os.path.abspath(env_path)
    expected = read_openclaw_hooks_token(oc)

    ui_exists = os.path.isfile(ui_p)
    ui_tok: str | None = None
    if ui_exists:
        try:
            with open(ui_p, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if isinstance(cfg, dict):
                ht = cfg.get("hooks_token")
                if isinstance(ht, str) and ht.strip():
                    ui_tok = ht.strip()
        except (OSError, json.JSONDecodeError, TypeError):
            ui_tok = None

    env_val = parse_dotenv_value(env_p, "AUTODEV_HOOKS_TOKEN")
    env_missing = env_val is None

    if not expected:
        return WebhookSecretSync(
            expected_token=None,
            ui_config_path=ui_p,
            ui_config_exists=ui_exists,
            ui_hooks_token=ui_tok,
            env_path=env_p,
            env_hooks_token=env_val,
            ui_needs_sync=False,
            env_key_missing_or_empty=env_missing,
            env_wrong=False,
        )

    ui_needs = (not ui_exists) or (ui_tok != expected)
    env_wrong = env_val is not None and env_val != expected

    return WebhookSecretSync(
        expected_token=expected,
        ui_config_path=ui_p,
        ui_config_exists=ui_exists,
        env_path=env_p,
        env_hooks_token=env_val,
        ui_hooks_token=ui_tok,
        ui_needs_sync=ui_needs,
        env_key_missing_or_empty=env_missing,
        env_wrong=env_wrong,
    )


def set_ui_config_hooks_token(ui_config_path: str, token: str) -> str:
    """Set ``hooks_token`` in ui/config.json (atomic). Preserves other keys.

    Returns: updated | unchanged | error:<msg>
    """
    path = os.path.abspath(ui_config_path)
    if not os.path.isfile(path):
        return "error:file not found"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return f"error:{e}"
    if not isinstance(data, dict):
        return "error:root must be an object"
    t = str(token).strip()
    if not t:
        return "error:empty token"
    if data.get("hooks_token") == t:
        return "unchanged"
    data["hooks_token"] = t
    parent = os.path.dirname(path)
    try:
        fd, tmp = tempfile.mkstemp(dir=parent, prefix="ui_config_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        return "updated"
    except Exception as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return f"error:{e}"


def set_dotenv_key(env_path: str, key: str, value: str) -> str:
    """Set or replace ``KEY=value`` in ``.env`` (atomic write).

    Returns: created | updated | unchanged | error:<msg>
    """
    path = os.path.abspath(env_path)
    key = key.strip()
    if not key:
        return "error:empty key"
    val = str(value)
    assignment = f"{key}={val}\n"
    existed = os.path.isfile(path)
    lines: list[str] = []
    if existed:
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError as e:
            return f"error:{e}"
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    replaced = False
    out: list[str] = []
    for line in lines:
        if pattern.match(line) and not line.lstrip().startswith("#"):
            if not replaced:
                out.append(assignment)
                replaced = True
            continue
        out.append(line)
    if not replaced:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        if out:
            out.append("\n# Webhook Bearer (synced by install.sh)\n")
        out.append(assignment)
    body = "".join(out)
    if existed:
        try:
            with open(path, "r", encoding="utf-8") as f:
                before = f.read()
        except OSError as e:
            return f"error:{e}"
        if before == body:
            return "unchanged"
    parent = os.path.dirname(path) or "."
    try:
        os.makedirs(parent, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent, prefix="env_", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp, path)
        return "created" if not existed else "updated"
    except Exception as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return f"error:{e}"


def webhook_secret_remediation_text() -> str:
    """Human-readable steps when webhook secret sync could not be applied (no secrets)."""
    return """\
Pipeline / Project Ideas / agent webhooks will NOT work until this is fixed.

  1. Open ~/.openclaw/openclaw.json → hooks.token (not gateway.auth).
  2. Set the same value as AUTODEV_HOOKS_TOKEN in <repo>/.env and/or hooks_token in ui/config.json.
  3. Always run:  source .env  before starting the UI (or use systemd EnvironmentFile= for .env).
  4. Restart uvicorn.
  5. Verify with POST (not GET only), from the same host as OpenClaw:
       curl -sS -o /dev/null -w \"HTTP %{http_code}\\n\" -X POST http://127.0.0.1:18789/hooks/agent \\
         -H \"Authorization: Bearer <hooks.token>\" -H \"Content-Type: application/json\" \\
         -d '{\"agentId\":\"prd-creator\",\"sessionKey\":\"ideas:install-check:0\",\"wakeMode\":\"now\",\"message\":\"ping\"}'
     Expect HTTP 200. 401 means the Bearer does not match hooks.token.
"""


def set_openclaw_global_tools_profile(openclaw_json_path: str, profile: str = "coding") -> str:
    """Set top-level ``tools.profile`` in openclaw.json (atomic write).

    Returns: updated | unchanged | error:<msg>
    """
    path = os.path.abspath(openclaw_json_path)
    if not os.path.isfile(path):
        return "error:file not found"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return f"error:{e}"
    if not isinstance(data, dict):
        return "error:root must be an object"
    tools = data.setdefault("tools", {})
    if not isinstance(tools, dict):
        return "error:tools must be an object"
    current = tools.get("profile")
    if current == profile:
        return "unchanged"
    tools["profile"] = profile
    parent = os.path.dirname(path)
    try:
        fd, tmp = tempfile.mkstemp(dir=parent, prefix="openclaw_tools_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        return "updated"
    except Exception as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return f"error:{e}"


def _merge_sections(existing: Any, required: tuple[str, ...]) -> list[str]:
    """Preserve order of existing string entries; append any missing required names."""
    out: list[str] = []
    seen: set[str] = set()
    if isinstance(existing, list):
        for s in existing:
            if isinstance(s, str) and s not in seen:
                out.append(s)
                seen.add(s)
    for s in required:
        if s not in seen:
            out.append(s)
            seen.add(s)
    return out


def audit_openclaw_context_limits(openclaw_json_path: str) -> list[str]:
    """Read-only audit of the truncation keys ``ensure_openclaw_context_limits`` seeds.

    Returns issue codes; an empty list means the config is conformant. Codes:
      * ``no_file`` / ``invalid_json`` / ``invalid_root`` / ``agents_shape``
      * ``{agent_id}:bootstrapMaxChars`` when a present Lullabeast agent entry is
        missing the cap or carries a value below ``AUTODEV_BOOTSTRAP_MAX_CHARS``
      * ``{agent_id}:postCompactionMaxChars`` when a present pipeline-role entry
        is missing the cap or carries a value below
        ``AUTODEV_POSTCOMPACTION_MAX_CHARS``
      * ``missing_section:{name}`` for each required Always-Apply header absent
        from ``agents.defaults.compaction.postCompactionSections``

    Absent agent entries are deliberately NOT reported here (that is the
    agents-registered check's job); only entries that exist are audited. This is
    the audit-only sibling of the mutating ``ensure_openclaw_context_limits``,
    used by the doctor, which must never write.
    """
    path = os.path.abspath(openclaw_json_path)
    if not os.path.isfile(path):
        return ["no_file"]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return ["invalid_json"]
    if not isinstance(data, dict):
        return ["invalid_root"]
    agents = data.get("agents")
    if not isinstance(agents, dict) or not isinstance(agents.get("list"), list):
        return ["agents_shape"]

    issues: list[str] = []
    bootstrap_ids = set(AUTODEV_BOOTSTRAP_AGENT_IDS)
    postcompaction_ids = set(AUTODEV_POSTCOMPACTION_AGENT_IDS)
    for entry in agents["list"]:
        if not isinstance(entry, dict):
            continue
        aid = entry.get("id")
        if aid in bootstrap_ids:
            bmc = entry.get("bootstrapMaxChars")
            if not isinstance(bmc, (int, float)) or isinstance(bmc, bool) or bmc < AUTODEV_BOOTSTRAP_MAX_CHARS:
                issues.append(f"{aid}:bootstrapMaxChars")
        if aid in postcompaction_ids:
            cl = entry.get("contextLimits")
            pcm = cl.get("postCompactionMaxChars") if isinstance(cl, dict) else None
            if not isinstance(pcm, (int, float)) or isinstance(pcm, bool) or pcm < AUTODEV_POSTCOMPACTION_MAX_CHARS:
                issues.append(f"{aid}:postCompactionMaxChars")

    defaults = agents.get("defaults")
    compaction = defaults.get("compaction") if isinstance(defaults, dict) else None
    sections = compaction.get("postCompactionSections") if isinstance(compaction, dict) else None
    present = {s for s in sections if isinstance(s, str)} if isinstance(sections, list) else set()
    for name in AUTODEV_POSTCOMPACTION_SECTIONS:
        if not name.startswith("Always-Apply:"):
            continue
        if name not in present:
            issues.append(f"missing_section:{name}")
    return issues


def ensure_openclaw_context_limits(openclaw_json_path: str) -> str:
    """Seed Lullabeast bootstrap/compaction truncation keys in openclaw.json (atomic).

    Idempotently ensures, without disturbing any other key:
      * ``agents.list[id in AUTODEV_BOOTSTRAP_AGENT_IDS].bootstrapMaxChars`` =
        ``AUTODEV_BOOTSTRAP_MAX_CHARS`` — stops the 12k per-file bootstrap default
        from truncating each role's AGENTS.md (the Stage A ``## Always-Apply``
        rules begin past byte ~10k).
      * ``agents.list[id in AUTODEV_POSTCOMPACTION_AGENT_IDS].contextLimits.
        postCompactionMaxChars`` = ``AUTODEV_POSTCOMPACTION_MAX_CHARS`` — sizes the
        post-compaction refresh to hold the two Always-Apply sections.
      * ``agents.defaults.compaction.postCompactionSections`` merged to include
        ``AUTODEV_POSTCOMPACTION_SECTIONS`` — points the refresh at our real H2
        header names (OpenClaw's default targets sections we do not have, so the
        rules are otherwise dropped on every compaction).

    Only entries whose ``id`` is an Lullabeast agent are touched; other agents and
    all unrelated keys are preserved. This is the upgrade path for installs whose
    agents already exist (register_agent leaves existing entries untouched) and is
    safe to re-run.

    Returns: updated | unchanged | error:<msg>
    """
    path = os.path.abspath(openclaw_json_path)
    if not os.path.isfile(path):
        return "error:file not found"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return f"error:{e}"
    if not isinstance(data, dict):
        return "error:root must be an object"
    agents = data.get("agents")
    if not isinstance(agents, dict):
        return "error:agents must be an object"
    agent_list = agents.get("list")
    if not isinstance(agent_list, list):
        return "error:agents.list must be an array"

    before = json.dumps(data, sort_keys=True)

    bootstrap_ids = set(AUTODEV_BOOTSTRAP_AGENT_IDS)
    postcompaction_ids = set(AUTODEV_POSTCOMPACTION_AGENT_IDS)
    for entry in agent_list:
        if not isinstance(entry, dict):
            continue
        aid = entry.get("id")
        if aid in bootstrap_ids:
            entry["bootstrapMaxChars"] = AUTODEV_BOOTSTRAP_MAX_CHARS
        if aid in postcompaction_ids:
            cl = entry.get("contextLimits")
            if not isinstance(cl, dict):
                cl = {}
                entry["contextLimits"] = cl
            cl["postCompactionMaxChars"] = AUTODEV_POSTCOMPACTION_MAX_CHARS

    # postCompactionSections is global-only — the schema has no per-agent
    # ``agents.list[].compaction`` block — so it lives under agents.defaults.
    defaults = agents.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}
        agents["defaults"] = defaults
    compaction = defaults.get("compaction")
    if not isinstance(compaction, dict):
        compaction = {}
        defaults["compaction"] = compaction
    compaction["postCompactionSections"] = _merge_sections(
        compaction.get("postCompactionSections"), AUTODEV_POSTCOMPACTION_SECTIONS
    )

    after = json.dumps(data, sort_keys=True)
    if before == after:
        return "unchanged"
    parent = os.path.dirname(path)
    try:
        fd, tmp = tempfile.mkstemp(dir=parent, prefix="openclaw_ctxlimits_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        return "updated"
    except Exception as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return f"error:{e}"
