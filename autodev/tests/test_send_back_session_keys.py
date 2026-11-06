"""Fresh agent sessions after the reviewer sends a phase back.

The base session keys count retries within a round (``planner-attempt-N``,
``executor-attempt-N``, ``reviewer-attempt-N``), and the routes back from the
reviewer restart those counters: ROUTE_EXECUTOR and MISSING_ARTIFACTS zero
``executor_retries``, ROUTE_PLANNER zeros ``planner_retries`` and
``executor_retries``, and an executor self-failure zeros ``reviewer_retries``.
So the run after a send-back rebuilt an earlier attempt's key and OpenClaw
reattached that session: after a rejection the executor resumed attempt 1's
transcript (not even the run whose work was rejected when attempt 1 had failed
or been stopped), and every later round piled onto the same transcript. That
broke the rule the pipeline documents (a distinct session key per attempt) and
weakened the stray-sentinel guard, which tells attempts apart by session key.

``_send_back_suffix`` (``-b{N}``, N = ``review_send_backs``) gives every round
its own sessions. Keys before the first send-back keep their shape.
"""

import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402

with open(os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8") as _f:
    _RAW_SRC = _f.read()
_ORCH_SRC = re.sub(r"\s+", " ", _RAW_SRC)


class _R:
    """Stub for ``subprocess.CompletedProcess``."""
    returncode = 0
    stdout = ""
    stderr = ""


def _orch(tmp_path, monkeypatch, phase_state=None):
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(tmp_path))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json"))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json"))
    (tmp_path / "phase_state.json").write_text(json.dumps(phase_state or {}))
    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = {
        "current_phase": 1,
        "current_phase_raw_id": "CORE-E1",
        "current_agent": "planner",
        "planner_retries": 0,
        "executor_retries": 0,
        "reviewer_retries": 0,
        "pipeline_status": "RUNNING",
        "project_path": str(tmp_path),
    }
    orch.openclaw_config = {}
    orch.lock_fd = None
    orch._current_attempt_retry_class = "initial_attempt"
    import subprocess as _sp

    monkeypatch.setattr(_sp, "run", lambda *a, **k: _R())
    return orch


def _branch(handler: str) -> str:
    """The body of ``elif gate_result == "<handler>":`` in the main loop."""
    start = _RAW_SRC.find(f'elif gate_result == "{handler}":')
    assert start != -1, f"{handler} handler missing from orchestrator source"
    end = _RAW_SRC.find("elif gate_result ==", start + 10)
    return _RAW_SRC[start:end if end != -1 else start + 6000]


class TestSendBackSuffix:
    def test_empty_before_any_send_back(self, tmp_path, monkeypatch):
        assert _orch(tmp_path, monkeypatch, {})._send_back_suffix() == ""
        assert _orch(tmp_path, monkeypatch, {"review_send_backs": 0})._send_back_suffix() == ""

    def test_counts_send_backs(self, tmp_path, monkeypatch):
        assert _orch(tmp_path, monkeypatch, {"review_send_backs": 2})._send_back_suffix() == "-b2"

    def test_unreadable_count_reads_as_zero(self, tmp_path, monkeypatch):
        assert _orch(tmp_path, monkeypatch, {"review_send_backs": "x"})._send_back_suffix() == ""

    def test_count_send_back_increments_in_place(self, tmp_path, monkeypatch):
        orch = _orch(tmp_path, monkeypatch)
        ps = {}
        orch._count_send_back(ps)
        assert ps == {"review_send_backs": 1}
        orch._count_send_back(ps)
        assert ps["review_send_backs"] == 2


class TestHandlersCountTheSendBack:
    """Each route back to an agent counts before its phase_state write; the
    MISSING_ARTIFACTS cap arm escalates instead, so it doesn't count."""

    def test_route_executor(self):
        body = _branch("ROUTE_EXECUTOR")
        call = body.find("self._count_send_back(_ps_re)")
        assert call != -1
        assert call < body.find("self.write_phase_state_atomic(_ps_re)")

    def test_route_planner(self):
        body = _branch("ROUTE_PLANNER")
        call = body.find("self._count_send_back(_ps_rp)")
        assert call != -1
        assert call < body.find("self.write_phase_state_atomic(_ps_rp)")

    def test_missing_artifacts_re_invoke_only(self):
        body = _branch("MISSING_ARTIFACTS")
        call = body.find("self._count_send_back(_ps_ma)")
        assert call != -1
        assert body.count("self._count_send_back(") == 1
        # In the re-invoke arm (it sets executor_keep_output), before that arm's write.
        keep = body.find('_ps_ma["executor_keep_output"] = True')
        assert keep != -1 and call > body.find("else:", body.find("if _ma_retries >= 2"))
        assert call < body.find("self.write_phase_state_atomic(_ps_ma)", keep)


class TestKeySitesCarryTheSendBackSuffix:
    """The suffix sits after the attempt and -c/-u discriminators, before -pr."""

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

    def test_prior_executor_attempt_key_is_in_the_same_round(self):
        """The retry-start interrupt rebuilds the prior attempt's key; a
        self-failure retry stays in its round, so the key carries the suffix."""
        site = (
            'f"pipeline:phase-{phase}:{raw_id}:executor-attempt-{retries}" '
            "+ self._send_back_suffix() + self._reset_suffix() + self._run_suffix()"
        )
        assert site in _ORCH_SRC


class TestEveryDispatchGetsItsOwnKey:
    """Walk a phase through every send-back and the self-failures between them,
    building each key exactly as its site does, and require no repeats."""

    def _key(self, orch, role):
        phase, raw_id = 1, "CORE-E1"
        if role == "reviewer":
            ps = orch.read_phase_state()
            base = orch._reviewer_session_key(
                phase, raw_id, orch.state.get("reviewer_retries", 0),
                ps.get("reviewer_contract_retries", 0), ps.get("reviewer_unverified_retries", 0),
            )
        else:
            retries = orch.state.get(f"{role}_retries", 0)
            base = f"pipeline:phase-{phase}:{raw_id}:{role}-attempt-{retries + 1}"
        return base + orch._send_back_suffix() + orch._provider_retry_suffix() + orch._reset_suffix() + orch._run_suffix()

    def _send_back(self, orch, to):
        """The counter effects of the three send-back handlers."""
        ps = orch.read_phase_state()
        if to == "executor":            # ROUTE_EXECUTOR
            ps["executor_reviewer_rejection_retries"] = ps.get("executor_reviewer_rejection_retries", 0) + 1
        elif to == "artifacts":         # MISSING_ARTIFACTS re-invoke
            ps["reviewer_artifacts_retries"] = ps.get("reviewer_artifacts_retries", 0) + 1
        orch._count_send_back(ps)
        orch.write_phase_state_atomic(ps)
        if to != "artifacts":
            orch.increment_reviewer_retries()
        if to == "planner":             # ROUTE_PLANNER
            orch.state["planner_retries"] = 0
        orch.state["executor_retries"] = 0

    def test_no_key_repeats_across_send_backs(self, tmp_path, monkeypatch):
        orch = _orch(tmp_path, monkeypatch)
        steps = [
            ("run", "planner"),
            ("run", "executor"), ("fail", None), ("run", "executor"),  # fail, then pass
            ("run", "reviewer"), ("back", "executor"),                 # rejected
            ("run", "executor"), ("fail", None), ("run", "executor"),  # fail (zeros reviewer_retries), pass
            ("run", "reviewer"), ("back", "artifacts"),                # archive/metrics missing
            ("run", "executor"), ("run", "reviewer"), ("back", "planner"),
            ("run", "planner"), ("run", "executor"), ("run", "reviewer"),
        ]
        keys = []
        for action, arg in steps:
            if action == "run":
                keys.append(self._key(orch, arg))
            elif action == "fail":
                orch.reset_execution("auto")
            else:
                self._send_back(orch, arg)

        repeats = sorted({k for k in keys if keys.count(k) > 1})
        assert not repeats, f"a send-back reattached an earlier session: {repeats}"

    def test_first_round_keys_keep_their_shape(self, tmp_path, monkeypatch):
        orch = _orch(tmp_path, monkeypatch)
        assert self._key(orch, "executor") == "pipeline:phase-1:CORE-E1:executor-attempt-1"
        self._send_back(orch, "executor")
        assert self._key(orch, "executor") == "pipeline:phase-1:CORE-E1:executor-attempt-1-b1"
