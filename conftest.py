"""Repo-root pytest hooks shared by ``tests/`` and ``autodev/tests/``."""

import importlib
import os

import pytest


@pytest.fixture(autouse=True)
def _default_autodev_hooks_token_for_tests(monkeypatch):
    """Ensure webhook tests see a token when real ``load_config()`` runs (empty DEFAULTS)."""
    if not os.environ.get("AUTODEV_HOOKS_TOKEN"):
        monkeypatch.setenv("AUTODEV_HOOKS_TOKEN", "test-token")


# The pipeline is importable under two distinct module names — top-level ``orchestrator``
# (``autodev/tests/conftest.py`` wires its dir onto ``sys.path``) and the package path
# ``autodev.pipeline.orchestrator`` (a handful of tests import it that way). Those are
# SEPARATE objects in ``sys.modules`` with independent copies of the import-frozen path
# constants, so both must be redirected.
_ORCHESTRATOR_MODULE_NAMES = ("orchestrator", "autodev.pipeline.orchestrator")


@pytest.fixture(autouse=True)
def _isolate_pipeline_event_writes(monkeypatch, tmp_path):
    """Keep test-emitted pipeline events out of the developer's real activity feed.

    ``orchestrator._write_pipeline_event`` writes to ``<AUTODEV_PIPELINE_ROOT>/pipeline_events.jsonl``,
    resolving the module-level ``AUTODEV_PIPELINE_ROOT`` constant captured at import time. The
    per-test env-scrub in the suite conftests cannot reach that frozen constant, and its fallback
    chain lands on the real ``<repo>/.autodev`` regardless. So any test that drives an Orchestrator
    to an event-emitting path in-process (e.g. ``_select_next_queue_project`` -> the QUEUE_HALTED
    branch) without patching it appends a real event line into the running dashboard's feed.

    Redirect the write root to this test's ``tmp_path`` so the write is captured harmlessly. We
    deliberately leave ``SYMLINK_TARGET`` alone: it only feeds the event's cosmetic ``project``
    label, and the import-time-derived ``PROJECT_ARTIFACTS_DIR`` invariant built from it is asserted
    by ``test_orchestrator_project_artifacts_dir``. Tests that patch ``AUTODEV_PIPELINE_ROOT``
    themselves are unaffected: ``monkeypatch.setattr`` is restored per-test and a later per-test
    setattr (or a module reload that re-reads env) overrides this autouse default. Subprocess-spawned
    orchestrators do not inherit the patch — those are stubbed separately
    (``tests/conftest.py::_disable_queue_autostart``).
    """
    for modname in _ORCHESTRATOR_MODULE_NAMES:
        try:
            mod = importlib.import_module(modname)
        except Exception:
            continue
        if hasattr(mod, "AUTODEV_PIPELINE_ROOT"):
            monkeypatch.setattr(mod, "AUTODEV_PIPELINE_ROOT", str(tmp_path))
