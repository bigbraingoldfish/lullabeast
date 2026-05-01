"""repo_init_check respects OPENCLAW_ROOT for non-default layouts (e.g. Docker bind mounts).

  - Only ``OPENCLAW_ROOT`` is consulted for the OpenClaw hub path.
  - Only ``AUTODEV_PIPELINE_ROOT`` is consulted for the pipeline state path.
  - The legacy alias ``AUTODEV_ROOT`` is ignored for hub resolution.
"""

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
def test_repo_init_check_uses_openclaw_root_custom_path(tmp_path):
    """When OPENCLAW_ROOT points at a custom tree, gate checks that tree."""
    project = tmp_path / "pipeline_repo"
    project.mkdir()
    (project / "roadmap.md").write_text("# Roadmap\n")
    (project / ".gitignore").write_text("*.done\n")

    oc_root = tmp_path / "docker_dot_openclaw"
    _seed_minimal_openclaw_layout(oc_root, project)

    env = os.environ.copy()
    # Strip legacy AUTODEV_ROOT to prove it is unused where relevant.
    env.pop("AUTODEV_ROOT", None)
    env["OPENCLAW_ROOT"] = str(oc_root)
    # Custom OpenClaw tree: pin pipeline state onto the same root so the gate
    # finds pipeline-project under $OPENCLAW_ROOT.
    env["AUTODEV_PIPELINE_ROOT"] = str(oc_root)

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
    env.pop("AUTODEV_ROOT", None)
    env["OPENCLAW_ROOT"] = str(oc_root)
    env["AUTODEV_PIPELINE_ROOT"] = str(oc_root)

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


def _seed_agent_workspaces_only(oc_root: Path) -> None:
    oc_root.mkdir(parents=True, exist_ok=True)
    for agent in ("planner", "executor", "reviewer", "escalation"):
        wd = oc_root / f"workspace-{agent}"
        wd.mkdir(parents=True, exist_ok=True)
        for doc in ("AGENTS.md", "TOOLS.md", "SOUL.md", "USER.md", "IDENTITY.md"):
            (wd / doc).write_text("ok\n")


@pytest.mark.skipif(not GATE_SCRIPT.is_file(), reason="gate script missing")
def test_repo_init_check_repo_local_runtime_pipeline_project(tmp_path):
    """Default layout: pipeline-project under $AUTODEV_REPO_PATH/.autodev (not ~/.openclaw)."""
    fake_repo = tmp_path / "autodev_repo_clone"
    fake_repo.mkdir()
    project = fake_repo / "target_project"
    project.mkdir()
    (project / "roadmap.md").write_text("# Roadmap\n")
    (project / ".gitignore").write_text("*.done\n")

    autodev_rt = fake_repo / ".autodev"
    autodev_rt.mkdir()
    pp = autodev_rt / "pipeline-project"
    try:
        pp.symlink_to(project, target_is_directory=True)
    except OSError:
        pytest.skip("symlink not supported")

    oc_root = tmp_path / "openclaw_home"
    _seed_agent_workspaces_only(oc_root)

    env = os.environ.copy()
    env.pop("AUTODEV_ROOT", None)
    env["OPENCLAW_ROOT"] = str(oc_root)
    env["AUTODEV_REPO_PATH"] = str(fake_repo)

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
def test_repo_init_check_legacy_autodev_root_is_ignored(tmp_path):
    """Setting only AUTODEV_ROOT (legacy) must have no effect; gate falls back
    to ``~/.openclaw`` and either succeeds there or fails cleanly — the key
    property is that the provided tmp_path is NOT consulted."""
    project = tmp_path / "pipeline_repo"
    project.mkdir()
    (project / "roadmap.md").write_text("# Roadmap\n")
    (project / ".gitignore").write_text("*.done\n")

    bogus_oc_root = tmp_path / "legacy_ignored"
    _seed_minimal_openclaw_layout(bogus_oc_root, project)

    env = os.environ.copy()
    env.pop("OPENCLAW_ROOT", None)
    env.pop("AUTODEV_PIPELINE_ROOT", None)
    # Only legacy alias set — must be ignored.
    env["AUTODEV_ROOT"] = str(bogus_oc_root)

    result = subprocess.run(
        [sys.executable, str(GATE_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )
    combined = result.stdout + result.stderr
    # Gate should never reference the legacy-only tmp path — proving the alias
    # was ignored. It may pass (real ~/.openclaw is seeded) or fail, but must
    # not touch bogus_oc_root.
    assert str(bogus_oc_root) not in combined, (
        f"Legacy AUTODEV_ROOT should not be consulted, but gate referenced it:\n{combined}"
    )
