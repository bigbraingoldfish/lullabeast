"""TDD guard for honest reviewer model-hard-error attribution (v0.1.1).

A reviewer CONTRACT_FAILURE = the session ended without a parseable
reviewer_output.json. Two very different causes produce it:

  1. The reviewer genuinely gave up / was cut off — no error row.
  2. The reviewer's MODEL hard-errored mid-turn (stopReason:"error" — e.g. a
     500/server_error from GPU contention or model eviction on the shared local
     host). The reviewer did real work; the inference call failed.

Before this change both escalated with the same generic "reviewer ended without a
verdict (gave up or was cut off)" message + ERR_REVIEWER_CONTRACT_FAILURE, giving
the operator no signal that case 2 was infrastructure. ``_compose_contract_failure_escalation``
distinguishes them by reading the session JSONL's last assistant error row (reusing
``_session_jsonl_last_assistant_error_message``): a hard-error gets the real message
+ ERR_REVIEWER_MODEL_ERROR; a genuine give-up keeps the generic message +
ERR_REVIEWER_CONTRACT_FAILURE. Retry behaviour is unchanged — only the terminal
escalation label.
"""
import json
import os
import sys

OPENCLAW_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in [os.path.join(OPENCLAW_DIR, "autodev", "pipeline"), OPENCLAW_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from error_codes import ERR_REVIEWER_CONTRACT_FAILURE, ERR_REVIEWER_MODEL_ERROR
from orchestrator import (
    _compose_contract_failure_escalation,
    _is_provider_rejected_error,
    _is_recoverable_context_overflow,
)


def _assistant_error_row(error_message: str) -> str:
    """One OpenClaw session JSONL line: an assistant row that ended in error."""
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


def _assistant_text_row(text: str) -> str:
    """A normal (non-error) assistant row — a genuine end-of-turn with no error."""
    return (
        json.dumps(
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "stopReason": "end_turn",
                    "content": [{"type": "text", "text": text}],
                },
            }
        )
        + "\n"
    )


def test_hard_error_surfaces_model_error_code(tmp_path):
    """A reviewer session ending in a 500/server_error → honest reason naming the
    real error + ERR_REVIEWER_MODEL_ERROR (the live image-500 / contention case)."""
    p = tmp_path / "session.jsonl"
    p.write_text(
        _assistant_error_row("500 image input is not supported - hint: provide the mmproj"),
        encoding="utf-8",
    )

    reason, code = _compose_contract_failure_escalation(str(p), 3)

    assert code == ERR_REVIEWER_MODEL_ERROR, "a model hard-error must map to ERR_REVIEWER_MODEL_ERROR"
    assert "image input is not supported" in reason, "the real inference error must be surfaced"
    assert "gave up" not in reason.lower(), "a hard-error must NOT be labelled a give-up"


def test_genuine_giveup_keeps_contract_failure(tmp_path):
    """A reviewer session with no error row (genuine give-up / cut-off) keeps the
    generic message + ERR_REVIEWER_CONTRACT_FAILURE."""
    p = tmp_path / "session.jsonl"
    p.write_text(_assistant_text_row("thinking about the review..."), encoding="utf-8")

    reason, code = _compose_contract_failure_escalation(str(p), 3)

    assert code == ERR_REVIEWER_CONTRACT_FAILURE
    assert "gave up or was cut off" in reason
    assert "CONTRACT_FAILURE_SOFT_RETRY_EXHAUSTED" in reason


def test_missing_session_file_keeps_contract_failure(tmp_path):
    """No resolvable session file (None / absent) is indistinguishable from a
    give-up → keep the safe generic CONTRACT_FAILURE label, never crash."""
    reason, code = _compose_contract_failure_escalation(None, 3)
    assert code == ERR_REVIEWER_CONTRACT_FAILURE
    assert "gave up or was cut off" in reason

    reason2, code2 = _compose_contract_failure_escalation(str(tmp_path / "nope.jsonl"), 3)
    assert code2 == ERR_REVIEWER_CONTRACT_FAILURE


def test_provider_rejection_is_not_a_model_hard_error(tmp_path):
    """Ordering guard: provider rejections (401/402/429) are peeled off UPSTREAM by
    _escalate_if_provider_rejected before the CONTRACT_FAILURE handler runs, so they
    never reach the composer. This documents that a 429 IS a provider rejection (and
    would have been handled earlier) — the composer is only ever reached for the
    non-provider error subset (e.g. 500)."""
    assert _is_provider_rejected_error("429 Too Many Requests")
    assert not _is_provider_rejected_error(
        "500 image input is not supported - hint: provide the mmproj"
    )


def test_context_overflow_is_not_a_model_hard_error(tmp_path):
    """A reviewer session that terminated on a recoverable CONTEXT OVERFLOW is a
    context-size problem, not a model-host failure. Labelling it ERR_REVIEWER_MODEL_ERROR
    would tell the operator to "check the model host — the implementation may well be
    correct," sending them to debug healthy infrastructure while the real cause (the
    phase exceeds the reviewer model's context) goes unaddressed. It must keep the
    give-up label instead. (Regression for the overflow mislabel: the prior code
    treated ANY error row as a model hard-error.)"""
    overflow_msg = (
        "Context overflow: estimated context size exceeds safe threshold during tool loop."
    )
    p = tmp_path / "session.jsonl"
    p.write_text(_assistant_error_row(overflow_msg), encoding="utf-8")

    reason, code = _compose_contract_failure_escalation(str(p), 3)

    assert code == ERR_REVIEWER_CONTRACT_FAILURE, (
        "a recoverable context overflow must NOT map to ERR_REVIEWER_MODEL_ERROR"
    )
    assert "model host" not in reason.lower()
    # Sanity: the message really is one _is_recoverable_context_overflow recognises,
    # so the exclusion above is exercising the intended branch.
    assert _is_recoverable_context_overflow(overflow_msg)


def test_recovered_then_gaveup_keeps_contract_failure(tmp_path):
    """An error the session RECOVERED past — an earlier error row, but the turn then
    continued and ended on a clean assistant row — is not how the turn ended. It is a
    give-up, not a terminal model hard-error. Guards the "last error anywhere in the
    log" trigger-happiness: the classifier must look at the session's TERMINAL state,
    not just any historical error. (Regression: the prior code returned the last error
    row regardless of whether the session recovered after it.)"""
    p = tmp_path / "session.jsonl"
    p.write_text(
        _assistant_error_row("500 internal server error")
        + _assistant_text_row("recovered; reviewed but wrote no verdict file"),
        encoding="utf-8",
    )

    reason, code = _compose_contract_failure_escalation(str(p), 3)

    assert code == ERR_REVIEWER_CONTRACT_FAILURE, (
        "an error the session recovered past (last assistant row is a clean end) "
        "must not be labelled a terminal model hard-error"
    )
    assert "gave up or was cut off" in reason


def test_terminal_hard_error_after_prior_content_still_model_error(tmp_path):
    """Positive case preserved: when the session ENDS on a genuine (non-overflow)
    error row — even after earlier non-error content — it is a terminal model
    hard-error and must still map to ERR_REVIEWER_MODEL_ERROR with the real message."""
    p = tmp_path / "session.jsonl"
    p.write_text(
        _assistant_text_row("starting the review...")
        + _assistant_error_row("500 server_error: model evicted"),
        encoding="utf-8",
    )

    reason, code = _compose_contract_failure_escalation(str(p), 3)

    assert code == ERR_REVIEWER_MODEL_ERROR
    assert "500 server_error: model evicted" in reason
    assert "gave up" not in reason.lower()
