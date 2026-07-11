"""Tests for GET /api/doctor (the server consumer of the doctor module)."""

from __future__ import annotations

import json
import os
import re
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


class TestDoctorHealthPanel:
    """Health card on the Settings screen. The frontend is an in-browser
    Babel block with no transpiler in CI, so these pin the render gates by
    marker (the repo's UI-test idiom); they deliberately do not re-test the
    report content, which the endpoint tests above own."""

    @pytest.fixture(scope="class")
    def html(self):
        path = os.path.join(os.path.dirname(__file__), "..", "ui", "index.html")
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_health_card_component_present(self, html):
        assert "function DoctorHealthCard()" in html
        assert 'data-testid="doctor-health-card"' in html

    def test_card_runs_the_doctor_on_click_not_on_mount(self, html):
        # The full checklist is heavy; the card mounts idle and runs on demand
        # (maintainer request 2026-07-10).
        start = html.index("function DoctorHealthCard()")
        end = html.index("function PreflightScreen(")
        seg = html[start:end]
        assert 'fetch("/api/doctor")' in seg
        assert "useEffect" not in seg
        assert 'data-testid="doctor-health-run"' in seg

    def test_card_mounted_on_the_settings_screen_only(self, html):
        settings = html.index("function SettingsScreen(")
        assert "<DoctorHealthCard />" in html[settings:]
        assert html.count("<DoctorHealthCard />") == 1

    def test_settings_screen_reachable_from_sidebar(self, html):
        assert 'data-testid="nav-settings"' in html
        assert '"settings" && (' in html or "'settings')" in html

    def test_settings_gateway_card_present(self, html):
        # Model/provider management lives in OpenClaw; Settings opens its UI
        # signed in via one button. The token rides the URL hash fragment
        # (never sent to the server, absent from logs) from the token-guarded
        # gateway-access endpoint.
        assert 'data-testid="settings-gateway-card"' in html
        assert 'data-testid="gateway-open-link"' in html
        assert "/api/setup/gateway-access" in html
        assert "#token=" in html
        # The link port is deployment-aware: it renders whatever port the
        # gateway-access endpoint reports (dev stack publishes 28789), and
        # only falls back to the standard published port 18789 when the
        # endpoint gives no usable URL — never a hardcoded port in the URL.
        assert re.search(r"useState\(\s*18789\s*\)", html), "gateway port fallback must stay 18789"
        assert ":${gatewayPort}" in html, "gateway URL must use the resolved port"
        assert re.search(r"setGatewayPort\(", html), "port must be adopted from gateway-access"
        # The two-button copy-token flow is gone.
        assert 'data-testid="gateway-copy-token"' not in html

    def test_card_renders_statuses_and_fix_hints(self, html):
        # One glyph per doctor status, plus the fix-hint line for warn/fail rows.
        assert "DOCTOR_STATUS_ICON" in html
        for status in ("ok:", "warn:", "fail:", "skipped:"):
            assert status in html.split("DOCTOR_STATUS_ICON")[1][:400]
        assert "Fix: {c.fix_hint}" in html

    def test_per_check_hover_explanations(self, html):
        # Every row carries a plain-language hover title from the explain map.
        assert "DOCTOR_CHECK_EXPLAIN" in html
        assert "title={DOCTOR_CHECK_EXPLAIN[c.id] || c.title}" in html

    def test_run_is_the_cards_only_action(self, html):
        """The run button is the card's single action; the report itself stays
        read-only (no per-check fix or mutate controls)."""
        start = html.index('data-testid="doctor-health-card"')
        end = html.index("function PreflightScreen(")
        assert html[start:end].count("<button") == 1
