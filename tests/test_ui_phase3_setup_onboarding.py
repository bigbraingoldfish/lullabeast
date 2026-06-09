"""Phase 3 — Setup & onboarding flow: static-source contract tests.

UI REVIEW roadmap Phase 3 fixes five project-start bugs (N1, N2, N4, 4-A/4-B, 4-C).
These are static contracts on ``ui/index.html`` (single-file CDN-React, no JS build or
test runner — same approach as the other ``tests/test_ui_*.py``). They guard the new
behaviour against regressions.

* N1 — a thin / abort-stub roadmap dead-ended with a misleading "Roadmap Format Invalid"
  + Fix-Format modal. Now a *soft* "your PRD needs more detail" modal routes the
  ``no_phase_lines`` and "Transformation Aborted" cases, with a **Continue anyway** bypass
  that proceeds straight to Setup (so a deliberately small enhancement roadmap is not trapped).
* N2 — the shared path input no longer browser-autofills (``autoComplete="off"``) and the
  recents ``<datalist>`` is gone; ``AddProjectModal`` accepts an ``initialPath`` prefill.
* N4 — confirming a repo with an on-disk roadmap but no linked idea pops a redirect modal to
  Queue → Add Project, prefilled (real ``repo-roadmap-hint`` fetch, not a comment).
* 4-A — recents are a visible click-to-fill list (real projects only; ``/tmp`` filtered),
  not a hidden autocomplete.
* 4-C — the "currently running" banner follows ``state === 'ACTIVE'`` (the launch predicate),
  not ``live_pipeline_status`` busy.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "ui" / "index.html"


def load_html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def extract_function(html, func_name):
    """Return the body of a named top-level JS function (same helper as the sibling UI tests)."""
    match = re.search(
        rf"\n([ \t]*)function {re.escape(func_name)}\s*\([^)]*\)\s*\{{",
        html,
    )
    if not match:
        return None
    indent = match.group(1)
    body_start = match.end()
    remainder = html[body_start:]
    next_fn = re.search(rf"\n\n{re.escape(indent)}function \w", remainder)
    if next_fn:
        candidate = remainder[: next_fn.start()]
    else:
        script_end = re.search(r"\n\s*</script>", remainder)
        candidate = remainder[: script_end.start()] if script_end else remainder
    last_close = candidate.rfind(f"\n{indent}}}")
    return candidate[:last_close] if last_close != -1 else candidate


def window(html, anchor, size=1800):
    """Return ``size`` chars of source starting at ``anchor`` (asserts the anchor exists)."""
    i = html.find(anchor)
    assert i != -1, f"anchor {anchor!r} not found in index.html"
    return html[i : i + size]


# ── N1 — honest "needs more detail" recovery modal, with bypass ───────────────

class TestN1NeedsDetailModal:
    def test_needs_detail_modal_state_and_testid_exist(self):
        """A dedicated modal (not the reused 'Roadmap Format Invalid' one) carries the honest
        copy. Regresses to the misleading format error if this modal is removed."""
        html = load_html()
        assert "showRoadmapNeedsDetailModal" in html, "needs-detail modal state missing"
        assert 'data-testid="ideas-roadmap-needs-detail"' in html, "needs-detail modal testid missing"

    def test_no_phase_lines_routes_to_needs_detail(self):
        """A zero-phase roadmap (backend ``code: 'no_phase_lines'``) must open the needs-detail
        modal, not the format-invalid/Fix-Format modal."""
        html = load_html()
        assert "no_phase_lines" in html, "frontend must branch on the no_phase_lines code"
        assert "setShowRoadmapNeedsDetailModal(true)" in html, "no_phase_lines must open the needs-detail modal"

    def test_transformation_aborted_no_longer_uses_format_invalid_copy(self):
        """The old hard-block copy is removed — the abort-stub case now routes to needs-detail.
        Catches a regression that re-introduces the misleading 'cannot be used for setup' dead-end."""
        html = load_html()
        assert "Transformation Aborted" in html, "the abort-stub guard itself must remain"
        assert "cannot be used for setup. Generate a valid roadmap first." not in html, (
            "the old format-invalid abort copy must be removed (routes to needs-detail now)"
        )

    def test_needs_detail_modal_has_continue_anyway_bypass(self):
        """The modal is a soft warning: a 'Continue anyway' CTA bypasses validation and proceeds
        to Setup via navigateToPreflightWithSeed, so a small/intentional roadmap is never trapped.
        Regresses to a hard block if the bypass helper or CTA is removed."""
        html = load_html()
        modal = window(html, 'data-testid="ideas-roadmap-needs-detail"', 2800)
        assert "Continue anyway" in modal, "needs-detail modal must offer a Continue-anyway bypass"
        assert "Keep editing" in modal and "Regenerate" in modal, "modal must keep the edit/regenerate CTAs"
        assert "_proceedToPreflight" in html, "the bypass must call a _proceedToPreflight helper"
        proceed = window(html, "_proceedToPreflight = ", 600)
        assert "navigateToPreflightWithSeed" in proceed, (
            "_proceedToPreflight must bypass validation and go straight to navigateToPreflightWithSeed"
        )


# ── N2 — kill sticky-path autofill on the shared input ────────────────────────

class TestN2NoStickyPath:
    def test_server_path_input_disables_browser_autofill(self):
        html = load_html()
        body = extract_function(html, "ServerPathInput")
        assert body is not None, "ServerPathInput not found"
        assert 'autoComplete="off"' in body, "input must disable browser autofill (N2 sticky path)"

    def test_server_path_input_has_no_datalist(self):
        """The recents <datalist> is replaced by the visible list; keeping it re-introduces the
        N2 autofill vector and the hidden-autocomplete discoverability bug (4-A)."""
        html = load_html()
        body = extract_function(html, "ServerPathInput")
        assert body is not None
        assert "<datalist" not in body, "datalist must be removed"
        assert "list={listId}" not in body, "input must not reference the removed datalist"

    def test_add_project_modal_accepts_and_prefills_initial_path(self):
        html = load_html()
        body = extract_function(html, "AddProjectModal")
        assert body is not None
        assert "initialPath" in body, "AddProjectModal must accept an initialPath prop"
        assert "useState(initialPath" in body, "projectPath state must seed from initialPath"


# ── N4 — redirect modal for on-disk-roadmap repos with no linked idea ─────────

class TestN4RepoNeedsIdeaRedirect:
    def test_confirm_path_does_a_real_repo_roadmap_hint_fetch(self):
        """onRepoPathConfirm must really call repo-roadmap-hint (not leave it in a stale comment).
        The old removal comment is deleted so the contract can't pass against dead text."""
        html = load_html()
        app = extract_function(html, "App")
        assert app is not None
        assert ('fetch("/api/setup/repo-roadmap-hint"' in app) or (
            "fetch('/api/setup/repo-roadmap-hint'" in app
        ), "App must perform a real repo-roadmap-hint fetch"
        assert "the on-disk auto-load (formerly" not in html, "the stale removal comment must be deleted"

    def test_repo_needs_idea_modal_exists(self):
        html = load_html()
        assert "showRepoNeedsIdeaModal" in html, "N4 redirect modal state missing"
        assert 'data-testid="setup-repo-needs-idea"' in html, "N4 redirect modal testid missing"

    def test_redirect_cta_navigates_to_queue_with_prefill(self):
        """The redirect CTA stashes the staged path and jumps to the Queue screen so Add-Project
        opens prefilled."""
        html = load_html()
        modal = window(html, 'data-testid="setup-repo-needs-idea"', 2400)
        assert "setPendingQueueAddPath(" in modal, "redirect CTA must stash the path for prefill"
        assert ("setCurrentScreen('queue')" in modal) or ('setCurrentScreen("queue")' in modal), (
            "redirect CTA must navigate to the Queue screen"
        )

    def test_pending_queue_add_path_exposed_on_context(self):
        html = load_html()
        ctx = window(html, "const appCtxValue = {", 500)
        assert "pendingQueueAddPath" in ctx, "pendingQueueAddPath must be on the app context"
        assert "clearPendingQueueAddPath" in ctx, "clearPendingQueueAddPath must be on the app context"

    def test_queue_screen_consumes_pending_path_to_open_modal(self):
        html = load_html()
        body = extract_function(html, "QueueScreen")
        assert body is not None
        assert "pendingQueueAddPath" in body, "QueueScreen must read the pending add path"
        assert "setShowAddModal(true)" in body, "QueueScreen must open the Add-Project modal on a pending path"


# ── 4-A — visible click-to-fill recents list, /tmp filtered ───────────────────

class TestRecentsClickToFillList:
    def test_display_helper_excludes_tmp(self):
        html = load_html()
        assert "function recentsToDisplayPaths" in html, "recents display helper missing"
        helper = window(html, "function recentsToDisplayPaths", 500)
        assert "/tmp" in helper, "display helper must filter /tmp paths (defensive backstop)"

    def test_server_path_input_renders_clickable_recents(self):
        """Recents are a visible click-to-fill list (buttons), not a hidden datalist."""
        html = load_html()
        body = extract_function(html, "ServerPathInput")
        assert body is not None
        assert "recentsToDisplayPaths" in body, "ServerPathInput must render filtered recents"
        assert "<button" in body, "recents must be clickable buttons (click-to-fill), not a datalist"


# ── 4-C — banner follows state === 'ACTIVE' (launch predicate) ────────────────

class TestBannerActiveEntryPredicate:
    def test_queue_has_active_entry_helper_exists(self):
        html = load_html()
        assert "function queueHasActiveEntry" in html, "queueHasActiveEntry helper missing"
        helper = window(html, "function queueHasActiveEntry", 200)
        assert "state === 'ACTIVE'" in helper, "helper must key on the queue entry's ACTIVE state"

    def test_banner_fed_by_active_entry_not_busy_live(self):
        html = load_html()
        app = extract_function(html, "App")
        assert app is not None
        assert "queueHasActiveEntry(d.queue)" in app, "useEffect probe must feed banner from ACTIVE state"
        assert "queueHasActiveEntry(qr.queue)" in app, "onLaunch probe must feed banner from ACTIVE state"
        assert "queueEntriesHaveBusyLivePipeline(d.queue)" not in app, "banner must not use the busy-live predicate"
        assert "queueEntriesHaveBusyLivePipeline(qr.queue)" not in app, "banner must not use the busy-live predicate"

    def test_busy_live_helper_retained_for_cold_bootstrap(self):
        """The busy-live helper is NOT dead — it still gates first-run Ideas routing (677).
        Removing it would break shouldOpenIdeasOnColdBootstrap."""
        html = load_html()
        assert "function queueEntriesHaveBusyLivePipeline" in html, "cold-bootstrap helper must remain"
        assert "queueEntriesHaveBusyLivePipeline(queueEntries)" in html, (
            "shouldOpenIdeasOnColdBootstrap must keep using the busy-live helper"
        )
