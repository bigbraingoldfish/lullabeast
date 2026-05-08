"""Static contract tests: Ideas gateway failure UX (late-heal, rollback UI hints)."""

import re


def load_index_html():
    with open("ui/index.html", "r") as f:
        return f.read()


def test_submit_message_late_heal_includes_502_and_503():
    """502/503 should start the same session poll recovery as 408 (gateway vs stall)."""
    html = load_index_html()
    assert re.search(
        r"if\s*\(\s*status\s*===\s*408\s*\|\|\s*status\s*===\s*502\s*\|\|\s*status\s*===\s*503\s*\)",
        html,
    ), "Expected late-heal branch to include 502 and 503 alongside 408"


def test_submit_message_gateway_failure_marks_ephemeral_pair_and_restores_input():
    """Gateway path flags optimistic rows and restores composer text for retry."""
    html = load_index_html()
    assert "_gatewayFailed" in html, "Expected _gatewayFailed for ephemeral gateway-failed turns"
    assert re.search(
        r"\(status\s*===\s*502\s*\|\|\s*status\s*===\s*503\)[\s\S]{0,400}?setInputText\s*\(\s*t\s*\)",
        html,
    ), "Expected setInputText(t) in 502/503 branch when overrideText is undefined"
    assert re.search(
        r"messages\.filter\s*\(\s*\(\s*m\s*\)\s*=>\s*!\s*m\._gatewayFailed\s*\)",
        html,
    ), "Expected baseMsgs filter to drop prior _gatewayFailed rows before send"


def test_submit_message_catch_maps_gateway_failed_on_user_row():
    """User bubble must also carry _gatewayFailed so the pair is filtered on resubmit."""
    html = load_index_html()
    assert re.search(
        r"\(\s*status\s*===\s*502\s*\|\|\s*status\s*===\s*503\s*\)\s*&&\s*m\.id\s*===\s*userMsgId",
        html,
    ), "Expected user row to receive _gatewayFailed when status is 502/503"
