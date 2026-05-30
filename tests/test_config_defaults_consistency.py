"""Guard against drift between the THREE places an Ideas-poll knob is declared.

The Ideas chat timeout knobs (`poll_timeout`, `ideas_idle_threshold`) are
declared in three spots that must agree:

1. ``ui/server.py`` ``DEFAULTS`` dict — the production source of truth
   (``load_config`` does ``DEFAULTS.copy()`` then ``.update(user_config)``,
   so a key present here ALWAYS wins over the module-constant fallback).
2. ``ui/server.py`` ``POLL_TIMEOUT`` constant — the fallback used by tests that
   patch ``ui.server.POLL_TIMEOUT`` with a mock config that omits the key.
3. ``ui/config.example.json`` — the template a fresh operator copies to
   ``ui/config.json``; any value here becomes a permanent override.

These drifted once already: a fix bumped the ``POLL_TIMEOUT`` constant to 900 s
but left ``DEFAULTS["poll_timeout"]`` at 180 s, so the intended 900 s backstop
never took effect in production (the constant fallback was unreachable). And
``config.example.json`` carried a stale ``ideas_idle_threshold: 120`` that, when
copied, would silently re-introduce the false-timeout bug. These tests make any
such drift fail loudly.
"""
import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_EXAMPLE = _REPO_ROOT / "ui" / "config.example.json"

# Knobs whose example value would OVERRIDE the code default on a fresh copy, so
# the template must match DEFAULTS exactly.
_TUNABLE_KEYS = ("poll_timeout", "ideas_idle_threshold")


@pytest.fixture(scope="module")
def server_defaults():
    from ui.server import DEFAULTS
    return DEFAULTS


@pytest.fixture(scope="module")
def config_example():
    return json.loads(_CONFIG_EXAMPLE.read_text())


def test_poll_timeout_constant_matches_defaults(server_defaults):
    """``POLL_TIMEOUT`` (test-patch fallback) and ``DEFAULTS["poll_timeout"]``
    (production value) must be equal — otherwise the effective production
    backstop silently diverges from what the constant claims, which is exactly
    the bug that shipped a 180 s backstop while the constant said 900 s."""
    from ui.server import POLL_TIMEOUT

    assert POLL_TIMEOUT == server_defaults["poll_timeout"], (
        f"POLL_TIMEOUT constant ({POLL_TIMEOUT}) != "
        f"DEFAULTS['poll_timeout'] ({server_defaults['poll_timeout']}). "
        "load_config merges DEFAULTS, so DEFAULTS wins in production — keep "
        "the two in sync or the constant change is a no-op."
    )


def test_effective_poll_timeout_is_the_intended_backstop():
    """The value the chat endpoint actually uses
    (``config.get("poll_timeout", POLL_TIMEOUT)`` on a default load) must be the
    intended generous backstop, not the old tight 180 s."""
    from ui.server import POLL_TIMEOUT, load_config

    cfg = load_config()
    effective = cfg.get("poll_timeout", POLL_TIMEOUT)
    assert effective >= 900, (
        f"effective Ideas poll_timeout is {effective}s — expected >= 900s. "
        "A thorough multi-call PRD turn can exceed 180s; the hard backstop must "
        "not kill it. (Check DEFAULTS['poll_timeout'], not just the constant.)"
    )


def test_ideas_idle_threshold_default_survives_long_opaque_model_call(server_defaults):
    """The stall threshold must exceed the longest legitimate single model call
    (118 s measured live for a PRD draft)."""
    assert server_defaults["ideas_idle_threshold"] >= 300, (
        f"ideas_idle_threshold default is {server_defaults['ideas_idle_threshold']}s "
        "— too tight; a 118s opaque PRD-draft call would false-stall"
    )


def test_config_example_tunables_match_code_defaults(server_defaults, config_example):
    """``ui/config.example.json`` must not carry stale tunable values: a fresh
    operator copying it to ``config.json`` would override the code defaults. Any
    listed tunable must equal the corresponding DEFAULTS value."""
    for key in _TUNABLE_KEYS:
        if key in config_example:
            assert config_example[key] == server_defaults[key], (
                f"config.example.json[{key!r}]={config_example[key]} but "
                f"DEFAULTS[{key!r}]={server_defaults[key]} — a fresh copy would "
                f"override the code default with a stale value. Sync the template."
            )
