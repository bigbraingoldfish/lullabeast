"""Fresh agent sessions after an operator reset.

RESET_REVIEWER, RESET_EXECUTION, RESET_PHASE and the nuclear reset zero the retry
counters a pipeline session key encodes (``planner-attempt-N``,
``executor-attempt-N``, ``reviewer-attempt-N`` and its ``-c``/``-u`` retry
discriminators), and a reset keeps the run id. So the first attempt after a reset
rebuilt an earlier attempt's key and OpenClaw reattached that session: a reset
reviewer re-entered the transcript it had been rejected for. In the DeepSeek V4.1
Flash smoke run, the two reviewer passes after RESET_REVIEWER reattached the two
passes before it.

``_reset_suffix`` (``-e{N}``, N = operator resets of the phase) gives each reset
cycle its own sessions; keys before the first reset keep their shape.
"""

import json
import os
import re
import sys
from unittest.mock import MagicMock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402

with open(os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8") as _f:
    _ORCH_SRC = re.sub(r"\s+", " ", _f.read())


def _orch(tmp_path, monkeypatch, phase_state):
    ps = tmp_path / "phase_state.json"
    ps.write_text(json.dumps(phase_state))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(ps))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(tmp_path))
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.lock_fd = None
    orch.openclaw_config = {"hooks": {"token": "tok"}}
    orch.state = {
        "current_phase": 1,
        "current_phase_raw_id": "CORE-E1",
        "current_agent": "escalation",
        "reviewer_retries": 0,
    }
    orch.write_state = MagicMock()
    orch.transition_state = MagicMock()
    return orch


class TestResetSuffix:
    def test_empty_before_any_reset(self, tmp_path, monkeypatch):
        assert _orch(tmp_path, monkeypatch, {})._reset_suffix() == ""
        assert _orch(tmp_path, monkeypatch, {"escalation_resets": 0})._reset_suffix() == ""

    def test_counts_every_operator_reset_of_the_phase(self, tmp_path, monkeypatch):
        orch = _orch(tmp_path, monkeypatch, {"escalation_resets": 2, "nuclear_resets": 1})
        assert orch._reset_suffix() == "-e3"

    def test_reset_reviewer_moves_the_reviewer_to_a_new_session(self, tmp_path, monkeypatch):
        """The smoke run's case: two evidence retries, escalation, RESET_REVIEWER.
        The next reviewer pass must not rebuild the first pass's key."""
        orch = _orch(tmp_path, monkeypatch, {
            "reviewer_unverified_retries": 2,
            "escalation_resets": 0,
            "last_error_code": "ERR_BEHAVIORAL_UNVERIFIED",
        })
        first_pass = orch._reviewer_session_key(1, "CORE-E1", 0, 0, 0) + orch._reset_suffix()

        orch.reset_reviewer()

        ps = orch.read_phase_state()
        after_reset = orch._reviewer_session_key(
            1, "CORE-E1", orch.state["reviewer_retries"],
            ps.get("reviewer_contract_retries", 0), ps.get("reviewer_unverified_retries", 0),
        ) + orch._reset_suffix()
        assert first_pass == "pipeline:phase-1:CORE-E1:reviewer-attempt-1"
        assert after_reset == "pipeline:phase-1:CORE-E1:reviewer-attempt-1-e1"


class TestKeySitesCarryTheResetSuffix:
    """Every planner, executor and reviewer key folds in the suffix, between the
    retry discriminators and the run suffix."""

    def test_planner_and_executor_keys(self):
        for role in ("planner", "executor"):
            site = (
                f'session_key = f"pipeline:phase-{{phase}}:{{raw_id}}:{role}-attempt-{{retries + 1}}" '
                "+ self._send_back_suffix() + self._provider_retry_suffix() "
                "+ self._reset_suffix() + self._run_suffix()"
            )
            assert site in _ORCH_SRC, role

    def test_reviewer_key(self):
        site = (
            "_reviewer_session_key( phase, raw_id, retries, _contract_retries, _unverified_retries ) "
            "+ self._send_back_suffix() + self._provider_retry_suffix() "
            "+ self._reset_suffix() + self._run_suffix()"
        )
        assert site in _ORCH_SRC

    def test_prior_executor_attempt_key_matches_its_cycle(self):
        """The retry-start interrupt rebuilds the prior executor attempt's key. That
        attempt is in the same reset cycle, so the key carries the same suffix."""
        site = (
            'f"pipeline:phase-{phase}:{raw_id}:executor-attempt-{retries}" '
            "+ self._send_back_suffix() + self._reset_suffix() + self._run_suffix()"
        )
        assert site in _ORCH_SRC
