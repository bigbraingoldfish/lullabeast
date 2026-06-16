"""LAUNCH-6 regression guard: gate-script paths derive from one GATE_SCRIPTS_DIR constant.

The legibility refactor centralised the gate-scripts directory into a single
``orchestrator.GATE_SCRIPTS_DIR`` constant. These tests protect that win: the constant must
resolve to the real directory holding the gate scripts, and the orchestrator must never
reconstruct the ``os.path.join(AUTODEV_REPO_PATH, "autodev", "pipeline", "gate_scripts", ...)``
literal inline again (which is how the path had drifted across ~11 call sites).
"""
import os

import orchestrator


def test_gate_scripts_dir_points_at_real_dir_with_the_gates():
    assert os.path.isdir(orchestrator.GATE_SCRIPTS_DIR)
    for gate in (
        "planner_gate.py",
        "executor_gate.py",
        "reviewer_gate.py",
        "phase_resolver.py",
        "repo_init_check.py",
    ):
        assert os.path.isfile(os.path.join(orchestrator.GATE_SCRIPTS_DIR, gate)), gate


def test_no_inline_gate_scripts_path_reconstruction_in_orchestrator():
    with open(orchestrator.__file__, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    # The quoted "gate_scripts" path element should appear on exactly one line: the
    # GATE_SCRIPTS_DIR definition. Any other occurrence is an inline reconstruction.
    offenders = [
        (i + 1, ln.strip())
        for i, ln in enumerate(lines)
        if '"gate_scripts"' in ln and "GATE_SCRIPTS_DIR =" not in ln
    ]
    assert not offenders, (
        "gate-script paths must derive from GATE_SCRIPTS_DIR, not an inline "
        'os.path.join(..., "gate_scripts", ...). Offending lines: %r' % offenders
    )
