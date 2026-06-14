"""Issue 2 — escalation/advisory fields must clear on phase advance without a refresh.

``GET /api/state`` omits the "present-only" escalation fields once
``phase_state.json`` is deleted on phase advance. ``fetchState`` shallow-merges
each poll (``setPState(prev => ({ ...prev, ...data }))``), so an omitted key could
never overwrite a stale value — the ``Reset ×N`` badge stuck until the page was
refreshed (which unmounts ``PipelineScreen`` and reinitialises ``pState``). The
fix re-seeds ``ESCALATION_VIEW_DEFAULTS`` *before* spreading the response, so an
absent key reverts to its default while a present key still wins.

Static content checks — no server / browser needed (project convention; see
``test_w4d_escalation_resets_badge.py``).
"""
from pathlib import Path
import re


def _html() -> str:
    return (Path(__file__).parent.parent / "ui" / "index.html").read_text(encoding="utf-8")


def test_escalation_view_defaults_const_exists():
    """A defaults map for the present-only escalation fields must exist and zero
    the two counters that drive sticky badges (escalation_resets / nuclear_resets)."""
    html = _html()
    m = re.search(r"ESCALATION_VIEW_DEFAULTS\s*=\s*\{(.*?)\}", html, re.DOTALL)
    assert m, "ESCALATION_VIEW_DEFAULTS object literal must exist"
    body = m.group(1)
    assert re.search(r"escalation_resets\s*:\s*0", body), "must default escalation_resets to 0"
    assert re.search(r"nuclear_resets\s*:\s*0", body), "must default nuclear_resets to 0"


def test_fetch_state_reseeds_defaults_before_merging_response():
    """fetchState must spread the defaults BEFORE ...data so an omitted key falls
    back to its default instead of the stale previous value. Catches a revert to
    the bare ``{ ...prev, ...data }`` shallow merge."""
    html = _html()
    i = html.find("setPState(prev =>")
    assert i != -1, "could not locate the setPState merge in fetchState"
    seg = html[i : i + 200]
    pos_defaults = seg.find("ESCALATION_VIEW_DEFAULTS")
    pos_data = seg.find("...data")
    assert pos_defaults != -1, "fetchState must re-seed ESCALATION_VIEW_DEFAULTS on each poll"
    assert pos_data != -1, "fetchState must still apply the response payload (...data)"
    assert pos_defaults < pos_data, (
        "ESCALATION_VIEW_DEFAULTS must be spread BEFORE ...data so response "
        "values still win but omitted keys reset to default"
    )
