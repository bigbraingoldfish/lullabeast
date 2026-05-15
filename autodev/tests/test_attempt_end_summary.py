"""Section 6.3 — per-attempt summary line + attempt_end event.

After each agent poll returns we want a single dense ``[ATTEMPT_END]``
line in ``/tmp/orchestrator.log`` plus an ``attempt_end`` event in
``pipeline_events.jsonl``.  The two channels serve different operators:

* The print line is what you ``grep '\[ATTEMPT_END\]'`` to reconstruct
  a phase's attempt history in one screen.
* The event powers the UI activity tab so the same data is visible
  without log-tailing.

These tests pin both channels at each of the three poll sites
(planner, executor, reviewer).
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


def _poll_site_window(agent: str) -> str:
    """Slice ~3000 chars around the agent's poll call (anchored on
    the Section 4 ``stall_detection_path=_{agent}_stamp`` marker)."""
    marker = f"stall_detection_path=_{agent}_stamp"
    idx = _ORCH_SRC.find(marker)
    assert idx != -1, f"Could not locate {agent} poll site"
    return _ORCH_SRC[max(0, idx - 1500) : idx + 2500]


# ---------------------------------------------------------------------------
# A1 — [ATTEMPT_END] print line at each poll site
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent", ["planner", "executor", "reviewer"])
def test_attempt_end_print_line_at_each_poll_site(agent):
    """Each poll site must emit a dense ``[ATTEMPT_END] ...`` print
    line carrying agent, attempt, reason, duration — the operator's
    one-line summary of what just happened."""
    window = _poll_site_window(agent)
    assert "[ATTEMPT_END]" in window, (
        f"{agent} poll site must emit a '[ATTEMPT_END] ...' summary "
        f"print line so operators can reconstruct attempt history with "
        f"a single grep"
    )


@pytest.mark.parametrize("agent", ["planner", "executor", "reviewer"])
def test_attempt_end_line_carries_required_fields(agent):
    """The dense line must include ``agent=``, ``attempt=``, ``reason=``,
    and ``duration=`` so it parses without a schema."""
    window = _poll_site_window(agent)
    pat = re.compile(
        r"\[ATTEMPT_END\].{0,400}?agent=.{0,200}?attempt=.{0,200}?"
        r"reason=.{0,200}?duration=",
        re.DOTALL,
    )
    assert pat.search(window), (
        f"{agent} [ATTEMPT_END] line must include agent, attempt, "
        f"reason, duration in order"
    )


# ---------------------------------------------------------------------------
# A2 — attempt_end event at each poll site
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent", ["planner", "executor", "reviewer"])
def test_attempt_end_event_emitted_at_each_poll_site(agent):
    """Companion structured event — same data the dense print carries,
    flowing through ``_write_pipeline_event`` so the UI activity tab
    surfaces it."""
    window = _poll_site_window(agent)
    pat = re.compile(r'_write_pipeline_event\(\s*["\']attempt_end["\']')
    assert pat.search(window), (
        f"{agent} poll site must emit an 'attempt_end' event so the "
        f"summary surfaces in the UI activity tab"
    )


@pytest.mark.parametrize("agent", ["planner", "executor", "reviewer"])
def test_attempt_end_event_detail_contains_reason_and_duration(agent):
    """attempt_end detail must include reason + duration_s — exactly
    what an operator needs to triage from the UI without opening logs."""
    window = _poll_site_window(agent)
    for field in ("reason", "duration_s", "attempt"):
        pat = re.compile(
            r'_write_pipeline_event\(\s*["\']attempt_end["\'][\s\S]{0,500}?'
            + field
        )
        assert pat.search(window), (
            f"{agent} attempt_end event detail must include {field!r}"
        )
