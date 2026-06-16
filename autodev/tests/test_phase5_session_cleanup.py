"""Phase 5 — session_cleanup rewrite (reframed T5.3/T5.4 + path/schema fix).

The cron was a double silent no-op: it read the wrong path
(``OPENCLAW_ROOT/workspace-{agent}/sessions/sessions.json``, absent) and the
wrong schema (``store.get("sessions", [])``; the real file is a **flat dict
keyed by sessionKey**). The rewrite:
  * reads ``OPENCLAW_ROOT/agents/{agent}/sessions/sessions.json`` (flat dict);
  * prunes entries older than ``TTL_DAYS`` by a millisecond ``updatedAt``;
  * keeps (and warns) on a missing/zero/non-numeric/bool/seconds-magnitude
    ``updatedAt`` instead of treating it as epoch-old and deleting it;
  * persists the pruned index atomically **before** deleting transcripts;
  * deletes the ``.jsonl`` transcript + its trajectory siblings, only ever
    inside the agent's own ``sessions/`` dir (boundary-checked);
  * leaves the escalation agent's sessions untouched (audit trail);
  * refuses to run (exit 1) when ``OPENCLAW_ROOT`` is not a directory.

All fixtures are hermetic ``tmp_path`` stores — the real ``~/.openclaw`` is never
touched (with live-delete, running the real cron here would bulk-prune ~150
sessions).
"""

import glob
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import session_cleanup  # noqa: E402 - sys.path wired by conftest


def _ms(dt):
    return int(dt.timestamp() * 1000)


NOW = datetime.now(timezone.utc)
OLD_MS = _ms(NOW - timedelta(days=40))      # older than the 30-day TTL
RECENT_MS = _ms(NOW - timedelta(days=1))    # well within the TTL


def _seed(root, agent, sessions):
    """Create a flat-dict ``sessions.json`` + transcript siblings for *agent*.

    *sessions* is a list of ``(session_key, session_id, updated_at_or_None)``.
    Returns the agent's ``sessions/`` directory.
    """
    sdir = os.path.join(root, "agents", agent, "sessions")
    os.makedirs(sdir, exist_ok=True)
    store = {}
    for key, sid, updated in sessions:
        entry = {"sessionId": sid, "sessionFile": os.path.join(sdir, f"{sid}.jsonl")}
        if updated is not None:
            entry["updatedAt"] = updated
        store[key] = entry
        for suffix in (".jsonl", ".trajectory.jsonl", ".trajectory-path.json"):
            with open(os.path.join(sdir, sid + suffix), "w") as handle:
                handle.write("x")
    with open(os.path.join(sdir, "sessions.json"), "w") as handle:
        json.dump(store, handle)
    return sdir


def _read_store(sdir):
    with open(os.path.join(sdir, "sessions.json")) as handle:
        return json.load(handle)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Point the module at a hermetic OpenClaw root; neutralise log rotation."""
    root = tmp_path / "openclaw"
    root.mkdir()
    pipeline = tmp_path / "pipeline"
    pipeline.mkdir()
    monkeypatch.setattr(session_cleanup, "OPENCLAW_ROOT", str(root))
    monkeypatch.setattr(session_cleanup, "AUTODEV_PIPELINE_ROOT", str(pipeline))
    monkeypatch.setattr(session_cleanup, "rotate_pipeline_logs", lambda: None)
    return session_cleanup, str(root)


class TestPruning:
    def test_prunes_old_session_at_correct_path_and_schema(self, env):
        mod, root = env
        sdir = _seed(root, "executor", [
            ("k_old", "uuid-old", OLD_MS),
            ("k_recent", "uuid-recent", RECENT_MS),
        ])
        with patch.object(mod, "AGENTS", ["executor"]):
            mod.cleanup_sessions()

        store = _read_store(sdir)
        assert "k_old" not in store          # pruned
        assert "k_recent" in store           # kept
        assert isinstance(store, dict)       # flat-dict schema preserved
        assert not os.path.exists(os.path.join(sdir, "uuid-old.jsonl"))
        assert os.path.exists(os.path.join(sdir, "uuid-recent.jsonl"))

    def test_deletes_trajectory_siblings(self, env):
        mod, root = env
        sdir = _seed(root, "executor", [("k_old", "uuid-old", OLD_MS)])
        with patch.object(mod, "AGENTS", ["executor"]):
            mod.cleanup_sessions()
        for suffix in (".jsonl", ".trajectory.jsonl", ".trajectory-path.json"):
            assert not os.path.exists(os.path.join(sdir, "uuid-old" + suffix))

    def test_missing_zero_and_invalid_updatedAt_are_kept(self, env, caplog):
        mod, root = env
        sdir = _seed(root, "executor", [
            ("k_missing", "uuid-missing", None),       # no updatedAt at all
            ("k_zero", "uuid-zero", 0),                # epoch
            ("k_bool", "uuid-bool", True),             # bool (int subclass)
            ("k_seconds", "uuid-seconds", 1_700_000_000),  # seconds, not ms
            ("k_old", "uuid-old", OLD_MS),             # genuinely old -> pruned
        ])
        with patch.object(mod, "AGENTS", ["executor"]), caplog.at_level(logging.WARNING):
            mod.cleanup_sessions()

        store = _read_store(sdir)
        for kept in ("k_missing", "k_zero", "k_bool", "k_seconds"):
            assert kept in store, f"{kept} must be kept (invalid updatedAt)"
        assert "k_old" not in store
        # At least one keep-and-warn was emitted for the invalid timestamps.
        assert any("updatedAt" in rec.message for rec in caplog.records)

    def test_string_updatedAt_is_kept(self, env):
        mod, root = env
        sdir = _seed(root, "executor", [("k_str", "uuid-str", "not-a-number")])
        with patch.object(mod, "AGENTS", ["executor"]):
            mod.cleanup_sessions()
        assert "k_str" in _read_store(sdir)


class TestDurability:
    def test_index_persisted_before_transcript_deletion(self, env):
        """If transcript deletion fails, the pruned index must already be on disk."""
        mod, root = env
        sdir = _seed(root, "executor", [
            ("k_old", "uuid-old", OLD_MS),
            ("k_recent", "uuid-recent", RECENT_MS),
        ])
        with (
            patch.object(mod, "AGENTS", ["executor"]),
            patch.object(mod, "_delete_session_transcripts",
                         side_effect=RuntimeError("disk gone")),
        ):
            mod.cleanup_sessions()  # per-agent except logs the error; must not raise

        store = _read_store(sdir)
        assert "k_old" not in store      # index was persisted first
        assert "k_recent" in store

    def test_sessions_json_write_is_atomic(self, env):
        mod, root = env
        sdir = _seed(root, "executor", [
            ("k_old", "uuid-old", OLD_MS),
            ("k_recent", "uuid-recent", RECENT_MS),
        ])
        real_replace = os.replace
        replaced = []

        def spy(src, dst):
            replaced.append(dst)
            return real_replace(src, dst)

        with (
            patch.object(mod, "AGENTS", ["executor"]),
            patch("atomic_io.os.replace", side_effect=spy),
        ):
            mod.cleanup_sessions()

        assert any(dst.endswith("sessions.json") for dst in replaced)  # atomic rename
        assert not glob.glob(os.path.join(sdir, "*.tmp"))              # no leftover temp


class TestSafety:
    def test_escalation_sessions_untouched(self, env):
        """The escalation agent's audit trail is never pruned."""
        mod, root = env
        sdir = _seed(root, "escalation", [("k_old", "uuid-old", OLD_MS)])
        with patch.object(mod, "AGENTS", ["escalation"]):
            mod.cleanup_sessions()
        assert "k_old" in _read_store(sdir)
        assert os.path.exists(os.path.join(sdir, "uuid-old.jsonl"))

    def test_transcript_outside_sessions_dir_not_followed(self, env, tmp_path):
        """A sessionFile pointing outside the sessions dir must not be deleted."""
        mod, root = env
        sdir = os.path.join(root, "agents", "executor", "sessions")
        os.makedirs(sdir, exist_ok=True)
        outside = tmp_path / "outside-evil.jsonl"
        outside.write_text("precious")
        store = {
            "k_evil": {
                "sessionId": "outside-evil",
                "sessionFile": str(outside),     # absolute, outside sessions_dir
                "updatedAt": OLD_MS,
            }
        }
        with open(os.path.join(sdir, "sessions.json"), "w") as handle:
            json.dump(store, handle)

        with patch.object(mod, "AGENTS", ["executor"]):
            mod.cleanup_sessions()

        assert "k_evil" not in _read_store(sdir)   # entry pruned from the index
        assert outside.exists()                     # outside file preserved

    def test_missing_sessions_file_skips_agent(self, env):
        mod, root = env
        # No sessions.json for 'planner'; an old one for 'executor'.
        sdir = _seed(root, "executor", [("k_old", "uuid-old", OLD_MS)])
        with patch.object(mod, "AGENTS", ["planner", "executor"]):
            mod.cleanup_sessions()  # must not raise on the missing planner store
        assert "k_old" not in _read_store(sdir)


class TestDryRun:
    def test_dry_run_reports_but_changes_nothing(self, env, caplog):
        """``dry_run=True`` logs the would-prune counts but writes nothing and
        deletes no transcript — the safety valve for the first enable."""
        mod, root = env
        sdir = _seed(root, "executor", [
            ("k_old", "uuid-old", OLD_MS),
            ("k_recent", "uuid-recent", RECENT_MS),
        ])
        before = _read_store(sdir)
        with patch.object(mod, "AGENTS", ["executor"]), caplog.at_level(logging.INFO):
            mod.cleanup_sessions(dry_run=True)

        # Index untouched and every transcript (even the stale one) still on disk.
        assert _read_store(sdir) == before
        assert "k_old" in _read_store(sdir)
        for suffix in (".jsonl", ".trajectory.jsonl", ".trajectory-path.json"):
            assert os.path.exists(os.path.join(sdir, "uuid-old" + suffix))
        # But it DID report the would-be prune.
        assert any("[DRY-RUN]" in rec.message and "would delete" in rec.message
                   for rec in caplog.records)

    def test_dry_run_writes_no_temp_file(self, env):
        """No atomic-write temp should be created in dry-run mode."""
        mod, root = env
        sdir = _seed(root, "executor", [("k_old", "uuid-old", OLD_MS)])
        with patch.object(mod, "AGENTS", ["executor"]):
            mod.cleanup_sessions(dry_run=True)
        assert not glob.glob(os.path.join(sdir, "*.tmp"))

    def test_main_passes_dry_run_from_argv(self, tmp_path):
        """``--dry-run`` on the CLI reaches ``cleanup_sessions(dry_run=True)``."""
        with (
            patch.object(session_cleanup, "OPENCLAW_ROOT", str(tmp_path)),
            patch.object(session_cleanup, "cleanup_sessions") as mock_clean,
            patch.object(session_cleanup.sys, "argv",
                         ["session_cleanup.py", "--dry-run"]),
        ):
            session_cleanup.main()
        mock_clean.assert_called_once_with(dry_run=True)

    def test_main_dry_run_via_env_var(self, tmp_path, monkeypatch):
        """``SESSION_CLEANUP_DRY_RUN=1`` also routes to dry-run."""
        monkeypatch.setenv("SESSION_CLEANUP_DRY_RUN", "1")
        with (
            patch.object(session_cleanup, "OPENCLAW_ROOT", str(tmp_path)),
            patch.object(session_cleanup, "cleanup_sessions") as mock_clean,
            patch.object(session_cleanup.sys, "argv", ["session_cleanup.py"]),
        ):
            session_cleanup.main()
        mock_clean.assert_called_once_with(dry_run=True)


class TestReporting:
    """A run must never be silent — the '???' case where nothing is old enough
    to prune still has to confirm it ran and what it scanned."""

    def test_dry_run_with_nothing_old_still_reports(self, env, caplog):
        mod, root = env
        _seed(root, "executor", [("k_recent", "uuid-recent", RECENT_MS)])
        with patch.object(mod, "AGENTS", ["executor"]), caplog.at_level(logging.INFO):
            mod.cleanup_sessions(dry_run=True)
        text = caplog.text.lower()
        assert "would delete 0" in text          # per-agent line fired with 0
        assert "cleanup complete (dry-run)" in text  # summary always fires

    def test_live_run_with_nothing_old_still_reports(self, env, caplog):
        mod, root = env
        _seed(root, "executor", [("k_recent", "uuid-recent", RECENT_MS)])
        with patch.object(mod, "AGENTS", ["executor"]), caplog.at_level(logging.INFO):
            mod.cleanup_sessions()
        text = caplog.text.lower()
        assert "nothing older than" in text
        assert "cleanup complete (live)" in text

    def test_summary_counts_across_agents(self, env, caplog):
        mod, root = env
        _seed(root, "planner", [("p_old", "uuid-p-old", OLD_MS)])
        _seed(root, "executor", [("e_recent", "uuid-e-recent", RECENT_MS)])
        with patch.object(mod, "AGENTS", ["planner", "executor"]), caplog.at_level(logging.INFO):
            mod.cleanup_sessions()
        # 2 agents scanned, 1 pruned (planner), 1 kept (executor).
        assert "scanned 2 agent(s); pruned 1 session(s), kept 1" in caplog.text


class TestMainGuard:
    def test_main_exits_when_openclaw_root_invalid(self, tmp_path, capsys):
        bad_root = str(tmp_path / "nope")
        with (
            patch.object(session_cleanup, "OPENCLAW_ROOT", bad_root),
            patch.object(session_cleanup, "cleanup_sessions") as mock_clean,
        ):
            with pytest.raises(SystemExit) as exc:
                session_cleanup.main()
        assert exc.value.code == 1
        mock_clean.assert_not_called()
        assert "[CRITICAL]" in capsys.readouterr().out

    def test_main_runs_cleanup_when_root_valid(self, tmp_path):
        with (
            patch.object(session_cleanup, "OPENCLAW_ROOT", str(tmp_path)),
            patch.object(session_cleanup, "cleanup_sessions") as mock_clean,
        ):
            session_cleanup.main()
        mock_clean.assert_called_once()
