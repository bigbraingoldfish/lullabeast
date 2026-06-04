"""F11 (UI) — the StoppedRecoveryPanel resume affordance must render for
HALTED_SILENT, not just STOPPED.

Static-lint / render-map-completeness tests (the dashboard is a single-file
text/babel block with no real transpiler in CI, so we pin the render gates by
source content; the operator reviews the actual wording visually). These guard
that all THREE render gates were widened to include HALTED_SILENT and that the
reused panel carries distinct, non-misleading copy for a silent halt.
"""

import re


def _index():
    with open("ui/index.html", "r", encoding="utf-8") as f:
        return f.read()


def test_current_phase_panel_gate_includes_halted_silent():
    """The CurrentPhasePanel render gate (currently STOPPED-only) must also fire
    for HALTED_SILENT so the operator sees the resume panel on a silent halt."""
    content = _index()
    assert "pipeline_status === 'STOPPED' || pipeline_status === 'HALTED_SILENT'" in content, (
        "the CurrentPhasePanel StoppedRecoveryPanel gate must include HALTED_SILENT"
    )


def test_active_stopped_queue_card_gate_includes_halted_silent():
    """The queue/hub card's ``isActiveStopped`` gate (the SECOND render site —
    easy to miss) must also include HALTED_SILENT in both the live and snapshot
    status checks."""
    content = _index()
    m = re.search(r"const isActiveStopped =[\s\S]{0,400}?;", content)
    assert m, "could not locate the isActiveStopped gate"
    block = m.group(0)
    assert block.count("HALTED_SILENT") >= 2, (
        "isActiveStopped must check HALTED_SILENT for both selLive and "
        f"snapshot.pipeline_status; got: {block!r}"
    )


def test_header_resume_button_shown_for_halted_silent():
    """The header Resume button gate must include HALTED_SILENT (the resume flow
    spawns the orchestrator, so it works even though a silent halt left it dead)."""
    content = _index()
    assert re.search(
        r'showResumeButton\s*=\s*status === "STOPPED" \|\| status === "HALTED_SILENT"',
        content,
    ), "showResumeButton must be shown for STOPPED or HALTED_SILENT"


def test_recovery_panel_has_distinct_halted_silent_copy():
    """The reused panel must NOT label a silent halt 'Pipeline Stopped'. It must
    carry distinct copy ('Intervention Required' / 'Halted during phase') driven
    by the pipeline_status prop."""
    content = _index()
    assert "Intervention Required" in content, (
        "StoppedRecoveryPanel must show 'Intervention Required' for HALTED_SILENT"
    )
    assert "Halted during phase" in content, (
        "StoppedRecoveryPanel must show 'Halted during phase' for HALTED_SILENT"
    )
    # The panel must receive pipeline_status to make that choice.
    assert re.search(r"function StoppedRecoveryPanel\(\{[\s\S]{0,200}?pipeline_status", content), (
        "StoppedRecoveryPanel must accept a pipeline_status prop"
    )
