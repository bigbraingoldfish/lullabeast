"""Escalation advisory is agent-owned — orchestrator makes no LLM call.

The orchestrator's synchronous qwen advisory call is removed. The escalation
agent — already equipped with the ``escalation-summary`` skill and invoked via
OpenClaw at every escalation — composes the ``{summary, recommended_action}``
advisory itself and writes ``escalation_summary.json`` BEFORE notifying the
operator. The orchestrator:

  1. records an honest deterministic ``escalation_message`` immediately
     (``_record_escalation_reason`` → ``_compose_fallback_reason``,
     status="fallback") so the dashboard never shows an empty panel or an
     indefinite loader;
  2. clears any stale ``escalation_summary.json`` at dispatch
     (``_clear_stale_escalation_summary``) so a summary from a previous
     escalation — or a previous project, after a queue advance repointed the
     symlink — can never be promoted as if it described the current failure;
  3. promotes the agent-written summary into phase_state as soon as it lands
     (``_promote_agent_escalation_summary``, called from the WAITING_FOR_HUMAN
     poll loop and at resolution), flipping the status to "ready" so the
     dashboard upgrades in place.

The "generating" advisory status is retired: nothing synchronous remains to
spin on, and a status that only an in-process wait can clear would strand the
panel on a queue auto-advance.

Pattern: unit tests on the extracted helpers + source-inspection guards for
the in-``run()`` wiring (idiom from ``test_escalation_advisory_loader.py``).
"""

import json
import os
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

ORCHESTRATOR_PATH = os.path.join(PIPELINE_DIR, "orchestrator.py")
WEBHOOK_CLIENT_PATH = os.path.join(PIPELINE_DIR, "webhook_client.py")


def _source(path=ORCHESTRATOR_PATH) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _slice(src, start_sub, end_sub):
    start = src.find(start_sub)
    assert start != -1, f"anchor not found: {start_sub!r}"
    end = src.find(end_sub, start)
    assert end != -1, f"end anchor not found after {start_sub!r}: {end_sub!r}"
    return src[start:end]


# ---------------------------------------------------------------------------
# Bare-orch helper (mirrors test_escalation_advisory_loader._bare_orch)
# ---------------------------------------------------------------------------


def _bare_orch(monkeypatch, tmp_dir):
    """Bare Orchestrator with artifacts + phase_state wired to tmp_dir, and
    requests.post booby-trapped — these paths must never make an HTTP call."""
    import orchestrator as orc_module

    ps_path = os.path.join(tmp_dir, "phase_state.json")
    monkeypatch.setattr(orc_module, "PHASE_STATE_FILE", ps_path)
    monkeypatch.setattr(orc_module, "PROJECT_ARTIFACTS_DIR", str(tmp_dir))

    def _no_http(*args, **kwargs):  # pragma: no cover - failure path
        raise AssertionError("orchestrator must not make HTTP calls on the advisory path")

    monkeypatch.setattr(orc_module.requests, "post", _no_http)

    orch = orc_module.Orchestrator.__new__(orc_module.Orchestrator)
    orch.lock_fd = None
    orch.openclaw_config = {"hooks": {"token": "t"}}
    orch.state = {"current_phase": 1, "current_phase_raw_id": "CORE-1"}
    orch.write_state = MagicMock()
    orch.transition_state = MagicMock()
    return orch, ps_path


def _write_summary(tmp_dir, summary="Executor hit ERR_X three times.",
                   action="Use Reset Execution."):
    path = os.path.join(tmp_dir, "escalation_summary.json")
    with open(path, "w") as f:
        json.dump({"summary": summary, "recommended_action": action}, f)
    return path


# ---------------------------------------------------------------------------
# Old LLM machinery is gone; new helpers exist
# ---------------------------------------------------------------------------


def test_old_llm_advisory_methods_removed():
    src = _source()
    for gone in (
        "def _generate_escalation_advisory",
        "def _generate_and_record_advisory",
        "def _llama_chat_completions_url",
        "def _read_escalation_summary",  # legacy zero-caller reader
    ):
        assert gone not in src, f"{gone!r} must be removed, not left as dead code"
    for kept in (
        "def _compose_fallback_reason",
        "def _read_escalation_advisory",
        "def _record_escalation_reason",
        "def _clear_stale_escalation_summary",
        "def _promote_agent_escalation_summary",
        "def _build_escalation_webhook_message",
    ):
        assert kept in src, f"{kept!r} missing — required by the agent-owned advisory design"


# ---------------------------------------------------------------------------
# _record_escalation_reason — honest fallback, immediately, no HTTP
# ---------------------------------------------------------------------------


class TestRecordEscalationReason:

    def test_sets_fallback_immediately_without_http(self, monkeypatch, tmp_path):
        orch, ps_path = _bare_orch(monkeypatch, tmp_path)
        ps = {
            "last_error_code": "ERR_TESTS_FAILING",
            "escalation_trigger_reason": "Executor retries exhausted after 3 attempts",
        }
        orch._record_escalation_reason(ps)  # booby-trapped requests.post must not fire
        assert ps["escalation_advisory_status"] == "fallback"
        assert ps.get("escalation_message"), "fallback must populate escalation_message"
        assert "log" in ps["escalation_message"].lower()
        # Persisted atomically for the dashboard.
        with open(ps_path) as f:
            on_disk = json.load(f)
        assert on_disk["escalation_advisory_status"] == "fallback"
        assert on_disk["escalation_message"] == ps["escalation_message"]

    def test_never_raises_on_empty_state(self, monkeypatch, tmp_path):
        orch, _ = _bare_orch(monkeypatch, tmp_path)
        ps = {}
        orch._record_escalation_reason(ps)
        assert ps["escalation_advisory_status"] == "fallback"
        assert ps.get("escalation_message")


# ---------------------------------------------------------------------------
# _clear_stale_escalation_summary — dispatch staleness guard
# ---------------------------------------------------------------------------


class TestClearStaleSummary:

    def test_removes_existing_file(self, monkeypatch, tmp_path):
        orch, _ = _bare_orch(monkeypatch, tmp_path)
        path = _write_summary(tmp_path)
        orch._clear_stale_escalation_summary()
        assert not os.path.exists(path)

    def test_noop_when_missing(self, monkeypatch, tmp_path):
        orch, _ = _bare_orch(monkeypatch, tmp_path)
        orch._clear_stale_escalation_summary()  # must not raise


# ---------------------------------------------------------------------------
# _promote_agent_escalation_summary — fallback → ready upgrade
# ---------------------------------------------------------------------------


class TestPromoteAgentSummary:

    def _seed_fallback_state(self, ps_path):
        with open(ps_path, "w") as f:
            json.dump({
                "escalation_message": "Executor failed (ERR_TESTS_FAILING). See the log.",
                "escalation_advisory_status": "fallback",
            }, f)

    def test_upgrades_fallback_to_ready(self, monkeypatch, tmp_path):
        orch, ps_path = _bare_orch(monkeypatch, tmp_path)
        self._seed_fallback_state(ps_path)
        _write_summary(tmp_path, summary="Tests fail on auth.", action="Reset Execution.")
        assert orch._promote_agent_escalation_summary() is True
        with open(ps_path) as f:
            ps = json.load(f)
        assert ps["escalation_advisory_status"] == "ready"
        assert ps["escalation_message"] == "Tests fail on auth."
        assert ps["escalation_recommended_action"] == "Reset Execution."

    def test_idempotent_once_ready(self, monkeypatch, tmp_path):
        orch, ps_path = _bare_orch(monkeypatch, tmp_path)
        self._seed_fallback_state(ps_path)
        _write_summary(tmp_path)
        assert orch._promote_agent_escalation_summary() is True
        assert orch._promote_agent_escalation_summary() is False

    def test_missing_file_keeps_fallback(self, monkeypatch, tmp_path):
        orch, ps_path = _bare_orch(monkeypatch, tmp_path)
        self._seed_fallback_state(ps_path)
        assert orch._promote_agent_escalation_summary() is False
        with open(ps_path) as f:
            assert json.load(f)["escalation_advisory_status"] == "fallback"

    def test_malformed_json_keeps_fallback_no_raise(self, monkeypatch, tmp_path):
        orch, ps_path = _bare_orch(monkeypatch, tmp_path)
        self._seed_fallback_state(ps_path)
        with open(os.path.join(tmp_path, "escalation_summary.json"), "w") as f:
            f.write("{not valid json")
        assert orch._promote_agent_escalation_summary() is False
        with open(ps_path) as f:
            assert json.load(f)["escalation_advisory_status"] == "fallback"

    def test_empty_summary_keeps_fallback(self, monkeypatch, tmp_path):
        orch, ps_path = _bare_orch(monkeypatch, tmp_path)
        self._seed_fallback_state(ps_path)
        _write_summary(tmp_path, summary="   ", action="x")
        assert orch._promote_agent_escalation_summary() is False


# ---------------------------------------------------------------------------
# _build_escalation_webhook_message — the agent's marching orders
# ---------------------------------------------------------------------------


class TestWebhookMessage:

    def test_instructs_summary_write_before_notify(self, monkeypatch, tmp_path):
        orch, _ = _bare_orch(monkeypatch, tmp_path)
        msg = orch._build_escalation_webhook_message()
        assert "TRUSTED control invocation" in msg
        assert "escalation_summary.json" in msg
        # Write must precede the operator notification.
        assert msg.find("escalation_summary.json") < msg.find("NOTIFY"), (
            "the agent must write the summary file BEFORE notifying the operator"
        )
        assert "BEFORE" in msg
        assert "escalation_output" in msg  # still NOTIFY-only (F13)
        # Reads use the resolved absolute path so they survive a queue-advance
        # symlink repoint; writes go through the workspace symlink (sandbox).
        assert os.path.realpath(str(tmp_path)) in msg
        # No pre-computed advisory is embedded any more.
        assert "Advisory:" not in msg

    def test_used_by_both_dispatch_sites(self):
        src = _source()
        assert src.count("self._build_escalation_webhook_message()") >= 2, (
            "both the main dispatch and the repo-init dispatch must share the "
            "single message builder so the two cannot drift"
        )


# ---------------------------------------------------------------------------
# Dispatch-site wiring (source-inspection; in-run() blocks are not extractable)
# ---------------------------------------------------------------------------


class TestDispatchWiring:

    def test_main_dispatch_agent_owned(self):
        src = _source()
        block = _slice(
            src,
            'f"pipeline:phase-{phase}:{raw_id}:escalation"',
            "webhook_status = invoke_agent_webhook",
        )
        assert "_record_escalation_reason" in block
        assert "_clear_stale_escalation_summary" in block
        assert '"generating"' not in block, (
            "the 'generating' advisory status is retired — the honest fallback "
            "message is recorded immediately instead"
        )
        assert "_generate_and_record_advisory" not in block

    def test_repo_init_dispatch_agent_owned(self):
        src = _source()
        block = _slice(
            src,
            ':repo-init-failure"',
            "webhook_status = invoke_agent_webhook",
        )
        assert "_record_escalation_reason" in block
        assert "_clear_stale_escalation_summary" in block
        assert '"generating"' not in block
        assert "_generate_and_record_advisory" not in block

    def test_crash_site_agent_owned(self):
        src = _source()
        block = _slice(
            src,
            "Escalated after unhandled exception",
            "except Exception as escalation_err",
        )
        assert "_record_escalation_reason" in block
        assert '"generating"' not in block
        assert "_generate_and_record_advisory" not in block

    def test_poll_loop_promotes_during_wait(self):
        """The WAITING_FOR_HUMAN poll arm must attempt promotion on every
        iteration — the dashboard upgrades within one poll cycle of the agent
        writing its summary, not only when the operator finally answers."""
        src = _source()
        anchor = "_poll_escalation_output_json_path(timeout_seconds=10)"
        pos = src.find(anchor)
        assert pos != -1, "WAITING_FOR_HUMAN poll site not found"
        window = src[max(0, pos - 1500) : pos + 1500]
        assert "_promote_agent_escalation_summary" in window, (
            "the poll loop must call _promote_agent_escalation_summary so a "
            "landed summary upgrades the dashboard without waiting for the "
            "operator command"
        )


# ---------------------------------------------------------------------------
# webhook_client default message — crash path parity
# ---------------------------------------------------------------------------


def test_webhook_client_default_escalation_message_agent_owned():
    """The crash-handler webhook fires with no message kwarg, so the
    default escalation message in webhook_client.py must carry the same
    compose-and-write instruction."""
    src = _source(WEBHOOK_CLIENT_PATH)
    assert "escalation_summary.json" in src, (
        "webhook_client.py default escalation message must instruct the agent "
        "to write escalation_summary.json before notifying the operator"
    )
