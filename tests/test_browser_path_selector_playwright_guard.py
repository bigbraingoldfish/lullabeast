"""Regression guard: ``tests/test_browser_path_selector.py`` must SKIP (not error) when the
optional Python ``playwright`` package is absent.

Before the module-level ``pytest.importorskip`` gate, a reachable UI server plus an
uninstalled ``playwright`` produced fixture-setup ``ModuleNotFoundError``s — one per browser
test — because the real import lived inside the ``pw_page`` fixture body and only fired once
``_server_ok()`` returned True. This test pins the contract: the module skips *loudly* at
import/collection, with a reason that names both the dependency and the remedy.

It is deliberately independent of UI-server state (the issue's suggested ``pytest … -q`` shows
skips on a server-down host for the *wrong* reason), and it runs even when ``playwright`` is
installed because it blocks the import in-process. See CHANGELOG "P1 Stage B" — skips must be
loud and narrow.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

_TARGET = pathlib.Path(__file__).with_name("test_browser_path_selector.py")


def test_browser_path_selector_skips_without_playwright(monkeypatch):
    # `sys.modules[name] = None` makes a subsequent `import name` raise ImportError (a
    # documented CPython import-system idiom); monkeypatch auto-restores both entries after the
    # test, so a real installed playwright is untouched for the rest of the session. Block the
    # top package and the submodule the module gates on.
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    spec = importlib.util.spec_from_file_location("_tbs_guard_probe", _TARGET)
    module = importlib.util.module_from_spec(spec)

    # Executing the module body runs its top-level pytest.importorskip gate. With playwright
    # blocked, that raises Skipped; before the gate existed, the body imported cleanly (the
    # playwright import was inside the fixture, never touched at module exec) and this raised
    # nothing — which is exactly the regression this test catches.
    with pytest.raises(pytest.skip.Exception) as excinfo:
        spec.loader.exec_module(module)

    msg = str(excinfo.value)
    assert "playwright" in msg, f"skip reason must name the dependency, got: {msg!r}"
    assert "pip install playwright" in msg, f"skip reason must name the remedy, got: {msg!r}"
