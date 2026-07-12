"""Tests for the model property overlay (autodev/installer/model_overrides.py).

The overlay is the durable seam for dashboard edits to model metadata: per-boot
reconcile force-wins template-declared values, so the overlay must survive a
render/reconcile pass and re-assert user edits on top (D8 in the per-role model
selection roadmap).
"""
import json
import os
from pathlib import Path

import pytest

from autodev.installer.model_overrides import (
    CONTEXT_WINDOW_CEILING,
    COST_CEILING,
    MAX_TOKENS_CEILING,
    apply_model_overrides,
    load_model_overrides,
    merge_model_overrides,
    split_valid_overrides,
    validate_model_override,
)
from autodev.installer import openclaw_template as tpl

REPO = Path(__file__).resolve().parents[1]


def _config():
    return {
        "models": {
            "providers": {
                "openrouter": {
                    "models": [
                        {
                            "id": "vendor/alpha",
                            "name": "Alpha",
                            "input": ["text"],
                            "cost": {"input": 1.0, "output": 2.0},
                            "contextWindow": 100_000,
                            "maxTokens": 8192,
                            "reasoning": False,
                        },
                    ]
                },
                "local": {"models": [{"id": "qwen3.5", "name": "qwen3.5"}]},
            }
        },
        "agents": {
            "defaults": {
                "models": {
                    "openrouter/vendor/alpha": {"params": {"temperature": 0.6, "top_p": 0.95}}
                }
            },
            "list": [],
        },
    }


# ── validation ───────────────────────────────────────────────────────────────

class TestValidate:
    def test_full_valid_override_passes(self):
        errors = validate_model_override(
            {
                "input": ["text", "image"],
                "contextWindow": 200_000,
                "maxTokens": 16384,
                "reasoning": True,
                "cost": {"input": 0.5, "output": 1.5, "cacheRead": 0, "cacheWrite": 0},
                "params": {"temperature": 0.7, "top_p": 0.9},
            }
        )
        assert errors == []

    def test_full_envelope_at_ceilings_passes(self):
        # The outer edge of what the editor permits, paired with the CI boot
        # guard in deploy-image.yml: if this envelope ever stops booting the
        # gateway, OpenClaw tightened its schema under our ceilings and both bounds
        # move together.
        errors = validate_model_override(
            {
                "input": ["text", "image", "video", "audio"],
                "contextWindow": CONTEXT_WINDOW_CEILING,
                "maxTokens": MAX_TOKENS_CEILING,
                "reasoning": True,
                "cost": {k: COST_CEILING for k in ("input", "output", "cacheRead", "cacheWrite")},
                "params": {"temperature": 2, "top_p": 1},
            }
        )
        assert errors == []

    def test_none_clears_are_accepted(self):
        assert validate_model_override({"reasoning": None, "cost": {"input": None}}) == []

    def test_empty_or_non_dict_rejected(self):
        assert validate_model_override({}) != []
        assert validate_model_override("nope") != []

    def test_unknown_property_rejected(self):
        assert any("unknown property" in e for e in validate_model_override({"speed": 9}))

    @pytest.mark.parametrize(
        "props",
        [
            {"input": []},
            {"input": ["image"]},  # must include text
            {"input": ["text", "telepathy"]},
            {"input": "text"},
            {"contextWindow": 0},
            {"contextWindow": -5},
            {"contextWindow": True},
            {"contextWindow": CONTEXT_WINDOW_CEILING + 1},
            {"maxTokens": 0},
            {"maxTokens": MAX_TOKENS_CEILING + 1},
            {"maxTokens": 1.5},
            {"reasoning": 1},
            {"cost": {"input": -0.1}},
            {"cost": {"input": True}},
            {"cost": {"tokens": 1}},
            {"cost": {"input": float("nan")}},
            {"cost": {"input": float("inf")}},
            {"cost": {}},
            {"params": {"frequency_penalty": 0.5}},
            {"params": {"temperature": -0.1}},
            {"params": {"temperature": 2.1}},
            {"params": {"top_p": 0}},
            {"params": {"top_p": 1.01}},
            {"params": {}},
        ],
    )
    def test_bad_values_rejected(self, props):
        assert validate_model_override(props) != []


# ── split (boot-time quarantine) ─────────────────────────────────────────────

class TestSplitValid:
    def test_keeps_valid_drops_invalid(self):
        valid, dropped = split_valid_overrides(
            {
                "openrouter/good": {"contextWindow": 200_000},
                "openrouter/bad": {"contextWindow": -5},
            }
        )
        assert valid == {"openrouter/good": {"contextWindow": 200_000}}
        assert list(dropped) == ["openrouter/bad"]
        assert dropped["openrouter/bad"]  # carries the validation error(s)

    def test_all_valid_none_dropped(self):
        ov = {"m/x": {"reasoning": True}}
        valid, dropped = split_valid_overrides(ov)
        assert valid == ov
        assert dropped == {}

    def test_all_invalid_none_applied(self):
        valid, dropped = split_valid_overrides({"m/x": {"contextWindow": 0}})
        assert valid == {}
        assert list(dropped) == ["m/x"]


# ── merge ────────────────────────────────────────────────────────────────────

class TestMerge:
    def test_new_entry_created(self):
        merged = merge_model_overrides({}, {"openrouter/vendor/alpha": {"reasoning": True}})
        assert merged == {"openrouter/vendor/alpha": {"reasoning": True}}

    def test_per_field_merge_preserves_others(self):
        existing = {"openrouter/vendor/alpha": {"reasoning": True, "cost": {"input": 0.5}}}
        merged = merge_model_overrides(
            existing, {"openrouter/vendor/alpha": {"cost": {"output": 2.5}}}
        )
        assert merged["openrouter/vendor/alpha"] == {
            "reasoning": True,
            "cost": {"input": 0.5, "output": 2.5},
        }
        # Inputs are not mutated.
        assert existing["openrouter/vendor/alpha"]["cost"] == {"input": 0.5}

    def test_null_clears_field_and_empty_entry_dropped(self):
        existing = {"openrouter/vendor/alpha": {"reasoning": True}}
        merged = merge_model_overrides(existing, {"openrouter/vendor/alpha": {"reasoning": None}})
        assert merged == {}

    def test_null_clears_cost_subkey(self):
        existing = {"m/x": {"cost": {"input": 1.0, "output": 2.0}}}
        merged = merge_model_overrides(existing, {"m/x": {"cost": {"output": None}}})
        assert merged == {"m/x": {"cost": {"input": 1.0}}}

    def test_other_models_untouched(self):
        existing = {"m/x": {"reasoning": True}}
        merged = merge_model_overrides(existing, {"m/y": {"reasoning": False}})
        assert merged["m/x"] == {"reasoning": True}
        assert merged["m/y"] == {"reasoning": False}


# ── apply ────────────────────────────────────────────────────────────────────

class TestApply:
    def test_provider_entry_fields_applied(self):
        out = apply_model_overrides(
            _config(),
            {
                "openrouter/vendor/alpha": {
                    "input": ["text", "image"],
                    "contextWindow": 250_000,
                    "maxTokens": 4096,
                    "reasoning": True,
                    "cost": {"input": 9.9},
                }
            },
        )
        entry = out["models"]["providers"]["openrouter"]["models"][0]
        assert entry["input"] == ["text", "image"]
        assert entry["contextWindow"] == 250_000
        assert entry["maxTokens"] == 4096
        assert entry["reasoning"] is True
        # cost merges per key: output survives the input edit.
        assert entry["cost"] == {"input": 9.9, "output": 2.0}

    def test_params_land_in_agents_defaults(self):
        out = apply_model_overrides(
            _config(), {"openrouter/vendor/alpha": {"params": {"temperature": 1.2}}}
        )
        params = out["agents"]["defaults"]["models"]["openrouter/vendor/alpha"]["params"]
        assert params == {"temperature": 1.2, "top_p": 0.95}

    def test_params_entry_created_when_absent(self):
        out = apply_model_overrides(_config(), {"local/qwen3.5": {"params": {"top_p": 0.8}}})
        assert out["agents"]["defaults"]["models"]["local/qwen3.5"]["params"] == {"top_p": 0.8}

    def test_unregistered_model_skipped(self):
        cfg = _config()
        out = apply_model_overrides(cfg, {"openrouter/vendor/ghost": {"reasoning": True}})
        assert out == cfg

    def test_input_config_never_mutated(self):
        cfg = _config()
        before = json.dumps(cfg, sort_keys=True)
        apply_model_overrides(cfg, {"openrouter/vendor/alpha": {"reasoning": True}})
        assert json.dumps(cfg, sort_keys=True) == before

    def test_no_overrides_returns_config_unchanged(self):
        cfg = _config()
        assert apply_model_overrides(cfg, {}) is cfg

    def test_survives_render_reconcile_pass(self):
        # The entrypoint sequence: render the golden template, reconcile the
        # live config toward it (force-winning template scalars), then re-apply
        # the overlay. The user's edits must land after every pass.
        with open(tpl.template_path(str(REPO)), encoding="utf-8") as f:
            rendered = tpl.render_template_text(
                f.read(), {"HOOKS_TOKEN": "t", "GATEWAY_TOKEN": "g"}
            )
        template = json.loads(rendered)
        overrides = {
            "openrouter/moonshotai/kimi-k2.7-code": {
                "cost": {"input": 0.9},
                "contextWindow": 131_072,
                "params": {"temperature": 0.3},
            }
        }
        live = apply_model_overrides(template, overrides)
        live["meta"] = {"runtime": "bookkeeping"}

        reconciled = tpl.reconcile_config_to_template(template, live)
        kimi = next(
            m
            for m in reconciled["models"]["providers"]["openrouter"]["models"]
            if m["id"] == "moonshotai/kimi-k2.7-code"
        )
        assert kimi["cost"]["input"] == 0.75, "reconcile must revert the raw edit"

        final = apply_model_overrides(reconciled, overrides)
        kimi = next(
            m
            for m in final["models"]["providers"]["openrouter"]["models"]
            if m["id"] == "moonshotai/kimi-k2.7-code"
        )
        assert kimi["cost"]["input"] == 0.9
        assert kimi["contextWindow"] == 131_072
        params = final["agents"]["defaults"]["models"]["openrouter/moonshotai/kimi-k2.7-code"]["params"]
        assert params["temperature"] == 0.3
        assert params["top_p"] == 0.95, "unedited params keep template values"
        assert final["meta"] == {"runtime": "bookkeeping"}, "live-only keys survive"

    def test_overlaid_config_conforms_against_overlay_aware_baseline(self):
        # Boot order: reconcile toward the template, then re-apply the overlay. The
        # doctor's expected baseline is the raw template with the same overlay, so
        # the overlaid live config conforms against it. The absence of this
        # assertion is what let the crash-loop ship.
        raw = tpl.load_template(str(REPO))
        overrides = {
            "openrouter/moonshotai/kimi-k2.7-code": {
                "cost": {"input": 0.9},
                "contextWindow": 131_072,
            }
        }
        reconciled = tpl.reconcile_config_to_template(
            raw, apply_model_overrides(raw, overrides)
        )
        live = apply_model_overrides(reconciled, overrides)
        expected = apply_model_overrides(raw, overrides)
        assert tpl.template_conformance_issues(expected, live) == []
        assert tpl.template_conformance_issues(raw, live) != []


# ── load ─────────────────────────────────────────────────────────────────────

class TestLoad:
    def test_missing_file_returns_empty(self, tmp_path):
        assert load_model_overrides(str(tmp_path / "nope.json")) == {}
        assert load_model_overrides("") == {}

    def test_corrupt_file_returns_empty(self, tmp_path):
        p = tmp_path / "model-overrides.json"
        p.write_text("{not json")
        assert load_model_overrides(str(p)) == {}

    def test_wrong_shape_entries_dropped(self, tmp_path):
        p = tmp_path / "model-overrides.json"
        p.write_text(
            json.dumps(
                {
                    "models": {
                        "openrouter/vendor/alpha": {"reasoning": True},
                        "no-slash": {"reasoning": True},
                        "m/list-valued": ["nope"],
                    }
                }
            )
        )
        assert load_model_overrides(str(p)) == {"openrouter/vendor/alpha": {"reasoning": True}}

    def test_roundtrip(self, tmp_path):
        p = tmp_path / "model-overrides.json"
        models = {"openrouter/vendor/alpha": {"cost": {"input": 0.1}}}
        p.write_text(json.dumps({"models": models}))
        assert load_model_overrides(str(p)) == models
