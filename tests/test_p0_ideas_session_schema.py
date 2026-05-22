"""P0 Stage B4 + B5: session schema additions for verification_content.

- ``_default_idea_session`` returns ``verification_content`` as part of the
  default dict so new ideas have the slot ready.
- ``_rehydrate_session_from_artifacts`` backfills ``verification_content``
  from ``verification_draft.md`` when present, mirroring the existing PRD
  rehydration behaviour.
"""
import os

from ui.server import (
    _default_idea_session,
    _rehydrate_session_from_artifacts,
)


def test_default_idea_session_includes_verification_content():
    schema = _default_idea_session()
    assert "verification_content" in schema, (
        "New ideas need a slot for verification_content so the convert "
        "endpoint can drop the generated doc there."
    )
    assert schema["verification_content"] == ""


def test_default_idea_session_preserves_existing_fields():
    schema = _default_idea_session()
    for required in ("name", "messages", "prd_content", "roadmap_content", "created", "updated"):
        assert required in schema, (
            f"Existing schema key '{required}' must remain after the additive change"
        )


def test_rehydrate_backfills_verification_content_from_disk(tmp_path):
    idea_dir = tmp_path / "ideas" / "alpha"
    idea_dir.mkdir(parents=True)
    # Both the doc AND its sentinel must exist — the converter writes them in
    # sequence; the doc alone may be a partial write.
    (idea_dir / "verification_draft.md").write_text("# Verification\n\n## Project type\nweb-app\n")
    (idea_dir / "verification_draft.done").write_text("")

    session_data = {
        "name": "alpha",
        "messages": [{"role": "assistant", "content": "hi", "ts": "2026-01-01T00:00:00Z"}],
        "prd_content": "# PRD",
        "roadmap_content": "",
        "verification_content": "",
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
    }
    rehydrated, changed = _rehydrate_session_from_artifacts(idea_dir, session_data)
    assert changed is True, "Rehydrate must report change when filling from disk"
    assert rehydrated["verification_content"].startswith("# Verification")


def test_rehydrate_skips_verification_when_sentinel_missing(tmp_path):
    """Sentinel-gated: a verification_draft.md without its .done sentinel may
    be a partial converter write. Rehydrate must not surface it."""
    idea_dir = tmp_path / "ideas" / "alpha-partial"
    idea_dir.mkdir(parents=True)
    (idea_dir / "verification_draft.md").write_text("# Partial write")
    # No verification_draft.done.

    session_data = {
        "name": "alpha-partial",
        "messages": [{"role": "assistant", "content": "hi", "ts": "2026-01-01T00:00:00Z"}],
        "prd_content": "# PRD",
        "roadmap_content": "",
        "verification_content": "",
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
    }
    rehydrated, _changed = _rehydrate_session_from_artifacts(idea_dir, session_data)
    assert rehydrated.get("verification_content", "") == "", (
        "Without the .done sentinel, verification_content must stay empty — "
        "the disk file may be a partial converter write."
    )


def test_rehydrate_idempotent_when_verification_already_populated(tmp_path):
    idea_dir = tmp_path / "ideas" / "beta"
    idea_dir.mkdir(parents=True)
    (idea_dir / "verification_draft.md").write_text("# DISK CONTENT")

    session_data = {
        "name": "beta",
        "messages": [{"role": "assistant", "content": "hi", "ts": "2026-01-01T00:00:00Z"}],
        "prd_content": "# PRD",
        "roadmap_content": "",
        "verification_content": "# IN-MEMORY CONTENT",
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
    }
    rehydrated, _changed = _rehydrate_session_from_artifacts(idea_dir, session_data)
    assert rehydrated["verification_content"] == "# IN-MEMORY CONTENT", (
        "Rehydrate must not overwrite an already-populated verification_content "
        "field — only fill when empty."
    )


def test_rehydrate_updated_timestamp_uses_verification_mtime_when_latest(tmp_path):
    idea_dir = tmp_path / "ideas" / "gamma"
    idea_dir.mkdir(parents=True)
    (idea_dir / "verification_draft.md").write_text("# Verification")
    (idea_dir / "verification_draft.done").write_text("")

    base = 1_700_000_000
    os.utime(idea_dir / "verification_draft.md", (base + 5000, base + 5000))
    os.utime(idea_dir / "verification_draft.done", (base + 5000, base + 5000))

    session_data = {
        "name": "gamma",
        "messages": [],
        "prd_content": "",
        "roadmap_content": "",
        "verification_content": "",
        "created": None,
        "updated": None,
    }
    rehydrated, changed = _rehydrate_session_from_artifacts(idea_dir, session_data)
    assert changed is True
    assert rehydrated.get("verification_content", "").startswith("# Verification")
    assert rehydrated.get("updated"), "updated timestamp should be set when content is backfilled"
