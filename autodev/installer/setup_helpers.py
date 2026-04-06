#!/usr/bin/env python3
"""Installer helpers: refresh exec-approvals paths, merge .env keys, hooks baseline.

Callable from install.sh and from tests (TDD).

NOTE: openclaw.json is intentionally NOT created by AutoDev.  Its absence
means OpenClaw is not installed or is broken beyond what AutoDev can fix
(gateway process, auth-profiles, agent session management all depend on it).
install.sh fails fast when openclaw.json is missing rather than generating a
stub that would give a false sense of success.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

# Webhook session keys used by the pipeline and idea-to-PRD flows.
_REQUIRED_SESSION_KEY_PREFIXES: tuple[str, ...] = ("pipeline:", "ideas:")


def openclaw_hooks_issues(openclaw_json_path: str) -> list[str]:
    """Return human-readable issue codes for AutoDev hook expectations (read-only).

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
    """Ensure AutoDev-compatible ``hooks`` keys in openclaw.json (atomic write).

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
