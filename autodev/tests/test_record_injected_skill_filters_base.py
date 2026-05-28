"""P1 Stage A — orchestrator's ``_record_injected_skill`` must filter base
discipline subdirectories so ``phase_state.skill_injected`` continues to
record only the *variable* phase-prefix discipline.

After P1 Stage A, every agent workspace's ``skills/`` directory contains
1–3 subdirectories per phase: two constant base disciplines
(``integration-wiring-{role}``, ``testing-quality-{role}``) plus the
phase-prefix discipline when one maps. The audit field
``phase_state.skill_injected`` is consumed by:

  * orchestrator metrics row ``skill_used`` column (orchestrator.py:3313)
  * UI snapshot endpoint (ui/server.py)
  * UI header label "Skill: X / role" (ui/index.html)
  * ``test_p0_stage_h_metrics_row_breakdown.py``

All four expect "the variable discipline for this phase" — surfacing the
constant base skills would be pure reporting noise (user-confirmed during
planning). The orchestrator therefore filters ``BASE_DISCIPLINES`` out of
the workspace listing before deriving the discipline name.
"""

import json
import os
import pathlib
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _make_workspace_with_skills(tmp_path: pathlib.Path, agent_role: str, skill_names):
    """Create workspace-{role}/skills/{name}/SKILL.md for each name in skill_names."""
    skills_dir = tmp_path / f"workspace-{agent_role}" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    for name in skill_names:
        sub = skills_dir / name
        sub.mkdir()
        (sub / "SKILL.md").write_text(f"# {name}\n")
    return skills_dir


def _stub_orchestrator(tmp_path, monkeypatch):
    """Build a bare Orchestrator instance with phase_state I/O routed at tmp_path."""
    import orchestrator as orch_mod

    monkeypatch.setattr(orch_mod, "OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(
        orch_mod, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json")
    )
    # Seed an empty phase_state so read_phase_state has something well-formed.
    (tmp_path / "phase_state.json").write_text("{}")

    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    return orch_mod, orch


def test_records_prefix_discipline_when_present(tmp_path, monkeypatch):
    """Workspace with 2 base + 1 prefix subdir → skill_injected == prefix discipline."""
    orch_mod, orch = _stub_orchestrator(tmp_path, monkeypatch)
    _make_workspace_with_skills(
        tmp_path,
        "executor",
        [
            "integration-wiring-executor",
            "testing-quality-executor",
            "core-logic-executor",
        ],
    )

    orch._record_injected_skill("executor")

    phase_state = json.loads((tmp_path / "phase_state.json").read_text())
    assert phase_state["skill_injected"] == "core-logic", (
        "skill_injected must record the variable phase-prefix discipline, "
        f"not a base-skill name. Got: {phase_state.get('skill_injected')!r}"
    )
    assert phase_state["skill_agent"] == "executor"


def test_records_none_when_only_base_skills_present(tmp_path, monkeypatch):
    """Workspace with only 2 base subdirs (unmapped/empty prefix) → skill_injected is None."""
    orch_mod, orch = _stub_orchestrator(tmp_path, monkeypatch)
    _make_workspace_with_skills(
        tmp_path,
        "executor",
        ["integration-wiring-executor", "testing-quality-executor"],
    )

    orch._record_injected_skill("executor")

    phase_state = json.loads((tmp_path / "phase_state.json").read_text())
    assert phase_state["skill_injected"] is None, (
        "When only base skills are present (no variable discipline mapped), "
        "skill_injected must be None so the audit/metrics surfaces show "
        "'no prefix discipline for this phase' rather than a base-skill name."
    )
    assert phase_state["skill_agent"] == "executor"


def test_records_none_when_workspace_empty(tmp_path, monkeypatch):
    """Workspace empty (e.g. role disabled by kill switch) → skill_injected is None."""
    orch_mod, orch = _stub_orchestrator(tmp_path, monkeypatch)
    # Create the skills/ directory but leave it empty
    (tmp_path / "workspace-executor" / "skills").mkdir(parents=True)

    orch._record_injected_skill("executor")

    phase_state = json.loads((tmp_path / "phase_state.json").read_text())
    assert phase_state["skill_injected"] is None
    assert phase_state["skill_agent"] == "executor"
