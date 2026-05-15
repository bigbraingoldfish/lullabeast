"""Section 6.1.a — poll_start and poll_outcome events.

The Section 0–5 work added ``[POLL][CONFIG]`` and stall/no_first_activity
print lines, but nothing structured.  The UI activity feed reads
``pipeline_events.jsonl`` via SSE; without event-emission alongside the
prints, operators cannot see poll lifecycle events in the dashboard.

These tests pin that every agent poll site emits two events:

* ``poll_start`` — before ``poll_for_sentinel`` is invoked, with the
  effective thresholds and session_key.
* ``poll_outcome`` — after the poll returns, with the ``PollResult``
  reason (succeeded/stalled/no_first_activity/timeout/stopped) so the
  activity tab can render the right colour without parsing log lines.
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
    """Slice the source around the agent's poll call uniquely.

    Anchors on ``stall_detection_path=_{agent}_stamp`` (introduced in
    Section 4) which exists exactly once per agent.  Returns ~3000
    characters around that line — enough to cover the [POLL][CONFIG]
    print before the poll and the outcome-handling block after.
    """
    marker = f"stall_detection_path=_{agent}_stamp"
    idx = _ORCH_SRC.find(marker)
    assert idx != -1, f"Could not locate {agent} poll site"
    return _ORCH_SRC[max(0, idx - 1500) : idx + 1500]


# ---------------------------------------------------------------------------
# E1 — poll_start event emitted before poll_for_sentinel at each site
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent", ["planner", "executor", "reviewer"])
def test_poll_start_event_emitted_at_each_poll_site(agent):
    """Each agent's poll site must call
    ``_write_pipeline_event("poll_start", ...)`` so the UI SSE feed
    surfaces the poll lifecycle.  Reuses the existing helper so no new
    infrastructure is needed."""
    window = _poll_site_window(agent)
    # Match either single or double quotes around the event name.
    pat = re.compile(
        r'_write_pipeline_event\(\s*["\']poll_start["\']'
    )
    assert pat.search(window), (
        f"{agent} poll site must emit a 'poll_start' event so the UI "
        f"activity tab can render the poll lifecycle without parsing log "
        f"lines"
    )


# ---------------------------------------------------------------------------
# E2 — poll_outcome event emitted after poll_for_sentinel returns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent", ["planner", "executor", "reviewer"])
def test_poll_outcome_event_emitted_at_each_poll_site(agent):
    """Each agent's poll site must emit a 'poll_outcome' event with the
    PollResult reason so the UI shows succeeded/stalled/no_first_activity
    /timeout/stopped without parsing log lines."""
    window = _poll_site_window(agent)
    pat = re.compile(
        r'_write_pipeline_event\(\s*["\']poll_outcome["\']'
    )
    assert pat.search(window), (
        f"{agent} poll site must emit a 'poll_outcome' event after "
        f"poll_for_sentinel returns"
    )


# ---------------------------------------------------------------------------
# E3 — events carry the expected detail fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent", ["planner", "executor", "reviewer"])
def test_poll_start_detail_contains_thresholds_and_session_key(agent):
    """poll_start detail must include startup_grace, stall_threshold,
    infra_backstop, session_key — the operational context an operator
    needs to reason about the poll without reading state files."""
    window = _poll_site_window(agent)
    pat = re.compile(
        r'_write_pipeline_event\(\s*["\']poll_start["\'][\s\S]{0,400}?'
        r'(startup_grace|stall_threshold|infra_backstop)',
    )
    assert pat.search(window), (
        f"{agent} poll_start event detail must include at least one of "
        f"startup_grace / stall_threshold / infra_backstop so operators "
        f"see the effective thresholds in the UI"
    )
    pat_sk = re.compile(
        r'_write_pipeline_event\(\s*["\']poll_start["\'][\s\S]{0,400}?'
        r'session_key',
    )
    assert pat_sk.search(window), (
        f"{agent} poll_start event detail must include session_key for "
        f"correlation with OpenClaw session records"
    )


@pytest.mark.parametrize("agent", ["planner", "executor", "reviewer"])
def test_poll_outcome_detail_contains_reason(agent):
    """poll_outcome detail must include the PollResult.reason field —
    the entire point of this event is exposing that signal to the UI."""
    window = _poll_site_window(agent)
    pat = re.compile(
        r'_write_pipeline_event\(\s*["\']poll_outcome["\'][\s\S]{0,400}?'
        r'reason',
    )
    assert pat.search(window), (
        f"{agent} poll_outcome event detail must include the PollResult.reason"
    )
