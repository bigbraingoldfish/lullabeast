"""
Tests for skill_manager.SkillManager.

All tests use tmp_path so the real ~/.openclaw workspace is never touched.
The conftest.py in this package wires OPENCLAW_DIR into sys.path, so
SkillManager is importable without installation.
"""

import os
import shutil
from unittest.mock import patch

import pytest
import yaml

from skill_manager import SkillManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_skill_library(base_dir: str, disciplines: list[str], roles: list[str]) -> str:
    """Create a minimal skill library under base_dir/autodev/skill-library/."""
    lib = os.path.join(base_dir, "autodev", "skill-library")
    for discipline in disciplines:
        for role in roles:
            d = os.path.join(lib, discipline, role)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "SKILL.md"), "w") as f:
                f.write(
                    f"---\nname: {discipline}-{role}\n"
                    f"description: Test skill for {discipline} / {role}\n---\n"
                    f"\n# {discipline} / {role} skill\n"
                )
    return lib


def make_mapping_file(config_dir: str, mapping: dict) -> str:
    """Write a skill_mapping.yaml under config_dir."""
    os.makedirs(config_dir, exist_ok=True)
    mapping_path = os.path.join(config_dir, "skill_mapping.yaml")
    lines = ["# test mapping\n"]
    for k, v in mapping.items():
        lines.append(f"{k}: {v}\n")
    with open(mapping_path, "w") as f:
        f.writelines(lines)
    return mapping_path


def make_workspace(base_dir: str, agent_role: str) -> str:
    """Create a bare workspace-{agent_role} directory."""
    ws = os.path.join(base_dir, f"workspace-{agent_role}")
    os.makedirs(ws, exist_ok=True)
    return ws


def skills_enabled_config(
    enabled=True,
    planner=True,
    executor=True,
    reviewer=True,
) -> dict:
    return {
        "pipeline": {
            "skills": {
                "enabled": enabled,
                "planner_skills_enabled": planner,
                "executor_skills_enabled": executor,
                "reviewer_skills_enabled": reviewer,
            }
        }
    }


def build_manager(tmp_path, disciplines=None, mapping=None) -> SkillManager:
    """Build a SkillManager wired to tmp_path, with optional pre-populated library."""
    disciplines = disciplines or ["core-logic"]
    mapping = mapping or {"CORE": "core-logic"}

    make_skill_library(str(tmp_path), disciplines, ["planner", "executor", "reviewer"])
    make_mapping_file(os.path.join(str(tmp_path), "autodev", "config"), mapping)
    for role in ("planner", "executor", "reviewer"):
        make_workspace(str(tmp_path), role)

    with patch.dict(os.environ, {"AUTODEV_REPO_PATH": str(tmp_path)}):
        return SkillManager(str(tmp_path))


# ---------------------------------------------------------------------------
# 1. Subsystem mapping resolution
# ---------------------------------------------------------------------------

def test_resolve_mapped_subsystem(tmp_path):
    """CORE-E2 + executor should resolve to core-logic/executor/SKILL.md."""
    sm = build_manager(tmp_path)
    config = skills_enabled_config()
    sm.inject_skill("CORE-E2", "executor", config)

    dest = tmp_path / "workspace-executor" / "skills" / "core-logic-executor" / "SKILL.md"
    assert dest.exists(), "SKILL.md should have been injected into workspace-executor/skills/"


def test_resolve_unmapped_subsystem(tmp_path, capsys):
    """MCP-E3 has no mapping — workspace should be cleaned, Status=none_mapped logged."""
    sm = build_manager(tmp_path)
    config = skills_enabled_config()

    # Pre-seed a stale skill to verify it gets cleaned
    stale_dir = tmp_path / "workspace-executor" / "skills" / "stale-skill"
    stale_dir.mkdir(parents=True)
    (stale_dir / "SKILL.md").write_text("stale")

    sm.inject_skill("MCP-E3", "executor", config)

    skills_dir = tmp_path / "workspace-executor" / "skills"
    # directory exists but should be empty (cleaned, nothing injected)
    assert skills_dir.exists()
    assert list(skills_dir.iterdir()) == [], "Stale skill should have been removed"
    captured = capsys.readouterr()
    assert "Status=none_mapped" in captured.out


def test_resolve_missing_file(tmp_path, capsys):
    """Mapping entry exists but skill file is absent → Status=none_found."""
    make_mapping_file(os.path.join(str(tmp_path), "autodev", "config"), {"CORE": "core-logic"})
    # No skill-library created
    make_workspace(str(tmp_path), "executor")

    with patch.dict(os.environ, {"AUTODEV_REPO_PATH": str(tmp_path)}):
        sm = SkillManager(str(tmp_path))
    sm.inject_skill("CORE-E2", "executor", skills_enabled_config())

    captured = capsys.readouterr()
    assert "Status=none_found" in captured.out


# ---------------------------------------------------------------------------
# 2. Injection correctness
# ---------------------------------------------------------------------------

def test_inject_skill_copies_file(tmp_path):
    """inject_skill places file at workspace-executor/skills/core-logic-executor/SKILL.md."""
    sm = build_manager(tmp_path)
    sm.inject_skill("CORE-1", "executor", skills_enabled_config())

    dest = tmp_path / "workspace-executor" / "skills" / "core-logic-executor" / "SKILL.md"
    assert dest.exists()
    content = dest.read_text()
    assert "core-logic" in content


def test_inject_skill_cleans_stale_before_injecting(tmp_path):
    """A skill from phase N is removed before injecting the skill for phase N+1."""
    sm = build_manager(
        tmp_path,
        disciplines=["core-logic", "infra-config"],
        mapping={"CORE": "core-logic", "INFRA": "infra-config"},
    )
    cfg = skills_enabled_config()

    # Phase 1 — CORE
    sm.inject_skill("CORE-1", "executor", cfg)
    core_dest = tmp_path / "workspace-executor" / "skills" / "core-logic-executor" / "SKILL.md"
    assert core_dest.exists()

    # Phase 2 — INFRA (should clean CORE skill and inject INFRA)
    sm.inject_skill("INFRA-2", "executor", cfg)
    assert not core_dest.exists(), "Stale CORE skill should have been removed"
    infra_dest = tmp_path / "workspace-executor" / "skills" / "infra-config-executor" / "SKILL.md"
    assert infra_dest.exists()


def test_same_phase_different_roles(tmp_path):
    """Each role gets its own discipline+role-specific skill for the same phase."""
    sm = build_manager(tmp_path)
    cfg = skills_enabled_config()

    for role in ("planner", "executor", "reviewer"):
        sm.inject_skill("CORE-1", role, cfg)
        dest = tmp_path / f"workspace-{role}" / "skills" / f"core-logic-{role}" / "SKILL.md"
        assert dest.exists(), f"SKILL.md missing for {role}"
        assert f"core-logic-{role}" in dest.read_text()


# ---------------------------------------------------------------------------
# 3. Toggle flags
# ---------------------------------------------------------------------------

def test_global_disabled_cleans_all_workspaces(tmp_path):
    """skills.enabled=false → no skills injected, any stale skill removed."""
    sm = build_manager(tmp_path)

    # Pre-seed stale skill
    stale = tmp_path / "workspace-executor" / "skills" / "old-skill"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("stale")

    sm.inject_skill("CORE-1", "executor", skills_enabled_config(enabled=False))

    skills_dir = tmp_path / "workspace-executor" / "skills"
    assert skills_dir.exists()
    assert list(skills_dir.iterdir()) == []


def test_per_agent_disabled_reviewer(tmp_path, capsys):
    """reviewer_skills_enabled=false → reviewer workspace cleaned, planner/executor unaffected."""
    sm = build_manager(tmp_path)
    cfg = skills_enabled_config(reviewer=False)

    sm.inject_skill("CORE-1", "planner", cfg)
    sm.inject_skill("CORE-1", "executor", cfg)
    sm.inject_skill("CORE-1", "reviewer", cfg)

    planner_dest  = tmp_path / "workspace-planner"  / "skills" / "core-logic-planner"  / "SKILL.md"
    executor_dest = tmp_path / "workspace-executor" / "skills" / "core-logic-executor" / "SKILL.md"
    reviewer_dir  = tmp_path / "workspace-reviewer"  / "skills"

    assert planner_dest.exists(),  "Planner skill should be loaded"
    assert executor_dest.exists(), "Executor skill should be loaded"
    assert list(reviewer_dir.iterdir()) == [], "Reviewer skills dir should be empty"

    captured = capsys.readouterr()
    assert "reviewer_skills_disabled" in captured.out


# ---------------------------------------------------------------------------
# 4. Graceful degradation
# ---------------------------------------------------------------------------

def test_missing_mapping_file(tmp_path, capsys):
    """No skill_mapping.yaml → no skills loaded, no crash."""
    # Create library but NO mapping file
    make_skill_library(str(tmp_path), ["core-logic"], ["executor"])
    make_workspace(str(tmp_path), "executor")

    with patch.dict(os.environ, {"AUTODEV_REPO_PATH": str(tmp_path)}):
        sm = SkillManager(str(tmp_path))  # mapping file missing — warns at init
    sm.inject_skill("CORE-1", "executor", skills_enabled_config())

    dest = tmp_path / "workspace-executor" / "skills" / "core-logic-executor" / "SKILL.md"
    assert not dest.exists(), "No skill should be injected when mapping is missing"
    captured = capsys.readouterr()
    assert "none_mapped" in captured.out or "WARN" in captured.out


def test_bad_yaml_mapping(tmp_path, capsys):
    """Malformed YAML in mapping file → graceful, no skill loaded, no crash."""
    config_dir = os.path.join(str(tmp_path), "autodev", "config")
    os.makedirs(config_dir, exist_ok=True)
    with open(os.path.join(config_dir, "skill_mapping.yaml"), "w") as f:
        f.write(": : : this is not valid yaml :\n  bad: [unclosed\n")

    make_skill_library(str(tmp_path), ["core-logic"], ["executor"])
    make_workspace(str(tmp_path), "executor")

    with patch.dict(os.environ, {"AUTODEV_REPO_PATH": str(tmp_path)}):
        sm = SkillManager(str(tmp_path))  # bad YAML — should warn, not crash
    sm.inject_skill("CORE-1", "executor", skills_enabled_config())

    dest = tmp_path / "workspace-executor" / "skills" / "core-logic-executor" / "SKILL.md"
    assert not dest.exists()
    captured = capsys.readouterr()
    assert "ERROR" in captured.out or "WARN" in captured.out


def test_empty_phase_id(tmp_path, capsys):
    """Empty phase_raw_id → none_mapped, no crash, nothing injected."""
    sm = build_manager(tmp_path)
    sm.inject_skill("", "executor", skills_enabled_config())

    captured = capsys.readouterr()
    assert "none_mapped" in captured.out or "empty_phase_id" in captured.out

    skills_dir = tmp_path / "workspace-executor" / "skills"
    assert list(skills_dir.iterdir()) == [], "Empty phase_raw_id injects nothing"


def test_default_enabled_when_config_absent(tmp_path):
    """If openclaw.json has no pipeline.skills block, skills default to enabled."""
    sm = build_manager(tmp_path)
    sm.inject_skill("CORE-1", "executor", {})   # empty config — defaults to enabled

    dest = tmp_path / "workspace-executor" / "skills" / "core-logic-executor" / "SKILL.md"
    assert dest.exists(), "Skills should default to enabled when config key is absent"


# ---------------------------------------------------------------------------
# 5. Logging
# ---------------------------------------------------------------------------

def test_inject_logs_skill_status(tmp_path, capsys):
    """[SKILL] log line: exactly one Status=loaded line for the single injected discipline.

    Single-discipline injection means one prefix skill per phase = one loaded line.
    The line carries Phase, Agent, and the Skill identifier.
    """
    sm = build_manager(tmp_path)
    sm.inject_skill("CORE-E2", "executor", skills_enabled_config())

    out = capsys.readouterr().out
    assert "[SKILL]" in out
    assert "Phase=CORE-E2" in out
    assert "Agent=executor" in out
    loaded_lines = [line for line in out.splitlines() if "Status=loaded" in line]
    assert len(loaded_lines) == 1, (
        f"Expected exactly 1 Status=loaded line, got: {loaded_lines}"
    )
    assert "core-logic/executor/SKILL.md" in out


def test_log_emitted_for_disabled_case(tmp_path, capsys):
    """[SKILL] line is still emitted when skills are globally disabled."""
    sm = build_manager(tmp_path)
    sm.inject_skill("CORE-1", "executor", skills_enabled_config(enabled=False))

    out = capsys.readouterr().out
    assert "[SKILL]" in out
    assert "Status=disabled" in out


# ---------------------------------------------------------------------------
# 6. Refactor regression guards — these pin the ABSENCE of the P1 Stage A
#    "base skills" mechanism (refactored into role AGENTS.md). They exist so a
#    future contributor cannot silently re-introduce always-injected skills,
#    re-add base/prefix log tagging, or re-map a deleted discipline and keep
#    the suite green. See plans/Active/p1-stage-a-refactor-universal-rules-into-agents-md.md.
# ---------------------------------------------------------------------------

def test_base_disciplines_constant_absent():
    """SkillManager must NOT carry a BASE_DISCIPLINES constant.

    The always-inject base-skill mechanism was removed; universal rules now
    live in each role's AGENTS.md. A re-introduced constant would resurrect the
    semantic the refactor deliberately retired.
    """
    assert not hasattr(SkillManager, "BASE_DISCIPLINES"), (
        "SkillManager.BASE_DISCIPLINES was removed in the P1 Stage A refactor "
        "(universal rules moved to AGENTS.md). Re-adding it re-introduces the "
        "always-injected-skill mechanism that was deliberately un-shipped."
    )


def test_no_base_token_emitted_in_log_lines(tmp_path, capsys):
    """No [SKILL] log line carries a base=true|false token post-refactor.

    Single-discipline injection has no base/prefix distinction, so the log
    tagging that distinguished the two layers is gone.
    """
    sm = build_manager(tmp_path)
    sm.inject_skill("CORE-E2", "executor", skills_enabled_config())

    out = capsys.readouterr().out
    assert "base=true" not in out, "base=true log token must not be emitted post-refactor"
    assert "base=false" not in out, "base=false log token must not be emitted post-refactor"


def test_deleted_disciplines_have_no_surviving_mapping():
    """No entry in the production skill_mapping.yaml may point at a deleted discipline.

    `integration-wiring` and `testing-quality` were removed from the skill
    library (their content lives in AGENTS.md now). Any surviving mapping value
    pointing at them would resolve to a non-existent directory (none_found)
    mid-pipeline. This guards against re-adding INTEGRATION/TEST/E2E (or any
    new key) that resurrects a reference to a deleted directory.

    Static-on-production check (not build_manager's synthetic mapping): the
    real autodev/config/skill_mapping.yaml is the file the refactor edits.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    mapping_file = os.path.join(repo_root, "autodev", "config", "skill_mapping.yaml")
    with open(mapping_file, "r") as fh:
        raw = yaml.safe_load(fh)
    values = {str(v).strip() for v in raw.values()} if isinstance(raw, dict) else set()

    assert "integration-wiring" not in values, (
        "skill_mapping.yaml still maps a prefix to 'integration-wiring', but that "
        "discipline directory was deleted in the P1 Stage A refactor. Remove the "
        "mapping (INTEGRATION) — the rules live in AGENTS.md now."
    )
    assert "testing-quality" not in values, (
        "skill_mapping.yaml still maps a prefix to 'testing-quality', but that "
        "discipline directory was deleted in the P1 Stage A refactor. Remove the "
        "mappings (TEST, E2E) — the rules live in AGENTS.md now."
    )
