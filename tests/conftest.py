"""Shared fixtures for UI server tests.

Prevents the developer's real ``.env`` (sourced into the shell before running
pytest) from leaking into hermetic tests. Individual tests that set these via
``monkeypatch.setenv`` still work because the autouse fixture runs before the
per-test monkeypatch.
"""

import pytest


_ENV_KEYS_TO_SCRUB = (
    "OPENCLAW_ROOT",
    "AUTODEV_PIPELINE_ROOT",
    "AUTODEV_HOOKS_TOKEN",
)


@pytest.fixture(autouse=True)
def _scrub_autodev_env(monkeypatch):
    for key in _ENV_KEYS_TO_SCRUB:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _disable_queue_autostart(request, monkeypatch):
    """Disable server-side queue auto-start by default across the whole UI suite.

    ``ui.server._maybe_autostart_queue`` starts the next eligible project by spawning a real
    orchestrator (via ``_queue_run_trigger_next_logic``). That orchestrator resolves the real
    ``OPENCLAW_ROOT`` / ``AUTODEV_PIPELINE_ROOT`` — the mock config omits them and the env-scrub
    above only forces the *fallback* chain, which still lands on the real ``~/.openclaw`` and the
    real ``<repo>/.autodev`` — so a stray auto-start from a test would mutate the live
    ``pipeline_state.json`` and repoint the live ``pipeline-project`` symlink.

    Any endpoint that makes a queue row READY (``add`` / ``parent``-clear / ``revalidate``) now
    calls this helper, so disable it by default. Tests that intentionally exercise auto-start opt
    in by setting ``_uses_real_autostart = True`` on their class and stub ``_spawn_orchestrator``
    themselves.
    """
    if getattr(getattr(request, "cls", None), "_uses_real_autostart", False):
        return
    monkeypatch.setattr(
        "ui.server._maybe_autostart_queue",
        lambda config: {"attempted": False, "reason": "test_disabled"},
        raising=False,
    )
