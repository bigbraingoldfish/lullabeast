"""Hermetic unit tests for autodev/installer/local_models.py.

The shared LOCAL_MODEL_URL wiring contract (v1.0.0 Phase 3, B2/B4). Nothing
here touches the network: every probe is exercised by monkeypatching
urllib.request.urlopen (probe_openai_models) or the module-level probe
(discover_local_servers).
"""

from __future__ import annotations

import json

import pytest

from autodev.installer import local_models


# ── normalize_local_base_url ─────────────────────────────────────────────────

class TestNormalize:
    def test_scheme_less_input_assumes_http(self):
        assert (
            local_models.normalize_local_base_url("host.docker.internal:11434")
            == "http://host.docker.internal:11434/v1"
        )

    def test_trailing_slash_stripped(self):
        assert (
            local_models.normalize_local_base_url("http://localhost:8080/")
            == "http://localhost:8080/v1"
        )

    def test_already_v1_is_idempotent(self):
        assert (
            local_models.normalize_local_base_url("http://localhost:8080/v1")
            == "http://localhost:8080/v1"
        )
        # And a trailing slash on the /v1 form.
        assert (
            local_models.normalize_local_base_url("http://localhost:8080/v1/")
            == "http://localhost:8080/v1"
        )

    def test_https_preserved(self):
        assert (
            local_models.normalize_local_base_url("https://gpu.local:1234")
            == "https://gpu.local:1234/v1"
        )

    def test_bare_v1_appended_once(self):
        assert (
            local_models.normalize_local_base_url("http://host:11434")
            == "http://host:11434/v1"
        )

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            local_models.normalize_local_base_url("")
        with pytest.raises(ValueError):
            local_models.normalize_local_base_url("   ")

    def test_rejects_bad_scheme(self):
        with pytest.raises(ValueError):
            local_models.normalize_local_base_url("ftp://host:11434")

    def test_rejects_no_host(self):
        with pytest.raises(ValueError):
            local_models.normalize_local_base_url("http://")
        with pytest.raises(ValueError):
            local_models.normalize_local_base_url("http:///v1")


# ── probe_openai_models ──────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, raw: bytes):
        self._raw = raw

    def read(self, n=-1):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestProbe:
    def _patch_urlopen(self, monkeypatch, raw=None, exc=None):
        def fake_urlopen(req, timeout=None):
            if exc is not None:
                raise exc
            return _FakeResp(raw)

        monkeypatch.setattr(local_models.urllib.request, "urlopen", fake_urlopen)

    def test_happy_shape(self, monkeypatch):
        payload = json.dumps(
            {"data": [{"id": "qwen3.5-27b"}, {"id": "llama3"}]}
        ).encode()
        self._patch_urlopen(monkeypatch, raw=payload)
        assert local_models.probe_openai_models("http://h:11434/v1") == [
            "qwen3.5-27b",
            "llama3",
        ]

    def test_non_json_returns_none(self, monkeypatch):
        self._patch_urlopen(monkeypatch, raw=b"<html>not json</html>")
        assert local_models.probe_openai_models("http://h:11434/v1") is None

    def test_missing_data_returns_none(self, monkeypatch):
        self._patch_urlopen(monkeypatch, raw=json.dumps({"models": []}).encode())
        assert local_models.probe_openai_models("http://h:11434/v1") is None

    def test_non_dict_payload_returns_none(self, monkeypatch):
        self._patch_urlopen(monkeypatch, raw=json.dumps([1, 2, 3]).encode())
        assert local_models.probe_openai_models("http://h:11434/v1") is None

    def test_connection_error_returns_none(self, monkeypatch):
        import urllib.error

        self._patch_urlopen(
            monkeypatch, exc=urllib.error.URLError("connection refused")
        )
        assert local_models.probe_openai_models("http://h:11434/v1") is None

    def test_os_error_returns_none(self, monkeypatch):
        self._patch_urlopen(monkeypatch, exc=OSError("boom"))
        assert local_models.probe_openai_models("http://h:11434/v1") is None

    def test_zero_models_returns_empty_list(self, monkeypatch):
        # An answering server with nothing loaded is usable: [] not None.
        self._patch_urlopen(monkeypatch, raw=json.dumps({"data": []}).encode())
        assert local_models.probe_openai_models("http://h:11434/v1") == []

    def test_malformed_entries_filtered(self, monkeypatch):
        payload = json.dumps(
            {"data": [{"id": "ok"}, {"id": ""}, {"id": "  "}, {"noid": 1}, "str", 5]}
        ).encode()
        self._patch_urlopen(monkeypatch, raw=payload)
        assert local_models.probe_openai_models("http://h:11434/v1") == ["ok"]


# ── discover_local_servers ───────────────────────────────────────────────────

class TestDiscover:
    def test_only_answering_ports_returned(self, monkeypatch):
        # Only Ollama's port answers; the other two probe to None.
        def fake_probe(base_url, timeout=None):
            if ":11434/" in base_url:
                return ["mistral"]
            return None

        monkeypatch.setattr(local_models, "probe_openai_models", fake_probe)
        found = local_models.discover_local_servers(host="host.docker.internal")
        assert len(found) == 1
        hit = found[0]
        assert hit["name"] == "Ollama"
        assert hit["url"] == "http://host.docker.internal:11434"
        assert hit["base_url"] == "http://host.docker.internal:11434/v1"
        assert hit["models"] == ["mistral"]

    def test_none_answering_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            local_models, "probe_openai_models", lambda base_url, timeout=None: None
        )
        assert local_models.discover_local_servers() == []

    def test_answering_with_zero_models_is_a_hit(self, monkeypatch):
        # [] is a usable server (nothing loaded yet), so it must be returned.
        monkeypatch.setattr(
            local_models, "probe_openai_models", lambda base_url, timeout=None: []
        )
        found = local_models.discover_local_servers()
        # All three known servers "answer" with empty model lists.
        assert len(found) == len(local_models.KNOWN_LOCAL_SERVERS)
        assert all(h["models"] == [] for h in found)


# ── build_local_provider_entry ───────────────────────────────────────────────

class TestBuildEntry:
    def test_forces_no_key_and_normalizes(self):
        entry = local_models.build_local_provider_entry(
            "host.docker.internal:11434", ["a", "b"]
        )
        assert entry["apiKey"] == "no-key"
        assert entry["baseUrl"] == "http://host.docker.internal:11434/v1"
        assert entry["api"] == "openai-completions"

    def test_model_entries_shape(self):
        entry = local_models.build_local_provider_entry(
            "http://h:8080/v1", ["qwen", "llama"]
        )
        assert entry["models"] == [
            {"id": "qwen", "name": "qwen"},
            {"id": "llama", "name": "llama"},
        ]

    def test_empty_and_non_string_ids_filtered(self):
        entry = local_models.build_local_provider_entry(
            "http://h:8080/v1", ["ok", "", "  ", None, 5]
        )
        assert entry["models"] == [{"id": "ok", "name": "ok"}]

    def test_none_ids_gives_empty_models(self):
        entry = local_models.build_local_provider_entry("http://h:8080/v1", None)
        assert entry["models"] == []


# ── merge_local_provider ─────────────────────────────────────────────────────

class TestMerge:
    def _entry(self, ids=("m1",)):
        return local_models.build_local_provider_entry("http://h:11434/v1", list(ids))

    def test_creates_providers_path(self):
        result = local_models.merge_local_provider({}, self._entry())
        assert (
            result["models"]["providers"]["local"]["baseUrl"]
            == "http://h:11434/v1"
        )

    def test_preserves_unrelated_providers_and_keys(self):
        config = {
            "gateway": {"port": 18789},
            "models": {
                "pricing": {"enabled": True},
                "providers": {
                    "openrouter": {"apiKey": "${OPENROUTER_API_KEY}"},
                },
            },
        }
        result = local_models.merge_local_provider(config, self._entry())
        assert result["gateway"] == {"port": 18789}
        assert result["models"]["pricing"] == {"enabled": True}
        assert result["models"]["providers"]["openrouter"] == {
            "apiKey": "${OPENROUTER_API_KEY}"
        }
        assert "local" in result["models"]["providers"]

    def test_replaces_prior_local_entry(self):
        config = {
            "models": {
                "providers": {
                    "local": {"baseUrl": "http://old:1/v1", "models": [{"id": "old"}]}
                }
            }
        }
        result = local_models.merge_local_provider(
            config, self._entry(ids=("new",))
        )
        local = result["models"]["providers"]["local"]
        assert local["baseUrl"] == "http://h:11434/v1"
        assert local["models"] == [{"id": "new", "name": "new"}]

    def test_does_not_mutate_input(self):
        config = {"models": {"providers": {"openrouter": {"apiKey": "x"}}}}
        before = json.dumps(config, sort_keys=True)
        local_models.merge_local_provider(config, self._entry())
        assert json.dumps(config, sort_keys=True) == before

    def test_forces_no_key_even_if_entry_lies(self):
        bad_entry = {
            "baseUrl": "http://h:11434/v1",
            "api": "openai-completions",
            "apiKey": "sk-should-be-ignored",
            "models": [],
        }
        result = local_models.merge_local_provider({}, bad_entry)
        assert result["models"]["providers"]["local"]["apiKey"] == "no-key"

    def test_non_dict_config_degrades_to_empty(self):
        # Defensive: a malformed config still yields a valid providers path.
        result = local_models.merge_local_provider(None, self._entry())
        assert "local" in result["models"]["providers"]
