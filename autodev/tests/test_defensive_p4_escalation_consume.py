"""Phase 4 — T4.5 + T4.10: defensive guards in _apply_pending_escalation_command.

T4.5 — a corrupt ``pending_escalation_command.json`` currently becomes a silent
``STOP`` (the operator's RESET_PHASE/PROCEED/SKIP intent is discarded and the
file deleted). The hardened path emits ``escalation_command_invalid``, leaves the
file in place for re-banking, and does NOT consume it as STOP.

T4.10 — when the queued project's directory is deleted, ``makedirs`` fails
(swallowed) and the missing pending file reads identically to "no banked
command" — the answer is silently lost. The hardened path logs loudly and emits
``queue_revive_project_missing`` so the deleted-dir case is distinguishable.
"""
import importlib
import os
import sys
from unittest.mock import MagicMock

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
        "pipeline_status": "QUEUE_HALTED",
        "current_agent": "escalation",
        "current_phase": 0,
        "current_phase_raw_id": "",
        "last_action": "test",
    }
    inst.lock_fd = None
    inst.openclaw_config = {"hooks_url": "http://x", "hooks_token": "t"}
    inst.skill_manager = MagicMock()

    monkeypatch.setattr(orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json"))
    monkeypatch.setattr(orch_mod, "_write_pipeline_event", MagicMock())

    return inst, orch_mod, tmp_path


def _events(mod):
    return [c.args[0] for c in mod._write_pipeline_event.call_args_list]


# ---------------------------------------------------------------------------
# T4.5 — corrupt banked answer must surface, not silently STOP.
# ---------------------------------------------------------------------------

class TestT45CorruptBankedAnswer:

    def test_corrupt_answer_emits_event_and_preserves_file(self, orch):
        inst, mod, tmp_path = orch
        proj = tmp_path / "proj"
        art = proj / ".autodev" / "pipeline"
        art.mkdir(parents=True)
        pending = art / "pending_escalation_command.json"
        pending.write_text("{ this is not valid json ")  # truncated / corrupt

        result = inst._apply_pending_escalation_command(str(proj))

        assert result is None, "corrupt banked answer must not be consumed as a command"
        assert pending.exists(), "the corrupt file must be left in place for re-banking"
        assert not (art / "escalation_output.done").exists(), "must NOT write a STOP escalation_output"
        assert "escalation_command_invalid" in _events(mod)

    def test_non_object_answer_emits_event(self, orch):
        """A valid-JSON but non-object answer (e.g. a bare list) is also corrupt."""
        inst, mod, tmp_path = orch
        art = tmp_path / "proj2" / ".autodev" / "pipeline"
        art.mkdir(parents=True)
        (art / "pending_escalation_command.json").write_text("[1, 2, 3]")

        result = inst._apply_pending_escalation_command(str(tmp_path / "proj2"))

        assert result is None
        assert "escalation_command_invalid" in _events(mod)

    def test_valid_answer_still_applies(self, orch):
        """Characterization: a well-formed banked command is applied as before."""
        inst, mod, tmp_path = orch
        art = tmp_path / "proj3" / ".autodev" / "pipeline"
        art.mkdir(parents=True)
        (art / "pending_escalation_command.json").write_text('{"command": "RESET_PHASE"}')

        result = inst._apply_pending_escalation_command(str(tmp_path / "proj3"))

        assert result == "RESET_PHASE"
        assert (art / "escalation_output.done").exists()
        assert "escalation_command_invalid" not in _events(mod)


# ---------------------------------------------------------------------------
# T4.10 — deleted project dir on revive must be surfaced, not silently dropped.
# ---------------------------------------------------------------------------

class TestT410DeletedProjectDir:

    def test_missing_project_dir_emits_event(self, orch):
        inst, mod, tmp_path = orch
        missing = tmp_path / "deleted-project"  # never created

        result = inst._apply_pending_escalation_command(str(missing))

        assert result is None
        assert "queue_revive_project_missing" in _events(mod), (
            "a deleted project dir must emit queue_revive_project_missing, not read "
            "identically to 'no banked command'"
        )

    def test_existing_dir_no_pending_file_is_silent(self, orch):
        """The normal 'no banked command' case stays quiet — distinguishable from
        the deleted-dir case above."""
        inst, mod, tmp_path = orch
        (tmp_path / "proj4" / ".autodev" / "pipeline").mkdir(parents=True)

        result = inst._apply_pending_escalation_command(str(tmp_path / "proj4"))

        assert result is None
        assert "queue_revive_project_missing" not in _events(mod)
        assert "escalation_command_invalid" not in _events(mod)
