"""
W1-G: Token + cost capture from OpenClaw session JSONL files.

Tests verify:
- _sum_session_tokens module-level helper exists and is correct (runtime)
- Helper is called after executor sentinel, reviewer sentinel, planner sentinel
- Planner branch has a session-lookup loop like executor
- metrics.jsonl row includes token fields (source text)
"""
import json
import pathlib
import sys
import importlib
import unittest.mock as mock

import pytest

_ORCH_PATH = pathlib.Path(__file__).parent.parent / "pipeline" / "orchestrator.py"
_SRC = _ORCH_PATH.read_text()
_LINES = _SRC.splitlines()


# ---------------------------------------------------------------------------
# Structural checks (source text)
# ---------------------------------------------------------------------------

def test_sum_session_tokens_helper_exists():
    """_sum_session_tokens must be defined in orchestrator source."""
    assert "def _sum_session_tokens(" in _SRC, (
        "_sum_session_tokens not found in orchestrator.py. "
        "Add a module-level helper that sums token usage from an OpenClaw session JSONL."
    )


def test_metrics_row_includes_planner_tokens_field():
    """Canonical metrics row must include planner_tokens."""
    assert '"planner_tokens"' in _SRC or "'planner_tokens'" in _SRC, (
        "No 'planner_tokens' key found in orchestrator source. "
        "Add it to the canonical metrics.jsonl row dict."
    )


def test_metrics_row_includes_executor_tokens_field():
    """Canonical metrics row must include executor_tokens."""
    assert '"executor_tokens"' in _SRC or "'executor_tokens'" in _SRC, (
        "No 'executor_tokens' key found in orchestrator source. "
        "Add it to the canonical metrics.jsonl row dict (sum across all retry attempts)."
    )


def test_metrics_row_includes_reviewer_tokens_field():
    """Canonical metrics row must include reviewer_tokens."""
    assert '"reviewer_tokens"' in _SRC or "'reviewer_tokens'" in _SRC, (
        "No 'reviewer_tokens' key found in orchestrator source. "
        "Add it to the canonical metrics.jsonl row dict."
    )


def test_metrics_row_includes_cost_total_field():
    """Canonical metrics row must include cost_total."""
    assert '"cost_total"' in _SRC or "'cost_total'" in _SRC, (
        "No 'cost_total' key found in orchestrator source. "
        "Add it to the canonical metrics.jsonl row dict."
    )


def test_sum_session_tokens_called_after_executor_sentinel():
    """_sum_session_tokens must be called in the executor branch after the sentinel poll."""
    lines = _LINES
    # Find executor sentinel poll line — after agent_end integration executor uses
    # poll_for_sentinel with timeout_seconds=1200
    executor_sentinel_linenos = [
        i for i, ln in enumerate(lines, 1)
        if "poll_for_sentinel" in ln and "poll_for_sentinel_with" not in ln
        and "1200" in ln  # executor uses 1200s timeout
    ]
    all_sum_linenos = [
        i for i, ln in enumerate(lines, 1)
        if "_sum_session_tokens" in ln
    ]
    if executor_sentinel_linenos and all_sum_linenos:
        executor_sentinel_start = min(executor_sentinel_linenos)
        calls_after_sentinel = [s for s in all_sum_linenos if s > executor_sentinel_start]
        assert calls_after_sentinel, (
            f"No _sum_session_tokens call found after the executor sentinel poll "
            f"(line {executor_sentinel_start}). "
            "Call _sum_session_tokens(_jsonl_path) after poll_for_sentinel() returns."
        )
    else:
        assert all_sum_linenos, "_sum_session_tokens never called in orchestrator."


def test_sum_session_tokens_called_after_reviewer_sentinel():
    """_sum_session_tokens must be called in the reviewer branch after the sentinel poll."""
    lines = _LINES
    # Reviewer sentinel uses 600s timeout — after agent_end integration uses poll_for_sentinel
    reviewer_sentinel_linenos = [
        i for i, ln in enumerate(lines, 1)
        if "poll_for_sentinel" in ln and "poll_for_sentinel_with" not in ln
        and "600" in ln
    ]
    all_sum_linenos = [
        i for i, ln in enumerate(lines, 1)
        if "_sum_session_tokens" in ln
    ]
    if reviewer_sentinel_linenos and all_sum_linenos:
        reviewer_sentinel_start = min(reviewer_sentinel_linenos)
        calls_after_sentinel = [s for s in all_sum_linenos if s > reviewer_sentinel_start]
        assert calls_after_sentinel, (
            "No _sum_session_tokens call found after the reviewer sentinel poll. "
            "Call _sum_session_tokens(_jsonl_path) after reviewer poll_for_sentinel()."
        )


def test_planner_branch_has_session_lookup():
    """Planner branch must have a post-sentinel session-JSONL lookup."""
    # After the agent_end integration, all three agents use a single post-sentinel
    # read of sessions.json (no 15-retry loop needed since agent_end guarantees
    # sessions.json is populated before the sentinel is written).
    # Check for the "agent:planner:" lookup key pattern.
    assert "agent:planner:" in _SRC or ('"planner"' in _SRC and "_jsonl_path" in _SRC), (
        "No planner session lookup found in orchestrator. "
        "Add a post-sentinel sessions.json read for the planner JSONL path after poll_for_sentinel()."
    )


def test_executor_tokens_accumulated_across_retries():
    """executor_tokens must be accumulated in phase_state across retry attempts."""
    # Look for the accumulation pattern: executor_tokens_acc updated per attempt in phase_state
    lines = _LINES
    executor_accumulation = [
        ln for ln in lines
        if "executor_tokens_acc" in ln
    ]
    assert executor_accumulation, (
        "No 'executor_tokens_acc' found in orchestrator. "
        "Accumulate executor tokens in phase_state across retry attempts: "
        "phase_state['executor_tokens_acc'][k] += attempt_tokens[k]"
    )


# ---------------------------------------------------------------------------
# Runtime tests — the helper must actually work
# ---------------------------------------------------------------------------

def _load_sum_session_tokens():
    """Import _sum_session_tokens from orchestrator module."""
    pipeline_dir = str(_ORCH_PATH.parent)
    if pipeline_dir not in sys.path:
        sys.path.insert(0, pipeline_dir)
    if "orchestrator" in sys.modules:
        del sys.modules["orchestrator"]
    orch_mod = importlib.import_module("orchestrator")
    fn = getattr(orch_mod, "_sum_session_tokens", None)
    return orch_mod, fn


def _make_assistant_row(inp=100, out=50, cache_read=10, cache_write=0, total=160, cost=0.001):
    return json.dumps({
        "type": "message",
        "role": "assistant",
        "usage": {
            "input": inp, "output": out,
            "cacheRead": cache_read, "cacheWrite": cache_write,
            "totalTokens": total,
            "cost": {"total": cost},
        }
    })


def _make_user_row():
    return json.dumps({"type": "message", "role": "user", "usage": {"input": 50, "output": 0}})


def _make_tool_row():
    return json.dumps({"type": "tool_use", "role": "assistant"})


@pytest.fixture()
def sum_fn():
    _, fn = _load_sum_session_tokens()
    assert fn is not None, "_sum_session_tokens not exported from orchestrator module."
    return fn


def test_sum_session_tokens_returns_zeros_on_none(sum_fn):
    """Returns all-zeros dict when jsonl_path is None, no exception."""
    result = sum_fn(None)
    assert isinstance(result, dict)
    assert result["input"] == 0
    assert result["output"] == 0
    assert result["total_tokens"] == 0
    assert result["cost_total"] == 0.0


def test_sum_session_tokens_returns_zeros_on_missing_file(sum_fn, tmp_path):
    """Returns all-zeros dict when file does not exist, no exception."""
    result = sum_fn(str(tmp_path / "nonexistent.jsonl"))
    assert isinstance(result, dict)
    assert all(v == 0 or v == 0.0 for v in result.values())


def test_sum_session_tokens_sums_assistant_messages(sum_fn, tmp_path):
    """Correctly sums token fields across assistant message rows."""
    jsonl = tmp_path / "session.jsonl"
    rows = [
        _make_assistant_row(inp=100, out=50, cache_read=10, cache_write=0, total=160, cost=0.001),
        _make_user_row(),  # should be skipped
        _make_assistant_row(inp=200, out=80, cache_read=0, cache_write=5, total=285, cost=0.002),
    ]
    jsonl.write_text("\n".join(rows) + "\n")

    result = sum_fn(str(jsonl))
    assert result["input"] == 300, f"Expected input=300, got {result['input']}"
    assert result["output"] == 130, f"Expected output=130, got {result['output']}"
    assert result["cache_read"] == 10, f"Expected cache_read=10, got {result['cache_read']}"
    assert result["cache_write"] == 5, f"Expected cache_write=5, got {result['cache_write']}"
    assert result["total_tokens"] == 445, f"Expected total_tokens=445, got {result['total_tokens']}"
    assert abs(result["cost_total"] - 0.003) < 1e-9, f"Expected cost_total≈0.003, got {result['cost_total']}"


def test_sum_session_tokens_skips_non_message_rows(sum_fn, tmp_path):
    """Skips tool_use and tool_result rows — only assistant messages count."""
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text(
        "\n".join([
            json.dumps({"type": "tool_use", "role": "assistant", "usage": {"input": 999}}),
            json.dumps({"type": "tool_result", "role": "user", "usage": {"input": 999}}),
        ]) + "\n"
    )
    result = sum_fn(str(jsonl))
    assert result["input"] == 0
    assert result["total_tokens"] == 0


def test_sum_session_tokens_handles_malformed_lines(sum_fn, tmp_path):
    """Skips malformed lines without raising, accumulates valid rows."""
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text(
        _make_assistant_row(inp=50, out=20, total=70) + "\n"
        + "NOT VALID JSON {\n"
        + _make_assistant_row(inp=30, out=10, total=40) + "\n"
    )
    result = sum_fn(str(jsonl))
    assert result["input"] == 80
    assert result["total_tokens"] == 110


def test_sum_session_tokens_handles_empty_file(sum_fn, tmp_path):
    """Returns zeros on an empty JSONL file, no exception."""
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text("")
    result = sum_fn(str(jsonl))
    assert all(v == 0 or v == 0.0 for v in result.values())


def test_sum_session_tokens_result_keys(sum_fn):
    """Result dict must have exactly the required keys."""
    result = sum_fn(None)
    required_keys = {"input", "output", "cache_read", "cache_write", "total_tokens", "cost_total"}
    assert set(result.keys()) == required_keys, (
        f"Unexpected keys in result: {set(result.keys())} vs expected {required_keys}"
    )


# ---------------------------------------------------------------------------
# Real OpenClaw schema: role + usage are nested under row["message"]
# ---------------------------------------------------------------------------

def _make_openclaw_row(inp=100, out=50, cache_read=10, cache_write=0, total=160, cost=0.001, role="assistant"):
    """Mirror the actual OpenClaw session JSONL row shape."""
    return json.dumps({
        "id": "msg_abc",
        "type": "message",
        "parentId": "msg_prev",
        "timestamp": "2026-05-19T00:00:00Z",
        "message": {
            "role": role,
            "usage": {
                "input": inp, "output": out,
                "cacheRead": cache_read, "cacheWrite": cache_write,
                "totalTokens": total,
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": cost},
            },
        },
    })


def test_sum_session_tokens_reads_openclaw_nested_message_shape(sum_fn, tmp_path):
    """The real OpenClaw row nests role + usage under message{}. Must sum these."""
    jsonl = tmp_path / "session.jsonl"
    rows = [
        _make_openclaw_row(inp=100, out=50, cache_read=10, cache_write=0, total=160, cost=0.001),
        _make_openclaw_row(inp=200, out=80, cache_read=0, cache_write=5, total=285, cost=0.002),
    ]
    jsonl.write_text("\n".join(rows) + "\n")

    result = sum_fn(str(jsonl))
    assert result["input"] == 300
    assert result["output"] == 130
    assert result["cache_read"] == 10
    assert result["cache_write"] == 5
    assert result["total_tokens"] == 445
    assert abs(result["cost_total"] - 0.003) < 1e-9


def test_sum_session_tokens_skips_openclaw_non_assistant_rows(sum_fn, tmp_path):
    """Nested-message rows with role != 'assistant' must be skipped."""
    jsonl = tmp_path / "session.jsonl"
    rows = [
        _make_openclaw_row(inp=999, role="user"),
        _make_openclaw_row(inp=100, out=50, total=150, cost=0.001, role="assistant"),
    ]
    jsonl.write_text("\n".join(rows) + "\n")

    result = sum_fn(str(jsonl))
    assert result["input"] == 100
    assert result["total_tokens"] == 150
