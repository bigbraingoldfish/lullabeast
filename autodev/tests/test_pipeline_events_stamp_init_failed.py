"""Section 6.1.d + F10(a) — stamp_init_failed event and escalation routing.

``_init_activity_stamp_or_escalate`` (formerly ``_init_activity_stamp_or_halt``)
emits a ``stamp_init_failed`` event and, when the workspace dir is missing or
unwritable, **routes to the escalation agent** (sets ``current_agent =
"escalation"`` + ``transition_state("RUNNING", …)``) so the operator is notified
(advisory + Signal via the escalation agent) and can answer from the dashboard.
The three call sites ``continue`` the main loop on a False return so the next
iteration fires the escalation dispatch.

Before F10(a) the helper dead-ended at ``HALTED_SILENT`` with only the event —
a silent halt with no notification and no recovery short of git-recover. These
tests pin both the event emission AND the escalation routing so neither
regresses.
"""

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402

_ORCH_SRC = open(
    os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8"
).read()


def _helper_body():
    """Return the source of the stamp-init helper method body."""
    method_idx = _ORCH_SRC.find("def _init_activity_stamp_or_escalate")
    assert method_idx != -1, (
        "Could not find _init_activity_stamp_or_escalate method (was it renamed "
        "back to _init_activity_stamp_or_halt?)"
    )
    next_def = _ORCH_SRC.find("\n    def ", method_idx + 1)
    return _ORCH_SRC[method_idx : next_def if next_def != -1 else method_idx + 3000]


def test_stamp_init_failed_event_emitted_on_failure():
    """``_init_activity_stamp_or_escalate`` must emit ``stamp_init_failed``
    before routing to escalation so the UI can render the failure cause
    distinctly in the activity feed."""
    method_body = _helper_body()
    pat = re.compile(r'_write_pipeline_event\(\s*["\']stamp_init_failed["\']')
    assert pat.search(method_body), (
        "_init_activity_stamp_or_escalate must emit 'stamp_init_failed' event "
        "on the False-return path so the UI surfaces the cause"
    )


def test_stamp_init_failed_event_detail_includes_agent_and_stamp_path():
    """Detail must include ``agent_role`` and ``stamp_path`` — exactly
    what the operator needs to fix the underlying workspace issue."""
    method_body = _helper_body()
    for field in ("agent_role", "stamp_path"):
        pat = re.compile(
            r'_write_pipeline_event\(\s*["\']stamp_init_failed["\'][\s\S]{0,500}?'
            + field
        )
        assert pat.search(method_body), (
            f"stamp_init_failed detail must include {field!r}"
        )


def test_stamp_init_routes_to_escalation():
    """F10(a): on a stamp-init failure the helper must route to the escalation
    agent (set ``current_agent = "escalation"`` + ``transition_state("RUNNING",
    …)``) — the only mechanism that notifies the operator — and must NOT
    dead-end at the old silent ``HALTED_SILENT``.

    Catches a regression to the silent-halt shape, which left the operator with
    no notification and no recovery short of the phase-destroying git-recover."""
    body = _helper_body()
    assert re.search(r'current_agent"\]\s*=\s*"escalation"', body), (
        "the helper must set current_agent = 'escalation' on the failure path "
        "so the next loop iteration fires the escalation dispatch"
    )
    assert re.search(r'transition_state\(\s*"RUNNING"', body), (
        "the helper must transition_state('RUNNING', …) so the escalation "
        "branch is reached (its honest reason becomes escalation_trigger_reason)"
    )
    assert not re.search(r'transition_state\(\s*"HALTED_SILENT"', body), (
        "the helper must no longer transition to HALTED_SILENT — F10(a) routes "
        "stamp-init failures to escalation instead"
    )


def test_stamp_init_call_sites_continue():
    """The three call sites (planner, executor, reviewer) must ``continue`` the
    main loop after a False result so the escalation routed by the helper is
    fired on the next iteration. A ``return`` would exit the orchestrator
    process at the old silent halt — exactly the regression to guard against."""
    pat = re.compile(
        r'self\._init_activity_stamp_or_escalate\(\s*["\'](\w+)["\']\s*\)'
        r"\s*\n\s*if not _stamp_ok:"
        r"(?:\s*\n\s*#[^\n]*)*"  # tolerate explanatory comment lines
        r"\s*\n\s*(continue|return)\b"
    )
    matches = pat.findall(_ORCH_SRC)
    assert len(matches) >= 3, (
        f"expected >=3 call sites of _init_activity_stamp_or_escalate guarded by "
        f"`if not _stamp_ok:`, found {len(matches)}: {matches}"
    )
    for role, verb in matches:
        assert verb == "continue", (
            f"call site for {role!r} must `continue` (found {verb!r}) so the "
            f"escalation routed by the helper fires on the next loop iteration "
            f"instead of exiting the process"
        )
