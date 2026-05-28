"""Static contract tests: Ideas chat 408 timeout draft recovery + reply auto-scroll.

The 502/503 paths in `submitMessage` (ui/index.html) already preserve UX
across gateway failures: the failed user row gets ``_gatewayFailed: true``,
the assistant error bubble gets the same flag, and the input text is restored
to the composer so the user can retry without re-typing.  The 408 path —
which is what fires on a real OpenClaw model stall after our backend
``_poll_sentinel_with_idle_detect`` migration — was never extended to the same
behaviour, so stacked red error bubbles accumulate in the chat (one per
retry) and the user re-types from scratch each time.

These contract tests pin the 408 path to the same three guarantees the
gateway heal contract pins for 502/503, plus one more guarantee for
auto-scroll on the new reply arriving: the existing ``messagesEndRef`` (line
~5626) must actually be scrolled into view via a ``useEffect`` that watches
the ``messages`` array.

Pattern mirrors :mod:`tests.test_ui_ideas_gateway_heal_contract` — read the
single-file React frontend as a string, regex for required code shapes.
"""

import re


def load_index_html():
    with open("ui/index.html", "r") as f:
        return f.read()


def test_submit_message_408_marks_user_row_with_gateway_failed():
    """408 must flag the user row so it is dropped from baseMsgs on resubmit.

    Mirrors the 502/503 guarantee at
    ``test_submit_message_catch_maps_gateway_failed_on_user_row`` — without
    this, the user's failed message stays in the chat history and the next
    send produces a duplicate user bubble + new error bubble side-by-side
    with the old pair (the visible stacking in the bug screenshot).
    """
    html = load_index_html()
    assert re.search(
        r"\(\s*status\s*===\s*408\s*\|\|\s*status\s*===\s*502\s*\|\|\s*status\s*===\s*503\s*\)"
        r"\s*&&\s*m\.id\s*===\s*userMsgId",
        html,
    ), "Expected user row to receive _gatewayFailed when status is 408 alongside 502/503"


def test_submit_message_408_marks_assistant_error_bubble_with_gateway_failed():
    """408 must flag the assistant error bubble itself so it is also dropped
    on resubmit.  Without this, even with the user row filtered the red
    bubble persists in baseMsgs and stacks across retries.
    """
    html = load_index_html()
    # The assistant-error mutation block spreads `_gatewayFailed: true` when
    # the status is in the heal-eligible set.  The shape currently is:
    #     ...((status === 502 || status === 503) ? { _gatewayFailed: true } : {})
    # — must be extended to 408.
    assert re.search(
        r"\(\s*status\s*===\s*408\s*\|\|\s*status\s*===\s*502\s*\|\|\s*status\s*===\s*503\s*\)"
        r"\s*\?\s*\{\s*_gatewayFailed:\s*true\s*\}",
        html,
    ), (
        "Expected the assistant error bubble's _gatewayFailed marker to "
        "include status 408 alongside 502/503"
    )


def test_submit_message_408_restores_input_text():
    """408 must restore the typed draft to the composer so the user can
    retry without re-typing — mirrors the 502/503 setInputText branch.
    """
    html = load_index_html()
    assert re.search(
        r"\(\s*status\s*===\s*408\s*\|\|\s*status\s*===\s*502\s*\|\|\s*status\s*===\s*503\s*\)"
        r"\s*&&\s*overrideText\s*===\s*undefined[\s\S]{0,300}?setInputText\s*\(\s*t\s*\)",
        html,
    ), "Expected setInputText(t) on 408 (alongside 502/503) when overrideText is undefined"


def test_messages_end_ref_scrolled_into_view_on_messages_change():
    """A ``useEffect`` must watch ``messages`` and call
    ``messagesEndRef.current?.scrollIntoView(...)`` so the chat auto-scrolls
    when the agent's reply replaces the pending placeholder.  Today the ref
    is allocated and attached but never scrolled, so the user has to scroll
    manually to see new replies (especially after staying scrolled up to
    read prior context).

    The trigger should *only* fire when the last item is a non-pending
    assistant message — i.e. the actual reply (or error) has landed — to
    avoid scrolling on the user's own send while their bubble is still
    pending.
    """
    html = load_index_html()
    # Single regex covering the useEffect's body and dependency array.
    # The effect must:
    #   (a) reference messagesEndRef.current and call scrollIntoView,
    #   (b) guard against scrolling while the last message is pending,
    #   (c) depend on `messages` so React re-runs it when the list mutates.
    assert re.search(
        r"useEffect\s*\(\s*\(\s*\)\s*=>\s*\{"
        r"[\s\S]{0,400}?messages\s*\[\s*messages\.length\s*-\s*1\s*\]"
        r"[\s\S]{0,400}?messagesEndRef\.current\??\.scrollIntoView"
        r"[\s\S]{0,200}?\}\s*,\s*\[\s*messages\s*\]\s*\)",
        html,
    ), (
        "Expected a useEffect watching `messages` that calls "
        "messagesEndRef.current?.scrollIntoView(...) when the last message "
        "is a non-pending assistant reply"
    )


def test_scroll_effect_guards_against_pending_last_message():
    """The scroll effect must NOT fire when the last message is the pending
    'Working on your request…' bubble — only on the actual reply/error
    transition.  Pin the guard shape so future refactors don't accidentally
    scroll on every user send.
    """
    html = load_index_html()
    assert re.search(
        r"useEffect\s*\(\s*\(\s*\)\s*=>\s*\{"
        r"[\s\S]{0,600}?!\s*\w+\.pending"
        r"[\s\S]{0,200}?scrollIntoView"
        r"[\s\S]{0,200}?\}\s*,\s*\[\s*messages\s*\]\s*\)",
        html,
    ), (
        "Expected the messages-watch useEffect to guard scrollIntoView with "
        "a `!last.pending` check (or equivalent) so it only scrolls on "
        "non-pending assistant arrivals"
    )


# ── reason-aware timeout messaging (backend is the sole message author) ───────

def test_submit_message_reads_server_error_body_on_failure():
    """On a non-ok response the submit handler must read the JSON body so it can
    surface the backend's reason-specific message — not just the status code.
    Pins that `r.json()` is consulted in the error path and a server message is
    carried on the thrown error."""
    html = load_index_html()
    # The !r.ok branch must parse the body and capture detail.message.
    assert re.search(
        r"if\s*\(\s*!\s*r\.ok\s*\)[\s\S]{0,400}?r\.json\(\)[\s\S]{0,200}?\.detail",
        html,
    ), "Expected the !r.ok branch to read r.json() and reference .detail (the backend reason/message body)"
    assert "serverMessage" in html, (
        "Expected a serverMessage field carried from the 408 body so the catch "
        "can render the backend's reason-specific text"
    )


def test_submit_message_408_prefers_server_message_over_generic():
    """The 408 branch must render the backend message when present, falling back
    to the generic copy only when absent — so the wording lives in ONE place
    (the backend `_ideas_timeout_message`), not duplicated per-reason here."""
    html = load_index_html()
    assert re.search(
        r"status\s*===\s*408[\s\S]{0,400}?serverMessage\s*\|\|",
        html,
    ), (
        "Expected the 408 branch to use `err.serverMessage || <generic fallback>` "
        "so reason-specific wording comes from the backend, not the frontend"
    )
