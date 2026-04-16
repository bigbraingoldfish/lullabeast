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
