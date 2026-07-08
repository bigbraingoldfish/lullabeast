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


# ── probe_model_metadata ─────────────────────────────────────────────────────

class TestMetadataProbe:
    """Family endpoints are faked by routing urlopen on the request URL; every
    unrouted URL raises URLError (that family's server is 'absent')."""

    def _route(self, monkeypatch, routes):
        import urllib.error

        def fake_urlopen(req, timeout=None):
            url = req.full_url
            for suffix, payload in routes.items():
                if url.endswith(suffix):
                    body = payload(req) if callable(payload) else payload
                    return _FakeResp(json.dumps(body).encode())
            raise urllib.error.URLError("no route")

        monkeypatch.setattr(local_models.urllib.request, "urlopen", fake_urlopen)

    def test_lmstudio_context_and_vision(self, monkeypatch):
        self._route(monkeypatch, {
            "/api/v0/models": {
                "data": [
                    {"id": "qwen", "type": "llm", "max_context_length": 32768},
                    {"id": "llava", "type": "vlm", "max_context_length": 8192},
                ]
            },
        })
        md = local_models.probe_model_metadata("http://h:1234/v1", ["qwen", "llava"])
        assert md["qwen"] == {"context_window": 32768, "reasoning": None, "vision": False}
        assert md["llava"] == {"context_window": 8192, "reasoning": None, "vision": True}

    def test_ollama_capabilities_and_context(self, monkeypatch):
        def show(req):
            model = json.loads(req.data.decode())["model"]
            if model == "qwen":
                return {
                    "capabilities": ["completion", "tools", "thinking"],
                    "model_info": {"qwen2.context_length": 131072},
                }
            return {"capabilities": ["completion", "vision"], "model_info": {}}

        self._route(monkeypatch, {"/api/version": {"version": "0.9.0"}, "/api/show": show})
        md = local_models.probe_model_metadata("http://h:11434/v1", ["qwen", "llava"])
        assert md["qwen"] == {"context_window": 131072, "reasoning": True, "vision": False}
        # Capabilities present without "thinking"/"vision" are definitive negatives.
        assert md["llava"] == {"context_window": None, "reasoning": False, "vision": True}

    def test_llamacpp_props_apply_to_all_models(self, monkeypatch):
        self._route(monkeypatch, {
            "/props": {"default_generation_settings": {"n_ctx": 32768}},
        })
        md = local_models.probe_model_metadata("http://h:8080/v1", ["m1", "m2"])
        assert md["m1"] == {"context_window": 32768, "reasoning": None, "vision": None}
        assert md["m2"] == md["m1"]

    def test_unrecognized_server_returns_empty(self, monkeypatch):
        self._route(monkeypatch, {})
        assert local_models.probe_model_metadata("http://h:9999/v1", ["m"]) == {}

    def test_no_models_short_circuits(self, monkeypatch):
        # No network call at all for an empty id list.
        def boom(req, timeout=None):
            raise AssertionError("must not probe")

        monkeypatch.setattr(local_models.urllib.request, "urlopen", boom)
        assert local_models.probe_model_metadata("http://h:8080/v1", []) == {}

    def test_ollama_per_model_probe_is_bounded(self, monkeypatch):
        calls = []

        def show(req):
            calls.append(json.loads(req.data.decode())["model"])
            return {"capabilities": ["completion"], "model_info": {}}

        self._route(monkeypatch, {"/api/version": {"version": "0.9.0"}, "/api/show": show})
        ids = [f"m{i}" for i in range(local_models.METADATA_PROBE_MAX_MODELS + 5)]
        md = local_models.probe_model_metadata("http://h:11434/v1", ids)
        assert len(calls) == local_models.METADATA_PROBE_MAX_MODELS
        assert set(md) == set(ids[: local_models.METADATA_PROBE_MAX_MODELS])


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
        # maxTokens and input are always present: an entry without them runs on
        # OpenClaw's 8192 fallback (truncated turns) and degrades the
        # vision-dependent executor/reviewer.
        entry = local_models.build_local_provider_entry(
            "http://h:8080/v1", ["qwen", "llama"]
        )
        expected = {
            "maxTokens": local_models.DEFAULT_MAX_TOKENS,
            "input": ["text", "image"],
        }
        assert entry["models"] == [
            {"id": "qwen", "name": "qwen", **expected},
            {"id": "llama", "name": "llama", **expected},
        ]

    def test_empty_and_non_string_ids_filtered(self):
        entry = local_models.build_local_provider_entry(
            "http://h:8080/v1", ["ok", "", "  ", None, 5]
        )
        assert [m["id"] for m in entry["models"]] == ["ok"]

    def test_none_ids_gives_empty_models(self):
        entry = local_models.build_local_provider_entry("http://h:8080/v1", None)
        assert entry["models"] == []

    def test_metadata_enriches_known_fields_only(self):
        metadata = {
            "qwen": {"context_window": 32768, "reasoning": True, "vision": False},
            "llava": {"context_window": None, "reasoning": None, "vision": True},
        }
        entry = local_models.build_local_provider_entry(
            "http://h:11434/v1", ["qwen", "llava", "bare"], metadata
        )
        qwen, llava, bare = entry["models"]
        assert qwen == {
            "id": "qwen",
            "name": "qwen",
            "maxTokens": 16384,
            "contextWindow": 32768,
            "reasoning": True,
            "input": ["text"],
        }
        # Unknown contextWindow/reasoning are omitted, never guessed; unknown
        # vision defaults input to text+image (executor/reviewer need image).
        assert llava == {
            "id": "llava",
            "name": "llava",
            "maxTokens": local_models.DEFAULT_MAX_TOKENS,
            "input": ["text", "image"],
        }
        assert bare == {
            "id": "bare",
            "name": "bare",
            "maxTokens": local_models.DEFAULT_MAX_TOKENS,
            "input": ["text", "image"],
        }

    def test_small_context_halves_max_tokens(self):
        metadata = {"tiny": {"context_window": 8192, "reasoning": None, "vision": None}}
        entry = local_models.build_local_provider_entry(
            "http://h:8080/v1", ["tiny"], metadata
        )
        assert entry["models"][0]["maxTokens"] == 4096
        assert entry["models"][0]["contextWindow"] == 8192


# ── derive_max_tokens / parse helpers ────────────────────────────────────────

class TestDerivedDefaults:
    def test_unknown_context_gets_conservative_default(self):
        assert local_models.derive_max_tokens(None) == local_models.DEFAULT_MAX_TOKENS
        assert local_models.derive_max_tokens(0) == local_models.DEFAULT_MAX_TOKENS
        assert local_models.derive_max_tokens("64k") == local_models.DEFAULT_MAX_TOKENS

    def test_large_known_context_capped_at_32k(self):
        # The 32k pipeline-comfortable cap is only reached when the window is
        # KNOWN to be 64k+; an unknown window stays on the conservative 16k.
        assert local_models.derive_max_tokens(262144) == local_models.DERIVED_MAX_TOKENS_CAP
        assert local_models.derive_max_tokens(65536) == 32768

    def test_small_context_halved(self):
        assert local_models.derive_max_tokens(8192) == 4096
        assert local_models.derive_max_tokens(32768) == 16384

    def test_parse_positive_int(self):
        assert local_models.parse_positive_int("16384") == 16384
        assert local_models.parse_positive_int(" 8192 ") == 8192
        for bad in (None, "", "0", "-5", "lots", "1.5"):
            assert local_models.parse_positive_int(bad) is None

    def test_parse_bool_flag(self):
        for yes in ("1", "true", "YES", "on"):
            assert local_models.parse_bool_flag(yes) is True
        for no in ("0", "false", "No", "off"):
            assert local_models.parse_bool_flag(no) is False
        for unset in (None, "", "maybe"):
            assert local_models.parse_bool_flag(unset) is None


# ── apply_local_model_overrides ──────────────────────────────────────────────

class TestOverrides:
    def _entry(self):
        return local_models.build_local_provider_entry(
            "http://h:11434/v1", ["a", "b"]
        )

    def test_overrides_win_on_all_models_when_untargeted(self):
        out = local_models.apply_local_model_overrides(
            self._entry(), max_tokens=20000, reasoning=True
        )
        assert all(m["maxTokens"] == 20000 and m["reasoning"] is True for m in out["models"])

    def test_target_ids_limit_scope(self):
        out = local_models.apply_local_model_overrides(
            self._entry(), max_tokens=20000, reasoning=False, target_ids=["b"]
        )
        a, b = out["models"]
        assert a["maxTokens"] == local_models.DEFAULT_MAX_TOKENS and "reasoning" not in a
        assert b["maxTokens"] == 20000 and b["reasoning"] is False

    def test_context_window_override_rederives_max_tokens(self):
        # An explicit window with no explicit budget re-derives maxTokens.
        out = local_models.apply_local_model_overrides(
            self._entry(), context_window=131072
        )
        for m in out["models"]:
            assert m["contextWindow"] == 131072
            assert m["maxTokens"] == local_models.DERIVED_MAX_TOKENS_CAP

    def test_explicit_max_tokens_beats_rederivation(self):
        out = local_models.apply_local_model_overrides(
            self._entry(), context_window=131072, max_tokens=8000
        )
        assert all(m["maxTokens"] == 8000 for m in out["models"])

    def test_vision_override_maps_to_input(self):
        no = local_models.apply_local_model_overrides(self._entry(), vision=False)
        assert all(m["input"] == ["text"] for m in no["models"])
        yes = local_models.apply_local_model_overrides(no, vision=True)
        assert all(m["input"] == ["text", "image"] for m in yes["models"])

    def test_none_overrides_are_noops_and_input_not_mutated(self):
        entry = self._entry()
        before = json.dumps(entry, sort_keys=True)
        out = local_models.apply_local_model_overrides(entry)
        assert json.dumps(entry, sort_keys=True) == before
        assert out == entry


# ── summarize_model_entry ────────────────────────────────────────────────────

class TestSummarize:
    def test_full_entry(self):
        s = local_models.summarize_model_entry(
            {"maxTokens": 16384, "contextWindow": 32768, "reasoning": True, "input": ["text", "image"]}
        )
        assert s == "maxTokens=16384, contextWindow=32768, reasoning=on, vision"

    def test_bare_entry_reads_unset(self):
        s = local_models.summarize_model_entry({"maxTokens": 16384})
        assert s == "maxTokens=16384, contextWindow=unknown, reasoning=unset"


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
        # Ids not on the live server are dropped; the list mirrors the probe.
        assert [m["id"] for m in local["models"]] == ["new"]

    def test_prior_model_fields_survive_regeneration(self):
        # A hand-tuned (or setup-screen-enriched) model entry must not be wiped
        # by the per-boot regeneration: same-id fields win over the fresh entry.
        config = {
            "models": {
                "providers": {
                    "local": {
                        "baseUrl": "http://h:11434/v1",
                        "models": [
                            {
                                "id": "m1",
                                "name": "Qwen (tuned)",
                                "maxTokens": 32768,
                                "reasoning": True,
                                "contextWindow": 131072,
                            }
                        ],
                    }
                }
            }
        }
        result = local_models.merge_local_provider(
            config, self._entry(ids=("m1", "m2"))
        )
        m1, m2 = result["models"]["providers"]["local"]["models"]
        # Hand-tuned fields win; the missing input key is gap-filled by the
        # fresh entry's default.
        assert m1 == {
            "id": "m1",
            "name": "Qwen (tuned)",
            "maxTokens": 32768,
            "reasoning": True,
            "contextWindow": 131072,
            "input": ["text", "image"],
        }
        # A newly discovered model still gets the generated defaults.
        assert m2["id"] == "m2" and m2["maxTokens"] == local_models.DEFAULT_MAX_TOKENS

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
