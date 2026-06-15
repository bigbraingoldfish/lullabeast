"""PREREQ-5 — static content-lint for prerequisites elicitation + value-redaction.

Like ``test_p0_skill_format.py``, these assert the agent-facing source-of-truth
carries the structural guidance — not that the LLM complies at runtime:

  - the PRD-creator (the Ideas-chat agent) must *ask* what tools/env vars the
    project needs and capture NAMES only, never values;
  - the roadmap-converter must redact any value-shaped string and emit only the
    name into the ``## Prerequisites`` block.

They would catch the elicitation question or the names-only safety rule being
dropped — the first line of defense for the never-ingest-a-value invariant.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PRD_CREATOR_AGENTS = REPO_ROOT / "autodev" / "agents" / "prd-creator" / "AGENTS.md"
ROADMAP_GEN_SKILL = (
    REPO_ROOT
    / "autodev"
    / "skill-library"
    / "roadmap-converter"
    / "roadmap-generation"
    / "SKILL.md"
)


def _read(path: Path) -> str:
    assert path.exists(), f"Expected file at {path}"
    return path.read_text(encoding="utf-8")


class TestPrdCreatorElicitation:
    def test_asks_for_tools_and_env_names(self):
        body = _read(PRD_CREATOR_AGENTS)
        assert "### Prerequisites Elicitation" in body, (
            "prd-creator/AGENTS.md must carry a Prerequisites Elicitation section "
            "so the conversation captures the tool/env contract early."
        )
        assert "config or secret" in body, (
            "The elicitation must ask for env var names typed as config or secret."
        )

    def test_names_only_safety_rule(self):
        body = _read(PRD_CREATOR_AGENTS)
        assert "never accept, store, or echo a value" in body, (
            "prd-creator/AGENTS.md must carry the names-only safety rule — the "
            "agent never accepts, stores, or echoes an env value."
        )


class TestRoadmapGenerationRedaction:
    def test_converter_redaction_rule(self):
        body = _read(ROADMAP_GEN_SKILL)
        assert "emit only the name" in body, (
            "roadmap-generation/SKILL.md must instruct the converter to redact a "
            "value-shaped string and emit only the name in the Prerequisites block."
        )
