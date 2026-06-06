"""Phase 4 — parse-safety cluster: T4.9, T4.2, T4.3.

T4.9 — an empty-string llama ``baseUrl`` (key present but blank) must be treated
as absent and fall back to the default origin, not yield a relative URL.

T4.2 — a well-formed-but-wrong-shape 200 from the blame analyst (e.g. an
OpenAI-style ``{"error": ...}`` body a loaded llama-server returns) must route to
the ``unknown``/escalate branch, NOT be laundered into an ``impl`` verdict that
burns a real executor retry.

T4.3 — a corrupt or empty ``current_phase.json`` on the advance path must route
to the existing F4 escalation (``ERR_PHASE_RESOLVER_FAILED``) instead of crashing
with an unhandled ``JSONDecodeError`` or silently advancing to a blank phase.
"""
import importlib
import json
import os
import sys
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
        "current_phase": 2,
        "current_phase_raw_id": "CORE-1",
        "last_action": "test",
    }
    inst.lock_fd = None
    inst.openclaw_config = {"hooks_url": "http://x", "hooks_token": "t", "pipeline": {}}
    inst.skill_manager = MagicMock()
    inst._current_attempt_retry_class = "initial_attempt"

    proj = tmp_path / "pipeline-project"
    artifacts = proj / ".autodev" / "pipeline"
    artifacts.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json"))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(proj))
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(artifacts / "phase_state.json"))
    monkeypatch.setattr(orch_mod, "AUTODEV_PIPELINE_ROOT", str(tmp_path))
    monkeypatch.setattr(orch_mod, "_write_pipeline_event", MagicMock())

    return inst, orch_mod, tmp_path


# ---------------------------------------------------------------------------
# T4.9 — empty-string baseUrl treated as absent.
# ---------------------------------------------------------------------------

class TestT49LlamaBaseUrl:

    def test_empty_baseurl_falls_back_to_default(self, orch):
        """A blank baseUrl ("" — key present) must resolve to the default
        absolute origin, never a relative '/chat/completions' URL."""
        inst, mod, _ = orch
        inst.openclaw_config = {"models": {"providers": {"llama-local": {"baseUrl": ""}}}}
        url = inst._llama_chat_completions_url()
        assert url == f"{mod._LLAMA_ORIGIN}/v1/chat/completions"
        assert url.startswith("http"), "empty baseUrl must not yield a relative URL"

    def test_absent_baseurl_falls_back_to_default(self, orch):
        inst, mod, _ = orch
        inst.openclaw_config = {"models": {"providers": {"llama-local": {}}}}
        assert inst._llama_chat_completions_url() == f"{mod._LLAMA_ORIGIN}/v1/chat/completions"

    def test_explicit_baseurl_used_and_trailing_slash_stripped(self, orch):
        inst, _, _ = orch
        inst.openclaw_config = {
            "models": {"providers": {"llama-local": {"baseUrl": "http://gpu:8080/v1/"}}}
        }
        assert inst._llama_chat_completions_url() == "http://gpu:8080/v1/chat/completions"


# ---------------------------------------------------------------------------
# T4.2 — blame-analyst wrong-shape 200 must escalate as 'unknown', not 'impl'.
#
# Validation note: only the BLAME path has the bug. The escalation-advisory
# parse (`_generate_escalation_advisory`) already returns None on any wrong
# shape (empty content → ValueError → its broad `except` returns None), the
# safe outcome — so it is intentionally left unchanged. The blame path's broad
# `except` instead FALLS THROUGH to Layer 2/3, which defaults to 'impl'.
# ---------------------------------------------------------------------------

class TestT42BlameAnalystShapeCheck:

    @staticmethod
    def _write_failure_context(mod):
        # A thin failure context with no strong plan/infra signal: absent the
        # Layer-1 verdict, Layer 2/3 default to 'impl' — so a green assertion of
        # 'unknown' proves Layer 1 routed it, not the heuristic fallback.
        with open(os.path.join(mod.PROJECT_ARTIFACTS_DIR, "failure_context.json"), "w") as f:
            json.dump({"failure_reason": "tests failed", "error_codes": []}, f)

    @staticmethod
    def _fake_resp(payload):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = payload
        return resp

    def test_wrong_shape_200_routes_to_unknown_not_impl(self, orch):
        """A loaded llama-server returns 200 with an OpenAI-style {"error": ...}
        body (no 'choices'). That must escalate as 'unknown', never be laundered
        into 'impl' (which burns a real executor retry on an infra blip)."""
        inst, mod, _ = orch
        self._write_failure_context(mod)
        resp = self._fake_resp({"error": {"message": "overloaded", "type": "server_error"}})
        with patch.object(mod.requests, "post", return_value=resp):
            result = inst.run_blame_attribution()
        assert result["blame"] == "unknown", (
            f"wrong-shape 200 must escalate as 'unknown', got '{result['blame']}'"
        )

    def test_empty_content_routes_to_unknown_not_impl(self, orch):
        """A 200 with an empty assistant message is the same infra symptom."""
        inst, mod, _ = orch
        self._write_failure_context(mod)
        resp = self._fake_resp({"choices": [{"message": {"content": "   "}}]})
        with patch.object(mod.requests, "post", return_value=resp):
            result = inst.run_blame_attribution()
        assert result["blame"] == "unknown"

    def test_wellformed_high_confidence_impl_still_routes_impl(self, orch):
        """Characterization: a valid high-confidence impl verdict is unaffected."""
        inst, mod, _ = orch
        self._write_failure_context(mod)
        body = {"choices": [{"message": {"content": json.dumps(
            {"fault": "impl", "confidence": "high", "reasoning": "logic error"})}}]}
        resp = self._fake_resp(body)
        with patch.object(mod.requests, "post", return_value=resp):
            result = inst.run_blame_attribution()
        assert result["blame"] == "impl"


# ---------------------------------------------------------------------------
# T4.3 — a corrupt/empty current_phase.json on advance must escalate (F4),
# not crash with an unhandled JSONDecodeError or advance to a blank phase.
#
# _advance_to_next_pending_phase deletes current_phase.json at the top and the
# resolver re-writes it; so the mocked resolver writes the (bad) file as a side
# effect and reports a PENDING verdict.
# ---------------------------------------------------------------------------

class TestT43CurrentPhaseGuard:

    @staticmethod
    def _resolver_writes(mod, content):
        def _fake_run(cmd, **kwargs):
            if (isinstance(cmd, (list, tuple)) and len(cmd) >= 2
                    and str(cmd[-1]).endswith("phase_resolver.py")):
                with open(os.path.join(mod.PROJECT_ARTIFACTS_DIR, "current_phase.json"), "w") as f:
                    f.write(content)
                m = MagicMock()
                m.returncode = 0
                m.stdout = "PENDING: Phase 3 (CORE-2)"
                m.stderr = ""
                return m
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
            return m
        return _fake_run

    def test_corrupt_phase_file_escalates(self, orch):
        """A truncated current_phase.json must route to F4 escalation, not raise
        an unhandled JSONDecodeError out of the advance helper."""
        inst, mod, _ = orch
        with patch.object(mod.subprocess, "run",
                          side_effect=self._resolver_writes(mod, "{ corrupt not json ")):
            sig = inst._advance_to_next_pending_phase(trigger="phase_complete")
        assert sig == "continue"
        assert inst.state["current_agent"] == "escalation"
        assert inst.read_phase_state().get("last_error_code") == "ERR_PHASE_RESOLVER_FAILED"

    def test_empty_shape_phase_file_escalates(self, orch):
        """A valid-but-shapeless current_phase.json ({} — no raw_id) must escalate
        rather than silently advancing to current_phase=0, raw_id=''."""
        inst, mod, _ = orch
        with patch.object(mod.subprocess, "run",
                          side_effect=self._resolver_writes(mod, "{}")):
            sig = inst._advance_to_next_pending_phase(trigger="phase_complete")
        assert sig == "continue"
        assert inst.state["current_agent"] == "escalation"
        assert inst.state.get("current_phase_raw_id", "") == "", "must not advance to a blank phase"
        assert inst.read_phase_state().get("last_error_code") == "ERR_PHASE_RESOLVER_FAILED"

    def test_startup_shapeless_phase_file_escalates(self, orch):
        """The startup resolver read (the roadmap's 'mirror') must also escalate on
        a shapeless current_phase.json instead of advancing blind to a 'phase/'
        branch with colliding session keys. (A corrupt file there is already caught
        by the helper's broad except; this covers the empty-shape gap.)"""
        inst, mod, _ = orch
        inst.state["current_agent"] = "planner"
        inst.state["current_phase"] = 0
        with patch.object(mod.subprocess, "run",
                          side_effect=self._resolver_writes(mod, "{}")):
            sig = inst._run_startup_planner_phase_zero_and_branch()
        assert sig == "enter_main_loop"
        assert inst.state["current_agent"] == "escalation"
        assert inst.read_phase_state().get("last_error_code") == "ERR_PHASE_RESOLVER_FAILED"
