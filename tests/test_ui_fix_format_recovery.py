"""Static contract tests: format-correction late-reply recovery (Phase B2).

``doFixRoadmapFormat`` previously dropped the corrected roadmap on a backend
timeout — it only showed "Fix Format failed", stranding the corrected roadmap
that the agent finished writing a moment later. It now mirrors convert: on a
timeout verdict (HTTP 504 today; 408 kept for old in-flight responses —
browsers transparently re-POST on 408, which is why the backend moved off it)
it starts ``startRoadmapRecoverPoll`` to pick up the corrected roadmap that
lands late, and continues setup with the FRESH content rather than the
not-yet-flushed ``roadmapContent`` state.

The shared ``startRoadmapRecoverPoll`` gained an optional
``onRecovered(freshRoadmap)`` callback; ``continueSetup`` /
``_continueSetupAfterStalenessOk`` gained an optional ``contentOverride`` so the
``/api/setup/validate-roadmap`` POST validates the fresh corrected roadmap (this
also fixes the same-tick stale-state read on the Fix-Format success path).

Pattern mirrors the other ``ui_ideas`` contract suites: read index.html, regex
the required code shapes.
"""
import re


def load_index_html():
    with open("ui/index.html", "r") as f:
        return f.read()


def test_fix_format_preserves_http_status_into_catch():
    """doFixRoadmapFormat must carry the HTTP status into the catch so it can
    distinguish a 504/408 (slow-but-running → recover) from a hard failure."""
    html = load_index_html()
    assert re.search(
        r"const e\s*=\s*new Error\(t\);\s*e\.status\s*=\s*r\.status;\s*throw e;",
        html,
    ), "Expected doFixRoadmapFormat to attach r.status to the thrown error"


def test_fix_format_starts_recover_poll_on_timeout_status():
    """On a 504 (or a legacy in-flight 408), doFixRoadmapFormat starts
    startRoadmapRecoverPoll (not a hard 'Fix Format failed') and wires
    continueSetup as the onRecovered callback."""
    html = load_index_html()
    assert re.search(
        r"e\.status\s*===\s*504\s*\|\|\s*e\.status\s*===\s*408[\s\S]{0,500}?"
        r"startRoadmapRecoverPoll\(\s*ideaId\s*,\s*\(rm\)\s*=>\s*continueSetup\(rm\)\s*\)",
        html,
    ), "Expected a 504 (with 408 back-compat) to start the roadmap recovery poll with continueSetup as onRecovered"


def test_start_roadmap_recover_poll_accepts_and_invokes_onRecovered():
    """startRoadmapRecoverPoll gains an optional onRecovered(freshRoadmap) invoked
    after it picks up the recovered roadmap (so format-correction can continue)."""
    html = load_index_html()
    assert re.search(
        r"const startRoadmapRecoverPoll\s*=\s*\(\s*ideaId\s*,\s*onRecovered\s*\)\s*=>",
        html,
    ), "Expected startRoadmapRecoverPoll to accept an onRecovered parameter"
    assert re.search(
        r"stopRoadmapRecoverPoll\(\);[\s\S]{0,300}?"  # tolerate an explanatory comment
        r"if\s*\(\s*onRecovered\s*\)\s*onRecovered\(\s*d\.roadmap_content",
        html,
    ), "Expected the recover resolve to invoke onRecovered with the fresh roadmap content"


def test_continue_setup_accepts_content_override():
    """continueSetup and _continueSetupAfterStalenessOk accept an optional
    contentOverride so the recovery (and the Fix-Format success path) validate the
    FRESH corrected roadmap, not the not-yet-flushed roadmapContent state."""
    html = load_index_html()
    assert re.search(
        r"const continueSetup\s*=\s*\(\s*contentOverride\s*\)\s*=>", html
    ), "Expected continueSetup to accept contentOverride"
    assert re.search(
        r"const _continueSetupAfterStalenessOk\s*=\s*\(\s*contentOverride\s*\)\s*=>", html
    ), "Expected _continueSetupAfterStalenessOk to accept contentOverride"
    # Both functions must honor contentOverride ONLY when it is a string, so a
    # React SyntheticEvent passed by ``onClick={continueSetup}`` is ignored.
    # (Regression: a ``!= null`` check treated the event object as a roadmap
    # override and threw in ``rm.includes(...)``, silently breaking the button.)
    assert len(re.findall(
        r'typeof\s+contentOverride\s*===\s*"string"\s*\?\s*contentOverride\s*:\s*roadmapContent',
        html,
    )) >= 2, "Both continue fns must guard contentOverride with typeof === 'string'"
    assert "contentOverride != null ? contentOverride" not in html, (
        "the `!= null` override check treats an onClick SyntheticEvent as a roadmap "
        "override — must use a typeof-string guard instead"
    )


def test_continue_to_setup_button_is_event_safe():
    """Regression: the 'Continue to Setup' button is wired ``onClick={continueSetup}``,
    so React hands continueSetup a SyntheticEvent as its first argument. Adding the
    ``contentOverride`` param meant that event was (with a ``!= null`` check) treated
    as a roadmap override → ``rm.includes(...)`` threw → the button silently died.
    continueSetup must therefore guard the first arg with ``typeof === "string"`` so a
    non-string (event / undefined) falls back to the roadmapContent state."""
    html = load_index_html()
    # The button passes the event directly (not `() => continueSetup()`).
    assert re.search(r"onClick=\{continueSetup\}", html), \
        "Continue-to-Setup button passes the event directly to continueSetup"
    # So continueSetup must guard its first arg with a typeof-string check.
    assert re.search(
        r"const continueSetup\s*=\s*\(\s*contentOverride\s*\)\s*=>\s*\{[\s\S]{0,500}?"  # tolerate the explanatory comment
        r'typeof\s+contentOverride\s*===\s*"string"',
        html,
    ), "continueSetup must guard contentOverride with typeof === 'string' (event-safe)"


def test_convert_triggers_recover_poll_on_timeout_status():
    """_runConvert must trigger startRoadmapRecoverPoll on an HTTP 504 status
    (408 kept for old in-flight responses), not only on a brittle
    message-string match. Both convert and fix-format attach r.status to the
    thrown error for this."""
    html = load_index_html()
    assert html.count(
        "const e = new Error(t); e.status = r.status; throw e;"
    ) >= 2, "both convert and fix-format should attach r.status to the thrown error"
    assert re.search(
        r"if\s*\(\s*e\.status\s*===\s*504\s*\|\|\s*e\.status\s*===\s*408\s*\|\|[\s\S]{0,400}?"
        r"startRoadmapRecoverPoll\(ideaId\)",
        html,
    ), "Expected _runConvert to start recovery on a 504/408 status (with string fallback)"


def test_fix_format_success_path_passes_fresh_content():
    """The Fix-Format happy path passes the freshly corrected content to
    continueSetup (fixes the latent same-tick stale-state read)."""
    html = load_index_html()
    assert re.search(
        r"setRoadmapContent\(d\.roadmap_content\);[\s\S]{0,300}?"  # tolerate an explanatory comment
        r"continueSetup\(d\.roadmap_content\)",
        html,
    ), "Expected the Fix-Format success path to continueSetup with the fresh content"
