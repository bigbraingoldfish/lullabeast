"""Tests for the first-run local-model setup endpoints (v1.0.0 Phase 3 B4).

Covers GET /api/setup/local-models (discovery probe), POST /api/setup/local-model
(merge-wire a local server into the provider key file), the local_configured flag
on GET /api/setup/provider-status, and the static index.html markers.

Same cross-agent contract as the A2 key surfaces: the server's only unlock
responsibility is writing the dotenv file correctly (LOCAL_MODEL_URL + the six
*_MODEL knobs); setup mode itself is entrypoint-owned. Every surface degrades to
"unsupported" when the config keys are unset, and no key material is ever logged.
"""
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ui.server import app
from autodev.installer.openclaw_template import TEMPLATE_MODEL_DEFAULTS

client = TestClient(app)

# The six role knobs the endpoint writes, from the single source of truth.
_KNOB_NAMES = tuple(TEMPLATE_MODEL_DEFAULTS.keys())


def _cfg(tmp_path: Path, *, with_key_path=True, probe_host=None) -> dict:
    cfg = {"autodev_repo_path": str(tmp_path / "repo")}
    if with_key_path:
        cfg["provider_key_path"] = str(tmp_path / "secrets" / "provider.env")
    if probe_host is not None:
        cfg["local_model_probe_host"] = probe_host
    return cfg


# ── local-models discovery ───────────────────────────────────────────────────

class TestLocalModelsDiscovery:
    def test_unsupported_when_no_probe_host(self, tmp_path):
        cfg = _cfg(tmp_path, probe_host=None)  # local_model_probe_host unset
        with patch("ui.server.load_config", return_value=cfg):
            r = client.get("/api/setup/local-models")
        assert r.status_code == 200
        data = r.json()
        assert data["supported"] is False
        assert data["servers"] == []
        assert data["hint"] is None

    def test_unsupported_when_probe_host_blank(self, tmp_path):
        cfg = _cfg(tmp_path, probe_host="   ")
        with patch("ui.server.load_config", return_value=cfg):
            r = client.get("/api/setup/local-models")
        assert r.json()["supported"] is False

    def test_supported_passthrough_drops_base_url(self, tmp_path):
        cfg = _cfg(tmp_path, probe_host="host.docker.internal")
        discovered = [
            {
                "name": "Ollama",
                "url": "http://host.docker.internal:11434",
                "base_url": "http://host.docker.internal:11434/v1",
                "models": ["llama3:8b", "qwen2.5:14b"],
            },
        ]
        with patch("ui.server.load_config", return_value=cfg), patch(
            "ui.server._local_models.discover_local_servers", return_value=discovered
        ) as probe:
            r = client.get("/api/setup/local-models")
        assert r.status_code == 200
        data = r.json()
        assert data["supported"] is True
        assert data["hint"] is None
        assert len(data["servers"]) == 1
        srv = data["servers"][0]
        # Only name/url/models pass through; base_url is dropped.
        assert srv == {
            "name": "Ollama",
            "url": "http://host.docker.internal:11434",
            "models": ["llama3:8b", "qwen2.5:14b"],
        }
        assert "base_url" not in srv
        # The host from config is what gets probed.
        probe.assert_called_once()
        assert probe.call_args.args[0] == "host.docker.internal"

    def test_supported_no_servers_returns_hint(self, tmp_path):
        cfg = _cfg(tmp_path, probe_host="host.docker.internal")
        with patch("ui.server.load_config", return_value=cfg), patch(
            "ui.server._local_models.discover_local_servers", return_value=[]
        ):
            r = client.get("/api/setup/local-models")
        data = r.json()
        assert data["supported"] is True
        assert data["servers"] == []
        assert isinstance(data["hint"], str) and data["hint"].strip()
        # The hint names the three probed servers/ports.
        for token in ("Ollama", "11434", "llama.cpp", "8080", "LM Studio", "1234"):
            assert token in data["hint"]


# ── local-model wiring: unsupported / validation ─────────────────────────────

class TestLocalModelUnsupported:
    def test_409_when_key_path_unconfigured(self, tmp_path):
        cfg = _cfg(tmp_path, with_key_path=False)
        with patch("ui.server.load_config", return_value=cfg):
            r = client.post(
                "/api/setup/local-model",
                json={"url": "http://host.docker.internal:11434", "model": "llama3:8b"},
            )
        assert r.status_code == 409


class TestLocalModelValidation:
    def _post(self, cfg, body):
        with patch("ui.server.load_config", return_value=cfg):
            return client.post("/api/setup/local-model", json=body)

    def test_400_bad_url(self, tmp_path):
        cfg = _cfg(tmp_path)
        # ftp scheme -> normalize_local_base_url raises ValueError.
        r = self._post(cfg, {"url": "ftp://host:1", "model": "llama3:8b"})
        assert r.status_code == 400
        assert not os.path.exists(cfg["provider_key_path"])

    def test_400_empty_url(self, tmp_path):
        cfg = _cfg(tmp_path)
        r = self._post(cfg, {"url": "   ", "model": "llama3:8b"})
        assert r.status_code == 400

    def test_400_non_string_url(self, tmp_path):
        cfg = _cfg(tmp_path)
        r = self._post(cfg, {"url": 123, "model": "llama3:8b"})
        assert r.status_code == 400

    def test_400_empty_model(self, tmp_path):
        cfg = _cfg(tmp_path)
        r = self._post(cfg, {"url": "http://host:11434", "model": "   "})
        assert r.status_code == 400
        assert not os.path.exists(cfg["provider_key_path"])

    def test_400_whitespace_model(self, tmp_path):
        cfg = _cfg(tmp_path)
        r = self._post(cfg, {"url": "http://host:11434", "model": "llama 3"})
        assert r.status_code == 400

    def test_400_non_string_model(self, tmp_path):
        cfg = _cfg(tmp_path)
        r = self._post(cfg, {"url": "http://host:11434", "model": 42})
        assert r.status_code == 400

    def test_400_model_too_long(self, tmp_path):
        cfg = _cfg(tmp_path)
        r = self._post(cfg, {"url": "http://host:11434", "model": "a" * 257})
        assert r.status_code == 400

    def test_slash_and_colon_model_ids_accepted(self, tmp_path):
        cfg = _cfg(tmp_path)
        # Ollama "name:tag" and OpenRouter "vendor/model" ids are legal.
        for mid in ("llama3:8b", "vendor/model-name"):
            r = self._post(
                cfg, {"url": "http://host.docker.internal:11434", "model": mid}
            )
            assert r.status_code == 200

    def test_400_url_with_newline_injection(self, tmp_path):
        # provider_key_path is `source`d by the container entrypoint, so a URL
        # carrying a newline would inject an arbitrary dotenv line (env var) or a
        # `$(...)` line the shell evaluates. normalize_local_base_url does not
        # reject internal newlines, so the endpoint must. Nothing is written.
        cfg = _cfg(tmp_path)
        r = self._post(
            cfg,
            {"url": "http://host\nPWNED=$(id)", "model": "llama3:8b"},
        )
        assert r.status_code == 400
        assert not os.path.exists(cfg["provider_key_path"])

    def test_400_url_with_internal_space(self, tmp_path):
        cfg = _cfg(tmp_path)
        r = self._post(
            cfg,
            {"url": "http://host /v1", "model": "llama3:8b"},
        )
        assert r.status_code == 400
        assert not os.path.exists(cfg["provider_key_path"])

    def test_400_url_non_printable(self, tmp_path):
        cfg = _cfg(tmp_path)
        r = self._post(
            cfg,
            {"url": "http://host\x00:11434", "model": "llama3:8b"},
        )
        assert r.status_code == 400
        assert not os.path.exists(cfg["provider_key_path"])

    def test_400_url_command_substitution(self, tmp_path):
        # A whitespace-free `$(...)`/backtick still command-substitutes when the
        # file is `source`d; the $/backtick guard must reject it.
        cfg = _cfg(tmp_path)
        for bad in ("http://host$(id)", "http://host`id`"):
            r = self._post(cfg, {"url": bad, "model": "llama3:8b"})
            assert r.status_code == 400, bad
            assert not os.path.exists(cfg["provider_key_path"])

    def test_400_model_command_substitution(self, tmp_path):
        # The model is written as `<KNOB>=local/<model>`; a $/backtick there is a
        # command substitution on source just like the url/key fields.
        cfg = _cfg(tmp_path)
        for bad in ("x$(id)", "x`id`"):
            r = self._post(
                cfg, {"url": "http://host:11434", "model": bad}
            )
            assert r.status_code == 400, bad
            assert not os.path.exists(cfg["provider_key_path"])


# ── local-model wiring: success + merge ──────────────────────────────────────

class TestLocalModelSuccess:
    def test_happy_path_writes_url_and_six_knobs(self, tmp_path):
        cfg = _cfg(tmp_path)
        url = "http://host.docker.internal:11434"
        with patch("ui.server.load_config", return_value=cfg):
            r = client.post(
                "/api/setup/local-model",
                json={"url": url, "model": "llama3:8b"},
            )
        assert r.status_code == 200
        assert r.json() == {"ok": True, "restarting": True}
        content = Path(cfg["provider_key_path"]).read_text()
        # LOCAL_MODEL_URL carries the client URL verbatim (not the /v1 form).
        assert f"LOCAL_MODEL_URL={url}\n" in content
        assert "/v1" not in content
        # All six role knobs assign local/<model>.
        for knob in _KNOB_NAMES:
            assert f"{knob}=local/llama3:8b" in content
        assert len(_KNOB_NAMES) == 6

    def test_file_mode_is_0600(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch("ui.server.load_config", return_value=cfg):
            client.post(
                "/api/setup/local-model",
                json={"url": "http://host:11434", "model": "llama3:8b"},
            )
        mode = stat.S_IMODE(os.stat(cfg["provider_key_path"]).st_mode)
        assert mode == 0o600

    def test_parent_dir_created(self, tmp_path):
        cfg = _cfg(tmp_path)
        assert not os.path.exists(os.path.dirname(cfg["provider_key_path"]))
        with patch("ui.server.load_config", return_value=cfg):
            client.post(
                "/api/setup/local-model",
                json={"url": "http://host:11434", "model": "llama3:8b"},
            )
        assert os.path.isdir(os.path.dirname(cfg["provider_key_path"]))

    def test_merge_preserves_cloud_key_and_replaces_stale_knob(self, tmp_path):
        cfg = _cfg(tmp_path)
        p = Path(cfg["provider_key_path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        # A mixed install: a cloud key plus a stale PLANNER_MODEL line.
        p.write_text(
            "OPENROUTER_API_KEY=abc\n"
            "PLANNER_MODEL=openrouter/stale-model\n"
        )
        with patch("ui.server.load_config", return_value=cfg):
            r = client.post(
                "/api/setup/local-model",
                json={"url": "http://host:11434", "model": "llama3:8b"},
            )
        assert r.status_code == 200
        content = p.read_text()
        lines = content.splitlines()
        # Cloud key preserved verbatim.
        assert "OPENROUTER_API_KEY=abc" in lines
        # Stale knob replaced, not duplicated.
        assert "PLANNER_MODEL=openrouter/stale-model" not in lines
        assert lines.count("PLANNER_MODEL=local/llama3:8b") == 1
        # No duplicate LOCAL_MODEL_URL lines.
        assert sum(1 for l in lines if l.startswith("LOCAL_MODEL_URL=")) == 1

    def test_url_absent_is_not_a_secret_but_key_content_not_echoed(self, tmp_path):
        cfg = _cfg(tmp_path)
        p = Path(cfg["provider_key_path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("OPENROUTER_API_KEY=sk-or-topsecret-xyz\n")
        with patch("ui.server.load_config", return_value=cfg):
            r = client.post(
                "/api/setup/local-model",
                json={"url": "http://host:11434", "model": "llama3:8b"},
            )
        # Preserved cloud key value never appears in the response.
        assert "sk-or-topsecret-xyz" not in r.text


# ── provider-status: local_configured ────────────────────────────────────────

class TestLocalConfigured:
    def test_local_configured_false_by_default(self, tmp_path, monkeypatch):
        for v in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "LOCAL_MODEL_URL"):
            monkeypatch.delenv(v, raising=False)
        cfg = _cfg(tmp_path)
        with patch("ui.server.load_config", return_value=cfg):
            r = client.get("/api/setup/provider-status")
        assert r.json()["local_configured"] is False

    def test_local_configured_via_env(self, tmp_path, monkeypatch):
        for v in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("LOCAL_MODEL_URL", "http://host:11434")
        cfg = _cfg(tmp_path)
        with patch("ui.server.load_config", return_value=cfg):
            r = client.get("/api/setup/provider-status")
        assert r.json()["local_configured"] is True

    def test_local_configured_via_file(self, tmp_path, monkeypatch):
        for v in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "LOCAL_MODEL_URL"):
            monkeypatch.delenv(v, raising=False)
        cfg = _cfg(tmp_path)
        p = Path(cfg["provider_key_path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("LOCAL_MODEL_URL=http://host:11434\nPLANNER_MODEL=local/llama3\n")
        with patch("ui.server.load_config", return_value=cfg):
            r = client.get("/api/setup/provider-status")
        assert r.json()["local_configured"] is True

    def test_local_configured_false_when_file_line_empty(self, tmp_path, monkeypatch):
        for v in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "LOCAL_MODEL_URL"):
            monkeypatch.delenv(v, raising=False)
        cfg = _cfg(tmp_path)
        p = Path(cfg["provider_key_path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        # Present-but-empty value must not count as configured.
        p.write_text("LOCAL_MODEL_URL=\n")
        with patch("ui.server.load_config", return_value=cfg):
            r = client.get("/api/setup/provider-status")
        assert r.json()["local_configured"] is False


# ── static index.html markers ────────────────────────────────────────────────

class TestLocalModelUiMarkers:
    def _html(self):
        with open("ui/index.html", "r") as f:
            return f.read()

    def test_endpoints_present(self):
        html = self._html()
        assert "/api/setup/local-models" in html
        assert "/api/setup/local-model" in html

    def test_use_this_model_button_present(self):
        html = self._html()
        assert "Use this model for all roles" in html

    def test_local_models_surface_testid(self):
        html = self._html()
        assert 'data-testid="setup-local-models"' in html
