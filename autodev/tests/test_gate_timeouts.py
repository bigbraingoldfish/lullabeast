"""Gate subprocess.run must use a hard timeout so a hung gate cannot stall the orchestrator."""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

OPENCLAW_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(OPENCLAW_DIR, "autodev", "pipeline")
for _p in [PIPELINE_DIR, OPENCLAW_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _minimal_orch(tmp_dir: str):
    import orchestrator as orc_module

    state_file = os.path.join(tmp_dir, "pipeline_state.json")
    lock_file = os.path.join(tmp_dir, "pipeline.lock")
    config_file = os.path.join(tmp_dir, "openclaw.json")
    phase_state_file = os.path.join(tmp_dir, "phase_state.json")
    with open(config_file, "w") as f:
        json.dump({"hooks": {"token": "t"}}, f)
    with (
        patch.object(orc_module, "STATE_FILE", state_file),
        patch.object(orc_module, "SYMLINK_TARGET", tmp_dir),
        patch.object(orc_module, "LOCK_FILE", lock_file),
        patch.object(orc_module, "CONFIG_FILE", config_file),
        patch.object(orc_module, "PHASE_STATE_FILE", phase_state_file),
    ):
        from orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch.lock_fd = None
        orch.openclaw_config = {"hooks": {"token": "t"}}
        orch.state = {}
    return orch


@pytest.fixture
def orch(tmp_path):
    return _minimal_orch(str(tmp_path))


class TestGateTimeouts:
    """Each gate subprocess.run uses timeout=60; TimeoutExpired is a gate failure."""

    def test_planner_gate_timeout_returns_false(self, orch, tmp_path):
        import orchestrator as orc_module
        from subprocess import TimeoutExpired

        planner_json = os.path.join(str(tmp_path), "planner_output.json")
        with open(planner_json, "w") as f:
            json.dump({"implementation_plan": [], "tdd_test_structure": [], "pass_criteria": []}, f)

        with patch.object(orc_module, "SYMLINK_TARGET", str(tmp_path)):
            with patch("orchestrator.subprocess.run", side_effect=TimeoutExpired("cmd", 60)):
                assert orch.run_planner_output_gate() is False

    def test_executor_gate_timeout_returns_false(self, orch):
        import orchestrator as orc_module
        from subprocess import TimeoutExpired

        with patch.object(orc_module, "SYMLINK_TARGET", orch.state.get("__unused__", "/tmp")):
            with patch("orchestrator.subprocess.run", side_effect=TimeoutExpired("cmd", 60)):
                assert orch.run_executor_output_gate() is False

    def test_reviewer_gate_timeout_returns_route_escalate(self, orch):
        import orchestrator as orc_module
        from subprocess import TimeoutExpired

        with patch.object(orc_module, "SYMLINK_TARGET", "/tmp"):
            with patch("orchestrator.subprocess.run", side_effect=TimeoutExpired("cmd", 60)):
                assert orch.run_reviewer_output_gate() == "ROUTE_ESCALATE"

    def test_repo_init_gate_timeout_returns_false_details(self, orch):
        from subprocess import TimeoutExpired

        with patch("orchestrator.subprocess.run", side_effect=TimeoutExpired("cmd", 60)):
            ok, details = orch.run_repo_init_check()
            assert ok is False
            assert "timeout" in details.lower() or "timed out" in details.lower()

    def test_gate_timeout_value_is_60(self, orch, tmp_path):
        """Successful subprocess.run calls must pass timeout=60."""
        import orchestrator as orc_module
        from subprocess import CompletedProcess

        fake = CompletedProcess(args=[], returncode=0, stdout="PASS\n", stderr="")

        planner_json = os.path.join(str(tmp_path), "planner_output.json")
        with open(planner_json, "w") as f:
            json.dump(
                {
                    "implementation_plan": ["a"],
                    "tdd_test_structure": ["b"],
                    "pass_criteria": [{"condition": "c"}],
                },
                f,
            )

        with patch.object(orc_module, "SYMLINK_TARGET", str(tmp_path)):
            with patch("orchestrator.subprocess.run", return_value=fake) as mock_run:
                orch.run_planner_output_gate()
                assert mock_run.call_args.kwargs.get("timeout") == 60

        with patch("orchestrator.subprocess.run", return_value=fake) as mock_run2:
            orch.run_executor_output_gate()
            assert mock_run2.call_args.kwargs.get("timeout") == 60

        with patch("orchestrator.subprocess.run", return_value=fake) as mock_run3:
            orch.run_reviewer_output_gate()
            assert mock_run3.call_args.kwargs.get("timeout") == 60

        with patch("orchestrator.subprocess.run", return_value=CompletedProcess([], 0, "ok", "")) as mock_run4:
            ok, _ = orch.run_repo_init_check()
            assert ok is True
            assert mock_run4.call_args.kwargs.get("timeout") == 60
