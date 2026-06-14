"""Issue 1 (wart) — an operator stop mid-planner/mid-reviewer must not burn a retry.

``poll_for_sentinel`` returns ``PollResult(False, "stopped")`` when the stop
sentinel is on disk. That result is falsy and is NOT in the stall set
(``stalled``/``no_first_activity``/``timeout``), so before this fix the planner
and reviewer polls fell into ``if not sentinel_found:`` and called
``increment_{planner,reviewer}_retries()`` — spuriously burning a retry, and in
the ``retries == 2`` edge flipping ``current_agent`` to "escalation" right before
the loop-top consumed the stop (which would then make a resume restore
"escalation" instead of the real agent). The executor path is unaffected — its
``classify_executor_outcome`` ignores the reason.

Tested by source-structure analysis (the convention for the monolithic ``run()``
poll loop — see ``test_abort_retry_and_timeout_routing.py``; there is no harness
that drives the loop). The executor is intentionally excluded.
"""

import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ORCH_SRC = open(
    os.path.join(REPO_ROOT, "autodev", "pipeline", "orchestrator.py"), encoding="utf-8"
).read()


@pytest.mark.parametrize(
    "agent,increment",
    [
        ("planner", "increment_planner_retries"),
        ("reviewer", "increment_reviewer_retries"),
    ],
)
def test_operator_stop_short_circuits_before_retry_increment(agent, increment):
    # Anchor on the poll call, then reason about absolute offsets (no fixed-size
    # window — the reviewer's token-capture block has long lines that a char
    # window would clip).
    start = ORCH_SRC.find(f"stall_detection_path=_{agent}_stamp")
    assert start != -1, f"Could not locate the {agent} poll site"

    # The first retry-increment after the poll call — the path a stop must skip.
    incr_idx = ORCH_SRC.find(increment, start)
    assert incr_idx != -1, f"The {agent} poll site must still contain {increment}."

    # The explicit operator-stop short-circuit must appear after the poll call and
    # BEFORE that increment, so reason=='stopped' never burns a retry.
    stopped_idx = ORCH_SRC.find('== "stopped"', start)
    if stopped_idx == -1:
        stopped_idx = ORCH_SRC.find("== 'stopped'", start)
    assert stopped_idx != -1, (
        f"The {agent} poll must explicitly handle reason=='stopped' so an "
        "operator stop is not misread as a sentinel timeout."
    )
    assert stopped_idx < incr_idx, (
        f"The reason=='stopped' short-circuit must precede {increment} so a "
        "stop never burns an agent retry."
    )

    # The stopped branch must `continue` (defer the clean halt to the loop-top
    # stop check) rather than fall through into the increment.
    assert "continue" in ORCH_SRC[stopped_idx:incr_idx], (
        f"The {agent} stopped branch must 'continue' and let the loop-top "
        "_check_stop_requested() perform the clean halt."
    )
