"""Tests for the per-role model selection API (Stage B).

GET /api/models/roles (catalog + assignments), PUT /api/models/roles (knob
writes + apply marker), PUT /api/models/properties (overlay writes + apply
marker). The cross-agent contract: the server writes provider.env lines and
the overlay file; the entrypoint watch loop consumes the apply marker and
performs the actual re-render + gateway restart.
"""
import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from autodev.installer.openclaw_template import TEMPLATE_MODEL_DEFAULTS
from ui.server import _ROLE_KNOBS, app

client = TestClient(app)

ALL_ROLES = ("planner", "executor", "reviewer", "prd-creator", "roadmap-converter", "escalation")
VISION_ROLES = ("executor", "reviewer", "prd-creator")


def _openclaw_json() -> dict:
    """A live-config fixture: shipped cloud provider, probed local provider, and
    a hand-added provider, plus the six role agents and per-model params."""
    return {
        "models": {
            "providers": {
                "openrouter": {
                    "models": [
                        {
                            "id": "moonshotai/kimi-k2.7-code",
                            "name": "Kimi K2.7 Code",
                            "reasoning": True,
                            "input": ["text", "image"],
                            "cost": {"input": 0.75, "output": 3.5, "cacheRead": 0.16, "cacheWrite": 0},
                            "contextWindow": 262144,
                            "maxTokens": 32768,
                        },
                        {
                            "id": "z-ai/glm-5.2",
                            "name": "GLM 5.2",
                            "reasoning": True,
                            "input": ["text"],
                            "cost": {"input": 0.95, "output": 3},
                            "contextWindow": 1000000,
                            "maxTokens": 131072,
                        },
                    ]
                },
                "local": {
                    "apiKey": "no-key",
                    "models": [
                        {"id": "qwen3.5", "name": "qwen3.5", "input": ["text", "image"]},
                        {"id": "llama3-text", "name": "llama3-text", "input": ["text"]},
                        {"id": "mystery", "name": "mystery"},  # no input declared
                    ],
                },
                "custom": {"models": [{"id": "vendor/hand-added", "name": "Hand Added"}]},
            }
        },
        "agents": {
            "defaults": {
                "models": {
                    "openrouter/moonshotai/kimi-k2.7-code": {
                        "params": {"temperature": 0.6, "top_p": 0.95}
                    }
                }
            },
            "list": [
                {"id": "planner", "model": {"primary": "openrouter/z-ai/glm-5.2"}},
                {"id": "executor", "model": {"primary": "openrouter/moonshotai/kimi-k2.7-code"}},
                {"id": "reviewer", "model": {"primary": "openrouter/moonshotai/kimi-k2.7-code"}},
                {"id": "prd-creator", "model": {"primary": "openrouter/moonshotai/kimi-k2.7-code"}},
                {"id": "roadmap-converter", "model": "openrouter/z-ai/glm-5.2"},  # string form
                {"id": "escalation", "model": {"primary": "local/qwen3.5"}},
            ],
        },
    }


@pytest.fixture
def cfg(tmp_path: Path) -> dict:
    oc_root = tmp_path / "openclaw"
    oc_root.mkdir()
    (oc_root / "openclaw.json").write_text(json.dumps(_openclaw_json()))
    return {
        "openclaw_root": str(oc_root),
        "provider_key_path": str(tmp_path / "secrets" / "provider.env"),
        "setup_marker_path": str(tmp_path / ".setup-mode"),
        "apply_request_path": str(tmp_path / "secrets" / "apply.request"),
        "model_overrides_path": str(tmp_path / "model-overrides.json"),
        "pipeline_state_path": str(tmp_path / "pipeline_state.json"),
        "lock_path": str(tmp_path / "pipeline.lock"),
    }


@pytest.fixture(autouse=True)
def _no_env_pins(monkeypatch):
    """Default to no deploy/.env pins so role dicts read pinned=False; the
    pinned-detection test sets AUTODEV_PINNED_MODEL_KNOBS explicitly."""
    monkeypatch.delenv("AUTODEV_PINNED_MODEL_KNOBS", raising=False)


def _get(cfg):
    with patch("ui.server.load_config", return_value=cfg):
        return client.get("/api/models/roles")


def _put_roles(cfg, body):
    with patch("ui.server.load_config", return_value=cfg):
        return client.put("/api/models/roles", json=body)


def _put_props(cfg, body):
    with patch("ui.server.load_config", return_value=cfg):
        return client.put("/api/models/properties", json=body)


# ── the role/knob map is the template contract ───────────────────────────────

def test_role_knobs_mirror_template_defaults():
    assert set(_ROLE_KNOBS.values()) == set(TEMPLATE_MODEL_DEFAULTS.keys())
    assert set(_ROLE_KNOBS.keys()) == set(ALL_ROLES)


# ── GET /api/models/roles ────────────────────────────────────────────────────

class TestGetRoles:
    def test_shape_and_assignments(self, cfg):
        r = _get(cfg)
        assert r.status_code == 200
        data = r.json()
        assert data["supported"] is True
        assert set(data["roles"]) == set(ALL_ROLES)
        assert data["roles"]["executor"] == {
            "model": "openrouter/moonshotai/kimi-k2.7-code",
            "knob": "EXECUTOR_MODEL",
            "pinned": False,
        }
        # The plain-string model form is tolerated.
        assert data["roles"]["roadmap-converter"]["model"] == "openrouter/z-ai/glm-5.2"
        assert data["roles"]["escalation"]["model"] == "local/qwen3.5"

    def test_roles_report_deploy_env_pins(self, cfg, monkeypatch):
        # A *_MODEL knob pinned in deploy/.env wins over any provider.env write
        # at render; the entrypoint exports that boot-time set so the card can
        # say so instead of letting a save silently revert. Only the named
        # knobs read pinned.
        monkeypatch.setenv("AUTODEV_PINNED_MODEL_KNOBS", "ROADMAP_MODEL EXECUTOR_MODEL")
        roles = _get(cfg).json()["roles"]
        assert roles["roadmap-converter"]["pinned"] is True
        assert roles["executor"]["pinned"] is True
        assert roles["planner"]["pinned"] is False
        assert roles["prd-creator"]["pinned"] is False

    def test_defaults_come_from_template(self, cfg):
        data = _get(cfg).json()
        assert data["defaults"]["planner"] == TEMPLATE_MODEL_DEFAULTS["PLANNER_MODEL"]
        assert data["defaults"]["prd-creator"] == TEMPLATE_MODEL_DEFAULTS["PRD_MODEL"]

    def test_catalog_covers_every_provider(self, cfg):
        ids = {e["id"] for e in _get(cfg).json()["catalog"]}
        assert "openrouter/moonshotai/kimi-k2.7-code" in ids
        assert "local/qwen3.5" in ids
        assert "custom/vendor/hand-added" in ids

    def test_catalog_entry_fields(self, cfg):
        entries = {e["id"]: e for e in _get(cfg).json()["catalog"]}
        kimi = entries["openrouter/moonshotai/kimi-k2.7-code"]
        assert kimi["provider"] == "openrouter"
        assert kimi["name"] == "Kimi K2.7 Code"
        assert kimi["input"] == ["text", "image"]
        assert kimi["contextWindow"] == 262144
        assert kimi["maxTokens"] == 32768
        assert kimi["cost"]["input"] == 0.75
        assert kimi["reasoning"] is True
        assert kimi["params"] == {"temperature": 0.6, "top_p": 0.95}
        # Undeclared metadata surfaces as null/empty, never a guess.
        mystery = entries["local/mystery"]
        assert mystery["input"] is None
        assert mystery["params"] == {}

    def test_overlay_is_applied_to_catalog(self, cfg):
        Path(cfg["model_overrides_path"]).write_text(
            json.dumps(
                {
                    "models": {
                        "openrouter/moonshotai/kimi-k2.7-code": {
                            "cost": {"input": 0.5},
                            "params": {"temperature": 0.2},
                        }
                    }
                }
            )
        )
        entries = {e["id"]: e for e in _get(cfg).json()["catalog"]}
        kimi = entries["openrouter/moonshotai/kimi-k2.7-code"]
        assert kimi["cost"]["input"] == 0.5
        assert kimi["cost"]["output"] == 3.5, "unedited cost keys keep registered values"
        assert kimi["params"] == {"temperature": 0.2, "top_p": 0.95}

    def test_bare_metal_reads_but_reports_unsupported(self, cfg):
        for key in ("provider_key_path", "apply_request_path", "model_overrides_path"):
            cfg.pop(key)
        data = _get(cfg).json()
        assert data["supported"] is False
        assert data["roles"]["planner"]["model"] == "openrouter/z-ai/glm-5.2"

    def test_503_when_openclaw_json_missing(self, cfg):
        os.remove(os.path.join(cfg["openclaw_root"], "openclaw.json"))
        assert _get(cfg).status_code == 503


# ── PUT /api/models/roles ────────────────────────────────────────────────────

class TestPutRolesGuards:
    def test_409_bare_metal(self, cfg):
        cfg.pop("provider_key_path")
        r = _put_roles(cfg, {"planner": "openrouter/z-ai/glm-5.2"})
        assert r.status_code == 409
        assert "deploy/.env" in r.json()["detail"]

    def test_409_setup_mode(self, cfg):
        Path(cfg["setup_marker_path"]).write_text("")
        r = _put_roles(cfg, {"planner": "openrouter/z-ai/glm-5.2"})
        assert r.status_code == 409
        assert not os.path.exists(cfg["apply_request_path"])

    def test_409_pipeline_active(self, cfg):
        Path(cfg["pipeline_state_path"]).write_text(json.dumps({"pipeline_status": "RUNNING"}))
        with patch("ui.server._orchestrator_alive_from_config", return_value=True):
            r = _put_roles(cfg, {"planner": "openrouter/z-ai/glm-5.2"})
        assert r.status_code == 409
        assert not os.path.exists(cfg["apply_request_path"])

    def test_stale_running_state_does_not_block(self, cfg):
        # A RUNNING left behind by a dead orchestrator must not lock the card.
        Path(cfg["pipeline_state_path"]).write_text(json.dumps({"pipeline_status": "RUNNING"}))
        with patch("ui.server._orchestrator_alive_from_config", return_value=False):
            r = _put_roles(cfg, {"planner": "openrouter/z-ai/glm-5.2"})
        assert r.status_code == 200

    def test_idle_pipeline_does_not_block(self, cfg):
        Path(cfg["pipeline_state_path"]).write_text(json.dumps({"pipeline_status": "COMPLETE"}))
        r = _put_roles(cfg, {"planner": "openrouter/z-ai/glm-5.2"})
        assert r.status_code == 200


class TestPutRolesValidation:
    def test_400_empty_body(self, cfg):
        assert _put_roles(cfg, {}).status_code == 400

    def test_400_unknown_role(self, cfg):
        r = _put_roles(cfg, {"deployer": "openrouter/z-ai/glm-5.2"})
        assert r.status_code == 400
        assert "unknown role" in r.json()["detail"]

    def test_400_unregistered_model(self, cfg):
        r = _put_roles(cfg, {"planner": "openrouter/vendor/ghost"})
        assert r.status_code == 400
        assert "not a registered model" in r.json()["detail"]
        assert not os.path.exists(cfg["provider_key_path"])
        assert not os.path.exists(cfg["apply_request_path"])

    @pytest.mark.parametrize("role", VISION_ROLES)
    def test_400_text_only_model_on_vision_role(self, cfg, role):
        r = _put_roles(cfg, {role: "openrouter/z-ai/glm-5.2"})
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert "image input" in detail
        assert "—" not in detail, "UI copy never uses em dashes"

    def test_text_only_model_allowed_on_planner(self, cfg):
        assert _put_roles(cfg, {"planner": "openrouter/z-ai/glm-5.2"}).status_code == 200

    def test_undeclared_input_allowed_on_vision_role(self, cfg):
        # No declared input is unverified, not text-only; the doctor stays the
        # backstop (never a guessed verdict).
        assert _put_roles(cfg, {"reviewer": "local/mystery"}).status_code == 200

    def test_overlay_vision_edit_is_honored(self, cfg):
        # Marking a registered model text-only via the overlay blocks it on
        # vision roles: the overlay is the source of truth for modality gates.
        Path(cfg["model_overrides_path"]).write_text(
            json.dumps({"models": {"local/mystery": {"input": ["text"]}}})
        )
        assert _put_roles(cfg, {"reviewer": "local/mystery"}).status_code == 400

    def test_400_non_string_model(self, cfg):
        assert _put_roles(cfg, {"planner": 42}).status_code == 400


class TestPutRolesWrites:
    def test_writes_knobs_and_marker(self, cfg):
        r = _put_roles(
            cfg,
            {"planner": "openrouter/moonshotai/kimi-k2.7-code", "escalation": "local/qwen3.5"},
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True, "restarting": True}
        content = Path(cfg["provider_key_path"]).read_text()
        assert "PLANNER_MODEL=openrouter/moonshotai/kimi-k2.7-code\n" in content
        assert "ESCALATION_MODEL=local/qwen3.5\n" in content
        assert os.path.exists(cfg["apply_request_path"])

    def test_marker_failure_reports_apply_error_not_config_error(self, cfg):
        # The assignments saved; only the apply-request marker failed. The error
        # must name the apply request, not claim the key-file write failed.
        with patch("ui.server._touch_apply_marker", side_effect=OSError(28, "No space left")):
            r = _put_roles(cfg, {"planner": "openrouter/z-ai/glm-5.2"})
        assert r.status_code == 500
        detail = r.json()["detail"]
        assert "apply" in detail.lower()
        assert "provider key file" not in detail
        assert "PLANNER_MODEL=openrouter/z-ai/glm-5.2" in Path(cfg["provider_key_path"]).read_text()

    def test_partial_update_preserves_unrelated_lines(self, cfg):
        p = Path(cfg["provider_key_path"])
        p.parent.mkdir(parents=True)
        p.write_text(
            "OPENROUTER_API_KEY=sk-or-secret\n"
            "EXECUTOR_MODEL=openrouter/moonshotai/kimi-k2.7-code\n"
            "PLANNER_MODEL=openrouter/z-ai/glm-5.2\n"
        )
        r = _put_roles(cfg, {"planner": "openrouter/moonshotai/kimi-k2.7-code"})
        assert r.status_code == 200
        lines = p.read_text().splitlines()
        assert "OPENROUTER_API_KEY=sk-or-secret" in lines
        assert "EXECUTOR_MODEL=openrouter/moonshotai/kimi-k2.7-code" in lines
        assert lines.count("PLANNER_MODEL=openrouter/moonshotai/kimi-k2.7-code") == 1
        assert not any(l == "PLANNER_MODEL=openrouter/z-ai/glm-5.2" for l in lines)

    def test_file_mode_0600(self, cfg):
        _put_roles(cfg, {"planner": "openrouter/z-ai/glm-5.2"})
        mode = stat.S_IMODE(os.stat(cfg["provider_key_path"]).st_mode)
        assert mode == 0o600

    def test_rejected_put_writes_nothing(self, cfg):
        _put_roles(cfg, {"reviewer": "openrouter/z-ai/glm-5.2"})
        assert not os.path.exists(cfg["provider_key_path"])
        assert not os.path.exists(cfg["apply_request_path"])


# ── PUT /api/models/properties ───────────────────────────────────────────────

class TestPutProperties:
    def test_409_when_overrides_path_unset(self, cfg):
        cfg.pop("model_overrides_path")
        r = _put_props(cfg, {"local/qwen3.5": {"reasoning": True}})
        assert r.status_code == 409

    def test_409_setup_mode(self, cfg):
        Path(cfg["setup_marker_path"]).write_text("")
        r = _put_props(cfg, {"local/qwen3.5": {"reasoning": True}})
        assert r.status_code == 409

    def test_409_pipeline_active(self, cfg):
        Path(cfg["pipeline_state_path"]).write_text(json.dumps({"pipeline_status": "WAITING_FOR_SENTINEL"}))
        with patch("ui.server._orchestrator_alive_from_config", return_value=True):
            r = _put_props(cfg, {"local/qwen3.5": {"reasoning": True}})
        assert r.status_code == 409

    def test_400_unregistered_model(self, cfg):
        r = _put_props(cfg, {"openrouter/vendor/ghost": {"reasoning": True}})
        assert r.status_code == 400
        assert not os.path.exists(cfg["model_overrides_path"])

    def test_400_invalid_property(self, cfg):
        r = _put_props(cfg, {"local/qwen3.5": {"cost": {"input": -1}}})
        assert r.status_code == 400
        assert "cost.input" in r.json()["detail"]
        assert not os.path.exists(cfg["apply_request_path"])

    def test_400_empty_body(self, cfg):
        assert _put_props(cfg, {}).status_code == 400

    def test_persists_overlay_and_marker(self, cfg):
        r = _put_props(
            cfg,
            {
                "local/qwen3.5": {
                    "input": ["text", "image"],
                    "contextWindow": 131072,
                    "cost": {"input": 0, "output": 0},
                    "reasoning": True,
                    "params": {"temperature": 0.4},
                }
            },
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True, "restarting": True}
        saved = json.loads(Path(cfg["model_overrides_path"]).read_text())
        assert saved["models"]["local/qwen3.5"]["contextWindow"] == 131072
        assert saved["models"]["local/qwen3.5"]["params"] == {"temperature": 0.4}
        assert os.path.exists(cfg["apply_request_path"])

    def test_marker_failure_reports_apply_error_not_config_error(self, cfg):
        # The overlay saved; only the apply-request marker failed. The error must
        # name the apply request, not claim the overrides-file write failed.
        with patch("ui.server._touch_apply_marker", side_effect=OSError(28, "No space left")):
            r = _put_props(cfg, {"local/qwen3.5": {"reasoning": True}})
        assert r.status_code == 500
        detail = r.json()["detail"]
        assert "apply" in detail.lower()
        assert "overrides file" not in detail
        assert os.path.exists(cfg["model_overrides_path"])

    def test_merge_preserves_other_models_and_fields(self, cfg):
        Path(cfg["model_overrides_path"]).write_text(
            json.dumps(
                {
                    "models": {
                        "local/qwen3.5": {"reasoning": True},
                        "openrouter/z-ai/glm-5.2": {"cost": {"input": 0.9}},
                    }
                }
            )
        )
        _put_props(cfg, {"local/qwen3.5": {"contextWindow": 65536}})
        saved = json.loads(Path(cfg["model_overrides_path"]).read_text())
        assert saved["models"]["local/qwen3.5"] == {"reasoning": True, "contextWindow": 65536}
        assert saved["models"]["openrouter/z-ai/glm-5.2"] == {"cost": {"input": 0.9}}

    def test_null_clears_an_override(self, cfg):
        Path(cfg["model_overrides_path"]).write_text(
            json.dumps({"models": {"local/qwen3.5": {"reasoning": True}}})
        )
        r = _put_props(cfg, {"local/qwen3.5": {"reasoning": None}})
        assert r.status_code == 200
        saved = json.loads(Path(cfg["model_overrides_path"]).read_text())
        assert saved["models"] == {}

    def test_get_reflects_saved_properties_before_apply(self, cfg):
        # Between the PUT and the entrypoint's apply pass the live config is
        # stale; the GET must already show the saved values.
        _put_props(cfg, {"local/qwen3.5": {"maxTokens": 4096}})
        entries = {e["id"]: e for e in _get(cfg).json()["catalog"]}
        assert entries["local/qwen3.5"]["maxTokens"] == 4096

    def test_400_text_only_edit_on_vision_bound_model(self, cfg):
        # kimi runs reviewer/executor/prd-creator; stripping image would break
        # their visual turns, so the edit is refused and nothing is written.
        r = _put_props(cfg, {"openrouter/moonshotai/kimi-k2.7-code": {"input": ["text"]}})
        assert r.status_code == 400
        assert "image" in r.json()["detail"]
        assert not os.path.exists(cfg["model_overrides_path"])
        assert not os.path.exists(cfg["apply_request_path"])

    def test_multimodal_edit_on_vision_bound_model_ok(self, cfg):
        r = _put_props(
            cfg, {"openrouter/moonshotai/kimi-k2.7-code": {"input": ["text", "image"]}}
        )
        assert r.status_code == 200

    def test_text_only_edit_on_non_vision_model_ok(self, cfg):
        # glm-5.2 runs only planner/roadmap-converter, so text-only is fine.
        r = _put_props(cfg, {"openrouter/z-ai/glm-5.2": {"input": ["text"]}})
        assert r.status_code == 200
