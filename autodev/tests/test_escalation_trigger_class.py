"""P1-B — structured ``escalation_trigger_class`` taxonomy.

Today an escalation carries only a free-text ``escalation_trigger_reason`` (a copy
of ``last_action``), so "which gate fired this escalation" requires prose parsing.
P1-B adds a structured ``escalation_trigger_class`` enum, resolved at the single
escalation-dispatch chokepoint and added to the ``escalation_trigger`` event detail
(alongside ``last_error_code`` and ``last_poll_reason``) and the canonical metrics row.

Mechanism (hybrid):
- error-coded chokepoints (provider, reset-git, resolver, roadmap-checkbox, merge,
  dead-on-arrival) are classified by a pure ``_derive_escalation_trigger_class`` from
  ``last_error_code``;
- the exhaustion / reviewer-routing / webhook / stamp-init / repo-init chokepoints,
  which carry no distinguishing error code at dispatch time, stamp
  ``self.state["escalation_trigger_class"]`` explicitly;
- ``_resolve_escalation_trigger_class`` reads the explicit stamp first, else derives,
  else ``"unknown"`` — so the taxonomy degrades gracefully and never silently lies.

These pin the enum, the derive map, the resolve precedence, the metrics-row field, and
— via a source guard — that every explicit stamp literal in the orchestrator is a real
class (catches a typo or a chokepoint stamping a value outside the enum).
"""
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402


EXPECTED_CLASSES = {
    "planner_retries_exhausted",
    "executor_retries_exhausted",
    "reviewer_retries_exhausted",
    "reviewer_routed",
    "reviewer_verification_unmet",
    "provider_rejected",
    "webhook_failure",
    "resolver_failed",
    "roadmap_checkbox_failed",
    "repo_init_failed",
    "stamp_init_failed",
    "reset_git_failed",
    "git_op_failed",
    "preempted_output_invalid",
    "gate_crash",
    "unknown",
}


def test_enum_membership():
    """The module-level enum is the authoritative set of trigger classes."""
    assert set(orch_mod.ESCALATION_TRIGGER_CLASSES) == EXPECTED_CLASSES


def test_derive_maps_known_error_codes():
    """Each distinguishing ERR_* code derives to its class — this is how the
    error-coded chokepoints are classified without an explicit stamp."""
    cases = {
        "ERR_PROVIDER_REJECTED": "provider_rejected",
        "ERR_SESSION_DEAD_ON_ARRIVAL": "provider_rejected",
        "ERR_RESET_PHASE_GIT_FAILED": "reset_git_failed",
        "ERR_RESET_EXECUTION_GIT_FAILED": "reset_git_failed",
        "ERR_ROADMAP_CHECKBOX_FAILED": "roadmap_checkbox_failed",
        "ERR_PHASE_RESOLVER_FAILED": "resolver_failed",
        "ERR_MERGE_FAILED": "git_op_failed",
    }
    for code, expected in cases.items():
        assert orch_mod._derive_escalation_trigger_class(code) == expected, code


def test_derive_unknown_for_unmapped_or_missing():
    """An unmapped / missing error code derives to ``unknown`` rather than a wrong
    class — the taxonomy must never silently assert a cause it cannot prove."""
    assert orch_mod._derive_escalation_trigger_class("ERR_SOMETHING_NEW") == "unknown"
    assert orch_mod._derive_escalation_trigger_class(None) == "unknown"
    assert orch_mod._derive_escalation_trigger_class("") == "unknown"


def _bare_orch():
    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = {}
    return orch


def test_resolve_explicit_stamp_wins_over_derive():
    """An explicit ``self.state`` stamp (set by an exhaustion/routing chokepoint)
    takes precedence over derivation — so a lingering gate error code from the
    last attempt cannot misclassify an exhaustion escalation."""
    orch = _bare_orch()
    orch.state["escalation_trigger_class"] = "executor_retries_exhausted"
    # A gate error code is present but must be ignored in favor of the stamp.
    assert orch._resolve_escalation_trigger_class({"last_error_code": "ERR_PROVIDER_REJECTED"}) \
        == "executor_retries_exhausted"


def test_resolve_falls_back_to_derive_when_no_stamp():
    """With no explicit stamp, resolution derives from the phase_state error code."""
    orch = _bare_orch()
    assert orch._resolve_escalation_trigger_class({"last_error_code": "ERR_PHASE_RESOLVER_FAILED"}) \
        == "resolver_failed"


def test_resolve_unknown_when_neither():
    """No stamp and no derivable code → ``unknown`` (never a crash, never a lie)."""
    orch = _bare_orch()
    assert orch._resolve_escalation_trigger_class({}) == "unknown"


def test_resolve_ignores_invalid_explicit_stamp():
    """A bogus explicit stamp (not in the enum) is not trusted — resolution falls
    through to derivation, so a typo'd stamp can't inject a junk class."""
    orch = _bare_orch()
    orch.state["escalation_trigger_class"] = "not_a_real_class"
    assert orch._resolve_escalation_trigger_class({"last_error_code": "ERR_MERGE_FAILED"}) \
        == "git_op_failed"


def test_metrics_row_includes_escalation_trigger_class(tmp_path, monkeypatch):
    """The canonical metrics row carries ``escalation_trigger_class`` from phase_state
    so completed-phase history records why a phase escalated during its life."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    pipeline_root = tmp_path / "pipeline_root"
    pipeline_root.mkdir()
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(artifacts))
    monkeypatch.setattr(orch_mod, "AUTODEV_PIPELINE_ROOT", str(pipeline_root))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(artifacts / "phase_state.json"))
    (artifacts / "phase_state.json").write_text(json.dumps({
        "executor_self_failure_retries": 0,
        "executor_reviewer_rejection_retries": 0,
        "planner_tokens_acc": {}, "executor_tokens_acc": {}, "reviewer_tokens_acc": {},
        "escalation_trigger_class": "executor_retries_exhausted",
    }))
    (artifacts / "current_phase.json").write_text(json.dumps({"raw_id": "CORE-E1", "detail": "g"}))

    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = {"current_phase": 1, "current_phase_raw_id": "CORE-E1", "reviewer_retries": 0}
    orch.openclaw_config = {}
    orch.lock_fd = None
    orch._write_canonical_metrics_row()

    lines = [l for l in (artifacts / "metrics.jsonl").read_text().splitlines() if l.strip()]
    assert json.loads(lines[-1]).get("escalation_trigger_class") == "executor_retries_exhausted"


def test_every_explicit_stamp_literal_is_a_valid_class():
    """Source guard: every ``escalation_trigger_class"] = "<literal>"`` stamped in the
    orchestrator must be a member of the enum. This is the cheap, durable guarantee
    that the taxonomy cannot lie — a chokepoint stamping a typo'd or invented class
    fails here instead of emitting an out-of-taxonomy value at runtime. Also asserts a
    floor on the number of stamps so the exhaustion/routing sites can't be silently lost.
    """
    src = open(orch_mod.__file__, "r", encoding="utf-8").read()
    literals = re.findall(r'escalation_trigger_class"\]\s*=\s*"([a-z_]+)"', src)
    assert literals, "expected explicit escalation_trigger_class stamps in orchestrator.py"
    invalid = sorted(set(literals) - EXPECTED_CLASSES)
    assert not invalid, f"stamped classes not in the enum: {invalid}"
    # The non-derivable chokepoints (exhaustion ×3, webhook ×3, reviewer routing ×4,
    # preempted, branch/git-op ×2, stamp-init, repo-init) each stamp explicitly.
    assert len(literals) >= 12, f"too few explicit stamps ({len(literals)}) — a chokepoint may be unstamped"
