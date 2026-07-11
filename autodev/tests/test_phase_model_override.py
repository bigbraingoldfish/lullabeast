"""Per-phase model override (``phase_model_overrides.json``).

The dashboard writes ``{raw_id: {role: model}}`` into the project's pipeline
dir; the orchestrator resolves the current phase's entry at every
planner/executor/reviewer invocation and threads it as ``model=`` on the
webhook (sessions bake their model at creation, so no gateway restart is
needed and every attempt in the phase gets the override). ``models_used``
stamps the effective model, and the entry is dropped when the phase closes.

Fixture pattern mirrors ``test_models_used_capture.py`` (live instance,
patched path constants).
"""
import importlib
import inspect
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _agents_config(models):
    return {
        "hooks_url": "http://x",
        "hooks_token": "t",
        "hooks": {"token": "t"},
        "pipeline": {},
        "agents": {"list": [
            {"id": aid, "model": {"primary": m}} for aid, m in models.items()
        ]},
    }


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
    inst.openclaw_config = _agents_config({
        "planner": "openrouter/z-ai/glm-5.2",
        "executor": "openrouter/moonshotai/kimi-k2.7-code",
        "reviewer": "openrouter/moonshotai/kimi-k2.7-code",
    })
    inst.skill_manager = MagicMock()
    inst._preset_session_response_usage = MagicMock()

    proj = tmp_path / "pipeline-project"
    artifacts = proj / ".autodev" / "pipeline"
    artifacts.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json"))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(proj))
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(artifacts / "phase_state.json"))
    monkeypatch.setattr(
        orch_mod, "PHASE_MODEL_OVERRIDES_FILE", str(artifacts / "phase_model_overrides.json")
    )
    monkeypatch.setattr(orch_mod, "AUTODEV_PIPELINE_ROOT", str(tmp_path))
    monkeypatch.setattr(orch_mod, "_write_pipeline_event", MagicMock())

    return inst, orch_mod, artifacts


def _write_overrides(artifacts, data):
    (artifacts / "phase_model_overrides.json").write_text(json.dumps(data), encoding="utf-8")


class TestResolve:

    def test_missing_file_means_no_override(self, orch):
        inst, _, _ = orch
        assert inst._phase_model_override("executor") is None

    def test_override_resolves_for_current_phase_role(self, orch):
        inst, _, artifacts = orch
        _write_overrides(artifacts, {"CORE-1": {"executor": "openrouter/big/strong"}})
        assert inst._phase_model_override("executor") == "openrouter/big/strong"
        assert inst._phase_model_override("planner") is None

    def test_other_phase_entry_is_ignored(self, orch):
        inst, _, artifacts = orch
        _write_overrides(artifacts, {"UI-3": {"executor": "openrouter/big/strong"}})
        assert inst._phase_model_override("executor") is None

    def test_malformed_file_means_no_override(self, orch):
        inst, _, artifacts = orch
        (artifacts / "phase_model_overrides.json").write_text("{not json", encoding="utf-8")
        assert inst._phase_model_override("executor") is None

    def test_empty_raw_id_means_no_override(self, orch):
        inst, _, artifacts = orch
        inst.state["current_phase_raw_id"] = ""
        _write_overrides(artifacts, {"": {"executor": "openrouter/big/strong"}})
        assert inst._phase_model_override("executor") is None


class TestWebhookThreading:

    def test_executor_override_reaches_model_kwarg(self, orch):
        inst, orch_mod, artifacts = orch
        _write_overrides(artifacts, {"CORE-1": {"executor": "openrouter/big/strong"}})
        with patch.object(orch_mod, "invoke_agent_webhook", return_value="SUCCESS") as hook:
            inst._invoke_executor("sk", "tok")
        assert hook.call_args.kwargs["model"] == "openrouter/big/strong"

    def test_executor_without_override_passes_none(self, orch):
        inst, orch_mod, _ = orch
        with patch.object(orch_mod, "invoke_agent_webhook", return_value="SUCCESS") as hook:
            inst._invoke_executor("sk", "tok")
        # webhook_client omits a falsy model from the payload (pinned in
        # test_webhook_client_invoke_model.py), so None here means no change.
        assert hook.call_args.kwargs["model"] is None

    def test_reviewer_override_reaches_model_kwarg(self, orch):
        inst, orch_mod, artifacts = orch
        _write_overrides(artifacts, {"CORE-1": {"reviewer": "openrouter/big/strong"}})
        with patch.object(orch_mod, "invoke_agent_webhook", return_value="SUCCESS") as hook:
            inst._invoke_reviewer("sk", "tok")
        assert hook.call_args.kwargs["model"] == "openrouter/big/strong"

    def test_retry_within_phase_keeps_override(self, orch):
        inst, orch_mod, artifacts = orch
        _write_overrides(artifacts, {"CORE-1": {"executor": "openrouter/big/strong"}})
        with patch.object(orch_mod, "invoke_agent_webhook", return_value="SUCCESS") as hook:
            inst._invoke_executor("sk-attempt-1", "tok")
            inst._invoke_executor("sk-attempt-2", "tok")
        models = [c.kwargs["model"] for c in hook.call_args_list]
        assert models == ["openrouter/big/strong", "openrouter/big/strong"]


class TestModelsUsedStamp:

    def test_stamp_reflects_override(self, orch):
        inst, _, artifacts = orch
        _write_overrides(artifacts, {"CORE-1": {"executor": "openrouter/big/strong"}})
        inst._record_active_agent("executor", "sk")
        models = inst.read_phase_state().get("models_used")
        assert models == {"executor": "openrouter/big/strong"}

    def test_stamp_falls_back_to_configured_model(self, orch):
        inst, _, _ = orch
        inst._record_active_agent("executor", "sk")
        models = inst.read_phase_state().get("models_used")
        assert models == {"executor": "openrouter/moonshotai/kimi-k2.7-code"}


class TestClearOnPhaseClose:

    def test_clear_drops_entry_and_keeps_other_phases(self, orch):
        inst, _, artifacts = orch
        _write_overrides(artifacts, {
            "CORE-1": {"executor": "openrouter/big/strong"},
            "UI-3": {"reviewer": "openrouter/big/strong"},
        })
        inst._clear_phase_model_override("CORE-1")
        data = json.loads((artifacts / "phase_model_overrides.json").read_text(encoding="utf-8"))
        assert data == {"UI-3": {"reviewer": "openrouter/big/strong"}}

    def test_clear_removes_file_when_empty(self, orch):
        inst, _, artifacts = orch
        _write_overrides(artifacts, {"CORE-1": {"executor": "openrouter/big/strong"}})
        inst._clear_phase_model_override("CORE-1")
        assert not (artifacts / "phase_model_overrides.json").exists()

    def test_clear_tolerates_missing_and_malformed_file(self, orch):
        inst, _, artifacts = orch
        inst._clear_phase_model_override("CORE-1")  # no file
        (artifacts / "phase_model_overrides.json").write_text("{not json", encoding="utf-8")
        inst._clear_phase_model_override("CORE-1")  # malformed file

    def test_phase_advance_clears_the_closed_phase_entry(self, orch):
        # The advance helper does git + subprocess work that is out of scope
        # here; pin the wiring instead (the behavior is covered above).
        _, orch_mod, _ = orch
        src = inspect.getsource(orch_mod.Orchestrator._advance_to_next_pending_phase)
        assert "_clear_phase_model_override" in src
