"""GET /api/setup/gateway-access surfaces the OpenClaw Control UI URL + token.

Model/provider management lives in OpenClaw's gateway UI; this endpoint lets the
authenticated dashboard operator open it and copy its token without a shell. The
token is read from openclaw.json and must never appear in a log line.
"""
import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from ui.server import app

client = TestClient(app)


def _cfg(tmp_path, gateway=None) -> dict:
    oc = tmp_path / "openclaw"
    oc.mkdir(parents=True, exist_ok=True)
    doc = {}
    if gateway is not None:
        doc["gateway"] = gateway
    (oc / "openclaw.json").write_text(json.dumps(doc))
    return {"openclaw_root": str(oc)}


class TestGatewayAccess:
    def test_returns_url_and_token(self, tmp_path):
        cfg = _cfg(tmp_path, {"port": 18789, "auth": {"mode": "token", "token": "gw-secret-0123"}})
        with patch("ui.server.load_config", return_value=cfg):
            r = client.get("/api/setup/gateway-access")
        assert r.status_code == 200
        data = r.json()
        assert data["available"] is True
        assert data["url"] == "http://127.0.0.1:18789"
        assert data["token"] == "gw-secret-0123"

    def test_url_honors_configured_port(self, tmp_path):
        cfg = _cfg(tmp_path, {"port": 12345, "auth": {"token": "t"}})
        with patch("ui.server.load_config", return_value=cfg):
            data = client.get("/api/setup/gateway-access").json()
        assert data["url"] == "http://127.0.0.1:12345"

    def test_url_prefers_published_port(self, tmp_path):
        # The container entrypoint seeds gateway_published_port when compose
        # publishes the gateway on a different host port (the dev stack); the
        # link must point where the host browser can reach it, not at the
        # in-container gateway.port.
        cfg = _cfg(tmp_path, {"port": 18789, "auth": {"token": "t"}})
        cfg["gateway_published_port"] = 28789
        with patch("ui.server.load_config", return_value=cfg):
            data = client.get("/api/setup/gateway-access").json()
        assert data["url"] == "http://127.0.0.1:28789"
        assert data["available"] is True

    def test_garbage_published_port_falls_back_to_gateway_port(self, tmp_path):
        cfg = _cfg(tmp_path, {"port": 18789, "auth": {"token": "t"}})
        for garbage in ("not-a-port", None, -1, 0):
            cfg["gateway_published_port"] = garbage
            with patch("ui.server.load_config", return_value=cfg):
                data = client.get("/api/setup/gateway-access").json()
            assert data["url"] == "http://127.0.0.1:18789", garbage

    def test_unavailable_without_token(self, tmp_path):
        cfg = _cfg(tmp_path, {"port": 18789, "auth": {"mode": "token", "token": ""}})
        with patch("ui.server.load_config", return_value=cfg):
            data = client.get("/api/setup/gateway-access").json()
        assert data["available"] is False
        assert data["token"] == ""

    def test_unavailable_when_config_missing(self, tmp_path):
        cfg = {"openclaw_root": str(tmp_path / "nope")}
        with patch("ui.server.load_config", return_value=cfg):
            data = client.get("/api/setup/gateway-access").json()
        assert data["available"] is False

    def test_token_not_logged(self, tmp_path, caplog):
        cfg = _cfg(tmp_path, {"port": 18789, "auth": {"token": "supersecret-xyz-999"}})
        with patch("ui.server.load_config", return_value=cfg):
            with caplog.at_level("DEBUG"):
                client.get("/api/setup/gateway-access")
        assert "supersecret-xyz-999" not in caplog.text
