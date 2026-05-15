"""Section 6.1.c — reviewer_verdict event.

The Section 5 work added the ``[REVIEWER_GATE] verdict=...`` print line
on every reviewer-gate consumption, but no structured event.  Operators
viewing the UI activity tab can see ``gate_pass`` / ``gate_fail`` today,
but those events do not carry the routing decision (which agent fires
next) — the diagnostic the user explicitly asked for during planning.

These tests pin that the reviewer-gate consumption block emits a
``reviewer_verdict`` event alongside the existing
``[REVIEWER_GATE] verdict=...`` print, with detail containing the
verdict, pass number, and next agent so the UI can render the
correct downstream-routing arrow without parsing log lines.
"""

import os
import re
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402

_ORCH_SRC = open(
    os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8"
).read()


def _reviewer_gate_window() -> str:
    """Slice the reviewer-gate consumption block."""
    idx = _ORCH_SRC.find("run_reviewer_output_gate()")
    assert idx != -1
    # End at the escalation-agent branch (the next major block).
    end = _ORCH_SRC.find('elif current_agent == "escalation"', idx)
    if end == -1:
        end = idx + 20000
    return _ORCH_SRC[idx:end]


# ---------------------------------------------------------------------------
# E7 — reviewer_verdict event emitted on every gate consumption
# ---------------------------------------------------------------------------


def test_reviewer_verdict_event_emitted_on_every_dispatch():
    """The reviewer-gate consumption block must emit ``reviewer_verdict``
    so the UI can render routing decisions in real time.  Sits next to
    the existing ``[REVIEWER_GATE] verdict=...`` print line introduced
    in Section 5d."""
    window = _reviewer_gate_window()
    pat = re.compile(r'_write_pipeline_event\(\s*["\']reviewer_verdict["\']')
    assert pat.search(window), (
        "reviewer-gate consumption block must emit 'reviewer_verdict' "
        "event with the dispatch decision so the UI shows routing in "
        "the activity tab"
    )


# ---------------------------------------------------------------------------
# E8 — detail carries verdict + pass_number + next_agent
# ---------------------------------------------------------------------------


def test_reviewer_verdict_event_detail_contains_verdict_pass_next_agent():
    """Detail must include the three fields the UI needs:
    ``verdict`` (PASS/ROUTE_EXECUTOR/...), ``pass_number`` (1-indexed),
    and ``next_agent`` (the agent the orchestrator will invoke next).
    """
    window = _reviewer_gate_window()
    for field in ("verdict", "pass", "next_agent"):
        pat = re.compile(
            r'_write_pipeline_event\(\s*["\']reviewer_verdict["\'][\s\S]{0,800}?'
            + field
        )
        assert pat.search(window), (
            f"reviewer_verdict event detail must include {field!r} so the "
            f"UI can render routing without parsing logs"
        )
