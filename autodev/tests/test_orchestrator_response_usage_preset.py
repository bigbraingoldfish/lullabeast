"""Orchestrator responseUsage pre-seed wiring (`_preset_session_response_usage`).

OpenClaw's ``responseUsage`` is a per-session-entry preference (no agent- or
config-level default), so the orchestrator patches every session it is about to
invoke — via gateway ``sessions.patch``, which creates the entry when the key
does not exist yet — so the run appends a token-usage + cost line to each reply.

The pre-seed is wired into ``_record_active_agent`` (covering the three
phase-agent invocation sites) plus the escalation / completion-review invokes.
``AUTODEV_RESPONSE_USAGE`` env governs it: default ``full``, empty/``off``
disables (the test conftest forces ``off`` globally for hermeticity; these
tests opt back in explicitly).
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402


def _make_orch(monkeypatch, tmp_path):
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    calls = []

    def _set_usage(store_key, ws_url, token, mode="full", **k):
        calls.append((store_key, ws_url, token, mode))
        return True

    monkeypatch.setattr(orch_mod, "set_session_response_usage", _set_usage)
    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.openclaw_config = {
        "hooks": {"token": "tok"}, "hooks_url": "http://h",
        "gateway_token": "gw-tok", "gateway_ws_url": "ws://gw",
    }
    orch.state = {"current_phase_raw_id": "CORE-1"}
    return orch, calls


def test_preset_builds_store_key_and_uses_gateway_creds(monkeypatch, tmp_path):
    """The patch must target the gateway STORE key — ``agent:{role}:{bare}``,
    lowercased (the same shape sessions.abort uses) — with the configured
    gateway WS url + token and the default ``full`` mode."""
    monkeypatch.setenv("AUTODEV_RESPONSE_USAGE", "full")
    orch, calls = _make_orch(monkeypatch, tmp_path)
    orch._preset_session_response_usage(
        "executor", "pipeline:phase-2:CORE-1:executor-attempt-1"
    )
    assert calls == [(
        "agent:executor:pipeline:phase-2:core-1:executor-attempt-1",
        "ws://gw", "gw-tok", "full",
    )]


def test_record_active_agent_triggers_preset(monkeypatch, tmp_path):
    """_record_active_agent is the chokepoint for the three phase-agent
    invocation sites — recording the active agent must pre-seed its session."""
    monkeypatch.setenv("AUTODEV_RESPONSE_USAGE", "full")
    orch, calls = _make_orch(monkeypatch, tmp_path)
    orch._record_active_agent("planner", "pipeline:phase-1:CORE-1:planner-attempt-1")
    assert calls and calls[0][0] == (
        "agent:planner:pipeline:phase-1:core-1:planner-attempt-1"
    )


def test_env_off_disables(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTODEV_RESPONSE_USAGE", "off")
    orch, calls = _make_orch(monkeypatch, tmp_path)
    orch._preset_session_response_usage("planner", "pipeline:x")
    assert calls == []


def test_env_empty_disables(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTODEV_RESPONSE_USAGE", "")
    orch, calls = _make_orch(monkeypatch, tmp_path)
    orch._preset_session_response_usage("planner", "pipeline:x")
    assert calls == []


def test_env_mode_override(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTODEV_RESPONSE_USAGE", "tokens")
    orch, calls = _make_orch(monkeypatch, tmp_path)
    orch._preset_session_response_usage("reviewer", "pipeline:x")
    assert calls[0][3] == "tokens"


def test_preset_failure_never_raises(monkeypatch, tmp_path):
    """Best-effort contract: a gateway failure must not block the invocation."""
    monkeypatch.setenv("AUTODEV_RESPONSE_USAGE", "full")
    orch, _ = _make_orch(monkeypatch, tmp_path)

    def _boom(*a, **k):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(orch_mod, "set_session_response_usage", _boom)
    orch._preset_session_response_usage("executor", "pipeline:x")  # must not raise
