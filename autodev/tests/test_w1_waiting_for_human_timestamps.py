"""
W1-E: waiting_for_human_at and waiting_for_human_resolved_at timestamps.

Tests verify that phase_state.json receives these timestamps at both
WAITING_FOR_HUMAN escalation sites, and when the command is resolved.

Pattern: source-text presence tests matching existing codebase style.
"""
import pathlib

_ORCH = pathlib.Path(__file__).parent.parent / "pipeline" / "orchestrator.py"
_SRC = _ORCH.read_text()
_LINES = _SRC.splitlines()


def _find_transition_waiting_for_human_linenos():
    """Return line numbers (1-based) of transition_state("WAITING_FOR_HUMAN" calls."""
    return [
        i for i, ln in enumerate(_LINES, 1)
        if 'transition_state("WAITING_FOR_HUMAN"' in ln
        or "transition_state('WAITING_FOR_HUMAN'" in ln
    ]


def test_waiting_for_human_at_key_present_in_source():
    """'waiting_for_human_at' must appear in orchestrator source."""
    assert "waiting_for_human_at" in _SRC, (
        "waiting_for_human_at not found in orchestrator. "
        "Add it to phase_state before each WAITING_FOR_HUMAN transition."
    )


def test_waiting_for_human_at_written_at_both_escalation_sites():
    """waiting_for_human_at must appear at least twice — once per WAITING_FOR_HUMAN site."""
    count = _SRC.count('"waiting_for_human_at"')
    # Accept single-quote form too
    count += _SRC.count("'waiting_for_human_at'")
    assert count >= 2, (
        f"'waiting_for_human_at' appears only {count} time(s) in orchestrator source — "
        "expected >= 2 (one per WAITING_FOR_HUMAN escalation site). "
        "Add the timestamp write at the repo-init and main escalation paths."
    )


def test_waiting_for_human_at_written_before_each_transition():
    """waiting_for_human_at assignment must appear BEFORE each WAITING_FOR_HUMAN transition."""
    wfh_transitions = _find_transition_waiting_for_human_linenos()
    assert len(wfh_transitions) >= 2, (
        f"Expected >= 2 transition_state('WAITING_FOR_HUMAN') sites, "
        f"found {len(wfh_transitions)}."
    )
    for transition_lineno in wfh_transitions:
        # Scan up to 20 lines before transition for the timestamp assignment
        start = max(0, transition_lineno - 20)
        window = _LINES[start : transition_lineno - 1]
        timestamp_writes = [
            ln for ln in window
            if "waiting_for_human_at" in ln and ("=" in ln or "strftime" in ln)
        ]
        assert timestamp_writes, (
            f"No 'waiting_for_human_at' assignment found within 20 lines before "
            f"transition_state('WAITING_FOR_HUMAN') at line {transition_lineno}. "
            "Write the timestamp into phase_state before every WAITING_FOR_HUMAN transition."
        )


def test_waiting_for_human_resolved_at_key_present_in_source():
    """'waiting_for_human_resolved_at' must appear in orchestrator source."""
    assert "waiting_for_human_resolved_at" in _SRC, (
        "waiting_for_human_resolved_at not found in orchestrator. "
        "Add it to phase_state when escalation_output.json is consumed."
    )


def test_waiting_for_human_resolved_at_written_near_escalation_resolution():
    """resolved_at must appear within 30 lines of the escalation output read."""
    # Find the escalation resolution site — where out_path is consumed
    esc_read_linenos = [
        i for i, ln in enumerate(_LINES, 1)
        if "_poll_escalation_output_json_path" in ln
    ]
    assert esc_read_linenos, "Could not find _poll_escalation_output_json_path call."

    for read_lineno in esc_read_linenos:
        window = _LINES[read_lineno - 1 : read_lineno + 30]
        resolved_writes = [
            ln for ln in window
            if "waiting_for_human_resolved_at" in ln
        ]
        if resolved_writes:
            return  # found at least one resolution site

    raise AssertionError(
        "No 'waiting_for_human_resolved_at' found within 30 lines after "
        "_poll_escalation_output_json_path call. "
        "Write the resolved timestamp into phase_state when the command is received."
    )
