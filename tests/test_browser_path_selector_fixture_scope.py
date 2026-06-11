"""Unit guards for the browser-fixture topology in ``tests/test_browser_path_selector.py``.

Part B launches Chromium **once** (a session-scoped ``_pw_browser``) and gives each test its own
``browser.new_context()`` (the function-scoped ``pw_page``). The performance win must not cost
test isolation: a shared *context* would leak cookies/storage/UI-state between tests and make
them order-dependent. These tests pin both halves of that contract without a live browser, by
driving the extracted ``_isolated_page_session`` generator with a fake browser and reading the
fixtures' scope metadata.

The live behavioural check (the 9 real browser tests passing, in forward and reversed order
against a running UI server) is the primary verification; this file is the cheap regression net.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib

import pytest

# Introspecting the fixtures requires importing the module under test, which gates on playwright
# (pytest.importorskip). Skip this whole file loudly when the package is absent.
pytest.importorskip(
    "playwright.sync_api",
    reason="playwright not installed — pip install playwright && playwright install chromium",
)

_TARGET = pathlib.Path(__file__).with_name("test_browser_path_selector.py")


def _load_module():
    # Load by file path under a throwaway name so we never collide with pytest's own collection
    # of test_browser_path_selector.py in sys.modules. The dashboard token is stripped around
    # exec: these tests pin the bare NO-TOKEN contract (new_context() with zero kwargs), which
    # must hold even when the operator's shell has AUTODEV_UI_E2E_TOKEN exported — the
    # token-mode contract is pinned separately in test_browser_path_selector_token_auth.py.
    prior = os.environ.pop("AUTODEV_UI_E2E_TOKEN", None)
    try:
        spec = importlib.util.spec_from_file_location("_tbs_under_test", _TARGET)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if prior is not None:
            os.environ["AUTODEV_UI_E2E_TOKEN"] = prior


_MOD = _load_module()


class _FakePage:
    pass


class _FakeContext:
    def __init__(self):
        self.closed = False
        self.page = _FakePage()

    def new_page(self):
        return self.page

    def close(self):
        self.closed = True


class _FakeBrowser:
    """Records the contexts opened against it so a test can assert isolation + teardown."""

    def __init__(self):
        self.contexts = []
        self.closed = False

    def new_context(self):
        ctx = _FakeContext()
        self.contexts.append(ctx)
        return ctx

    def close(self):
        self.closed = True


def _fixture_scope(fixture_fn):
    # pytest 9 stores the scope on the FixtureFunctionDefinition's marker; fall back to the
    # older attribute name so a pytest bump degrades gracefully rather than KeyError-ing.
    marker = getattr(fixture_fn, "_fixture_function_marker", None) or getattr(
        fixture_fn, "_pytestfixturefunction", None
    )
    return getattr(marker, "scope", None)


def test_pw_browser_is_session_scoped_and_pw_page_is_function_scoped():
    assert _fixture_scope(_MOD._pw_browser) == "session", (
        "the browser must be launched once per session, not per test"
    )
    assert _fixture_scope(_MOD.pw_page) == "function", (
        "each test must receive its own page fixture"
    )


def test_isolated_page_session_uses_a_fresh_context_and_tears_only_it_down():
    browser = _FakeBrowser()
    gen = _MOD._isolated_page_session(browser)

    page = next(gen)
    assert len(browser.contexts) == 1, "exactly one fresh context per invocation"
    assert page is browser.contexts[0].page, "the page must come from the fresh context"
    assert browser.contexts[0].closed is False

    with pytest.raises(StopIteration):
        next(gen)  # run the generator's teardown
    assert browser.contexts[0].closed is True, "the per-test context must be closed on teardown"
    assert browser.closed is False, "the shared session browser must survive across tests"


def test_each_invocation_gets_a_distinct_isolated_context():
    browser = _FakeBrowser()
    g1 = _MOD._isolated_page_session(browser)
    next(g1)
    g2 = _MOD._isolated_page_session(browser)
    next(g2)
    assert len(browser.contexts) == 2
    assert browser.contexts[0] is not browser.contexts[1], (
        "two tests must not share a browser context (no cookie/storage/UI-state bleed)"
    )
