"""Unit tests for the shared logging primitives (autodev/pipeline/log_utils.py).

These pin the two contracts the rest of LAUNCH-8 depends on:
  * ``tagged`` emits a contiguous ``[TAG] message`` token (the substring the
    orchestrator's capsys-based observability tests rely on), and
  * ``configure_stream_logging`` is idempotent, re-binds to a swapped stream, and
    honors level/propagate/logfile — generalizing the old ``_ensure_stdout_logging``.

Tests use explicit ``io.StringIO`` streams and uniquely-named loggers so they are
deterministic and don't pollute the root logger or each other.
"""

import io
import logging
import os

import pytest

from log_utils import configure_stream_logging, resolve_log_level, set_level, tagged


# ---------------------------------------------------------------------------
# tagged()
# ---------------------------------------------------------------------------

def test_tagged_emits_contiguous_token():
    buf = io.StringIO()
    lg = configure_stream_logging("test.tagged.contiguous", logging.INFO, stream=buf, propagate=False)
    tagged("FOO", "bar", logger=lg)
    assert "[FOO] bar" in buf.getvalue()


def test_tagged_empty_message():
    buf = io.StringIO()
    lg = configure_stream_logging("test.tagged.empty", logging.INFO, stream=buf, propagate=False)
    tagged("PING", logger=lg)
    assert "[PING]" in buf.getvalue()


def test_tagged_debug_suppressed_at_info():
    buf = io.StringIO()
    lg = configure_stream_logging("test.tagged.debug", logging.INFO, stream=buf, propagate=False)
    tagged("FOO", "hidden", level=logging.DEBUG, logger=lg)
    tagged("FOO", "shown", level=logging.INFO, logger=lg)
    out = buf.getvalue()
    assert "hidden" not in out
    assert "[FOO] shown" in out


def test_tagged_debug_visible_when_logger_at_debug():
    buf = io.StringIO()
    lg = configure_stream_logging("test.tagged.debugon", logging.DEBUG, stream=buf, propagate=False)
    tagged("FOO", "now-shown", level=logging.DEBUG, logger=lg)
    assert "[FOO] now-shown" in buf.getvalue()


# ---------------------------------------------------------------------------
# configure_stream_logging()
# ---------------------------------------------------------------------------

def test_configure_idempotent_same_stream():
    buf = io.StringIO()
    lg = configure_stream_logging("test.idem", logging.INFO, stream=buf, propagate=False)
    n1 = len(lg.handlers)
    configure_stream_logging("test.idem", logging.INFO, stream=buf, propagate=False)
    assert len(lg.handlers) == n1  # no duplicate handler on re-invoke


def test_configure_rebinds_to_new_stream():
    buf1, buf2 = io.StringIO(), io.StringIO()
    lg = configure_stream_logging("test.rebind", logging.INFO, stream=buf1, propagate=False)
    n1 = len(lg.handlers)
    configure_stream_logging("test.rebind", logging.INFO, stream=buf2, propagate=False)
    assert len(lg.handlers) == n1 + 1  # a fresh handler for the swapped stream
    tagged("T", "msg", logger=lg)
    assert "[T] msg" in buf2.getvalue()


def test_configure_named_sets_level_and_propagate():
    buf = io.StringIO()
    lg = configure_stream_logging("test.named", logging.DEBUG, stream=buf, propagate=False)
    assert lg.level == logging.DEBUG
    assert lg.propagate is False


def test_configure_custom_format_preserved():
    buf = io.StringIO()
    lg = configure_stream_logging(
        "test.fmt", logging.INFO, stream=buf,
        fmt="%(levelname)s:%(name)s:%(message)s", propagate=False,
    )
    tagged("X", "y", logger=lg)
    out = buf.getvalue()
    assert out.startswith("INFO:test.fmt:")
    assert "[X] y" in out


def test_configure_logfile_written(tmp_path):
    buf = io.StringIO()
    logpath = str(tmp_path / "x.log")
    lg = configure_stream_logging(
        "test.logfile", logging.INFO, stream=buf, logfile=logpath, propagate=False
    )
    tagged("F", "filemsg", logger=lg)
    for h in lg.handlers:
        h.flush()
    assert os.path.exists(logpath)
    with open(logpath) as fh:
        assert "[F] filemsg" in fh.read()


def test_set_level_updates_logger_and_handlers():
    # A bare logger.setLevel would leave the handler (created at INFO) filtering DEBUG.
    buf = io.StringIO()
    lg = configure_stream_logging("test.setlevel", logging.INFO, stream=buf, propagate=False)
    tagged("X", "hidden", level=logging.DEBUG, logger=lg)
    assert "hidden" not in buf.getvalue()  # filtered at INFO

    set_level(logging.DEBUG, "test.setlevel")
    assert lg.level == logging.DEBUG
    assert lg.handlers and all(h.level == logging.DEBUG for h in lg.handlers)
    tagged("X", "shown", level=logging.DEBUG, logger=lg)
    assert "[X] shown" in buf.getvalue()  # now visible — handler level was updated too


def test_configure_logfile_unwritable_does_not_raise():
    buf = io.StringIO()
    # A non-existent parent dir would raise OSError opening the FileHandler; the
    # helper must swallow it (best-effort file logging never blocks startup).
    lg = configure_stream_logging(
        "test.badlog", logging.INFO, stream=buf,
        logfile="/nonexistent-dir-xyzzy/x.log", propagate=False,
    )
    assert lg is not None
    tagged("F", "still-works", logger=lg)
    assert "[F] still-works" in buf.getvalue()


# ---------------------------------------------------------------------------
# resolve_log_level()
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("DEBUG", logging.DEBUG),
    ("debug", logging.DEBUG),
    ("  info ", logging.INFO),
    ("WARNING", logging.WARNING),
    ("ERROR", logging.ERROR),
    ("CRITICAL", logging.CRITICAL),
    ("", logging.INFO),
    (None, logging.INFO),
    ("loud", logging.INFO),
    ("5 min", logging.INFO),
])
def test_resolve_log_level(value, expected):
    assert resolve_log_level(value) == expected


def test_resolve_log_level_passthrough_int():
    assert resolve_log_level(logging.DEBUG) == logging.DEBUG


def test_resolve_log_level_bool_is_default():
    # bool is an int subclass — must NOT be treated as a level integer.
    assert resolve_log_level(True) == logging.INFO
    assert resolve_log_level(False) == logging.INFO


def test_resolve_log_level_custom_default():
    assert resolve_log_level("nope", logging.WARNING) == logging.WARNING
