"""Static contract tests: apply the currentIdeaIdRef idea-switch guard UNIFORMLY.

Follow-up to test_ui_ideas_heal_poll_idea_switch_guard.py. The heal poll was the
first fix; the same stale-closure bug class lives in every other async resolve
that writes idea-scoped state with an ideaId captured at call time. When the user
switches ideas while one of these is in flight, the resolve repaints the OLD
idea's data into the now-foreground idea.

This suite pins the uniform treatment:

  * REMOVAL — the notification-poll machinery (startNotificationPoll /
    refreshMessages / notificationPollRef / stopNotificationPoll) was DEAD
    (startNotificationPoll had no call sites), so it is removed outright rather
    than guarded — guarding code that never runs is meaningless.

  * GUARDS (plain `return`, NOT the heal poll's stopX()+return) on the live
    readiness poll, the idea-load effect's own /session + /readiness resolves,
    and the one-shot fetchPrdSectionDiff / refreshAnnotations. Plain return —
    never a self-stop — because these interval refs are RE-ARMED by a
    newly-selected idea, so a stale resolve that called stopReadinessPoll()
    would kill the NEW idea's poll. (Roadmap recovery rides the shared watch
    loop since Phase 4; its stale-resolve safety is the loop's generation
    guard — see test_ui_ideas_single_poller.py.)

  * the readiness poll's GLOBAL refreshIdeas() (sidebar X/10 for every idea) runs
    BEFORE its guard, so a background assessment that finishes after a switch still
    updates the list ("see X/10 without switching ideas"); only the foreground
    readiness-strip writes (setReadinessStatus/Data) are gated on the live idea.
    readiness keeps its existing unmount-only [] cleanup — the poll's lifecycle is
    already managed by the idea-load effect, so no [currentIdeaId] change is made.

Pattern mirrors the other ui_ideas contract suites: read index.html, regex the
required code shapes.
"""
import re


def load_index_html():
    with open("ui/index.html", "r") as f:
        return f.read()


def test_dead_notification_poll_island_removed():
    """startNotificationPoll was never called -> refreshMessages never ran ->
    notificationPollRef never held a live interval -> stopNotificationPoll (called
    once in the idea-load effect) was always a no-op. The whole dead island is
    removed; no guard is added to code that cannot execute."""
    html = load_index_html()
    for sym in ("startNotificationPoll", "refreshMessages", "notificationPollRef", "stopNotificationPoll"):
        assert sym not in html, f"Expected dead notification-poll symbol {sym!r} to be fully removed"


def test_readiness_poll_guards_on_idea_switch_with_plain_return():
    """startReadinessPoll's interval tick and its nested /readiness resolve must
    bail on idea switch with a PLAIN return (no stopReadinessPoll — the ref is
    re-armed by the newly-selected idea, so a self-stop would kill the NEW idea's
    poll). In the nested resolve the global refreshIdeas() runs BEFORE the guard
    (sidebar X/10 must update even for a background completion after a switch);
    only the foreground readiness-strip writes (setReadinessStatus/Data) sit after
    the guard."""
    html = load_index_html()
    # Tick guard: first statement of the interval body, plain return.
    assert re.search(
        r"readinessPollRef\.current\s*=\s*setInterval\(\(\)\s*=>\s*\{\s*"
        r"if\s*\(\s*ideaId\s*!==\s*currentIdeaIdRef\.current\s*\)\s*return;",
        html,
    ), "Expected the readiness interval tick to plain-return on idea switch"
    # Nested /readiness resolve: refreshIdeas() (global) runs first, THEN the guard
    # gates the foreground setReadinessStatus/Data writes.
    assert re.search(
        r"\.then\(\s*\(d\)\s*=>\s*\{[\s\S]{0,600}?"  # tolerate the explanatory comment block
        r"refreshIdeas\(\);\s*"
        r"if\s*\(\s*ideaId\s*!==\s*currentIdeaIdRef\.current\s*\)\s*return;\s*"
        r"const nextStatus\s*=\s*d\.status",
        html,
    ), "Expected the readiness nested resolve to run refreshIdeas() then plain-return before setReadinessStatus"


def test_roadmap_recover_poll_rides_the_guarded_shared_loop():
    """startRoadmapRecoverPoll's writes (setRoadmapContent / setVerificationContent)
    must sit behind the shared watch loop's idea-switch + generation guards, so a
    recovered roadmap can't land in the wrong idea's document pane: the wrapper
    delegates to startSessionHealPoll and runs no bespoke fetch/interval of its own."""
    html = load_index_html()
    start = html.index("const startRoadmapRecoverPoll")
    body = html[start: html.index("};", start) + 2]
    assert "startSessionHealPoll({" in body, (
        "Expected startRoadmapRecoverPoll to delegate to the shared startSessionHealPoll"
    )
    for banned in ("fetch(", "setInterval("):
        assert banned not in body, (
            f"startRoadmapRecoverPoll must not run its own loop; found {banned!r}"
        )
    assert re.search(
        r"applyTurnState:\s*false[\s\S]{0,600}?setRoadmapContent\(\s*d\.roadmap_content", body
    ), "Expected the roadmap caller to opt out of the turn render and own its writes in onResolved"


def test_idea_load_effect_resolves_guard_on_current_idea_ref():
    """The idea-load effect's own /session and /readiness fetches capture
    currentIdeaId at run time; a fast A->B->C switch lands stale state. Both
    resolves must bail when the captured currentIdeaId no longer matches the ref."""
    html = load_index_html()
    # /session resolve -> guard -> const rawMsgs (then setMessages).
    assert re.search(
        r"\.then\(\s*\(d\)\s*=>\s*\{\s*"
        r"if\s*\(\s*currentIdeaId\s*!==\s*currentIdeaIdRef\.current\s*\)\s*return;\s*"
        r"const rawMsgs",
        html,
    ), "Expected the load-effect /session resolve to bail on a stale idea before setMessages"
    # /readiness resolve -> guard -> const nextStatus.
    assert re.search(
        r"\.then\(\s*\(d\)\s*=>\s*\{\s*"
        r"if\s*\(\s*currentIdeaId\s*!==\s*currentIdeaIdRef\.current\s*\)\s*return;\s*"
        r"const nextStatus",
        html,
    ), "Expected the load-effect /readiness resolve to bail on a stale idea"


def test_one_shot_fetches_guard_on_idea_switch():
    """fetchPrdSectionDiff and refreshAnnotations write setPrdSectionDiff /
    setAnnotations from async resolves keyed on an ideaId captured at call time;
    both must bail on idea switch so a slow response can't repaint the wrong idea."""
    html = load_index_html()
    assert re.search(
        r"if\s*\(\s*ideaId\s*!==\s*currentIdeaIdRef\.current\s*\)\s*return;\s*setPrdSectionDiff\(",
        html,
    ), "Expected fetchPrdSectionDiff resolve to bail on idea switch before setPrdSectionDiff"
    assert re.search(
        r"if\s*\(\s*ideaId\s*!==\s*currentIdeaIdRef\.current\s*\)\s*return;\s*setAnnotations\(",
        html,
    ), "Expected refreshAnnotations resolve to bail on idea switch before setAnnotations"


def test_new_poller_guards_do_not_self_stop():
    """Correctness pin for the re-armable-ref hazard: the readiness guard must
    NOT pair the idea-switch check with stopReadinessPoll() (which would clear
    the NEW idea's interval). The shared watch loop self-stops, but only via
    its generation-owned exits; the bespoke roadmap poller (and its stop) are
    gone entirely."""
    html = load_index_html()
    assert not re.search(
        r"currentIdeaIdRef\.current\s*\)\s*\{\s*stopReadinessPoll\(\)", html
    ), "Readiness idea-switch guard must be a plain return, not stopReadinessPoll()+return"
    for sym in ("stopRoadmapRecoverPoll", "roadmapRecoverPollRef"):
        assert sym not in html, f"Expected consolidated-away symbol {sym!r} to be fully removed"
