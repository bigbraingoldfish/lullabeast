"""C2-03: _trigger_readiness_assessment must check resp.status and persist an
error sentinel when the webhook returns 4xx/5xx.

Without the fix the function logs the status and returns silently, leaving the
readiness UI forever stale with no operator-visible error.
"""
import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "ui"))

from ui.server import _trigger_readiness_assessment


def _make_mock_aiohttp_session(status: int):
    """Return (mock_cls, mock_session) with post returning a response of given status."""
    mock_response = MagicMock()
    mock_response.status = status
    mock_session = MagicMock()
    mock_session.post = AsyncMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_cls = MagicMock(return_value=mock_session)
    return mock_cls, mock_session


class TestC203ReadinessWebhookStatusCheck:
    def test_401_from_webhook_writes_error_sentinel(self, tmp_path):
        """When the webhook returns 401, readiness_error.json must be written so
        the UI can surface the failure instead of staying silently stale."""
        idea_id = "test-readiness-idea"
        idea_dir = tmp_path / idea_id
        idea_dir.mkdir()
        config = {
            "ideas_dir": str(tmp_path),
            "hooks_url": "http://localhost:18789/hooks/agent",
            "hooks_token": "secret",
        }

        mock_cls, _ = _make_mock_aiohttp_session(status=401)

        with patch("ui.server.aiohttp.ClientSession", mock_cls):
            asyncio.run(_trigger_readiness_assessment(idea_id, config))

        error_file = idea_dir / "readiness_error.json"
        assert error_file.exists(), (
            "readiness_error.json was not created on 401 — UI cannot surface the error (C2-03 unfixed)"
        )
        data = json.loads(error_file.read_text())
        assert "error" in data, f"Expected 'error' key in readiness_error.json, got: {data}"
        assert "401" in str(data["error"]), f"Error should mention status 401, got: {data}"

    def test_503_from_webhook_writes_error_sentinel(self, tmp_path):
        """When the webhook returns 503 (infra error), readiness_error.json must be written."""
        idea_id = "test-readiness-503"
        idea_dir = tmp_path / idea_id
        idea_dir.mkdir()
        config = {
            "ideas_dir": str(tmp_path),
            "hooks_url": "http://localhost:18789/hooks/agent",
            "hooks_token": "secret",
        }

        mock_cls, _ = _make_mock_aiohttp_session(status=503)

        with patch("ui.server.aiohttp.ClientSession", mock_cls):
            asyncio.run(_trigger_readiness_assessment(idea_id, config))

        error_file = idea_dir / "readiness_error.json"
        assert error_file.exists(), "readiness_error.json was not created on 503 (C2-03 unfixed)"

    def test_200_from_webhook_does_not_write_error_sentinel(self, tmp_path):
        """Sanity: a successful 200 response must NOT write a readiness_error.json."""
        idea_id = "test-readiness-ok"
        idea_dir = tmp_path / idea_id
        idea_dir.mkdir()
        config = {
            "ideas_dir": str(tmp_path),
            "hooks_url": "http://localhost:18789/hooks/agent",
            "hooks_token": "secret",
        }

        mock_cls, _ = _make_mock_aiohttp_session(status=200)

        with patch("ui.server.aiohttp.ClientSession", mock_cls):
            asyncio.run(_trigger_readiness_assessment(idea_id, config))

        error_file = idea_dir / "readiness_error.json"
        assert not error_file.exists(), (
            "readiness_error.json should NOT be written on a 200 success response"
        )
