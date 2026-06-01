"""Drift guard: every phase-prefix → discipline mapping must resolve to a real skill on disk.

`autodev/config/skill_mapping.yaml` maps 8 roadmap subsystem prefixes to skill
disciplines (INFRA→infra-config, CORE→core-logic, DATA→data-persistence,
API→api-service, AUTH→auth-security, UI→ui-frontend, CLI→cli-tooling,
COMPLETE→completion). At runtime `SkillManager.inject_skill()` looks up the
discipline and copies `autodev/skill-library/<discipline>/<role>/SKILL.md` into the
agent workspace. If a mapped discipline (or an expected role file) is missing, the
manager logs `Status=none_found` and the agent runs with NO discipline skill — a
SILENT degradation (graceful-by-design), not a crash. The YAML and the directory
tree are edited independently, so a typo or a renamed/deleted directory can drift
without anything failing loudly. This test pins the invariant the loader cannot.

We read the YAML directly with the same `safe_load` + uppercase/strip normalization
as `SkillManager._load_mapping`, rather than constructing `SkillManager`, because the
constructor writes `skill_health.json` into `OPENCLAW_ROOT` (`~/.openclaw`) — an
unwanted filesystem side effect for a read-only test.

Role policy: the 7 standard disciplines need planner+executor+reviewer SKILL.md;
`completion` is intentionally reviewer-only (it backs COMPLETE phases, which have no
plan/execute step). The expected-roles table makes this explicit and self-documenting.
We assert mapped→exists, NEVER the reverse: unmapped directories (`prd-creator/`,
`roadmap-converter/`, a possible `legacy/`) are legitimate and ignored. Adding a new
mapping with no skill files on disk FAILS this test loudly.
"""
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]  # autodev/tests/ -> repo root
_MAPPING_FILE = _REPO_ROOT / "autodev" / "config" / "skill_mapping.yaml"
_SKILL_LIBRARY = _REPO_ROOT / "autodev" / "skill-library"

# Roles every standard mapped discipline must provide a SKILL.md for.
STANDARD_ROLES = ("planner", "executor", "reviewer")

# `completion` backs COMPLETE phases (no plan/execute step) and is intentionally
# reviewer-only. Any discipline NOT listed here is held to STANDARD_ROLES.
SPECIAL_DISCIPLINE_ROLES = {
    "completion": ("reviewer",),
}

# The documented 8-prefix contract, pinned so an accidental key/value rename — which
# would silently re-route or drop a phase's skill — is caught at the YAML level.
EXPECTED_MAPPING = {
    "INFRA": "infra-config",
    "CORE": "core-logic",
    "DATA": "data-persistence",
    "API": "api-service",
    "AUTH": "auth-security",
    "UI": "ui-frontend",
    "CLI": "cli-tooling",
    "COMPLETE": "completion",
}


def _load_mapping() -> dict:
    """Mirror SkillManager._load_mapping: uppercase keys, stripped string values."""
    raw = yaml.safe_load(_MAPPING_FILE.read_text(encoding="utf-8"))
    assert isinstance(raw, dict), f"{_MAPPING_FILE} did not parse to a dict"
    return {str(k).upper(): str(v).strip() for k, v in raw.items() if k and v}


def _expected_roles(discipline: str) -> tuple:
    return SPECIAL_DISCIPLINE_ROLES.get(discipline, STANDARD_ROLES)


_MAPPING = _load_mapping()
_DISCIPLINES = sorted(set(_MAPPING.values()))
_DISCIPLINE_ROLE_PAIRS = [(d, r) for d in _DISCIPLINES for r in _expected_roles(d)]


def test_mapping_matches_expected_prefix_contract():
    """The phase-prefix → discipline contract is fixed. A rename silently changes
    which skill a phase resolves to (and an empty mapping would make the
    per-discipline checks below pass vacuously — this catches that too)."""
    assert _MAPPING == EXPECTED_MAPPING, (
        f"skill_mapping.yaml resolved to {_MAPPING} but expected {EXPECTED_MAPPING}. "
        "A prefix or discipline name changed; phase-prefix → discipline resolution "
        "drifted from the documented contract. Confirm the rename is intentional, "
        "update this pin, and rename the matching skill-library directory."
    )


@pytest.mark.parametrize("discipline", _DISCIPLINES)
def test_mapped_discipline_directory_exists(discipline):
    disc_dir = _SKILL_LIBRARY / discipline
    assert disc_dir.is_dir(), (
        f"skill_mapping.yaml maps a phase prefix to discipline {discipline!r}, but "
        f"{disc_dir} does not exist. Every mapped discipline MUST resolve to a skill "
        "directory — otherwise SkillManager.inject_skill logs Status=none_found and "
        "the agent silently runs with NO discipline skill on those phases. Add the "
        "directory or remove the mapping."
    )


@pytest.mark.parametrize("discipline,role", _DISCIPLINE_ROLE_PAIRS)
def test_mapped_discipline_role_skill_present_and_nonempty(discipline, role):
    skill_md = _SKILL_LIBRARY / discipline / role / "SKILL.md"
    assert skill_md.is_file(), (
        f"Missing skill: {skill_md.relative_to(_REPO_ROOT)}, required because "
        f"{discipline!r} is mapped in skill_mapping.yaml and {role!r} is an expected "
        "role for it (policy: completion is reviewer-only; every other mapped "
        f"discipline needs planner+executor+reviewer). Without it the {role} agent "
        f"gets no discipline skill on {discipline} phases — silent context loss. Add "
        "the SKILL.md, or update SPECIAL_DISCIPLINE_ROLES if this discipline is "
        "intentionally role-limited."
    )
    assert skill_md.read_text(encoding="utf-8").strip(), (
        f"{skill_md.relative_to(_REPO_ROOT)} exists but is empty — an empty skill "
        "injects no guidance yet passes a naive existence check. Populate it."
    )
