"""Tests for POST /api/ideas/{id}/prd-section-revert."""
import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from ui.server import _slugify_section, app

client = TestClient(app)

FAKE_CONFIG = {
    "ideas_dir": "/tmp/test-prd-revert-ideas",
    "hooks_url": "http://localhost:19999/hooks/agent",
    "hooks_token": "test-token",
}


def _make_idea(ideas_dir: str) -> str:
    idea_id = str(uuid.uuid4())
    idea_dir = Path(ideas_dir) / idea_id
    idea_dir.mkdir(parents=True, exist_ok=True)
    turns_dir = idea_dir / "turns"
    turns_dir.mkdir()
    (turns_dir / "1.done").write_text("done")
    (turns_dir / "1.md").write_text("Hello")
    session = {
        "name": "Test Idea",
        "messages": [],
        "prd_content": "",
        "roadmap_content": "",
        "annotations": [],
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
    }
    (idea_dir / "session.json").write_text(json.dumps(session))
    return idea_id


@pytest.fixture
def ideas_dir(tmp_path):
    return str(tmp_path / "ideas")


@pytest.fixture
def idea_id(ideas_dir):
    Path(ideas_dir).mkdir(parents=True, exist_ok=True)
    return _make_idea(ideas_dir)


@pytest.fixture
def config_patch(ideas_dir):
    cfg = {**FAKE_CONFIG, "ideas_dir": ideas_dir}
    with patch("ui.server.load_config", return_value=cfg):
        yield cfg


def _write_pair(ideas_dir, idea_id, cur: str, prev: str):
    p = Path(ideas_dir) / idea_id
    (p / "prd_draft.md").write_text(cur, encoding="utf-8")
    (p / "prd_draft.previous.md").write_text(prev, encoding="utf-8")


def test_revert_unknown_section_key(idea_id, config_patch, ideas_dir):
    _write_pair(
        ideas_dir,
        idea_id,
        "## Problem Statement\n\nA\n",
        "## Problem Statement\n\nB\n",
    )
    r = client.post(
        f"/api/ideas/{idea_id}/prd-section-revert",
        json={"section_key": "not-a-real-slug"},
    )
    assert r.status_code == 422


def test_revert_404_without_previous_file(idea_id, config_patch, ideas_dir):
    p = Path(ideas_dir) / idea_id
    (p / "prd_draft.md").write_text("## Problem Statement\n\nA\n", encoding="utf-8")
    r = client.post(
        f"/api/ideas/{idea_id}/prd-section-revert",
        json={"section_key": _slugify_section("Problem Statement")},
    )
    assert r.status_code == 404


def test_revert_only_target_section_unchanged_others(idea_id, config_patch, ideas_dir):
    cur = """## Problem Statement\n\nNEW_P\n\n## User Stories\n\nKEEP_US\n"""
    prev = """## Problem Statement\n\nOLD_P\n\n## User Stories\n\nKEEP_US\n"""
    _write_pair(ideas_dir, idea_id, cur, prev)
    sk = _slugify_section("Problem Statement")
    r = client.post(
        f"/api/ideas/{idea_id}/prd-section-revert",
        json={"section_key": sk},
    )
    assert r.status_code == 200
    assert r.json()["content"] == "OLD_P"
    new_cur = (Path(ideas_dir) / idea_id / "prd_draft.md").read_text(encoding="utf-8")
    new_prev = (Path(ideas_dir) / idea_id / "prd_draft.previous.md").read_text(encoding="utf-8")
    assert "OLD_P" in new_cur
    assert "NEW_P" in new_prev
    assert "KEEP_US" in new_cur
    assert "KEEP_US" in new_prev


def test_second_revert_restores_original(idea_id, config_patch, ideas_dir):
    cur = """## Problem Statement\n\nNEW_P\n"""
    prev = """## Problem Statement\n\nOLD_P\n"""
    _write_pair(ideas_dir, idea_id, cur, prev)
    sk = _slugify_section("Problem Statement")
    r1 = client.post(f"/api/ideas/{idea_id}/prd-section-revert", json={"section_key": sk})
    assert r1.status_code == 200
    r2 = client.post(f"/api/ideas/{idea_id}/prd-section-revert", json={"section_key": sk})
    assert r2.status_code == 200
    final_cur = (Path(ideas_dir) / idea_id / "prd_draft.md").read_text(encoding="utf-8")
    final_prev = (Path(ideas_dir) / idea_id / "prd_draft.previous.md").read_text(encoding="utf-8")
    assert "NEW_P" in final_cur
    assert "OLD_P" in final_prev
    from ui.server import _parse_prd_sections

    assert "NEW_P" in (_parse_prd_sections(final_cur)["Problem Statement"] or "")
    assert "OLD_P" in (_parse_prd_sections(final_prev)["Problem Statement"] or "")


def test_revert_preserves_unrecognized_heading_block(idea_id, config_patch, ideas_dir):
    cur = """## Problem Statement\n\nCUR\n\n## Custom Agent Section\n\nmust stay\n\n## User Stories\n\nUS\n"""
    prev = """## Problem Statement\n\nPREV\n\n## Custom Agent Section\n\nmust stay\n\n## User Stories\n\nUS\n"""
    _write_pair(ideas_dir, idea_id, cur, prev)
    sk = _slugify_section("Problem Statement")
    r = client.post(f"/api/ideas/{idea_id}/prd-section-revert", json={"section_key": sk})
    assert r.status_code == 200
    new_cur = (Path(ideas_dir) / idea_id / "prd_draft.md").read_text(encoding="utf-8")
    assert "Custom Agent Section" in new_cur
    assert "must stay" in new_cur
