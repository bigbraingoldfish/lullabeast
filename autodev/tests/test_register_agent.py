"""Tests for autodev.installer.register_agent (AutoDev agent registration)."""

import json
import os
from io import StringIO

import pytest

from autodev.installer.register_agent import AUTODEV_AGENT_IDS, register_roadmap_converter


def _write_oc(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def test_accepts_agents_defaults_without_list(tmp_path):
    """OpenClaw variant: agents.defaults but no agents.list — normalize and register."""
    oc = tmp_path / "openclaw.json"
    root = str(tmp_path / "ocroot")
    os.makedirs(root, exist_ok=True)
    _write_oc(
        oc,
        {
            "version": "1.2.0",
            "agents": {
                "defaults": {
                    "model": {"primary": "openrouter/other/model"},
                    "models": {"openrouter/moonshotai/kimi-k2.7-code": {}},
                }
            },
            "hooks": {"token": "x"},
        },
    )

    err = StringIO()
    rv = register_roadmap_converter(str(oc), root, dry_run=True, stderr=err)
    assert rv == "dry_run"

    rv2 = register_roadmap_converter(str(oc), root, dry_run=False, stderr=StringIO())
    assert rv2 == "registered"

    data = json.loads(oc.read_text())
    assert isinstance(data["agents"]["list"], list)
    ids = [e["id"] for e in data["agents"]["list"]]
    for aid in AUTODEV_AGENT_IDS:
        assert aid in ids
    rc = next(e for e in data["agents"]["list"] if e["id"] == "roadmap-converter")
    assert rc["workspace"] == os.path.join(root, "workspace-roadmap-converter")
    assert rc["model"]["primary"] == "openrouter/moonshotai/kimi-k2.7-code"
    hooks = data.get("hooks", {}).get("allowedAgentIds", [])
    for aid in AUTODEV_AGENT_IDS:
        assert aid in hooks


def test_preserves_existing_agent_entries(tmp_path):
    oc = tmp_path / "openclaw.json"
    root = str(tmp_path / "ocroot")
    os.makedirs(root, exist_ok=True)
    existing = [
        {"id": "planner", "workspace": os.path.join(root, "w1")},
        {
            "id": "prd-creator",
            "workspace": os.path.join(root, "w2"),
            "model": {"primary": "custom/model", "fallbacks": []},
            "tools": {"allow": ["read"], "deny": []},
        },
    ]
    _write_oc(
        oc,
        {
            "agents": {"list": list(existing)},
            "hooks": {"allowedAgentIds": ["prd-creator"]},
        },
    )

    assert register_roadmap_converter(str(oc), root, dry_run=False, stderr=StringIO()) == "registered"
    data = json.loads(oc.read_text())
    assert len(data["agents"]["list"]) == 6
    assert data["agents"]["list"][0]["id"] == "planner"
    assert data["agents"]["list"][1]["id"] == "prd-creator"
    rc = next(e for e in data["agents"]["list"] if e["id"] == "roadmap-converter")
    assert rc["model"]["primary"] == "custom/model"


def test_idempotent_already_registered(tmp_path):
    oc = tmp_path / "openclaw.json"
    root = str(tmp_path / "ocroot")
    os.makedirs(root, exist_ok=True)
    m = {"primary": "openrouter/moonshotai/kimi-k2.7-code", "fallbacks": []}
    esc_tools = {
        "allow": ["read", "write"],
        "deny": ["edit", "apply_patch", "exec", "process", "browser"],
    }
    agents = [
        {"id": "planner", "workspace": os.path.join(root, "workspace-planner"), "model": m},
        {"id": "executor", "workspace": os.path.join(root, "workspace-executor"), "model": m},
        {"id": "reviewer", "workspace": os.path.join(root, "workspace-reviewer"), "model": m},
        {"id": "escalation", "workspace": os.path.join(root, "workspace-escalation"), "model": m, "tools": esc_tools},
        {"id": "prd-creator", "workspace": os.path.join(root, "workspace-prd-creator"), "model": m},
        {
            "id": "roadmap-converter",
            "workspace": os.path.join(root, "workspace-roadmap-converter"),
            "model": m,
        },
    ]
    _write_oc(
        oc,
        {
            "agents": {"list": agents},
            "hooks": {"allowedAgentIds": list(AUTODEV_AGENT_IDS)},
        },
    )
    assert register_roadmap_converter(str(oc), root, dry_run=False, stderr=StringIO()) == "already_registered"


def test_adds_hooks_allowlist_if_missing(tmp_path):
    oc = tmp_path / "openclaw.json"
    root = str(tmp_path / "ocroot")
    os.makedirs(root, exist_ok=True)
    _write_oc(
        oc,
        {
            "agents": {
                "list": [
                    {
                        "id": "roadmap-converter",
                        "workspace": os.path.join(root, "workspace-roadmap-converter"),
                        "model": {"primary": "openrouter/moonshotai/kimi-k2.7-code", "fallbacks": []},
                    }
                ]
            },
            "hooks": {},
            "models": {"providers": {"openrouter": {"apiKey": "x"}}},
        },
    )
    assert register_roadmap_converter(str(oc), root, dry_run=False, stderr=StringIO()) == "registered"
    data = json.loads(oc.read_text())
    hooks = data["hooks"]["allowedAgentIds"]
    for aid in AUTODEV_AGENT_IDS:
        assert aid in hooks


def test_missing_prd_creator_uses_openrouter_fallback_when_configured(tmp_path):
    oc = tmp_path / "openclaw.json"
    root = str(tmp_path / "ocroot")
    os.makedirs(root, exist_ok=True)
    _write_oc(
        oc,
        {
            "agents": {"defaults": {}},
            "models": {"providers": {"openrouter": {"apiKey": "test-key"}}},
        },
    )
    err = StringIO()
    assert register_roadmap_converter(str(oc), root, dry_run=False, stderr=err) == "registered"
    data = json.loads(oc.read_text())
    rc = next(e for e in data["agents"]["list"] if e["id"] == "roadmap-converter")
    assert rc["model"]["primary"] == "openrouter/moonshotai/kimi-k2.7-code"
    assert "openrouter" in err.getvalue().lower() or "kimi" in err.getvalue().lower()


def test_missing_prd_creator_uses_defaults_model_when_no_openrouter(tmp_path):
    oc = tmp_path / "openclaw.json"
    root = str(tmp_path / "ocroot")
    os.makedirs(root, exist_ok=True)
    _write_oc(
        oc,
        {
            "agents": {
                "defaults": {
                    "model": {"primary": "anthropic/claude-sonnet", "fallbacks": []},
                }
            },
        },
    )
    err = StringIO()
    assert register_roadmap_converter(str(oc), root, dry_run=False, stderr=err) == "registered"
    data = json.loads(oc.read_text())
    rc = next(e for e in data["agents"]["list"] if e["id"] == "roadmap-converter")
    assert rc["model"]["primary"] == "anthropic/claude-sonnet"
    warn = err.getvalue().lower()
    assert "openrouter" in warn or "kimi" in warn or "recommend" in warn


def test_agents_not_object_errors(tmp_path):
    oc = tmp_path / "openclaw.json"
    root = str(tmp_path / "ocroot")
    _write_oc(oc, {"agents": "bad"})
    rv = register_roadmap_converter(str(oc), root, dry_run=True, stderr=StringIO())
    assert rv.startswith("error:")


# --- Truncation seeding (audit: metaprompt-2-truncation-settings-audit) ------
# Newly created agent entries must be "born correct" with the AutoDev bootstrap
# cap (all six) and, for the pipeline roles, the post-compaction cap. Existing
# installs are handled by setup_helpers.ensure_openclaw_context_limits; these
# tests pin the fresh-install path that _build_new_entry owns.


def _oc_openrouter(tmp_path):
    oc = tmp_path / "openclaw.json"
    root = str(tmp_path / "ocroot")
    os.makedirs(root, exist_ok=True)
    _write_oc(
        oc,
        {
            "agents": {"defaults": {}},
            "models": {"providers": {"openrouter": {"apiKey": "x"}}},
        },
    )
    return oc, root


def test_new_entries_seed_bootstrap_max_chars(tmp_path):
    from autodev.installer.register_agent import BOOTSTRAP_MAX_CHARS

    oc, root = _oc_openrouter(tmp_path)
    assert register_roadmap_converter(str(oc), root, dry_run=False, stderr=StringIO()) == "registered"
    data = json.loads(oc.read_text())
    assert BOOTSTRAP_MAX_CHARS == 32000
    for e in data["agents"]["list"]:
        assert e["bootstrapMaxChars"] == BOOTSTRAP_MAX_CHARS, e["id"]


def test_new_pipeline_entries_seed_postcompaction_cap(tmp_path):
    from autodev.installer.register_agent import POSTCOMPACTION_AGENT_IDS, POSTCOMPACTION_MAX_CHARS

    oc, root = _oc_openrouter(tmp_path)
    register_roadmap_converter(str(oc), root, dry_run=False, stderr=StringIO())
    by_id = {e["id"]: e for e in json.loads(oc.read_text())["agents"]["list"]}
    assert POSTCOMPACTION_MAX_CHARS == 8000
    assert tuple(POSTCOMPACTION_AGENT_IDS) == ("planner", "executor", "reviewer")
    for a in POSTCOMPACTION_AGENT_IDS:
        assert by_id[a]["contextLimits"]["postCompactionMaxChars"] == POSTCOMPACTION_MAX_CHARS, a
    for a in ("escalation", "prd-creator", "roadmap-converter"):
        assert "postCompactionMaxChars" not in by_id[a].get("contextLimits", {}), a


def test_register_agent_seed_matches_setup_helpers_constants():
    """Drift guard: register_agent runs as a standalone script (no package import at
    runtime), so its seed constants are duplicated by necessity. This test prevents
    the two installer modules from silently diverging on the truncation values.
    """
    from autodev.installer import register_agent, setup_helpers

    assert register_agent.BOOTSTRAP_MAX_CHARS == setup_helpers.AUTODEV_BOOTSTRAP_MAX_CHARS
    assert register_agent.POSTCOMPACTION_MAX_CHARS == setup_helpers.AUTODEV_POSTCOMPACTION_MAX_CHARS
    assert tuple(register_agent.POSTCOMPACTION_AGENT_IDS) == tuple(
        setup_helpers.AUTODEV_POSTCOMPACTION_AGENT_IDS
    )
