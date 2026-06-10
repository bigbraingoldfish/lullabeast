"""Queue redesign — filter chips + flat table + inline row expansion.

The Project Queue screen drops the 3-column master/detail/actions grid for a
glance-first layout: toggleable status-bucket chips (running / attention /
queued / complete, counts included, persisted to localStorage), a full-width
table (PROJECT | STATUS | PHASE | PROGRESS | ELAPSED | COST | QUEUED), and a
per-row inline expansion that reuses QueueProjectSnapshot + QueueActionHub.

Static substring/regex checks on ui/index.html — the house idiom (no JSX
transpiler in CI). Component-block extraction mirrors
tests/test_ui_phase2_live_state_subscription.py.
"""
import re
from pathlib import Path

import pytest

INDEX_HTML = Path(__file__).resolve().parent.parent / "ui" / "index.html"


@pytest.fixture
def html():
    return INDEX_HTML.read_text(encoding="utf-8")


def _block(html, start_pat, end_pat):
    m = re.search(start_pat + r"(.*?)(?=" + end_pat + r")", html, re.DOTALL)
    assert m, f"block {start_pat!r}..{end_pat!r} not found in index.html"
    return m.group(1)


def _queue_block(html):
    return _block(html, r"function QueueScreen\(\)", r"\n\s+function PipelineScreen")


def _rows_region(html):
    """The table-rows render region: from the rows marker to the AddProjectModal gate."""
    start = html.index("{/* Queue table rows */}")
    end = html.index("{showAddModal", start)
    return html[start:end]


# ── Layout swap: grid gone, chips + table in ─────────────────────────────────

def test_three_column_grid_removed(html):
    assert "grid-cols-[30%_35%_35%]" not in html, (
        "the 3-column master/detail/actions grid must be fully removed"
    )


def test_filter_chips_rendered_with_counts(html):
    q = _queue_block(html)
    assert "{/* Filter chips */}" in q
    assert "toggleBucket(" in q
    assert "bucketCounts" in q, "chips must render live per-bucket counts"
    # The four bucket labels (chip copy) live in the module-level chip meta.
    assert "QUEUE_BUCKET_CHIP" in html
    for label in ("running", "attention", "queued", "complete"):
        assert f"'{label}'" in html, f"bucket {label!r} missing from chip meta/order"


def test_table_column_headers_present(html):
    q = _queue_block(html)
    assert "{/* Queue table header */}" in q
    for col in ("PROJECT", "STATUS", "PHASE", "PROGRESS", "ELAPSED", "COST", "QUEUED"):
        assert f">{col}</span>" in q, f"table header column {col} missing"


# ── Bucket mapping helper ─────────────────────────────────────────────────────

def test_queue_entry_bucket_helper_mapping(html):
    assert "function queueEntryBucket" in html
    i = html.index("function queueEntryBucket")
    body = html[i:i + 900]
    assert "'COMPLETED'" in body and "'complete'" in body
    for state in ("'ESCALATION'", "'ESCALATION_ANSWERED'", "'BLOCKED'", "'FAILED'"):
        assert state in body, f"{state} must map to the attention bucket"
    assert "'attention'" in body
    assert "QUEUE_ATTENTION_LIVE_STATUSES" in body, (
        "an ACTIVE row whose live status needs a human belongs in attention"
    )
    assert "'queued'" in body, "READY/DEPENDENCY_HOLD/SKIPPED_PENDING default to queued"
    # The live statuses that pull an ACTIVE row into attention:
    j = html.index("QUEUE_ATTENTION_LIVE_STATUSES")
    live = html[j:j + 220]
    for status in ("WAITING_FOR_HUMAN", "STOPPED", "HALTED_SILENT", "QUEUE_HALTED"):
        assert status in live, f"{status} missing from QUEUE_ATTENTION_LIVE_STATUSES"


# ── Filter persistence + reorder interplay + grouping ─────────────────────────

def test_filter_persistence_uses_localstorage(html):
    assert "const QUEUE_HIDDEN_BUCKETS_KEY = 'autodev_queue_hidden_buckets'" in html
    q = _queue_block(html)
    assert "localStorage.getItem(QUEUE_HIDDEN_BUCKETS_KEY" in q
    assert "localStorage.setItem(QUEUE_HIDDEN_BUCKETS_KEY" in q


def test_reorder_mode_bypasses_filters(html):
    """Drag indices operate on the raw queue array — reorder mode must render
    the unfiltered, ungrouped list."""
    q = _queue_block(html)
    assert "const visibleQueue = reorderMode ? queue" in q


def test_bucket_grouped_sort_outside_reorder(html):
    """Rows group running → attention → queued → complete; completed rows sort
    newest-first; everything else keeps the server order via stable sort."""
    assert "QUEUE_BUCKET_ORDER = ['running', 'attention', 'queued', 'complete']" in html
    q = _queue_block(html)
    assert "bucketRank" in q
    assert "b.completed_at || ''" in q, "complete bucket must sort by completed_at desc"


# ── Row expansion replaces the selection panels ───────────────────────────────

def test_row_click_toggles_expansion(html):
    q = _queue_block(html)
    assert "setExpandedId(prev => prev === entry.id ? null : entry.id)" in q
    assert "selectedId" not in html, "selectedId must be fully renamed to expandedId"
    assert "setSelectedId" not in html


def test_snapshot_effect_keyed_on_expanded_id(html):
    q = _queue_block(html)
    assert "}, [expandedId, snapshotVersion]);" in q
    assert "/api/queue/${expandedId}/snapshot" in q


def test_expansion_renders_snapshot_and_action_hub(html):
    q = _queue_block(html)
    assert "function QueueRowExpansion" in q
    i = q.index("function QueueRowExpansion")
    body = q[i:i + 2400]
    assert "QueueProjectSnapshot()" in body
    assert "QueueActionHub()" in body
    rows = _rows_region(html)
    assert "expandedId === entry.id" in rows
    assert "QueueRowExpansion()" in rows


def test_expansion_subtree_called_inline_not_remounted(html):
    """THE modal-squash guard. QueueRowExpansion / QueueProjectSnapshot /
    QueueActionHub are nested inside QueueScreen, so rendering them as JSX
    elements (<QueueActionHub/>) creates a NEW component type every render —
    React then unmounts + remounts the whole expansion subtree on every 5s
    poll, wiping EscalationCommandPanel's open confirm modal (and any other
    in-subtree state: advisory disclosure, ticking timers, open selects).
    They are hook-free, so they must be rendered as plain FUNCTION CALLS,
    which keeps one render scope and leaves the module-level panel mounted."""
    for el in ("<QueueRowExpansion", "<QueueProjectSnapshot", "<QueueActionHub"):
        assert el not in html, (
            f"{el}/> recreates the component type each render and remounts the "
            "expansion subtree on every poll — render it as a function call instead"
        )


def test_snapshot_setstate_identity_guard(html):
    """Idle polls (unchanged snapshot payload) must not re-render QueueScreen
    at all — same identity-guard idiom as setQueue."""
    q = _queue_block(html)
    assert "setSnapshot(prev => JSON.stringify(prev) === JSON.stringify(d) ? prev : d)" in q


def test_select_a_project_empty_states_removed(html):
    """The expansion always has a target row — the master/detail placeholder
    states are dead and must be gone."""
    assert "Select a project to view details" not in html
    assert "Select a project to see actions" not in html


def test_actions_header_label_removed(html):
    assert 'tracking-wide">Actions</p>' not in html


def test_dead_set_hub_modal_command_removed(html):
    """Pre-redesign latent bug: row click called an undefined setter and threw
    a ReferenceError. Must not survive the rewrite."""
    assert "setHubModalCommand" not in html


def test_depth_indent_removed(html):
    assert "paddingLeft: depth * 16" not in html
    assert "depthOf" not in html


# ── Per-state cell content ────────────────────────────────────────────────────

def test_phase_cell_formats(html):
    rows = _rows_region(html)
    assert "entry.current_phase_raw_id" in rows
    assert "entry.parked_agent" in rows
    assert "· done" in rows, "COMPLETED rows show their final phase as '<id> · done'"
    # Dedup (round 2): the STATUS pill already reads "Running {agent}" for live rows
    # (formatWaitForSentinelLabel inside queueRowDisplay), so the PHASE cell must not
    # repeat the live agent — only parked rows append their snapshot agent.
    assert "entry.live_current_agent" not in rows, (
        "PHASE cell must not duplicate the live agent already shown in the status pill"
    )


def test_elapsed_cell_formats(html):
    assert "function formatElapsedSince" in html
    rows = _rows_region(html)
    assert "formatElapsedSince(entry.started_at)" in rows, "running rows: wall-clock since start"
    assert "'waiting ' + formatElapsedSince(entry.parked_at)" in rows, (
        "parked rows show time WAITING on the operator, not runtime"
    )
    assert "formatDuration(entry.duration_seconds)" in rows, (
        "COMPLETED rows show summed active work time from metrics"
    )
    assert "relTime(entry.failed_at)" in rows


def test_cost_cell_zero_suppressed(html):
    rows = _rows_region(html)
    assert "Number(entry.cost_total) > 0" in rows
    assert "fmtUSD(entry.cost_total)" in rows
    assert "$0.00" not in html


def test_progress_bar_per_state_colors(html):
    rows = _rows_region(html)
    assert "entry.phases_total" in rows
    assert "entry.phases_complete" in rows
    assert "bg-emerald-600" in rows, "running/complete bars are emerald"
    assert "bg-amber-500" in rows, "attention bars are amber"


# ── Carried-over affordances ──────────────────────────────────────────────────

def test_dep_badge_and_waiting_subline_kept(html):
    rows = _rows_region(html)
    assert "depBadgeTitle(" in rows
    assert "isWaitingForParent(parentEntry)" in rows
    assert "Waiting for " in rows
    assert "↳ after " in rows, (
        "a child whose parent is hidden by filters shows the inline '↳ after <parent>' chip"
    )


def test_skip_count_badge_kept(html):
    rows = _rows_region(html)
    assert "entry.skip_count > 0" in rows
    assert "↩" in rows


def test_drag_reorder_on_table_rows(html):
    rows = _rows_region(html)
    assert "draggable={canDrag}" in rows
    assert (
        "const canDrag = reorderMode && !['ACTIVE','COMPLETED'].includes(entry.state) && !entry.parent_id;"
        in rows
    ), "the roots-only / not-ACTIVE/COMPLETED drag gate must survive the table move"


def test_queued_cell_uses_display_ranks(html):
    rows = _rows_region(html)
    assert "effectiveRanks[entry.id]" in rows


# ── Polling without flash (round 2) ───────────────────────────────────────────

def test_snapshot_background_refresh_keeps_stale_data(html):
    """The 5s queue poll bumps snapshotVersion, which refetches the expansion's
    snapshot. Flipping to a Loading state on every bump made the whole expansion
    flash every 5 seconds — the spinner is reserved for a genuine target change
    (a different row expanded); background refreshes swap data in place."""
    q = _queue_block(html)
    assert "snapshotIdRef" in q, "the effect must track which row the on-screen snapshot belongs to"
    assert "const isNewTarget = snapshotIdRef.current !== expandedId" in q
    assert "if (isNewTarget)" in q, "spinner/clearing must be gated on the target changing"
    assert "if (snapshotLoading && !snap)" in q, (
        "QueueProjectSnapshot may show Loading only when it has no data yet — "
        "never over an existing snapshot during a background refresh"
    )


def test_queue_poll_preserves_identity_when_unchanged(html):
    """fetchQueue must keep the previous state identity when the server payload
    is unchanged, so the 5s poll doesn't re-render every row for nothing."""
    q = _queue_block(html)
    assert "setQueue(prev => JSON.stringify(prev) === JSON.stringify(q) ? prev : q)" in q


# ── Per-state expansion designs (round 3) ─────────────────────────────────────

def _snapshot_block(html):
    start = html.index("function QueueProjectSnapshot(")
    end = html.index("// ── Row expansion: Action Hub", start)
    return html[start:end]


def test_active_expansion_drops_started_and_preflight(html):
    """An active project is already valid and running — 'Started' (≈ Elapsed) and
    'Preflight passed' are redundant noise and must not render on the active body."""
    snap = _snapshot_block(html)
    assert "Started {relTime" not in snap, "active body must not show a 'Started' line (≈ Elapsed)"
    assert "Preflight passed {relTime" not in snap, "active body must not show 'Preflight passed'"


def test_ready_uses_next_eligible_for_confidence_note(html):
    """READY rows must consume nextEligible (previously dead, write-only) to show the
    'next up / N ahead' confidence note — filling the wasted READY space."""
    q = _queue_block(html)
    snap = _snapshot_block(html)
    assert "selected.id === nextEligible" in snap, "the snapshot must read nextEligible to flag the next-up project"
    assert "ahead" in snap, "a queued-but-not-next row tells the operator how many are ahead"
    # nextEligible is now genuinely read, not just written.
    assert q.count("nextEligible") >= 2


def test_ready_stat_cards_are_phases_preflight_position_not_cost(html):
    """The READY stat trio is Phases planned / Preflight / Position — NOT cost
    (unknown before a run; we don't guess)."""
    snap = _snapshot_block(html)
    assert "planned" in snap, "READY shows phases planned"
    assert "last checked" in snap, "READY shows when preflight last passed"
    assert "Next up" in snap or "in line" in snap, "READY shows queue position / next-up"


def test_escalation_advisory_left_buttons_right(html):
    """Escalation detail (the advisory) belongs on the LEFT (snapshot), the command
    buttons on the RIGHT (hub) — the queue suppresses the shared panel's own advisory
    via showAdvisory={false} so it isn't duplicated, and renders the advisory from the
    snapshot's escalation_* fields on the left."""
    snap = _snapshot_block(html)
    q = _queue_block(html)
    assert "escalation_message" in snap, "the advisory message renders on the left (snapshot)"
    assert "escalation_recommended_action" in snap, "the suggested action renders on the left"
    assert "showAdvisory={false}" in q, "the queue's EscalationCommandPanel must suppress its built-in advisory"


def test_escalation_panel_has_show_advisory_prop_defaulting_true(html):
    """The shared EscalationCommandPanel gains an additive showAdvisory prop, default
    true so the Pipeline Monitor (which passes nothing) is byte-unchanged."""
    sig_start = html.index("function EscalationCommandPanel({")
    sig = html[sig_start:html.index("}) {", sig_start)]
    assert "showAdvisory = true" in sig, "showAdvisory must default true (Monitor unchanged)"


def test_escalation_expansion_uses_wider_action_rail(html):
    """Escalation must not cram the command buttons — the expansion widens the action
    rail when the selected entry is escalation-like."""
    q = _queue_block(html)
    assert "selectedIsEscalation" in q
    assert "md:w-80" in q, "escalation widens the action rail beyond the default md:w-64"


def test_escalation_panel_compact_prop_defaults_false(html):
    """The shared panel gains a compact mode for the queue's narrow rail; default
    false so the Pipeline Monitor render is unchanged."""
    sig_start = html.index("function EscalationCommandPanel({")
    sig = html[sig_start:html.index("}) {", sig_start)]
    assert "compact = false" in sig


def test_queue_escalation_panel_is_compact(html):
    """The queue passes compact so command buttons render one-line in the rail."""
    q = _queue_block(html)
    assert "compact={true}" in q


def test_compact_buttons_move_desc_to_hover(html):
    """Compact buttons hide the inline description (the per-button hover title and
    the confirmation modal still carry the consequences) — this is what collapses
    the rail from ~700px to ~300px."""
    assert "{!compact && desc &&" in html, (
        "the button desc span must be gated off in compact mode"
    )


def test_compact_panel_drops_inner_orange_frame(html):
    """In the queue rail the panel must not double-frame (expansion border + its own
    border-2 orange box) — compact renders frameless; the urgency color lives in the
    row pill and the left advisory."""
    assert 'compact ? "space-y-3"' in html, (
        "panel container must swap to a frameless layout in compact mode"
    )


def test_escalation_left_shows_waiting_stat_trio(html):
    """The escalation left pane carries a Phases / Cost / Waiting stat trio so it
    is informative rather than a void under the advisory."""
    snap = _snapshot_block(html)
    assert "'Waiting'" in snap, "escalation stat trio includes a Waiting card"
    assert "'on you'" in snap, "the Waiting card subtext says whose turn it is"


# ── Module-scope hoists ───────────────────────────────────────────────────────

def test_fmtusd_hoisted_to_module_scope(html):
    """fmtUSD moves from PipelineCompletePanel-local to module scope (the queue
    table COST cell needs it too). Exactly one definition, defined before the
    first component that uses it."""
    assert html.count("const fmtUSD") == 1
    assert html.index("const fmtUSD") < html.index("function ElapsedTimer")
