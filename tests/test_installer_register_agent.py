"""Tests for autodev/installer/register_agent.py — AutoDev agent registration."""

import json
import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from autodev.installer.register_agent import AUTODEV_AGENT_IDS, register_roadmap_converter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRD_CREATOR_ENTRY = {
    "id": "prd-creator",
    "workspace": "~/.openclaw/workspace-prd-creator",
    "model": {"primary": "openrouter/minimax/minimax-m2.7", "fallbacks": []},
    "tools": {
        "allow": ["read", "write"],
        "deny": ["edit", "apply_patch", "exec", "process", "browser"],
    },
}

_ROADMAP_CONVERTER_ENTRY = {
    "id": "roadmap-converter",
    "workspace": "/fake/openclaw/workspace-roadmap-converter",
    "model": {"primary": "openrouter/minimax/minimax-m2.7", "fallbacks": []},
    "tools": {
        "allow": ["read", "write"],
        "deny": ["edit", "apply_patch", "exec", "process", "browser"],
    },
}

_ESCALATION_TOOLS = {
    "allow": ["read", "write"],
    "deny": ["edit", "apply_patch", "exec", "process", "browser"],
}


def _fully_registered_agents(autodev_root: str) -> list:
    """Six pipeline agents as install would leave them (for idempotency tests)."""
    m = {"primary": "openrouter/minimax/minimax-m2.7", "fallbacks": []}
    root = autodev_root.rstrip("/")
    return [
        {"id": "planner", "workspace": f"{root}/workspace-planner", "model": m},
        {"id": "executor", "workspace": f"{root}/workspace-executor", "model": m},
        {"id": "reviewer", "workspace": f"{root}/workspace-reviewer", "model": m},
        {
            "id": "escalation",
            "workspace": f"{root}/workspace-escalation",
            "model": m,
            "tools": dict(_ESCALATION_TOOLS),
        },
        {
            "id": "prd-creator",
            "workspace": f"{root}/workspace-prd-creator",
            "model": m,
            "tools": dict(_PRD_CREATOR_ENTRY["tools"]),
        },
        {
            "id": "roadmap-converter",
            "workspace": f"{root}/workspace-roadmap-converter",
            "model": m,
            "tools": dict(_PRD_CREATOR_ENTRY["tools"]),
        },
    ]


def _fully_registered_openclaw(tmp_path: Path) -> dict:
    root = str(tmp_path)
    return _base_openclaw_json(
        agents_list=_fully_registered_agents(root),
        allowed_agent_ids=list(AUTODEV_AGENT_IDS),
    )


def _base_openclaw_json(agents_list=None, allowed_agent_ids=None):
    """Return a minimal but realistic openclaw.json dict.

    allowed_agent_ids: if provided, sets hooks.allowedAgentIds explicitly.
    Defaults to a list that mirrors which agent IDs are present in agents_list.
    """
    if agents_list is None:
        agents_list = [_PRD_CREATOR_ENTRY]
    if allowed_agent_ids is None:
        # Derive allowed IDs from the agents_list so tests stay in sync
        allowed_agent_ids = [e["id"] for e in agents_list if "id" in e]
    return {
        "version": "1.2.0",
        "auth": {"profile": "anthropic:default"},
        "agents": {
            "defaults": {"model": "openrouter/minimax/minimax-m2.7"},
            "list": agents_list,
        },
        "hooks": {
            "allowedAgentIds": allowed_agent_ids,
        },
        "tools": {"profile": "coding"},
    }


def _write_openclaw_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAlreadyRegistered:
    def test_already_registered_returns_already_registered(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        data = _fully_registered_openclaw(tmp_path)
        _write_openclaw_json(oc_json, data)

        result = register_roadmap_converter(str(oc_json), str(tmp_path))
        assert result == "already_registered"

    def test_already_registered_does_not_write(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        data = _fully_registered_openclaw(tmp_path)
        _write_openclaw_json(oc_json, data)
        mtime_before = oc_json.stat().st_mtime

        register_roadmap_converter(str(oc_json), str(tmp_path))
        assert oc_json.stat().st_mtime == mtime_before


class TestFallbackWhenNoPrdCreator:
    def test_registers_with_minimax_when_openrouter_configured(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        data = _base_openclaw_json(agents_list=[])
        data["models"] = {"providers": {"openrouter": {"apiKey": "x"}}}
        _write_openclaw_json(oc_json, data)

        result = register_roadmap_converter(str(oc_json), str(tmp_path))
        assert result == "registered"
        updated = json.loads(oc_json.read_text())
        ids = [a["id"] for a in updated["agents"]["list"]]
        for aid in AUTODEV_AGENT_IDS:
            assert aid in ids
        rc = next(a for a in updated["agents"]["list"] if a["id"] == "roadmap-converter")
        assert rc["model"]["primary"] == "openrouter/minimax/minimax-m2.7"

    def test_missing_top_level_agents_key_is_normalized(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        minimal = {
            "version": "1.0",
            "hooks": {"allowedAgentIds": []},
            "models": {"providers": {"openrouter": {"apiKey": "x"}}},
        }
        _write_openclaw_json(oc_json, minimal)

        result = register_roadmap_converter(str(oc_json), str(tmp_path))
        assert result == "registered"
        updated = json.loads(oc_json.read_text())
        assert isinstance(updated.get("agents", {}).get("list"), list)
        ids = [a.get("id") for a in updated["agents"]["list"]]
        for aid in AUTODEV_AGENT_IDS:
            assert aid in ids


class TestSuccessfulRegistration:
    def test_registers_when_missing_returns_registered(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        _write_openclaw_json(oc_json, _base_openclaw_json())

        result = register_roadmap_converter(str(oc_json), str(tmp_path))
        assert result == "registered"
        updated = json.loads(oc_json.read_text())
        assert len(updated["agents"]["list"]) == len(AUTODEV_AGENT_IDS)

    def test_registered_entry_has_correct_id(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        _write_openclaw_json(oc_json, _base_openclaw_json())
        register_roadmap_converter(str(oc_json), str(tmp_path))

        updated = json.loads(oc_json.read_text())
        ids = [a["id"] for a in updated["agents"]["list"]]
        assert "roadmap-converter" in ids

    def test_registered_entry_copies_model_from_prd_creator(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        _write_openclaw_json(oc_json, _base_openclaw_json())
        register_roadmap_converter(str(oc_json), str(tmp_path))

        updated = json.loads(oc_json.read_text())
        rc = next(a for a in updated["agents"]["list"] if a["id"] == "roadmap-converter")
        assert rc["model"] == _PRD_CREATOR_ENTRY["model"]

    def test_registered_entry_has_correct_workspace(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        _write_openclaw_json(oc_json, _base_openclaw_json())
        register_roadmap_converter(str(oc_json), str(tmp_path))

        updated = json.loads(oc_json.read_text())
        rc = next(a for a in updated["agents"]["list"] if a["id"] == "roadmap-converter")
        assert rc["workspace"] == str(tmp_path) + "/workspace-roadmap-converter"

    def test_registered_entry_copies_tools_from_prd_creator(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        _write_openclaw_json(oc_json, _base_openclaw_json())
        register_roadmap_converter(str(oc_json), str(tmp_path))

        updated = json.loads(oc_json.read_text())
        rc = next(a for a in updated["agents"]["list"] if a["id"] == "roadmap-converter")
        assert rc["tools"] == _PRD_CREATOR_ENTRY["tools"]

    def test_preserves_all_existing_top_level_keys(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        data = _base_openclaw_json()
        data["extra_key"] = "must_survive"
        data["tools"] = {"profile": "coding", "custom": True}
        _write_openclaw_json(oc_json, data)

        register_roadmap_converter(str(oc_json), str(tmp_path))
        updated = json.loads(oc_json.read_text())

        assert updated["extra_key"] == "must_survive"
        assert updated["tools"] == {"profile": "coding", "custom": True}
        assert updated["version"] == "1.2.0"
        assert updated["auth"] == {"profile": "anthropic:default"}

    def test_preserves_existing_agents_list_entries(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        planner = {"id": "planner", "workspace": "~/.openclaw/workspace-planner"}
        data = _base_openclaw_json(agents_list=[_PRD_CREATOR_ENTRY, planner])
        _write_openclaw_json(oc_json, data)

        register_roadmap_converter(str(oc_json), str(tmp_path))
        updated = json.loads(oc_json.read_text())

        ids = [a["id"] for a in updated["agents"]["list"]]
        for aid in AUTODEV_AGENT_IDS:
            assert aid in ids
        # Original order preserved; new agents append in AUTODEV_AGENT_IDS order.
        assert updated["agents"]["list"][0]["id"] == "prd-creator"
        assert updated["agents"]["list"][1]["id"] == "planner"
        assert updated["agents"]["list"][1]["workspace"] == "~/.openclaw/workspace-planner"

    def test_atomic_write_calls_os_replace(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        _write_openclaw_json(oc_json, _base_openclaw_json())

        # Capture original BEFORE entering the patch context
        original_replace = os.replace

        with patch("autodev.installer.register_agent.os.replace") as mock_replace:
            mock_replace.side_effect = original_replace
            register_roadmap_converter(str(oc_json), str(tmp_path))

        mock_replace.assert_called_once()
        # Destination must be the original json path
        assert mock_replace.call_args[0][1] == str(oc_json)


class TestDryRun:
    def test_dry_run_returns_dry_run(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        _write_openclaw_json(oc_json, _base_openclaw_json())

        result = register_roadmap_converter(str(oc_json), str(tmp_path), dry_run=True)
        assert result == "dry_run"

    def test_dry_run_does_not_write(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        _write_openclaw_json(oc_json, _base_openclaw_json())
        original_text = oc_json.read_text()

        register_roadmap_converter(str(oc_json), str(tmp_path), dry_run=True)
        assert oc_json.read_text() == original_text

    def test_dry_run_already_registered_returns_already_registered(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        data = _fully_registered_openclaw(tmp_path)
        _write_openclaw_json(oc_json, data)

        result = register_roadmap_converter(str(oc_json), str(tmp_path), dry_run=True)
        assert result == "already_registered"


class TestErrorHandling:
    def test_missing_file_returns_error(self, tmp_path):
        result = register_roadmap_converter(str(tmp_path / "nonexistent.json"), str(tmp_path))
        assert result.startswith("error:")

    def test_invalid_json_returns_error(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        oc_json.write_text("this is not valid json {{{")

        result = register_roadmap_converter(str(oc_json), str(tmp_path))
        assert result.startswith("error:")

    def test_agents_not_object_returns_error(self, tmp_path):
        oc_json = tmp_path / "openclaw.json"
        oc_json.write_text(json.dumps({"version": "1.0", "agents": "invalid"}))

        result = register_roadmap_converter(str(oc_json), str(tmp_path))
        assert result.startswith("error:")


# ---------------------------------------------------------------------------
# install.sh Step 6 — roadmap-converter workspace (contract + source files)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _REPO_ROOT / "install.sh"


def _install_sh_step6_section() -> str:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    start = text.index("6/15  AGENT WORKSPACE PROVISIONING")
    end = text.index("7/15  EXEC-APPROVALS VALIDATION")
    return text[start:end]


def _install_sh_step9_section() -> str:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    start = text.index("9/15  REGISTER AUTODEV AGENTS")
    end = text.index("10/15  CONVERSION PROMPT")
    return text[start:end]


class TestStep6RoadmapConverterWorkspace:
    def test_roadmap_converter_workspace_files_match_source(self):
        rc = _REPO_ROOT / "autodev" / "agents" / "roadmap-converter"
        for name in ("IDENTITY.md", "SOUL.md", "TOOLS.md", "AGENTS.md", "USER.md"):
            assert (rc / name).is_file(), f"missing {name}"
        assert not (rc / "HEARTBEAT.md").exists()

    def test_step6_provisions_roadmap_converter(self):
        step6 = _install_sh_step6_section()
        loops = list(re.finditer(r"for agent in ([^;]+);\s*do", step6))
        assert len(loops) == 4, f"expected 4 'for agent in' loops in step 6, got {len(loops)}"
        for m in loops:
            agents = " ".join(m.group(1).split())
            assert "roadmap-converter" in agents, f"missing roadmap-converter in: {agents!r}"
        assert "planner|executor|reviewer|roadmap-converter" not in step6
        assert re.search(r'case "\$agent" in planner\|executor\|reviewer\)', step6)
        assert step6.count('mkdir -p "$dst_dir/skills"') >= 1


class TestStep6WorkspacePipelineSymlinks:
    """Contract: install.sh step 6 wires pipeline agents to the shared hub symlink."""

    def test_step6_defines_and_calls_symlink_helper(self):
        step6 = _install_sh_step6_section()
        assert "ensure_workspace_pipeline_project_symlinks" in step6
        # Called at least once (definition + invocation)
        assert step6.count("ensure_workspace_pipeline_project_symlinks") >= 2

    def test_step6_symlink_uses_hub_and_ln_sfn(self):
        step6 = _install_sh_step6_section()
        assert 'local hub="$OPENCLAW_ROOT/pipeline-project"' in step6
        assert 'ln -sfn "$hub" "$link"' in step6

    def test_step6_symlink_covers_four_pipeline_agents(self):
        step6 = _install_sh_step6_section()
        assert "planner executor reviewer escalation" in step6.replace("\n", " ")

    def test_step6_skips_pipeline_project_when_not_symlink(self):
        step6 = _install_sh_step6_section()
        assert '[ ! -L "$link" ]' in step6

    def test_step6_symlink_helper_before_step7(self):
        text = _INSTALL_SH.read_text(encoding="utf-8")
        assert text.index("ensure_workspace_pipeline_project_symlinks()") < text.index(
            "7/15  EXEC-APPROVALS VALIDATION"
        )


def test_step3_respects_autodev_repo_path_from_environment():
    """install.sh must not overwrite AUTODEV_REPO_PATH when already exported."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    start = text.index("3/15  PYTHON DEPENDENCIES")
    end = text.index("4/15  OPENCLAW DETECTION")
    step3 = text[start:end]
    assert "AUTODEV_REPO_PATH:-}" in step3
    assert "Using AUTODEV_REPO_PATH from environment" in step3


class TestStep9HooksPreflight:
    def test_step9_audits_and_patches_hooks_before_tools_profile(self):
        step9 = _install_sh_step9_section()
        assert "openclaw_hooks_issues" in step9
        assert "patch_openclaw_hooks_baseline" in step9
        assert step9.index("openclaw_hooks_issues") < step9.index("TOOLS_PROFILE=")

    def test_install_summary_includes_hooks_line(self):
        text = _INSTALL_SH.read_text(encoding="utf-8")
        assert "OpenClaw hooks (webhook):" in text
