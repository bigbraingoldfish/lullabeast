"""Reviewer restart: skip re-invocation when sentinel already arrived mid-wait."""

import os
import sys
from datetime import datetime, timezone

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod


@pytest.fixture
def art_dir(tmp_path):
    d = tmp_path / "pipeline_artifacts"
    d.mkdir()
    return d


def test_reviewer_sentinel_ready_false_when_not_waiting(art_dir, monkeypatch):
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(art_dir))
    inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    inst.state = {"pipeline_status": "RUNNING", "sentinel_wait_started_at": "2026-01-15T10:00:00+00:00"}
    assert inst.reviewer_sentinel_ready_from_prior_wait() is False


def test_reviewer_sentinel_ready_false_when_missing_files(art_dir, monkeypatch):
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(art_dir))
    inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    inst.state = {"pipeline_status": "WAITING_FOR_SENTINEL", "sentinel_wait_started_at": "2026-01-15T10:00:00+00:00"}
    assert inst.reviewer_sentinel_ready_from_prior_wait() is False


def test_reviewer_sentinel_ready_false_when_done_older_than_wait(art_dir, monkeypatch):
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(art_dir))
    (art_dir / "reviewer_output.json").write_text("{}")
    (art_dir / "reviewer_output.done").write_text("")
    old = datetime(2026, 1, 15, 9, 0, 0, tzinfo=timezone.utc).timestamp()
    os.utime(art_dir / "reviewer_output.done", (old, old))

    inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    inst.state = {"pipeline_status": "WAITING_FOR_SENTINEL", "sentinel_wait_started_at": "2026-01-15T10:00:00+00:00"}
    assert inst.reviewer_sentinel_ready_from_prior_wait() is False


def test_reviewer_sentinel_ready_true_when_done_after_wait(art_dir, monkeypatch):
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(art_dir))
    (art_dir / "reviewer_output.json").write_text("{}")
    (art_dir / "reviewer_output.done").write_text("")
    new = datetime(2026, 1, 15, 10, 5, 0, tzinfo=timezone.utc).timestamp()
    os.utime(art_dir / "reviewer_output.done", (new, new))

    inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    inst.state = {"pipeline_status": "WAITING_FOR_SENTINEL", "sentinel_wait_started_at": "2026-01-15T10:00:00+00:00"}
    assert inst.reviewer_sentinel_ready_from_prior_wait() is True


def test_reviewer_sentinel_ready_parses_zulu_sentinel_wait_timestamp(art_dir, monkeypatch):
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(art_dir))
    (art_dir / "reviewer_output.json").write_text("{}")
    (art_dir / "reviewer_output.done").write_text("")
    new = datetime(2026, 1, 15, 10, 5, 0, tzinfo=timezone.utc).timestamp()
    os.utime(art_dir / "reviewer_output.done", (new, new))

    inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    inst.state = {"pipeline_status": "WAITING_FOR_SENTINEL", "sentinel_wait_started_at": "2026-01-15T10:00:00Z"}
    assert inst.reviewer_sentinel_ready_from_prior_wait() is True
