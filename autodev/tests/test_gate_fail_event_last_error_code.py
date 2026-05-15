"""gate_fail pipeline events must include phase_state last_error_code in detail (orchestrator)."""

import re
from pathlib import Path

_ORCHESTRATOR = Path(__file__).resolve().parent.parent / "pipeline" / "orchestrator.py"

_SNAPSHOT = '"last_error_code": self.read_phase_state().get("last_error_code")'


def test_each_gate_fail_write_includes_last_error_code_in_detail_dict():
    """All gate_fail emits attach last_error_code from phase_state (same line or multi-line dict)."""
    src = _ORCHESTRATOR.read_text(encoding="utf-8")
    n = src.count(_SNAPSHOT)
    assert n == 3, f"expected 3 gate_fail detail snapshots with last_error_code, found {n}"

    blocks = list(re.finditer(r'_write_pipeline_event\(\s*"gate_fail"', src, re.MULTILINE))
    assert len(blocks) == 3, f"expected 3 _write_pipeline_event('gate_fail'...), found {len(blocks)}"

    for m in blocks:
        tail = src[m.start() : m.start() + 450]
        assert "last_error_code" in tail, f"gate_fail block missing last_error_code:\n{tail[:400]}"
