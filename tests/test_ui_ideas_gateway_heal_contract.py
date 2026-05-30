"""Static contract tests: Ideas gateway failure UX (late-heal, rollback UI hints)."""

import re


def load_index_html():
    with open("ui/index.html", "r") as f:
        return f.read()


def test_submit_message_502_503_start_shared_heal_poll():
    """502/503 (gateway down, agent never invoked) get the shared session-poll
    recovery. They now live in their OWN branch — 408 is handled separately by
    the deferred recovery poll (see ``test_ui_ideas_408_signal_based_revert``),
    so this no longer asserts a combined ``(408 || 502 || 503)`` condition."""
    html = load_index_html()
    assert "startSessionHealPoll" in html
    assert re.search(
        r"status\s*===\s*502\s*\|\|\s*status\s*===\s*503[\s\S]{0,700}?startSessionHealPoll",
        html,
    ), "Expected the 502/503 branch to start the shared heal poll"


def test_submit_message_gateway_failure_marks_ephemeral_pair_and_restores_input():
    """Gateway path flags optimistic rows and restores composer text for retry.

    The condition shape may include additional statuses (notably 408 — see
    ``test_ui_ideas_timeout_recovery_contract``) but 502 and 503 must remain
    in the heal-eligible set.  Regex below asserts the *substring*
    ``status === 502 || status === 503`` is present, without pinning the
    enclosing parens or extra terms.
    """
    html = load_index_html()
    assert "_gatewayFailed" in html, "Expected _gatewayFailed for ephemeral gateway-failed turns"
    assert re.search(
        r"status\s*===\s*502\s*\|\|\s*status\s*===\s*503[\s\S]{0,400}?setInputText\s*\(\s*t\s*\)",
        html,
    ), "Expected setInputText(t) reachable from the 502/503 branch when overrideText is undefined"
    assert re.search(
        r"messages\.filter\s*\(\s*\(\s*m\s*\)\s*=>\s*!\s*m\._gatewayFailed\s*\)",
        html,
    ), "Expected baseMsgs filter to drop prior _gatewayFailed rows before send"


def test_submit_message_catch_maps_gateway_failed_on_user_row():
    """User bubble must also carry _gatewayFailed so the pair is filtered on resubmit.

    Asserts 502/503 stay in the user-row marker; 408 coverage is pinned by
    the timeout-recovery contract suite.
    """
    html = load_index_html()
    assert re.search(
        r"status\s*===\s*502\s*\|\|\s*status\s*===\s*503[\s\S]{0,100}?&&\s*m\.id\s*===\s*userMsgId",
        html,
    ), "Expected user row to receive _gatewayFailed when status is 502/503"
