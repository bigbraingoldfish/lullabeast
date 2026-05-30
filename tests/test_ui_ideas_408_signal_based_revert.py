"""Static contract tests: Ideas chat reverts the draft only on a DEFINITIVE signal.

Background. The Ideas backend used to give the agent only ~30 s to produce its
first activity stamp; if it didn't, it returned HTTP 408 (`no_first_activity`)
and the frontend *immediately* yanked the typed message back into the composer
and showed a red error — even though the agent was usually just slow to start
and a reply landed moments later. That 30 s check is a PREMATURE signal.

New contract (signal-based revert):
  • The backend chat send now waits for a DEFINITIVE verdict — `stalled`
    (silent after activity) or the hard `timeout` backstop. Those fire well
    after the frontend's 120 s bounded-wait abort, so in practice the UI learns
    the verdict via the recovery poll reading GET /session, not a direct 408.
  • The catch routes abort / network drop / a direct (now-definitive) 408 into
    the SAME shared `startSessionHealPoll`: keep the "Working…" bubble, poll
    until the turn resolves. It restores the draft ONLY when the resolved turn
    is a definitive backend error (married to the error bubble), never on a
    successful reply, and never on a frontend-timer exhaustion.
  • 502/503 (gateway down, agent never invoked) stay an immediate married
    failure — that path is unchanged and covered by the gateway-heal contract.

These tests pin the new shape by regexing the single-file React frontend.
"""

import re


def load_index_html():
    with open("ui/index.html", "r") as f:
        return f.read()


def test_408_routed_into_deferred_heal_poll_branch():
    """408 must be handled by the deferred recovery branch (alongside abort /
    network drop), NOT the immediate gateway-failure path — the agent may still
    be working, so we keep 'Working…' and wait for the backend's verdict."""
    html = load_index_html()
    assert re.search(
        r"isAbort\s*\|\|\s*!\s*Number\.isFinite\(\s*status\s*\)\s*\|\|\s*status\s*===\s*408",
        html,
    ), "Expected 408 folded into the abort/network heal-poll guard (deferred, not immediate)"


def test_immediate_draft_restore_is_gateway_only_not_408():
    """The only SYNCHRONOUS draft restore in the catch is gated on 502/503 (the
    agent was never invoked). 408 must not synchronously restore the draft."""
    html = load_index_html()
    assert re.search(
        r"\(\s*status\s*===\s*502\s*\|\|\s*status\s*===\s*503\s*\)\s*&&\s*overrideText\s*===\s*undefined"
        r"[\s\S]{0,80}?setInputText\s*\(\s*t\s*\)",
        html,
    ), "Expected the synchronous setInputText(t) gated on 502/503 only (no 408)"


def test_married_revert_lives_in_onresolved_gated_on_error():
    """The deferred poll restores the draft only when the resolved turn is a
    DEFINITIVE backend error (the heal poll already rendered it from server
    state) — married to the failure, never on a successful reply."""
    html = load_index_html()
    assert "onResolved" in html, "Expected an onResolved hook on the recovery poll"
    # Window is generous because the chat JSX is deeply indented (~36 spaces/line);
    # the assertion still pins the coupling: onResolved gates setInputText on `.error`.
    assert re.search(
        r"onResolved[\s\S]{0,700}?\.error[\s\S]{0,200}?setInputText\s*\(\s*t\s*\)",
        html,
    ), "Expected onResolved to restore the draft only when the resolved assistant has .error"


def test_onexhausted_couldnt_confirm_does_not_restore_draft():
    """If the UI never observes a verdict (server unreachable) that is NOT a
    definitive timeout — show 'couldn't confirm' but do NOT revert the draft."""
    html = load_index_html()
    idx = html.find("Couldn't confirm the reply landed")
    assert idx != -1, "Expected the 'couldn't confirm' onExhausted copy"
    window = html[max(0, idx - 300): idx + 300]
    assert "setInputText" not in window, (
        "onExhausted (no definitive verdict observed) must NOT restore the draft"
    )


def test_recovery_poll_resolves_on_nonpending_assistant():
    """The deferred poll resolves when the turn becomes non-pending — a real
    reply OR the backend's definitive error persisted to session.json."""
    html = load_index_html()
    assert re.search(
        r"resolveWhen:\s*\(\s*msgs\s*\)\s*=>\s*\{[\s\S]{0,200}?!\s*\w+\.pending",
        html,
    ), "Expected a resolveWhen keyed on !assistant.pending"


def test_no_server_message_parsing_single_source_from_session_json():
    """The reason-specific wording is authored by the backend and rendered from
    session.json by the heal poll — the frontend must not re-parse it from the
    response body. The redundant `serverMessage` plumbing must be gone."""
    html = load_index_html()
    assert "serverMessage" not in html, (
        "serverMessage plumbing should be removed — the heal poll renders the "
        "backend's message from session.json (single source of the wording)"
    )
