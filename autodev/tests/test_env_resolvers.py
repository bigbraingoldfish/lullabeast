"""Tests for the shared env resolvers that centralise OpenClaw / pipeline roots.

  - Only ``OPENCLAW_ROOT`` is consulted for the OpenClaw hub path.
  - Only ``AUTODEV_PIPELINE_ROOT`` is consulted for the pipeline state path.
  - ``AUTODEV_ROOT`` / ``AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME`` are ignored for
    pipeline resolution (OpenClaw uses its own rules for hub path).
"""

import os

import pytest

from env_resolvers import (  # noqa: E402 - sys.path wired by conftest
    resolve_openclaw_root,
    resolve_pipeline_root,
)


ALL_ENV_KEYS = (
    "OPENCLAW_ROOT",
    "AUTODEV_ROOT",
    "AUTODEV_PIPELINE_ROOT",
    "AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ALL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


class TestResolveOpenclawRoot:
    def test_defaults_to_home_openclaw_when_unset(self):
        assert resolve_openclaw_root() == os.path.expanduser("~/.openclaw")

    def test_reads_canonical_name(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
        assert resolve_openclaw_root() == str(tmp_path)

    def test_legacy_alias_is_ignored(self, monkeypatch, tmp_path):
        """AUTODEV_ROOT is no longer consulted; it must not override the default."""
        monkeypatch.setenv("AUTODEV_ROOT", str(tmp_path))
        assert resolve_openclaw_root() == os.path.expanduser("~/.openclaw")

    def test_canonical_ignores_legacy_even_when_both_set(self, monkeypatch, tmp_path):
        new_val = tmp_path / "new"
        old_val = tmp_path / "old"
        new_val.mkdir()
        old_val.mkdir()
        monkeypatch.setenv("OPENCLAW_ROOT", str(new_val))
        monkeypatch.setenv("AUTODEV_ROOT", str(old_val))
        assert resolve_openclaw_root() == str(new_val)

    def test_expands_tilde(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_ROOT", "~/custom-openclaw")
        assert resolve_openclaw_root() == os.path.expanduser("~/custom-openclaw")

    def test_empty_string_falls_through_to_default(self, monkeypatch):
        monkeypatch.setenv("OPENCLAW_ROOT", "")
        monkeypatch.setenv("AUTODEV_ROOT", str("/should/be/ignored"))
        assert resolve_openclaw_root() == os.path.expanduser("~/.openclaw")


class TestResolvePipelineRoot:
    def test_defaults_to_repo_dot_autodev_when_unset(self, tmp_path):
        repo = str(tmp_path)
        assert resolve_pipeline_root(repo) == os.path.join(repo, ".autodev")

    def test_reads_canonical_name(self, monkeypatch, tmp_path):
        target = tmp_path / "pipeline-state"
        target.mkdir()
        monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(target))
        assert resolve_pipeline_root("/ignored/repo") == str(target)

    def test_expands_tilde(self, monkeypatch):
        monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", "~/custom-pipeline")
        assert resolve_pipeline_root("/ignored/repo") == os.path.expanduser(
            "~/custom-pipeline"
        )

    def test_empty_strings_fall_through_to_default(self, monkeypatch, tmp_path):
        repo = str(tmp_path)
        monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", "")
        assert resolve_pipeline_root(repo) == os.path.join(repo, ".autodev")

    def test_legacy_flag_is_ignored(self, monkeypatch, tmp_path):
        """The removed legacy switch must not influence resolution."""
        repo = str(tmp_path)
        monkeypatch.setenv("AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME", "1")
        monkeypatch.setenv("OPENCLAW_ROOT", "/some/openclaw")
        assert resolve_pipeline_root(repo) == os.path.join(repo, ".autodev")
