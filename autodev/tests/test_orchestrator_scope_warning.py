"""v0.2.1 — orchestrator drains a planner ``scope_warning`` from planner_output.json.

``_emit_scope_warning(raw_id)`` runs on the planner-PASS path. It:

* emits exactly one ``scope_warning`` event (agent ``planner``, carrying the
  warning text) when planner_output.json carries a non-empty ``scope_warning``;
* stashes the warning string onto phase_state under ``last_scope_warning`` so
  the canonical metrics row can persist it;
* clears a stale stash on a clean pass (no field) so the metrics row is accurate;
* does NOT remove or rewrite planner_output.json — the executor reads it next.

The metrics row then surfaces the stash under ``scope_warning``.

Idiom mirrors test_orchestrator_gate_warnings.py — the executor-side sibling.
"""

import json
import os
import re
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402

_ORCH_PATH = os.path.join(PIPELINE_DIR, "orchestrator.py")
with open(_ORCH_PATH, "r", encoding="utf-8") as _f:
    _ORCH_SRC = _f.read()


def _make_orchestrator():
    return orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)


def _write_planner(tmp_path, payload):
    path = os.path.join(str(tmp_path), "planner_output.json")
    with open(path, "w") as f:
        json.dump(payload, f)
    return path


def _read_ps(tmp_path):
    p = tmp_path / "phase_state.json"
    return json.loads(p.read_text()) if p.exists() else {}


@pytest.fixture
def sw_env(tmp_path, monkeypatch):
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(tmp_path / "phase_state.json"))
    captured = []
    monkeypatch.setattr(
        orch_mod, "_write_pipeline_event",
        lambda et, ph, ag, det: captured.append((et, ph, ag, det)),
    )
    return tmp_path, captured


_WARN = "Phase spans persistence + API + a new dependency; descoped to persistence only."
_SAMPLE = {
    "implementation_plan": ["do a"],
    "tdd_test_structure": ["tests/test_a.py"],
    "pass_criteria": [{"condition": "x", "traces_to": "tdd:tests/test_a.py"}],
    "scope_warning": _WARN,
}


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------


def test_one_scope_warning_event_with_text(sw_env):
    """A non-empty scope_warning → exactly ONE scope_warning event carrying the
    text, attributed to the planner."""
    tmp_path, captured = sw_env
    _write_planner(tmp_path, _SAMPLE)
    _make_orchestrator()._emit_scope_warning("CORE-E1")

    assert len(captured) == 1, f"expected one scope_warning event, got {captured!r}"
    et, ph, ag, det = captured[0]
    assert et == "scope_warning"
    assert ph == "CORE-E1"
    assert ag == "planner"
    assert det.get("warning") == _WARN


def test_planner_output_preserved(sw_env):
    """The helper must not remove or rewrite planner_output.json — the executor
    reads it next."""
    tmp_path, _ = sw_env
    _write_planner(tmp_path, _SAMPLE)
    _make_orchestrator()._emit_scope_warning("CORE-E1")
    path = os.path.join(str(tmp_path), "planner_output.json")
    assert os.path.exists(path)
    assert json.load(open(path)).get("scope_warning") == _WARN


def test_no_file_emits_nothing(sw_env):
    """Missing planner_output.json (defensive) → no event, no crash."""
    tmp_path, captured = sw_env
    _make_orchestrator()._emit_scope_warning("CORE-E1")
    assert captured == []


def test_absent_field_emits_nothing(sw_env):
    """A valid planner output with no scope_warning → no event (the common case)."""
    tmp_path, captured = sw_env
    _write_planner(tmp_path, {"implementation_plan": ["a"], "tdd_test_structure": ["t"],
                              "pass_criteria": [{"condition": "x"}]})
    _make_orchestrator()._emit_scope_warning("CORE-E1")
    assert captured == []


def test_blank_field_emits_nothing(sw_env):
    """A whitespace-only scope_warning is treated as absent — no event."""
    tmp_path, captured = sw_env
    _write_planner(tmp_path, {**_SAMPLE, "scope_warning": "   "})
    _make_orchestrator()._emit_scope_warning("CORE-E1")
    assert captured == []


def test_long_warning_truncated(sw_env):
    """A runaway string is bounded to _SCOPE_WARNING_MAX_CHARS on both the event
    and the stash so it can't bloat the event log or the row."""
    tmp_path, captured = sw_env
    long_text = "x" * 5000
    _write_planner(tmp_path, {**_SAMPLE, "scope_warning": long_text})
    orch = _make_orchestrator()
    orch._emit_scope_warning("CORE-E1")
    cap = orch._SCOPE_WARNING_MAX_CHARS
    assert len(captured[0][3]["warning"]) == cap
    assert len(_read_ps(tmp_path)["last_scope_warning"]) == cap


# ---------------------------------------------------------------------------
# phase_state stash
# ---------------------------------------------------------------------------


def test_stash_holds_warning_text(sw_env):
    tmp_path, _ = sw_env
    _write_planner(tmp_path, _SAMPLE)
    _make_orchestrator()._emit_scope_warning("CORE-E1")
    assert _read_ps(tmp_path).get("last_scope_warning") == _WARN


def test_stash_preserves_existing_phase_state_keys(sw_env):
    """Read-modify-write — sibling keys survive the stash."""
    tmp_path, _ = sw_env
    (tmp_path / "phase_state.json").write_text(json.dumps({"planner_retries": 2}))
    _write_planner(tmp_path, _SAMPLE)
    _make_orchestrator()._emit_scope_warning("CORE-E1")
    ps = _read_ps(tmp_path)
    assert ps.get("planner_retries") == 2
    assert ps.get("last_scope_warning") == _WARN


def test_clean_pass_clears_stale_stash(sw_env):
    """A prior attempt's scope_warning must be cleared when the current passing
    plan raises none, so the metrics row doesn't report stale data."""
    tmp_path, _ = sw_env
    (tmp_path / "phase_state.json").write_text(
        json.dumps({"planner_retries": 1, "last_scope_warning": "old warning"})
    )
    _write_planner(tmp_path, {"implementation_plan": ["a"], "tdd_test_structure": ["t"],
                              "pass_criteria": [{"condition": "x"}]})
    _make_orchestrator()._emit_scope_warning("CORE-E1")
    ps = _read_ps(tmp_path)
    assert "last_scope_warning" not in ps, "stale stash must be cleared on a clean pass"
    assert ps.get("planner_retries") == 1, "clearing must not wipe sibling keys"


# ---------------------------------------------------------------------------
# Structural — call site on the planner-PASS path
# ---------------------------------------------------------------------------


def test_emit_called_on_planner_pass_path():
    """_emit_scope_warning must be invoked on the planner PASS path so the signal
    reaches the feed/stash before the orchestrator moves on to the executor."""
    assert re.search(r"self\._emit_scope_warning\(", _ORCH_SRC), (
        "orchestrator must invoke _emit_scope_warning on the planner PASS path"
    )
