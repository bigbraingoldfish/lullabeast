"""Phase 2 (Remote-Call Resilience) — webhook_client hardening.

Covers roadmap tasks T2.1 (configurable url), T2.2 (non-retryable 4xx →
REQUEST_ERROR), and T2.3 ((connect, read) timeout tuple + per-call idempotency
key). The retry/backoff/AUTH_ERROR/INFRA_ERROR contract for the *transient*
classes (429 / 5xx) is asserted unchanged so the new 4xx branch cannot silently
swallow a self-healing rate-limit.
"""

from unittest.mock import MagicMock, patch

import webhook_client as wc


def _post_kwargs(mock_post, idx=0):
    return mock_post.call_args_list[idx].kwargs


def _post_url(mock_post, idx=0):
    # invoke_agent_webhook calls requests.post(url, headers=..., json=..., timeout=...)
    return mock_post.call_args_list[idx].args[0]


# ---------------------------------------------------------------------------
# T2.1 — configurable URL (default preserved)
# ---------------------------------------------------------------------------

def test_url_param_is_honored():
    with patch.object(wc.requests, "post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        wc.invoke_agent_webhook(
            "planner", "pipeline:phase-1:x:planner-attempt-1", "tok",
            url="http://127.0.0.1:9999/hooks/agent",
        )
        assert _post_url(mock_post) == "http://127.0.0.1:9999/hooks/agent"


def test_url_default_unchanged():
    with patch.object(wc.requests, "post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        wc.invoke_agent_webhook("planner", "pipeline:phase-1:x:planner-attempt-1", "tok")
        assert _post_url(mock_post) == "http://localhost:18789/hooks/agent"


# ---------------------------------------------------------------------------
# T2.2 — deterministic 4xx fail fast as REQUEST_ERROR (no retry)
# ---------------------------------------------------------------------------

def test_deterministic_4xx_returns_request_error_without_retry():
    for status in (400, 404, 422):
        with patch.object(wc.requests, "post") as mock_post, \
                patch.object(wc.time, "sleep") as mock_sleep:
            mock_post.return_value = MagicMock(status_code=status)
            result = wc.invoke_agent_webhook(
                "planner", "pipeline:phase-1:x:planner-attempt-1", "tok"
            )
            assert result == "REQUEST_ERROR", status
            assert mock_post.call_count == 1, status  # no retry
            assert mock_sleep.call_count == 0, status


# ---------------------------------------------------------------------------
# T2.2 — regression guard: existing classifications unchanged. 429 (rate-limit)
# and 5xx still RETRY (self-healing preserved); 401/403 still AUTH_ERROR; 200
# still SUCCESS. A reorder that put the 4xx branch ahead of the 429 branch would
# fail here.
# ---------------------------------------------------------------------------

def test_auth_error_unchanged():
    for status in (401, 403):
        with patch.object(wc.requests, "post") as mock_post, \
                patch.object(wc.time, "sleep"):
            mock_post.return_value = MagicMock(status_code=status)
            result = wc.invoke_agent_webhook("planner", "k", "tok")
            assert result == "AUTH_ERROR", status
            assert mock_post.call_count == 1, status


def test_rate_limit_429_still_retries_then_infra_error():
    with patch.object(wc.requests, "post") as mock_post, \
            patch.object(wc.time, "sleep") as mock_sleep:
        mock_post.return_value = MagicMock(status_code=429)
        result = wc.invoke_agent_webhook("planner", "k", "tok")
        assert result == "INFRA_ERROR"
        assert mock_post.call_count == 3      # retried, not fast-failed
        assert mock_sleep.call_count == 2     # 30s between the 3 attempts


def test_server_5xx_still_retries_then_infra_error():
    with patch.object(wc.requests, "post") as mock_post, \
            patch.object(wc.time, "sleep"):
        mock_post.return_value = MagicMock(status_code=503)
        result = wc.invoke_agent_webhook("planner", "k", "tok")
        assert result == "INFRA_ERROR"
        assert mock_post.call_count == 3


def test_success_unchanged():
    with patch.object(wc.requests, "post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        result = wc.invoke_agent_webhook("planner", "k", "tok")
        assert result == "SUCCESS"
        assert mock_post.call_count == 1


# ---------------------------------------------------------------------------
# T2.3 — (connect, read) timeout tuple + idempotency key
# ---------------------------------------------------------------------------

def test_timeout_is_connect_read_tuple():
    with patch.object(wc.requests, "post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        wc.invoke_agent_webhook("planner", "k", "tok")
        assert _post_kwargs(mock_post).get("timeout") == (5, 30)


def test_idempotency_key_present_and_stable_across_retries():
    """A read-timeout retry must carry the SAME idempotencyKey so OpenClaw's
    replay cache dedups it (no double-enqueue). Force 500->500->200 (3 posts in
    one call) and assert one shared, non-empty key."""
    with patch.object(wc.requests, "post") as mock_post, \
            patch.object(wc.time, "sleep"):
        mock_post.side_effect = [
            MagicMock(status_code=500),
            MagicMock(status_code=500),
            MagicMock(status_code=200),
        ]
        result = wc.invoke_agent_webhook("planner", "k", "tok")
        assert result == "SUCCESS"
        keys = [_post_kwargs(mock_post, i)["json"]["idempotencyKey"] for i in range(3)]
        assert all(keys), "idempotencyKey must be non-empty on every attempt"
        assert len(set(keys)) == 1, "key must be stable across retries of one call"


def test_idempotency_key_unique_per_call():
    """Two distinct invocations (e.g. attempt-1 vs attempt-2) must NOT dedup
    against each other."""
    keys = []
    for _ in range(2):
        with patch.object(wc.requests, "post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            wc.invoke_agent_webhook("planner", "k", "tok")
            keys.append(_post_kwargs(mock_post)["json"]["idempotencyKey"])
    assert keys[0] != keys[1]
