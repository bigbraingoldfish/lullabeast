"""
Integration tests for init-project skill Mode A (new project creation).
These tests MUST be run AFTER manually executing the skill steps as shell
commands — they verify the results, they do not invoke the skill themselves.
The symlink verification uses direct subprocess calls, not pytest tmp_path.
"""
import subprocess
import os
import re

import pytest

PROJECT_DIR = "/tmp/infra-e1-test-a"
PIPELINE_SYMLINK = os.path.expanduser("~/.openclaw/pipeline-project")


def _manual_infra_ready() -> bool:
    """Only run when fixture dir exists and pipeline symlink points at it."""
    if not os.path.isdir(PROJECT_DIR):
        return False
    result = subprocess.run(
        ["readlink", "-f", PIPELINE_SYMLINK],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    return result.stdout.strip() == os.path.realpath(PROJECT_DIR)


pytestmark = pytest.mark.skipif(
    not _manual_infra_ready(),
    reason=f"Manual infra: {PROJECT_DIR} must exist and ~/.openclaw/pipeline-project must target it",
)


def test_symlink_points_to_project():
    """readlink -f ~/.openclaw/pipeline-project equals /tmp/infra-e1-test-a"""
    result = subprocess.run(
        ["readlink", "-f", PIPELINE_SYMLINK],
        capture_output=True, text=True
    )
    assert result.returncode == 0, f"readlink failed: {result.stderr}"
    assert result.stdout.strip() == PROJECT_DIR, (
        f"Expected {PROJECT_DIR}, got {result.stdout.strip()}"
    )


def test_repo_init_check_passes():
    """python3 gate_scripts/repo_init_check.py /tmp/infra-e1-test-a exits 0"""
    gate_script = "/home/pi/.openclaw/gate_scripts/repo_init_check.py"
    result = subprocess.run(
        ["python3", gate_script, PROJECT_DIR],
        capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"repo_init_check failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_gitignore_has_all_pipeline_entries():
    """cat /tmp/infra-e1-test-a/.gitignore contains all 7 required pipeline entries"""
    gitignore_path = os.path.join(PROJECT_DIR, ".gitignore")
    with open(gitignore_path) as f:
        content = f.read()

    required = [
        "*.done",
        "phase_state.json",
        "planner_output.json",
        "executor_output.json",
        "reviewer_output.json",
        "escalation_output.json",
        "current_phase.json",
    ]
    missing = [e for e in required if e not in content]
    assert not missing, f"Missing .gitignore entries: {missing}"


def test_roadmap_format_valid():
    """roadmap.md passes the Phase line regex: ^\\- \\[.\\] \\`[A-Z]+-[A-Z]\\d+\\` \\| (LOW|HIGH) \\| .+"""
    roadmap_path = os.path.join(PROJECT_DIR, "roadmap.md")
    with open(roadmap_path) as f:
        lines = f.readlines()

    phase_pattern = re.compile(r'^\- \[.\] `[A-Z]+-[A-Z]\d+` \| (LOW|HIGH) \| .+')
    malformed = []
    for line in lines:
        line = line.strip()
        if line.startswith("- ["):
            if not phase_pattern.match(line):
                malformed.append(line)
    assert not malformed, f"Malformed phase lines: {malformed}"


def test_roadmap_passes_roadmap_parser():
    """python3 gate_scripts/roadmap_parser.py /tmp/infra-e1-test-a/roadmap.md exits 0"""
    gate_script = "/home/pi/.openclaw/gate_scripts/roadmap_parser.py"
    roadmap_path = os.path.join(PROJECT_DIR, "roadmap.md")
    result = subprocess.run(
        ["python3", gate_script, roadmap_path],
        capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"roadmap_parser failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
