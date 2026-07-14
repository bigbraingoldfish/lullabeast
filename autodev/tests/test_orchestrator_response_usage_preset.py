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

    def _set_usage(store_key, ws_url, token, mode="full", model=None, **k):
        calls.append((store_key, ws_url, token, mode, model))
        return True, None

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
        "ws://gw", "gw-tok", "full", None,
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
    """Best-effort contract (no model riding): a gateway failure must not block
    the invocation."""
    monkeypatch.setenv("AUTODEV_RESPONSE_USAGE", "full")
    orch, _ = _make_orch(monkeypatch, tmp_path)

    def _boom(*a, **k):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(orch_mod, "set_session_response_usage", _boom)
    assert orch._preset_session_response_usage("executor", "pipeline:x") is None


def test_preset_failure_without_model_is_benign(monkeypatch, tmp_path):
    """A rejected usage-only patch stays best-effort: the session still bakes
    the configured default, so the invocation proceeds."""
    monkeypatch.setenv("AUTODEV_RESPONSE_USAGE", "full")
    orch, _ = _make_orch(monkeypatch, tmp_path)
    monkeypatch.setattr(
        orch_mod, "set_session_response_usage",
        lambda *a, **k: (False, "gateway busy"),
    )
    assert orch._preset_session_response_usage("executor", "pipeline:x") is None


def test_preset_failure_with_model_returns_the_gateway_reason(monkeypatch, tmp_path):
    """Fatal contract: when the phase override rides the creating patch and the
    gateway rejects it, the caller gets the reason and must not invoke —
    proceeding ran the wrong model or stalled out the startup grace (observed
    live: "model not allowed" surfaced as a 601s no_first_activity)."""
    monkeypatch.setenv("AUTODEV_RESPONSE_USAGE", "full")
    orch, _ = _make_orch(monkeypatch, tmp_path)
    monkeypatch.setattr(
        orch_mod, "set_session_response_usage",
        lambda *a, **k: (False, "INVALID_REQUEST: model not allowed: local/x"),
    )
    err = orch._preset_session_response_usage("executor", "pipeline:x", model="local/x")
    assert err == "INVALID_REQUEST: model not allowed: local/x"


def test_preset_exception_with_model_returns_detail(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTODEV_RESPONSE_USAGE", "full")
    orch, _ = _make_orch(monkeypatch, tmp_path)

    def _boom(*a, **k):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(orch_mod, "set_session_response_usage", _boom)
    assert orch._preset_session_response_usage(
        "executor", "pipeline:x", model="local/x"
    ) == "gateway down"


def test_preset_threads_model_onto_the_patch(monkeypatch, tmp_path):
    """A given model must ride the session-creating patch."""
    monkeypatch.setenv("AUTODEV_RESPONSE_USAGE", "full")
    orch, calls = _make_orch(monkeypatch, tmp_path)
    orch._preset_session_response_usage(
        "executor", "pipeline:phase-2:CORE-1:executor-attempt-1",
        model="openrouter/big/strong",
    )
    assert calls[0][4] == "openrouter/big/strong"


def _stub_phase_state(orch, override):
    """Isolate the threading: fix the override the role resolves and no-op the
    phase_state read/write the models_used stamp performs."""
    orch._phase_model_override = lambda role: override
    orch._get_agent_model = lambda role: "configured/default"
    orch.read_phase_state = lambda: {}
    orch.write_phase_state_atomic = lambda ps: None


def test_record_active_agent_bakes_phase_override_as_model(monkeypatch, tmp_path):
    """The fix: a phase override must reach the session-creating patch (a
    session's model is fixed at creation), not only the later webhook."""
    monkeypatch.setenv("AUTODEV_RESPONSE_USAGE", "full")
    orch, calls = _make_orch(monkeypatch, tmp_path)
    _stub_phase_state(orch, "openrouter/big/strong")
    orch._record_active_agent("executor", "pipeline:phase-2:CORE-1:executor-attempt-1")
    assert calls and calls[0][4] == "openrouter/big/strong"


def test_record_active_agent_no_override_leaves_model_unset(monkeypatch, tmp_path):
    """No override -> model unset, so the entry bakes the configured default
    (the pre-fix behavior for the common no-override case is preserved)."""
    monkeypatch.setenv("AUTODEV_RESPONSE_USAGE", "full")
    orch, calls = _make_orch(monkeypatch, tmp_path)
    _stub_phase_state(orch, None)
    orch._record_active_agent("planner", "pipeline:phase-1:CORE-1:planner-attempt-1")
    assert calls and calls[0][4] is None


def test_record_active_agent_surfaces_a_rejected_override(monkeypatch, tmp_path):
    """The invoke sites branch to escalation on this return value; a baked
    override that the gateway refuses must not report success."""
    monkeypatch.setenv("AUTODEV_RESPONSE_USAGE", "full")
    orch, _ = _make_orch(monkeypatch, tmp_path)
    _stub_phase_state(orch, "local/not-allowed")
    monkeypatch.setattr(
        orch_mod, "set_session_response_usage",
        lambda *a, **k: (False, "INVALID_REQUEST: model not allowed: local/not-allowed"),
    )
    err = orch._record_active_agent("executor", "pipeline:phase-2:CORE-1:executor-attempt-1")
    assert err == "INVALID_REQUEST: model not allowed: local/not-allowed"


def test_record_active_agent_ok_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTODEV_RESPONSE_USAGE", "full")
    orch, calls = _make_orch(monkeypatch, tmp_path)
    _stub_phase_state(orch, "openrouter/big/strong")
    assert orch._record_active_agent(
        "executor", "pipeline:phase-2:CORE-1:executor-attempt-1"
    ) is None
    assert calls


def test_escalate_model_override_rejected_routes_to_escalation(monkeypatch, tmp_path):
    """State effects: ERR_MODEL_OVERRIDE_REJECTED + the gateway reason land in
    phase_state, current_agent flips to escalation, and the retry counter is
    untouched (config problem, not an agent failure)."""
    orch, _ = _make_orch(monkeypatch, tmp_path)
    written = {}
    orch.read_phase_state = lambda: {"executor_retries": 1}
    orch.write_phase_state_atomic = lambda ps: written.update(ps)
    transitions = []
    orch.transition_state = lambda status, reason: transitions.append((status, reason))

    assert orch._escalate_model_override_rejected(
        "executor", "INVALID_REQUEST: model not allowed: local/x"
    ) is True
    assert written["last_error_code"] == orch_mod.ERR_MODEL_OVERRIDE_REJECTED
    assert "model not allowed: local/x" in written["escalation_trigger_reason"]
    assert "Clear or change the phase model override" in written["escalation_trigger_reason"]
    assert written["executor_retries"] == 1
    assert orch.state["current_agent"] == "escalation"
    assert transitions and transitions[0][0] == "RUNNING"
    assert "ERR_MODEL_OVERRIDE_REJECTED (executor)" in transitions[0][1]


def test_trigger_class_derivation_covers_override_rejection():
    assert orch_mod._derive_escalation_trigger_class(
        orch_mod.ERR_MODEL_OVERRIDE_REJECTED
    ) == "model_override_rejected"
    assert "model_override_rejected" in orch_mod.ESCALATION_TRIGGER_CLASSES
