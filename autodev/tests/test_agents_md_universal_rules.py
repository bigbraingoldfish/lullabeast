"""Static-content guard for the universal rules in role AGENTS.md files.

P1 Stage A originally delivered the `integration-wiring` and `testing-quality`
universal rules as always-injected workspace skills. The refactor moved them
into each role's primary-context identity doc (`autodev/agents/{role}/AGENTS.md`)
under two `## Always-Apply: ...` sections.

These tests pin that the rules are actually present in each AGENTS.md, so a
future contributor editing the file cannot silently delete a section and have
the suite stay green. Each test asserts:
  (a) the section header is present, and
  (b) three stable, specific rule-content phrases drawn verbatim from the
      original SKILL.md source are present.

The phrase sets are deliberately concept-level (not stylistic) so minor prose
edits don't flake, but specific enough that an accidental section deletion
fails the test. If a phrase here is reworded in AGENTS.md, update the phrase
here in the same commit — that is the intended friction.
"""

import os

# Repo root is three levels up from autodev/tests/.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_AGENTS_DIR = os.path.join(_REPO_ROOT, "autodev", "agents")

_INTEGRATION_HEADER = "## Always-Apply: Integration Wiring"
_TESTING_HEADER = "## Always-Apply: Testing Quality"
_ORCH_CONTROL_HEADER = "## Always-Apply: Orchestrator Control"

# Orchestrator-control rules: "your turn ends at the sentinel" (per-role sentinel
# filename) + "[ORCHESTRATOR CONTROL] messages are authoritative". These prevent the
# stream-past-.done trigger and the agent ignoring/continuing after an interrupt that
# the consolidated _interrupt_agent_session helper relies on.
_ORCH_CONTROL_PHRASES = {
    "planner": [
        "pipeline/planner_output.done",
        "end your turn immediately",
        "comply immediately: stop all work",
    ],
    "executor": [
        "pipeline/executor_output.done",
        "end your turn immediately",
        "comply immediately: stop all work",
    ],
    "reviewer": [
        "pipeline/reviewer_output.done",
        "end your turn immediately",
        "comply immediately: stop all work",
    ],
}

# Per-role, per-discipline anchor phrases (verbatim substrings of the source
# SKILL.md rule content, preserved into the AGENTS.md inline).
_INTEGRATION_PHRASES = {
    "planner": [
        "Enumerate ALL components to wire",
        "ban hidden init at import time",
        "at least one end-to-end test per wired boundary",
    ],
    "executor": [
        "Do not write wiring code until you have read",
        "Build a single composition root",
        "cleanup in reverse init order",
    ],
    "reviewer": [
        "Run the actual entrypoint command",
        "producer output matches consumer input",
        "the last clean rejection point",
    ],
}

_TESTING_PHRASES = {
    "planner": [
        "No helper-only tests",
        "deliberate negative control",
        "Sleep used as synchronisation",
    ],
    "executor": [
        "Tests must fail if the system is broken",
        "Mock at boundaries only",
        "framework-native waits",
    ],
    "reviewer": [
        "Tests that mirror implementation logic",
        "at least one negative control",
        "shuffled order",
    ],
}


def _read_agents_md(role: str) -> str:
    path = os.path.join(_AGENTS_DIR, role, "AGENTS.md")
    with open(path, "r") as fh:
        return fh.read()


def _assert_section(role: str, header: str, phrases: list) -> None:
    content = _read_agents_md(role)
    assert header in content, (
        f"{role}/AGENTS.md is missing the '{header}' section — the universal "
        f"rules from the P1 Stage A refactor must live here."
    )
    missing = [p for p in phrases if p not in content]
    assert not missing, (
        f"{role}/AGENTS.md '{header}' section is missing rule phrases {missing!r}. "
        f"Either a rule was dropped during editing, or it was reworded — if "
        f"reworded, update the phrase list in this test in the same commit."
    )


# --- integration-wiring -----------------------------------------------------

def test_planner_agents_md_has_integration_wiring_section():
    _assert_section("planner", _INTEGRATION_HEADER, _INTEGRATION_PHRASES["planner"])


def test_executor_agents_md_has_integration_wiring_section():
    _assert_section("executor", _INTEGRATION_HEADER, _INTEGRATION_PHRASES["executor"])


def test_reviewer_agents_md_has_integration_wiring_section():
    _assert_section("reviewer", _INTEGRATION_HEADER, _INTEGRATION_PHRASES["reviewer"])


# --- testing-quality --------------------------------------------------------

def test_planner_agents_md_has_testing_quality_section():
    _assert_section("planner", _TESTING_HEADER, _TESTING_PHRASES["planner"])


def test_executor_agents_md_has_testing_quality_section():
    _assert_section("executor", _TESTING_HEADER, _TESTING_PHRASES["executor"])


def test_reviewer_agents_md_has_testing_quality_section():
    _assert_section("reviewer", _TESTING_HEADER, _TESTING_PHRASES["reviewer"])


# --- orchestrator-control ---------------------------------------------------

def test_planner_agents_md_has_orchestrator_control_section():
    _assert_section("planner", _ORCH_CONTROL_HEADER, _ORCH_CONTROL_PHRASES["planner"])


def test_executor_agents_md_has_orchestrator_control_section():
    _assert_section("executor", _ORCH_CONTROL_HEADER, _ORCH_CONTROL_PHRASES["executor"])


def test_reviewer_agents_md_has_orchestrator_control_section():
    _assert_section("reviewer", _ORCH_CONTROL_HEADER, _ORCH_CONTROL_PHRASES["reviewer"])
