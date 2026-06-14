"""Part B1 — escalation correlation token.

A correlation token couples an outbound escalation notification to the project
that escalated, so an inbound operator reply (POST /api/escalation/inbound) can
be routed back to THAT project regardless of which project is active when the
reply lands (the B0 project-boundedness guarantee).

Covers:
- ``_build_escalation_webhook_message(reply_token=...)`` appends the token + an
  inbound-reply instruction when present, and omits it when None (back-compat:
  the repo-init caller passes no token).
- ``_prepare_escalation_reply_token()`` builds an ``{entry_id}.{nonce}`` token,
  persists it to phase_state.json, and is best-effort about both the entry id
  (run-scoped fallback) and the phase_state write.
"""

import os
import re
import sys
from unittest.mock import MagicMock

PIPELINE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "autodev", "pipeline",
)
if PIPELINE_DIR not in sys.path:
    sys.path.insert(0, PIPELINE_DIR)

import orchestrator as orc  # noqa: E402


# ---------------------------------------------------------------------------
# Webhook message threading
# ---------------------------------------------------------------------------

def test_build_escalation_message_appends_token_when_present():
    m = MagicMock()
    msg = orc.Orchestrator._build_escalation_webhook_message(m, reply_token="e1.ab12cd")
    # the literal token is present so the agent can echo it verbatim
    assert "e1.ab12cd" in msg
    assert "correlation token" in msg.lower()
    # the agent still does not apply commands itself
    assert "do not apply commands" in msg.lower()


def test_build_escalation_message_omits_token_when_none():
    m = MagicMock()
    default_msg = orc.Orchestrator._build_escalation_webhook_message(m)
    none_msg = orc.Orchestrator._build_escalation_webhook_message(m, reply_token=None)
    # default param is None — back-compat for the repo-init caller
    assert default_msg == none_msg
    assert "correlation token" not in default_msg.lower()
    # the core escalation framing is unchanged
    assert "escalation" in default_msg.lower()


# ---------------------------------------------------------------------------
# Token generation + persistence
# ---------------------------------------------------------------------------

def _mock_orch_for_token(entry, phase_state=None):
    m = MagicMock()
    m._read_queue.return_value = {"queue": [entry] if entry else []}
    m._find_active_queue_entry.return_value = (0, entry) if entry else (None, None)
    m.read_phase_state.return_value = dict(phase_state or {})
    return m


def test_prepare_reply_token_uses_entry_id_prefix_and_persists():
    m = _mock_orch_for_token({"id": "e1", "state": "ACTIVE"})
    stored = {}
    m.write_phase_state_atomic.side_effect = lambda ps: stored.update(ps)

    token = orc.Orchestrator._prepare_escalation_reply_token(m)

    assert re.match(r"^e1\.[0-9a-f]{6}$", token), token
    assert stored.get("escalation_reply_token") == token
    assert stored.get("escalation_reply_token_at")


def test_prepare_reply_token_preserves_existing_phase_state_keys():
    m = _mock_orch_for_token({"id": "e1", "state": "ACTIVE"},
                             phase_state={"escalation_resets": 2})
    stored = {}
    m.write_phase_state_atomic.side_effect = lambda ps: stored.update(ps)
    orc.Orchestrator._prepare_escalation_reply_token(m)
    # read-modify-write must not drop sibling keys
    assert stored.get("escalation_resets") == 2


def test_prepare_reply_token_falls_back_to_run_prefix_without_entry():
    m = _mock_orch_for_token(None)
    token = orc.Orchestrator._prepare_escalation_reply_token(m)
    assert re.match(r"^run\.[0-9a-f]{6}$", token), token


def test_prepare_reply_token_persist_failure_still_returns_token():
    """Best-effort persistence: a phase_state write failure must not crash the
    escalation dispatch — the token is still returned (and rides the notification)."""
    m = _mock_orch_for_token(None)
    m.read_phase_state.side_effect = RuntimeError("disk gone")
    token = orc.Orchestrator._prepare_escalation_reply_token(m)
    assert token.startswith("run.")
