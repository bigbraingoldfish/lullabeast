"""P1 Stage D — orchestrator handler for BEHAVIORAL_UNVERIFIED (pooled-counter form).

Stage D consolidates the three contract-shape handlers (VISUAL_UNVERIFIED,
BEHAVIORAL_UNVERIFIED, REGRESSION_UNVERIFIED) into a single parameterised
``elif gate_result in (...):`` branch driving a pooled
``reviewer_unverified_retries`` counter (cap 2 across all three).

Previously (P0 Stage F), this file pinned a per-flavour
``reviewer_behavioral_retries`` counter against a dedicated handler. Both
the counter and the dedicated handler are GONE after Stage D — those
assertions were tests of dead behaviour, so they have been replaced with
assertions on the unified shape.

Filename kept so operators searching for "behavioral" still find this surface.
"""

import os
import re

import pytest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")

_ORCH_PATH = os.path.join(PIPELINE_DIR, "orchestrator.py")
with open(_ORCH_PATH, "r", encoding="utf-8") as _f:
    _ORCH_SRC = _f.read()


def _unverified_handler_window():
    """Return the slice of orchestrator.py containing the parameterised
    UNVERIFIED handler (the consolidated branch covering all three
    contract-shape verdicts).

    Pre-Stage-D this returned the BEHAVIORAL_UNVERIFIED-only handler; the
    consolidation means the handler is now a single tuple-dispatched branch."""
    pat = re.compile(r"elif gate_result in \(")
    m = pat.search(_ORCH_SRC)
    assert m is not None, (
        "orchestrator.py must contain a parameterised UNVERIFIED handler "
        "(`elif gate_result in (...):`) covering BEHAVIORAL_UNVERIFIED and "
        "its sibling contract-shape verdicts. The per-flavour "
        "BEHAVIORAL_UNVERIFIED handler from P0 Stage F was deleted in P1 "
        "Stage D's consolidation."
    )
    start_idx = m.start()
    next_elif = _ORCH_SRC.find("elif gate_result ==", start_idx + 10)
    next_else = _ORCH_SRC.find("\n                    else:", start_idx + 10)
    candidates = [c for c in (next_elif, next_else) if c != -1]
    end_idx = min(candidates) if candidates else start_idx + 5000
    return _ORCH_SRC[start_idx:end_idx]


def test_behavioral_unverified_handled_by_unified_handler():
    """BEHAVIORAL_UNVERIFIED must be one of the verdicts dispatched by the
    parameterised handler. Pre-Stage-D this was a per-flavour handler; after
    Stage D it shares the consolidated branch with VISUAL_UNVERIFIED and
    REGRESSION_UNVERIFIED."""
    window = _unverified_handler_window()
    header = window.split(":", 1)[0]
    assert '"BEHAVIORAL_UNVERIFIED"' in header, (
        f"BEHAVIORAL_UNVERIFIED must appear in the parameterised handler's "
        f"verdict tuple. Header was: {header!r}"
    )


def test_behavioral_unverified_uses_pooled_counter_not_per_flavour():
    """The handler must use the pooled ``reviewer_unverified_retries`` counter,
    NOT the deleted ``reviewer_behavioral_retries`` per-flavour counter.

    Pre-Stage-D contract pinned a per-flavour counter. Stage D's
    consolidation pools all three contract-shape verdicts onto one counter
    — keeping the per-flavour counter would be sprawl."""
    window = _unverified_handler_window()
    assert "reviewer_unverified_retries" in window, (
        "Parameterised handler must use the pooled "
        "reviewer_unverified_retries counter — without this, the three "
        "contract-shape verdicts cannot share a budget and the consolidation "
        "is incomplete."
    )
    assert "reviewer_behavioral_retries" not in window, (
        "Per-flavour reviewer_behavioral_retries counter must NOT appear in "
        "the consolidated handler — that's the dead behaviour the "
        "consolidation deletes."
    )


def test_unified_handler_writes_reviewer_retry_directive_to_phase_state():
    """The handler must persist a remediation instruction so the next reviewer
    invocation sees what was missing. The Phase-4 directive-channel unification
    writes the instruction to the unified ``reviewer_retry_directive`` field
    (shared with the CONTRACT_FAILURE branch and DELIVERED to the reviewer via
    the webhook ``message=`` at the invocation site). The old
    ``unverified_instruction`` field was written but never delivered — a dead
    write that left these retries blind. It must be gone."""
    window = _unverified_handler_window()
    assert "reviewer_retry_directive" in window, (
        "Parameterised handler must write the unified reviewer_retry_directive "
        "field to phase_state so the re-invoked reviewer is actually fed what "
        "was missing (delivered via message= by _invoke_reviewer)."
    )
    assert "unverified_instruction" not in window, (
        "The dead unverified_instruction field must be gone — it was written to "
        "phase_state but never delivered to the reviewer (the dead-write bug the "
        "Phase-4 directive channel retires)."
    )
    assert "behavioral_instruction" not in window, (
        "Per-flavour behavioral_instruction must NOT appear in the consolidated "
        "handler — it's the dead phase-state field Stage D removed."
    )


def test_unverified_directive_delivered_to_reviewer_via_message():
    """R-C delivery proof for the UNVERIFIED path: a reviewer_retry_directive set by
    the UNVERIFIED handler must REACH invoke_agent_webhook as ``message=`` on the next
    reviewer invocation — not merely sit unread in phase_state (the dead-write trap
    that hid the old unverified_instruction). The delivery seam ``_invoke_reviewer`` is
    shared with the CONTRACT_FAILURE branch, so this also guards the UNVERIFIED case."""
    import json
    import tempfile
    from unittest.mock import MagicMock, patch

    import orchestrator as orc_module

    with tempfile.TemporaryDirectory() as tmp:
        ps_path = os.path.join(tmp, "phase_state.json")
        with open(ps_path, "w") as f:
            json.dump(
                {"reviewer_retry_directive": "VISUAL VERIFICATION REQUIRED: ..."}, f
            )

        with (
            patch.object(orc_module, "PHASE_STATE_FILE", ps_path),
            patch.object(orc_module, "SYMLINK_TARGET", tmp),
        ):
            from orchestrator import Orchestrator

            orch = Orchestrator.__new__(Orchestrator)
            orch.lock_fd = None
            orch.openclaw_config = {"hooks": {"token": "tok"}}
            orch.state = {"current_phase": 1, "current_phase_raw_id": "UI-1"}
            orch.write_state = MagicMock()
            orch.transition_state = MagicMock()

            with patch.object(orc_module, "invoke_agent_webhook") as mock_hook:
                mock_hook.return_value = "SUCCESS"
                orch._invoke_reviewer(
                    "pipeline:phase-1:UI-1:reviewer-attempt-1", "tok"
                )

        assert mock_hook.called
        _, kwargs = mock_hook.call_args
        assert "VISUAL VERIFICATION REQUIRED" in (kwargs.get("message") or ""), (
            "the UNVERIFIED remediation directive must reach invoke_agent_webhook as "
            "message=, proving delivery rather than a dead phase_state write"
        )


def test_unified_handler_caps_pooled_counter_at_two_then_escalates():
    """Cap unchanged at 2. The pooled counter inherits the threshold the
    per-flavour design used."""
    window = _unverified_handler_window()
    cap_pat = re.compile(
        r"reviewer_unverified_retries.*?>=\s*2", re.DOTALL
    )
    assert cap_pat.search(window) or re.search(r">=\s*2", window), (
        "Parameterised handler must cap pooled retries at 2 (mirror of the "
        "per-flavour cap from P0 Stage F)."
    )
    assert "escalation" in window, (
        "Parameterised handler must route to escalation when the pooled "
        "retry cap is reached."
    )


def test_behavioral_unverified_in_verdict_routing_table():
    """The verdict-to-next-agent diagnostic dispatch table (printed via
    ``[REVIEWER_GATE] verdict=… next_agent=…``) must still include
    BEHAVIORAL_UNVERIFIED → "reviewer". The dispatch-table entry survives
    the consolidation because each verdict still needs an explicit operator-
    facing next_agent mapping."""
    pat = re.compile(r'"BEHAVIORAL_UNVERIFIED"\s*:\s*"reviewer"')
    assert pat.search(_ORCH_SRC), (
        "orchestrator.py verdict→next-agent dispatch table must include "
        '"BEHAVIORAL_UNVERIFIED": "reviewer" so the [REVIEWER_GATE] '
        "diagnostic log line names the right next_agent. The dispatch "
        "table is operator-facing and per-verdict — pooling counters does "
        "not collapse the entries."
    )


def test_unverified_handler_aborts_prior_run_before_reinvoke():
    """The UNVERIFIED handler must abort the prior reviewer run before re-invoking,
    via ``_abort_active_agent_session("reviewer_retry")``. Without it, the same-session
    re-invoke reattaches a still-streaming embedded run → concurrent "zombie" reviewer
    sessions (the bug this fix closes). The abort must NOT be gated behind a
    "still running" liveness check — it fires unconditionally on the retry path (it is
    a no-op when the run already ended, and the verdict-hold acceptor means it usually
    has), so it also catches a true zombie streaming past the accepted sentinel."""
    window = _unverified_handler_window()
    assert '_abort_active_agent_session("reviewer_retry")' in window, (
        "the UNVERIFIED handler must call "
        '_abort_active_agent_session("reviewer_retry") before re-invoking the '
        "reviewer, so the prior (possibly still-streaming) run is killed first"
    )


def test_unverified_handler_reads_unverified_detail_for_enrichment():
    """The handler must consume ``reviewer_unverified_detail`` (the gate's specific
    problem list) so the retry directive can tell the reviewer exactly what failed,
    not just the generic contract reminder."""
    window = _unverified_handler_window()
    assert "reviewer_unverified_detail" in window, (
        "the UNVERIFIED handler must read reviewer_unverified_detail to enrich the "
        "directive with the gate's specific problems"
    )


def test_reviewer_invoke_site_feeds_unverified_retries_for_fresh_session():
    """The reviewer invocation site must feed ``reviewer_unverified_retries`` into
    ``_reviewer_session_key`` so an *_UNVERIFIED retry gets a FRESH -u{N} session
    (not the prior, possibly-streaming/overflowed session). Catches the key builder
    being called without the unverified counter — which would silently revert to
    same-session reuse."""
    # the invoke-site reads the pooled counter ...
    assert 'get("reviewer_unverified_retries"' in _ORCH_SRC, (
        "the reviewer invoke site must read reviewer_unverified_retries"
    )
    # ... and passes 5 positional args to _reviewer_session_key (phase, raw_id,
    # reviewer_retries, contract_retries, unverified_retries).
    m = re.search(
        r"_reviewer_session_key\(\s*phase,\s*raw_id,\s*retries,"
        r"\s*_contract_retries,\s*_unverified_retries\s*\)",
        _ORCH_SRC,
    )
    assert m is not None, (
        "_reviewer_session_key must be called with the unverified_retries argument so "
        "*_UNVERIFIED retries rotate to a fresh -u{N} session"
    )


def _bare_orch():
    import orchestrator as orc_module

    orch = orc_module.Orchestrator.__new__(orc_module.Orchestrator)
    return orch


class TestComposeUnverifiedDirective:
    """``_compose_unverified_directive(gate_result, detail)`` builds the retry
    directive: the generic per-verdict instruction, enriched with the gate's specific
    problem list when present. Pure function — unit-tested directly (the handler that
    calls it is inline in run())."""

    def test_enriched_with_problem_list(self):
        orch = _bare_orch()
        out = orch._compose_unverified_directive(
            "BEHAVIORAL_UNVERIFIED",
            ["behavioral_verification missing or not an object"],
        )
        assert "BEHAVIORAL VERIFICATION REQUIRED" in out, "generic instruction must remain"
        assert "Specifically" in out, "enriched directive must flag the specifics"
        assert "behavioral_verification missing" in out, (
            "the gate's specific problem must be appended so the reviewer knows "
            "exactly what failed"
        )

    def test_generic_when_no_detail(self):
        orch = _bare_orch()
        for detail in (None, []):
            out = orch._compose_unverified_directive("VISUAL_UNVERIFIED", detail)
            assert "VISUAL VERIFICATION REQUIRED" in out
            assert "Specifically" not in out, (
                "with no detail the directive must be the plain generic instruction "
                "(no dangling 'Specifically:' / no crash on None)"
            )

    def test_bounded(self):
        orch = _bare_orch()
        many = [f"problem number {i} " + ("x" * 200) for i in range(10)]
        out = orch._compose_unverified_directive("REGRESSION_UNVERIFIED", many)
        # at most the first 3 problems, total appended specifics capped (~500 chars)
        assert "problem number 0" in out and "problem number 2" in out
        assert "problem number 3" not in out, "only the first 3 problems are included"
        assert len(out) < 1500, "the enriched directive must be length-bounded"
