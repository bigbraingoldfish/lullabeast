"""P0 Stage H — phase-state defaults for the new retry counters.

Two new lifetime counters are added alongside the existing per-segment
``executor_retries``:

* ``executor_self_failure_retries`` — incremented every time the executor
  itself fails (gate exit 1, sentinel crash, or blame=impl re-run).
* ``executor_reviewer_rejection_retries`` — incremented every time the
  reviewer returns ``ROUTE_EXECUTOR`` and the executor is re-invoked.

Both accumulate across the whole phase (never reset on reviewer rejection
or operator escalation reset). They reset to 0 only on a true new phase
via ``reset_phase()``. This test file pins the defaults at every site
that initialises phase_state-shaped dictionaries; without uniform
defaults, downstream readers ``.get(..., 0)`` would still work but the
canonical metrics writer's invariant
``executor_attempts == self_failures + reviewer_rejections + 1`` could
silently produce wrong values if the keys were missing on first read.

Pattern: source-text inspection (mirrors ``test_w1_metrics_row_counters.py``)
plus one runtime drive of ``reset_phase`` to confirm the merged dict.
"""

import json
import os
import pathlib
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_ORCH_PATH = pathlib.Path(PIPELINE_DIR) / "orchestrator.py"
_SRC = _ORCH_PATH.read_text()


# ---------------------------------------------------------------------------
# Source-text: both new counter literals must appear at >= 6 init sites
# ---------------------------------------------------------------------------


_EXPECTED_INIT_SITES = 6  # __init__, queue-advance, 3 fallbacks, reset_phase


def test_self_failure_counter_present_at_all_init_sites():
    """``"executor_self_failure_retries": 0`` must appear at each of the six
    phase-state default sites (orchestrator __init__, queue auto-advance,
    increment_{planner,executor,reviewer}_retries fallbacks, reset_phase).
    """
    occurrences = _SRC.count('"executor_self_failure_retries": 0')
    assert occurrences >= _EXPECTED_INIT_SITES, (
        f'"executor_self_failure_retries": 0 appears {occurrences} time(s) '
        f"in orchestrator.py — expected >= {_EXPECTED_INIT_SITES} "
        "(__init__, queue auto-advance self.state, increment_planner_retries "
        "fallback, reset_phase new_phase_state, increment_executor_retries "
        "fallback, increment_reviewer_retries fallback). Missing defaults "
        "would let phase_state.json fall back to .get(..., 0) at read time, "
        "but the canonical metrics row invariant breaks if the keys are "
        "absent on the first phase_state write of a new phase."
    )


def test_rejection_counter_present_at_all_init_sites():
    """Symmetric check for ``executor_reviewer_rejection_retries``."""
    occurrences = _SRC.count('"executor_reviewer_rejection_retries": 0')
    assert occurrences >= _EXPECTED_INIT_SITES, (
        f'"executor_reviewer_rejection_retries": 0 appears {occurrences} '
        f"time(s) — expected >= {_EXPECTED_INIT_SITES}. Each phase-state "
        "init site must include both new counters or downstream readers "
        "race against missing keys."
    )


# ---------------------------------------------------------------------------
# Runtime: __init__ seeds self.state with both counters at 0
# ---------------------------------------------------------------------------


def test_init_state_has_new_counters_zero(monkeypatch, tmp_path):
    """A freshly constructed Orchestrator must have both new counters
    initialised to 0 in self.state. This ensures the in-memory mirror
    matches the on-disk default before the first phase_state write."""
    import orchestrator as orch_mod

    # _validate_openclaw_root requires workspace-{role} subdirs + openclaw.json
    # at OPENCLAW_ROOT. Create the minimal fixture.
    for role in ("planner", "executor", "reviewer"):
        (tmp_path / f"workspace-{role}").mkdir()
    fake_config_path = tmp_path / "openclaw.json"
    fake_config_path.write_text(json.dumps({
        "hooks": {"token": "test-token"},
        "gateway": {"port": 18789, "auth": {"token": "gw-token"}},
    }))
    monkeypatch.setattr(orch_mod, "CONFIG_FILE", str(fake_config_path))
    monkeypatch.setattr(orch_mod, "OPENCLAW_ROOT", str(tmp_path))

    orch = orch_mod.Orchestrator()
    assert orch.state.get("executor_self_failure_retries") == 0, (
        "Orchestrator.__init__ must initialise self.state["
        "'executor_self_failure_retries'] to 0 so the in-memory mirror "
        "matches the on-disk phase_state default."
    )
    assert orch.state.get("executor_reviewer_rejection_retries") == 0, (
        "Orchestrator.__init__ must initialise self.state["
        "'executor_reviewer_rejection_retries'] to 0 for the same reason."
    )


# ---------------------------------------------------------------------------
# Runtime: reset_phase writes both counters to 0 in phase_state.json
# ---------------------------------------------------------------------------


def test_reset_phase_initialises_new_counters(monkeypatch, tmp_path):
    """After ``reset_phase()``, phase_state.json must show both new
    counters at 0 — a phase reset is the only point where they re-set
    to zero (reviewer rejection and operator escalation do NOT reset
    them, since they're lifetime accumulators)."""
    import orchestrator as orch_mod

    # Seed a phase_state with non-zero values so we can verify the reset.
    (tmp_path / "phase_state.json").write_text(json.dumps({
        "executor_self_failure_retries": 7,
        "executor_reviewer_rejection_retries": 4,
        "executor_retries": 2,
        "reviewer_retries": 1,
        "escalation_resets": 1,
    }))
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(tmp_path))
    monkeypatch.setattr(
        orch_mod, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json")
    )
    monkeypatch.setattr(
        orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json")
    )

    orch = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    orch.state = {
        "current_phase": 1,
        "current_phase_raw_id": "CORE-E1",
        "current_agent": "executor",
        "executor_retries": 2,
        "reviewer_retries": 1,
        "planner_retries": 0,
        "pipeline_status": "RUNNING",
        "project_path": str(tmp_path),
    }
    orch.openclaw_config = {}
    orch.lock_fd = None

    # Stub subprocess.run so git checkout / phase_resolver re-run are no-ops.
    import subprocess as _sp

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(_sp, "run", lambda *a, **k: _R())

    orch.reset_phase()

    ps = json.loads((tmp_path / "phase_state.json").read_text())
    assert ps.get("executor_self_failure_retries") == 0, (
        "reset_phase must zero executor_self_failure_retries — phase reset "
        "is the canonical boundary at which lifetime counters re-set."
    )
    assert ps.get("executor_reviewer_rejection_retries") == 0, (
        "reset_phase must zero executor_reviewer_rejection_retries."
    )


# ---------------------------------------------------------------------------
# Source-text: the three increment_* fallback dicts include both counters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("helper_name", [
    "increment_planner_retries",
    "increment_executor_retries",
    "increment_reviewer_retries",
])
def test_increment_helper_fallback_includes_new_counters(helper_name):
    """Each ``increment_*_retries`` helper has a fallback dict for when
    phase_state.json is missing. That fallback must include both new
    counters so the first write after the fallback fires does not produce
    a phase_state.json missing the new keys."""
    needle = f"def {helper_name}"
    idx = _SRC.find(needle)
    assert idx != -1, f"{helper_name} not found in orchestrator source"
    # The fallback dict literal sits within ~40 lines of the method header.
    block = _SRC[idx : idx + 2000]
    assert '"executor_self_failure_retries": 0' in block, (
        f"{helper_name} fallback dict must include "
        '"executor_self_failure_retries": 0 so a missing phase_state.json '
        "does not produce a phase_state lacking the new lifetime counter"
    )
    assert '"executor_reviewer_rejection_retries": 0' in block, (
        f"{helper_name} fallback dict must include "
        '"executor_reviewer_rejection_retries": 0 for the same reason'
    )
