"""P1 Stage G2 — the nuclear-reset button (operator escape hatch, cap 2).

Static-lint guards (mirror tests/test_ui_escalation_panel_shared.py) for the
"Reset Everything & Restart Phase" control on the shared EscalationCommandPanel:

* NUCLEAR_RESET lives in ESCALATION_CMD_DEFS with its operator-facing label
* it is in its OWN group (not 'recover') — the recover group is hidden exactly
  when the nuclear button must appear (escalation_resets >= 3), so they cannot share
* the panel gates the button on BOTH caps: visible only at escalation_resets >= 3,
  hidden once nuclear_resets >= 2
* a dedicated NuclearResetConfirmModal carries the canonical destructive copy with a
  WARNING on its own emphasized line
* nuclear_resets is threaded into the panel at BOTH call sites (Monitor + Queue), so
  the single backend surfacing point gates the button identically in both views
"""

import re
from pathlib import Path

import pytest

INDEX_HTML = Path(__file__).parent.parent / "ui" / "index.html"


@pytest.fixture
def html():
    return INDEX_HTML.read_text(encoding="utf-8")


def _escalation_cmd_defs_block(html):
    m = re.search(r"const ESCALATION_CMD_DEFS = \[(.*?)\];", html, re.DOTALL)
    assert m, "ESCALATION_CMD_DEFS array not found in index.html"
    return m.group(0)


def _escalation_panel_block(html):
    m = re.search(
        r"// ─── EscalationCommandPanel.*?(?=// ─── PipelineCompletePanel)", html, re.DOTALL
    )
    assert m, "EscalationCommandPanel block not found in index.html"
    return m.group(0)


def _nuclear_modal_block(html):
    m = re.search(
        r"function NuclearResetConfirmModal\(.*?(?=\n\s+function [A-Z])", html, re.DOTALL
    )
    assert m, "NuclearResetConfirmModal component not found in index.html"
    return m.group(0)


# ── NUCLEAR_RESET command definition ─────────────────────────────────────────

def test_nuclear_reset_in_escalation_cmd_defs(html):
    """The command + its operator label live in the single source of truth."""
    block = _escalation_cmd_defs_block(html)
    assert "NUCLEAR_RESET" in block, "NUCLEAR_RESET must be defined in ESCALATION_CMD_DEFS"
    assert "Reset Everything & Restart Phase" in block, "the operator-facing label must be present"


def test_nuclear_reset_not_in_recover_group(html):
    """The nuclear button must NOT be in the 'recover' group — that group is dropped
    entirely at escalation_resets >= 3 (capReached), which is exactly when the nuclear
    button must appear. It gets its own group so it survives the cap."""
    block = _escalation_cmd_defs_block(html)
    # The NUCLEAR_RESET entry's group must be its own (e.g. 'nuclear'), never 'recover'.
    m = re.search(r"command:\s*'NUCLEAR_RESET'.*?group:\s*'([a-z]+)'", block, re.DOTALL)
    assert m, "NUCLEAR_RESET entry must declare a group"
    assert m.group(1) != "recover", (
        "NUCLEAR_RESET must not share the 'recover' group — that group is hidden at the "
        "escalation cap, but the nuclear button must appear precisely then"
    )


# ── Two-cap gating in the panel ──────────────────────────────────────────────

def test_nuclear_reset_button_gated_on_both_caps(html):
    """Visible only when escalation_resets >= 3 AND nuclear_resets < 2."""
    block = _escalation_panel_block(html)
    assert "nuclear_resets" in block, "panel must read nuclear_resets"
    assert "showNuclear" in block, "panel must compute a showNuclear visibility flag"
    assert "escalation_resets >= 3" in block, (
        "nuclear button must be gated on the escalation cap being spent (escalation_resets >= 3)"
    )
    # the nuclear cap upper-bound must appear in the gating expression
    assert re.search(r"nuclear_resets[^\n]*<\s*2|<\s*2[^\n]*nuclear_resets", block), (
        "nuclear button must be hidden once nuclear_resets >= 2"
    )


def test_nuclear_reset_dispatched_through_confirm_modal(html):
    """The panel routes NUCLEAR_RESET through its confirm-modal dispatch (like STOP)."""
    block = _escalation_panel_block(html)
    assert 'modalCommand === "NUCLEAR_RESET"' in block, (
        "the modal dispatch must branch on NUCLEAR_RESET to render NuclearResetConfirmModal"
    )
    assert "NuclearResetConfirmModal" in block


# ── Dedicated confirm modal with the canonical destructive copy ───────────────

def test_nuclear_reset_confirm_modal_present_with_warning(html):
    """A dedicated modal carries the spec copy + a WARNING on its own emphasized line."""
    modal = _nuclear_modal_block(html)
    assert "permanently discards all code on this phase's branch" in modal, (
        "the canonical destructive copy must be present verbatim"
    )
    assert "WARNING" in modal, "the modal must carry an explicit WARNING"
    assert "last resort" in modal, "the WARNING line must frame this as a last resort"


# ── Threaded into BOTH views ──────────────────────────────────────────────────

def test_nuclear_resets_prop_threaded_in_both_views(html):
    """The single backend surfacing point (_compute_escalation_view) feeds both call
    sites: the Monitor passes pState.nuclear_resets and the Queue passes its snapshot
    value, so the button gates identically in both views."""
    assert "pState.nuclear_resets" in html, "Monitor call site must thread pState.nuclear_resets"
    assert "hubNuclearResets" in html, "Queue call site must thread its snapshot nuclear_resets"
    assert html.count("nuclear_resets={") >= 2, (
        "the nuclear_resets prop must be passed at BOTH EscalationCommandPanel call sites"
    )
