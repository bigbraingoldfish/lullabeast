"""``_spawn_orchestrator`` env-dict construction tests.

  * The UI sets ``OPENCLAW_ROOT`` and (when provided) ``AUTODEV_PIPELINE_ROOT``.
  * ``AUTODEV_ROOT`` / ``AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME`` must never be
    written into the child env (parent copies may be scrubbed).
  * When the UI config leaves ``autodev_pipeline_root`` blank, the parent env's
    ``AUTODEV_PIPELINE_ROOT`` must be preserved (regression guard for the
    empty-string overwrite bug).
"""

import os
from unittest.mock import patch

from ui import server


def _capture_popen_env():
    """Patch subprocess.Popen to capture env= and avoid actually spawning."""
    calls: list[dict] = []

    class _FakePopen:
        def __init__(self, *args, env=None, **kwargs):
            calls.append(env or {})

    return _FakePopen, calls


def test_spawn_emits_only_canonical_env_names(tmp_path, monkeypatch):
    """Child env carries canonical names; scrubbed legacy vars absent."""
    repo = tmp_path / "repo"
    (repo / "autodev" / "pipeline").mkdir(parents=True)
    (repo / "autodev" / "pipeline" / "orchestrator.py").write_text("")
    oc = tmp_path / "openclaw"
    oc.mkdir()
    pipeline_state = tmp_path / "pipeline_state"
    pipeline_state.mkdir()

    for var in (
        "OPENCLAW_ROOT",
        "AUTODEV_ROOT",
        "AUTODEV_PIPELINE_ROOT",
        "AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME",
    ):
        monkeypatch.delenv(var, raising=False)

    fake_popen, calls = _capture_popen_env()
    with patch("subprocess.Popen", fake_popen):
        result = server._spawn_orchestrator(
            str(tmp_path / "proj"),
            config={
                "autodev_repo_path": str(repo),
                "openclaw_root": str(oc),
                "autodev_pipeline_root": str(pipeline_state),
            },
        )

    assert result["ok"], result
    assert calls, "subprocess.Popen was not invoked"
    env = calls[0]
    assert env["OPENCLAW_ROOT"] == str(oc)
    assert env["AUTODEV_PIPELINE_ROOT"] == str(pipeline_state)
    assert "AUTODEV_ROOT" not in env
    assert "AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME" not in env


def test_spawn_does_not_clobber_parent_pipeline_root_when_config_blank(
    tmp_path, monkeypatch
):
    """When the UI config leaves the pipeline root blank, ``_spawn_orchestrator``
    must preserve whatever ``AUTODEV_PIPELINE_ROOT`` was already in the parent
    env. Previously the UI overwrote it with ``""``, which sent the orchestrator
    to the wrong ``.autodev`` tree."""
    repo = tmp_path / "repo"
    (repo / "autodev" / "pipeline").mkdir(parents=True)
    (repo / "autodev" / "pipeline" / "orchestrator.py").write_text("")

    parent_pipeline_root = str(tmp_path / "already_exported")
    (tmp_path / "already_exported").mkdir()

    for var in (
        "OPENCLAW_ROOT",
        "AUTODEV_ROOT",
        "AUTODEV_USE_LEGACY_OPENCLAW_RUNTIME",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", parent_pipeline_root)

    fake_popen, calls = _capture_popen_env()
    with patch("subprocess.Popen", fake_popen):
        server._spawn_orchestrator(
            str(tmp_path / "proj"),
            config={
                "autodev_repo_path": str(repo),
                "openclaw_root": str(tmp_path / "oc"),
                "autodev_pipeline_root": "",  # blank in config
            },
        )

    env = calls[0]
    assert env["AUTODEV_PIPELINE_ROOT"] == parent_pipeline_root
    assert "AUTODEV_ROOT" not in env
