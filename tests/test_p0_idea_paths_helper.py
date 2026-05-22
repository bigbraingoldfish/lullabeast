"""P0 Stage B3: ``_idea_paths_for_messages`` exposes the verification-doc paths.

The roadmap-converter webhook payload needs absolute paths it can hand to the
agent. The helper already returns ``roadmap_draft`` + ``roadmap_done``; P0
adds ``verification_draft`` + ``verification_done`` so the same helper covers
both artefacts.
"""
from pathlib import Path

from ui.server import _idea_paths_for_messages


def test_idea_paths_returns_verification_draft_and_done_paths(tmp_path):
    config = {"ideas_dir": str(tmp_path / "ideas")}
    paths = _idea_paths_for_messages(config, "abc-123")
    assert "verification_draft" in paths, (
        "_idea_paths_for_messages must expose 'verification_draft' so the "
        "/convert webhook payload can reference it."
    )
    assert "verification_done" in paths, (
        "_idea_paths_for_messages must expose 'verification_done' so the "
        "converter knows where to drop the sentinel."
    )
    expected_dir = Path(tmp_path / "ideas" / "abc-123")
    assert paths["verification_draft"] == str(expected_dir / "verification_draft.md")
    assert paths["verification_done"] == str(expected_dir / "verification_draft.done")


def test_idea_paths_still_returns_existing_keys(tmp_path):
    """Regression guard: existing callers must not lose access to prior keys."""
    config = {"ideas_dir": str(tmp_path / "ideas")}
    paths = _idea_paths_for_messages(config, "abc-123")
    for required in ("dir", "prd_draft", "roadmap_draft", "roadmap_done"):
        assert required in paths, f"Existing key '{required}' must remain"
