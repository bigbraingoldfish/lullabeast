"""Pipeline webhook must omit ``model`` by default so OpenClaw uses agent config (like Ideas)."""

from unittest.mock import MagicMock, patch

import webhook_client as wc


def _extract_json_from_post(mock_post):
    assert mock_post.called
    _args, kwargs = mock_post.call_args
    return kwargs.get("json") or {}


def test_invoke_agent_webhook_omits_model_when_not_passed():
    with patch.object(wc.requests, "post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        wc.invoke_agent_webhook("planner", "pipeline:phase-1:x:planner-attempt-1", "tok")
        body = _extract_json_from_post(mock_post)
        assert "model" not in body
        assert body.get("agentId") == "planner"
        assert body.get("sessionKey") == "pipeline:phase-1:x:planner-attempt-1"


def test_invoke_agent_webhook_includes_model_when_explicitly_passed():
    with patch.object(wc.requests, "post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        wc.invoke_agent_webhook(
            "executor",
            "pipeline:phase-1:x:executor-attempt-1",
            "tok",
            model="openrouter/vendor/some-model",
        )
        body = _extract_json_from_post(mock_post)
        assert body.get("model") == "openrouter/vendor/some-model"
