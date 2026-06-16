"""Tests for INFRA-B1: Fix --project-path CLI, prd-creator config, and new config keys."""

import json
import os
import re
import sys

import pytest

# Paths resolved relative to project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_PY = os.path.join(PROJECT_ROOT, "ui", "server.py")
CONFIG_EXAMPLE_JSON = os.path.join(PROJECT_ROOT, "ui", "config.example.json")
OPENCLAW_JSON = os.path.expanduser("~/.openclaw/openclaw.json")


class TestProjectPathArg:
    """Pass criterion 1: server.py post_resume_orchestrator() spawns with --project-path."""

    def test_orchestrator_spawn_uses_project_path_flag(self):
        with open(SERVER_PY, "r") as f:
            content = f.read()
        # Find the subprocess.Popen call in or near post_resume_orchestrator.
        # Production uses sys.executable (not the literal string "python") as the interpreter.
        match = re.search(
            r'\[.*?sys\.executable.*?orchestrator_script.*?"--project-path".*?project_path.*?\]',
            content,
            re.DOTALL,
        )
        assert match is not None, (
            "subprocess.Popen for orchestrator.py must use '--project-path' "
            "(not '--project') as the CLI argument"
        )


@pytest.mark.skipif(
    not os.path.exists(OPENCLAW_JSON),
    reason="requires a local OpenClaw install (~/.openclaw/openclaw.json); absent in CI",
)
class TestOpenClawConfig:
    """Pass criteria 2 & 3: openclaw.json has prd-creator and ideas: prefix."""

    def test_prd_creator_in_allowed_agent_ids(self):
        with open(OPENCLAW_JSON, "r") as f:
            cfg = json.load(f)
        assert "prd-creator" in cfg.get("hooks", {}).get("allowedAgentIds", []), (
            "hooks.allowedAgentIds must contain 'prd-creator'"
        )

    def test_ideas_prefix_in_allowed_session_key_prefixes(self):
        with open(OPENCLAW_JSON, "r") as f:
            cfg = json.load(f)
        assert "ideas:" in cfg.get("hooks", {}).get("allowedSessionKeyPrefixes", []), (
            "hooks.allowedSessionKeyPrefixes must contain 'ideas:'"
        )


class TestServerDefaults:
    """Pass criterion 4: server.py DEFAULTS contains the four new keys."""

    def test_defaults_has_all_four_new_keys(self):
        # Dynamically import server module from its path
        import importlib.util

        spec = importlib.util.spec_from_file_location("server", SERVER_PY)
        server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(server)

        required_keys = [
            "ideas_dir",
            "hooks_url",
            "hooks_token",
            "conversion_prompt_path",
        ]
        for key in required_keys:
            assert key in server.DEFAULTS, f"DEFAULTS missing key: {key}"


class TestConfigJson:
    """Pass criterion 5: ui/config.example.json documents the four new keys."""

    def test_config_example_json_has_all_four_new_keys(self):
        with open(CONFIG_EXAMPLE_JSON, "r") as f:
            cfg = json.load(f)
        required_keys = [
            "ideas_dir",
            "hooks_url",
            "hooks_token",
            "conversion_prompt_path",
        ]
        for key in required_keys:
            assert key in cfg, f"config.example.json missing key: {key}"
