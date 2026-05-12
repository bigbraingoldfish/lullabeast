"""Unit tests for JSONL-based ``_poll_sentinel_with_idle_detect`` (Ideas workflow).

The function polls for a ``.done`` sentinel file while monitoring a session JSONL
file for liveness.  It returns ``True`` when the sentinel appears, ``False`` on
timeout, idle detection, or no-session (startup grace expired without JSONL found).
"""

import asyncio
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest


def _get_poll():
    from ui.server import _poll_sentinel_with_idle_detect
    return _poll_sentinel_with_idle_detect


def _setup_sessions_json(openclaw_root: Path, session_key: str, jsonl_path: str):
    """Write a sessions.json that maps session_key → jsonl_path."""
    sessions_dir = openclaw_root / "agents" / "prd-creator" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    full_key = f"agent:prd-creator:{session_key}"
    sessions = {full_key: {"sessionId": "sess-001"}}
    (sessions_dir / "sessions.json").write_text(json.dumps(sessions))
    # Write the JSONL file so it exists with a current mtime
    Path(jsonl_path).parent.mkdir(parents=True, exist_ok=True)
    Path(jsonl_path).write_text("")
    return str(sessions_dir / "sess-001.jsonl")


class TestPollSentinelStampDetect:
    """Tests for async _poll_sentinel_with_idle_detect (JSONL liveness + .done)."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_returns_true_when_sentinel_found_immediately(self, tmp_path):
        poll = _get_poll()
        done = tmp_path / "1.done"
        done.write_text("done")
        oc = tmp_path / "oc"
        oc.mkdir()
        result = self._run(
            poll(
                done_path=done,
                session_key="ideas:42:session-1",
                openclaw_root=str(oc),
                poll_timeout=5.0,
                poll_interval=0.05,
                idle_threshold=120.0,
                startup_grace=2.0,
            )
        )
        assert result is True

    def test_returns_false_when_no_session_registered(self, tmp_path):
        """Startup grace expires with no sessions.json entry → False."""
        poll = _get_poll()
        done = tmp_path / "1.done"
        oc = tmp_path / "oc"
        oc.mkdir()
        start = time.monotonic()
        result = self._run(
            poll(
                done_path=done,
                session_key="ideas:42:session-1",
                openclaw_root=str(oc),
                poll_timeout=30.0,
                poll_interval=0.05,
                idle_threshold=120.0,
                startup_grace=0.15,
            )
        )
        elapsed = time.monotonic() - start
        assert result is False
        assert elapsed < 2.0

    def test_returns_false_idle_when_jsonl_mtime_stale(self, tmp_path):
        """JSONL exists but mtime never advances → idle detection fires."""
        poll = _get_poll()
        done = tmp_path / "1.done"
        oc = tmp_path / "oc"
        session_key = "ideas:42:session-1"
        sessions_dir = oc / "agents" / "prd-creator" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = sessions_dir / "sess-001.jsonl"
        jsonl_path.write_text("")
        os.utime(jsonl_path, (1000, 1000))  # very old mtime
        full_key = f"agent:prd-creator:{session_key}"
        (sessions_dir / "sessions.json").write_text(
            json.dumps({full_key: {"sessionId": "sess-001"}})
        )

        # Use fake monotonic to simulate time passing
        mono_seq = [0.0, 125.0, 250.0]
        tick = [0]

        def fake_monotonic():
            i = tick[0]
            tick[0] += 1
            return mono_seq[i] if i < len(mono_seq) else mono_seq[-1] + 200.0

        async def track_sleep(_interval):
            pass

        tick[0] = 0
        with patch("ui.server.time.monotonic", fake_monotonic):
            with patch("ui.server.asyncio.sleep", track_sleep):
                result = asyncio.run(
                    poll(
                        done_path=done,
                        session_key=session_key,
                        openclaw_root=str(oc),
                        poll_timeout=900.0,
                        poll_interval=0.01,
                        idle_threshold=120.0,
                        startup_grace=0.05,
                    )
                )
        assert result is False

    def test_returns_true_when_jsonl_refreshed_then_done_written(self, tmp_path):
        """JSONL mtime advances (agent active) and .done appears → True."""
        poll = _get_poll()
        done = tmp_path / "1.done"
        oc = tmp_path / "oc"
        session_key = "ideas:42:session-1"
        sessions_dir = oc / "agents" / "prd-creator" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = sessions_dir / "sess-001.jsonl"
        full_key = f"agent:prd-creator:{session_key}"
        (sessions_dir / "sessions.json").write_text(
            json.dumps({full_key: {"sessionId": "sess-001"}})
        )

        async def run_with_writer():
            async def bump_jsonl_then_done():
                await asyncio.sleep(0.08)
                jsonl_path.write_text("{}\n")
                await asyncio.sleep(0.08)
                jsonl_path.write_text("{}\n{}\n")
                await asyncio.sleep(0.08)
                done.write_text("done")

            t = asyncio.create_task(bump_jsonl_then_done())
            r = await poll(
                done_path=done,
                session_key=session_key,
                openclaw_root=str(oc),
                poll_timeout=10.0,
                poll_interval=0.05,
                idle_threshold=2.0,
                startup_grace=0.2,
            )
            await t
            return r

        assert asyncio.run(run_with_writer()) is True


def test_load_config_ideas_env_overrides(monkeypatch, tmp_path):
    """AUTODEV_IDEAS_* env vars override merged config (same pattern as hooks token)."""
    monkeypatch.setenv("AUTODEV_IDEAS_IDLE_THRESHOLD", "999")
    monkeypatch.setenv("AUTODEV_IDEAS_STARTUP_GRACE", "888")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        '{"ideas_idle_threshold": 1, "ideas_startup_grace": 2, "port": 18790}'
    )
    from ui.server import load_config

    cfg = load_config(config_path=str(cfg_path))
    assert cfg["ideas_idle_threshold"] == 999.0
    assert cfg["ideas_startup_grace"] == 888.0


def test_late_done_valid_accepts_sentinel_within_mtime_slack(tmp_path):
    """``.done`` mtime may trail ``attempt_start_wall`` by a second on coarse FS."""
    from ui.server import IDEAS_LATE_DONE_MTIME_SLACK_SEC, _late_done_valid_for_attempt

    done = tmp_path / "3.done"
    done.write_text("done")
    attempt_wall = time.time()
    back = attempt_wall - (IDEAS_LATE_DONE_MTIME_SLACK_SEC - 1.0)
    os.utime(done, (back, back))
    assert _late_done_valid_for_attempt(done, attempt_wall) is True


def test_late_done_valid_rejects_stale_sentinel(tmp_path):
    from ui.server import IDEAS_LATE_DONE_MTIME_SLACK_SEC, _late_done_valid_for_attempt

    done = tmp_path / "3.done"
    done.write_text("done")
    attempt_wall = time.time()
    old = attempt_wall - IDEAS_LATE_DONE_MTIME_SLACK_SEC - 50.0
    os.utime(done, (old, old))
    assert _late_done_valid_for_attempt(done, attempt_wall) is False
