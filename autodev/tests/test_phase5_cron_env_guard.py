"""Phase 5 — T5.1: cron entry-point env hardening.

Covers two behaviours:
  * ``env_resolvers.load_repo_env_file`` — the cron self-loads ``<repo>/.env``
    so a bare system-cron environment (no sourced ``.env``) still resolves the
    canonical roots. ``setdefault`` only: it never overrides an already-set var,
    and is a silent no-op when ``.env`` is absent.
  * ``heartbeat_cron.main`` — the fail-loud guard refuses to run (exit 1) when
    the resolved ``OPENCLAW_ROOT`` is not a directory, rather than restarting the
    orchestrator with a broken root. ``session_cleanup``'s twin guard is covered
    in ``test_phase5_session_cleanup.py``.

Why these tests: under system cron ``$HOME`` may be ``/`` or unset and ``.env``
is not sourced, so ``resolve_openclaw_root()`` silently resolved to ``/.openclaw``
and both crons no-op'd forever with no error. The self-loader removes the common
cause; the guard makes any residual misconfiguration loud instead of silent.
"""

import os
from unittest.mock import patch

import pytest

from env_resolvers import load_repo_env_file  # noqa: E402 - sys.path wired by conftest


@pytest.fixture
def restore_environ():
    """Snapshot ``os.environ`` and restore it exactly.

    ``load_repo_env_file`` mutates ``os.environ`` directly via ``setdefault``,
    which ``monkeypatch`` would not auto-undo, so leaked keys would bleed across
    tests. This fixture removes any key added during the test and reverts any
    changed value.
    """
    before = dict(os.environ)
    yield
    for key in list(os.environ.keys()):
        if key not in before:
            del os.environ[key]
    for key, value in before.items():
        if os.environ.get(key) != value:
            os.environ[key] = value


def _write_env(dir_path, body):
    (dir_path / ".env").write_text(body)


class TestLoadRepoEnvFile:
    def test_setdefaults_unset_vars(self, tmp_path, restore_environ):
        os.environ.pop("PHASE5_FOO", None)
        _write_env(tmp_path, "PHASE5_FOO=bar\n")
        load_repo_env_file(str(tmp_path))
        assert os.environ["PHASE5_FOO"] == "bar"

    def test_does_not_override_already_set_vars(self, tmp_path, restore_environ):
        os.environ["PHASE5_FOO"] = "preset"
        _write_env(tmp_path, "PHASE5_FOO=fromfile\n")
        load_repo_env_file(str(tmp_path))
        assert os.environ["PHASE5_FOO"] == "preset"

    def test_noop_when_env_file_missing(self, tmp_path, restore_environ):
        # No .env written — must not raise.
        load_repo_env_file(str(tmp_path))

    def test_skips_comments_and_blanks(self, tmp_path, restore_environ):
        os.environ.pop("PHASE5_REAL", None)
        os.environ.pop("PHASE5_COMMENTED", None)
        _write_env(
            tmp_path,
            "\n# a comment\n  # indented comment\nPHASE5_REAL=yes\n"
            "# PHASE5_COMMENTED=nope\n\n",
        )
        load_repo_env_file(str(tmp_path))
        assert os.environ["PHASE5_REAL"] == "yes"
        assert "PHASE5_COMMENTED" not in os.environ

    def test_strips_quotes_and_whitespace(self, tmp_path, restore_environ):
        for key in ("PHASE5_Q", "PHASE5_SP"):
            os.environ.pop(key, None)
        _write_env(tmp_path, 'PHASE5_Q="hello"\nPHASE5_SP = spaced \n')
        load_repo_env_file(str(tmp_path))
        assert os.environ["PHASE5_Q"] == "hello"
        assert os.environ["PHASE5_SP"] == "spaced"

    def test_splits_on_first_equals_only(self, tmp_path, restore_environ):
        os.environ.pop("PHASE5_URL", None)
        _write_env(tmp_path, "PHASE5_URL=http://x/?a=b&c=d\n")
        load_repo_env_file(str(tmp_path))
        assert os.environ["PHASE5_URL"] == "http://x/?a=b&c=d"

    def test_ignores_lines_without_equals(self, tmp_path, restore_environ):
        os.environ.pop("PHASE5_OK", None)
        _write_env(tmp_path, "this is not a kv line\nPHASE5_OK=1\n")
        load_repo_env_file(str(tmp_path))
        assert os.environ["PHASE5_OK"] == "1"


class TestHeartbeatMainGuard:
    """T5.1: heartbeat refuses to run (exit 1) when OPENCLAW_ROOT is not a dir.

    Heartbeat propagates ``OPENCLAW_ROOT`` to the orchestrator it restarts
    (``start_orchestrator`` -> ``env["OPENCLAW_ROOT"]``), so a broken root would
    spawn a broken orchestrator — worse than a no-op. The guard makes it loud.
    """

    def test_main_exits_when_openclaw_root_invalid(self, tmp_path, capsys):
        import heartbeat_cron

        bad_root = str(tmp_path / "does-not-exist")
        with (
            patch.object(heartbeat_cron, "OPENCLAW_ROOT", bad_root),
            patch("heartbeat_cron.run_heartbeat") as mock_run,
        ):
            with pytest.raises(SystemExit) as exc:
                heartbeat_cron.main()

        assert exc.value.code == 1
        mock_run.assert_not_called()
        assert "[CRITICAL]" in capsys.readouterr().out

    def test_main_proceeds_when_root_valid(self, tmp_path):
        import heartbeat_cron

        with (
            patch.object(heartbeat_cron, "OPENCLAW_ROOT", str(tmp_path)),
            patch("heartbeat_cron.run_heartbeat") as mock_run,
        ):
            heartbeat_cron.main()

        mock_run.assert_called_once()
