"""Executor retry exhaustion escalates directly — blame attribution removed.

The three-layer blame attribution system (LLM analyst → heuristics → impl
default) is removed. Rationale, verified against the pre-removal source:

  - The impl-blame "self-heal retry" was structurally inert: gate failures
    increment ``executor_retries`` via ``reset_execution("auto")``, so at
    ``retries >= 3`` an impl verdict's ``reset_execution("auto")`` incremented
    the counter PAST the gate (3→4) and the next loop iteration re-entered the
    blame block without ever re-running the executor. Production behavior: up
    to three sequential 60s LLM calls on the identical ``failure_context.json``,
    then escalation anyway.
  - The analyst collapsed precise ``gate_error_codes`` into a vaguer 3-way
    label biased toward "impl" by its own prompt; ``lessons.md`` ``[BLAME]``
    lines were write-only.
  - The direct llama-server POST was the orchestrator's only LLM call,
    hard-wiring a local-GPU dependency outside OpenClaw's provider layer.

New contract: executor exhaustion routes to the escalation agent immediately,
mirroring the planner-exhaustion pattern, with an honest action string carrying
the last gate error code. The EX-RR surviving-output salvage check still runs
first. The operator routes plan-vs-impl from the dashboard (RESET_PHASE /
RESET_EXECUTION), which grants a properly-reset fresh budget.

Pattern: source-inspection guards for the in-``run()`` block (idiom from
``test_orchestrator_executor_rr_guard.py``) + API-level absence checks.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

ORCHESTRATOR_PATH = os.path.join(PIPELINE_DIR, "orchestrator.py")

EXHAUSTION_MARKER = "Executor retries exhausted. Escalating."


def _source() -> str:
    with open(ORCHESTRATOR_PATH, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Blame attribution is fully removed (no stubs, no dangling references)
# ---------------------------------------------------------------------------


def test_blame_attribution_method_removed():
    """run_blame_attribution and its inner helpers must not exist anywhere."""
    src = _source()
    for marker in (
        "run_blame_attribution",
        "_append_blame_log",
        "_record_blame_attribution",
    ):
        assert marker not in src, (
            f"{marker!r} still present in orchestrator.py — blame attribution "
            "must be removed in full, not stubbed."
        )

    import orchestrator as orc_module

    assert not hasattr(orc_module.Orchestrator, "run_blame_attribution")


def test_no_blame_state_or_metrics_fields_remain():
    """No writer-side blame fields may remain: phase_state (blame_verdict /
    blame_context / blame_fires / prior_blame_attributions), the canonical
    metrics row, the run summary (blame_fires_total / blame_attributions),
    and the failure_context schema must all drop them. Historical rows on
    disk stay readable by the UI; the orchestrator just stops producing them."""
    src = _source()
    for marker in (
        "blame_fires",
        "blame_verdict",
        "blame_context",
        "blame_attributions",
        "prior_blame_attributions",
    ):
        assert marker not in src, (
            f"{marker!r} still present in orchestrator.py — blame data capture "
            "is replaced by gate_error_codes + escalation_trigger_reason + the "
            "agent-authored escalation_summary.json."
        )


def test_no_direct_llm_call_remains():
    """The orchestrator makes zero LLM calls: no hardcoded model name, no
    chat-completions URL resolver, no AUTODEV_LLAMA_BASE env dependency."""
    src = _source()
    for marker in (
        "qwen3.5-27b",
        "_llama_chat_completions_url",
        "_LLAMA_ORIGIN",
        "AUTODEV_LLAMA_BASE",
        "chat/completions",
    ):
        assert marker not in src, (
            f"{marker!r} still present in orchestrator.py — all LLM work runs "
            "as OpenClaw agents (models configured in agents.list[]), never as "
            "direct orchestrator HTTP calls."
        )


# ---------------------------------------------------------------------------
# The exhaustion block: EX-RR salvage → failure context → escalate
# ---------------------------------------------------------------------------


def test_exhaustion_block_escalates_directly():
    """After the exhaustion marker, the block must route to the escalation
    agent with an honest transition reason — and must NOT re-run the executor
    (no reset_execution) or call out over HTTP (no requests.post)."""
    src = _source()
    pos = src.find(EXHAUSTION_MARKER)
    assert pos != -1, (
        f"Expected marker {EXHAUSTION_MARKER!r} not found — the executor "
        "retries-exhausted block must escalate directly (planner-exhaustion "
        "pattern), not run blame attribution."
    )

    window = src[pos : pos + 1500]
    assert '"escalation"' in window or "'escalation'" in window, (
        "Exhaustion block must set current_agent to 'escalation'."
    )
    assert "transition_state" in window and "Executor retries exhausted" in window, (
        "Exhaustion block must transition with an action string the escalation "
        "dispatch copies into escalation_trigger_reason."
    )
    assert "last_error_code" in window, (
        "The transition reason must carry the last gate error code — that is "
        "the honest, deterministic signal that replaced the blame verdict."
    )
    assert "reset_execution" not in window, (
        "No hidden extra executor retries: exhaustion escalates, full stop. "
        "Fresh budgets come only from reviewer rejections or operator "
        "RESET_EXECUTION."
    )
    assert "requests.post" not in window, "No LLM/HTTP call in the exhaustion path."


def test_exhaustion_still_writes_failure_context():
    """write_failure_context must still run at exhaustion — it feeds the
    escalation agent's summary and the failure-history archive."""
    src = _source()
    pos = src.find(EXHAUSTION_MARKER)
    assert pos != -1
    window = src[pos : pos + 1500]
    assert "write_failure_context" in window, (
        "Exhaustion must write failure_context.json before escalating — the "
        "escalation agent and the dashboard read it."
    )


def test_ex_rr_salvage_precedes_escalation():
    """EX-RR: the surviving-output gate check must still appear BEFORE the
    exhaustion escalation (orphaned-session output is salvaged, not discarded)."""
    src = _source()
    pos = src.find(EXHAUSTION_MARKER)
    assert pos != -1
    window = src[max(0, pos - 2000) : pos]
    assert "run_executor_output_gate" in window, (
        "The EX-RR surviving-output check must run before escalation in the "
        "retries>=3 block (FIND-ID: EX-RR)."
    )
