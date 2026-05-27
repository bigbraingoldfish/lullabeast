"""P0 Stage I: consolidate duplicate Ideas-screen post-timeout salvage.

Stage I extracts a generic ``_merge_draft_into_session_data`` core that both
typed helpers (``_merge_roadmap_draft_into_session_data`` and
``_merge_verification_draft_into_session_data``) delegate to. It also removes
the inlined roadmap salvage block previously living inside
``get_ideas_session()`` so the endpoint routes both salvage paths through the
typed helpers symmetrically.

The tests below pin three invariants:

1. The new generic core exists and behaves correctly for any
   ``(basename, session_key)`` pair.
2. The typed wrappers are thin delegations — calling a wrapper invokes the
   core with the wrapper's hard-coded basename + session key.
3. ``get_ideas_session()`` routes both salvage paths through the typed
   helpers; the literal ``roadmap_draft.md`` reference no longer appears in
   its function body (AST guard against regression to an inlined block).
"""
import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _load_test_client():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


# ---------------------------------------------------------------------------
# Group 1: Generic core direct tests
#
# Mirror of the existing typed-direct tests at
# tests/test_p0_ideas_session_salvage.py:28-67, but for the new generic core.
# ---------------------------------------------------------------------------


class TestGenericMergeCore:
    """Direct tests of ``_merge_draft_into_session_data``.

    The core takes a ``basename`` ("roadmap_draft" / "verification_draft" /
    any other string) and a ``session_key`` ("roadmap_content" /
    "verification_content" / any other string) and applies the same
    sentinel-gated, whitespace-aware, in-place-mutating salvage logic.
    """

    def test_core_merges_when_disk_and_session_differ(self, tmp_path):
        """Happy path: both files present, disk text non-empty + differs from
        session → merge, return True."""
        from ui.server import _merge_draft_into_session_data

        idea_dir = tmp_path / "ideas" / "core1"
        idea_dir.mkdir(parents=True)
        (idea_dir / "foo_draft.md").write_text("# Disk content\n")
        (idea_dir / "foo_draft.done").write_text("")

        session_data = {"foo_content": ""}
        merged = _merge_draft_into_session_data(
            idea_dir, session_data, "foo_draft", "foo_content"
        )

        assert merged is True
        assert session_data["foo_content"].startswith("# Disk content")

    def test_core_no_merge_when_sentinel_missing(self, tmp_path):
        """Without the ``.done`` sentinel, the disk doc is treated as
        incomplete; do not merge. Returns False."""
        from ui.server import _merge_draft_into_session_data

        idea_dir = tmp_path / "ideas" / "core2"
        idea_dir.mkdir(parents=True)
        (idea_dir / "bar_draft.md").write_text("# Disk only")
        # No bar_draft.done.

        session_data = {"bar_content": ""}
        merged = _merge_draft_into_session_data(
            idea_dir, session_data, "bar_draft", "bar_content"
        )

        assert merged is False
        assert session_data["bar_content"] == "", (
            "Without the sentinel, session_data[bar_content] must stay empty."
        )

    def test_core_no_merge_when_content_unchanged(self, tmp_path):
        """When the stripped disk text equals the stripped session text,
        nothing has changed; do not merge. Returns False."""
        from ui.server import _merge_draft_into_session_data

        idea_dir = tmp_path / "ideas" / "core3"
        idea_dir.mkdir(parents=True)
        same_text = "# Verification\n\n## Project type\ncli\n"
        (idea_dir / "verification_draft.md").write_text(same_text)
        (idea_dir / "verification_draft.done").write_text("")

        session_data = {"verification_content": same_text}
        merged = _merge_draft_into_session_data(
            idea_dir, session_data, "verification_draft", "verification_content"
        )

        assert merged is False


# ---------------------------------------------------------------------------
# Group 2: Delegation pinning
#
# Each typed wrapper MUST call the generic core with its specific
# (basename, session_key) pair. Patch the core and assert the wrapper's
# behavior reduces to forwarding arguments.
# ---------------------------------------------------------------------------


class TestTypedWrappersDelegate:
    """Pin the "thin wrapper" design choice.

    If anyone later restores duplicated logic to either typed helper (so the
    helper inlines a sentinel check or comparison instead of calling the
    core), these tests catch it — the patched core would not be invoked.
    """

    def test_roadmap_typed_helper_delegates_to_core(self, tmp_path):
        """``_merge_roadmap_draft_into_session_data`` must call the core with
        ``basename='roadmap_draft'`` and ``session_key='roadmap_content'``."""
        import ui.server as srv

        idea_dir = tmp_path / "ideas" / "deleg1"
        idea_dir.mkdir(parents=True)
        session_data = {"roadmap_content": ""}

        with patch.object(srv, "_merge_draft_into_session_data", return_value=False) as mock_core:
            result = srv._merge_roadmap_draft_into_session_data(idea_dir, session_data)

        assert mock_core.call_count == 1, (
            "Roadmap typed wrapper must invoke the generic core exactly once."
        )
        # Positional args: (idea_dir, session_data, basename, session_key)
        args, kwargs = mock_core.call_args
        # Accept either positional or keyword form for robustness.
        passed_args = list(args) + [kwargs.get("idea_dir"), kwargs.get("session_data"),
                                    kwargs.get("basename"), kwargs.get("session_key")]
        assert idea_dir in passed_args
        assert session_data in passed_args
        assert "roadmap_draft" in passed_args, (
            "Roadmap wrapper must forward basename='roadmap_draft'."
        )
        assert "roadmap_content" in passed_args, (
            "Roadmap wrapper must forward session_key='roadmap_content'."
        )
        assert result is False, "Wrapper must return the core's return value."

    def test_verification_typed_helper_delegates_to_core(self, tmp_path):
        """``_merge_verification_draft_into_session_data`` must call the core
        with ``basename='verification_draft'`` and
        ``session_key='verification_content'``."""
        import ui.server as srv

        idea_dir = tmp_path / "ideas" / "deleg2"
        idea_dir.mkdir(parents=True)
        session_data = {"verification_content": ""}

        with patch.object(srv, "_merge_draft_into_session_data", return_value=True) as mock_core:
            result = srv._merge_verification_draft_into_session_data(idea_dir, session_data)

        assert mock_core.call_count == 1, (
            "Verification typed wrapper must invoke the generic core exactly once."
        )
        args, kwargs = mock_core.call_args
        passed_args = list(args) + [kwargs.get("idea_dir"), kwargs.get("session_data"),
                                    kwargs.get("basename"), kwargs.get("session_key")]
        assert idea_dir in passed_args
        assert session_data in passed_args
        assert "verification_draft" in passed_args, (
            "Verification wrapper must forward basename='verification_draft'."
        )
        assert "verification_content" in passed_args, (
            "Verification wrapper must forward session_key='verification_content'."
        )
        assert result is True, "Wrapper must return the core's return value."


# ---------------------------------------------------------------------------
# Group 3: Routing pinning (and inlined-block removal)
#
# ``get_ideas_session()`` previously contained an inlined roadmap salvage
# block (ui/server.py:4161-4171 prior to Stage I) that read
# ``roadmap_draft.md`` directly and wrote ``session.json`` inline. After
# Stage I, both roadmap and verification salvage are routed through the
# typed helpers.
# ---------------------------------------------------------------------------


class TestSessionEndpointRouting:
    """Pin that ``get_ideas_session`` calls each typed helper rather than
    reading the salvage files inline."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()

    def _mock_config(self):
        return {"ideas_dir": str(self.ideas_dir)}

    def _seed_idea(self, idea_id):
        idea_dir = self.ideas_dir / idea_id
        idea_dir.mkdir(parents=True, exist_ok=True)
        session = {
            "name": idea_id,
            "messages": [],
            "prd_content": "# PRD",
            "roadmap_content": "",
            "verification_content": "",
            "created": "2026-05-27T00:00:00Z",
            "updated": "2026-05-27T00:00:00Z",
        }
        (idea_dir / "session.json").write_text(json.dumps(session))
        return idea_dir

    def test_session_get_routes_roadmap_through_helper(self):
        """When ``roadmap_draft.md`` + ``roadmap_draft.done`` exist on disk,
        the GET /session handler must invoke the roadmap typed helper.

        Today this FAILS because the inlined block at lines 4161-4171 reads
        the file directly and never enters the helper. Stage I fixes this."""
        client = _load_test_client()
        idea_dir = self._seed_idea("route-rm")
        (idea_dir / "roadmap_draft.md").write_text("# Roadmap\n\n## Phase A\n")
        (idea_dir / "roadmap_draft.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server._merge_roadmap_draft_into_session_data",
                   return_value=False) as mock_helper:
            r = client.get("/api/ideas/route-rm/session")

        assert r.status_code == 200, r.text
        assert mock_helper.call_count == 1, (
            "GET /api/ideas/{id}/session must route roadmap salvage through "
            "_merge_roadmap_draft_into_session_data exactly once. The inlined "
            "block at ui/server.py:4161-4171 (pre-Stage-I) bypasses the helper."
        )

    def test_session_get_routes_verification_through_helper(self):
        """When ``verification_draft.md`` + ``verification_draft.done`` exist
        on disk, the GET /session handler must invoke the verification typed
        helper. This already passes today (per Stage B) — pinned here so
        future regression to an inline-style block is caught."""
        client = _load_test_client()
        idea_dir = self._seed_idea("route-ver")
        (idea_dir / "verification_draft.md").write_text("# Verification\n\n")
        (idea_dir / "verification_draft.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server._merge_verification_draft_into_session_data",
                   return_value=False) as mock_helper:
            r = client.get("/api/ideas/route-ver/session")

        assert r.status_code == 200, r.text
        assert mock_helper.call_count == 1, (
            "GET /api/ideas/{id}/session must route verification salvage "
            "through _merge_verification_draft_into_session_data exactly once."
        )

    def test_session_get_inlined_block_is_removed(self):
        """AST guard: the literal string ``roadmap_draft.md`` must not
        appear in the body of ``get_ideas_session``. After Stage I, the
        function references the salvage path only through the typed helper,
        which encapsulates the filename. If anyone restores an inlined block
        (reading ``idea_dir / "roadmap_draft.md"`` directly inside this
        function), this test fails immediately."""
        import ui.server as srv

        src = inspect.getsource(srv.get_ideas_session)
        assert "roadmap_draft.md" not in src, (
            "Inlined roadmap salvage block must be gone — get_ideas_session "
            "must route through _merge_roadmap_draft_into_session_data "
            "instead of reading the file directly."
        )
        assert "verification_draft.md" not in src, (
            "By symmetry, get_ideas_session must not reference "
            "verification_draft.md directly either — route through "
            "_merge_verification_draft_into_session_data."
        )
