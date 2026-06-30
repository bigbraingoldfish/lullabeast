"""Wiring guard: the tool-loop detector is attached + routed at all three poll sites.

The retry/cap/escalation behaviour itself is exercised by the existing
reviewer-timeout / executor-crash suites (a ``tool_loop`` poll result is falsy, so
it falls into each agent's self-failure path). What those suites can NOT see is
whether the new detector is actually *attached* to every poll site and the
``tool_loop`` reason is *routed* — a site left unwired would silently keep hanging
for 3 hours. This static guard (same style as ``test_error_codes.py``'s source
scan) proves each of planner/executor/reviewer:

  * reads its per-role threshold env knob,
  * passes a ``loop_detector`` built by ``_make_tool_loop_detector``,
  * calls ``_note_tool_loop`` on a ``tool_loop`` outcome, and
  * includes ``"tool_loop"`` in the ``_handle_stall_outcome`` reason tuple (so the
    live looping session is aborted before the self-failure retry).
"""
import os

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_ORCH = os.path.join(_REPO, "autodev", "pipeline", "orchestrator.py")
_N_SITES = 3  # planner, executor, reviewer


def _src():
    with open(_ORCH, encoding="utf-8") as f:
        return f.read()


def test_per_role_threshold_knobs_present():
    src = _src()
    for role in ("PLANNER", "EXECUTOR", "REVIEWER"):
        assert f"TOOL_LOOP_REPEAT_LIMIT_{role}" in src, f"missing knob for {role}"


def test_loop_detector_attached_at_each_site():
    src = _src()
    # Each site builds its loop_detector via the shared _maybe_tool_loop_detector
    # chokepoint (which applies the per-role threshold + 0-disable, then delegates
    # to _make_tool_loop_detector).
    assert src.count("self._maybe_tool_loop_detector(") >= _N_SITES


def test_note_tool_loop_routed_at_each_site():
    src = _src()
    assert src.count("self._note_tool_loop(") >= _N_SITES
    # the detection branch keys on the new reason:
    assert src.count('== "tool_loop"') >= _N_SITES


def test_tool_loop_in_stall_abort_reason_tuple_at_each_site():
    """tool_loop must be in each site's _handle_stall_outcome reason set so the live
    looping session is steer-aborted before the self-failure retry re-invokes."""
    src = _src()
    # tuple entries carry a trailing comma; the detection-branch comparisons do not.
    assert src.count('"tool_loop",') >= _N_SITES
