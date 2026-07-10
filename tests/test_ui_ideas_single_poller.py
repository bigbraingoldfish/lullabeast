"""Static contract tests: one background /session watch loop (Phase 4, T4.1).

The Ideas screen once ran two near-identical background pollers against GET
/session — the turn-recovery heal poll and a bespoke roadmap-recover interval
(tick-counted, no foreground catch-up, its own idea-switch rules). Phase 4
folds roadmap recovery into ``startSessionHealPoll`` parameters so the
budget/guard/catch-up lessons live in exactly one loop:

1. The roadmap caller is a thin delegation: payload-aware ``resolveWhen``,
   ``applyTurnState: false`` (it owns its render), an ``immediate`` first
   tick, and a named wall-clock budget.
2. Arming supersedes the previous watch, so the shared ref gains a generation
   guard: a superseded watch's in-flight fetch microtask (which clearInterval
   cannot cancel) must neither repaint state nor stop the successor's loop.

Pattern mirrors the other ui_ideas contract suites: read index.html, regex the
required code shapes.
"""
import re


def load_index_html():
    with open("ui/index.html", "r") as f:
        return f.read()


# ── 1. one loop, three callers ────────────────────────────────────────────────

def test_bespoke_roadmap_poller_machinery_is_gone():
    """No second near-identical poller: the roadmap-recover interval, its ref,
    and its stop are consolidated away entirely."""
    html = load_index_html()
    for sym in ("roadmapRecoverPollRef", "stopRoadmapRecoverPoll", "pollOnce"):
        assert sym not in html, f"Expected consolidated-away symbol {sym!r} to be gone"


def test_roadmap_recover_is_a_thin_delegation_with_own_render():
    """startRoadmapRecoverPoll must delegate to startSessionHealPoll — payload
    resolveWhen on roadmap content, turn render off, immediate first tick,
    named budget — and run no fetch/interval of its own."""
    html = load_index_html()
    start = html.index("const startRoadmapRecoverPoll")
    body = html[start: html.index("};", start) + 2]
    assert "startSessionHealPoll({" in body
    for banned in ("fetch(", "setInterval(", "clearInterval("):
        assert banned not in body, f"delegation must not carry its own loop: {banned!r}"
    assert re.search(r"resolveWhen:\s*\(\s*msgs\s*,\s*d\s*\)\s*=>", body), (
        "roadmap resolveWhen must read the payload (second arg), not the messages"
    )
    assert "applyTurnState: false" in body, (
        "roadmap recovery must not run the turn-state render (messages/readiness)"
    )
    assert "immediate: true" in body, (
        "roadmap recovery keeps its immediate first reconcile"
    )
    assert re.search(r"budgetMs:\s*ROADMAP_RECOVER_BUDGET_MS", body), (
        "roadmap recovery keeps a named wall-clock budget"
    )


def test_roadmap_recover_budget_is_wall_clock_and_bounded():
    """The old 36×5s tick budget becomes a named wall-clock deadline: minutes,
    not hours under background-tab throttling, and no unbounded degrade (a
    later salvage still renders on the next idea load)."""
    html = load_index_html()
    m = re.search(r"ROADMAP_RECOVER_BUDGET_MS\s*=\s*(\d+)", html)
    assert m, "Expected a named ROADMAP_RECOVER_BUDGET_MS constant"
    assert 60000 <= int(m.group(1)) <= 600000, "roadmap recovery budget should stay in the minutes range"
    start = html.index("const startRoadmapRecoverPoll")
    body = html[start: html.index("};", start) + 2]
    assert "degradeToIntervalMs" not in body, (
        "roadmap recovery stays bounded — no unbounded slow degrade"
    )


def test_shared_loop_supports_immediate_first_tick():
    """`immediate: true` fires one reconcile right after arming (the roadmap
    caller's old pollOnce()), instead of waiting out the first interval."""
    html = load_index_html()
    assert re.search(
        r"lateHealPollRef\.current\s*=\s*setInterval\(tick,\s*intervalMs\);\s*"
        r"if\s*\(\s*immediate\s*\)\s*tick\(\);",
        html,
    ), "Expected the shared loop to run an immediate first tick when asked"


def test_turn_state_render_is_gated_not_duplicated():
    """The default turn render (messages/PRD/readiness) must be one gated block
    inside the shared resolve — callers opt out via applyTurnState, they never
    get a second render path."""
    html = load_index_html()
    assert re.search(
        r"if\s*\(\s*applyTurnState\s*\)\s*\{[\s\S]{0,200}?setMessages\(",
        html,
    ), "Expected the turn-state render gated behind applyTurnState"
    assert html.count("applyTurnState = true") == 1, (
        "applyTurnState defaults on — recovery-watch turn callers stay unchanged"
    )


# ── 2. generation guard: superseded microtasks are inert ─────────────────────

def test_watch_generation_ref_exists_and_arms_bump_it():
    html = load_index_html()
    assert re.search(r"const\s+lateHealPollGenRef\s*=\s*useRef\(0\)", html), (
        "Expected a watch-generation ref"
    )
    assert re.search(r"const\s+gen\s*=\s*\+\+lateHealPollGenRef\.current", html), (
        "Expected each arm to claim a fresh generation"
    )


def test_stop_invalidates_inflight_microtasks():
    """stopLateHealPoll must bump the generation so microtasks of the stopped
    watch (e.g. a fetch resolving just after submitMessage stopped a leftover
    watch) can no longer repaint state."""
    html = load_index_html()
    assert re.search(
        r"const stopLateHealPoll\s*=\s*\(\)\s*=>\s*\{\s*"
        r"lateHealPollGenRef\.current\s*\+=\s*1;",
        html,
    ), "Expected stopLateHealPoll to invalidate the stopped watch's generation first"


def test_every_loop_exit_checks_generation_before_idea_guard():
    """Tick, resolve, and reject must each drop a superseded generation with a
    PLAIN return before the idea-switch stop — a stale microtask stopping the
    shared ref would kill the successor watch (the very hazard the plain-return
    rule for re-armable refs was written for)."""
    html = load_index_html()
    guards = re.findall(
        r"if\s*\(\s*gen\s*!==\s*lateHealPollGenRef\.current\s*\)\s*return;",
        html,
    )
    assert len(guards) >= 3, (
        f"Expected >= 3 generation guards (tick + resolve + catch); found {len(guards)}"
    )
    # Order: generation guard dominates the idea-switch stop in the resolve.
    assert re.search(
        r"if\s*\(\s*gen\s*!==\s*lateHealPollGenRef\.current\s*\)\s*return;\s*"
        r"if\s*\(\s*ideaId\s*!==\s*currentIdeaIdRef\.current\s*\)\s*\{\s*stopLateHealPoll\(\)",
        html,
    ), "Expected the generation check to run before the idea-switch stop"
