"""Phase 4 — T4.8 (reviewer token accumulation) + T4.4 (roadmap-checkbox flip).

T4.8 — the reviewer token-capture site OVERWRITES ``reviewer_tokens_acc`` on
every reviewer re-invocation (CONTRACT_FAILURE / *_UNVERIFIED / multi-pass),
while the planner/executor sites accumulate. The shared ``_accumulate_role_tokens``
helper fixes the reviewer path and removes the duplication that let it drift.

T4.4 — a failed roadmap-checkbox flip on a NON-git error (read-only roadmap,
encoding error) is swallowed and the phase tags + advances anyway → the resolver
re-returns the just-completed phase → silent re-run → ERR_MERGE_FAILED. The
hardened path escalates fail-closed with the Decision #5 operator message.
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
        "current_agent": "reviewer",
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
# T4.8 — _accumulate_role_tokens sums, never overwrites.
# ---------------------------------------------------------------------------

class TestT48AccumulateRoleTokens:

    def test_accumulates_across_calls(self, orch, monkeypatch):
        """Two reviewer invocations in one phase must SUM, not overwrite.

        Distinct invocations use distinct session keys → distinct JSONL paths
        (the post-sentinel-recount change keys contributions by path; the same
        path re-read REPLACES — see test_token_post_sentinel_recount.py)."""
        inst, mod, _ = orch
        monkeypatch.setattr(mod, "_sum_session_tokens", lambda p: {"input": 10, "output": 5})
        inst._accumulate_role_tokens("reviewer", "/fake-pass-1.jsonl")
        inst._accumulate_role_tokens("reviewer", "/fake-pass-2.jsonl")
        ps = inst.read_phase_state()
        assert ps["reviewer_tokens_acc"] == {"input": 20, "output": 10}

    def test_same_session_path_replaces_not_doubles(self, orch, monkeypatch):
        """A resumed session (restart RETRY reuses the attempt-1 session key →
        same JSONL path) must replace its earlier contribution, not add."""
        inst, mod, _ = orch
        monkeypatch.setattr(mod, "_sum_session_tokens", lambda p: {"input": 10, "output": 5})
        inst._accumulate_role_tokens("reviewer", "/fake.jsonl")
        inst._accumulate_role_tokens("reviewer", "/fake.jsonl")
        ps = inst.read_phase_state()
        assert ps["reviewer_tokens_acc"] == {"input": 10, "output": 5}

    def test_adds_to_existing_acc(self, orch, monkeypatch):
        inst, mod, _ = orch
        ps0 = inst.read_phase_state()
        ps0["reviewer_tokens_acc"] = {"input": 100}
        inst.write_phase_state_atomic(ps0)
        monkeypatch.setattr(mod, "_sum_session_tokens", lambda p: {"input": 7, "output": 3})
        inst._accumulate_role_tokens("reviewer", "/fake.jsonl")
        ps = inst.read_phase_state()
        assert ps["reviewer_tokens_acc"] == {"input": 107, "output": 3}

    def test_non_dict_acc_is_coerced(self, orch, monkeypatch):
        """A corrupt non-dict accumulator must not crash — reset to a fresh dict."""
        inst, mod, _ = orch
        ps0 = inst.read_phase_state()
        ps0["reviewer_tokens_acc"] = "garbage"
        inst.write_phase_state_atomic(ps0)
        monkeypatch.setattr(mod, "_sum_session_tokens", lambda p: {"input": 4})
        inst._accumulate_role_tokens("reviewer", "/fake.jsonl")
        ps = inst.read_phase_state()
        assert ps["reviewer_tokens_acc"] == {"input": 4}

    def test_planner_and_executor_roles_keyed_separately(self, orch, monkeypatch):
        inst, mod, _ = orch
        monkeypatch.setattr(mod, "_sum_session_tokens", lambda p: {"input": 2})
        inst._accumulate_role_tokens("planner", "/fake.jsonl")
        inst._accumulate_role_tokens("executor", "/fake.jsonl")
        ps = inst.read_phase_state()
        assert ps["planner_tokens_acc"] == {"input": 2}
        assert ps["executor_tokens_acc"] == {"input": 2}


# ---------------------------------------------------------------------------
# T4.4 — a failed roadmap-checkbox flip (non-git error) must escalate fail-closed
# with the Decision #5 operator message, not swallow + tag + advance.
# ---------------------------------------------------------------------------

class TestT44RoadmapCheckboxFlip:

    @staticmethod
    def _make_roadmap(tmp_path, raw_id="CORE-1"):
        rm = tmp_path / "roadmap.md"
        rm.write_text(f"# Roadmap\n- [ ] `{raw_id}` | Do the thing\n")
        return rm

    def test_unwritable_roadmap_escalates_fail_closed(self, orch, monkeypatch):
        """A non-git failure flipping the checkbox (unwritable roadmap dir) must
        route to escalation with the Decision #5 message — never silently proceed.

        The flip writes via ``write_text_atomic`` (mkstemp + os.replace), which a
        read-only *file* cannot block — rename permission lives on the directory —
        so the realistic failure is an unwritable directory (or full disk)."""
        if os.geteuid() == 0:
            pytest.skip("chmod read-only does not block the root user")
        inst, mod, tmp_path = orch
        inst.state["current_phase_raw_id"] = "CORE-1"
        # Own subdir so the escalation path's state writes (which live in
        # tmp_path / the artifacts dir) stay writable while the roadmap's
        # directory rejects the atomic write's mkstemp.
        rm_dir = tmp_path / "ro-roadmap"
        rm_dir.mkdir()
        rm = self._make_roadmap(rm_dir)
        # git add/commit are after the write; stub them so a stray call can't pass.
        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: MagicMock(returncode=0))

        rm_dir.chmod(0o555)  # read/traverse ok, mkstemp → PermissionError
        try:
            ok = inst._flip_roadmap_checkbox_or_escalate(str(rm), 2)
        finally:
            rm_dir.chmod(0o755)

        assert ok is False
        assert inst.state["current_agent"] == "escalation"
        ps = inst.read_phase_state()
        assert ps.get("last_error_code") == "ERR_ROADMAP_CHECKBOX_FAILED"
        assert "couldn't be flipped" in ps.get("escalation_trigger_reason", "")

    def test_successful_flip_marks_checkbox_and_returns_true(self, orch, monkeypatch):
        inst, mod, tmp_path = orch
        inst.state["current_phase_raw_id"] = "CORE-1"
        rm = self._make_roadmap(tmp_path, raw_id="CORE-1")
        monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: MagicMock(returncode=0))

        ok = inst._flip_roadmap_checkbox_or_escalate(str(rm), 2)

        assert ok is True
        assert "- [x] `CORE-1`" in rm.read_text()
        assert inst.state["current_agent"] != "escalation"

    def test_git_failure_reraises_for_outer_handler(self, orch, monkeypatch):
        """A git CalledProcessError must propagate so run()'s outer git handler
        (which already escalates) deals with it — not be swallowed here."""
        inst, mod, tmp_path = orch
        inst.state["current_phase_raw_id"] = "CORE-1"
        rm = self._make_roadmap(tmp_path, raw_id="CORE-1")

        def _fail_git(cmd, **k):
            raise mod.subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr(mod.subprocess, "run", _fail_git)
        with pytest.raises(mod.subprocess.CalledProcessError):
            inst._flip_roadmap_checkbox_or_escalate(str(rm), 2)
