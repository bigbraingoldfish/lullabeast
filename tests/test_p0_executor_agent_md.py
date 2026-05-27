"""P0 Stage E — executor/AGENTS.md doc-contract tests.

Pins the additions Stage E makes to ``autodev/agents/executor/AGENTS.md``:
the executor must read prd.md and verification.md, must produce a new
``behavioral_smoke_artifacts`` field on phases with a Behavioral Verification
block, must run the phase's ``how_to_check`` procedure as a final step,
and must re-run that procedure on a reviewer-rejection retry whose blocking
issues carry a behavioural criterion source.
"""

import os

import pytest

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir)
)
EXECUTOR_AGENTS_MD = os.path.join(
    REPO_ROOT, "autodev", "agents", "executor", "AGENTS.md"
)


@pytest.fixture(scope="module")
def agents_md_text():
    with open(EXECUTOR_AGENTS_MD, "r", encoding="utf-8") as f:
        return f.read()


def test_inputs_section_lists_prd_and_verification(agents_md_text):
    """The executor uses prd.md and verification.md to ground its
    implementation against the user's truth — webhook defaults already
    point at both (Stage D); AGENTS.md must agree."""
    assert "prd.md" in agents_md_text, (
        "executor/AGENTS.md must direct the agent to read prd.md"
    )
    assert "verification.md" in agents_md_text, (
        "executor/AGENTS.md must direct the agent to read verification.md"
    )


def test_output_contract_lists_behavioral_smoke_artifacts(agents_md_text):
    """behavioral_smoke_artifacts is the new field the executor gate
    validates (Stage F). Missing from the doc → executor silently omits
    the field and burns a retry on every behavioural phase."""
    assert "behavioral_smoke_artifacts" in agents_md_text, (
        "executor/AGENTS.md Output Contract must list "
        "behavioral_smoke_artifacts — the field the executor gate now "
        "validates on phases with a behavioral_verification block"
    )


def test_behavioral_smoke_path_documented(agents_md_text):
    """The captured artifacts MUST land under .autodev/pipeline/behavioral-smoke/
    so the gate's WORKSPACE_DIR + path-safety guard succeeds. A doc that
    omits the path location pushes the agent to invent its own."""
    assert ".autodev/pipeline/behavioral-smoke" in agents_md_text, (
        "executor/AGENTS.md must specify the .autodev/pipeline/behavioral-smoke/ "
        "directory as the canonical location for behavioural-verification "
        "captures — required for the executor gate's workspace-bound path "
        "validation to succeed"
    )


def test_scenario_b_mentions_rerun_how_to_check(agents_md_text):
    """On reviewer-rejection retries with a behavioural criterion source,
    the executor must re-run the how_to_check procedure and re-capture
    artifacts — otherwise the next reviewer pass sees stale artifacts and
    the rejection cycle does not converge."""
    # Look for both "how_to_check" and a re-run/rerun token in the same
    # body of text. Scenario B section is short, so check the whole file
    # for the conjunction.
    assert "how_to_check" in agents_md_text, (
        "executor/AGENTS.md must reference the how_to_check procedure"
    )
    # The reviewer-rejection retry path must include guidance to re-run
    # how_to_check (mention of "re-run" or "rerun" in proximity to
    # "how_to_check" or "Scenario B").
    text_lower = agents_md_text.lower()
    rerun_idx = text_lower.find("re-run")
    if rerun_idx == -1:
        rerun_idx = text_lower.find("rerun")
    assert rerun_idx != -1, (
        "executor/AGENTS.md Scenario B (reviewer-rejection retry) must "
        "explicitly instruct the executor to re-run the how_to_check "
        "procedure after the targeted fix when a blocking issue is anchored "
        "to a behavioural criterion source — otherwise the next reviewer "
        "pass sees stale behavioural artifacts"
    )


def test_executor_runs_how_to_check_documented(agents_md_text):
    """The executor must execute the how_to_check procedure as the final
    step on phases with a Behavioral Verification block. 'The agent that
    wrote the code also runs it.'"""
    # Look for instruction-shaped text linking the executor to running
    # how_to_check. Heuristic: mention of "execute" / "run" alongside
    # "how_to_check" within a reasonable window.
    htc_indices = []
    start = 0
    while True:
        idx = agents_md_text.find("how_to_check", start)
        if idx == -1:
            break
        htc_indices.append(idx)
        start = idx + 1
    assert htc_indices, "expected at least one mention of how_to_check"
    # At least one mention should be in close proximity (250 chars either
    # side) to an action verb the executor would do.
    found = False
    for idx in htc_indices:
        window = agents_md_text[max(0, idx - 250) : idx + 250].lower()
        if any(verb in window for verb in ("execute", "run ", "running ", "perform")):
            found = True
            break
    assert found, (
        "executor/AGENTS.md must instruct the executor to execute/run the "
        "how_to_check procedure (not merely mention the field) on phases "
        "with a Behavioral Verification block"
    )
