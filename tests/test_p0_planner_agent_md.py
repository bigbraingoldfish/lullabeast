"""P0 Stage E — planner/AGENTS.md doc-contract tests.

These tests pin the documentation Stage E adds to ``autodev/agents/planner/AGENTS.md``.
The planner agent's runtime behaviour is driven by what its AGENTS.md says,
so silent drift between this doc and the new ``pass_criteria[].traces_to``
contract would silently break behavioural-anchor traceability without any
gate complaining.

These are static-lint tests over the AGENTS.md file — same pattern as the
Stage A roadmap-converter tests under ``tests/test_p0_roadmap_converter_agent_md.py``.
"""

import os

import pytest

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir)
)
PLANNER_AGENTS_MD = os.path.join(
    REPO_ROOT, "autodev", "agents", "planner", "AGENTS.md"
)


@pytest.fixture(scope="module")
def agents_md_text():
    with open(PLANNER_AGENTS_MD, "r", encoding="utf-8") as f:
        return f.read()


def test_inputs_section_lists_prd_md(agents_md_text):
    """P0 introduces PRD-anchored pass criteria. Planner must read prd.md
    before producing planner_output.json."""
    assert "prd.md" in agents_md_text, (
        "planner/AGENTS.md Inputs section must direct the agent to read "
        "pipeline-project/prd.md — webhook default messages already do this "
        "(Stage D) but the AGENTS.md drifted; Stage E aligns them"
    )


def test_inputs_section_lists_verification_md(agents_md_text):
    """verification.md carries project type + public surface — the planner
    must reference it when authoring pass_criteria with behavior
    anchors."""
    assert "verification.md" in agents_md_text, (
        "planner/AGENTS.md must reference pipeline-project/verification.md "
        "as a required read alongside prd.md"
    )


def test_pass_criteria_documents_traces_to_enum_values(agents_md_text):
    """Every valid traces_to anchor form must be documented so the planner
    knows what values are legal. Missing one means the planner falls back to
    free-form criteria and PRD traceability silently degrades."""
    assert "traces_to" in agents_md_text, (
        "planner/AGENTS.md must document the pass_criteria[].traces_to field"
    )
    for token in ("tdd:", "behavior:user_observable",
                  "behavior:how_to_check"):
        assert token in agents_md_text, (
            f"planner/AGENTS.md must document the traces_to enum value "
            f"{token!r} — every pass criterion must anchor to one of the three "
            f"documented forms"
        )


def test_pass_criteria_example_uses_traces_to(agents_md_text):
    """A JSON example showing traces_to populated is what the agent will
    pattern-match against. Without it the planner sees only the schema
    description and may omit the field on first try, wasting a retry."""
    # Heuristic: a pass_criteria example block that contains traces_to.
    # The example block is JSON-shaped, so look for both pass_criteria and
    # traces_to within a reasonable proximity.
    pc_idx = agents_md_text.find("pass_criteria")
    assert pc_idx != -1
    # Examine a 1500-char window after the first pass_criteria mention —
    # the schema example should live near it.
    window = agents_md_text[pc_idx : pc_idx + 1500]
    assert "traces_to" in window, (
        "planner/AGENTS.md must include a pass_criteria example showing "
        "traces_to populated; a schema-only description without an example "
        "produces inconsistent first-pass output"
    )
