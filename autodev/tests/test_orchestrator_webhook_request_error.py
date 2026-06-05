"""Phase 2 (Remote-Call Resilience) — orchestrator consumption of webhook results.

Covers:
- T2.2: REQUEST_ERROR (deterministic 4xx) is labelled honestly, distinct from
  AUTH_ERROR and infra failure, via the module-level `webhook_failure_reason`
  helper used at the planner-invocation branch.
- T2.1: the webhook call sites forward `url=self.openclaw_config["hooks_url"]`
  (so a non-default gateway port is actually used).
- T2.4: `send_signal_notification` bounds its POST with `timeout=15` and posts
  to the configured `hooks_url`, not hardcoded localhost.
"""

import os
import sys
from unittest.mock import MagicMock, patch

PIPELINE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "autodev", "pipeline",
)
if PIPELINE_DIR not in sys.path:
    sys.path.insert(0, PIPELINE_DIR)

import orchestrator as orc  # noqa: E402


# ---------------------------------------------------------------------------
# T2.2 — honest failure-reason labelling
# ---------------------------------------------------------------------------

def test_webhook_failure_reason_distinguishes_request_error():
    assert orc.webhook_failure_reason("AUTH_ERROR") == "Auth Config Error"
    # REQUEST_ERROR must NOT be labelled as an infra failure — it is a
    # deterministic config/shape bug, and the activity feed should say so.
    reason = orc.webhook_failure_reason("REQUEST_ERROR")
    assert reason != "Webhook infra failure"
    assert "request" in reason.lower() or "config" in reason.lower()
    # Anything else (INFRA_ERROR or an unknown token) stays the infra label.
    assert orc.webhook_failure_reason("INFRA_ERROR") == "Webhook infra failure"


# ---------------------------------------------------------------------------
# T2.1 — call sites forward the configured hooks_url
# ---------------------------------------------------------------------------

_HOOKS_URL = "http://127.0.0.1:9999/hooks/agent"


def _mock_orch_with_url():
    m = MagicMock()
    m.openclaw_config = {"hooks_url": _HOOKS_URL}
    m.read_phase_state.return_value = {}
    return m


def test_invoke_reviewer_forwards_hooks_url():
    captured = {}

    def fake_webhook(agent_id, session_key, token, **kwargs):
        captured.update(kwargs)
        return "SUCCESS"

    with patch("orchestrator.invoke_agent_webhook", side_effect=fake_webhook):
        orc.Orchestrator._invoke_reviewer(_mock_orch_with_url(), "sk", "tok")
    assert captured.get("url") == _HOOKS_URL


def test_invoke_executor_forwards_hooks_url():
    captured = {}

    def fake_webhook(agent_id, session_key, token, **kwargs):
        captured.update(kwargs)
        return "SUCCESS"

    with patch("orchestrator.invoke_agent_webhook", side_effect=fake_webhook):
        orc.Orchestrator._invoke_executor(_mock_orch_with_url(), "sk", "tok")
    assert captured.get("url") == _HOOKS_URL


def test_completion_review_forwards_hooks_url():
    captured = {}

    def fake_webhook(agent_id, session_key, token, **kwargs):
        captured.update(kwargs)

    m = MagicMock()
    m.openclaw_config = {"hooks_url": _HOOKS_URL}
    m.skill_manager.inject_skill.return_value = None

    with patch("orchestrator.invoke_agent_webhook", side_effect=fake_webhook), \
            patch("orchestrator.poll_for_sentinel", return_value=True):
        orc._run_completion_review(m, project_basename="proj")
    assert captured.get("url") == _HOOKS_URL


def test_completion_review_tolerates_missing_hooks_url():
    """A call site reading the url must use .get() so an empty config (as several
    existing tests pass) does not KeyError before the webhook fires."""
    m = MagicMock()
    m.openclaw_config = {}
    m.skill_manager.inject_skill.return_value = None
    with patch("orchestrator.invoke_agent_webhook", return_value=None) as mw, \
            patch("orchestrator.poll_for_sentinel", return_value=True):
        orc._run_completion_review(m, project_basename="proj")
    assert mw.called  # reached the webhook without raising


# ---------------------------------------------------------------------------
# T2.4 — raw signal notification is bounded and uses the configured url
# ---------------------------------------------------------------------------

def test_send_signal_notification_is_timed_and_uses_hooks_url():
    m = MagicMock()
    m.openclaw_config = {"hooks_url": _HOOKS_URL, "hooks": {"token": "tok"}}

    with patch("orchestrator.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        orc.Orchestrator.send_signal_notification(m, "hello operator")

    assert mock_post.called
    args, kwargs = mock_post.call_args
    assert kwargs.get("timeout") == 15
    assert args[0] == _HOOKS_URL
