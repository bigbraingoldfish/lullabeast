"""Tests for GET /api/models/probe (backing-server liveness).

The catalog and gateway-allowlist checks confirm the gateway accepts a model,
not that the server behind it can serve it. This read-only endpoint probes a
local provider's /models endpoint so pickers and overrides can flag a dead
server at assignment time; cloud models report not_probeable rather than a
guess, because they are reached through the gateway.
"""
import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from ui.server import app

client = TestClient(app)

LOCAL = "local/llama3:8b"
CLOUD = "openrouter/z-ai/glm-5.2"


def _cfg(tmp_path: Path) -> dict:
    oc_root = tmp_path / "openclaw"
    oc_root.mkdir()
    (oc_root / "openclaw.json").write_text(json.dumps({
        "models": {
            "providers": {
                "openrouter": {"models": [{"id": "z-ai/glm-5.2", "input": ["text"]}]},
                "local": {
                    "baseUrl": "http://host.docker.internal:11434/v1",
                    "apiKey": "no-key",
                    "models": [{"id": "llama3:8b"}],
                },
            }
        },
        "agents": {"defaults": {"models": {}}, "list": []},
    }))
    return {"openclaw_root": str(oc_root)}


def _get(cfg, model):
    with patch("ui.server.load_config", return_value=cfg):
        return client.get("/api/models/probe", params={"model": model})


def test_missing_model_param_400(tmp_path):
    r = _get(_cfg(tmp_path), "")
    assert r.status_code == 400


def test_unregistered_model_400(tmp_path):
    r = _get(_cfg(tmp_path), "local/nope")
    assert r.status_code == 400
    assert "not a registered model" in r.json()["detail"]


def test_cloud_model_not_probeable(tmp_path):
    r = _get(_cfg(tmp_path), CLOUD)
    assert r.status_code == 200
    assert r.json()["state"] == "not_probeable"


def test_local_live_and_serving(tmp_path):
    with patch("ui.server._local_models.probe_openai_models", return_value=["llama3:8b"]):
        r = _get(_cfg(tmp_path), LOCAL)
    data = r.json()
    assert data["state"] == "live"
    assert data["serves_model"] is True
    assert isinstance(data["latency_ms"], int)
    assert data["model"] == LOCAL


def test_local_live_but_model_not_served(tmp_path):
    with patch("ui.server._local_models.probe_openai_models", return_value=["other"]):
        r = _get(_cfg(tmp_path), LOCAL)
    data = r.json()
    assert data["state"] == "live"
    assert data["serves_model"] is False


def test_local_unreachable(tmp_path):
    with patch("ui.server._local_models.probe_openai_models", return_value=None):
        r = _get(_cfg(tmp_path), LOCAL)
    data = r.json()
    assert data["state"] == "unreachable"
    assert data["base_url"]


def test_local_provider_without_base_url_not_probeable(tmp_path):
    cfg = _cfg(tmp_path)
    oc_path = Path(cfg["openclaw_root"]) / "openclaw.json"
    oc = json.loads(oc_path.read_text())
    del oc["models"]["providers"]["local"]["baseUrl"]
    oc_path.write_text(json.dumps(oc))
    r = _get(cfg, LOCAL)
    assert r.json()["state"] == "not_probeable"
