"""Static lint: the gate-script contract docs describe BOTH signalling conventions.

F7 — CLAUDE.md's "Gate Script Interface Contract" historically documented a single
universal exit-code / JSON-on-stdout protocol. That is true only for the resolver /
init gates (`phase_resolver.py`, `repo_init_check.py`). The verdict gates (planner,
executor, reviewer) always **exit 0** and signal via a stdout verdict string, with
failure detail on side channels (`executor_gate_detail.json` / `gate_warnings.json` /
`last_error_code`). This guards the reconciled two-convention docs (CLAUDE.md +
PIPELINE-SPEC.md) against regression to the false universal claim.
"""
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CLAUDE_MD = _REPO_ROOT / "CLAUDE.md"
_PIPELINE_SPEC = _REPO_ROOT / "autodev" / "docs" / "PIPELINE-SPEC.md"


def _gate_contract_intro() -> str:
    """The reconciled part: the 'Gate Script Interface Contract' heading through
    the start of the (unchanged) 'Advisory output channel' subsection."""
    text = _CLAUDE_MD.read_text()
    start = text.find("## Gate Script Interface Contract")
    assert start != -1, "CLAUDE.md is missing the Gate Script Interface Contract section"
    end = text.find("### Advisory output channel", start)
    assert end != -1, "the Advisory output channel subsection anchor moved"
    return text[start:end]


def test_contract_documents_verdict_gate_convention():
    """The contract must state the verdict gates exit 0 + signal via stdout, with
    detail on a side channel (not JSON-on-stdout)."""
    intro = _gate_contract_intro()
    low = intro.lower()
    assert "exit 0" in low, "must state the verdict gates exit 0"
    assert "stdout" in low, "must state the verdict is a stdout string"
    assert ("executor_gate_detail.json" in intro or "gate_warnings.json" in intro), (
        "must name a side-channel detail file (failure detail does not ride stdout "
        "for the verdict gates)"
    )


def test_contract_documents_resolver_gate_convention():
    """The contract must still name the exit-code family (phase_resolver /
    repo_init_check) so the 0/1/2 protocol is documented for those gates."""
    intro = _gate_contract_intro()
    assert "phase_resolver" in intro, "must name phase_resolver as an exit-code gate"
    assert "repo_init_check" in intro, "must name repo_init_check as an exit-code gate"


def test_contract_drops_universal_exit_code_claim():
    """The universal 'all communication is via exit codes and stdout' sentence is
    false for the verdict gates and must be removed/scoped."""
    intro = _gate_contract_intro()
    assert "All communication is via exit codes and stdout" not in intro, (
        "the universal exit-code/stdout claim is false for the verdict gates "
        "(they exit 0 + stdout verdict); scope it to the resolver/init family"
    )


def test_pipeline_spec_executor_gate_not_described_as_exit_1():
    """PIPELINE-SPEC must not claim the executor gate 'fails closed with exit code 1'
    for ERR_MISSING_BASE_COMMIT — it returns the stdout string FAIL (exit 0) with
    last_error_code in phase_state.json."""
    if not _PIPELINE_SPEC.exists():
        pytest.skip("PIPELINE-SPEC.md not present")
    text = _PIPELINE_SPEC.read_text()
    assert "with exit code 1 and error code `ERR_MISSING_BASE_COMMIT`" not in text, (
        "the executor gate is a verdict gate: it returns FAIL (exit 0) with "
        "last_error_code=ERR_MISSING_BASE_COMMIT, not exit code 1"
    )
