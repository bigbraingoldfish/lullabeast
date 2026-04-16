"""C5-04: Blame L1 — malformed analyst JSON must route to 'unknown' not fall through to impl.

When the qwen3.5-27b analyst returns malformed / truncated JSON, Layer 1 currently
falls through to Layer 2/3 which defaults to 'impl'.  Misrouting a broken infra
phase as implementation wastes a full executor retry.  The fix: treat malformed
analyst JSON as 'unknown' and return early rather than falling through.
"""
import json
import os
import sys
import importlib
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def orch(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))

    import orchestrator as orch_mod
    importlib.reload(orch_mod)

    inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    inst.state = {
        "pipeline_status": "RUNNING",
        "current_agent": "executor",
        "current_phase": 1,
        "current_phase_raw_id": "INFRA-1",
        "status": "RUNNING",
        "last_action": "test",
        "executor_retries": 0,
        "reviewer_retries": 0,
    }
    inst.lock_fd = None
    inst.skill_manager = MagicMock()
    inst.openclaw_config = {
        "hooks_url": "http://x", "hooks_token": "t",
        "models": {"providers": {"llama-local": {"baseUrl": "http://localhost:11434/v1"}}},
    }

    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(tmp_path))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json"))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json"))
    monkeypatch.setattr(orch_mod, "AUTODEV_PIPELINE_ROOT", str(tmp_path))

    # Create a failure_context.json so Layer 1 runs
    (tmp_path / "failure_context.json").write_text(json.dumps(
        {"failure_reason": "infra error", "gate_error_codes": ["ERR_GIT_FAIL"]}
    ))
    # No executor_output.json so Layer 2 heuristics produce no signal
    return inst, orch_mod, tmp_path


def _make_malformed_response(content):
    """Build a mock requests.Response with malformed JSON content."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return mock_resp


class TestC504BlameL1MalformedJSON:

    def test_malformed_json_does_not_route_to_impl(self, orch, tmp_path):
        """When L1 analyst returns malformed JSON, blame must NOT be 'impl'."""
        inst, mod, base = orch

        # Analyst returns truncated/malformed JSON
        malformed_content = '{"fault": "infrastructure", "confidence":' # truncated

        with patch("requests.post", return_value=_make_malformed_response(malformed_content)):
            result = inst.run_blame_attribution()

        assert result.get("blame") != "impl", (
            f"Malformed analyst JSON routed to 'impl' — should be 'unknown' or 'infra'. "
            f"Result: {result}"
        )

    def test_malformed_json_routes_to_unknown(self, orch, tmp_path):
        """When L1 analyst returns malformed JSON, blame should be 'unknown'."""
        inst, mod, base = orch

        malformed_content = 'not valid json at all {{'

        with patch("requests.post", return_value=_make_malformed_response(malformed_content)):
            result = inst.run_blame_attribution()

        assert result.get("blame") == "unknown", (
            f"Expected blame='unknown' for malformed analyst JSON, got: {result}"
        )

    def test_valid_high_confidence_impl_still_routes_correctly(self, orch, tmp_path):
        """Sanity: valid high-confidence 'impl' response still routes to impl."""
        inst, mod, base = orch

        valid_content = json.dumps({
            "fault": "impl", "confidence": "high",
            "reasoning": "The executor wrote incorrect code."
        })

        with patch("requests.post", return_value=_make_malformed_response(valid_content)):
            result = inst.run_blame_attribution()

        assert result.get("blame") == "impl", (
            f"Expected blame='impl' for valid high-confidence impl signal, got: {result}"
        )
