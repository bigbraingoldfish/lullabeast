"""Repo-root pytest hooks shared by ``tests/`` and ``autodev/tests/``."""

import os

import pytest


@pytest.fixture(autouse=True)
def _default_autodev_hooks_token_for_tests(monkeypatch):
    """Ensure webhook tests see a token when real ``load_config()`` runs (empty DEFAULTS)."""
    if not os.environ.get("AUTODEV_HOOKS_TOKEN"):
        monkeypatch.setenv("AUTODEV_HOOKS_TOKEN", "test-token")
