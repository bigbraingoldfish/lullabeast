"""Defect A (A1) — server-side symlink writes are SYMMETRIC (both project links).

The orchestrator's update_symlink moves BOTH the AUTODEV-side (.autodev/pipeline-project)
and the OpenClaw-side (~/.openclaw/pipeline-project, followed by agent workspaces) together.
The server historically moved only one (project_dir_path), so a server repoint left the other
stale. These tests pin that all three server repoint sites move both links, with a
transactional 'both or neither' rollback. RED against the single-link tree.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _two_link_config(tmp_path, autodev_link, oc_root):
    return {
        "project_dir_path": str(autodev_link),
        "openclaw_root": str(oc_root),
        "lock_path": str(tmp_path / "pipeline.lock"),
        "pipeline_state_path": str(tmp_path / "pipeline_state.json"),
        "pipeline_artifacts_path": "",
    }


def _both_links(config):
    from ui import server as srv
    return srv._pipeline_symlink_paths(config)


def test_pipeline_symlink_paths_returns_autodev_and_openclaw(tmp_path):
    from ui import server as srv
    cfg = _two_link_config(tmp_path, tmp_path / "ad" / "pipeline-project", tmp_path / "oc")
    paths = srv._pipeline_symlink_paths(cfg)
    assert str(tmp_path / "ad" / "pipeline-project") in paths
    assert str(tmp_path / "oc" / "pipeline-project") in paths
    assert len(paths) == 2  # de-duplicated, both sides present


def test_repoint_pipeline_project_symlink_updates_both_links(tmp_path):
    from ui import server as srv
    old = tmp_path / "old"; old.mkdir()
    new = tmp_path / "new"; new.mkdir()
    ad_link = tmp_path / "ad" / "pipeline-project"
    oc_link = tmp_path / "oc" / "pipeline-project"
    ad_link.parent.mkdir(); oc_link.parent.mkdir()
    ad_link.symlink_to(old); oc_link.symlink_to(old)

    cfg = _two_link_config(tmp_path, ad_link, tmp_path / "oc")
    res = srv._repoint_pipeline_project_symlink(cfg, str(new))

    assert res.get("ok") is True, res
    assert os.path.realpath(str(ad_link)) == os.path.realpath(str(new))
    assert os.path.realpath(str(oc_link)) == os.path.realpath(str(new))


def test_preflight_repoints_both_links(tmp_path):
    from ui.server import _run_preflight_checks
    old = tmp_path / "old"; old.mkdir(); (old / ".git").mkdir()
    new = tmp_path / "new"; new.mkdir(); (new / ".git").mkdir()
    (new / "roadmap.md").write_text("- [ ] `T-E1` | LOW | Task\n  > Test.\n")
    ad_link = tmp_path / "ad" / "pipeline-project"
    oc_link = tmp_path / "oc" / "pipeline-project"
    ad_link.parent.mkdir(); oc_link.parent.mkdir()
    ad_link.symlink_to(old); oc_link.symlink_to(old)

    cfg = _two_link_config(tmp_path, ad_link, tmp_path / "oc")
    # No lock held, no pipeline_state.json -> repoint freely.
    checks = _run_preflight_checks(str(new), config=cfg)

    sym = next((c for c in checks if c["check"] == "symlink"), None)
    assert sym is not None and sym["status"] in ("pass", "fixed"), sym
    assert os.path.realpath(str(ad_link)) == os.path.realpath(str(new))
    assert os.path.realpath(str(oc_link)) == os.path.realpath(str(new))


def test_symmetric_swap_rolls_back_first_on_second_failure(tmp_path):
    """If the second link cannot be committed, the first is rolled back to its
    prior target (both-or-neither). Mirrors the orchestrator's update_symlink rollback."""
    from ui import server as srv
    old1 = tmp_path / "old1"; old1.mkdir()
    new = tmp_path / "new"; new.mkdir()
    link1 = tmp_path / "link1"
    link1.symlink_to(old1)
    # link2's parent is a regular file, so committing it raises -> triggers rollback of link1.
    blocker = tmp_path / "blocker_file"
    blocker.write_text("x")
    link2 = blocker / "pipeline-project"

    with pytest.raises(Exception):
        srv._atomic_symlink_swap_multi(str(new), [str(link1), str(link2)])

    assert os.path.realpath(str(link1)) == os.path.realpath(str(old1)), (
        "first link must be rolled back to its prior target when the second commit fails"
    )


def test_three_server_sites_use_symmetric_swap():
    """Structural pin: the three server repoint sites (_repoint_pipeline_project_symlink,
    _run_preflight_checks, _run_init_project) all route through _atomic_symlink_swap_multi,
    so none can silently regress to a single-link write."""
    from ui import server as srv
    src = open(srv.__file__).read()
    assert src.count("_atomic_symlink_swap_multi(") >= 3, (
        "expected _repoint_pipeline_project_symlink + _run_preflight_checks + _run_init_project "
        "to all use the symmetric two-link swap"
    )
