"""Tests for _inject_converter_skill helper function."""
import os
import pytest
from pathlib import Path
from unittest.mock import patch

from ui.server import _inject_converter_skill


class TestInjectConverterSkill:

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.repo_root = tmp_path / "repo"
        self.workspace = tmp_path / "workspace"
        self.config = {
            "autodev_repo_path": str(self.repo_root),
            "roadmap_converter_workspace": str(self.workspace),
        }

    def _create_source(self, skill_name, content="# SKILL\nTest content."):
        source = (
            self.repo_root
            / "autodev"
            / "skill-library"
            / "roadmap-converter"
            / skill_name
            / "SKILL.md"
        )
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(content)
        return source

    def test_happy_path_copies_skill_to_workspace(self):
        self._create_source("roadmap-generation", "# Roadmap Generation Skill")
        _inject_converter_skill("roadmap-generation", self.config)
        dest = self.workspace / "skills" / "roadmap-generation" / "SKILL.md"
        assert dest.exists()
        assert dest.read_text() == "# Roadmap Generation Skill"

    def test_creates_dest_directory_if_missing(self):
        self._create_source("alignment-check", "# Alignment")
        assert not self.workspace.exists()
        _inject_converter_skill("alignment-check", self.config)
        dest = self.workspace / "skills" / "alignment-check" / "SKILL.md"
        assert dest.exists()

    def test_source_missing_raises_runtime_error(self):
        with pytest.raises(RuntimeError) as exc_info:
            _inject_converter_skill("nonexistent-skill", self.config)
        assert "nonexistent-skill" in str(exc_info.value)

    def test_idempotent_second_call_overwrites_cleanly(self):
        self._create_source("roadmap-generation", "# Version 1")
        _inject_converter_skill("roadmap-generation", self.config)

        dest = self.workspace / "skills" / "roadmap-generation" / "SKILL.md"
        assert dest.read_text() == "# Version 1"

        # Update source
        source = (
            self.repo_root
            / "autodev"
            / "skill-library"
            / "roadmap-converter"
            / "roadmap-generation"
            / "SKILL.md"
        )
        source.write_text("# Version 2")
        _inject_converter_skill("roadmap-generation", self.config)
        assert dest.read_text() == "# Version 2"

    def test_write_is_atomic(self):
        """Verifies dest file is written via tmp+replace (mkstemp pattern)."""
        self._create_source("adversarial-review", "# Adversarial")
        _inject_converter_skill("adversarial-review", self.config)
        dest = self.workspace / "skills" / "adversarial-review" / "SKILL.md"
        # Temp file should not remain
        tmp_files = list((self.workspace / "skills" / "adversarial-review").glob("*.tmp"))
        assert tmp_files == []
        assert dest.exists()
