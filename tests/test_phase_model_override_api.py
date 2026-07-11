"""Tests for the per-phase model override API (Stage E).

GET/POST/DELETE /api/phase-model-override write and clear one-phase role
overrides in the active project's phase_model_overrides.json; the orchestrator
consumes them at invocation and drops a phase's entry when the phase closes
(covered in autodev/tests/test_phase_model_override.py). No apply marker and no
gateway restart: sessions bake their model at creation.
"""
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ui.server import _is_visual_phase_raw_id, app

client = TestClient(app)

MULTIMODAL = "openrouter/moonshotai/kimi-k2.7-code"
TEXT_ONLY = "openrouter/z-ai/glm-5.2"
UNDECLARED = "local/mystery"

ROADMAP = """# Roadmap

- [x] `CORE-0` | CORE | Bootstrapping
- [ ] `CORE-1` | CORE | Build the thing
- [ ] `UI-2` | UI | Render the thing
- [-] `CORE-9` | CORE | Skipped work
"""


def _openclaw_json() -> dict:
    return {
        "models": {
            "providers": {
                "openrouter": {
                    "models": [
                        {"id": "moonshotai/kimi-k2.7-code", "input": ["text", "image"]},
                        {"id": "z-ai/glm-5.2", "input": ["text"]},
                    ]
                },
                "local": {"models": [{"id": "mystery"}]},
            }
        },
        "agents": {"list": []},
    }


@pytest.fixture
def cfg(tmp_path: Path) -> dict:
    oc_root = tmp_path / "openclaw"
    oc_root.mkdir()
    (oc_root / "openclaw.json").write_text(json.dumps(_openclaw_json()))
    project = tmp_path / "project"
    project.mkdir()
    (project / "roadmap.md").write_text(ROADMAP)
    state_path = tmp_path / "pipeline_state.json"
    state_path.write_text(json.dumps({"project_path": str(project)}))
    return {
        "openclaw_root": str(oc_root),
        "pipeline_state_path": str(state_path),
    }


def _overrides_path(cfg) -> Path:
    state = json.loads(Path(cfg["pipeline_state_path"]).read_text())
    project = Path(os.path.realpath(state["project_path"]))
    return project / ".autodev" / "pipeline" / "phase_model_overrides.json"


def _get(cfg):
    with patch("ui.server.load_config", return_value=cfg):
        return client.get("/api/phase-model-override")


def _post(cfg, body):
    with patch("ui.server.load_config", return_value=cfg):
        return client.post("/api/phase-model-override", json=body)


def _delete(cfg, body):
    with patch("ui.server.load_config", return_value=cfg):
        return client.request("DELETE", "/api/phase-model-override", json=body)


# ── the visual-phase rule mirrors the reviewer gate ──────────────────────────

def test_visual_phase_rule(monkeypatch):
    monkeypatch.delenv("AUTODEV_VISUAL_PHASE_RAW_IDS", raising=False)
    assert _is_visual_phase_raw_id("UI-2")
    assert _is_visual_phase_raw_id("INT-1")
    assert _is_visual_phase_raw_id("ui-2")
    assert not _is_visual_phase_raw_id("CORE-1")
    assert not _is_visual_phase_raw_id("")
    monkeypatch.setenv("AUTODEV_VISUAL_PHASE_RAW_IDS", "CORE-1, setup-e2")
    assert _is_visual_phase_raw_id("CORE-1")
    assert _is_visual_phase_raw_id("SETUP-E2")


# ── GET ───────────────────────────────────────────────────────────────────────

def test_get_empty_without_active_project(cfg):
    cfg.pop("pipeline_state_path")
    assert _get(cfg).json() == {"overrides": {}}


def test_get_returns_written_overrides(cfg):
    r = _post(cfg, {"raw_id": "CORE-1", "role": "executor", "model": MULTIMODAL})
    assert r.status_code == 200
    assert _get(cfg).json() == {"overrides": {"CORE-1": {"executor": MULTIMODAL}}}


# ── POST ──────────────────────────────────────────────────────────────────────

def test_post_writes_the_override_file(cfg):
    r = _post(cfg, {"raw_id": "CORE-1", "role": "executor", "model": MULTIMODAL})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and "warning" not in body
    data = json.loads(_overrides_path(cfg).read_text())
    assert data == {"CORE-1": {"executor": MULTIMODAL}}


def test_post_accumulates_roles_per_phase(cfg):
    _post(cfg, {"raw_id": "CORE-1", "role": "executor", "model": MULTIMODAL})
    _post(cfg, {"raw_id": "CORE-1", "role": "planner", "model": TEXT_ONLY})
    data = json.loads(_overrides_path(cfg).read_text())
    assert data == {"CORE-1": {"executor": MULTIMODAL, "planner": TEXT_ONLY}}


@pytest.mark.parametrize("body,fragment", [
    ({"raw_id": "CORE-1", "role": "prd-creator", "model": MULTIMODAL}, "unknown role"),
    ({"raw_id": "CORE-1", "role": "executor", "model": "nope/nope"}, "not a registered model"),
    ({"raw_id": "NOPE-1", "role": "executor", "model": MULTIMODAL}, "not a phase"),
    ({"raw_id": "CORE-0", "role": "executor", "model": MULTIMODAL}, "already closed"),
    ({"raw_id": "CORE-9", "role": "executor", "model": MULTIMODAL}, "already closed"),
    ({"raw_id": "", "role": "executor", "model": MULTIMODAL}, "raw_id"),
    ({"raw_id": "CORE-1", "role": "executor", "model": ""}, "model"),
])
def test_post_validation_rejects(cfg, body, fragment):
    r = _post(cfg, body)
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert fragment in detail
    assert "—" not in detail  # UI copy standard: no em dashes


def test_post_no_active_project_409(cfg):
    cfg.pop("pipeline_state_path")
    r = _post(cfg, {"raw_id": "CORE-1", "role": "executor", "model": MULTIMODAL})
    assert r.status_code == 409


def test_post_text_only_executor_blocked_on_visual_phase(cfg, monkeypatch):
    monkeypatch.delenv("AUTODEV_VISUAL_PHASE_RAW_IDS", raising=False)
    r = _post(cfg, {"raw_id": "UI-2", "role": "executor", "model": TEXT_ONLY})
    assert r.status_code == 400
    assert "image input" in r.json()["detail"]
    assert not _overrides_path(cfg).exists()


def test_post_env_var_extends_the_visual_set(cfg, monkeypatch):
    monkeypatch.setenv("AUTODEV_VISUAL_PHASE_RAW_IDS", "CORE-1")
    r = _post(cfg, {"raw_id": "CORE-1", "role": "reviewer", "model": TEXT_ONLY})
    assert r.status_code == 400


def test_post_text_only_executor_warns_on_nonvisual_phase(cfg, monkeypatch):
    monkeypatch.delenv("AUTODEV_VISUAL_PHASE_RAW_IDS", raising=False)
    r = _post(cfg, {"raw_id": "CORE-1", "role": "executor", "model": TEXT_ONLY})
    assert r.status_code == 200
    warning = r.json()["warning"]
    assert "text only" in warning
    assert "—" not in warning
    assert json.loads(_overrides_path(cfg).read_text()) == {"CORE-1": {"executor": TEXT_ONLY}}


def test_post_text_only_planner_allowed_anywhere(cfg, monkeypatch):
    monkeypatch.delenv("AUTODEV_VISUAL_PHASE_RAW_IDS", raising=False)
    r = _post(cfg, {"raw_id": "UI-2", "role": "planner", "model": TEXT_ONLY})
    assert r.status_code == 200
    assert "warning" not in r.json()


def test_post_undeclared_input_allowed_on_visual_phase(cfg, monkeypatch):
    # Only a confirmed text-only model is blocked (matches the roles PUT).
    monkeypatch.delenv("AUTODEV_VISUAL_PHASE_RAW_IDS", raising=False)
    r = _post(cfg, {"raw_id": "UI-2", "role": "executor", "model": UNDECLARED})
    assert r.status_code == 200


# ── DELETE ────────────────────────────────────────────────────────────────────

def test_delete_clears_one_role_and_keeps_the_rest(cfg):
    _post(cfg, {"raw_id": "CORE-1", "role": "executor", "model": MULTIMODAL})
    _post(cfg, {"raw_id": "CORE-1", "role": "planner", "model": TEXT_ONLY})
    r = _delete(cfg, {"raw_id": "CORE-1", "role": "executor"})
    assert r.status_code == 200
    assert json.loads(_overrides_path(cfg).read_text()) == {"CORE-1": {"planner": TEXT_ONLY}}


def test_delete_without_role_clears_the_phase_entry(cfg):
    _post(cfg, {"raw_id": "CORE-1", "role": "executor", "model": MULTIMODAL})
    _post(cfg, {"raw_id": "UI-2", "role": "reviewer", "model": MULTIMODAL})
    r = _delete(cfg, {"raw_id": "CORE-1"})
    assert r.status_code == 200
    assert json.loads(_overrides_path(cfg).read_text()) == {"UI-2": {"reviewer": MULTIMODAL}}


def test_delete_removes_the_file_when_empty(cfg):
    _post(cfg, {"raw_id": "CORE-1", "role": "executor", "model": MULTIMODAL})
    _delete(cfg, {"raw_id": "CORE-1", "role": "executor"})
    assert not _overrides_path(cfg).exists()


def test_delete_is_idempotent(cfg):
    assert _delete(cfg, {"raw_id": "CORE-1", "role": "executor"}).status_code == 200
    cfg.pop("pipeline_state_path")
    assert _delete(cfg, {"raw_id": "CORE-1"}).status_code == 200


def test_delete_rejects_unknown_role(cfg):
    r = _delete(cfg, {"raw_id": "CORE-1", "role": "escalation"})
    assert r.status_code == 400
