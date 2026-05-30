"""Static contract tests: Ideas chat bounded fetch + poll-during-wait recovery.

Regression context: raising the server `poll_timeout` to 900 s turned the chat
send into a single HTTP connection the browser holds for up to 15 minutes. The
recovery poll that surfaces a reply without a refresh only started after a
408/502/503 — which now doesn't fire until 900 s — so a long reply that
dropped/hung mid-wait needed a manual refresh.

Fix: bound the client fetch with an AbortController; on abort (or a network
drop) the server is still working, so keep the "Working on your request…"
bubble and hand off to a recovery poll that watches GET /session until the
turn's assistant message is resolved (a real reply OR a server-written
reason-aware error). One shared poll helper serves both the 408 late-heal and
the abort/network recovery (no second near-identical poller).

Pattern mirrors the other ui_ideas contract suites: read index.html, regex the
required code shapes.
"""
import re


def load_index_html():
    with open("ui/index.html", "r") as f:
        return f.read()


def test_chat_fetch_is_bounded_by_abort_controller():
    """The chat POST must use an AbortController with a bounded client wait so
    it never holds one connection for the server's full 900 s patience."""
    html = load_index_html()
    assert "CLIENT_WAIT_MS" in html, "Expected a named bounded-wait constant CLIENT_WAIT_MS"
    assert "new AbortController()" in html, "Expected an AbortController on the chat send"
    # The message fetch must pass the controller's signal and arm an abort timer.
    assert re.search(
        r"/api/ideas/\$\{currentIdeaId\}/message[\s\S]{0,300}?signal:",
        html,
    ), "Expected the /message fetch to pass `signal:` from the AbortController"
    assert re.search(r"setTimeout\([^)]*\.abort\(\)", html) or re.search(
        r"\.abort\(\)[\s\S]{0,40}?CLIENT_WAIT_MS", html
    ) or re.search(r"CLIENT_WAIT_MS[\s\S]{0,80}?\.abort\(\)", html), (
        "Expected an abort timer driven by CLIENT_WAIT_MS"
    )


def test_abort_or_network_hands_off_to_recovery_poll_without_error():
    """On AbortError / network drop the handler must NOT show an error — the
    server is still working — and must start the recovery poll, keeping the
    pending bubble alive."""
    html = load_index_html()
    assert 'name === "AbortError"' in html or "name === 'AbortError'" in html, (
        "Expected the catch to detect AbortError (client bounded-wait elapsed)"
    )
    # A shared session-poll helper must exist and be invoked for recovery.
    assert "startSessionHealPoll" in html, (
        "Expected a shared startSessionHealPoll helper used for recovery"
    )
    # The recovery branch must early-return before the error-message path so no
    # error bubble / draft-restore fires while the server is still working.
    # (Window widened: the recovery poll now also carries an `onResolved` hook
    # — it restores the draft only on a DEFINITIVE backend error — so the
    # startSessionHealPoll({...}) block before `return;` is legitimately larger.)
    assert re.search(
        r'AbortError[\s\S]{0,1200}?startSessionHealPoll[\s\S]{0,2600}?return;',
        html,
    ), "Expected the abort/network branch to start the recovery poll and return early"


def test_recovery_poll_resolves_on_nonpending_assistant():
    """The recovery resolve predicate must key on the turn's assistant message
    becoming NON-pending (real reply or server-written error) — not merely
    'no error' (which is true while still pending and would resolve early)."""
    html = load_index_html()
    assert re.search(
        r"startSessionHealPoll\(\s*\{[\s\S]{0,400}?resolveWhen[\s\S]{0,200}?pending",
        html,
    ), "Expected a recovery resolveWhen predicate that checks assistant `pending`"


def test_recovery_poll_covers_the_full_server_backstop():
    """Recovery must poll long enough to cover the 900 s server backstop (so a
    very long but healthy reply still lands without a refresh)."""
    html = load_index_html()
    # maxTicks at 5 s/tick must reach >= ~900 s → >= 180 ticks for the recovery caller.
    m = re.findall(r"maxTicks:\s*(\d+)", html)
    assert m, "Expected an explicit maxTicks on the heal poll calls"
    assert any(int(x) >= 180 for x in m), (
        f"Expected a recovery maxTicks >= 180 (>= ~900 s at 5 s/tick); saw {m}"
    )


def test_single_shared_heal_poll_loop_no_inline_duplicate():
    """The 408 late-heal must route through the same shared helper — no second
    inline setInterval poll loop hitting /session (dual-source guard)."""
    html = load_index_html()
    # The previous inline late-heal used a bare `setInterval(() => { ... fetch(
    # `/api/ideas/${healIdeaId}/session`) ... }, 5000)`. After the refactor the
    # session-poll loop lives in exactly one place.
    session_poll_intervals = re.findall(
        r"setInterval\([\s\S]{0,400}?/api/ideas/\$\{[^}]+\}/session", html
    )
    assert len(session_poll_intervals) <= 1, (
        f"Expected a single shared session-poll loop, found {len(session_poll_intervals)} "
        "— the 408 late-heal and the recovery poll must share one helper"
    )
