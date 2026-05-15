"""Section 6.1.b — abort_attempted and abort_verify_failed events.

The Section 0/2 work added ``[ABORT] result=`` and ``[ABORT][VERIFY_FAILED]``
print lines but no structured events.  The UI activity feed can show
``gate_pass``/``gate_fail`` today; without abort events, operators have
to tail ``/tmp/orchestrator.log`` by hand to see whether attempt #N's
abort succeeded — the exact gap that hid the CORE-E6 attempt-#2-kept-
running incident.

These tests pin that the orchestrator emits two events:

* ``abort_attempted`` — fired after every ``abort_agent_session`` call,
  at both the retry-start site (Section 0) and the inline stall site
  (Section 2 / ``_handle_stall_outcome``).  Detail carries
  ``{session_key, result, agent_role, reason}``.
* ``abort_verify_failed`` — fired when ``verify_session_stopped`` returns
  ``False`` after a successful abort.  This is the only event the UI
  needs to render the catastrophic "still streaming despite abort"
  state in red.
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


# ---------------------------------------------------------------------------
# E4 — abort_attempted at the retry-start site
# ---------------------------------------------------------------------------


def test_abort_attempted_event_at_retry_start_site():
    """The executor retry-start abort block (Section 0a) must emit
    ``abort_attempted`` after the abort call so the UI sees the retry
    boundary in real time."""
    # Anchor on the retry-start abort site — the unique marker is
    # ``prior_attempt=`` in the [ABORT] print introduced in Section 0a.
    idx = _ORCH_SRC.find("prior_attempt=")
    assert idx != -1, "Could not find retry-start abort site"
    window = _ORCH_SRC[max(0, idx - 800) : idx + 1500]
    pat = re.compile(r'_write_pipeline_event\(\s*["\']abort_attempted["\']')
    assert pat.search(window), (
        "Retry-start abort block must emit 'abort_attempted' event "
        "after the abort_agent_session call so operators can see the "
        "retry boundary in the UI"
    )


# ---------------------------------------------------------------------------
# E5 — abort_attempted from _handle_stall_outcome (stalled/no_first_activity)
# ---------------------------------------------------------------------------


def test_abort_attempted_event_in_handle_stall_outcome():
    """``_handle_stall_outcome`` (Section 2 helper) is called from all
    three poll sites when the PollResult reason is stalled or
    no_first_activity.  It must emit ``abort_attempted`` so the UI sees
    inline stall recoveries, not just retry-start aborts.
    """
    # Find the method definition; check for the event call inside.
    method_idx = _ORCH_SRC.find("def _handle_stall_outcome")
    assert method_idx != -1, "Could not find _handle_stall_outcome method"
    # Slice to the next method start.
    next_def = _ORCH_SRC.find("\n    def ", method_idx + 1)
    method_body = _ORCH_SRC[method_idx : next_def if next_def != -1 else method_idx + 5000]
    pat = re.compile(r'_write_pipeline_event\(\s*["\']abort_attempted["\']')
    assert pat.search(method_body), (
        "_handle_stall_outcome must emit 'abort_attempted' so inline "
        "stall recoveries are visible in the UI alongside retry-start "
        "aborts"
    )


# ---------------------------------------------------------------------------
# E6 — abort_verify_failed when verify_session_stopped returns False
# ---------------------------------------------------------------------------


def test_abort_verify_failed_event_emitted_on_verify_failure():
    """When ``verify_session_stopped`` returns ``False`` (gateway
    acknowledged abort but stamp still advancing — the CORE-E6 attempt-
    #2 failure mode), the orchestrator must emit
    ``abort_verify_failed`` before transitioning to ``HALTED_SILENT``.
    The UI uses this signal to render the catastrophic state distinctly
    from a normal stall.
    """
    # Two sites do the verify check: the retry-start abort block and
    # _handle_stall_outcome.  Both must emit the event.
    sites_with_event = 0
    for marker in (
        "[ABORT][VERIFY_FAILED]",  # appears in both print lines
    ):
        idx = 0
        while True:
            idx = _ORCH_SRC.find(marker, idx)
            if idx == -1:
                break
            window = _ORCH_SRC[max(0, idx - 500) : idx + 800]
            pat = re.compile(
                r'_write_pipeline_event\(\s*["\']abort_verify_failed["\']'
            )
            if pat.search(window):
                sites_with_event += 1
            idx += len(marker)
    assert sites_with_event >= 2, (
        f"Expected 'abort_verify_failed' event near both [ABORT]"
        f"[VERIFY_FAILED] prints (retry-start + _handle_stall_outcome); "
        f"found {sites_with_event}"
    )


# ---------------------------------------------------------------------------
# E7 — events carry the expected detail fields
# ---------------------------------------------------------------------------


def test_abort_attempted_detail_includes_session_key_and_result():
    """abort_attempted detail must carry ``session_key`` and ``result``
    (ok|FAILED) so the UI can correlate with OpenClaw sessions.json
    and colour the row green/red."""
    method_idx = _ORCH_SRC.find("def _handle_stall_outcome")
    assert method_idx != -1
    next_def = _ORCH_SRC.find("\n    def ", method_idx + 1)
    method_body = _ORCH_SRC[method_idx : next_def if next_def != -1 else method_idx + 5000]
    pat = re.compile(
        r'_write_pipeline_event\(\s*["\']abort_attempted["\'][\s\S]{0,400}?session_key',
    )
    assert pat.search(method_body), (
        "abort_attempted detail must include session_key for correlation"
    )
    pat_res = re.compile(
        r'_write_pipeline_event\(\s*["\']abort_attempted["\'][\s\S]{0,400}?result',
    )
    assert pat_res.search(method_body), (
        "abort_attempted detail must include 'result' (ok/FAILED) so the "
        "UI can colour the event"
    )


def test_abort_verify_failed_detail_includes_session_key():
    """abort_verify_failed detail must carry session_key so the UI shows
    *which* attempt is still streaming."""
    # Anchor on the event name; check session_key appears in detail.
    pat = re.compile(
        r'_write_pipeline_event\(\s*["\']abort_verify_failed["\'][\s\S]{0,400}?session_key',
    )
    assert pat.search(_ORCH_SRC), (
        "abort_verify_failed detail must include session_key"
    )
