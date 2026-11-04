"""A ``.done`` written by a session the orchestrator replaced must not end the attempt
that replaced it.

Observed live (MultiLife E2E on DeepSeek V4.1 Flash, 2026-11-03): the orchestrator
steer-aborted a stalled executor attempt and started attempt 2. ``sessions.steer``
always spawns a follow-up "stop here" turn on the aborted session; when that turn
ended 36 s into attempt 2, the autodev-pipeline-signals ``agent_end`` backstop wrote
``executor_output.done`` into the artifacts dir attempt 2 was polling. The
``min_sentinel_mtime`` guard cannot tell (the file is newer than attempt 2's start),
so attempt 2's poll took the sentinel as its own completion at 198 s while attempt 2
was still working; the gate failed it, attempt 3's start steer-aborted attempt 2, and
attempt 2's stop turn then did the same to attempt 3.

The backstop now writes the ending session's key into the sentinel, and
``poll_for_sentinel(expected_session_key=...)`` discards a sentinel naming a different
pipeline session. A sentinel the agent wrote itself (empty, per AGENTS.md) counts as
before.
"""

import ast
import os
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sentinel_poller as sp  # noqa: E402

# The shapes seen live: the orchestrator's key for attempt 2, and attempt 1's key as
# the gateway hands it to the plugin (agent:<role>: prefix, lowercased).
ATTEMPT_1_AS_GATEWAY = "agent:executor:pipeline:phase-0:core-e1:executor-attempt-1-r2018eb06"
ATTEMPT_2 = "pipeline:phase-0:CORE-E1:executor-attempt-2-r2018eb06"


def _write(path, body):
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)


def _write_later(path, body, delay):
    def run():
        time.sleep(delay)
        _write(path, body)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_sentinel_from_a_superseded_session_does_not_end_this_attempt(tmp_path):
    done = tmp_path / "executor_output.done"
    _write(done, ATTEMPT_1_AS_GATEWAY)  # the replaced attempt's late agent_end backstop
    writer = _write_later(done, "", 1.5)  # this attempt then finishes and writes its own

    start = time.monotonic()
    result = sp.poll_for_sentinel(
        str(done), timeout_seconds=15, expected_session_key=ATTEMPT_2
    )
    elapsed = time.monotonic() - start
    writer.join(timeout=5)

    assert result.success and result.reason == "succeeded"
    assert elapsed >= 1.5, "the superseded session's sentinel ended this attempt"


def test_superseded_sentinel_is_removed_and_named_in_the_log(tmp_path, capsys):
    done = tmp_path / "executor_output.done"
    _write(done, ATTEMPT_1_AS_GATEWAY)

    result = sp.poll_for_sentinel(
        str(done), timeout_seconds=3, expected_session_key=ATTEMPT_2
    )

    assert not result.success and result.reason == "timeout"
    assert not done.exists()
    out = capsys.readouterr().out
    assert "Stray sentinel discarded" in out
    assert ATTEMPT_1_AS_GATEWAY in out and ATTEMPT_2 in out


def test_superseded_sentinel_never_reaches_the_verdict_acceptor(tmp_path):
    # Live, attempt 2 had already written an early executor_output.json, so the
    # verdict-hold acceptor accepted the stray sentinel as soon as it was consulted.
    done = tmp_path / "executor_output.done"
    _write(done, ATTEMPT_1_AS_GATEWAY)
    acceptor = MagicMock(return_value=True)
    writer = _write_later(done, "", 1.5)

    start = time.monotonic()
    result = sp.poll_for_sentinel(
        str(done),
        timeout_seconds=15,
        expected_session_key=ATTEMPT_2,
        sentinel_acceptor=acceptor,
    )
    elapsed = time.monotonic() - start
    writer.join(timeout=5)

    assert result.success
    assert elapsed >= 1.5
    assert acceptor.call_count == 1, "consulted once, for this attempt's own sentinel"


def test_backstop_sentinel_of_this_attempt_counts(tmp_path):
    # The gateway lowercases the key and adds agent:<role>:; it is still this attempt.
    done = tmp_path / "executor_output.done"
    _write(done, "agent:executor:" + ATTEMPT_2.lower())

    result = sp.poll_for_sentinel(
        str(done), timeout_seconds=5, expected_session_key=ATTEMPT_2
    )

    assert result.success and result.reason == "succeeded"
    assert done.exists()


@pytest.mark.parametrize("body", ["", "\n", "done", "DONE\n", "complete"])
def test_sentinel_the_agent_wrote_counts(tmp_path, body):
    # Agents write .done themselves (empty, per AGENTS.md); no session key, no judgement.
    done = tmp_path / "executor_output.done"
    _write(done, body)

    result = sp.poll_for_sentinel(
        str(done), timeout_seconds=5, expected_session_key=ATTEMPT_2
    )

    assert result.success and result.reason == "succeeded"


def test_a_key_outside_the_pipeline_namespace_is_not_judged(tmp_path):
    done = tmp_path / "executor_output.done"
    _write(done, "agent:prd-creator:ideas:idea-1:session-3")

    result = sp.poll_for_sentinel(
        str(done), timeout_seconds=5, expected_session_key=ATTEMPT_2
    )

    assert result.success and result.reason == "succeeded"


def test_without_an_expected_key_any_sentinel_counts(tmp_path):
    done = tmp_path / "executor_output.done"
    _write(done, ATTEMPT_1_AS_GATEWAY)

    result = sp.poll_for_sentinel(str(done), timeout_seconds=5)

    assert result.success and result.reason == "succeeded"


def test_every_orchestrator_poll_passes_its_session_key():
    with open(os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "poll_for_sentinel"
    ]
    assert len(calls) >= 4, "planner, executor, reviewer and completion-review polls"
    for call in calls:
        keywords = {kw.arg: kw.value for kw in call.keywords}
        value = keywords.get("expected_session_key")
        assert isinstance(value, ast.Name) and value.id == "session_key", (
            f"poll_for_sentinel at orchestrator.py:{call.lineno} must pass "
            "expected_session_key=session_key"
        )
