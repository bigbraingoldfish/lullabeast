"""Unit tests for inference-provider rejection heuristics and JSONL error extraction."""

import json

from orchestrator import (
    _is_provider_rejected_error,
    _session_jsonl_last_assistant_error_message,
)


def _assistant_error_row(error_message: str) -> str:
    """Build one JSONL line matching OpenClaw session shape (role/stopReason inside ``message``)."""
    return (
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
        + "\n"
    )


def test_is_provider_rejected_error_billing():
    assert _is_provider_rejected_error(
        "402 This request requires more credits, or fewer max_tokens. "
        "You requested up to 32000 tokens, but can only afford 6331."
    )
    assert _is_provider_rejected_error("more credits")
    assert _is_provider_rejected_error("can only afford 100")
    assert _is_provider_rejected_error("monthly limit")


def test_is_provider_rejected_error_rate_limit():
    assert _is_provider_rejected_error("429 Too Many Requests")
    assert _is_provider_rejected_error("rate limit exceeded")


def test_is_provider_rejected_error_auth():
    assert _is_provider_rejected_error("401 Unauthorized")
    assert _is_provider_rejected_error("invalid api key")
    assert _is_provider_rejected_error("incorrect api key")
    assert _is_provider_rejected_error("Incorrect API key provided")


def test_is_provider_rejected_error_false_for_safe_strings():
    assert not _is_provider_rejected_error("")
    assert not _is_provider_rejected_error("500 Internal Server Error")
    assert not _is_provider_rejected_error("connection timeout")


def test_session_jsonl_last_error_message_reads_last_assistant_error(tmp_path):
    p = tmp_path / "session.jsonl"
    p.write_text(
        _assistant_error_row("first error") + _assistant_error_row("402 Payment Required"),
        encoding="utf-8",
    )
    assert _session_jsonl_last_assistant_error_message(str(p)) == "402 Payment Required"
