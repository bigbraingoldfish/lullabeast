"""Centralised resolvers for the two AutoDev path roots.

These helpers are the single source of truth for every pipeline, gate script,
heartbeat cron, and UI code path that needs to locate the OpenClaw installation
(``OPENCLAW_ROOT``) or the pipeline runtime / state directory
(``AUTODEV_PIPELINE_ROOT``).

Canonical env vars (post hard-cut — no aliases, no legacy flag)
--------------------------------------------------------------
- OpenClaw hub (contains ``openclaw.json``, ``workspace-*``):
    * env: ``OPENCLAW_ROOT``
    * default: ``~/.openclaw``

- Pipeline state directory (``pipeline_state.json``, ``pipeline.lock``,
  ``pipeline_queue.json``, ``pipeline-project`` symlink):
    * env: ``AUTODEV_PIPELINE_ROOT``
    * default: ``<AUTODEV_REPO_PATH>/.autodev``

Resolution order: canonical env -> built-in default. An empty string is treated
as "unset". The legacy alias ``AUTODEV_ROOT`` and the switch
``AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME`` do not affect pipeline root resolution.
Operators who need pipeline state to live alongside OpenClaw should set
``AUTODEV_PIPELINE_ROOT=$OPENCLAW_ROOT`` explicitly.
"""

from __future__ import annotations

import os


_OPENCLAW_DEFAULT = "~/.openclaw"


def _clean(value: str) -> str:
    return value.strip() if value and value.strip() else ""


def resolve_openclaw_root() -> str:
    """Return the absolute path to the OpenClaw installation root.

    Reads ``OPENCLAW_ROOT`` and falls back to ``~/.openclaw``. ``~`` is always
    expanded. Empty string is treated as unset.
    """
    raw = _clean(os.environ.get("OPENCLAW_ROOT", "")) or _OPENCLAW_DEFAULT
    return os.path.expanduser(raw)


def resolve_pipeline_root(repo_path: str) -> str:
    """Return the absolute path to the AutoDev pipeline state directory.

    Reads ``AUTODEV_PIPELINE_ROOT`` and otherwise derives
    ``<repo_path>/.autodev``. ``~`` is always expanded. Empty string is treated
    as unset.

    ``repo_path`` must be the resolved repository root (equivalent to
    ``AUTODEV_REPO_PATH``). The caller is responsible for passing a real path —
    this helper does not hunt for it.
    """
    raw = _clean(os.environ.get("AUTODEV_PIPELINE_ROOT", ""))
    if raw:
        return os.path.expanduser(raw)
    return os.path.join(repo_path, ".autodev")


def load_repo_env_file(repo_path: str | None = None) -> None:
    """Populate *unset* canonical env vars from ``<repo>/.env`` (cron self-load).

    The heartbeat and session-cleanup crons run under a bare system-cron
    environment where ``.env`` is not sourced, so without this the canonical
    roots would silently fall back to ``$HOME``-derived defaults (often wrong
    under cron — ``$HOME`` may be ``/`` or unset). Each ``KEY=VALUE`` line is
    applied with ``os.environ.setdefault``, so an already-set var (a properly
    sourced env, or a test's explicit override) is **never** clobbered.

    Blank lines, ``#`` comments, and lines without ``=`` are skipped; the split
    is on the first ``=`` only, and surrounding whitespace plus a single pair of
    wrapping quotes are stripped from the value. A missing or unreadable ``.env``
    is a silent no-op — this is a best-effort convenience, not a hard dependency.

    ``repo_path`` defaults to this module's repository root (three directories up
    from ``autodev/pipeline/env_resolvers.py``); callers may pass an explicit
    root (used by the tests). The loader is deliberately **not** invoked at
    import time — only the cron entry points call it — so library importers
    (orchestrator, UI server) see no import-time side effect.
    """
    if repo_path is None:
        repo_path = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env_path = os.path.join(repo_path, ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except (OSError, UnicodeDecodeError):
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


__all__ = ["resolve_openclaw_root", "resolve_pipeline_root", "load_repo_env_file"]
