"""Static contract tests: the Ideas chat client can never get stuck on a turn.

Contract (ideas-chat-robustness Phase 1). The server already resolves every
turn — it writes the reply or a reason-specific error into session.json and
heals late artifacts on every GET /session. The client's job is to always be
watching. Three properties pin that:

1. Watch on mount: when the loaded session's newest assistant row is still
   `pending` (screen remounted / idea reselected / page refreshed mid-turn),
   the session-load effect arms the shared recovery watch, so the verdict
   renders without re-navigation.
2. Wall-clock budgets + foreground catch-up: the watch budget is a Date.now()
   deadline (checked after each fetch, never a tick count — background tabs
   throttle timers), and a visibilitychange/focus listener fires one immediate
   reconcile when the tab returns to the foreground.
3. Degrade, don't dead-end: on budget exhaustion the watch surfaces "couldn't
   confirm" but continues as a slow unbounded poll on the same loop, so a
   verdict landing 30+ minutes late still auto-renders while the idea is open.

Pattern mirrors the other ui_ideas contract suites: read index.html, regex the
required code shapes.
"""
import re


def load_index_html():
    with open("ui/index.html", "r") as f:
        return f.read()


# ── 1. watch on mount ─────────────────────────────────────────────────────────

def test_session_load_effect_arms_recovery_watch_on_pending_turn():
    """The [currentIdeaId] session-load effect must detect a persisted pending
    assistant row and arm the shared recovery watch for it."""
    html = load_index_html()
    assert re.search(
        r"/api/ideas/\$\{currentIdeaId\}/session[\s\S]{0,2500}?"
        r"\.pending[\s\S]{0,300}?armTurnRecoveryPoll\(",
        html,
    ), "Expected the session-load effect to arm armTurnRecoveryPoll when the last assistant is pending"


def test_mount_armed_watch_locks_the_composer_like_an_inflight_send():
    """Finding an unresolved turn on mount must set the loading state (composer
    locked, roadmap actions locked) exactly as if the send were still in
    flight — the watch's resolve/exhaust paths unlock it."""
    html = load_index_html()
    assert re.search(
        r"\.pending\s*\)\s*\{[\s\S]{0,120}?setIsLoading\(true\)"
        r"[\s\S]{0,120}?armTurnRecoveryPoll\(",
        html,
    ), "Expected setIsLoading(true) alongside the mount-armed recovery watch"


# ── 2. wall-clock budget + foreground catch-up ───────────────────────────────

def test_budget_is_checked_after_the_fetch_not_before():
    """The deadline check must run on the fetch's resolve path (after
    resolveWhen fails), so a foreground catch-up tick can still render a
    verdict that lands right at the buzzer instead of exhausting first."""
    html = load_index_html()
    assert re.search(
        r"if\s*\(\s*!\s*resolveWhen\(\s*rawMsgs\s*\)\s*\)\s*\{\s*"
        r"if\s*\(\s*deadline\s*&&\s*Date\.now\(\)\s*>\s*deadline\s*\)",
        html,
    ), "Expected the wall-clock deadline checked after resolveWhen, inside the fetch resolve"


def test_foreground_catchup_listener_fires_immediate_reconcile():
    """A visibilitychange/focus listener must fire the current watch tick
    immediately when the tab becomes visible, instead of waiting out a
    throttled interval."""
    html = load_index_html()
    assert re.search(r"lateHealPollTickRef\s*=\s*useRef\(", html), (
        "Expected the current watch tick exposed via lateHealPollTickRef"
    )
    assert re.search(
        r'addEventListener\(\s*"visibilitychange"[\s\S]{0,200}?'
        r'addEventListener\(\s*"focus"',
        html,
    ), "Expected one visibilitychange + focus listener pair in the Ideas screen"
    assert re.search(
        r"document\.hidden[\s\S]{0,200}?lateHealPollTickRef\.current\(\)",
        html,
    ), "Expected the foreground handler to fire the watch tick when visible"
    # The listener pair must be torn down on unmount.
    assert re.search(
        r'removeEventListener\(\s*"visibilitychange"[\s\S]{0,200}?'
        r'removeEventListener\(\s*"focus"',
        html,
    ), "Expected the foreground listeners removed in the effect cleanup"


# ── 3. degrade on exhaustion ─────────────────────────────────────────────────

def test_exhaustion_degrades_to_slow_unbounded_watch_on_the_same_loop():
    """Budget exhaustion must re-arm the SAME tick at the slow cadence with the
    deadline cleared (unbounded), not stop the loop — one poller, degraded."""
    html = load_index_html()
    m = re.search(r"HEAL_POLL_SLOW_INTERVAL_MS\s*=\s*(\d+)", html)
    assert m, "Expected a named slow-watch interval constant HEAL_POLL_SLOW_INTERVAL_MS"
    assert int(m.group(1)) >= 15000, "Slow watch should be gentle (>= 15 s between reconciles)"
    assert re.search(
        r"setInterval\(\s*tick\s*,\s*degradeToIntervalMs\s*\)[\s\S]{0,120}?deadline\s*=\s*null",
        html,
    ), "Expected the degrade to reuse the same tick unbounded (setInterval(tick, degradeToIntervalMs); deadline = null)"
    assert re.search(r"degradeToIntervalMs:\s*HEAL_POLL_SLOW_INTERVAL_MS", html), (
        "Expected the recovery watch to opt into the slow-watch degrade"
    )


def test_couldnt_confirm_copy_is_honest_about_continuing():
    """The exhaustion copy must still exist (no silent stall) and must not
    claim the client stopped — it keeps checking in the background."""
    html = load_index_html()
    idx = html.find("Couldn't confirm the reply landed")
    assert idx != -1, "Expected the 'couldn't confirm' exhaustion copy"
    window = html[idx: idx + 200]
    assert "still checking" in window, (
        "Exhaustion copy should say the client is still checking (the slow watch runs on)"
    )


def test_stale_send_outcome_cannot_touch_foreground_idea():
    """A send whose idea is no longer on screen must not render its outcome
    into (or kill the watch of) the now-foreground idea: the POST resolve and
    catch handlers bail on an idea mismatch, and startSessionHealPoll refuses
    to arm for a non-foreground idea. isLoading is screen-wide, so the bail
    releases it only when no foreground watch owns it — otherwise a background
    send completing would unlock the composer of an idea whose own turn is
    still pending."""
    html = load_index_html()
    # startSessionHealPoll: foreground-only arming, checked BEFORE it stops the
    # current watch (a stale arm must be a no-op, not a watch-killer).
    assert re.search(
        r"const\s+startSessionHealPoll\s*=[\s\S]{0,600}?"
        r"ideaId\s*!==\s*currentIdeaIdRef\.current\s*\)\s*return;"
        r"[\s\S]{0,80}?stopLateHealPoll\(\)",
        html,
    ), "Expected startSessionHealPoll to no-op for a non-foreground idea before stopping the current watch"
    # POST resolve + catch: bail on idea mismatch with an OWNERSHIP-GATED lock
    # release (no release while a foreground recovery watch is armed), and no
    # other state writes.
    guards = re.findall(
        r"currentIdeaId\s*!==\s*currentIdeaIdRef\.current\s*\)\s*\{"
        r"[\s\S]{0,600}?if\s*\(\s*!\s*lateHealPollRef\.current\s*\)\s*\{"
        r"[\s\S]{0,80}?setIsLoading\(false\)[\s\S]{0,200}?return;",
        html,
    )
    assert len(guards) >= 2, (
        f"Expected resolve AND catch to bail with an ownership-gated lock release; found {len(guards)}"
    )


def test_new_send_stops_any_prior_recovery_watch():
    """submitMessage must stop a leftover watch (e.g. the degraded slow watch)
    before rendering its optimistic bubbles, so a stale /session snapshot
    can't clobber the new turn's pending UI."""
    html = load_index_html()
    assert re.search(
        r"const\s+submitMessage\s*=[\s\S]{0,700}?stopLateHealPoll\(\)"
        r"[\s\S]{0,1800}?setMessages\(",
        html,
    ), "Expected submitMessage to stopLateHealPoll() before its optimistic setMessages"
