"""P1 Stage G1 — the escalation advisory must not be fed blame-framed input,
and must surface the project's user-voice ``failure_language`` on ALL escalations
(not only the reviewer-rejection ones).

Three contract points enforced here:

  1. The ``_user_message`` payload sent to the LLM no longer carries the
     blame-framed keys ``escalation_trigger_reason`` or
     ``prior_blame_attributions``. The advisory grounds its summary in the actual
     failure (``failure_context``), the project's pre-authored ``failure_language``,
     and the retry counts — never in internal blame-attribution jargon.
  2. The ``failure_language`` gate is loosened: the behavioural block is built
     whenever ``failure_context`` carries a ``failure_language`` string,
     regardless of ``reviewer_retries``. Executor-self-failure escalations (which
     have ``reviewer_retries < 2``) now get the user-voice copy too — they used to
     be denied it because the advisory parroted blame jargon instead.
  3. The system prompt instructs the LLM to quote ``failure_language`` whenever the
     behavioural block is present — it no longer names a ``reviewer_retries >= 2``
     precondition.

Mirrors the test pattern from ``test_orchestrator_escalation_advisory_behavioral.py``
(``_make_test_orchestrator`` + mocked ``requests.post`` + ``_capture_post_payload``).
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

# A representative blame-cap string — exactly the kind of internal jargon that
# must NOT reach the advisory LLM or the operator.
BLAME_STRING = (
    "Impl blame cap reached (4x): [L3] Insufficient evidence for confident "
    "attribution; defaulting to impl."
)


def _make_test_orchestrator(tmp_dir: str):
    """Bare orchestrator with paths wired into tmp_dir. Mirrors the helper in
    ``test_orchestrator_escalation_advisory_behavioral.py``."""
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
            "last_action": BLAME_STRING,
        }
    return orch


def _llm_response_stub():
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
    fc = {
        "timestamp": "2026-05-29T00:00:00Z",
        "phase_raw_id": "CORE-E6",
        "failing_agent": "executor",
        "attempt_number": 4,
        "gate_error_codes": ["ERR_VALIDATION_FAILED"],
        "blocking_issues": [],
        "executor_retries_at_failure": 3,
        "reviewer_retries_at_failure": 0,
        "prior_blame_attributions": ["impl", "impl", "impl"],
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


def _write_phase_state(tmp_path, reviewer_retries=0,
                       prior_blame_attributions=("impl", "impl", "impl"),
                       escalation_trigger_reason=BLAME_STRING):
    (tmp_path / "phase_state.json").write_text(json.dumps({
        "escalation_trigger_reason": escalation_trigger_reason,
        "escalation_resets": 0,
        "executor_retries": 3,
        "reviewer_retries": reviewer_retries,
        "prior_blame_attributions": list(prior_blame_attributions),
    }))


def _capture_post_payload():
    captured = {}

    def fake_post(*args, **kwargs):
        captured["payload"] = kwargs.get("json")
        return _llm_response_stub()

    return fake_post, captured


def _user_payload(captured):
    """Parse the JSON user-message that was POSTed to the advisory LLM."""
    msgs = (captured.get("payload") or {}).get("messages") or []
    user_msg = next((m["content"] for m in msgs if m.get("role") == "user"), None)
    assert user_msg is not None, "advisory must POST a user message"
    return json.loads(user_msg)


def _system_message(captured):
    msgs = (captured.get("payload") or {}).get("messages") or []
    return next((m["content"] for m in msgs if m.get("role") == "system"), "")


def _run_advisory(orch, tmp_path, fake_post):
    import orchestrator as orc_module
    with (
        patch.object(orc_module, "PROJECT_ARTIFACTS_DIR", str(tmp_path)),
        patch.object(orc_module, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json")),
        patch("requests.post", side_effect=fake_post),
    ):
        orch._generate_escalation_advisory()


class TestAdvisoryPayloadDeblame:
    """The advisory LLM input must be free of blame-attribution jargon."""

    def test_advisory_payload_omits_prior_blame_attributions(self, tmp_path):
        """Blame history is internal routing state, not user-facing failure
        context. It must not be forwarded to the advisory LLM. Fails today: the
        ``prior_blame_attributions`` key is present in the payload."""
        orch = _make_test_orchestrator(str(tmp_path))
        _write_failure_context(tmp_path)
        _write_phase_state(tmp_path, prior_blame_attributions=["impl", "impl", "impl"])

        fake_post, captured = _capture_post_payload()
        _run_advisory(orch, tmp_path, fake_post)

        payload = _user_payload(captured)
        assert "prior_blame_attributions" not in payload, (
            "advisory payload must not carry prior_blame_attributions — feeding "
            "blame history makes the summary parrot internal attribution jargon"
        )

    def test_advisory_payload_omits_escalation_trigger_reason(self, tmp_path):
        """The raw escalation_trigger_reason is, in the dominant case, the
        blame-cap string. It must not be fed to the advisory LLM. Fails today:
        the ``escalation_trigger_reason`` key is present in the payload."""
        orch = _make_test_orchestrator(str(tmp_path))
        _write_failure_context(tmp_path)
        _write_phase_state(tmp_path, escalation_trigger_reason=BLAME_STRING)

        fake_post, captured = _capture_post_payload()
        _run_advisory(orch, tmp_path, fake_post)

        payload = _user_payload(captured)
        assert "escalation_trigger_reason" not in payload, (
            "advisory payload must not carry escalation_trigger_reason — it is "
            "the blame-cap string in the common case and pollutes the summary"
        )
        # Belt-and-braces: the blame text must not appear anywhere in the payload.
        assert BLAME_STRING not in json.dumps(payload), (
            "the blame-cap string must not reach the advisory LLM through any key"
        )


class TestFailureLanguageGateLoosened:
    """failure_language must reach the advisory on executor-self-failure
    escalations (reviewer_retries < 2), not only reviewer-rejection ones."""

    def test_advisory_payload_includes_failure_language_when_reviewer_retries_lt_2(self, tmp_path):
        """LOOSENED GATE (new behavior). With reviewer_retries=1 — an
        executor-self-failure escalation — and a populated failure_language, the
        payload's behavioral_verification block must carry that language. Fails
        today: the old gate returns None below reviewer_retries >= 2."""
        orch = _make_test_orchestrator(str(tmp_path))
        _write_failure_context(
            tmp_path,
            claimed_failure_language="The /tasks page did not load.",
            observed_verdict="fail",
            observed_evidence_count=3,
        )
        _write_phase_state(tmp_path, reviewer_retries=1)  # below the OLD threshold

        fake_post, captured = _capture_post_payload()
        _run_advisory(orch, tmp_path, fake_post)

        payload = _user_payload(captured)
        bv = payload.get("behavioral_verification")
        assert isinstance(bv, dict), (
            "behavioral_verification must be present whenever failure_language "
            "exists — even on executor-self-failure escalations (reviewer_retries "
            "< 2). The operator deserves the project's own user-voice copy "
            "regardless of which agent failed"
        )
        assert bv.get("failure_language") == "The /tasks page did not load."
        assert bv.get("verdict") == "fail"
        assert bv.get("evidence_count") == 3

    def test_system_prompt_quotes_failure_language_without_retry_condition(self, tmp_path):
        """The system prompt must still name failure_language but must NOT name a
        ``reviewer_retries >= 2`` precondition (removed by the loosened gate).
        Fails today: the prompt names that condition."""
        orch = _make_test_orchestrator(str(tmp_path))
        _write_failure_context(
            tmp_path,
            claimed_failure_language="The /tasks page did not load.",
            observed_verdict="fail",
            observed_evidence_count=3,
        )
        _write_phase_state(tmp_path, reviewer_retries=1)

        fake_post, captured = _capture_post_payload()
        _run_advisory(orch, tmp_path, fake_post)

        system_msg = _system_message(captured)
        assert "failure_language" in system_msg, (
            "system prompt must still name failure_language so the LLM knows the "
            "data shape it may receive"
        )
        assert "reviewer_retries >= 2" not in system_msg, (
            "system prompt must no longer gate failure_language on "
            "reviewer_retries >= 2 — the gate was loosened so self-failure "
            "escalations also surface the user-voice copy"
        )
