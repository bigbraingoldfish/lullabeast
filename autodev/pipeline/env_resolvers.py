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
as "unset". The legacy aliases ``AUTODEV_ROOT`` / ``AUTODEV_RUNTIME_ROOT`` and
the switch ``AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME`` were removed; setting any of
them has zero effect. Operators who need pipeline state to live alongside
OpenClaw should set ``AUTODEV_PIPELINE_ROOT=$OPENCLAW_ROOT`` explicitly.
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


__all__ = ["resolve_openclaw_root", "resolve_pipeline_root"]
