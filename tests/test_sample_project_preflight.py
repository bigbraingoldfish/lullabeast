"""The bundled first-run sample project must pass preflight as-is.

``examples/first-run-snake/`` is the "hello world" project the README's
"Your first run" walkthrough tells new users to copy and launch. These tests
run the real server-side validators against the real bundled files (copied
into a tmp fixture; the example directory itself is never mutated), so if
gate or preflight requirements ever tighten, this breaks CI instead of
breaking new users.

Hermetic: tmp_path only; the preflight config points every path (openclaw
root, symlinks, state) into tmp_path, and git runs against a tmp repo with a
repo-local identity, so the live install is never touched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ui.server import (
    _run_preflight_checks,
    _validate_roadmap_content,
    _validate_verification_content,
)
from autodev.pipeline.prereq_spec import parse_prerequisites

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = REPO_ROOT / "examples" / "first-run-snake"

WORKSPACE_AGENTS = ["planner", "executor", "reviewer", "escalation"]
WORKSPACE_DOCS = ["AGENTS.md", "TOOLS.md", "SOUL.md", "USER.md", "IDENTITY.md"]


@pytest.fixture
def sample_env(tmp_path):
    """The sample copied into a tmp project dir + a fully tmp preflight config."""
    repo = tmp_path / "snake"
    shutil.copytree(SAMPLE_DIR, repo)

    openclaw = tmp_path / ".openclaw"
    openclaw.mkdir()
    for agent in WORKSPACE_AGENTS:
        ws = openclaw / f"workspace-{agent}"
        ws.mkdir()
        for doc in WORKSPACE_DOCS:
            (ws / doc).write_text(f"# {doc}\n")

    pp = str(openclaw / "pipeline-project")
    config = {
        "openclaw_root": str(openclaw),
        "project_dir_path": pp,
        "autodev_repo_path": str(REPO_ROOT),
        "autodev_pipeline_root": str(openclaw),
        "pipeline_state_path": os.path.join(str(openclaw), "pipeline_state.json"),
        "lock_path": os.path.join(str(openclaw), "pipeline.lock"),
        "pipeline_queue_path": os.path.join(str(openclaw), "pipeline_queue.json"),
        "events_path": os.path.join(str(openclaw), "pipeline_events.jsonl"),
        "ideas_dir": os.path.join(str(openclaw), "ideas"),
    }
    return repo, config


class TestSampleFilesPresent:
    def test_bundled_triple_exists(self):
        for name in ("prd.md", "roadmap.md", "verification.md"):
            assert (SAMPLE_DIR / name).is_file(), f"sample is missing {name}"


class TestSamplePreflight:
    def test_preflight_zero_fails(self, sample_env):
        """The headline guard: real preflight, real git auto-init, zero fails.

        This exercises exactly what the README walkthrough promises: a user
        copies the sample folder (no .git) and preflight initializes git,
        creates the .gitignore, and comes back green.
        """
        repo, config = sample_env
        checks = _run_preflight_checks(str(repo), config=config)
        fails = [c for c in checks if c.get("status") == "fail"]
        assert fails == [], f"preflight failed on the bundled sample: {fails}"

    def test_roadmap_and_verification_rows_pass(self, sample_env):
        repo, config = sample_env
        checks = _run_preflight_checks(str(repo), config=config)
        by = {c["check"]: c["status"] for c in checks}
        assert by.get("roadmap file") == "pass"
        assert by.get("verification doc") == "pass"

    def test_preflight_initialized_git_with_a_commit(self, sample_env):
        repo, config = sample_env
        _run_preflight_checks(str(repo), config=config)
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
            capture_output=True, text=True,
        )
        assert head.returncode == 0, "preflight must leave a commit (phase_base_commit source)"


class TestSampleContentContracts:
    def test_roadmap_format_valid(self):
        result = _validate_roadmap_content((SAMPLE_DIR / "roadmap.md").read_text())
        assert result["valid"], result["errors"]

    def test_verification_format_valid(self):
        result = _validate_verification_content((SAMPLE_DIR / "verification.md").read_text())
        assert result["valid"], result["errors"]

    def test_prerequisites_parse_clean(self):
        spec = parse_prerequisites((SAMPLE_DIR / "verification.md").read_text())
        assert spec["block_present"] is True
        assert spec["warnings"] == []
        assert spec["env"] == []  # declared "none": no .env.example emission

    def test_phase_resolver_identifies_first_phase(self, tmp_path):
        """The gate-script contract: exit 0 + PENDING on the first phase."""
        shutil.copy(SAMPLE_DIR / "roadmap.md", tmp_path / "roadmap.md")
        r = subprocess.run(
            [sys.executable,
             str(REPO_ROOT / "autodev" / "pipeline" / "gate_scripts" / "phase_resolver.py"),
             str(tmp_path / "roadmap.md")],
            capture_output=True, text=True, cwd=str(tmp_path),
        )
        assert r.returncode == 0, r.stderr
        assert "CORE-E1" in r.stdout

    def test_all_phase_prefixes_have_skill_mappings(self):
        """Every subsystem prefix in the sample roadmap maps to a discipline,
        so no phase silently runs skill-less."""
        import re
        import yaml

        mapping = yaml.safe_load(
            (REPO_ROOT / "autodev" / "config" / "skill_mapping.yaml").read_text()
        )
        mapped = {str(k).upper() for k in (mapping or {})}
        roadmap = (SAMPLE_DIR / "roadmap.md").read_text()
        prefixes = {
            m.split("-")[0].upper()
            for m in re.findall(r"^- \[.\] `([A-Z]+-[A-Z]?\d+)`", roadmap, re.MULTILINE)
        }
        assert prefixes, "no phase lines found in the sample roadmap"
        missing = prefixes - mapped
        assert not missing, f"sample uses unmapped subsystem prefixes: {missing}"
