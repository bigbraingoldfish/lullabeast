"""P1 Stage G1 — the escalation panel headline must be a clean, deterministic
phase-level string, never the raw blame-attribution ``escalation_trigger_reason``.

The orchestrator exposes ``_clean_escalation_headline()`` and persists its result
as ``escalation_headline`` at every escalation trigger, so the UI has a non-blame
string to render without re-deriving it. The headline is derived from the phase
id — it is structurally incapable of echoing the blame-cap string.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

BLAME_STRING = (
    "Impl blame cap reached (4x): [L3] Insufficient evidence for confident "
    "attribution; defaulting to impl."
)


def _bare_orchestrator():
    from orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.lock_fd = None
    orch.openclaw_config = {}
    orch.state = {}
    return orch


def test_clean_escalation_headline_uses_phase_raw_id():
    """With a phase raw_id in state, the headline names that phase."""
    orch = _bare_orchestrator()
    orch.state = {"current_phase_raw_id": "REND-E1"}
    assert orch._clean_escalation_headline() == "Phase REND-E1 needs your input"


def test_clean_escalation_headline_uses_explicit_raw_id_arg():
    """An explicit raw_id argument wins over state (call sites pass the local)."""
    orch = _bare_orchestrator()
    orch.state = {"current_phase_raw_id": "OTHER"}
    assert orch._clean_escalation_headline("CORE-2") == "Phase CORE-2 needs your input"


def test_clean_escalation_headline_generic_without_raw_id():
    """No raw_id → a generic but clean headline (never blank, never blame)."""
    orch = _bare_orchestrator()
    orch.state = {}
    assert orch._clean_escalation_headline() == "This phase needs your input"


def test_clean_escalation_headline_treats_unknown_as_generic():
    """The escalation call sites default the local raw_id to the sentinel
    "unknown"; that must not become "Phase unknown needs your input"."""
    orch = _bare_orchestrator()
    orch.state = {"current_phase_raw_id": "unknown"}
    assert orch._clean_escalation_headline() == "This phase needs your input"
    assert orch._clean_escalation_headline("unknown") == "This phase needs your input"


def test_escalation_headline_ignores_blame_trigger_reason():
    """Even when a blame-cap string sits in state (last_action /
    escalation_trigger_reason), the computed headline contains none of the blame
    tokens — it is derived from the phase id, not the trigger reason."""
    orch = _bare_orchestrator()
    orch.state = {
        "current_phase_raw_id": "REND-E1",
        "last_action": BLAME_STRING,
        "escalation_trigger_reason": BLAME_STRING,
    }
    headline = orch._clean_escalation_headline()
    low = headline.lower()
    assert "blame" not in low
    assert "[l3]" not in low
    assert "defaulting to impl" not in low
    assert headline == "Phase REND-E1 needs your input"
