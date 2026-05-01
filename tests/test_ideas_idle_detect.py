"""Unit tests for _resolve_prd_creator_jsonl and _poll_sentinel_with_idle_detect helpers.

These tests are written TDD-first — they will fail (red) until the helpers are implemented
in ui/server.py.
"""
import asyncio
import json
import os
import time
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers to load the functions under test once the implementation exists
# ---------------------------------------------------------------------------


def _get_resolve():
    from ui.server import _resolve_prd_creator_jsonl
    return _resolve_prd_creator_jsonl


def _get_poll():
    from ui.server import _poll_sentinel_with_idle_detect
    return _poll_sentinel_with_idle_detect


# ---------------------------------------------------------------------------
# _resolve_prd_creator_jsonl
# ---------------------------------------------------------------------------


class TestResolvePrdCreatorJsonl:
    """Tests for _resolve_prd_creator_jsonl(session_key, openclaw_root) -> str | None."""

    def test_returns_none_on_missing_sessions_json(self, tmp_path):
        """If sessions.json does not exist, return None without error."""
        resolve = _get_resolve()
        # tmp_path has no agents/prd-creator/sessions/ directory at all
        result = resolve("ideas:abc123:session-1", str(tmp_path))
        assert result is None

    def test_returns_none_on_missing_key(self, tmp_path):
        """If sessions.json exists but the session key is absent, return None."""
        resolve = _get_resolve()
        sessions_dir = tmp_path / "agents" / "prd-creator" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / "sessions.json").write_text(json.dumps({
            "agent:prd-creator:ideas:other-id:session-1": {
                "sessionId": "aaaaaaaa-0000-0000-0000-000000000000",
                "updatedAt": 1700000000000,
            }
        }))
        result = resolve("ideas:abc123:session-1", str(tmp_path))
        assert result is None

    def test_returns_jsonl_path_when_entry_exists(self, tmp_path):
        """Returns the JSONL path when the session key is present and sessionId is set."""
        resolve = _get_resolve()
        sessions_dir = tmp_path / "agents" / "prd-creator" / "sessions"
        sessions_dir.mkdir(parents=True)
        session_id = "12345678-abcd-0000-0000-000000000001"
        (sessions_dir / "sessions.json").write_text(json.dumps({
            "agent:prd-creator:ideas:abc123:session-2": {
                "sessionId": session_id,
                "updatedAt": 1700000001000,
            }
        }))
        result = resolve("ideas:abc123:session-2", str(tmp_path))
        expected = str(sessions_dir / f"{session_id}.jsonl")
        assert result == expected

    def test_returns_none_on_malformed_json(self, tmp_path):
        """If sessions.json is not valid JSON, return None without error."""
        resolve = _get_resolve()
        sessions_dir = tmp_path / "agents" / "prd-creator" / "sessions"
        sessions_dir.mkdir(parents=True)
        (sessions_dir / "sessions.json").write_text("not-json{{{")
        result = resolve("ideas:abc123:session-1", str(tmp_path))
        assert result is None


# ---------------------------------------------------------------------------
# _poll_sentinel_with_idle_detect
# ---------------------------------------------------------------------------


class TestPollSentinelWithIdleDetect:
    """Tests for async _poll_sentinel_with_idle_detect."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_returns_true_when_sentinel_found_immediately(self, tmp_path):
        """If done_path already exists before the first sleep, returns True quickly."""
        poll = _get_poll()
        done = tmp_path / "1.done"
        done.write_text("done")
        result = self._run(poll(
            done_path=done,
            session_key="ideas:x:session-1",
            openclaw_root=str(tmp_path),
            poll_timeout=5.0,
            poll_interval=0.05,
            idle_threshold=120.0,
            startup_grace=2.0,
        ))
        assert result[0] is True

    def test_returns_false_on_hard_timeout_without_jsonl(self, tmp_path):
        """With no sessions.json and no sentinel, returns False at startup_grace expiry."""
        poll = _get_poll()
        done = tmp_path / "1.done"  # never written
        start = time.monotonic()
        result = self._run(poll(
            done_path=done,
            session_key="ideas:x:session-1",
            openclaw_root=str(tmp_path),
            poll_timeout=0.3,
            poll_interval=0.05,
            idle_threshold=120.0,
            startup_grace=0.1,
        ))
        elapsed = time.monotonic() - start
        assert result[0] is False
        assert result[1] == "no_session"
        # Should not have waited much longer than poll_timeout
        assert elapsed < 2.0

    def test_returns_false_when_jsonl_goes_idle(self, tmp_path):
        """Returns False early when JSONL mtime stops advancing after idle_threshold."""
        poll = _get_poll()
        sessions_dir = tmp_path / "agents" / "prd-creator" / "sessions"
        sessions_dir.mkdir(parents=True)
        session_id = "idle-test-0000-0000-0000-000000000001"
        jsonl_path = sessions_dir / f"{session_id}.jsonl"
        # Write JSONL once so the mtime is set, but do not update it further
        jsonl_path.write_text("initial line\n")
        (sessions_dir / "sessions.json").write_text(json.dumps({
            "agent:prd-creator:ideas:idleidea:session-1": {
                "sessionId": session_id,
                "updatedAt": 1700000001000,
            }
        }))
        done = tmp_path / "1.done"  # never written

        result = self._run(poll(
            done_path=done,
            session_key="ideas:idleidea:session-1",
            openclaw_root=str(tmp_path),
            poll_timeout=30.0,         # hard timeout is much longer
            poll_interval=0.05,
            idle_threshold=0.3,        # short idle threshold for the test
            startup_grace=0.1,
        ))
        assert result[0] is False  # returned early due to idle, not hard timeout
        assert result[1] == "idle"

    def test_returns_true_when_sentinel_appears_while_jsonl_active(self, tmp_path):
        """Returns True when sentinel appears while JSONL is actively being updated."""
        poll = _get_poll()
        sessions_dir = tmp_path / "agents" / "prd-creator" / "sessions"
        sessions_dir.mkdir(parents=True)
        session_id = "active-test-0000-0000-0000-000000000002"
        jsonl_path = sessions_dir / f"{session_id}.jsonl"
        jsonl_path.write_text("line 1\n")
        (sessions_dir / "sessions.json").write_text(json.dumps({
            "agent:prd-creator:ideas:activeidea:session-1": {
                "sessionId": session_id,
                "updatedAt": 1700000001000,
            }
        }))
        done = tmp_path / "1.done"

        write_count = [0]

        async def run_with_concurrent_writes():
            async def writer():
                # Simulate agent writing to JSONL and eventually writing sentinel
                for i in range(6):
                    await asyncio.sleep(0.08)
                    jsonl_path.write_text(f"line {i+2}\n")
                    write_count[0] += 1
                # Write sentinel after ~0.5s
                await asyncio.sleep(0.1)
                done.write_text("done")

            result_task = asyncio.create_task(poll(
                done_path=done,
                session_key="ideas:activeidea:session-1",
                openclaw_root=str(tmp_path),
                poll_timeout=10.0,
                poll_interval=0.05,
                idle_threshold=5.0,  # large idle threshold so we don't quit early
                startup_grace=0.2,
            ))
            await writer()
            return await result_task

        result = asyncio.run(run_with_concurrent_writes())
        assert result[0] is True
        assert write_count[0] > 0

    def test_idea_watch_dir_resets_idle_despite_stale_jsonl(self, tmp_path):
        """When JSONL mtime is flat, rising idea-workspace activity delays idle.

        ``_idea_workspace_activity_mtime`` is mocked so this test does not scan
        real files under ``idea/`` (avoids a hot FS loop under patched
        ``time.monotonic`` / ``asyncio.sleep`` on slow hosts).
        """
        poll = _get_poll()
        idea_dir = tmp_path / "idea"
        (idea_dir / "turns").mkdir(parents=True)
        prd = idea_dir / "prd_draft.md"
        prd.write_text("v0")
        os.utime(prd, (1000, 1000))

        sessions_dir = tmp_path / "agents" / "prd-creator" / "sessions"
        sessions_dir.mkdir(parents=True)
        session_id = "idea-watch-0000-0000-0000-000000000099"
        jsonl_path = sessions_dir / f"{session_id}.jsonl"
        jsonl_path.write_text("static-jsonl\n")
        os.utime(jsonl_path, (500, 500))
        (sessions_dir / "sessions.json").write_text(
            json.dumps(
                {
                    "agent:prd-creator:ideas:iwatch:session-1": {
                        "sessionId": session_id,
                        "updatedAt": 1700000001000,
                    }
                }
            )
        )
        done = tmp_path / "missing.done"

        mono_seq = [0.0, 125.0, 250.0]
        tick = [0]

        def fake_monotonic():
            i = tick[0]
            tick[0] += 1
            return mono_seq[i] if i < len(mono_seq) else mono_seq[-1] + 200.0

        sleeps: list[None] = []

        async def track_sleep_no_idea_write(_interval):
            sleeps.append(None)

        async def run_poll_jsonl_only():
            tick[0] = 0
            sleeps.clear()
            with patch("ui.server.time.monotonic", fake_monotonic):
                with patch("ui.server.asyncio.sleep", track_sleep_no_idea_write):
                    return await poll(
                        done_path=done,
                        session_key="ideas:iwatch:session-1",
                        openclaw_root=str(tmp_path),
                        poll_timeout=900.0,
                        poll_interval=0.01,
                        idle_threshold=120.0,
                        startup_grace=0.05,
                        idea_watch_dir=None,
                    )

        async def run_poll_with_mocked_idea_workspace_mtime():
            tick[0] = 0
            sleeps.clear()
            idea_activity = {"t": 1000.0}

            def fake_idea_mtime(_p):
                return idea_activity["t"]

            mock_idea = MagicMock(side_effect=fake_idea_mtime)

            async def bump_idea_activity_on_first_sleep(_interval):
                sleeps.append(None)
                if len(sleeps) == 1:
                    idea_activity["t"] = 2000.0

            with patch("ui.server.time.monotonic", fake_monotonic):
                with patch("ui.server.asyncio.sleep", bump_idea_activity_on_first_sleep):
                    with patch(
                        "ui.server._idea_workspace_activity_mtime", new=mock_idea
                    ):
                        result = await poll(
                            done_path=done,
                            session_key="ideas:iwatch:session-1",
                            openclaw_root=str(tmp_path),
                            poll_timeout=900.0,
                            poll_interval=0.01,
                            idle_threshold=120.0,
                            startup_grace=0.05,
                            idea_watch_dir=idea_dir,
                        )
            assert mock_idea.call_count >= 2, (
                "expected multiple poll iterations to consult idea workspace mtime"
            )
            return result

        # JSONL-only: second tick hits idle after one sleep (do not touch prd)
        r_jsonl = asyncio.run(run_poll_jsonl_only())
        assert r_jsonl[0] is False
        assert r_jsonl[1] == "idle"
        assert len(sleeps) == 1

        # With idea dir: simulated activity bump after first sleep resets idle; later tick fails
        r_idea = asyncio.run(run_poll_with_mocked_idea_workspace_mtime())
        assert r_idea[0] is False
        assert r_idea[1] == "idle"
        assert len(sleeps) == 2
