"""TDD suite for the UI access-token auth middleware (written before the implementation).

Covers the dashboard access-token design:

- **Legacy open mode** (no ``ui_token`` configured): loopback behavior is byte-compatible
  with the pre-auth server, and the suite's own no-credential assumption stays a *tested*
  contract rather than an accident.
- **Open-mode network guard**: with no token configured, a request whose client address
  parses as a non-loopback IP is refused (403). An unparseable client host (e.g. the
  TestClient's ``("testclient", 50000)``) is deliberately allowed — over real TCP uvicorn
  always supplies a real IP, so the fail-open only affects in-process transports.
- **Token mode**: ``/api/*`` and ``/`` require credentials (401); the three channels are
  Bearer header, ``?token=`` on ``/`` (303 redirect + HttpOnly SameSite=Lax cookie), and
  the cookie itself. The query channel is honored ONLY on ``/`` so tokens never appear in
  API request logs.
- **Exemptions**: ``/health`` and ``/static/*`` stay open (liveness + assets).
- **Dual-source config**: ``AUTODEV_UI_TOKEN`` (stripped) overrides ``ui_token`` from
  ui/config.json, mirroring the AUTODEV_HOOKS_TOKEN pattern.
- **Malformed config**: a non-string ``ui_token`` degrades to auth-off rather than crashing
  the middleware (house malformed-config philosophy).

SSE note: the positive (authenticated) streaming path of ``/api/events/stream`` is NOT
exercised through TestClient here — httpx's ASGI transport cannot consume the endpoint's
infinite stream, which is exactly why ``tests/test_api_events_stream.py`` uses a real
uvicorn server fixture. The middleware is pure ASGI (no response wrapping), credential
checks are path-independent (covered on ``/api/state``), and the live-server SSE tests
continue to cover streaming; this file pins the 401 gate on the stream route.
"""
import os
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ui.server import app

TOKEN = "test-ui-token-12345"
COOKIE_NAME = "autodev_ui_token"


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def _mock_config(temp_dir, **extra):
    """Minimal read-tolerant config (missing files => /api/state degrades to 200)."""
    cfg = {
        "pipeline_state_path": os.path.join(temp_dir, "pipeline_state.json"),
        "phase_state_path": os.path.join(temp_dir, "phase_state.json"),
        "lock_path": os.path.join(temp_dir, "pipeline.lock"),
        "events_path": os.path.join(temp_dir, "pipeline_events.jsonl"),
        "roadmap_path": os.path.join(temp_dir, "roadmap.md"),
        "project_dir_path": os.path.join(temp_dir, "project"),
    }
    cfg.update(extra)
    return cfg


@pytest.fixture
def fresh_client():
    """Per-test client so auth cookies can never leak between tests."""
    return TestClient(app)


# ── Open mode (no ui_token configured) ───────────────────────────────────────


def test_no_token_api_passthrough(fresh_client, temp_dir):
    """Legacy mode: without a configured token, /api/* behaves exactly as before."""
    with patch("ui.server.load_config", return_value=_mock_config(temp_dir)):
        response = fresh_client.get("/api/state")
    assert response.status_code == 200


def test_no_token_root_serves_index(fresh_client, temp_dir):
    with patch("ui.server.load_config", return_value=_mock_config(temp_dir)):
        response = fresh_client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")


def test_no_token_nonloopback_client_refused(temp_dir):
    """The README promise: no token configured => non-loopback requests are refused."""
    lan_client = TestClient(app, client=("203.0.113.9", 4444))
    with patch("ui.server.load_config", return_value=_mock_config(temp_dir)):
        response = lan_client.get("/api/state")
    assert response.status_code == 403
    assert "token" in response.json()["detail"].lower()


def test_no_token_nonloopback_root_also_refused(temp_dir):
    """The network guard covers the dashboard page too, not just /api/*."""
    lan_client = TestClient(app, client=("203.0.113.9", 4444))
    with patch("ui.server.load_config", return_value=_mock_config(temp_dir)):
        response = lan_client.get("/")
    assert response.status_code == 403


def test_no_token_loopback_client_allowed(temp_dir):
    loopback_client = TestClient(app, client=("127.0.0.1", 50000))
    with patch("ui.server.load_config", return_value=_mock_config(temp_dir)):
        response = loopback_client.get("/api/state")
    assert response.status_code == 200


def test_no_token_unparseable_client_host_allowed(temp_dir):
    """Non-IP client hosts (in-process transports) are treated as loopback.

    Over real TCP uvicorn always supplies a real IP, so this fail-open cannot be
    reached from the network; collapsing it to "refuse" would break every
    TestClient-based test in the suite. Pinned so nobody 'fixes' it.
    """
    inproc_client = TestClient(app, client=("not-an-ip", 1))
    with patch("ui.server.load_config", return_value=_mock_config(temp_dir)):
        response = inproc_client.get("/api/state")
    assert response.status_code == 200


def test_no_token_health_exempt_even_nonloopback(temp_dir):
    """/health stays reachable for network liveness probes; it leaks only {ok: true}."""
    lan_client = TestClient(app, client=("203.0.113.9", 4444))
    with patch("ui.server.load_config", return_value=_mock_config(temp_dir)):
        response = lan_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


# ── Token mode: the lock ─────────────────────────────────────────────────────


def test_token_api_requires_credentials(fresh_client, temp_dir):
    with patch("ui.server.load_config", return_value=_mock_config(temp_dir, ui_token=TOKEN)):
        response = fresh_client.get("/api/state")
    assert response.status_code == 401
    assert "token" in response.json()["detail"].lower()
    assert response.headers.get("www-authenticate") == "Bearer"


def test_token_root_unauthenticated_shows_401_page(fresh_client, temp_dir):
    """/ gets a human-readable 401 page (the browser entry point), not bare JSON."""
    with patch("ui.server.load_config", return_value=_mock_config(temp_dir, ui_token=TOKEN)):
        response = fresh_client.get("/")
    assert response.status_code == 401
    assert "text/html" in response.headers.get("content-type", "")
    assert "token" in response.text.lower()


def test_token_health_and_static_exempt(fresh_client, temp_dir):
    with patch("ui.server.load_config", return_value=_mock_config(temp_dir, ui_token=TOKEN)):
        health = fresh_client.get("/health")
        static = fresh_client.get("/static/marked.min.js")
    assert health.status_code == 200
    assert static.status_code == 200


def test_token_sse_stream_requires_credentials(temp_dir):
    """The SSE route is governed by the middleware (the reason it is pure ASGI).

    Runs in a daemon thread with a hard join timeout: if the middleware is
    absent or broken, the request reaches the real endpoint and streams
    forever, and TestClient.get() never returns — observed live on this
    suite's pre-implementation red run. The watchdog converts that hang into
    a prompt FAILURE; the abandoned daemon thread cannot block process exit.
    """
    import threading

    result = {}

    def _call():
        client = TestClient(app)
        with patch(
            "ui.server.load_config",
            return_value=_mock_config(temp_dir, ui_token=TOKEN),
        ):
            result["status"] = client.get("/api/events/stream").status_code

    worker = threading.Thread(target=_call, daemon=True)
    worker.start()
    worker.join(timeout=15)
    assert result.get("status") == 401, (
        "SSE stream request did not return 401 promptly — with the auth "
        "middleware absent or broken, the infinite stream hangs instead of "
        "being rejected"
    )


# ── Token mode: the three credential channels ────────────────────────────────


def test_token_bearer_header_grants(fresh_client, temp_dir):
    with patch("ui.server.load_config", return_value=_mock_config(temp_dir, ui_token=TOKEN)):
        api = fresh_client.get("/api/state", headers={"Authorization": f"Bearer {TOKEN}"})
        root = fresh_client.get("/", headers={"Authorization": f"Bearer {TOKEN}"})
    assert api.status_code == 200
    assert root.status_code == 200


def test_token_query_on_root_sets_cookie_and_redirects(fresh_client, temp_dir):
    """GET /?token=<valid> => 303 to a clean URL + HttpOnly SameSite=Lax cookie."""
    with patch("ui.server.load_config", return_value=_mock_config(temp_dir, ui_token=TOKEN)):
        response = fresh_client.get("/", params={"token": TOKEN}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    set_cookie = response.headers.get("set-cookie", "")
    assert COOKIE_NAME in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()


def test_token_cookie_grants_api(fresh_client, temp_dir):
    """The full browser flow: tokenized URL, then cookie-authenticated API calls."""
    with patch("ui.server.load_config", return_value=_mock_config(temp_dir, ui_token=TOKEN)):
        landing = fresh_client.get("/", params={"token": TOKEN})  # follows the 303
        api = fresh_client.get("/api/state")  # cookie jar carries the session
    assert landing.status_code == 200
    assert api.status_code == 200


# ── Token mode: rejection paths ──────────────────────────────────────────────


def test_token_wrong_credentials_rejected(temp_dir):
    cfg = _mock_config(temp_dir, ui_token=TOKEN)
    with patch("ui.server.load_config", return_value=cfg):
        bad_bearer = TestClient(app).get(
            "/api/state", headers={"Authorization": "Bearer wrong-token"}
        )
        bad_query = TestClient(app).get(
            "/", params={"token": "wrong-token"}, follow_redirects=False
        )
        bad_cookie_client = TestClient(app, cookies={COOKIE_NAME: "wrong-token"})
        bad_cookie = bad_cookie_client.get("/api/state")
    assert bad_bearer.status_code == 401
    assert bad_query.status_code == 401
    assert COOKIE_NAME not in bad_query.headers.get("set-cookie", "")
    assert bad_cookie.status_code == 401


def test_token_query_not_accepted_on_api(fresh_client, temp_dir):
    """?token= is a /-only channel: API URLs must never carry secrets into logs."""
    with patch("ui.server.load_config", return_value=_mock_config(temp_dir, ui_token=TOKEN)):
        response = fresh_client.get("/api/state", params={"token": TOKEN})
    assert response.status_code == 401


# ── Config resolution ────────────────────────────────────────────────────────


def test_env_var_overrides_config(monkeypatch, fresh_client):
    """AUTODEV_UI_TOKEN (stripped) wins over ui/config.json, like AUTODEV_HOOKS_TOKEN.

    Uses the real load_config() (no patch) so the env-override branch itself is
    exercised; '/' is the probe because it depends on no pipeline state.
    """
    monkeypatch.setenv("AUTODEV_UI_TOKEN", "  env-secret-token  ")
    denied = fresh_client.get("/")
    granted = fresh_client.get("/", headers={"Authorization": "Bearer env-secret-token"})
    assert denied.status_code == 401
    assert granted.status_code == 200


def test_non_string_token_treated_unset(fresh_client, temp_dir):
    """Malformed config degrades to auth-off instead of 500ing every request."""
    with patch("ui.server.load_config", return_value=_mock_config(temp_dir, ui_token=12345)):
        api = fresh_client.get("/api/state")
        root = fresh_client.get("/")
    assert api.status_code == 200
    assert root.status_code == 200


def test_config_sourced_token_neutralized_but_env_token_enforced(monkeypatch):
    """Hermeticity guard for the conftest ``_neutralize_ui_token_auth`` autouse fixture.

    A ``ui_token`` coming from ``ui/config.json`` (with no ``AUTODEV_UI_TOKEN`` env) is blanked in
    the test environment, so a developer machine that has dashboard auth configured cannot ``401``
    the whole suite. An *env*-sourced token is left intact — that is the env-wins branch
    ``test_env_var_overrides_config`` depends on. Pins the config-vs-env distinction so a future
    refactor of the fixture cannot silently re-break test hermeticity (or, worse, disable the
    env-token enforcement the auth contract relies on)."""
    import ui.server as srv

    # No AUTODEV_UI_TOKEN (autouse _scrub_autodev_env cleared it): whatever token the real
    # ui/config.json carries is wrapped away to "" by the fixture.
    assert srv.load_config().get("ui_token", "") == ""

    # With the env var set, the fixture must NOT strip it — load_config's env-wins path stands.
    monkeypatch.setenv("AUTODEV_UI_TOKEN", "env-secret")
    assert srv.load_config().get("ui_token") == "env-secret"
