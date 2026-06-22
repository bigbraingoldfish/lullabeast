"""Opt-in retry for *transient* provider rejections (PROVIDER_ERROR_RETRY).

``PROVIDER_ERROR_RETRY`` is an integer **count** (default 0 = disabled = fail-fast).
A positive N retries a transient provider rejection (rate-limit) up to N times by
re-invoking the SAME agent in place (current_agent unchanged → the caller's
``continue`` re-runs it), each on a fresh session key (``-pr{N}`` suffix) and
WITHOUT consuming the agent's own self-failure retry budget. Terminal rejections
(401/402, invalid key, insufficient credits) always escalate immediately.

Bare-Orchestrator + stubbed-paths pattern mirrors
``test_executor_self_failure_feedback.py``.
"""

import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402
from orchestrator import (  # noqa: E402
    _is_transient_provider_error,
    _is_provider_rejected_error,
    provider_error_retry_limit,
)


# --------------------------------------------------------------------------- #
# Pure classifier: transient (retryable) vs terminal provider rejections.
# --------------------------------------------------------------------------- #

def test_transient_matches_rate_limit_codes():
    assert _is_transient_provider_error("429 Provider returned error")  # the live case
    assert _is_transient_provider_error("429 Too Many Requests")
    assert _is_transient_provider_error("rate limit exceeded")
    assert _is_transient_provider_error("Rate Limit reached, slow down")


def test_transient_false_for_terminal_billing_and_auth():
    assert not _is_transient_provider_error("401 Unauthorized")
    assert not _is_transient_provider_error("invalid api key")
    assert not _is_transient_provider_error(
        "402 This request requires more credits, or fewer max_tokens. "
        "You requested up to 32000 tokens, but can only afford 6331."
    )
    assert not _is_transient_provider_error("monthly limit")
    assert not _is_transient_provider_error("")


def test_transient_is_a_strict_subset_of_provider_rejected():
    for msg in ("429 Provider returned error", "rate limit exceeded", "429 Too Many Requests"):
        assert _is_provider_rejected_error(msg)
        assert _is_transient_provider_error(msg)


# --------------------------------------------------------------------------- #
# Count resolver — integer, NOT a 1/0 toggle.
# --------------------------------------------------------------------------- #

def test_limit_default_zero(monkeypatch):
    monkeypatch.delenv("PROVIDER_ERROR_RETRY", raising=False)
    assert provider_error_retry_limit() == 0


@pytest.mark.parametrize("val,expected", [("1", 1), ("3", 3), ("10", 10), ("0", 0)])
def test_limit_parses_integer(monkeypatch, val, expected):
    monkeypatch.setenv("PROVIDER_ERROR_RETRY", val)
    assert provider_error_retry_limit() == expected


@pytest.mark.parametrize("val", ["true", "yes", "on", "abc", "5 retries", "", "-4"])
def test_limit_non_integer_or_negative_is_zero(monkeypatch, val):
    # A non-numeric/negative value must read as 0 (disabled), never silently enable
    # an unbounded retry — the operator must give an explicit count.
    monkeypatch.setenv("PROVIDER_ERROR_RETRY", val)
    assert provider_error_retry_limit() == 0


# --------------------------------------------------------------------------- #
# Integration: _escalate_if_provider_rejected routing.
# --------------------------------------------------------------------------- #

def _bare_orch(tmp_path, monkeypatch, phase_state):
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(tmp_path))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json"))
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json"))
    (tmp_path / "phase_state.json").write_text(json.dumps(phase_state))

    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = {
        "current_phase": 1,
        "current_phase_raw_id": "CORE-E2",
        "current_agent": "executor",
        "executor_retries": 0,
        "reviewer_retries": 0,
        "planner_retries": 0,
        "pipeline_status": "RUNNING",
        "project_path": str(tmp_path),
        "run_id": "test-run",
    }
    orch.openclaw_config = {}
    orch.lock_fd = None
    orch._current_attempt_retry_class = "initial_attempt"
    return orch


def _jsonl_with_error(tmp_path, error_message):
    p = tmp_path / "session.jsonl"
    p.write_text(
        json.dumps(
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "stopReason": "error",
                    "errorMessage": error_message,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return str(p)


def test_transient_escalates_when_count_is_zero(tmp_path, monkeypatch):
    """Default (0): a transient 429 escalates on the first failure."""
    monkeypatch.delenv("PROVIDER_ERROR_RETRY", raising=False)
    orch = _bare_orch(tmp_path, monkeypatch, {})
    jsonl = _jsonl_with_error(tmp_path, "429 Provider returned error")

    escalated = orch._escalate_if_provider_rejected(jsonl, "Executor")

    assert escalated is True
    assert orch.state["current_agent"] == "escalation"
    assert orch.read_phase_state().get("last_error_code") == orch_mod.ERR_PROVIDER_REJECTED


def test_transient_retries_in_place_when_budget_remains(tmp_path, monkeypatch):
    """Count=3, 0 used: a transient 429 re-invokes the SAME agent (no escalation),
    increments the counter, and does not touch the executor self-failure budget."""
    monkeypatch.setenv("PROVIDER_ERROR_RETRY", "3")
    orch = _bare_orch(tmp_path, monkeypatch, {"provider_error_retries": 0})
    jsonl = _jsonl_with_error(tmp_path, "429 Provider returned error")

    handled = orch._escalate_if_provider_rejected(jsonl, "Executor")

    assert handled is True  # caller continues...
    assert orch.state["current_agent"] == "executor"  # ...but re-runs the SAME agent
    assert orch.state["executor_retries"] == 0  # self-failure budget untouched
    assert orch.read_phase_state().get("provider_error_retries") == 1
    assert orch.read_phase_state().get("last_error_code") != orch_mod.ERR_PROVIDER_REJECTED


def test_transient_escalates_when_budget_exhausted(tmp_path, monkeypatch):
    """Count=3, already 3 used: the next transient 429 escalates."""
    monkeypatch.setenv("PROVIDER_ERROR_RETRY", "3")
    orch = _bare_orch(tmp_path, monkeypatch, {"provider_error_retries": 3})
    jsonl = _jsonl_with_error(tmp_path, "429 Provider returned error")

    escalated = orch._escalate_if_provider_rejected(jsonl, "Executor")

    assert escalated is True
    assert orch.state["current_agent"] == "escalation"
    assert orch.read_phase_state().get("last_error_code") == orch_mod.ERR_PROVIDER_REJECTED


def test_count_gives_exactly_n_retries(tmp_path, monkeypatch):
    """End-to-end counter walk: with N=3, three consecutive 429s retry in place,
    the fourth escalates."""
    monkeypatch.setenv("PROVIDER_ERROR_RETRY", "3")
    orch = _bare_orch(tmp_path, monkeypatch, {"provider_error_retries": 0})
    jsonl = _jsonl_with_error(tmp_path, "429 rate limit")

    for expected_used in (1, 2, 3):
        assert orch._escalate_if_provider_rejected(jsonl, "Executor") is True
        assert orch.state["current_agent"] == "executor"
        assert orch.read_phase_state().get("provider_error_retries") == expected_used

    # 4th occurrence: budget exhausted → escalate.
    assert orch._escalate_if_provider_rejected(jsonl, "Executor") is True
    assert orch.state["current_agent"] == "escalation"


def test_terminal_always_escalates_even_with_budget(tmp_path, monkeypatch):
    """A TERMINAL rejection (402 insufficient credits) escalates immediately even
    with retry budget available."""
    monkeypatch.setenv("PROVIDER_ERROR_RETRY", "3")
    orch = _bare_orch(tmp_path, monkeypatch, {"provider_error_retries": 0})
    jsonl = _jsonl_with_error(
        tmp_path,
        "402 This request requires more credits, or fewer max_tokens. "
        "You requested up to 32000 tokens, but can only afford 6331.",
    )

    escalated = orch._escalate_if_provider_rejected(jsonl, "Executor")

    assert escalated is True
    assert orch.state["current_agent"] == "escalation"
    assert orch.read_phase_state().get("provider_error_retries", 0) == 0  # never counted


def test_non_provider_outcome_resets_consecutive_counter(tmp_path, monkeypatch):
    """A non-provider outcome (the provider worked) breaks the streak: the
    consecutive counter is reset so a later 429 gets the full budget again."""
    monkeypatch.setenv("PROVIDER_ERROR_RETRY", "3")
    orch = _bare_orch(tmp_path, monkeypatch, {"provider_error_retries": 2})
    jsonl = _jsonl_with_error(tmp_path, "500 Internal Server Error")  # not a provider rejection

    handled = orch._escalate_if_provider_rejected(jsonl, "Executor")

    assert handled is False
    assert orch.state["current_agent"] == "executor"
    assert orch.read_phase_state().get("provider_error_retries") == 0


def test_provider_retry_suffix_keys_off_counter(tmp_path, monkeypatch):
    """The session-key suffix is empty at 0 (legacy shape) and ``-pr{N}`` while a
    provider retry is in flight, so each re-invoke gets a fresh OpenClaw session."""
    orch = _bare_orch(tmp_path, monkeypatch, {"provider_error_retries": 0})
    assert orch._provider_retry_suffix() == ""
    (tmp_path / "phase_state.json").write_text(json.dumps({"provider_error_retries": 2}))
    assert orch._provider_retry_suffix() == "-pr2"
