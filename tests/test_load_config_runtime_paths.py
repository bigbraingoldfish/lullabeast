"""load_config() derives pipeline state paths using the canonical env vars only.

Post hard-cut rules:
  - Only ``OPENCLAW_ROOT`` is consulted for the OpenClaw hub path.
  - Only ``AUTODEV_PIPELINE_ROOT`` is consulted for the pipeline state path.
  - The legacy env vars ``AUTODEV_ROOT`` / ``AUTODEV_RUNTIME_ROOT`` and the
    removed switch ``AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME`` are ignored.
  - The UI JSON accepts only ``autodev_pipeline_root``; the legacy
    ``autodev_runtime_root`` key is ignored.
  - ``_spawn_orchestrator`` must preserve a parent-env ``AUTODEV_PIPELINE_ROOT``
    when the UI config omits the key (regression guard for the empty-string
    overwrite bug).
"""

import json
import os
from pathlib import Path

from ui import server

_CLEAN_ENV_VARS = (
    "OPENCLAW_ROOT",
    "AUTODEV_ROOT",
    "AUTODEV_PIPELINE_ROOT",
    "AUTODEV_RUNTIME_ROOT",
    "AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME",
    "AUTODEV_HOOKS_TOKEN",
)


def _scrub_env(monkeypatch):
    for name in _CLEAN_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


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
    _scrub_env(monkeypatch)
    cfg_path.write_text(json.dumps({"autodev_repo_path": str(repo)}))
    cfg = server.load_config(str(cfg_path))
    rt = str(repo / ".autodev")
    assert cfg["autodev_pipeline_root"] == rt
    assert "autodev_runtime_root" not in cfg
    assert cfg["pipeline_state_path"] == os.path.join(rt, "pipeline_state.json")
    assert cfg["lock_path"] == os.path.join(rt, "pipeline.lock")
    assert cfg["pipeline_queue_path"] == os.path.join(rt, "pipeline_queue.json")
    assert cfg["ideas_dir"] == os.path.join(rt, "ideas")
    assert cfg["project_dir_path"] == os.path.join(rt, "pipeline-project")
    assert cfg["phase_state_path"] == os.path.join(
        rt, "pipeline-project", ".autodev", "pipeline", "phase_state.json"
    )
    assert cfg["roadmap_path"] == os.path.join(rt, "pipeline-project", "roadmap.md")


def test_pipeline_root_env_canonical_wins(monkeypatch, tmp_path):
    """AUTODEV_PIPELINE_ROOT overrides the repo-local default."""
    repo = _repo_root(tmp_path)
    custom = tmp_path / "state"
    custom.mkdir()
    cfg_path = repo / "ui" / "config.json"
    monkeypatch.chdir(repo)
    monkeypatch.setattr(server, "_AUTODEV_UI_ROOT", str(repo))
    _scrub_env(monkeypatch)
    monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(custom))
    cfg_path.write_text(json.dumps({"autodev_repo_path": str(repo)}))
    cfg = server.load_config(str(cfg_path))
    assert cfg["autodev_pipeline_root"] == str(custom)
    assert cfg["pipeline_state_path"] == str(custom / "pipeline_state.json")


def test_pipeline_root_env_legacy_alias_is_ignored(monkeypatch, tmp_path):
    """The legacy AUTODEV_RUNTIME_ROOT must have no effect."""
    repo = _repo_root(tmp_path)
    legacy = tmp_path / "legacy_state"
    legacy.mkdir()
    cfg_path = repo / "ui" / "config.json"
    monkeypatch.chdir(repo)
    monkeypatch.setattr(server, "_AUTODEV_UI_ROOT", str(repo))
    _scrub_env(monkeypatch)
    monkeypatch.setenv("AUTODEV_RUNTIME_ROOT", str(legacy))
    cfg_path.write_text(json.dumps({"autodev_repo_path": str(repo)}))
    cfg = server.load_config(str(cfg_path))
    rt = str(repo / ".autodev")
    assert cfg["autodev_pipeline_root"] == rt
    assert cfg["autodev_pipeline_root"] != str(legacy)


def test_openclaw_root_env_legacy_alias_is_ignored(monkeypatch, tmp_path):
    """The legacy AUTODEV_ROOT env var must have no effect."""
    repo = _repo_root(tmp_path)
    legacy = tmp_path / "legacy_oc"
    legacy.mkdir()
    cfg_path = repo / "ui" / "config.json"
    monkeypatch.chdir(repo)
    monkeypatch.setattr(server, "_AUTODEV_UI_ROOT", str(repo))
    _scrub_env(monkeypatch)
    monkeypatch.setenv("AUTODEV_ROOT", str(legacy))
    cfg_path.write_text(json.dumps({"autodev_repo_path": str(repo)}))
    cfg = server.load_config(str(cfg_path))
    assert cfg["openclaw_root"] != str(legacy)
    assert cfg["openclaw_root"] == os.path.expanduser("~/.openclaw")


def test_legacy_flag_env_is_ignored(monkeypatch, tmp_path):
    """AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME must have no effect."""
    repo = _repo_root(tmp_path)
    oc = tmp_path / "oc"
    oc.mkdir()
    cfg_path = repo / "ui" / "config.json"
    monkeypatch.chdir(repo)
    monkeypatch.setattr(server, "_AUTODEV_UI_ROOT", str(repo))
    _scrub_env(monkeypatch)
    monkeypatch.setenv("AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME", "1")
    cfg_path.write_text(
        json.dumps({"autodev_repo_path": str(repo), "openclaw_root": str(oc)})
    )
    cfg = server.load_config(str(cfg_path))
    assert cfg["autodev_pipeline_root"] == str(repo / ".autodev")
    assert cfg["autodev_pipeline_root"] != str(oc)


def test_legacy_flag_config_key_is_ignored(monkeypatch, tmp_path):
    """use_legacy_openclaw_runtime in config.json must have no effect."""
    repo = _repo_root(tmp_path)
    oc = tmp_path / "oc"
    oc.mkdir()
    cfg_path = repo / "ui" / "config.json"
    monkeypatch.chdir(repo)
    monkeypatch.setattr(server, "_AUTODEV_UI_ROOT", str(repo))
    _scrub_env(monkeypatch)
    cfg_path.write_text(
        json.dumps(
            {
                "autodev_repo_path": str(repo),
                "openclaw_root": str(oc),
                "use_legacy_openclaw_runtime": True,
            }
        )
    )
    cfg = server.load_config(str(cfg_path))
    assert cfg["autodev_pipeline_root"] == str(repo / ".autodev")


def test_json_key_autodev_pipeline_root_is_honored(monkeypatch, tmp_path):
    """The canonical JSON key ``autodev_pipeline_root`` pins the runtime root."""
    repo = _repo_root(tmp_path)
    custom = tmp_path / "state_from_json"
    custom.mkdir()
    cfg_path = repo / "ui" / "config.json"
    monkeypatch.chdir(repo)
    monkeypatch.setattr(server, "_AUTODEV_UI_ROOT", str(repo))
    _scrub_env(monkeypatch)
    cfg_path.write_text(
        json.dumps(
            {
                "autodev_repo_path": str(repo),
                "autodev_pipeline_root": str(custom),
            }
        )
    )
    cfg = server.load_config(str(cfg_path))
    assert cfg["autodev_pipeline_root"] == str(custom)
    assert cfg["pipeline_state_path"] == os.path.join(str(custom), "pipeline_state.json")


def test_json_key_autodev_runtime_root_alias_is_ignored(monkeypatch, tmp_path):
    """Legacy JSON key ``autodev_runtime_root`` must have no effect."""
    repo = _repo_root(tmp_path)
    legacy = tmp_path / "legacy_json"
    legacy.mkdir()
    cfg_path = repo / "ui" / "config.json"
    monkeypatch.chdir(repo)
    monkeypatch.setattr(server, "_AUTODEV_UI_ROOT", str(repo))
    _scrub_env(monkeypatch)
    cfg_path.write_text(
        json.dumps(
            {
                "autodev_repo_path": str(repo),
                "autodev_runtime_root": str(legacy),
            }
        )
    )
    cfg = server.load_config(str(cfg_path))
    rt = str(repo / ".autodev")
    assert cfg["autodev_pipeline_root"] == rt
    assert cfg["autodev_pipeline_root"] != str(legacy)


def test_user_override_ideas_dir_not_clobbered(monkeypatch, tmp_path):
    repo = _repo_root(tmp_path)
    custom = str(tmp_path / "my_ideas")
    cfg_path = repo / "ui" / "config.json"
    monkeypatch.chdir(repo)
    monkeypatch.setattr(server, "_AUTODEV_UI_ROOT", str(repo))
    _scrub_env(monkeypatch)
    cfg_path.write_text(
        json.dumps({"autodev_repo_path": str(repo), "ideas_dir": custom})
    )
    cfg = server.load_config(str(cfg_path))
    assert cfg["ideas_dir"] == os.path.expanduser(custom)


def test_config_example_empty_path_placeholders_still_derive(monkeypatch, tmp_path):
    """ui/config.example.json uses "" for derived paths; those must not block defaults."""
    repo = _repo_root(tmp_path)
    oc = tmp_path / "openclaw"
    oc.mkdir()
    cfg_path = repo / "ui" / "config.json"
    monkeypatch.chdir(repo)
    monkeypatch.setattr(server, "_AUTODEV_UI_ROOT", str(repo))
    _scrub_env(monkeypatch)
    cfg_path.write_text(
        json.dumps(
            {
                "port": 18790,
                "openclaw_root": str(oc),
                "autodev_repo_path": str(repo),
                "autodev_pipeline_root": "",
                "pipeline_state_path": "",
                "phase_state_path": "",
                "lock_path": "",
                "events_path": "",
                "roadmap_path": "",
                "project_dir_path": "",
                "ideas_dir": "",
                "pipeline_queue_path": "",
                "conversion_prompt_path": "",
                "hooks_url": "http://localhost:18789/hooks/agent",
                "hooks_token": "",
                "base_branch": "",
                "poll_timeout": 180,
                "poll_interval": 2,
                "ideas_idle_threshold": 120,
                "ideas_startup_grace": 30,
            }
        )
    )
    cfg = server.load_config(str(cfg_path))
    rt = str(repo / ".autodev")
    assert cfg["autodev_pipeline_root"] == rt
    assert cfg["pipeline_state_path"] == os.path.join(rt, "pipeline_state.json")
    assert cfg["project_dir_path"] == os.path.join(rt, "pipeline-project")
    assert cfg["phase_state_path"] == os.path.join(
        rt, "pipeline-project", ".autodev", "pipeline", "phase_state.json"
    )
    assert cfg["roadmap_path"] == os.path.join(rt, "pipeline-project", "roadmap.md")
    assert cfg["lock_path"] == os.path.join(rt, "pipeline.lock")
    assert cfg["pipeline_queue_path"] == os.path.join(rt, "pipeline_queue.json")
    assert cfg["events_path"] == os.path.join(rt, "pipeline_events.jsonl")
    assert cfg["ideas_dir"] == os.path.join(rt, "ideas")
    assert cfg["conversion_prompt_path"] == os.path.join(
        str(repo), "autodev", "prompts", "prd-to-roadmap-conversion.txt"
    )
    assert cfg["roadmap_converter_workspace"] == os.path.join(str(oc), "workspace-roadmap-converter")
