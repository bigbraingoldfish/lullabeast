"""P0 Stage J — Ideas-screen tab indicator pulses for Roadmap + Verification
during ``/api/ideas/{id}/convert``.

Plan §2.8 line 149 of ``plans/Active/p0-behavioral-verification-runtime-profile.md``
specifies that when the converter regenerates the roadmap and verification
documents, the tabs themselves should pulse so the user sees both
documents refreshing in lockstep. Operator-confirmed during plan review:
both tabs pulse together because the same /convert call writes both.

These are grep-based static assertions on the single-file React app —
mirrors the pattern in ``test_p0_stage_h_ui_phase_dropdown.py`` and
``test_p0_ideas_screen_tab.py``.

Pinned:
- a dedicated ``tabIsRegenerating`` helper exists (testable, single source
  of truth, matches the ``formatExecAttemptsBreakdown`` pattern from Stage H)
- the helper considers both 'roadmap' and 'verification' tabs regenerating
  while the converter is mid-run
- the helper is actually wired into the tab label JSX with the
  ``animate-pulse`` Tailwind utility — the helper existing but not wired
  silently disables the pulse
"""

from pathlib import Path


INDEX_HTML = Path(__file__).resolve().parents[1] / "ui" / "index.html"


def _read_index() -> str:
    assert INDEX_HTML.exists(), f"Expected {INDEX_HTML}"
    return INDEX_HTML.read_text()


def test_tab_is_regenerating_helper_exists():
    """A dedicated helper (function declaration syntax, matching the
    ``formatExecAttemptsBreakdown`` precedent from Stage H) must define
    the regeneration-decision rule. Pins the contract that the
    implementation uses a real function rather than an inline JSX
    ternary — inlining makes the logic hard to test in isolation and
    invites drift if a third regen-affected tab appears later."""
    body = _read_index()
    assert "function tabIsRegenerating" in body, (
        "ui/index.html must declare a tabIsRegenerating helper. Mirrors "
        "the formatExecAttemptsBreakdown extraction pattern from Stage H "
        "(line 908) — keeps the pulse logic a single source of truth and "
        "makes the static grep tests in this file possible."
    )


def test_helper_covers_both_roadmap_and_verification_tabs():
    """The helper body must reference both 'roadmap' and 'verification'
    tab IDs. The /convert call writes both setRoadmapContent and
    setVerificationContent in one effect (ui/index.html:4132-4133), so
    they regenerate in lockstep — pulsing only one creates an
    asymmetry the underlying data does not support."""
    body = _read_index()
    assert "'roadmap'" in body and "'verification'" in body, (
        "ui/index.html must reference both tab IDs in the source. Even "
        "outside the helper, these literals are required for the tab "
        "array; this test pins their presence so a refactor that "
        "consolidates one tab away surfaces here first."
    )
    # Stronger pin: the helper body itself must consider both tabs. We
    # locate the helper and check its body — be lenient on whitespace
    # and ordering so formatter passes do not break the assertion.
    helper_idx = body.find("function tabIsRegenerating")
    assert helper_idx != -1, (
        "tabIsRegenerating helper must exist before this assertion can "
        "check its body. Earlier test "
        "test_tab_is_regenerating_helper_exists pins the existence."
    )
    # Helper body is small — a ~400-char window is generous and resilient.
    helper_window = body[helper_idx:helper_idx + 400]
    assert "'roadmap'" in helper_window, (
        "tabIsRegenerating helper must return true for the 'roadmap' tab. "
        "Even though /convert is named for the roadmap, the helper "
        "decision must mention it explicitly — otherwise only verification "
        "pulses and the operator sees an asymmetric refresh signal."
    )
    assert "'verification'" in helper_window, (
        "tabIsRegenerating helper must return true for the 'verification' "
        "tab. Stage J's done-criteria explicitly requires the verification "
        "tab pulse during conversion — this is the line that ensures it."
    )


def test_animate_pulse_wired_into_tab_label_via_helper():
    """The helper must be actually consumed by the tab .map() block.
    Otherwise the helper exists but does nothing — the static-lint
    equivalent of dead code. Pins the helper-call site against the
    animate-pulse Tailwind utility so the visual treatment matches the
    existing section-level drafting banner at line 5028."""
    body = _read_index()
    # The helper is consumed at the tab .map() — confirm both the call
    # site and animate-pulse appear in close proximity inside a single
    # JSX expression.
    assert "tabIsRegenerating(" in body, (
        "ui/index.html must invoke tabIsRegenerating(...) inside the "
        "Ideas-screen tab .map() block. The helper declaration alone "
        "does nothing — the JSX has to call it."
    )
    # Locate at least one tabIsRegenerating call paired with animate-pulse
    # within a small window — the conditional className that drives the
    # pulse animation.
    body_len = len(body)
    call_positions = []
    pos = 0
    while True:
        i = body.find("tabIsRegenerating(", pos)
        if i == -1:
            break
        call_positions.append(i)
        pos = i + 1
    # Skip the declaration site (function tabIsRegenerating) — any call
    # site must pair with animate-pulse within ~200 chars.
    paired = False
    for call_idx in call_positions:
        window = body[max(0, call_idx - 50):min(body_len, call_idx + 250)]
        if "animate-pulse" in window:
            paired = True
            break
    assert paired, (
        "tabIsRegenerating must be paired with the animate-pulse Tailwind "
        "utility in the JSX — the helper's return value gates the pulse. "
        "Without this pairing, the helper exists but does not influence "
        "the rendered output and the operator never sees the pulse."
    )
