"""
Tests for the P1 Stage A base-skill injection model in SkillManager.

P1 Stage A change: SkillManager always injects ``integration-wiring/{role}/SKILL.md``
and ``testing-quality/{role}/SKILL.md`` for every phase, on top of the existing
phase-prefix discipline skill.  Workspace cleanup between phases continues to wipe
everything; the prefix-skill audit field (phase_state.skill_injected) continues to
report only the variable discipline — base skills are filtered out of that audit by
the orchestrator (see test_record_injected_skill_filters_base.py).

Tests in this file pin:
  * Base skills are present on mapped, unmapped, and empty-phase paths
  * Base skills are wiped between phases alongside the prefix skill (no stale leak)
  * Global / per-role kill switches suppress base skills too
  * A missing base-skill source does not strip the rest (graceful degradation)
  * One [SKILL] Status=loaded log line is emitted per skill injected, with
    ``base=true|false`` tokens distinguishing base from prefix.
"""

import os
from unittest.mock import patch

from skill_manager import SkillManager


# ---------------------------------------------------------------------------
# Helpers — duplicated from test_skill_manager.py.  See plan §2.1: an 8-test
# file does not warrant a shared conftest fixture; duplication beats premature
# abstraction here.
# ---------------------------------------------------------------------------

# The two disciplines P1 Stage A always injects, for every phase, on every role.
# Pinned here so a future rename of ``SkillManager.BASE_DISCIPLINES`` trips a
# clear test failure rather than silently changing pipeline behaviour.
BASE_DISCIPLINES = ("integration-wiring", "testing-quality")


def make_skill_library(base_dir, disciplines, roles):
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


def make_mapping_file(config_dir, mapping):
    os.makedirs(config_dir, exist_ok=True)
    mapping_path = os.path.join(config_dir, "skill_mapping.yaml")
    lines = ["# test mapping\n"]
    for k, v in mapping.items():
        lines.append(f"{k}: {v}\n")
    with open(mapping_path, "w") as f:
        f.writelines(lines)
    return mapping_path


def make_workspace(base_dir, agent_role):
    ws = os.path.join(base_dir, f"workspace-{agent_role}")
    os.makedirs(ws, exist_ok=True)
    return ws


def skills_enabled_config(enabled=True, planner=True, executor=True, reviewer=True):
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


def build_manager_with_base(tmp_path, prefix_disciplines=None, mapping=None):
    """Build a SkillManager whose library includes the base + prefix disciplines."""
    prefix_disciplines = prefix_disciplines or ["core-logic"]
    mapping = mapping or {"CORE": "core-logic"}

    disciplines = list(BASE_DISCIPLINES) + list(prefix_disciplines)
    make_skill_library(str(tmp_path), disciplines, ["planner", "executor", "reviewer"])
    make_mapping_file(os.path.join(str(tmp_path), "autodev", "config"), mapping)
    for role in ("planner", "executor", "reviewer"):
        make_workspace(str(tmp_path), role)

    with patch.dict(os.environ, {"AUTODEV_REPO_PATH": str(tmp_path)}):
        return SkillManager(str(tmp_path))


# ---------------------------------------------------------------------------
# 1.  Base skills present on every phase shape
# ---------------------------------------------------------------------------

def test_base_skills_injected_on_mapped_phase(tmp_path):
    """CORE-1 executor → workspace has 3 subdirs: 2 base + 1 prefix."""
    sm = build_manager_with_base(tmp_path)
    sm.inject_skill("CORE-1", "executor", skills_enabled_config())

    skills_dir = tmp_path / "workspace-executor" / "skills"
    assert {p.name for p in skills_dir.iterdir()} == {
        "integration-wiring-executor",
        "testing-quality-executor",
        "core-logic-executor",
    }
    for name in (
        "integration-wiring-executor",
        "testing-quality-executor",
        "core-logic-executor",
    ):
        assert (skills_dir / name / "SKILL.md").exists(), f"{name}/SKILL.md missing"


def test_base_skills_injected_when_prefix_unmapped(tmp_path):
    """Unmapped prefix (OPS) → 2 base subdirs only, no prefix skill."""
    sm = build_manager_with_base(tmp_path)
    sm.inject_skill("OPS-1", "executor", skills_enabled_config())

    skills_dir = tmp_path / "workspace-executor" / "skills"
    assert {p.name for p in skills_dir.iterdir()} == {
        "integration-wiring-executor",
        "testing-quality-executor",
    }


def test_base_skills_injected_when_phase_id_empty(tmp_path):
    """Empty phase_raw_id → 2 base subdirs; prefix lookup skipped silently."""
    sm = build_manager_with_base(tmp_path)
    sm.inject_skill("", "executor", skills_enabled_config())

    skills_dir = tmp_path / "workspace-executor" / "skills"
    assert {p.name for p in skills_dir.iterdir()} == {
        "integration-wiring-executor",
        "testing-quality-executor",
    }


def test_base_skills_per_role(tmp_path):
    """Each role gets its own role-tagged base + prefix skills."""
    sm = build_manager_with_base(tmp_path)
    cfg = skills_enabled_config()

    for role in ("planner", "executor", "reviewer"):
        sm.inject_skill("CORE-1", role, cfg)
        skills_dir = tmp_path / f"workspace-{role}" / "skills"
        expected = {
            f"integration-wiring-{role}",
            f"testing-quality-{role}",
            f"core-logic-{role}",
        }
        assert {p.name for p in skills_dir.iterdir()} == expected, (
            f"workspace-{role}/skills/ contents mismatch"
        )


# ---------------------------------------------------------------------------
# 2.  Wipe-between-phases preserved
# ---------------------------------------------------------------------------

def test_clean_wipes_base_skills_between_phases(tmp_path):
    """Phase N's prefix skill is gone after phase N+1 (only correct base + new prefix remain)."""
    sm = build_manager_with_base(
        tmp_path,
        prefix_disciplines=["core-logic", "ui-frontend"],
        mapping={"CORE": "core-logic", "UI": "ui-frontend"},
    )
    cfg = skills_enabled_config()

    sm.inject_skill("CORE-1", "executor", cfg)
    sm.inject_skill("UI-2", "executor", cfg)

    skills_dir = tmp_path / "workspace-executor" / "skills"
    assert {p.name for p in skills_dir.iterdir()} == {
        "integration-wiring-executor",
        "testing-quality-executor",
        "ui-frontend-executor",
    }, "Stale core-logic-executor must not survive the phase change"


# ---------------------------------------------------------------------------
# 3.  Kill switches suppress base skills too
# ---------------------------------------------------------------------------

def test_global_disabled_skips_base_skills_too(tmp_path):
    """skills.enabled=false → workspace empty (no base, no prefix)."""
    sm = build_manager_with_base(tmp_path)
    sm.inject_skill("CORE-1", "executor", skills_enabled_config(enabled=False))

    skills_dir = tmp_path / "workspace-executor" / "skills"
    assert list(skills_dir.iterdir()) == []


def test_per_role_disabled_skips_base_skills_too(tmp_path):
    """executor_skills_enabled=false → executor empty; planner/reviewer get all 3."""
    sm = build_manager_with_base(tmp_path)
    cfg = skills_enabled_config(executor=False)

    sm.inject_skill("CORE-1", "planner", cfg)
    sm.inject_skill("CORE-1", "executor", cfg)
    sm.inject_skill("CORE-1", "reviewer", cfg)

    executor_dir = tmp_path / "workspace-executor" / "skills"
    assert list(executor_dir.iterdir()) == []

    for role in ("planner", "reviewer"):
        d = tmp_path / f"workspace-{role}" / "skills"
        assert {p.name for p in d.iterdir()} == {
            f"integration-wiring-{role}",
            f"testing-quality-{role}",
            f"core-logic-{role}",
        }


# ---------------------------------------------------------------------------
# 4.  Graceful degradation when a base-skill source is missing
# ---------------------------------------------------------------------------

def test_missing_base_skill_source_still_injects_other_base_and_prefix(tmp_path, capsys):
    """A missing base-skill source file should not strip the rest of the skills."""
    sm = build_manager_with_base(tmp_path)
    src = tmp_path / "autodev" / "skill-library" / "testing-quality" / "executor" / "SKILL.md"
    src.unlink()

    sm.inject_skill("CORE-1", "executor", skills_enabled_config())

    skills_dir = tmp_path / "workspace-executor" / "skills"
    assert {p.name for p in skills_dir.iterdir()} == {
        "integration-wiring-executor",
        "core-logic-executor",
    }
    out = capsys.readouterr().out
    assert "testing-quality" in out
    assert "Status=none_found" in out


# ---------------------------------------------------------------------------
# 5.  Logging contract: one Status=loaded line per skill, base|prefix tagged
# ---------------------------------------------------------------------------

def test_log_emits_one_skill_line_per_injected_skill(tmp_path, capsys):
    """One [SKILL] Status=loaded line per skill injected; base lines tagged base=true."""
    sm = build_manager_with_base(tmp_path)
    sm.inject_skill("CORE-1", "executor", skills_enabled_config())

    out = capsys.readouterr().out
    loaded_lines = [line for line in out.splitlines() if "Status=loaded" in line]
    assert len(loaded_lines) == 3, f"Expected 3 loaded lines, got: {loaded_lines}"

    base_lines = [line for line in loaded_lines if "base=true" in line]
    prefix_lines = [line for line in loaded_lines if "base=false" in line]
    assert len(base_lines) == 2, f"Expected 2 base=true lines, got: {base_lines}"
    assert len(prefix_lines) == 1, f"Expected 1 base=false line, got: {prefix_lines}"
