"""P0 Stage A: Static lint that the roadmap-converter skills document the new
Behavioral Verification block and the new project-level ``verification.md``
output spec.

These tests do not validate LLM compliance with the skill — they only assert
that the skill files themselves carry the structural strings downstream
consumers will rely on. Runtime compliance is observed during integration.
"""
from pathlib import Path

import pytest


SKILL_ROOT = (
    Path(__file__).resolve().parents[1]
    / "autodev"
    / "skill-library"
    / "roadmap-converter"
)


def _read_skill(name: str) -> str:
    path = SKILL_ROOT / name / "SKILL.md"
    assert path.exists(), f"Expected skill file at {path}"
    return path.read_text()


class TestRoadmapGenerationSkill:
    """``roadmap-generation/SKILL.md`` is the canonical format spec the
    converter reads. P0 adds the Behavioral Verification per-phase block and
    a new top-level ``Verification Document Output`` section."""

    def test_contains_behavioral_verification_block(self):
        body = _read_skill("roadmap-generation")
        assert "Behavioral Verification" in body, (
            "The per-phase format spec must enumerate the new "
            "**Behavioral Verification:** block alongside the existing "
            "Entry/Exit/TDD/Done blocks."
        )
        assert "User-observable" in body
        assert "How we'll check" in body
        assert "If this fails, the user sees" in body

    def test_contains_verification_document_section(self):
        body = _read_skill("roadmap-generation")
        assert "## Verification Document Output" in body, (
            "A new top-level section ``## Verification Document Output`` "
            "must describe the ``verification.md`` shape the converter "
            "produces alongside the roadmap."
        )
        for heading in (
            "Project type",
            "Entry point",
            "Public surface",
            "Verification stack",
        ):
            assert heading in body, (
                f"Expected ``{heading}`` to appear in the "
                "Verification Document Output spec."
            )


class TestFormatCorrectionSkill:
    """``format-correction/SKILL.md`` mirrors the per-phase format spec used
    by ``roadmap-generation``, EXCLUDING the ``verification.md`` output spec —
    format-correction never touches that file (P0 §2.1)."""

    def test_mirrors_behavioral_verification_block(self):
        body = _read_skill("format-correction")
        assert "Behavioral Verification" in body
        assert "User-observable" in body
        assert "How we'll check" in body
        assert "If this fails, the user sees" in body

    def test_does_not_contain_verification_document_section(self):
        """Negative assertion: format-correction must NOT mention the
        verification document output spec, because it never produces that file."""
        body = _read_skill("format-correction")
        assert "Verification Document Output" not in body, (
            "format-correction is roadmap-only; it must not document "
            "the verification.md output."
        )


class TestAlignmentCheckSkill:
    """``alignment-check/SKILL.md`` audits PRD↔roadmap coverage. P0 extends it
    so the Behavioral Verification block is part of what gets audited
    against the PRD (missing-coverage and inflation rules)."""

    def test_audits_behavioral_coverage(self):
        body = _read_skill("alignment-check")
        assert "Behavioral Verification" in body, (
            "alignment-check must reference the Behavioral Verification "
            "block when describing what to audit against the PRD."
        )


class TestAdversarialReviewSkill:
    """``adversarial-review/SKILL.md`` stress-tests each phase. P0 extends the
    rubric so the per-phase failure hypothesis must address how the
    ``If this fails, the user sees`` outcome could trigger."""

    def test_generates_failure_hypothesis_for_behavioral(self):
        body = _read_skill("adversarial-review")
        assert "If this fails" in body or "failure_language" in body, (
            "adversarial-review must require a hypothesis about how the "
            "Behavioral Verification ``If this fails`` outcome could trigger."
        )
