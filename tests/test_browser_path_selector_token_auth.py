"""Unit guards for the browser E2E suite's dashboard-token support.

``tests/test_browser_path_selector.py`` runs against a live dashboard. When that dashboard is
token-protected (``AUTODEV_UI_TOKEN`` / ``_TokenAuthMiddleware``), the suite must be able to
authenticate via the dedicated opt-in env var ``AUTODEV_UI_E2E_TOKEN``:

- ``_auth_headers()`` yields the ``Authorization: Bearer`` header (or nothing without a token);
- browser contexts carry the header via ``extra_http_headers`` (or stay byte-identical bare);
- the reachability probe ``_probe_server()`` distinguishes ok / auth_rejected / unreachable so
  the skip stays loud and narrow (CHANGELOG "P1 Stage B"): a *wrong* explicit token must fail,
  a token-protected server without opt-in must skip with an actionable hint.

``AUTODEV_UI_E2E_TOKEN`` is deliberately separate from ``AUTODEV_UI_TOKEN``: the conftest scrubs
the latter per-test so a sourced ``.env`` cannot poison the suite, and pointing mutating browser
tests at a live tokenized dashboard must be an explicit operator opt-in.

These tests exec the module fresh per case (the token is read at import time, like ``URL``) and
need no live server or browser.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import urllib.error
import urllib.request

import pytest

# Module exec runs the target's pytest.importorskip gate, which needs playwright importable.
pytest.importorskip(
    "playwright.sync_api",
    reason="playwright not installed — pip install playwright && playwright install chromium",
)

_TARGET = pathlib.Path(__file__).with_name("test_browser_path_selector.py")
_TOKEN = "tok-e2e-123"
_BEARER = {"Authorization": f"Bearer {_TOKEN}"}


def _load_module(token: str | None):
    """Exec the target module with AUTODEV_UI_E2E_TOKEN set to *token* (None = absent).

    The token is captured at module import (same pattern as URL), so the env var must be
    arranged before exec; the prior value is always restored.
    """
    prior = os.environ.pop("AUTODEV_UI_E2E_TOKEN", None)
    if token is not None:
        os.environ["AUTODEV_UI_E2E_TOKEN"] = token
    try:
        spec = importlib.util.spec_from_file_location("_tbs_token_probe", _TARGET)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        os.environ.pop("AUTODEV_UI_E2E_TOKEN", None)
        if prior is not None:
            os.environ["AUTODEV_UI_E2E_TOKEN"] = prior


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


class _KwargsRecordingBrowser:
    """new_context(**kwargs) recorder — pins exactly what the fixture passes through."""

    def __init__(self):
        self.context_kwargs = []
        self.contexts = []
        self.closed = False

    def new_context(self, **kwargs):
        self.context_kwargs.append(kwargs)
        ctx = _FakeContext()
        self.contexts.append(ctx)
        return ctx

    def close(self):
        self.closed = True


# ---------------------------------------------------------------- _auth_headers


def test_auth_headers_empty_without_token():
    mod = _load_module(None)
    assert mod._auth_headers() == {}


def test_auth_headers_bearer_with_token():
    mod = _load_module(_TOKEN)
    assert mod._auth_headers() == _BEARER


# ------------------------------------------------------- browser context wiring


def test_context_is_bare_without_token():
    # Byte-identical no-token path: new_context() must receive ZERO kwargs.
    mod = _load_module(None)
    browser = _KwargsRecordingBrowser()
    gen = mod._isolated_page_session(browser)
    next(gen)
    assert browser.context_kwargs == [{}]
    with pytest.raises(StopIteration):
        next(gen)


def test_context_carries_bearer_header_with_token():
    mod = _load_module(_TOKEN)
    browser = _KwargsRecordingBrowser()
    gen = mod._isolated_page_session(browser)
    page = next(gen)
    assert browser.context_kwargs == [{"extra_http_headers": _BEARER}]
    assert page is browser.contexts[0].page
    with pytest.raises(StopIteration):
        next(gen)  # teardown: per-test context closes, shared browser survives
    assert browser.contexts[0].closed is True
    assert browser.closed is False


# ------------------------------------------------------------- _probe_server


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_probe_server_ok_and_sends_bearer_when_token_set(monkeypatch):
    mod = _load_module(_TOKEN)
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["auth"] = req.get_header("Authorization")
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert mod._probe_server() == "ok"
    assert seen["auth"] == f"Bearer {_TOKEN}"


def test_probe_server_sends_no_auth_header_without_token(monkeypatch):
    mod = _load_module(None)
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["auth"] = req.get_header("Authorization")
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert mod._probe_server() == "ok"
    assert seen["auth"] is None


def test_probe_server_auth_rejected_on_401(monkeypatch):
    mod = _load_module(None)

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", None, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert mod._probe_server() == "auth_rejected"


def test_probe_server_unreachable_on_connection_error(monkeypatch):
    mod = _load_module(None)

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert mod._probe_server() == "unreachable"


def test_probe_server_non_auth_http_error_is_unreachable(monkeypatch):
    # A 500 is not an auth rejection — it must not steer the operator toward token hints.
    mod = _load_module(None)

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 500, "Server Error", None, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert mod._probe_server() == "unreachable"
