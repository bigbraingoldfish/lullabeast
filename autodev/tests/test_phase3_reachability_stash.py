"""Phase 3 — ``_emit_reachability_advisory`` stashes a compact summary onto
phase_state before removing the advisory file.

The advisory file (``executor_advisory_detail.json``) is drained into events
and deleted on the executor-PASS path, long before the canonical metrics row
is written (on the reviewer-PASS path). Change #3 stashes a compact summary
onto phase_state under ``last_reachability_summary`` *before* the ``os.remove``,
so the metrics row can persist the reachability outcome. The stash:

* is best-effort (a phase_state write failure must never break event emission
  or the file removal);
* uses read-modify-write so sibling phase_state keys are preserved;
* is a no-op when the advisory carries no findings.

These tests monkeypatch BOTH ``PROJECT_ARTIFACTS_DIR`` and ``PHASE_STATE_FILE``
(the latter is new vs. ``test_orchestrator_reachability_events.py`` — the
method now writes phase_state, so the file must point at tmp).
"""

import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402


def _write_advisory(tmp_path, payload):
    path = os.path.join(str(tmp_path), "executor_advisory_detail.json")
    with open(path, "w") as f:
        json.dump(payload, f)
    return path


def _make_orchestrator():
    return orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)


def _read_ps(tmp_path):
    p = tmp_path / "phase_state.json"
    return json.loads(p.read_text()) if p.exists() else {}


@pytest.fixture
def reach_env(tmp_path, monkeypatch):
    """Point both PROJECT_ARTIFACTS_DIR and PHASE_STATE_FILE at tmp, and
    silence event emission (these tests assert on the stash, not the events —
    event behaviour is covered by test_orchestrator_reachability_events.py)."""
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json"))
    monkeypatch.setattr(orch_mod, "_write_pipeline_event", lambda *a, **k: None)
    return tmp_path


def test_unreachable_summary_stashed_to_phase_state(reach_env):
    """A populated reachability_summary must be stashed as a compact dict
    (kind/count/files/command — no reason_template) so the metrics row stays
    small while still naming the orphaned files."""
    tmp_path = reach_env
    _write_advisory(tmp_path, {
        "reachability_summary": {
            "files": ["a.py", "b.py", "c.py"],
            "count": 3,
            "command": "python main.py",
            "reason_template": "declared in manifest but not reached from entry point",
        },
        "reachability_not_applicable": None,
        "reachability_diagnostics": [],
    })
    _make_orchestrator()._emit_reachability_advisory("CORE-E1")
    ps = _read_ps(tmp_path)
    assert ps.get("last_reachability_summary") == {
        "kind": "unreachable_summary",
        "count": 3,
        "files": ["a.py", "b.py", "c.py"],
        "command": "python main.py",
    }
    # The advisory file must still be consumed.
    assert not os.path.exists(os.path.join(str(tmp_path), "executor_advisory_detail.json"))


def test_not_applicable_stashed(reach_env):
    """A test-runner entry point stashes a not_applicable kind with its reason."""
    tmp_path = reach_env
    _write_advisory(tmp_path, {
        "reachability_summary": None,
        "reachability_not_applicable": {
            "reason": "entry point is a test runner ('pytest'); reachability check intentionally skipped"
        },
        "reachability_diagnostics": [],
    })
    _make_orchestrator()._emit_reachability_advisory("CORE-E1")
    stash = _read_ps(tmp_path).get("last_reachability_summary")
    assert stash is not None
    assert stash.get("kind") == "not_applicable"
    assert "pytest" in stash.get("reason", "")


def test_diagnostics_only_stash_kind_diagnostics(reach_env):
    """Diagnostics-only (no summary, no not_applicable) stashes a compact
    count — the per-diagnostic detail stays in the events, not the row."""
    tmp_path = reach_env
    _write_advisory(tmp_path, {
        "reachability_summary": None,
        "reachability_not_applicable": None,
        "reachability_diagnostics": [
            {"kind": "no_resolver", "reason": "no resolver for 'cargo run'", "file": None},
        ],
    })
    _make_orchestrator()._emit_reachability_advisory("CORE-E1")
    ps = _read_ps(tmp_path)
    assert ps.get("last_reachability_summary") == {"kind": "diagnostics", "count": 1}


def test_no_findings_no_stash(reach_env):
    """An advisory with nothing populated must NOT write a stash — the row's
    reachability_summary stays null for a clean phase."""
    tmp_path = reach_env
    _write_advisory(tmp_path, {
        "reachability_summary": None,
        "reachability_not_applicable": None,
        "reachability_diagnostics": [],
    })
    _make_orchestrator()._emit_reachability_advisory("CORE-E1")
    assert "last_reachability_summary" not in _read_ps(tmp_path)


def test_stash_preserves_existing_phase_state_keys(reach_env):
    """The stash must read-modify-write — pre-existing phase_state keys must
    survive (proves we don't blanket-overwrite)."""
    tmp_path = reach_env
    (tmp_path / "phase_state.json").write_text(json.dumps({"executor_retries": 2}))
    _write_advisory(tmp_path, {
        "reachability_summary": {"files": ["x.py"], "count": 1,
                                 "command": "python x.py", "reason_template": "..."},
        "reachability_not_applicable": None,
        "reachability_diagnostics": [],
    })
    _make_orchestrator()._emit_reachability_advisory("CORE-E1")
    ps = _read_ps(tmp_path)
    assert ps.get("executor_retries") == 2
    assert ps.get("last_reachability_summary") is not None


def test_no_advisory_file_no_phase_state_write(reach_env):
    """No advisory file present → early return, no phase_state write."""
    tmp_path = reach_env
    _make_orchestrator()._emit_reachability_advisory("CORE-E1")
    assert "last_reachability_summary" not in _read_ps(tmp_path)
