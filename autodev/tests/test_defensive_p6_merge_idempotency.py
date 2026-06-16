"""Defensive Hardening Phase 6 — Group 4: idempotent reviewer-PASS merge + phase_merged marker.

T6.4 — a crash after the phase-branch merge commit lands but before the phase advances re-enters
the reviewer-PASS block (status RUNNING + current_agent=reviewer → reviewer re-PASSes) and today
re-runs ``git merge`` on an already-merged / branch-recreated-empty branch → false ``ERR_MERGE_FAILED``
escalation on completed work. The fix:
  * a durable ``phase_merged`` marker (+ ``merge_base_branch``) in phase_state, written only after a
    confirmed rc-0 merge, self-cleared when phase_state is deleted on advance / reset;
  * a ``git merge-base --is-ancestor`` backstop so a re-merge of an already-merged branch is treated
    as success (the branch-recreated-empty sub-case where the naive re-merge would fail);
  * the roadmap flip + suggestions append are skipped on confirmed re-entry, and the flip helper
    skips its ``git commit --amend`` when the checkbox is already ``[x]`` (so re-entry doesn't churn
    the merge-commit SHA / move the --force tag).

The reviewer-PASS block is inline in run() and not callable in isolation, so these use a hybrid of
AST / source-structure guards (to pin the new control flow) plus behavioral tests on the extractable
seams (``_flip_roadmap_checkbox_or_escalate``, ``_advance_to_next_pending_phase``, ``reset_phase``,
``_write_canonical_metrics_row``).
"""
import ast
import importlib
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
ORCH_PATH = os.path.join(PIPELINE_DIR, "orchestrator.py")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _source():
    with open(ORCH_PATH, encoding="utf-8") as f:
        return f.read()


def _pass_region(src):
    """Source text of the reviewer gate_result == 'PASS' arm (merge → tag → advance)."""
    start = src.index('if gate_result == "PASS":')
    end = src.index('_advance_to_next_pending_phase(trigger="phase_complete")', start)
    return src[start:end]


class _R:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr.encode() if isinstance(stderr, str) else stderr


# ---------------------------------------------------------------------------
# Structure guards (fail before implementation, pass after)
# ---------------------------------------------------------------------------

class TestMergeBlockStructure:
    def test_merge_failure_block_still_present(self):
        """B8 — the `if merge_result.returncode != 0:` ERR_MERGE_FAILED block must survive the
        restructure (merge_result stays bound; the merge-failure-diagnosis AST test stays green)."""
        tree = ast.parse(_source())
        found = False
        for node in ast.walk(tree):
            if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                    and isinstance(node.test.ops[0], ast.NotEq)
                    and isinstance(node.test.left, ast.Attribute)
                    and node.test.left.attr == "returncode"
                    and "merge" in getattr(node.test.left.value, "id", "").lower()):
                # LAUNCH-7: ERR_MERGE_FAILED is now an imported constant (ast.Name),
                # not an inline string literal — accept either form.
                lits = {c.value for c in ast.walk(node) if isinstance(c, ast.Constant) and isinstance(c.value, str)}
                names = {c.id for c in ast.walk(node) if isinstance(c, ast.Name)}
                if "ERR_MERGE_FAILED" in lits or "ERR_MERGE_FAILED" in names:
                    found = True
        assert found, "merge-failure ERR_MERGE_FAILED block missing/renamed"

    def test_marker_written_in_pass_region(self):
        region = _pass_region(_source())
        assert "phase_merged" in region, "phase_merged marker not written in the PASS region"
        assert "merge_base_branch" in region, "merge_base_branch not persisted with the marker"

    def test_is_ancestor_backstop_present(self):
        region = _pass_region(_source())
        assert "merge-base" in region and "--is-ancestor" in region, \
            "git merge-base --is-ancestor idempotent-merge backstop missing"

    def test_reentry_marker_gates_merge_and_flip(self):
        region = _pass_region(_source())
        assert "already_merged_marker" in region, "marker-gated re-entry flag missing"
        # The roadmap flip + suggestions must be gated so confirmed re-entry doesn't re-amend/duplicate.
        assert region.count("if not already_merged_marker") >= 1

    def test_guard_read_is_resilient_to_corrupt_phase_state(self):
        region = _pass_region(_source())
        # The marker pre-read must use a dedicated guard var wrapped in try/except so a corrupt
        # phase_state (read_phase_state raises) degrades to "marker absent" rather than crashing.
        assert "_ps_guard" in region, "marker guard read (_ps_guard) missing"
        guard_at = region.index("_ps_guard")
        assert "try:" in region[:guard_at][-200:], "marker guard read must be inside a try/except"


# ---------------------------------------------------------------------------
# Behavioral seam: _flip_roadmap_checkbox_or_escalate amend-skip (B3)
# ---------------------------------------------------------------------------

@pytest.fixture
def orch(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))
    import orchestrator as orch_mod
    importlib.reload(orch_mod)
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(tmp_path))
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json"))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json"))
    inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    inst.state = {"current_phase": 1, "current_phase_raw_id": "CORE-E1", "pipeline_status": "RUNNING"}
    inst.lock_fd = None
    return inst, orch_mod, tmp_path


class TestFlipAmendIdempotent:
    def test_amend_skipped_when_box_already_x(self, orch, monkeypatch):
        inst, orch_mod, tmp_path = orch
        roadmap = tmp_path / "roadmap.md"
        roadmap.write_text("- [x] `CORE-E1` | LOW | Already done\n")
        calls = []
        monkeypatch.setattr(orch_mod.subprocess, "run", lambda cmd, **k: calls.append(cmd) or _R(0))

        assert inst._flip_roadmap_checkbox_or_escalate(str(roadmap), 1) is True
        amends = [c for c in calls if isinstance(c, list) and "commit" in c and "--amend" in c]
        assert not amends, "must NOT git commit --amend when the checkbox is already [x] (B3)"
        # File content unchanged.
        assert roadmap.read_text() == "- [x] `CORE-E1` | LOW | Already done\n"

    def test_fresh_flip_still_amends(self, orch, monkeypatch):
        inst, orch_mod, tmp_path = orch
        roadmap = tmp_path / "roadmap.md"
        roadmap.write_text("- [ ] `CORE-E1` | LOW | To do\n")
        calls = []
        monkeypatch.setattr(orch_mod.subprocess, "run", lambda cmd, **k: calls.append(cmd) or _R(0))

        assert inst._flip_roadmap_checkbox_or_escalate(str(roadmap), 1) is True
        amends = [c for c in calls if isinstance(c, list) and "commit" in c and "--amend" in c]
        assert amends, "a real flip must still fold the checkbox into the merge commit"
        assert "[x]" in roadmap.read_text()


# ---------------------------------------------------------------------------
# Behavioral seam: marker self-clears on advance / reset
# ---------------------------------------------------------------------------

class TestMarkerSelfClears:
    def test_marker_dropped_on_advance(self, orch, monkeypatch):
        inst, orch_mod, tmp_path = orch
        # Seed a phase_state carrying the marker.
        inst.write_phase_state_atomic({"phase_merged": "CORE-E1", "merge_base_branch": "main"})

        class _Run:
            def __call__(self, cmd, **k):
                if isinstance(cmd, list) and any("phase_resolver" in str(x) for x in cmd):
                    with open(tmp_path / "current_phase.json", "w") as f:
                        json.dump({"phase_number": 2, "raw_id": "CORE-E2"}, f)
                    return _R(0, "PENDING: Phase CORE-E2 identified.")
                if isinstance(cmd, list) and cmd[:2] == ["git", "rev-parse"]:
                    return _R(0, "base123\n")
                return _R(0, "")
        monkeypatch.setattr(orch_mod.subprocess, "run", _Run())
        monkeypatch.setattr(orch_mod.time, "sleep", lambda *a, **k: None)

        inst._advance_to_next_pending_phase(trigger="phase_complete")
        # phase_state.json is deleted on advance → the marker is gone.
        assert not (tmp_path / "phase_state.json").exists()

    def test_marker_not_preserved_across_reset_phase(self, orch, monkeypatch):
        inst, orch_mod, tmp_path = orch
        inst.write_phase_state_atomic({
            "phase_merged": "CORE-E1", "merge_base_branch": "main",
            "escalation_resets": 1,
        })
        inst.state["phase_base_commit"] = "base123"
        inst.openclaw_config = {}
        monkeypatch.setattr(orch_mod.subprocess, "run", lambda cmd, **k: _R(0, ""))
        monkeypatch.setattr(orch_mod, "_write_pipeline_event", MagicMock())

        ok = inst.reset_phase()
        assert ok is True
        ps = inst.read_phase_state()
        assert "phase_merged" not in ps, "phase_merged must NOT survive reset_phase (it self-clears)"
        assert ps.get("escalation_resets") == 1, "governance counters are still preserved"


# ---------------------------------------------------------------------------
# Reader safety: metrics row ignores the additive phase_merged key
# ---------------------------------------------------------------------------

class TestReaderSafety:
    def test_metrics_row_ignores_phase_merged(self, orch, monkeypatch):
        inst, orch_mod, tmp_path = orch
        inst.write_phase_state_atomic({"phase_merged": "CORE-E1", "merge_base_branch": "main"})
        inst.openclaw_config = {}
        # Should not raise on the additive key.
        inst._write_canonical_metrics_row()
