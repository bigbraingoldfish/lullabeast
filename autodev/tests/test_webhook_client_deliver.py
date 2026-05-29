"""Pipeline webhooks must be file-only (deliver=False) for planner/executor/reviewer.

Completion is detected via sentinel files, not channel delivery.  Without
``deliver=False`` the gateway tries to deliver every agent reply to the bound
Signal channel and marks the run errored ("Delivering to Signal requires
target").  Escalation is the sole exception: it sends a real Signal
notification, so it must keep the default delivery behaviour.
"""

from unittest.mock import MagicMock, patch

import webhook_client as wc


def _extract_json_from_post(mock_post):
    assert mock_post.called
    _args, kwargs = mock_post.call_args
    return kwargs.get("json") or {}


def test_planner_webhook_is_file_only():
    with patch.object(wc.requests, "post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        wc.invoke_agent_webhook("planner", "pipeline:phase-1:x:planner-attempt-1", "tok")
        body = _extract_json_from_post(mock_post)
        assert body.get("deliver") is False


def test_executor_webhook_is_file_only():
    with patch.object(wc.requests, "post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        wc.invoke_agent_webhook("executor", "pipeline:phase-1:x:executor-attempt-1", "tok")
        body = _extract_json_from_post(mock_post)
        assert body.get("deliver") is False


def test_reviewer_webhook_is_file_only():
    with patch.object(wc.requests, "post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        wc.invoke_agent_webhook("reviewer", "pipeline:phase-1:x:reviewer-attempt-1", "tok")
        body = _extract_json_from_post(mock_post)
        assert body.get("deliver") is False


def test_escalation_webhook_still_delivers():
    """Escalation sends the human a Signal message; it must NOT be file-only."""
    with patch.object(wc.requests, "post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        wc.invoke_agent_webhook("escalation", "pipeline:phase-1:x:escalation", "tok")
        body = _extract_json_from_post(mock_post)
        assert body.get("deliver") is not False
