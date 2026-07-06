"""Tests for GET /api/doctor (DS-1 server consumer of the doctor module)."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import autodev.installer.doctor as doctor_mod
from ui.server import app

client = TestClient(app)

EXPECTED_KEYS = {"id", "title", "status", "detail", "fix_hint"}


@pytest.fixture
def hermetic_config(tmp_path):
    """Minimal tmp config; also neutralize the openclaw CLI subprocess probes."""
    oc = tmp_path / "openclaw"
    repo = tmp_path / "repo"
    proot = repo / ".autodev"
    for d in (oc, repo / "ui", proot):
        d.mkdir(parents=True)
    (oc / "openclaw.json").write_text(json.dumps({"hooks": {}}))
    return {
        "openclaw_root": str(oc),
        "autodev_repo_path": str(repo),
        "autodev_pipeline_root": str(proot),
        "hooks_url": "http://127.0.0.1:1/hooks/agent",  # port 1: nothing listens
        "hooks_token": "",
        "ui_token": "",
        "port": 1,
        "project_dir_path": str(proot / "pipeline-project"),
        "pipeline_state_path": str(proot / "pipeline_state.json"),
        "lock_path": str(proot / "pipeline.lock"),
    }


@pytest.fixture(autouse=True)
def _no_openclaw_cli(monkeypatch):
    monkeypatch.setattr(
        doctor_mod, "_openclaw_cli", lambda *a: (None, "openclaw CLI not on PATH", "")
    )


def test_doctor_endpoint_shape(hermetic_config):
    with patch("ui.server.load_config", return_value=hermetic_config):
        resp = client.get("/api/doctor")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"status", "counts", "checks"}
    assert body["status"] in ("ok", "warn", "fail")
    assert isinstance(body["checks"], list) and body["checks"]
    for c in body["checks"]:
        assert set(c.keys()) == EXPECTED_KEYS
        assert c["status"] in ("ok", "warn", "fail", "skipped")
    ids = [c["id"] for c in body["checks"]]
    assert "symlink_consistency" in ids and "webhook_ping" in ids


def test_doctor_endpoint_never_live(hermetic_config):
    """The server run must not perform the side-effectful webhook ping."""
    with patch("ui.server.load_config", return_value=hermetic_config):
        body = client.get("/api/doctor").json()
    ping = next(c for c in body["checks"] if c["id"] == "webhook_ping")
    assert ping["status"] == "skipped"


def test_doctor_endpoint_requires_token(monkeypatch, hermetic_config):
    """/api/doctor sits behind the dashboard token like every other /api route."""
    cfg = dict(hermetic_config, ui_token="sekret-doctor")
    monkeypatch.delenv("AUTODEV_UI_TOKEN", raising=False)
    with patch("ui.server.load_config", return_value=cfg):
        denied = client.get("/api/doctor")
        granted = client.get(
            "/api/doctor", headers={"Authorization": "Bearer sekret-doctor"}
        )
    assert denied.status_code == 401
    assert granted.status_code == 200
