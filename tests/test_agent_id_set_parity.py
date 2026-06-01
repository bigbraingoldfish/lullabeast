"""Drift guard: the AutoDev agent roster across its four hardcoded copies.

The set of pipeline agents — planner, executor, reviewer, escalation, prd-creator,
roadmap-converter — is declared independently in four places that must agree:

  1. autodev/installer/register_agent.py  AUTODEV_AGENT_IDS  (tuple, SOURCE OF TRUTH;
     drives agent registration + ordered append into openclaw.json).
  2. ui/server.py  _WORKSPACE_SYNC_AGENT_IDS  (tuple; drives the workspace-deploy step;
     its own comment says "kept in sync with install.sh").
  3. install.sh  `for agent in <literal list>; do` at five sites. This is a hardcoded
     literal, NOT a glob, so a missed edit drifts silently on fresh installs.
  4. autodev/agents/<id>/  the on-disk agent directories the installer deploys from.

If these drift, a fresh install can register an agent that never receives its
workspace files, or try to deploy files for an agent that is never registered —
both fail silently until a pipeline run mysteriously misbehaves. Order matters for
the two Python tuples (append/deploy ordering); only set membership matters for the
bash loops (loop order is cosmetic). This module pins all four copies.
"""
import re
from pathlib import Path

import pytest

from autodev.installer.register_agent import AUTODEV_AGENT_IDS

_REPO_ROOT = Path(__file__).resolve().parents[1]  # tests/ -> repo root
_INSTALL_SH = _REPO_ROOT / "install.sh"
_AGENTS_DIR = _REPO_ROOT / "autodev" / "agents"

# Matches only genuine loop headers `for agent in <list>; do` — NOT the case-statement
# arms, prompt comments, or curl examples in install.sh that also name agents.
_LOOP_RE = re.compile(r"^\s*for\s+agent\s+in\s+(.+?)\s*;\s*do\s*$", re.MULTILINE)
_EXPECTED_LOOP_COUNT = 5


@pytest.fixture(scope="module")
def workspace_sync_ids():
    """Import inside a fixture to contain the heavy ui.server (FastAPI) import to
    this one module, mirroring tests/test_config_defaults_consistency.py."""
    from ui.server import _WORKSPACE_SYNC_AGENT_IDS
    return _WORKSPACE_SYNC_AGENT_IDS


def test_workspace_sync_tuple_matches_source_of_truth(workspace_sync_ids):
    """ui/server.py _WORKSPACE_SYNC_AGENT_IDS must equal AUTODEV_AGENT_IDS, order
    included — both drive ordered append/deploy logic."""
    assert workspace_sync_ids == AUTODEV_AGENT_IDS, (
        f"_WORKSPACE_SYNC_AGENT_IDS (ui/server.py) = {workspace_sync_ids} != "
        f"AUTODEV_AGENT_IDS (register_agent.py, source of truth) = {AUTODEV_AGENT_IDS}. "
        "The workspace-deploy step and agent registration would operate on different "
        "rosters — an agent could be registered but never receive its workspace files "
        "(or vice versa), silently. Re-sync the tuple (order matters)."
    )


def test_install_sh_agent_loops_match_source_of_truth():
    """install.sh hardcodes the roster in `for agent in ...; do` loops (a literal
    list, NOT a glob), so a missed edit drifts silently on fresh installs."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    loops = _LOOP_RE.findall(text)
    assert len(loops) == _EXPECTED_LOOP_COUNT, (
        f"Expected exactly {_EXPECTED_LOOP_COUNT} 'for agent in ...; do' loops in "
        f"install.sh, found {len(loops)}. A loop was added, removed, or reshaped. The "
        "parity guard only covers the loops it can see — update this count and confirm "
        "each new loop lists the canonical roster."
    )
    expected = set(AUTODEV_AGENT_IDS)
    for idx, raw in enumerate(loops, start=1):
        found = set(raw.split())
        assert found == expected, (
            f"install.sh 'for agent in ...' loop #{idx} drifted from AUTODEV_AGENT_IDS "
            f"(source of truth). Symmetric difference: {found ^ expected}. install.sh "
            "hardcodes this list (not a glob), so a missed edit means the installer "
            "deploys/registers the wrong agents on a fresh install while the Python "
            "code expects the full roster. Update every loop in install.sh."
        )


def test_agent_directories_match_source_of_truth():
    """autodev/agents/<id>/ — the dirs the installer deploys from — are a fourth copy
    of the roster. A missing dir (or an orphan) breaks deploy silently."""
    agent_dirs = {
        p.name for p in _AGENTS_DIR.iterdir()
        if p.is_dir() and not p.name.startswith((".", "__"))
    }
    expected = set(AUTODEV_AGENT_IDS)
    assert agent_dirs == expected, (
        f"autodev/agents/ subdirectories {sorted(agent_dirs)} drifted from "
        f"AUTODEV_AGENT_IDS {sorted(expected)} (source of truth). Symmetric difference: "
        f"{agent_dirs ^ expected}. A roster entry has no agent directory to deploy from "
        "(or an orphan dir exists). Add/remove the directory or fix the roster."
    )
