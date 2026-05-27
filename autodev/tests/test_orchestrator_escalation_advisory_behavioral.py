"""P0 Stage G — escalation advisory consumes behavioural context (fallback path).

The escalation advisory is the FALLBACK consumer of Stage G behavioural data —
the primary consumer is the executor's reviewer-rejection retry. The advisory
fires only after the executor's self-heal passes have been attempted, which
the orchestrator gates on ``reviewer_retries >= 2``.

Two contract points enforced here:

  1. The ``_user_message`` payload sent to the LLM carries a top-level
     ``behavioral_verification: {failure_language, verdict, evidence_count}``
     block — but ONLY when ``reviewer_retries >= 2``. Below that threshold,
     the block is ``None`` so the LLM has no behavioural language to quote.
     This is the data-level gating (load-bearing); the system prompt also
     names the rule, but the prompt is constant.
  2. The advisory reads ``failure_language`` from
     ``failure_context.current_phase_behavioral_verification.failure_language``,
     NOT from a fresh read of ``current_phase.json``. The advisory and the
     executor's self-heal pass thus share a single source of truth.

Mirrors the test pattern from ``test_orchestrator_escalation_advisory.py``
(``_make_test_orchestrator`` + mocked ``requests.post``).
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _make_test_orchestrator(tmp_dir: str):
    """Bare orchestrator with paths wired into tmp_dir. Mirrors the helper in
    ``test_orchestrator_escalation_advisory.py``."""
    import orchestrator as orc_module

    state_file = os.path.join(tmp_dir, "pipeline_state.json")
    lock_file = os.path.join(tmp_dir, "pipeline.lock")
    config_file = os.path.join(tmp_dir, "openclaw.json")
    phase_state_file = os.path.join(tmp_dir, "phase_state.json")

    openclaw_cfg = {
        "hooks": {"token": "test-tok"},
        "models": {"providers": {"llama-local": {"baseUrl": "http://localhost:11434/v1"}}},
    }
    with open(config_file, "w") as f:
        json.dump(openclaw_cfg, f)

    with (
        patch.object(orc_module, "STATE_FILE", state_file),
        patch.object(orc_module, "SYMLINK_TARGET", tmp_dir),
        patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", tmp_dir),
        patch.object(orc_module, "LOCK_FILE", lock_file),
        patch.object(orc_module, "CONFIG_FILE", config_file),
        patch.object(orc_module, "PHASE_STATE_FILE", phase_state_file),
    ):
        from orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch.lock_fd = None
        orch.openclaw_config = openclaw_cfg
        orch.state = {
            "current_phase": 1,
            "current_phase_raw_id": "CORE-E6",
            "current_agent": "escalation",
            "pipeline_status": "WAITING_FOR_HUMAN",
            "last_action": "reviewer reset cap reached",
        }
    return orch


def _llm_response_stub():
    """Build a mock requests.Response that returns a syntactically-valid
    advisory JSON. We do not care about the LLM output here — we inspect the
    PAYLOAD we sent."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [
            {"message": {"content": json.dumps({"summary": "x", "recommended_action": "y"})}}
        ]
    }
    return mock_resp


def _write_failure_context(tmp_path, claimed_failure_language=None, observed_verdict=None,
                           observed_evidence_count=0):
    """Write a failure_context.json with optional behavioural fields."""
    fc = {
        "timestamp": "2026-05-22T00:00:00Z",
        "phase_raw_id": "CORE-E6",
        "failing_agent": "reviewer",
        "attempt_number": 3,
        "gate_error_codes": ["ERR_VALIDATION_FAILED"],
        "blocking_issues": [],
        "executor_retries_at_failure": 1,
        "reviewer_retries_at_failure": 2,
        "prior_blame_attributions": [],
    }
    if claimed_failure_language is not None:
        fc["current_phase_behavioral_verification"] = {
            "user_observable": "User sees X.",
            "how_to_check": "Run foo.",
            "failure_language": claimed_failure_language,
        }
    if observed_verdict is not None:
        fc["behavioral_verification_evidence"] = {
            "verdict": observed_verdict,
            "how_to_check_followed": True,
            "evidence": [{"claim": f"c{i}"} for i in range(observed_evidence_count)],
        }
    (tmp_path / "failure_context.json").write_text(json.dumps(fc))


def _write_phase_state(tmp_path, reviewer_retries=0):
    """Write phase_state.json with the supplied reviewer_retries count."""
    (tmp_path / "phase_state.json").write_text(json.dumps({
        "escalation_trigger_reason": "Reviewer reset cap",
        "escalation_resets": 0,
        "executor_retries": 1,
        "reviewer_retries": reviewer_retries,
        "prior_blame_attributions": [],
    }))


def _capture_post_payload():
    """Return (mock_post, captured_dict) for use as patch target.
    captured_dict['payload'] = body of the requests.post call."""
    captured = {}

    def fake_post(*args, **kwargs):
        captured["payload"] = kwargs.get("json")
        return _llm_response_stub()

    return fake_post, captured


class TestEscalationAdvisoryBehavioralPayload:
    """Behavioural data flow into the escalation advisory payload."""

    def test_user_message_includes_behavioral_verification_block_when_reviewer_retries_ge_2(
        self, tmp_path
    ):
        """When the advisory fires after the executor's self-heal passes
        (``reviewer_retries >= 2``) AND a behavioural failure language is
        available, the payload's ``behavioral_verification`` key must carry
        the failure language + observed verdict + evidence count summary."""
        import orchestrator as orc_module

        orch = _make_test_orchestrator(str(tmp_path))
        _write_failure_context(
            tmp_path,
            claimed_failure_language="The /tasks page did not load.",
            observed_verdict="fail",
            observed_evidence_count=3,
        )
        _write_phase_state(tmp_path, reviewer_retries=2)

        fake_post, captured = _capture_post_payload()
        with (
            patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path)),
            patch.object(orc_module, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json")),
            patch("requests.post", side_effect=fake_post),
        ):
            orch._generate_escalation_advisory()

        assert captured.get("payload") is not None, "advisory must POST to the LLM"
        msgs = captured["payload"].get("messages") or []
        user_msg = next((m["content"] for m in msgs if m.get("role") == "user"), None)
        assert user_msg is not None, "advisory must include a user message"
        payload = json.loads(user_msg)
        bv = payload.get("behavioral_verification")
        assert isinstance(bv, dict), (
            "user_message.behavioral_verification must be a dict when "
            "reviewer_retries >= 2 AND failure_language is present — otherwise "
            "the advisory has no project-specific language to surface to the "
            "operator"
        )
        assert bv.get("failure_language") == "The /tasks page did not load.", (
            "failure_language must be preserved verbatim — the operator wants "
            "the project's own pre-authored sentence, not an LLM paraphrase"
        )
        assert bv.get("verdict") == "fail"
        assert bv.get("evidence_count") == 3

    def test_user_message_behavioral_block_is_none_when_reviewer_retries_lt_2(
        self, tmp_path
    ):
        """Below the self-heal-attempted threshold, the advisory must NOT carry
        the behavioural block — even if the data is available. This enforces
        the principle that escalation is the FALLBACK consumer; the executor's
        self-heal path is primary and goes first."""
        import orchestrator as orc_module

        orch = _make_test_orchestrator(str(tmp_path))
        _write_failure_context(
            tmp_path,
            claimed_failure_language="The /tasks page did not load.",
            observed_verdict="fail",
            observed_evidence_count=3,
        )
        _write_phase_state(tmp_path, reviewer_retries=1)  # below threshold

        fake_post, captured = _capture_post_payload()
        with (
            patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path)),
            patch.object(orc_module, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json")),
            patch("requests.post", side_effect=fake_post),
        ):
            orch._generate_escalation_advisory()

        msgs = captured["payload"].get("messages") or []
        user_msg = next((m["content"] for m in msgs if m.get("role") == "user"), None)
        payload = json.loads(user_msg)
        assert payload.get("behavioral_verification") is None, (
            "advisory must not carry behavioural language when "
            "reviewer_retries < 2 — that condition means self-heal has not been "
            "attempted yet, and quoting failure_language prematurely would "
            "skip the executor's targeted self-heal pass"
        )

    def test_system_prompt_instructs_verbatim_failure_language_quote(self, tmp_path):
        """The system prompt must name the conditional quoting rule. The data
        gating (block None below threshold) is what actually flips behaviour,
        but the prompt has to explain the contract to the LLM so it knows what
        to do with the data when it's present."""
        import orchestrator as orc_module

        orch = _make_test_orchestrator(str(tmp_path))
        _write_failure_context(
            tmp_path,
            claimed_failure_language="The /tasks page did not load.",
            observed_verdict="fail",
            observed_evidence_count=3,
        )
        _write_phase_state(tmp_path, reviewer_retries=2)

        fake_post, captured = _capture_post_payload()
        with (
            patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path)),
            patch.object(orc_module, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json")),
            patch("requests.post", side_effect=fake_post),
        ):
            orch._generate_escalation_advisory()

        msgs = captured["payload"].get("messages") or []
        system_msg = next((m["content"] for m in msgs if m.get("role") == "system"), "")
        assert "failure_language" in system_msg, (
            "system prompt must mention failure_language so the LLM knows the "
            "data shape it might receive in the user payload"
        )
        assert "reviewer_retries >= 2" in system_msg, (
            "system prompt must name the reviewer_retries >= 2 trigger so the "
            "LLM does not invent the contract — the rule is contractual, the "
            "data gating enforces it, but the prompt explains it"
        )

    def test_advisory_failure_language_sourced_from_failure_context_not_current_phase(
        self, tmp_path
    ):
        """The advisory reads failure_language from the *materialised snapshot*
        in failure_context.json, not from a fresh read of current_phase.json.
        This pins that the advisory and the executor's self-heal pass share
        a single source of truth — both see what was true at failure time."""
        import orchestrator as orc_module

        orch = _make_test_orchestrator(str(tmp_path))
        # failure_context has language "X" (materialised snapshot at failure time)
        _write_failure_context(
            tmp_path,
            claimed_failure_language="X",
            observed_verdict="fail",
            observed_evidence_count=1,
        )
        # current_phase.json has language "Y" (could happen if phase rolled over)
        (tmp_path / "current_phase.json").write_text(json.dumps({
            "raw_id": "CORE-E6",
            "behavioral_verification": {
                "user_observable": "User sees X.",
                "how_to_check": "Run foo.",
                "failure_language": "Y",
            },
        }))
        _write_phase_state(tmp_path, reviewer_retries=2)

        fake_post, captured = _capture_post_payload()
        with (
            patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path)),
            patch.object(orc_module, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json")),
            patch("requests.post", side_effect=fake_post),
        ):
            orch._generate_escalation_advisory()

        msgs = captured["payload"].get("messages") or []
        user_msg = next((m["content"] for m in msgs if m.get("role") == "user"), None)
        payload = json.loads(user_msg)
        bv = payload.get("behavioral_verification")
        assert isinstance(bv, dict)
        assert bv.get("failure_language") == "X", (
            "failure_language must be read from failure_context.json (the "
            "materialised snapshot), not from a fresh read of current_phase.json. "
            "Reading current_phase fresh would surface stale-or-rolled-over "
            "language and desync the advisory from the executor's view"
        )
