"""load_config() derives repo-local runtime paths under <repo>/.autodev by default."""

import json
import os
from pathlib import Path

import pytest

from ui import server


def _repo_root(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    (r / "ui").mkdir()
    return r


def test_default_runtime_under_dot_autodev(monkeypatch, tmp_path):
    repo = _repo_root(tmp_path)
    cfg_path = repo / "ui" / "config.json"
    monkeypatch.chdir(repo)
    monkeypatch.setattr(server, "_AUTODEV_UI_ROOT", str(repo))
    monkeypatch.delenv("AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME", raising=False)
    cfg_path.write_text(json.dumps({"autodev_repo_path": str(repo)}))
    cfg = server.load_config(str(cfg_path))
    rt = str(repo / ".autodev")
    assert cfg["autodev_runtime_root"] == rt
    assert cfg["pipeline_state_path"] == os.path.join(rt, "pipeline_state.json")
    assert cfg["lock_path"] == os.path.join(rt, "pipeline.lock")
    assert cfg["pipeline_queue_path"] == os.path.join(rt, "pipeline_queue.json")
    assert cfg["ideas_dir"] == os.path.join(rt, "ideas")
    assert cfg["project_dir_path"] == os.path.join(rt, "pipeline-project")
    assert cfg["phase_state_path"] == os.path.join(rt, "pipeline-project", "phase_state.json")
    assert cfg["roadmap_path"] == os.path.join(rt, "pipeline-project", "roadmap.md")
    oc = os.path.join(str(Path.home()), ".openclaw")
    assert cfg["roadmap_converter_workspace"] == os.path.join(oc, "workspace-roadmap-converter")


def test_legacy_env_uses_openclaw_root(monkeypatch, tmp_path):
    repo = _repo_root(tmp_path)
    oc = tmp_path / "oc"
    oc.mkdir()
    cfg_path = repo / "ui" / "config.json"
    monkeypatch.chdir(repo)
    monkeypatch.setattr(server, "_AUTODEV_UI_ROOT", str(repo))
    monkeypatch.setenv("AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME", "1")
    cfg_path.write_text(
        json.dumps({"autodev_repo_path": str(repo), "openclaw_root": str(oc)})
    )
    cfg = server.load_config(str(cfg_path))
    assert cfg["autodev_runtime_root"] == str(oc)
    assert cfg["pipeline_state_path"] == str(oc / "pipeline_state.json")
    assert cfg["ideas_dir"] == str(oc / "ideas")


def test_user_override_ideas_dir_not_clobbered(monkeypatch, tmp_path):
    repo = _repo_root(tmp_path)
    custom = str(tmp_path / "my_ideas")
    cfg_path = repo / "ui" / "config.json"
    monkeypatch.chdir(repo)
    monkeypatch.setattr(server, "_AUTODEV_UI_ROOT", str(repo))
    cfg_path.write_text(
        json.dumps({"autodev_repo_path": str(repo), "ideas_dir": custom})
    )
    cfg = server.load_config(str(cfg_path))
    assert cfg["ideas_dir"] == os.path.expanduser(custom)
