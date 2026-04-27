"""TDD for _sync_agent_workspaces: mtime-based copy from autodev repo to OpenClaw workspaces.

See plan: auto_sync on UI startup (install.sh semantics).
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _planner_agents_md_paths(tmp: Path) -> tuple[Path, Path, str, str]:
    """Return (src, dst, new_content, old_content) for a single planner/AGENTS.md pair."""
    repo = tmp / "repo"
    openclaw = tmp / "openclaw"
    src = repo / "autodev" / "agents" / "planner" / "AGENTS.md"
    src.parent.mkdir(parents=True, exist_ok=True)
    dst = openclaw / "workspace-planner" / "AGENTS.md"
    openclaw.mkdir(parents=True, exist_ok=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    return src, dst, "# from repo new", "# from workspace old"


def _config(tmp: Path) -> dict:
    return {
        "autodev_repo_path": str(tmp / "repo"),
        "openclaw_root": str(tmp / "openclaw"),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_sync_copies_when_repo_is_newer(tmp_path: Path) -> None:
    from ui import server as srv

    src, dst, new_c, old_c = _planner_agents_md_paths(tmp_path)
    src.write_text(new_c)
    dst.write_text(old_c)
    # dest older, source newer
    t0 = time.time() - 100
    os.utime(str(src), (t0, t0 + 50))
    os.utime(str(dst), (t0, t0))

    r = srv._sync_agent_workspaces(_config(tmp_path))
    assert r["synced"] >= 1
    assert dst.read_text() == new_c


def test_sync_skips_when_dest_is_newer(tmp_path: Path) -> None:
    from ui import server as srv

    src, dst, new_c, old_c = _planner_agents_md_paths(tmp_path)
    src.write_text(new_c)
    dst.write_text(old_c)
    t0 = time.time() - 100
    # dest newer than source (user override) — do not overwrite
    os.utime(str(src), (t0, t0))
    os.utime(str(dst), (t0, t0 + 50))

    r = srv._sync_agent_workspaces(_config(tmp_path))
    assert r["synced"] == 0
    assert r["skipped"] >= 1
    assert dst.read_text() == old_c


def test_sync_copies_when_dest_missing(tmp_path: Path) -> None:
    from ui import server as srv

    repo = tmp_path / "repo"
    openclaw = tmp_path / "openclaw"
    src = repo / "autodev" / "agents" / "planner" / "AGENTS.md"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("# only in repo")
    openclaw.mkdir(parents=True, exist_ok=True)
    (openclaw / "workspace-planner").mkdir(parents=True, exist_ok=True)
    dst = openclaw / "workspace-planner" / "AGENTS.md"
    assert not dst.exists()

    r = srv._sync_agent_workspaces(_config(tmp_path))
    assert r["synced"] >= 1
    assert dst.read_text() == "# only in repo"


def test_sync_returns_error_on_permission_failure(tmp_path: Path) -> None:
    from ui import server as srv

    src, dst, new_c, _ = _planner_agents_md_paths(tmp_path)
    src.write_text(new_c)
    os.utime(str(src), (time.time(), time.time() + 10))
    # will try to copy to dest (newer source) or first available - make copy2 fail
    with patch.object(srv.shutil, "copy2", side_effect=OSError("denied")):
        r = srv._sync_agent_workspaces(_config(tmp_path))
    assert r["errors"]


def test_sync_skipped_when_flag_false() -> None:
    from ui.server import app, lifespan
    from ui import server as srv

    with patch.object(srv, "load_config", return_value={"auto_sync_agent_workspaces": False}):
        with patch.object(srv, "_sync_agent_workspaces") as mock_sync:

            async def _enter_lifespan() -> None:
                async with lifespan(app):
                    pass

            asyncio.run(_enter_lifespan())
    mock_sync.assert_not_called()


def test_sync_runs_at_startup_when_flag_true() -> None:
    from ui.server import app, lifespan
    from ui import server as srv

    with patch.object(
        srv,
        "load_config",
        return_value={"auto_sync_agent_workspaces": True},
    ):
        with patch.object(srv, "_sync_agent_workspaces") as mock_sync:

            async def _enter_lifespan() -> None:
                async with lifespan(app):
                    pass

            asyncio.run(_enter_lifespan())
    mock_sync.assert_called_once()
