"""Per-phase skill label must clear on phase advance without a manual refresh.

``GET /api/state`` emits ``skill_injected`` / ``skill_agent`` as "present-only"
fields (via ``_compute_escalation_view``, the same helper that drives the
escalation/advisory keys). They are dropped once ``phase_state.json`` is deleted
on phase advance — and also whenever the new phase maps no skill discipline.
``fetchState`` shallow-merges each poll (``setPState(prev => ({ ...prev, ...data }))``),
so an omitted key can never overwrite a stale value: the skill label would keep
showing the *previous* phase's discipline until the page was refreshed (which
unmounts ``PipelineScreen`` and reinitialises ``pState``) — the same bug class
fixed for the escalation fields in ``test_escalation_view_stale_merge.py``.

The fix re-seeds a sibling ``PHASE_SKILL_VIEW_DEFAULTS`` map *before* spreading the
response, so an absent key reverts to ``null`` while a present key still wins. It is
kept separate from ``ESCALATION_VIEW_DEFAULTS`` because the skill label is a
different feature family (not escalation).

Static content checks — no server / browser needed (project convention; mirrors
``test_escalation_view_stale_merge.py``).
"""
from pathlib import Path
import re


def _html() -> str:
    return (Path(__file__).parent.parent / "ui" / "index.html").read_text(encoding="utf-8")


def test_phase_skill_view_defaults_const_exists():
    """A defaults map for the present-only skill fields must exist and null BOTH
    skill_injected and skill_agent so the label clears when the new phase injects none."""
    html = _html()
    m = re.search(r"PHASE_SKILL_VIEW_DEFAULTS\s*=\s*\{(.*?)\}", html, re.DOTALL)
    assert m, "PHASE_SKILL_VIEW_DEFAULTS object literal must exist"
    body = m.group(1)
    assert re.search(r"skill_injected\s*:\s*null", body), "must default skill_injected to null"
    assert re.search(r"skill_agent\s*:\s*null", body), "must default skill_agent to null"


def test_fetch_state_reseeds_skill_defaults_before_merging_response():
    """fetchState must spread PHASE_SKILL_VIEW_DEFAULTS BEFORE ...data so an omitted
    skill key falls back to null instead of the stale previous phase's discipline.
    Catches a revert to a merge that drops the skill defaults."""
    html = _html()
    i = html.find("setPState(prev =>")
    assert i != -1, "could not locate the setPState merge in fetchState"
    seg = html[i : i + 300]
    pos_defaults = seg.find("PHASE_SKILL_VIEW_DEFAULTS")
    pos_data = seg.find("...data")
    assert pos_defaults != -1, "fetchState must re-seed PHASE_SKILL_VIEW_DEFAULTS on each poll"
    assert pos_data != -1, "fetchState must still apply the response payload (...data)"
    assert pos_defaults < pos_data, (
        "PHASE_SKILL_VIEW_DEFAULTS must be spread BEFORE ...data so response "
        "values still win but omitted keys reset to null"
    )
