"""
W1-A, W1-B, W1-C: metrics.jsonl row observability counters.

Tests verify that blame_fires, escalations, and skill_used are wired into
the canonical metrics row, replacing the prior hardcoded-zero placeholders.

Pattern: source-text presence tests (fast, matching existing codebase style)
plus one AST check for ordering.
"""
import os
import pathlib
import ast

_ORCH = pathlib.Path(__file__).parent.parent / "pipeline" / "orchestrator.py"
_SRC = _ORCH.read_text()


# ---------------------------------------------------------------------------
# W1-A: blame_fires counter
# ---------------------------------------------------------------------------

def test_blame_fires_not_hardcoded_zero():
    """Canonical metrics row must not contain the literal 0 for blame_fires."""
    assert '"blame_fires": 0' not in _SRC, (
        "blame_fires is still hardcoded to 0 in the metrics row — "
        "wire _blame_fires counter from phase_state instead."
    )


def test_blame_fires_counter_incremented_after_attribution():
    """blame_fires must be incremented after every run_blame_attribution() call."""
    lines = _SRC.splitlines()
    blame_call_lines = [
        i for i, ln in enumerate(lines, 1)
        if "run_blame_attribution()" in ln
    ]
    assert blame_call_lines, "run_blame_attribution() not found in orchestrator source."

    # Within 10 lines after the call there must be a blame_fires increment.
    # Accept both dict-increment form (foo["blame_fires"] = ... + 1) and augmented
    # assignment (blame_fires += 1), since phase_state is a dict requiring .get().
    for call_lineno in blame_call_lines:
        window = lines[call_lineno : call_lineno + 10]
        increments = [
            l for l in window
            if "blame_fires" in l and ("+=" in l or ("+ 1" in l or "+1" in l))
        ]
        assert increments, (
            f"No blame_fires increment found within 10 lines after "
            f"run_blame_attribution() at line {call_lineno}. "
            "Add: phase_state['blame_fires'] = phase_state.get('blame_fires', 0) + 1"
        )


def test_blame_fires_sourced_from_phase_state_in_metrics_row():
    """Canonical metrics row must source blame_fires from phase_state, not a literal."""
    # Should find something like: "blame_fires": _ps_m.get("blame_fires", 0)
    # (not the literal 0 placeholder replaced with a phase_state read)
    assert "blame_fires" in _SRC
    lines = _SRC.splitlines()
    metrics_blame_lines = [
        ln for ln in lines
        if "blame_fires" in ln and '"blame_fires"' in ln and "get(" in ln
    ]
    assert metrics_blame_lines, (
        "No 'blame_fires' key sourced via .get() found in orchestrator. "
        "The metrics row should read blame_fires from phase_state, e.g.: "
        "\"blame_fires\": _ps_m.get(\"blame_fires\", 0)"
    )


# ---------------------------------------------------------------------------
# W1-B: escalations counter
# ---------------------------------------------------------------------------

def test_escalations_not_hardcoded_zero():
    """Canonical metrics row must not contain the literal 0 for escalations."""
    assert '"escalations": 0' not in _SRC, (
        "escalations is still hardcoded to 0 in the metrics row — "
        "wire escalations counter from phase_state instead."
    )


def test_escalations_incremented_at_waiting_for_human_sites():
    """escalations counter must be incremented at each WAITING_FOR_HUMAN transition."""
    lines = _SRC.splitlines()
    escalation_increments = [
        i for i, ln in enumerate(lines, 1)
        if "escalations" in ln and "+=" in ln and "phase_state" not in ln.lower().replace(" ", "")
    ]
    # Also count via phase_state dict pattern
    phase_state_escalation_increments = [
        i for i, ln in enumerate(lines, 1)
        if "escalations" in ln and ("+=" in ln or '"escalations"' in ln and "get(" in ln)
    ]

    # There should be at least 2 increment sites (one per WAITING_FOR_HUMAN transition)
    assert len(phase_state_escalation_increments) >= 2, (
        f"Found only {len(phase_state_escalation_increments)} escalation increment(s) "
        "in orchestrator source — expected >= 2 (one per WAITING_FOR_HUMAN site). "
        "Add an escalations increment at each transition_state('WAITING_FOR_HUMAN') call."
    )


def test_escalations_sourced_from_phase_state_in_metrics_row():
    """Canonical metrics row must source escalations from phase_state, not a literal."""
    lines = _SRC.splitlines()
    metrics_esc_lines = [
        ln for ln in lines
        if "escalations" in ln and '"escalations"' in ln and "get(" in ln
    ]
    assert metrics_esc_lines, (
        "No 'escalations' key sourced via .get() found in orchestrator. "
        "The metrics row should read escalations from phase_state, e.g.: "
        "\"escalations\": _ps_m.get(\"escalations\", 0)"
    )


# ---------------------------------------------------------------------------
# W1-C: skill_used field
# ---------------------------------------------------------------------------

def test_metrics_row_includes_skill_used_key():
    """Canonical metrics row must include a skill_used field."""
    assert '"skill_used"' in _SRC, (
        "No 'skill_used' key found in orchestrator source. "
        "Add it to the canonical metrics.jsonl row dict."
    )


def test_skill_used_sourced_from_skill_injected():
    """skill_used value must come from phase_state's skill_injected field."""
    lines = _SRC.splitlines()
    skill_used_lines = [
        ln for ln in lines
        if '"skill_used"' in ln or ("skill_used" in ln and "skill_injected" in ln)
    ]
    # At least one line should reference skill_injected as the value source
    injected_ref = [
        ln for ln in _SRC.splitlines()
        if "skill_injected" in ln and "skill_used" in ln
    ]
    # OR the metrics row reads phase_state and uses skill_injected separately
    metrics_skill_lines = [
        ln for ln in _SRC.splitlines()
        if '"skill_used"' in ln and ("skill_injected" in ln or ".get(" in ln)
    ]
    assert metrics_skill_lines or injected_ref, (
        "skill_used in the metrics row should be sourced from "
        "phase_state.get('skill_injected'). "
        "No such reference found in orchestrator source."
    )


def test_phase_state_read_at_metrics_write_site():
    """A phase_state read must occur in the canonical metrics block for W1-A/B/C."""
    lines = _SRC.splitlines()
    # Find the metrics.jsonl write block (look for _metrics_path and _canonical_row)
    metrics_block_start = None
    for i, ln in enumerate(lines):
        if "_metrics_path = os.path.join(PROJECT_ARTIFACTS_DIR" in ln:
            metrics_block_start = i
            break
    assert metrics_block_start is not None, "_metrics_path assignment not found in orchestrator."

    # Within 80 lines of the metrics block start, there must be a phase_state read
    block = lines[metrics_block_start : metrics_block_start + 80]
    phase_state_reads = [
        ln for ln in block
        if ("phase_state" in ln or "read_phase_state" in ln or "PHASE_STATE_FILE" in ln)
        and ("open(" in ln or "read_phase_state()" in ln or ".get(" in ln)
    ]
    assert phase_state_reads, (
        "No phase_state read found within 80 lines of the metrics.jsonl write block. "
        "Add a phase_state read to source blame_fires, escalations, and skill_used."
    )
