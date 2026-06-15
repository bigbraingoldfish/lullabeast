"""PREREQ-4 — static content-lint that the mock-first external-call posture is
present in the agent guidance.

Like ``test_p0_skill_format.py``, these tests do not validate LLM compliance —
they assert that the agent-facing source-of-truth files carry the structural
guidance the pipeline relies on:

  - the *universal* rule lives in each role's ``AGENTS.md`` Always-Apply: Testing
    Quality section (injected every phase, every discipline — so a paid API in a
    CORE phase is covered, not only API-prefixed phases);
  - the *discipline-specific* elaboration lives in the ``api-service`` skills (the
    primary external-integration domain).

They would catch a regression that drops the mock-first guidance, or — critically
— one that *weakens* the reviewer's anti-fake-test blocker while adding the
external-API acceptance rule (the two must coexist: mocking the external paid
boundary is acceptable; mocking the system's own internals is not).
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_ROOT = REPO_ROOT / "autodev" / "agents"
API_SERVICE_ROOT = REPO_ROOT / "autodev" / "skill-library" / "api-service"


def _agents_md(role: str) -> str:
    path = AGENTS_ROOT / role / "AGENTS.md"
    assert path.exists(), f"Expected AGENTS.md at {path}"
    return path.read_text(encoding="utf-8")


def _api_skill(role: str) -> str:
    path = API_SERVICE_ROOT / role / "SKILL.md"
    assert path.exists(), f"Expected api-service skill at {path}"
    return path.read_text(encoding="utf-8")


class TestUniversalAgentsMdMockFirst:
    """The universal mock-first rule must appear in every pipeline role's
    AGENTS.md so it applies on every phase regardless of discipline prefix."""

    def test_planner_agents_md_forbids_planning_live_paid_calls(self):
        body = _agents_md("planner")
        assert "live paid call" in body, (
            "planner/AGENTS.md must instruct the planner not to plan a live paid "
            "call for external/paid-API phases (mock/fake/recorded boundary "
            "instead)."
        )

    def test_executor_agents_md_forbids_live_paid_calls(self):
        body = _agents_md("executor")
        assert "live paid call" in body, (
            "executor/AGENTS.md must instruct the executor never to make a live "
            "paid call during the run (route through a mock/fake/local stub)."
        )

    def test_reviewer_agents_md_accepts_mocked_external_evidence(self):
        body = _agents_md("reviewer")
        assert "mocked / recorded / local-stub" in body, (
            "reviewer/AGENTS.md must state that mocked / recorded / local-stub "
            "evidence is acceptable for an external-API feature."
        )

    def test_reviewer_internal_mock_blocker_is_preserved(self):
        """The external-API acceptance rule must not weaken the anti-fake-test
        discipline: mocking the system's own internals stays a blocker."""
        body = _agents_md("reviewer")
        assert "mock core internals" in body, (
            "reviewer/AGENTS.md must keep the 'mock core internals' blocker — "
            "accepting mocked external boundaries must NOT permit mocking the "
            "system under test's own logic."
        )


class TestApiServiceSkillMockFirst:
    """The api-service discipline skills carry the detailed external/paid-API
    guidance for each role."""

    def test_planner_skill_has_external_paid_api_section(self):
        body = _api_skill("planner")
        assert "## External & paid API boundaries" in body
        assert "mock" in body.lower()

    def test_executor_skill_has_external_paid_api_section(self):
        body = _api_skill("executor")
        assert "## External & paid API integration" in body
        assert "mock" in body.lower()

    def test_reviewer_skill_has_external_paid_api_section(self):
        body = _api_skill("reviewer")
        assert "## External & paid API evidence" in body
        assert "mock" in body.lower()
