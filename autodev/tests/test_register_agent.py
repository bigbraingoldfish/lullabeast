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
                    "models": {"openrouter/minimax/minimax-m2.7": {}},
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
    assert rc["model"]["primary"] == "openrouter/minimax/minimax-m2.7"
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
    m = {"primary": "openrouter/minimax/minimax-m2.7", "fallbacks": []}
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
                        "model": {"primary": "openrouter/minimax/minimax-m2.7", "fallbacks": []},
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
    assert rc["model"]["primary"] == "openrouter/minimax/minimax-m2.7"
    assert "openrouter" in err.getvalue().lower() or "minimax" in err.getvalue().lower()


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
    assert "openrouter" in warn or "minimax" in warn or "recommend" in warn


def test_agents_not_object_errors(tmp_path):
    oc = tmp_path / "openclaw.json"
    root = str(tmp_path / "ocroot")
    _write_oc(oc, {"agents": "bad"})
    rv = register_roadmap_converter(str(oc), root, dry_run=True, stderr=StringIO())
    assert rv.startswith("error:")
