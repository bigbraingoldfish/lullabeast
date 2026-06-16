"""LAUNCH-8 follow-up (review findings) — the `log_level` config key is functional.

The first cut configured the loggers at import from UI_LOG_LEVEL only, so a user who set
``"log_level": "DEBUG"`` in ui/config.json got a config that *reported* DEBUG while the
loggers actually ran at INFO. load_config now applies the resolved level (env wins over
config.json) to the already-created loggers via log_utils.set_level. Also pins that the
readiness logger keeps its pre-LAUNCH-8 stderr destination.
"""

import json
import logging
import sys

import pytest

import ui.server as server


@pytest.fixture(autouse=True)
def _restore_log_levels():
    """Snapshot + restore the two server loggers' levels around each test so the global
    logging state does not leak DEBUG/WARNING into the rest of the suite."""
    names = ("autodev.readiness", "autodev.ui")
    saved = {
        n: (logging.getLogger(n).level, [h.level for h in logging.getLogger(n).handlers])
        for n in names
    }
    yield
    for n in names:
        lg = logging.getLogger(n)
        lvl, hlvls = saved[n]
        lg.setLevel(lvl)
        for h, hl in zip(lg.handlers, hlvls):
            h.setLevel(hl)


def _write_cfg(tmp_path, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return str(p)


def test_config_log_level_debug_actually_applies(tmp_path, monkeypatch):
    monkeypatch.delenv("UI_LOG_LEVEL", raising=False)
    cfg = server.load_config(config_path=_write_cfg(tmp_path, {"log_level": "DEBUG"}))
    assert cfg["log_level"] == "DEBUG"  # reported
    # ...and actually applied to both loggers AND their handlers (the real fix).
    for name in ("autodev.readiness", "autodev.ui"):
        lg = logging.getLogger(name)
        assert lg.level == logging.DEBUG, f"{name} logger level not applied"
        assert all(h.level == logging.DEBUG for h in lg.handlers), f"{name} handler level not applied"


def test_env_wins_over_config_and_applies(tmp_path, monkeypatch):
    monkeypatch.setenv("UI_LOG_LEVEL", "WARNING")
    cfg = server.load_config(config_path=_write_cfg(tmp_path, {"log_level": "DEBUG"}))
    assert cfg["log_level"] == "WARNING"  # env wins over config.json
    assert logging.getLogger("autodev.readiness").level == logging.WARNING


def test_garbage_log_level_degrades_to_info(tmp_path, monkeypatch):
    monkeypatch.delenv("UI_LOG_LEVEL", raising=False)
    cfg = server.load_config(config_path=_write_cfg(tmp_path, {"log_level": "loud"}))
    assert cfg["log_level"] == "INFO"
    assert logging.getLogger("autodev.ui").level == logging.INFO


def test_readiness_logger_streams_to_stderr():
    """Finding 4: the readiness logger must keep its pre-LAUNCH-8 stderr destination
    (logging.StreamHandler() defaulted to stderr; configure_stream_logging defaults to
    stdout, so the readiness call passes stream=sys.stderr explicitly)."""
    rl = logging.getLogger("autodev.readiness")
    stream_handlers = [
        h for h in rl.handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    ]
    assert stream_handlers, "readiness logger should have a stream handler"
    assert any(getattr(h, "stream", None) is sys.stderr for h in stream_handlers), (
        "readiness logger's stream handler must target stderr"
    )
