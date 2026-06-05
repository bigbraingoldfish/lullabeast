"""Phase 3 — Server read-path & Ideas hardening (T3.1–T3.9).

TDD red tests for the defensive-hardening roadmap Phase 3
(``plans/Active/defensive-hardening-roadmap.md``). Each test is written to
**fail against the pre-Phase-3 ``ui/server.py``** and pass once the
corresponding fix lands. The class docstrings state what each section covers
and what regression it would catch.

All tests are hermetic: ``tmp_path`` fixtures, no live server, no orchestrator
(per the project rule against running broad suites against a live server).
"""
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import ui.server as srv
from ui.server import PollResult


def _client() -> TestClient:
    return TestClient(srv.app)


# ---------------------------------------------------------------------------
# T3.1 — Numeric config-key coercion in load_config (RV-7.1)
# ---------------------------------------------------------------------------
class TestT31ConfigNumericCoercion:
    """A malformed numeric key in config.json must degrade to its default,
    not flow through as a string that 500s a downstream ``float()`` *after*
    the Ideas webhook has already fired.

    Catches: a regression that drops the coercion and lets ``"5 min"`` /
    ``"abc"`` / ``"x"`` reach consumers verbatim.
    """

    def test_malformed_numeric_keys_fall_back_to_defaults(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({
            "ideas_idle_threshold": "5 min",   # garbage → default 300
            "port": "x",                        # garbage → default 18790
            "poll_timeout": "abc",              # garbage → default 900
            "poll_interval": "5",               # valid string → numeric 5
        }))

        config = srv.load_config(config_path=str(cfg_path))

        assert config["ideas_idle_threshold"] == 300
        assert config["port"] == 18790
        assert config["poll_timeout"] == 900
        # A valid numeric string is coerced to a real number (not left "5").
        assert config["poll_interval"] == 5
        assert isinstance(config["poll_interval"], (int, float))
        assert not isinstance(config["poll_interval"], str)

    def test_valid_numeric_values_are_unchanged(self, tmp_path):
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({
            "ideas_idle_threshold": 120,
            "port": 12345,
        }))

        config = srv.load_config(config_path=str(cfg_path))

        assert config["ideas_idle_threshold"] == 120
        assert config["port"] == 12345


# ---------------------------------------------------------------------------
# T3.2 — Broaden _read_json_file exception net (RV-4.7b)
# ---------------------------------------------------------------------------
class TestT32ReadJsonFileToleratesOsErrors:
    """Read-tolerant endpoints treat a ``None`` return as "absent". A path
    that exists but cannot be read as JSON (a directory, undecodable bytes)
    must therefore return ``None``, not raise.

    Catches: narrowing the catch back to ``FileNotFoundError``/
    ``JSONDecodeError`` so ``IsADirectoryError``/``UnicodeDecodeError``
    propagate as a 500.
    """

    def test_directory_path_returns_none(self, tmp_path):
        a_dir = tmp_path / "adir"
        a_dir.mkdir()
        # open(dir, 'r') raises IsADirectoryError (an OSError) — not caught pre-fix.
        assert srv._read_json_file(str(a_dir)) is None

    def test_undecodable_bytes_returns_none(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_bytes(b"\xff\xfe\x00\x01not utf8")
        # .read() raises UnicodeDecodeError — not caught pre-fix.
        assert srv._read_json_file(str(bad)) is None

    def test_missing_and_corrupt_still_return_none(self, tmp_path):
        assert srv._read_json_file(str(tmp_path / "nope.json")) is None
        corrupt = tmp_path / "corrupt.json"
        corrupt.write_text("{not json")
        assert srv._read_json_file(str(corrupt)) is None


# ---------------------------------------------------------------------------
# T3.7 — Unique-temp atomic session.json writer (RV-3.8)
# ---------------------------------------------------------------------------
class TestT37AtomicWriteUsesUniqueTemp:
    """The shared JSON writer must use a unique temp name (mkstemp), not a
    fixed ``<path>.tmp`` that two concurrent writers collide on and corrupt.

    Catches: reverting to the fixed ``str(path) + ".tmp"`` suffix.
    """

    def test_temp_name_is_unique_not_fixed_suffix(self, tmp_path, monkeypatch):
        target = tmp_path / "session.json"
        captured = {}
        real_replace = os.replace

        def cap_replace(src, dst):
            captured["src"] = str(src)
            return real_replace(src, dst)

        monkeypatch.setattr(srv.os, "replace", cap_replace)
        srv._atomic_write_json_file(target, {"a": 1})

        # Pre-fix the src is exactly "<path>.tmp"; post-fix it's a mkstemp name.
        assert captured["src"] != str(target) + ".tmp"
        assert os.path.dirname(captured["src"]) == str(tmp_path)
        assert json.loads(target.read_text()) == {"a": 1}

    def test_concurrent_writers_do_not_collide(self, tmp_path):
        """Two interleaved writers sharing one fixed temp name corrupt the
        file; unique temps make both writes valid."""
        target = tmp_path / "session.json"
        # Simulate the dangerous case directly: with a fixed suffix the second
        # writer's temp clobbers the first. With unique temps the names differ.
        names = set()
        real_mkstemp = __import__("tempfile").mkstemp

        def spy_mkstemp(*a, **k):
            fd, name = real_mkstemp(*a, **k)
            names.add(name)
            return fd, name

        with patch("ui.server.mkstemp", side_effect=spy_mkstemp):
            srv._atomic_write_json_file(target, {"x": 1})
            srv._atomic_write_json_file(target, {"x": 2})

        # mkstemp was actually used (proves no fixed ".tmp"), and the file is valid.
        assert names, "expected mkstemp-based unique temp names"
        assert json.loads(target.read_text()) == {"x": 2}

    def test_save_session_for_idea_uses_unique_temp(self, tmp_path, monkeypatch):
        """The annotations-path writer must route through the unique-temp writer,
        not its old fixed ``session.json.tmp`` suffix. ``_save_session_for_idea``
        writes the SAME ``session.json`` as ``_atomic_write_json_file`` (chat /
        readiness / convert), so a fixed temp here re-opens the concurrent-writer
        collision T3.7 closed.

        Catches: reverting ``_save_session_for_idea`` to ``str(path) + '.tmp'``.
        """
        captured = {}
        real_replace = os.replace

        def cap_replace(src, dst):
            captured["src"] = str(src)
            return real_replace(src, dst)

        monkeypatch.setattr(srv.os, "replace", cap_replace)
        srv._save_session_for_idea(tmp_path, {"messages": []})

        session_path = tmp_path / "session.json"
        # Must NOT be the fixed "<path>.tmp"; must be a mkstemp name in the same dir.
        assert captured["src"] != str(session_path) + ".tmp"
        assert os.path.dirname(captured["src"]) == str(tmp_path)
        # Routed through the shared writer → annotations default applied, valid JSON.
        data = json.loads(session_path.read_text())
        assert data["messages"] == []
        assert data["annotations"] == []


# ---------------------------------------------------------------------------
# T3.5 — Atomic escalation_output.json write (RV-3.5)
# ---------------------------------------------------------------------------
class TestT35EscalationWriteIsAtomic:
    """The live operator-command channel must be crash-atomic: a failure
    mid-write must not truncate a previously-valid ``escalation_output.json``.

    Catches: reverting to the plain truncating ``open('w')`` that empties the
    file before ``json.dump`` runs.
    """

    def test_failed_write_preserves_prior_file(self, tmp_path, monkeypatch):
        art_dir = Path(srv._pipeline_artifacts_dir(str(tmp_path)))
        art_dir.mkdir(parents=True, exist_ok=True)
        json_path = art_dir / "escalation_output.json"
        json_path.write_text(json.dumps({"command": "PRIOR", "source": "ui"}))

        def boom(*a, **k):
            raise RuntimeError("simulated crash mid-write")

        monkeypatch.setattr(srv.json, "dump", boom)
        with pytest.raises(RuntimeError):
            srv._write_escalation_files(str(tmp_path), "RETRY")

        # Pre-fix: open('w') already truncated json_path → content lost.
        assert json.loads(json_path.read_text()) == {"command": "PRIOR", "source": "ui"}
        # No orphaned mkstemp temp left behind.
        leftovers = [p for p in art_dir.iterdir() if p.name.startswith("eo_")]
        assert leftovers == []

    def test_happy_path_writes_payload_then_done(self, tmp_path):
        srv._write_escalation_files(str(tmp_path), "PROCEED")
        art_dir = Path(srv._pipeline_artifacts_dir(str(tmp_path)))
        payload = json.loads((art_dir / "escalation_output.json").read_text())
        assert payload["command"] == "PROCEED"
        assert (art_dir / "escalation_output.done").exists()


# ---------------------------------------------------------------------------
# T3.6 — Atomic pipeline-project symlink swap (RV-3.7)
# ---------------------------------------------------------------------------
class TestT36AtomicSymlinkSwap:
    """A shared helper must swap a symlink atomically: the link is never
    absent, and a failed swap leaves the old link pointing where it did.

    Catches: the absence of the helper (the three remove-then-symlink sites),
    which leaves a window with no link and can drop it entirely on failure.
    """

    def test_swap_creates_link_when_absent(self, tmp_path):
        target = tmp_path / "t1"
        target.mkdir()
        link = tmp_path / "link"
        srv._atomic_symlink_swap(str(target), str(link))
        assert os.path.islink(link)
        assert os.path.realpath(link) == os.path.realpath(target)

    def test_swap_replaces_existing_link(self, tmp_path):
        t1 = tmp_path / "t1"; t1.mkdir()
        t2 = tmp_path / "t2"; t2.mkdir()
        link = tmp_path / "link"
        os.symlink(str(t1), str(link))
        srv._atomic_symlink_swap(str(t2), str(link))
        assert os.path.islink(link)
        assert os.path.realpath(link) == os.path.realpath(t2)

    def test_failed_swap_preserves_old_link(self, tmp_path, monkeypatch):
        t1 = tmp_path / "t1"; t1.mkdir()
        t2 = tmp_path / "t2"; t2.mkdir()
        link = tmp_path / "link"
        os.symlink(str(t1), str(link))

        def boom(*a, **k):
            raise OSError("simulated symlink failure")

        monkeypatch.setattr(srv.os, "symlink", boom)
        with pytest.raises(OSError):
            srv._atomic_symlink_swap(str(t2), str(link))

        # Old link must survive intact.
        assert os.path.islink(link)
        assert os.path.realpath(link) == os.path.realpath(t1)


# ---------------------------------------------------------------------------
# T3.3 — Preflight tolerates missing git (RV-2.6)
# ---------------------------------------------------------------------------
class TestT33PreflightToleratesMissingGit:
    """``_run_preflight_checks`` must report a structured "git not available"
    failure instead of crashing when the git binary is absent — the very
    misconfiguration it exists to diagnose.

    Catches: the un-try/except'd ``git --version`` probe and the unguarded
    later git calls raising ``FileNotFoundError``.
    """

    def _config(self, tmp_path):
        return {
            "openclaw_root": str(tmp_path / "openclaw"),
            "autodev_repo_path": str(Path(__file__).resolve().parents[1]),
        }

    def _patch_git_missing(self):
        real_run = subprocess.run

        def fake_run(cmd, *a, **k):
            if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "git":
                raise FileNotFoundError("git not found")
            return real_run(cmd, *a, **k)

        return patch("ui.server.subprocess.run", side_effect=fake_run)

    def test_missing_git_no_dotgit_returns_fail_check(self, tmp_path):
        repo = tmp_path / "repo"; repo.mkdir()
        with self._patch_git_missing():
            checks = srv._run_preflight_checks(str(repo), self._config(tmp_path))
        git_checks = [c for c in checks if c.get("check") == "git"]
        assert git_checks and git_checks[0]["status"] == "fail"

    def test_missing_git_with_dotgit_does_not_crash(self, tmp_path):
        """When ``.git`` exists but git is missing, the later branch-list
        block (pre-fix: unguarded) would raise FileNotFoundError."""
        repo = tmp_path / "repo"; repo.mkdir()
        (repo / ".git").mkdir()  # forces the os.path.exists(git_dir) branch
        with self._patch_git_missing():
            checks = srv._run_preflight_checks(str(repo), self._config(tmp_path))
        assert isinstance(checks, list)
        git_checks = [c for c in checks if c.get("check") == "git"]
        assert git_checks and git_checks[0]["status"] == "fail"


# ---------------------------------------------------------------------------
# T3.4 — Reject empty roadmap from /convert & /fix-roadmap-format (RV-5.4)
# ---------------------------------------------------------------------------
class TestT34RejectEmptyRoadmap:
    """A converter refusal/truncation (empty draft) must surface as a 502 and
    must NOT overwrite the prior good roadmap in session.json.

    Catches: gating success only on the ``.done`` sentinel, which lets an
    empty draft be persisted as HTTP 200.
    """

    @pytest.fixture(autouse=True)
    def _ideas(self, tmp_path):
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()
        self.prompt = tmp_path / "prompt.txt"
        self.prompt.write_text("Convert PRD to roadmap.")

    def _config(self):
        return {
            "ideas_dir": str(self.ideas_dir),
            "hooks_url": "http://localhost:18789/hooks/agent",
            "hooks_token": "test-token",
            "conversion_prompt_path": str(self.prompt),
            "autodev_repo_path": str(Path(__file__).resolve().parents[1]),
        }

    def _write_session(self, idea_id, **fields):
        d = self.ideas_dir / idea_id
        d.mkdir(parents=True, exist_ok=True)
        base = {"messages": [], "prd_content": "## Problem\nx",
                "created": "2026-01-01T00:00:00Z", "updated": "2026-01-01T00:00:00Z"}
        base.update(fields)
        (d / "session.json").write_text(json.dumps(base))
        return d

    def _mock_aiohttp(self):
        resp = MagicMock(); resp.status = 200
        sess = MagicMock()
        sess.post = AsyncMock(return_value=resp)
        sess.__aenter__ = AsyncMock(return_value=sess)
        sess.__aexit__ = AsyncMock(return_value=None)
        return MagicMock(return_value=sess)

    def test_convert_empty_roadmap_returns_502_and_preserves_prior(self):
        idea_dir = self._write_session("ce1", roadmap_content="PRIOR GOOD ROADMAP")

        async def write_empty(*a, **k):
            (idea_dir / "roadmap_draft.md").write_text("")       # empty refusal
            (idea_dir / "verification_draft.md").write_text("V")
            (idea_dir / "verification_draft.done").write_text("")
            (idea_dir / "roadmap_draft.done").write_text("")

        with patch("ui.server.load_config", return_value=self._config()), \
             patch("ui.server.aiohttp.ClientSession", self._mock_aiohttp()), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.CONVERT_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", new=write_empty):
            r = _client().post("/api/ideas/ce1/convert")

        assert r.status_code == 502
        session = json.loads((idea_dir / "session.json").read_text())
        assert session["roadmap_content"] == "PRIOR GOOD ROADMAP"

    def test_convert_requires_both_artifacts_nonempty(self):
        idea_dir = self._write_session("ce2", roadmap_content="PRIOR")

        async def write_empty_verification(*a, **k):
            (idea_dir / "roadmap_draft.md").write_text("# Roadmap\n- [ ] x")
            (idea_dir / "verification_draft.md").write_text("")   # empty verification
            (idea_dir / "verification_draft.done").write_text("")
            (idea_dir / "roadmap_draft.done").write_text("")

        with patch("ui.server.load_config", return_value=self._config()), \
             patch("ui.server.aiohttp.ClientSession", self._mock_aiohttp()), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.CONVERT_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", new=write_empty_verification):
            r = _client().post("/api/ideas/ce2/convert")

        assert r.status_code == 502

    def test_fix_format_empty_returns_502_and_preserves_prior(self):
        idea_dir = self._write_session("fe1", roadmap_content="PRIOR GOOD")

        async def blank_correction(*a, **k):
            (idea_dir / "roadmap_draft.md").write_text("")  # corrected → empty
            (idea_dir / "roadmap_draft.done").write_text("")

        with patch("ui.server.load_config", return_value=self._config()), \
             patch("ui.server.aiohttp.ClientSession", self._mock_aiohttp()), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.FORMAT_CORRECTION_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", new=blank_correction):
            r = _client().post("/api/ideas/fe1/fix-roadmap-format")

        assert r.status_code == 502
        session = json.loads((idea_dir / "session.json").read_text())
        assert session["roadmap_content"] == "PRIOR GOOD"


# ---------------------------------------------------------------------------
# T3.8 — Wire stale-.done scrub into chat-send (RV-Ideas)
# ---------------------------------------------------------------------------
class TestT38ChatSendScrubsStaleDone:
    """Chat-send must scrub a stale ``turns/{n}.done`` (mtime predating this
    attempt) BEFORE polling, so a retry on a reused turn number doesn't latch
    onto the prior attempt's reply.

    Catches: the missing ``_ideas_scrub_stale_turn_artifacts`` call — verified
    by confirming the stale sentinel is gone by the time the poll begins.
    """

    @pytest.fixture(autouse=True)
    def _ideas(self, tmp_path):
        self.ideas_dir = tmp_path / "ideas"
        self.ideas_dir.mkdir()

    def _config(self):
        return {
            "ideas_dir": str(self.ideas_dir),
            "hooks_url": "http://localhost:18789/hooks/agent",
            "hooks_token": "test-token",
        }

    def _mock_session(self):
        resp = MagicMock(); resp.status = 200
        resp.read = AsyncMock(return_value=b"")
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=None)
        sess = MagicMock()
        sess.post = AsyncMock(return_value=resp)
        sess.__aenter__ = AsyncMock(return_value=sess)
        sess.__aexit__ = AsyncMock(return_value=None)
        return sess

    def test_stale_done_removed_before_poll(self):
        idea_id = "scrub-wire"
        idea_dir = self.ideas_dir / idea_id
        turns = idea_dir / "turns"
        turns.mkdir(parents=True)
        (idea_dir / "session.json").write_text(json.dumps({
            "messages": [], "prd_content": "",
            "created": "2026-01-01T00:00:00Z", "updated": "2026-01-01T00:00:00Z",
        }))
        stale_done = turns / "1.done"
        stale_md = turns / "1.md"
        stale_done.write_text("")
        stale_md.write_text("OLD STALE REPLY")
        os.utime(stale_done, (1000, 1000))  # far in the past
        os.utime(stale_md, (1000, 1000))

        seen = {}

        async def spy_poll(**kwargs):
            dp = kwargs["done_path"]
            seen["done_existed_at_poll_start"] = Path(dp).exists()
            return PollResult(False, "timeout", None)  # force a clean 408

        with patch("ui.server.load_config", return_value=self._config()), \
             patch("ui.server.aiohttp.ClientSession", return_value=self._mock_session()), \
             patch("ui.server._poll_sentinel_with_idle_detect", new=spy_poll), \
             patch("asyncio.create_task"):
            _client().post(f"/api/ideas/{idea_id}/message",
                           json={"content": "retry please", "turn": 1})

        # Pre-fix: the stale .done still exists when the poll starts (True).
        assert seen.get("done_existed_at_poll_start") is False


# ---------------------------------------------------------------------------
# T3.9 — SSE ring-buffer must not drop repeated events (RV-1.6)
# ---------------------------------------------------------------------------
class _StopLoop(Exception):
    pass


class TestT39SseFallbackDeliversRepeats:
    """In the synthetic-fallback (ring-buffer) path the stream must deliver
    every event from the client's own queue, including a legitimately-repeated
    event.

    Catches: the ``if event != last_event`` dedup that silently drops repeats.
    """

    def test_repeated_event_is_delivered_twice(self, tmp_path, monkeypatch):
        cfg = {"events_path": str(tmp_path / "absent.jsonl")}  # missing → fallback
        monkeypatch.setattr(srv, "load_config", lambda: cfg)

        async def drive():
            before = set(srv._sse_clients)
            resp = await srv.events_stream()
            new = set(srv._sse_clients) - before
            q = new.pop()
            ev = {"event": "gate_pass", "phase": "p1"}
            q.put_nowait(ev)
            q.put_nowait(ev)  # identical repeat — must NOT be deduped away

            async def fake_sleep(_):
                raise _StopLoop()

            monkeypatch.setattr(srv.asyncio, "sleep", fake_sleep)
            chunks = []
            try:
                async for chunk in resp.body_iterator:
                    chunks.append(chunk)
            except _StopLoop:
                pass
            return "".join(chunks)

        data = asyncio.run(drive())
        # Both copies of the repeated event must appear (pre-fix: only one).
        assert data.count('"event": "gate_pass"') == 2
