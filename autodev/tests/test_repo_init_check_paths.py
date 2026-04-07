"""repo_init_check respects AUTODEV_ROOT for non-default OpenClaw layouts (e.g. Docker bind mounts)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_SCRIPT = REPO_ROOT / "autodev" / "pipeline" / "gate_scripts" / "repo_init_check.py"


def _seed_minimal_openclaw_layout(oc_root: Path, project_dir: Path) -> None:
    oc_root.mkdir(parents=True, exist_ok=True)
    pp = oc_root / "pipeline-project"
    try:
        pp.symlink_to(project_dir, target_is_directory=True)
    except OSError:
        # Windows tests without symlink privilege: use real dir (gate only needs exists + content)
        pp.mkdir()
        for name in ("roadmap.md", ".gitignore"):
            src = project_dir / name
            if src.exists():
                (pp / name).write_text(src.read_text())
            elif name == ".gitignore":
                (pp / name).write_text("*.done\n")
            else:
                (pp / name).write_text("# r\n")

    if not (pp / "roadmap.md").exists():
        (pp / "roadmap.md").write_text("# Test roadmap\n")
    if not (pp / ".gitignore").exists():
        (pp / ".gitignore").write_text("*.done\n")

    for agent in ("planner", "executor", "reviewer", "escalation"):
        wd = oc_root / f"workspace-{agent}"
        wd.mkdir(parents=True, exist_ok=True)
        for doc in ("AGENTS.md", "TOOLS.md", "SOUL.md", "USER.md", "IDENTITY.md"):
            (wd / doc).write_text("ok\n")


@pytest.mark.skipif(not GATE_SCRIPT.is_file(), reason="gate script missing")
def test_repo_init_check_uses_autodev_root_custom_path(tmp_path):
    """When AUTODEV_ROOT points at a custom tree, gate checks that tree—not ~/.openclaw."""
    project = tmp_path / "pipeline_repo"
    project.mkdir()
    (project / "roadmap.md").write_text("# Roadmap\n")
    (project / ".gitignore").write_text("*.done\n")

    oc_root = tmp_path / "docker_dot_openclaw"
    _seed_minimal_openclaw_layout(oc_root, project)

    env = os.environ.copy()
    env["AUTODEV_ROOT"] = str(oc_root)

    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Repo initialization check passed" in (result.stdout + result.stderr)


@pytest.mark.skipif(not GATE_SCRIPT.is_file(), reason="gate script missing")
def test_repo_init_check_custom_root_fails_without_workspace(tmp_path):
    project = tmp_path / "pipeline_repo"
    project.mkdir()
    (project / "roadmap.md").write_text("# Roadmap\n")
    (project / ".gitignore").write_text("*.done\n")

    oc_root = tmp_path / "incomplete_openclaw"
    oc_root.mkdir()
    pp = oc_root / "pipeline-project"
    pp.symlink_to(project, target_is_directory=True)

    env = os.environ.copy()
    env["AUTODEV_ROOT"] = str(oc_root)

    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 1
    out = result.stdout + result.stderr
    assert "Agent workspace not found" in out
    assert str(oc_root) in out
