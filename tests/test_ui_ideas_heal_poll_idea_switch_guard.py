"""Static contract tests: heal-poll must not flash a stale idea's state.

Regression context: `startSessionHealPoll` runs a setInterval that fetches
GET /api/ideas/${ideaId}/session, where `ideaId` is captured at call time.
If the user switches to a different idea while a poll is in flight (up to
~16 min on the abort/timeout recovery branch), `clearInterval` (via the
[currentIdeaId] cleanup effect) cannot cancel an ALREADY in-flight fetch's
.then/.catch microtask. The stale resolve then runs setMessages / setPrdContent
/ startReadinessPoll / onResolved for the OLD idea, flashing the wrong idea's
messages and PRD into the currently-viewed idea.

Fix: mirror currentIdeaId into a ref (currentIdeaIdRef) and bail out of the
interval tick, the .then resolve, and the .catch reject whenever the poll's
captured ideaId no longer matches the on-screen idea — stopping the loop and
returning before any state write.

Pattern mirrors the other ui_ideas contract suites: read index.html, regex the
required code shapes.
"""
import re


def load_index_html():
    with open("ui/index.html", "r") as f:
        return f.read()


def test_current_idea_id_ref_is_declared_via_useref():
    """A ref mirror of the on-screen idea must exist so in-flight async work
    (the heal poll) can detect that the user has switched ideas."""
    html = load_index_html()
    assert re.search(r"const\s+currentIdeaIdRef\s*=\s*useRef\(", html), \
        "Expected `const currentIdeaIdRef = useRef(...)` to track the on-screen idea"


def test_current_idea_id_ref_is_synced_from_state_via_effect():
    """currentIdeaIdRef must be kept in sync with currentIdeaId via a useEffect
    keyed on currentIdeaId (the established ref-mirrors-state pattern), so the
    ref is always the authority an async callback can read. Without this the
    guard would forever compare against a stale/null ref."""
    html = load_index_html()
    assert re.search(
        r"currentIdeaIdRef\.current\s*=\s*currentIdeaId\b[\s\S]{0,120}?\[\s*currentIdeaId\s*\]",
        html,
    ), "Expected an effect assigning currentIdeaIdRef.current = currentIdeaId with [currentIdeaId] dep"


def test_heal_poll_resolve_bails_on_idea_switch_before_setMessages():
    """PRIMARY FIX: inside the heal poll's GET /session resolve handler, the
    captured ideaId must be compared against currentIdeaIdRef.current; on a
    mismatch the loop must stop and the handler must return BEFORE any
    setMessages — otherwise the stale idea's messages flash into the current
    idea. The guard must dominate the existing `if (!resolveWhen(...)) return;`."""
    html = load_index_html()
    assert re.search(
        r"/api/ideas/\$\{ideaId\}/session[\s\S]{0,200}?"
        r"\.then\(\s*\(d\)\s*=>\s*\{[\s\S]{0,120}?"
        r"ideaId\s*!==\s*currentIdeaIdRef\.current[\s\S]{0,80}?"
        r"stopLateHealPoll\(\)[\s\S]{0,40}?return",
        html,
    ), "Expected the heal-poll resolve to bail (stopLateHealPoll + return) on idea switch"
    # The bail must precede setMessages (window > the ~340 chars currently between them,
    # < the distance to any later setMessages, so it pins ORDER not mere coexistence).
    assert re.search(
        r"ideaId\s*!==\s*currentIdeaIdRef\.current[\s\S]{0,40}?stopLateHealPoll\(\)"
        r"[\s\S]{0,40}?return;[\s\S]{0,600}?setMessages\(",
        html,
    ), "Expected the idea-switch guard + return to precede setMessages in the resolve path"


def test_heal_poll_tick_and_catch_are_also_guarded_on_idea_switch():
    """Defense across all three loop exits: the interval tick (so budget
    exhaustion -> onExhausted can't fire for a stale idea and the loop
    self-terminates) and the .catch reject (so fails>=6 -> onExhausted can't
    write the old turn's error into the new idea). At least three
    idea-identity guards must exist, each tearing the loop down. Each is
    preceded by the generation guard (see
    test_ui_ideas_single_poller.py) — a superseded watch's microtask must
    plain-return, never stop the successor's loop."""
    html = load_index_html()
    guards = re.findall(
        r"ideaId\s*!==\s*currentIdeaIdRef\.current\s*\)\s*\{\s*stopLateHealPoll\(\)\s*;\s*return",
        html,
    )
    assert len(guards) >= 3, \
        f"Expected >= 3 idea-switch guards (tick + resolve + catch); found {len(guards)}"
    assert re.search(
        r"\.catch\(\s*\(\)\s*=>\s*\{\s*"
        r"if\s*\(\s*gen\s*!==\s*lateHealPollGenRef\.current\s*\)\s*return;\s*"
        r"if\s*\(\s*ideaId\s*!==\s*currentIdeaIdRef\.current\s*\)\s*\{\s*stopLateHealPoll\(\)\s*;\s*return"
        r"[\s\S]{0,40}?fails\s*\+=\s*1",
        html,
    ), "Expected the heal-poll .catch to bail (generation, then idea switch) before incrementing fails"
