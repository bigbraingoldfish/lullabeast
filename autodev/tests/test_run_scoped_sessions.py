"""Run-scoping of pipeline session keys and per-phase overrides.

A pipeline session's model is frozen when the session is first created, and the
session key was reused across runs (and across projects sharing a phase id), so
a re-run or a fresh project reattached a prior run's session and silently ran
its stale model. Session keys now fold in a short slice of ``run_id`` (minted
per run, preserved across resume), so a new run gets a fresh session baked with
the current model while a resume reattaches the same run's session.

The same run scoping fixes the sibling "stale override on re-run" bug: per-phase
overrides are cleared at run start when they belong to a previous run.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402


def _orch(state=None):
    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = state if state is not None else {}
    return orch


# ── _run_suffix ──────────────────────────────────────────────────────────────

class TestRunSuffix:
    def test_empty_without_run_id(self):
        # Legacy / unit-test path: no run_id => byte-identical legacy keys.
        assert _orch({})._run_suffix() == ""
        assert _orch({"run_id": ""})._run_suffix() == ""
        assert _orch(None)._run_suffix() == ""

    def test_token_derived_from_run_id(self):
        s = _orch({"run_id": "1234abcd-5678-90ef-aaaa-bbbbbbbbbbbb"})._run_suffix()
        # -r + first 8 hex of the dash-stripped uuid.
        assert s == "-r1234abcd"

    def test_distinct_runs_distinct_suffix_same_run_stable(self):
        a = _orch({"run_id": "aaaaaaaa-0000-0000-0000-000000000000"})._run_suffix()
        b = _orch({"run_id": "bbbbbbbb-0000-0000-0000-000000000000"})._run_suffix()
        a2 = _orch({"run_id": "aaaaaaaa-0000-0000-0000-000000000000"})._run_suffix()
        assert a != b            # a re-run / different project gets a fresh session
        assert a == a2           # a resume reattaches the same run's session

    def test_suffix_appended_outermost_preserves_pipeline_prefix(self):
        # The run-exit sweep matches on the ``pipeline:`` prefix; appending the
        # token keeps that prefix and the legacy base intact.
        base = "pipeline:phase-2:CORE-1:planner-attempt-1"
        key = base + _orch({"run_id": "deadbeef-0000-0000-0000-000000000000"})._run_suffix()
        assert key.startswith("pipeline:")
        assert key.startswith(base)
        assert key == "pipeline:phase-2:CORE-1:planner-attempt-1-rdeadbeef"


# ── _reconcile_phase_overrides_for_run ───────────────────────────────────────

class TestOverrideRunReconcile:
    def _paths(self, monkeypatch, tmp_path):
        ov = tmp_path / "phase_model_overrides.json"
        run = tmp_path / "phase_model_overrides.run"
        monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
        monkeypatch.setattr(orch_mod, "PHASE_MODEL_OVERRIDES_FILE", str(ov))
        monkeypatch.setattr(orch_mod, "PHASE_MODEL_OVERRIDES_RUN_FILE", str(run))
        return ov, run

    def test_prior_run_overrides_are_cleared_and_restamped(self, monkeypatch, tmp_path):
        ov, run = self._paths(monkeypatch, tmp_path)
        ov.write_text('{"UI-E1": {"planner": "openrouter/x-ai/grok-4.5"}}')
        run.write_text("old-run-id")
        _orch({"run_id": "new-run-id"})._reconcile_phase_overrides_for_run()
        assert not ov.exists()                       # leftover dropped
        assert run.read_text().strip() == "new-run-id"  # sidecar re-stamped

    def test_same_run_overrides_survive(self, monkeypatch, tmp_path):
        # A resume keeps the same run_id, so its overrides must be preserved.
        ov, run = self._paths(monkeypatch, tmp_path)
        ov.write_text('{"UI-E1": {"planner": "openrouter/z-ai/glm-5.2"}}')
        run.write_text("run-42")
        _orch({"run_id": "run-42"})._reconcile_phase_overrides_for_run()
        assert ov.exists()
        assert "glm-5.2" in ov.read_text()

    def test_first_run_stamps_sidecar(self, monkeypatch, tmp_path):
        ov, run = self._paths(monkeypatch, tmp_path)  # neither file exists
        _orch({"run_id": "first"})._reconcile_phase_overrides_for_run()
        assert run.read_text().strip() == "first"

    def test_noop_without_run_id(self, monkeypatch, tmp_path):
        ov, run = self._paths(monkeypatch, tmp_path)
        ov.write_text('{"UI-E1": {"planner": "m"}}')
        _orch({})._reconcile_phase_overrides_for_run()
        assert ov.exists()          # legacy path never touches the file
        assert not run.exists()
