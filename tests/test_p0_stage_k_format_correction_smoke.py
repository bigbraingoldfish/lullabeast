"""P0 Stage K — format-correction endpoint smoke.

Pins the data path through ``POST /api/ideas/{id}/fix-roadmap-format``:
input (a pre-P0 roadmap with no Behavioral Verification blocks) →
mocked-LLM transformation → output (a corrected roadmap that passes strict
validation, marked with ``<!-- TODO: human-review -->`` per the
format-correction skill).

These tests exercise the plumbing (webhook + sentinel polling + session.json
write-back), not the LLM. The LLM is mocked via the
``aiohttp.ClientSession`` + ``asyncio.sleep`` side-effect pattern used by
the existing convert tests. The corrected content is taken from
``_POST_CORRECTION_ROADMAP_FIXTURE`` in
``tests/test_p0_validate_roadmap_strict.py`` — keeping the fixture pair
co-located with the validator-only assertions keeps a single source of
truth for "what success looks like."

Approaches considered and rejected (per the Stage K plan):

* A deterministic Python migration utility under
  ``autodev/pipeline/migrations/`` — duplicates the SKILL.md contract and
  creates a second source of truth that will drift. Reserved as a parked
  fallback if production data shows the agent unreliable.
* Real subprocess invocation of the format-correction agent — violates
  the no-live-LLM constraint that holds across the Stage K test suite.
* Byte-identical golden-file comparison of corrected output — the LLM's
  output is non-deterministic by nature; validator pass/fail is the right
  contract surface.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ui.server import _validate_roadmap_content, app
from tests.test_p0_validate_roadmap_strict import (
    _POST_CORRECTION_ROADMAP_FIXTURE,
    _PRE_P0_ROADMAP_FIXTURE,
)


def _load_client() -> TestClient:
    return TestClient(app)


class TestFormatCorrectionEndpointSmoke:
    """End-to-end smoke of ``/api/ideas/{id}/fix-roadmap-format`` against the
    fixture pair from ``test_p0_validate_roadmap_strict.py``.

    Setup mirrors ``tests/test_api_ideas_convert.py`` (same mock-aiohttp +
    asyncio.sleep side-effect pattern); helpers are inlined rather than
    extracted to conftest because no third consumer exists yet (targeted
    rule from the engineering standards).
    """

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.tmp_path = tmp_path
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()

    def _mock_config(self) -> dict:
        repo = Path(__file__).resolve().parents[1]
        return {
            "ideas_dir": str(self.ideas_dir),
            "hooks_url": "http://localhost:18789/hooks/agent",
            "hooks_token": "test-token",
            "autodev_repo_path": str(repo),
        }

    def _write_session(self, idea_id: str, roadmap_content: str) -> Path:
        idea_dir = self.ideas_dir / idea_id
        idea_dir.mkdir(parents=True, exist_ok=True)
        session = {
            "messages": [],
            "prd_content": "## Problem Statement\n\nA pre-P0 idea.",
            "roadmap_content": roadmap_content,
            "verification_content": "",
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        }
        (idea_dir / "session.json").write_text(json.dumps(session))
        return idea_dir

    def _make_mock_aiohttp(self):
        mock_response = MagicMock()
        mock_response.status = 200
        mock_session = MagicMock()
        mock_session.post = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_cls = MagicMock(return_value=mock_session)
        return mock_cls, mock_session

    def test_fix_roadmap_format_pipes_corrected_content_through(self):
        """Happy path: pre-P0 fixture in, post-correction fixture out, session.json updated.

        Pins the full data path under a mocked LLM:
        1. Endpoint reads ``roadmap_content`` from session.json (set to the
           pre-P0 fixture).
        2. Endpoint writes that content to ``roadmap_draft.md``.
        3. Endpoint POSTs to the webhook (mocked 200).
        4. Endpoint polls ``roadmap_draft.done``; the mocked ``asyncio.sleep``
           side-effect simulates the agent writing the corrected fixture and
           the sentinel.
        5. Endpoint reads ``roadmap_draft.md`` (now the corrected content)
           and writes it back to ``session.json``.

        Final assertion: the corrected content passes strict
        ``_validate_roadmap_content``. This pins the success target of the
        SKILL.md contract end-to-end through the endpoint.
        """
        client = _load_client()
        idea_dir = self._write_session("fix-1", roadmap_content=_PRE_P0_ROADMAP_FIXTURE)
        mock_cls, _ = self._make_mock_aiohttp()

        async def write_corrected_sentinel(*args, **kwargs):
            (idea_dir / "roadmap_draft.md").write_text(_POST_CORRECTION_ROADMAP_FIXTURE)
            (idea_dir / "roadmap_draft.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.FORMAT_CORRECTION_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", new=write_corrected_sentinel):
            r = client.post("/api/ideas/fix-1/fix-roadmap-format")

        assert r.status_code == 200, f"Expected 200; got {r.status_code} body={r.text}"
        body = r.json()
        assert "roadmap_content" in body
        assert body["roadmap_content"] == _POST_CORRECTION_ROADMAP_FIXTURE, (
            "Response body must round-trip the corrected fixture verbatim."
        )
        # session.json must carry the corrected content (atomically written by the endpoint).
        session = json.loads((idea_dir / "session.json").read_text())
        assert session.get("roadmap_content") == _POST_CORRECTION_ROADMAP_FIXTURE
        # The corrected content must pass strict validation — this is the
        # cutover gate that proves the SKILL.md target shape is consumable
        # downstream by ``_validate_roadmap_content`` without further work.
        validation = _validate_roadmap_content(body["roadmap_content"])
        assert validation["valid"] is True, (
            f"Corrected content must pass strict validation; errors: {validation['errors']}"
        )

    def test_fix_roadmap_format_pipes_invalid_corrected_content_unmodified(self):
        """Pins current behavior: the endpoint does NOT re-validate the
        corrected content before piping it back to session.json.

        Rationale (Stage K plan): validation belongs to the downstream
        ``/api/setup/preflight`` gate, not to ``/api/ideas/{id}/fix-roadmap-format``.
        If a future change adds inline post-correction validation, this
        test fires and forces a deliberate contract decision — the
        endpoint either grows a fail-on-invalid path (with a new status
        code and UI affordance) or stays piped-through. Either is a
        meaningful choice; silent fail-soft drift is not.
        """
        client = _load_client()
        idea_dir = self._write_session("fix-2", roadmap_content=_PRE_P0_ROADMAP_FIXTURE)
        mock_cls, _ = self._make_mock_aiohttp()

        # The "corrected" content omits the **If this fails...** sub-bullet
        # on every phase — strict validation will reject it.
        invalid_corrected = _PRE_P0_ROADMAP_FIXTURE + (
            "\n  **Behavioral Verification:**\n"
            "  - **User-observable:** Something.\n"
            "  - **How we'll check:** Something else.\n"
        )

        async def write_invalid_sentinel(*args, **kwargs):
            (idea_dir / "roadmap_draft.md").write_text(invalid_corrected)
            (idea_dir / "roadmap_draft.done").write_text("")

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.FORMAT_CORRECTION_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", new=write_invalid_sentinel):
            r = client.post("/api/ideas/fix-2/fix-roadmap-format")

        # 200 — current contract pipes whatever the agent produced through.
        assert r.status_code == 200, (
            f"Endpoint must currently pass invalid content through unmodified; got {r.status_code}. "
            "If this test fails because the endpoint now returns 4xx on invalid corrected content, "
            "that is a deliberate contract change — update the test to assert the new shape and "
            "document the change in the next Stage's log."
        )
        body = r.json()
        assert body["roadmap_content"] == invalid_corrected
        # Independent confirmation: the corrected content really would fail validation.
        validation = _validate_roadmap_content(body["roadmap_content"])
        assert validation["valid"] is False

    def test_fix_roadmap_format_408_on_stall_does_not_surface_prewritten_malformed(self):
        """A genuine stall returns 408 and does NOT surface the pre-written malformed roadmap.

        The endpoint pre-writes the malformed input to ``roadmap_draft.md``
        before invoking the agent. If the idle-detection poll stalls (agent
        active then silent, no ``.done``), the endpoint must 408 — NOT read
        ``roadmap_draft.md`` back as a "corrected" result, and must not mutate
        ``session.json``. Regression guard for the ``rescue_stranded_reply_md``
        opt-out: without it the helper's sibling-``.md`` rescue would surface the
        server-pre-written malformed roadmap as a successful correction.
        """
        from autodev.pipeline.sentinel_poller import PollResult

        client = _load_client()
        idea_dir = self._write_session("fix-stall", roadmap_content=_PRE_P0_ROADMAP_FIXTURE)
        mock_cls, _ = self._make_mock_aiohttp()

        with patch("ui.server.load_config", return_value=self._mock_config()), \
             patch("ui.server.aiohttp.ClientSession", mock_cls), \
             patch("ui.server._inject_converter_skill"), \
             patch(
                 "ui.server._poll_sentinel_with_idle_detect",
                 AsyncMock(return_value=PollResult(False, "stalled")),
             ):
            r = client.post("/api/ideas/fix-stall/fix-roadmap-format")

        assert r.status_code == 408, f"a stall must 408; got {r.status_code} body={r.text}"
        assert "stalled" in r.text, "the 408 detail should carry the poll reason"
        # session.json roadmap_content must NOT have been overwritten with the
        # pre-written malformed input as if it were a successful correction.
        session = json.loads((idea_dir / "session.json").read_text())
        assert session.get("roadmap_content") == _PRE_P0_ROADMAP_FIXTURE, (
            "a stalled correction must not mutate session roadmap_content"
        )
