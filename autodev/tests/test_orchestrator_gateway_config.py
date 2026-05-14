"""load_config() must extract gateway_token and gateway_ws_url from openclaw.json."""

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for p in (PIPELINE_DIR, REPO_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import orchestrator as orch_mod  # noqa: E402


def _make_config(tmp_path_str, extra=None):
    cfg = {
        "hooks": {"token": "hooks-tok"},
        "gateway": {"port": 18789, "auth": {"mode": "token", "token": "gw-tok"}},
    }
    if extra:
        cfg.update(extra)
    p = os.path.join(tmp_path_str, "openclaw.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    return p


class TestLoadConfigGateway:
    def test_gateway_token_extracted(self, tmp_path, monkeypatch):
        """load_config() must populate config['gateway_token'] from gateway.auth.token."""
        cfg_path = _make_config(str(tmp_path))
        monkeypatch.setattr(orch_mod, "CONFIG_FILE", cfg_path)
        orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
        orch.skill_manager = None
        config = orch.load_config()
        assert config.get("gateway_token") == "gw-tok"

    def test_gateway_ws_url_constructed(self, tmp_path, monkeypatch):
        """load_config() must populate config['gateway_ws_url'] using gateway.port."""
        cfg_path = _make_config(str(tmp_path))
        monkeypatch.setattr(orch_mod, "CONFIG_FILE", cfg_path)
        orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
        orch.skill_manager = None
        config = orch.load_config()
        assert config.get("gateway_ws_url") == "ws://127.0.0.1:18789/__openclaw__/ws"
