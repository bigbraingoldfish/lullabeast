"""
Tests for install.sh step 13 (Playwright MCP provisioning).

The step must:
  - Be present in the install script with the correct numbering (13/15).
  - Accept --skip-playwright as a top-level CLI flag and honor it.
  - Prompt the user with a default-yes answer.
  - Attempt to install @playwright/mcp via npx and chromium via playwright install.
  - Attempt to register mcp.servers.playwright in openclaw.json.
  - Write a PLAYWRIGHT_STEP status into the final summary table.

Also tests that register_agent.py grants the browser tool to executor and
reviewer (needed so the executor can capture screenshots and the reviewer
can verify them — see solitaire post-mortem Step 10).
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _REPO_ROOT / "install.sh"


def _read_install_sh() -> str:
    return _INSTALL_SH.read_text(encoding="utf-8")


class TestPlaywrightCliFlag:
    def test_skip_playwright_flag_handled(self):
        text = _read_install_sh()
        assert "SKIP_PLAYWRIGHT=0" in text, "SKIP_PLAYWRIGHT default must be 0 (default-on)"
        assert '"$arg" = "--skip-playwright"' in text, "--skip-playwright flag must be parsed"

    def test_usage_line_mentions_skip_playwright(self):
        text = _read_install_sh()
        assert "[--skip-playwright]" in text, "usage line must document --skip-playwright"


class TestStep13PlaywrightSection:
    def _section(self) -> str:
        text = _read_install_sh()
        start = text.index("13/15  INSTALL PLAYWRIGHT MCP")
        end = text.index("14/15  MARK SETUP COMPLETE")
        return text[start:end]

    def test_step_exists_and_is_numbered_13(self):
        # Will raise ValueError if not found
        self._section()

    def test_step_runs_before_setup_marker(self):
        text = _read_install_sh()
        assert text.index("13/15  INSTALL PLAYWRIGHT MCP") < text.index(
            "14/15  MARK SETUP COMPLETE"
        ), "Playwright provisioning must complete before the setup-complete marker"

    def test_step_honors_skip_flag(self):
        s = self._section()
        assert 'SKIP_PLAYWRIGHT' in s and '-eq 1' in s, (
            "--skip-playwright must short-circuit the step"
        )

    def test_step_prompts_default_yes(self):
        s = self._section()
        assert "prompt_yn" in s, "step must use prompt_yn helper"
        # Default-yes means the prompt suffix is [Y/n] and the second arg is "Y"
        assert '"Y"' in s, "prompt must default to Y"

    def test_step_installs_playwright_mcp_package(self):
        s = self._section()
        assert "@playwright/mcp" in s, "step must install @playwright/mcp"

    def test_step_installs_chromium_browser(self):
        s = self._section()
        assert "playwright install" in s and "chromium" in s, (
            "step must run 'playwright install ... chromium'"
        )

    def test_step_registers_mcp_in_openclaw_json(self):
        s = self._section()
        assert "mcp" in s and "servers" in s and "playwright" in s, (
            "step must register the playwright MCP server in openclaw.json"
        )

    def test_step_uses_atomic_write_for_openclaw_json(self):
        s = self._section()
        assert "tempfile.mkstemp" in s and "os.replace" in s, (
            "openclaw.json mutation must be atomic (mkstemp + os.replace)"
        )

    def test_step_writes_status_to_summary(self):
        text = _read_install_sh()
        assert "Playwright MCP" in text, "summary table must include a Playwright row"
        assert "PLAYWRIGHT_STEP" in text, "step must set PLAYWRIGHT_STEP for the summary"


class TestRegisterAgentBrowserGrants:
    def test_executor_gets_browser_in_tools_allow(self):
        from autodev.installer import register_agent

        entry = register_agent._build_new_entry(
            "executor",
            autodev_root="/tmp/oc",
            shared_model={"primary": "openrouter/moonshotai/kimi-k2.6", "fallbacks": []},
            working_list=[],
            stderr=None,
        )
        assert isinstance(entry.get("tools"), dict), "executor must have explicit tools"
        assert "browser" in entry["tools"].get("allow", []), (
            "executor.tools.allow must include 'browser' so Playwright MCP is reachable"
        )

    def test_reviewer_gets_browser_in_tools_allow(self):
        from autodev.installer import register_agent

        entry = register_agent._build_new_entry(
            "reviewer",
            autodev_root="/tmp/oc",
            shared_model={"primary": "openrouter/moonshotai/kimi-k2.6", "fallbacks": []},
            working_list=[],
            stderr=None,
        )
        assert isinstance(entry.get("tools"), dict), "reviewer must have explicit tools"
        assert "browser" in entry["tools"].get("allow", []), (
            "reviewer.tools.allow must include 'browser' so it can verify screenshots"
        )

    def test_planner_does_not_get_browser(self):
        """Planner produces plans; it does not need a browser."""
        from autodev.installer import register_agent

        entry = register_agent._build_new_entry(
            "planner",
            autodev_root="/tmp/oc",
            shared_model={"primary": "openrouter/minimax/minimax-m2.7", "fallbacks": []},
            working_list=[],
            stderr=None,
        )
        # Planner falls into _CODING_WITHOUT_EXPLICIT_TOOLS — no `tools` key at all
        assert "tools" not in entry, "planner must inherit global tools.profile (no explicit tools key)"

    def test_escalation_still_denies_browser(self):
        """Escalation is a human-loop messenger — must NOT have browser access."""
        from autodev.installer import register_agent

        entry = register_agent._build_new_entry(
            "escalation",
            autodev_root="/tmp/oc",
            shared_model={"primary": "llama-local/qwen3.6-27b"},
            working_list=[],
            stderr=None,
        )
        tools = entry["tools"]
        assert "browser" in tools.get("deny", []), (
            "escalation.tools.deny must still include 'browser' (no regression)"
        )
        assert "browser" not in tools.get("allow", [])
