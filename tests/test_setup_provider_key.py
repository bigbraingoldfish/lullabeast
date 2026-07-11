"""Tests for the first-run provider-key setup endpoints (A2).

Covers GET /api/setup/provider-status and POST /api/setup/provider-key. The
cross-agent contract: the server's only unlock responsibility is writing the
dotenv key file correctly; setup mode itself is entrypoint-owned via a marker
file. Every surface degrades to "unsupported" when the config keys are unset.
"""
import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ui.server import app

client = TestClient(app)


def _cfg(tmp_path: Path, *, with_key_path=True, with_marker=True, repo=None) -> dict:
    cfg = {
        "autodev_repo_path": str(repo) if repo else str(tmp_path / "repo"),
    }
    if with_key_path:
        cfg["provider_key_path"] = str(tmp_path / "secrets" / "provider.env")
    if with_marker:
        cfg["setup_marker_path"] = str(tmp_path / ".setup-mode")
    return cfg


# ── provider-status ──────────────────────────────────────────────────────────

class TestProviderStatus:
    def test_unsupported_when_unconfigured(self, tmp_path, monkeypatch):
        for v in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(v, raising=False)
        cfg = _cfg(tmp_path, with_key_path=False, with_marker=False)
        with patch("ui.server.load_config", return_value=cfg):
            r = client.get("/api/setup/provider-status")
        assert r.status_code == 200
        data = r.json()
        assert data["supported"] is False
        assert data["setup_mode"] is False
        assert data["key_present"] is False

    def test_setup_mode_true_when_marker_present(self, tmp_path, monkeypatch):
        for v in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(v, raising=False)
        cfg = _cfg(tmp_path)
        Path(cfg["setup_marker_path"]).write_text("")
        with patch("ui.server.load_config", return_value=cfg):
            r = client.get("/api/setup/provider-status")
        data = r.json()
        assert data["supported"] is True
        assert data["setup_mode"] is True

    def test_setup_mode_false_when_marker_absent(self, tmp_path, monkeypatch):
        for v in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(v, raising=False)
        cfg = _cfg(tmp_path)
        # marker file not created
        with patch("ui.server.load_config", return_value=cfg):
            r = client.get("/api/setup/provider-status")
        assert r.json()["setup_mode"] is False

    def test_key_present_via_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-something")
        cfg = _cfg(tmp_path)
        with patch("ui.server.load_config", return_value=cfg):
            r = client.get("/api/setup/provider-status")
        assert r.json()["key_present"] is True

    def test_key_present_via_file(self, tmp_path, monkeypatch):
        for v in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(v, raising=False)
        cfg = _cfg(tmp_path)
        p = Path(cfg["provider_key_path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("OPENROUTER_API_KEY=abc123456\n")
        with patch("ui.server.load_config", return_value=cfg):
            r = client.get("/api/setup/provider-status")
        assert r.json()["key_present"] is True

    def test_key_present_false_when_file_empty(self, tmp_path, monkeypatch):
        for v in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(v, raising=False)
        cfg = _cfg(tmp_path)
        p = Path(cfg["provider_key_path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
        with patch("ui.server.load_config", return_value=cfg):
            r = client.get("/api/setup/provider-status")
        assert r.json()["key_present"] is False

    def test_applying_reflects_marker_presence(self, tmp_path, monkeypatch):
        # The Settings model card polls this to distinguish "apply queued" from
        # idle; the entrypoint removes the marker when its pass starts.
        for v in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(v, raising=False)
        cfg = _cfg(tmp_path)
        with patch("ui.server.load_config", return_value=cfg):
            assert client.get("/api/setup/provider-status").json()["applying"] is False

        cfg["apply_request_path"] = str(tmp_path / "secrets" / "apply.request")
        with patch("ui.server.load_config", return_value=cfg):
            assert client.get("/api/setup/provider-status").json()["applying"] is False

        marker = Path(cfg["apply_request_path"])
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("")
        with patch("ui.server.load_config", return_value=cfg):
            assert client.get("/api/setup/provider-status").json()["applying"] is True


# ── provider-key ─────────────────────────────────────────────────────────────

class TestProviderKeyUnsupported:
    def test_409_when_key_path_unconfigured(self, tmp_path):
        cfg = _cfg(tmp_path, with_key_path=False)
        with patch("ui.server.load_config", return_value=cfg):
            r = client.post(
                "/api/setup/provider-key",
                json={"provider": "openrouter", "key": "abc123456"},
            )
        assert r.status_code == 409


class TestProviderKeyValidation:
    def _post(self, cfg, body):
        with patch("ui.server.load_config", return_value=cfg):
            return client.post("/api/setup/provider-key", json=body)

    def test_400_unknown_provider(self, tmp_path):
        cfg = _cfg(tmp_path)
        r = self._post(cfg, {"provider": "cohere", "key": "abc123456"})
        assert r.status_code == 400

    def test_400_empty_key(self, tmp_path):
        cfg = _cfg(tmp_path)
        r = self._post(cfg, {"provider": "openrouter", "key": "   "})
        assert r.status_code == 400

    def test_400_key_with_internal_whitespace(self, tmp_path):
        cfg = _cfg(tmp_path)
        r = self._post(cfg, {"provider": "openrouter", "key": "abc 12345"})
        assert r.status_code == 400

    def test_400_key_with_newline_injection(self, tmp_path):
        cfg = _cfg(tmp_path)
        r = self._post(
            cfg,
            {"provider": "openrouter", "key": "abc123456\nANTHROPIC_API_KEY=evil"},
        )
        assert r.status_code == 400
        # Nothing written on rejection.
        assert not os.path.exists(cfg["provider_key_path"])

    def test_400_key_too_short(self, tmp_path):
        cfg = _cfg(tmp_path)
        r = self._post(cfg, {"provider": "openrouter", "key": "short"})
        assert r.status_code == 400

    def test_400_key_too_long(self, tmp_path):
        cfg = _cfg(tmp_path)
        r = self._post(cfg, {"provider": "openrouter", "key": "a" * 513})
        assert r.status_code == 400

    def test_400_key_non_printable(self, tmp_path):
        cfg = _cfg(tmp_path)
        r = self._post(cfg, {"provider": "openrouter", "key": "abc\x00defgh"})
        assert r.status_code == 400

    def test_400_key_with_command_substitution(self, tmp_path):
        # provider_key_path is `source`d by the entrypoint, so `$(...)` / backtick
        # in a single (whitespace-free) value command-substitutes on load. The
        # whitespace/newline checks alone do not catch these; the $/backtick guard
        # must. Nothing is written on rejection.
        cfg = _cfg(tmp_path)
        for bad in ("abc$(id)xyz", "abc`id`xyz", "sk-${HOME}"):
            r = self._post(cfg, {"provider": "openrouter", "key": bad})
            assert r.status_code == 400, bad
            assert not os.path.exists(cfg["provider_key_path"])


class TestProviderKeySuccess:
    def test_writes_exact_dotenv_line_openrouter(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch("ui.server.load_config", return_value=cfg):
            r = client.post(
                "/api/setup/provider-key",
                json={"provider": "openrouter", "key": "sk-or-abcdefg"},
            )
        assert r.status_code == 200
        assert r.json() == {"ok": True, "restarting": True}
        content = Path(cfg["provider_key_path"]).read_text()
        assert content == "OPENROUTER_API_KEY=sk-or-abcdefg\n"

    def test_anthropic_provider_rejected(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch("ui.server.load_config", return_value=cfg):
            r = client.post(
                "/api/setup/provider-key",
                json={"provider": "anthropic", "key": "sk-ant-abcdefg"},
            )
        assert r.status_code == 400
        assert not os.path.exists(cfg["provider_key_path"])

    def test_file_mode_is_0600(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch("ui.server.load_config", return_value=cfg):
            client.post(
                "/api/setup/provider-key",
                json={"provider": "openrouter", "key": "sk-or-abcdefg"},
            )
        mode = stat.S_IMODE(os.stat(cfg["provider_key_path"]).st_mode)
        assert mode == 0o600

    def test_parent_dir_created(self, tmp_path):
        cfg = _cfg(tmp_path)
        assert not os.path.exists(os.path.dirname(cfg["provider_key_path"]))
        with patch("ui.server.load_config", return_value=cfg):
            client.post(
                "/api/setup/provider-key",
                json={"provider": "openrouter", "key": "sk-or-abcdefg"},
            )
        assert os.path.isdir(os.path.dirname(cfg["provider_key_path"]))

    def test_atomic_write_leaves_no_temp_files(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch("ui.server.load_config", return_value=cfg):
            client.post(
                "/api/setup/provider-key",
                json={"provider": "openrouter", "key": "sk-or-abcdefg"},
            )
        secrets_dir = os.path.dirname(cfg["provider_key_path"])
        entries = os.listdir(secrets_dir)
        assert entries == ["provider.env"]

    def test_key_value_absent_from_response(self, tmp_path):
        cfg = _cfg(tmp_path)
        secret = "sk-or-topsecret-value-xyz"
        with patch("ui.server.load_config", return_value=cfg):
            r = client.post(
                "/api/setup/provider-key",
                json={"provider": "openrouter", "key": secret},
            )
        assert secret not in r.text

    def test_resubmission_overwrites(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch("ui.server.load_config", return_value=cfg):
            client.post(
                "/api/setup/provider-key",
                json={"provider": "openrouter", "key": "sk-or-first000"},
            )
            client.post(
                "/api/setup/provider-key",
                json={"provider": "openrouter", "key": "sk-or-second00"},
            )
        content = Path(cfg["provider_key_path"]).read_text()
        assert content == "OPENROUTER_API_KEY=sk-or-second00\n"


# ── provider-key with per-role customization (Stage D) ───────────────────────

def _oc_root(tmp_path: Path) -> str:
    """A minimal rendered openclaw.json: one multimodal and one text-only model."""
    oc_root = tmp_path / "openclaw"
    oc_root.mkdir(parents=True, exist_ok=True)
    (oc_root / "openclaw.json").write_text(
        json.dumps(
            {
                "models": {
                    "providers": {
                        "openrouter": {
                            "models": [
                                {"id": "moonshotai/kimi-k2.7-code", "input": ["text", "image"]},
                                {"id": "z-ai/glm-5.2", "input": ["text"]},
                            ]
                        }
                    }
                },
                "agents": {"list": []},
            }
        )
    )
    return str(oc_root)


class TestProviderKeyRoleCustomization:
    """The optional roles map rides the key write: one file pass, one restart."""

    def _post(self, cfg, body):
        with patch("ui.server.load_config", return_value=cfg):
            return client.post("/api/setup/provider-key", json=body)

    def test_default_write_is_a_single_key_line(self, tmp_path):
        # Regression pin: without roles, the write is byte-identical to before.
        cfg = _cfg(tmp_path)
        r = self._post(cfg, {"provider": "openrouter", "key": "abc123456"})
        assert r.status_code == 200
        assert Path(cfg["provider_key_path"]).read_text() == "OPENROUTER_API_KEY=abc123456\n"

    def test_empty_roles_object_behaves_like_absent(self, tmp_path):
        cfg = _cfg(tmp_path)
        r = self._post(cfg, {"provider": "openrouter", "key": "abc123456", "roles": {}})
        assert r.status_code == 200
        assert Path(cfg["provider_key_path"]).read_text() == "OPENROUTER_API_KEY=abc123456\n"

    def test_roles_written_with_key_in_one_pass(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg["openclaw_root"] = _oc_root(tmp_path)
        r = self._post(
            cfg,
            {
                "provider": "openrouter",
                "key": "abc123456",
                "roles": {
                    "planner": "openrouter/z-ai/glm-5.2",
                    "executor": "openrouter/moonshotai/kimi-k2.7-code",
                },
            },
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True, "restarting": True}
        lines = Path(cfg["provider_key_path"]).read_text().splitlines()
        assert lines[0] == "OPENROUTER_API_KEY=abc123456"
        assert "PLANNER_MODEL=openrouter/z-ai/glm-5.2" in lines
        assert "EXECUTOR_MODEL=openrouter/moonshotai/kimi-k2.7-code" in lines
        # Only the key and the two changed knobs: partial map, partial write.
        assert len(lines) == 3

    def test_roles_must_be_an_object(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg["openclaw_root"] = _oc_root(tmp_path)
        r = self._post(
            cfg, {"provider": "openrouter", "key": "abc123456", "roles": ["planner"]}
        )
        assert r.status_code == 400
        assert not os.path.exists(cfg["provider_key_path"])

    def test_unknown_role_rejected(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg["openclaw_root"] = _oc_root(tmp_path)
        r = self._post(
            cfg,
            {
                "provider": "openrouter",
                "key": "abc123456",
                "roles": {"conductor": "openrouter/z-ai/glm-5.2"},
            },
        )
        assert r.status_code == 400
        assert "unknown role" in r.json()["detail"]
        assert not os.path.exists(cfg["provider_key_path"])

    def test_unregistered_model_rejected(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg["openclaw_root"] = _oc_root(tmp_path)
        r = self._post(
            cfg,
            {
                "provider": "openrouter",
                "key": "abc123456",
                "roles": {"planner": "openrouter/not-shipped"},
            },
        )
        assert r.status_code == 400
        assert "not a registered model" in r.json()["detail"]
        assert not os.path.exists(cfg["provider_key_path"])

    @pytest.mark.parametrize("role", ("executor", "reviewer", "prd-creator"))
    def test_text_only_model_rejected_for_vision_roles(self, tmp_path, role):
        cfg = _cfg(tmp_path)
        cfg["openclaw_root"] = _oc_root(tmp_path)
        r = self._post(
            cfg,
            {
                "provider": "openrouter",
                "key": "abc123456",
                "roles": {role: "openrouter/z-ai/glm-5.2"},
            },
        )
        assert r.status_code == 400
        assert "image input" in r.json()["detail"]
        assert not os.path.exists(cfg["provider_key_path"])

    def test_roles_without_catalog_is_503(self, tmp_path):
        # openclaw.json unreadable: role choices cannot be validated, and the
        # key is not written either (the user retries or saves without roles).
        cfg = _cfg(tmp_path)
        cfg["openclaw_root"] = str(tmp_path / "missing")
        r = self._post(
            cfg,
            {
                "provider": "openrouter",
                "key": "abc123456",
                "roles": {"planner": "openrouter/z-ai/glm-5.2"},
            },
        )
        assert r.status_code == 503
        assert not os.path.exists(cfg["provider_key_path"])
