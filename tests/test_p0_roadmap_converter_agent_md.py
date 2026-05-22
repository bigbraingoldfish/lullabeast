"""P0 Stage B1: AGENTS.md Mode 1 documents the dual-document write order.

The roadmap-converter Mode 1 session must produce ``roadmap_draft.md`` AND
``verification_draft.md`` in the same call, with both sentinels (each written
after its primary document). This static-lint test asserts the AGENTS.md
identity doc names both artefacts.
"""
from pathlib import Path


AGENTS_MD = (
    Path(__file__).resolve().parents[1]
    / "autodev"
    / "agents"
    / "roadmap-converter"
    / "AGENTS.md"
)


def _read_agents_md() -> str:
    assert AGENTS_MD.exists(), f"Expected {AGENTS_MD}"
    return AGENTS_MD.read_text()


def test_mode1_documents_verification_draft_artifact():
    body = _read_agents_md()
    assert "verification_draft.md" in body, (
        "Mode 1 must explicitly name verification_draft.md as one of its "
        "output artefacts so the converter writes it alongside the roadmap."
    )


def test_mode1_documents_verification_done_sentinel():
    body = _read_agents_md()
    assert "verification_draft.done" in body, (
        "Mode 1 must name verification_draft.done as a sentinel; the "
        "/api/ideas/{id}/convert endpoint polls both sentinels after P0."
    )


def test_mode1_section_exists_with_dual_output_language():
    """Sanity: ensure ``Mode 1`` is still the section heading and references
    both files in its write order."""
    body = _read_agents_md()
    assert "Mode 1" in body
    # Locate the Mode 1 section text and assert both files appear within it.
    mode1_start = body.find("Mode 1")
    mode2_start = body.find("Mode 2")
    if mode2_start == -1:
        mode2_start = len(body)
    mode1_section = body[mode1_start:mode2_start]
    assert "verification_draft.md" in mode1_section, (
        "verification_draft.md must be named inside the Mode 1 section, "
        "not just elsewhere in AGENTS.md."
    )
    assert "verification_draft.done" in mode1_section
