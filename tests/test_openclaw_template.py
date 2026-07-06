"""Tests for the DS-2b golden openclaw.json template and its helpers.

Covers the roadmap's test contract: the template parses; every known-required
baseline key is present with the required value; every model entry referenced
by an agent has a complete 4-field pricing block (and the executor/reviewer
picks are multimodal); no secret-shaped values; env-substitution placeholders
well-formed. Plus the DS-2b acceptance run: rendering the template with test
env values and pointing the doctor's config-level checks at it comes back all
green, including the new template_conformance check.

Hermetic: the template is read from the repo checkout (a committed file, not
operator state); rendered configs land under tmp_path.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from autodev.installer import doctor
from autodev.installer.openclaw_template import (
    TEMPLATE_ENV_VARS,
    TEMPLATE_MODEL_DEFAULTS,
    TEMPLATE_PLACEHOLDER_RE,
    TEMPLATE_REQUIRED_VARS,
    load_template,
    render_template_text,
    template_conformance_issues,
    template_path,
    template_placeholders,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE_PATH = Path(template_path(str(_REPO_ROOT)))

AGENT_IDS = (
    "planner", "executor", "reviewer", "escalation", "prd-creator", "roadmap-converter",
)
ALWAYS_APPLY = (
    "Always-Apply: Integration Wiring",
    "Always-Apply: Testing Quality",
    "Always-Apply: Orchestrator Control",
)
PRICING_FIELDS = ("input", "output", "cacheRead", "cacheWrite")

_TEST_ENV = {
    "HOOKS_TOKEN": "test-hooks-token-0123456789",
    "GATEWAY_TOKEN": "test-gateway-token-0123456789",
}


def _raw_text() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def _template() -> dict:
    return load_template(str(_REPO_ROOT))


def _rendered() -> dict:
    return json.loads(render_template_text(_raw_text(), _TEST_ENV))


def _agents_by_id(data: dict) -> dict:
    return {e["id"]: e for e in data["agents"]["list"]}


def _resolve_model_ref(primary: str) -> str:
    """Resolve a model.primary value (placeholder or literal) to a model ref."""
    m = TEMPLATE_PLACEHOLDER_RE.fullmatch(primary)
    if m:
        return TEMPLATE_MODEL_DEFAULTS[m.group(1)]
    return primary


# ── template parses ──────────────────────────────────────────────────────────

class TestTemplateParses:
    def test_file_exists_and_parses(self):
        assert _TEMPLATE_PATH.is_file()
        data = json.loads(_raw_text())
        assert isinstance(data, dict)

    def test_load_template_helper(self):
        assert isinstance(_template(), dict)


# ── known-required baseline (DS-2b task 2) ───────────────────────────────────

class TestRequiredBaseline:
    def test_hooks_block_shape(self):
        hooks = _template()["hooks"]
        assert hooks["enabled"] is True
        assert hooks["allowRequestSessionKey"] is True
        assert hooks["token"] == "${HOOKS_TOKEN}"
        assert "pipeline:" in hooks["allowedSessionKeyPrefixes"]
        assert "ideas:" in hooks["allowedSessionKeyPrefixes"]
        assert set(hooks["allowedAgentIds"]) == set(AGENT_IDS)
        assert hooks["defaultSessionKey"].startswith("pipeline:")

    def test_heartbeat_disabled(self):
        assert _template()["agents"]["defaults"]["heartbeat"]["every"] == "0m"

    def test_tools_profile_coding(self):
        assert _template()["tools"]["profile"] == "coding"

    def test_context_limits_on_agents(self):
        by_id = _agents_by_id(_template())
        assert set(by_id) == set(AGENT_IDS)
        for aid in AGENT_IDS:
            assert by_id[aid]["bootstrapMaxChars"] == 32000, aid
        for aid in ("planner", "executor", "reviewer"):
            assert by_id[aid]["contextLimits"]["postCompactionMaxChars"] == 8000, aid

    def test_postcompaction_sections(self):
        sections = _template()["agents"]["defaults"]["compaction"][
            "postCompactionSections"
        ]
        for name in ALWAYS_APPLY:
            assert name in sections, name

    def test_plugin_entry_with_conversation_access(self):
        plugins = _template()["plugins"]
        assert "autodev-pipeline-signals" in plugins["allow"]
        entry = plugins["entries"]["autodev-pipeline-signals"]
        assert entry["enabled"] is True
        assert entry["hooks"]["allowConversationAccess"] is True

    def test_playwright_mcp_registered(self):
        server = _template()["mcp"]["servers"]["playwright"]
        assert server["command"] == "npx"
        assert any("@playwright/mcp" in a for a in server["args"])

    def test_pricing_enabled(self):
        assert _template()["models"]["pricing"]["enabled"] is True

    def test_escalation_tool_policy(self):
        # Security constraint: escalation is notify-only (read/write/message,
        # never edit/exec/browser). The template must not weaken it.
        esc = _agents_by_id(_template())["escalation"]
        tools = esc["tools"]
        assert set(tools["alsoAllow"]) == {"read", "write", "message"}
        for denied in ("edit", "apply_patch", "exec", "process", "browser"):
            assert denied in tools["deny"], denied

    def test_no_baked_version_or_operator_blocks(self):
        # Gateway-owned bookkeeping and operator-personal blocks must not ship.
        data = _template()
        for absent in ("meta", "wizard", "auth", "bindings", "channels", "messages"):
            assert absent not in data, absent


# ── agent-referenced models carry complete pricing (DS-2b task 3) ────────────

class TestModelPricing:
    def _openrouter_models(self) -> dict:
        data = _template()
        return {
            m["id"]: m
            for m in data["models"]["providers"]["openrouter"]["models"]
        }

    def _referenced_model_ids(self) -> set[str]:
        data = _template()
        refs = {data["agents"]["defaults"]["model"]["primary"]}
        for entry in data["agents"]["list"]:
            refs.add(entry["model"]["primary"])
        ids = set()
        for ref in refs:
            resolved = _resolve_model_ref(ref)
            assert resolved.startswith("openrouter/"), resolved
            ids.add(resolved[len("openrouter/"):])
        return ids

    def test_every_referenced_model_has_complete_pricing(self):
        models = self._openrouter_models()
        for mid in self._referenced_model_ids():
            assert mid in models, f"referenced model {mid} not shipped in the template"
            cost = models[mid]["cost"]
            for field in PRICING_FIELDS:
                assert field in cost, f"{mid}: missing cost.{field}"
                v = cost[field]
                assert isinstance(v, (int, float)) and not isinstance(v, bool), (
                    f"{mid}: cost.{field} not numeric"
                )
                assert v >= 0, f"{mid}: cost.{field} negative"
            assert cost["input"] > 0 and cost["output"] > 0, mid

    def test_every_shipped_model_is_referenced(self):
        # No dead pricing entries: the shipped set is exactly the recommended set.
        assert set(self._openrouter_models()) == self._referenced_model_ids()

    def test_executor_and_reviewer_defaults_are_multimodal(self):
        models = self._openrouter_models()
        for var in ("EXECUTOR_MODEL", "REVIEWER_MODEL"):
            ref = TEMPLATE_MODEL_DEFAULTS[var]
            mid = ref[len("openrouter/"):]
            assert "image" in models[mid]["input"], (
                f"{var} default {mid} must accept image input (visual review)"
            )

    def test_shipped_set_is_three_or_four_models(self):
        assert 3 <= len(self._openrouter_models()) <= 4


# ── no secret-shaped values (DS-2b tests bullet) ─────────────────────────────

_SECRET_PATTERNS = (
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),          # hex tokens (hooks/gateway style)
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),      # provider API keys
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),    # Google API keys
    re.compile(r"\+\d{10,}\b"),                    # phone numbers
    re.compile(r"\b[A-Za-z0-9+/]{40,}=\b"),       # base64 blobs
)


def _iter_strings(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _iter_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _iter_strings(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


class TestNoSecrets:
    def test_no_secret_shaped_string_values(self):
        offenders = [
            (path, s)
            for path, s in _iter_strings(_template())
            for pat in _SECRET_PATTERNS
            if pat.search(s)
        ]
        assert offenders == [], f"secret-shaped values in template: {offenders}"

    def test_token_fields_are_placeholders(self):
        data = _template()
        assert data["hooks"]["token"] == "${HOOKS_TOKEN}"
        assert data["gateway"]["auth"]["token"] == "${GATEWAY_TOKEN}"


# ── placeholder well-formedness + rendering ──────────────────────────────────

class TestPlaceholders:
    def test_every_dollar_is_a_wellformed_placeholder(self):
        text = _raw_text()
        # Strip every well-formed placeholder; no `$` may survive (catches
        # `$VAR`, `${lower}`, and unclosed `${` alike).
        leftover = TEMPLATE_PLACEHOLDER_RE.sub("", text)
        assert "$" not in leftover, "malformed $-token in template"

    def test_placeholder_set_matches_contract(self):
        found = template_placeholders(_raw_text())
        assert found == set(TEMPLATE_ENV_VARS)

    def test_render_with_test_env_is_valid_json_with_no_placeholders(self):
        rendered = render_template_text(_raw_text(), _TEST_ENV)
        assert "${" not in rendered
        data = json.loads(rendered)
        assert data["hooks"]["token"] == _TEST_ENV["HOOKS_TOKEN"]
        assert data["gateway"]["auth"]["token"] == _TEST_ENV["GATEWAY_TOKEN"]
        for entry in data["agents"]["list"]:
            assert entry["model"]["primary"].startswith("openrouter/")

    def test_model_env_overrides_win(self):
        env = dict(_TEST_ENV, EXECUTOR_MODEL="openrouter/custom/model-x")
        data = json.loads(render_template_text(_raw_text(), env))
        by_id = _agents_by_id(data)
        assert by_id["executor"]["model"]["primary"] == "openrouter/custom/model-x"
        # Unset vars still fall back to the audit defaults.
        assert by_id["planner"]["model"]["primary"] == TEMPLATE_MODEL_DEFAULTS["PLANNER_MODEL"]

    @pytest.mark.parametrize("missing", TEMPLATE_REQUIRED_VARS)
    def test_render_missing_required_var_raises(self, missing):
        env = {k: v for k, v in _TEST_ENV.items() if k != missing}
        with pytest.raises(ValueError, match=missing):
            render_template_text(_raw_text(), env)


# ── conformance checker unit behavior ────────────────────────────────────────

class TestConformanceIssues:
    def test_rendered_config_conforms_to_template(self):
        assert template_conformance_issues(_template(), _rendered()) == []

    def test_extra_live_keys_and_entries_tolerated(self):
        live = _rendered()
        live["meta"] = {"lastTouchedVersion": "2026.6.11"}
        live["agents"]["list"].append({"id": "personal-assistant"})
        live["hooks"]["allowedAgentIds"].append("personal-assistant")
        assert template_conformance_issues(_template(), live) == []

    def test_drifted_scalar_reported(self):
        live = _rendered()
        live["tools"]["profile"] = "minimal"
        issues = template_conformance_issues(_template(), live)
        assert any(i.startswith("tools.profile:") for i in issues)

    def test_missing_key_reported(self):
        live = _rendered()
        del live["agents"]["defaults"]["heartbeat"]
        issues = template_conformance_issues(_template(), live)
        assert any("agents.defaults.heartbeat" in i for i in issues)

    def test_missing_agent_entry_reported(self):
        live = _rendered()
        live["agents"]["list"] = [
            e for e in live["agents"]["list"] if e["id"] != "reviewer"
        ]
        issues = template_conformance_issues(_template(), live)
        assert any("agents.list[id=reviewer]" in i for i in issues)

    def test_blanked_placeholder_target_reported(self):
        live = _rendered()
        live["hooks"]["token"] = ""
        issues = template_conformance_issues(_template(), live)
        assert any(i.startswith("hooks.token:") for i in issues)

    def test_bool_int_confusion_is_drift(self):
        live = _rendered()
        live["hooks"]["enabled"] = 1
        issues = template_conformance_issues(_template(), live)
        assert any(i.startswith("hooks.enabled:") for i in issues)


# ── acceptance: doctor config-level checks against the rendered template ─────

@pytest.fixture
def rendered_root(tmp_path):
    """A fake OPENCLAW_ROOT whose openclaw.json is the rendered template."""
    oc = tmp_path / "openclaw"
    oc.mkdir()
    (oc / "openclaw.json").write_text(
        render_template_text(_raw_text(), _TEST_ENV), encoding="utf-8"
    )
    return {
        "openclaw_root": str(oc),
        # The real repo checkout: template_conformance reads the committed
        # template file (read-only), never operator state.
        "autodev_repo_path": str(_REPO_ROOT),
    }


class TestDoctorAgainstRenderedTemplate:
    CONFIG_LEVEL_CHECKS = (
        doctor.check_openclaw_json,
        doctor.check_hooks_baseline,
        doctor.check_agents_registered,
        doctor.check_context_limits,
        doctor.check_tools_profile,
        doctor.check_heartbeat_disabled,
    )

    def test_config_level_checks_all_green(self, rendered_root):
        for check in self.CONFIG_LEVEL_CHECKS:
            result = check(rendered_root)
            assert result.status == "ok", (result.id, result.detail)

    def test_template_conformance_ok_in_owned_mode(self, rendered_root, monkeypatch):
        monkeypatch.setenv("OWNED_OPENCLAW", "1")
        result = doctor.check_template_conformance(rendered_root)
        assert result.status == "ok", result.detail

    def test_template_conformance_skipped_outside_owned_mode(
        self, rendered_root, monkeypatch
    ):
        monkeypatch.delenv("OWNED_OPENCLAW", raising=False)
        assert doctor.check_template_conformance(rendered_root).status == "skipped"
        monkeypatch.setenv("OWNED_OPENCLAW", "0")
        assert doctor.check_template_conformance(rendered_root).status == "skipped"

    def test_template_conformance_fails_on_drift(self, rendered_root, monkeypatch):
        monkeypatch.setenv("OWNED_OPENCLAW", "1")
        path = os.path.join(rendered_root["openclaw_root"], "openclaw.json")
        with open(path) as f:
            data = json.load(f)
        data["agents"]["defaults"]["heartbeat"]["every"] = "5m"
        with open(path, "w") as f:
            json.dump(data, f)
        result = doctor.check_template_conformance(rendered_root)
        assert result.status == "fail"
        assert "heartbeat" in result.detail
        assert result.fix_hint

    def test_template_conformance_fails_on_missing_template(
        self, rendered_root, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("OWNED_OPENCLAW", "1")
        config = dict(rendered_root, autodev_repo_path=str(tmp_path / "empty-repo"))
        result = doctor.check_template_conformance(config)
        assert result.status == "fail"
        assert "not found" in result.detail
