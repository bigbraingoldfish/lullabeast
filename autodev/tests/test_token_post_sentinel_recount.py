"""Post-sentinel token recount — close the under-count window.

``_accumulate_role_tokens`` snapshots the OpenClaw session JSONL at the moment
``poll_for_sentinel`` detects ``.done``, but agents routinely keep streaming
after writing the sentinel (mid-turn write / plugin agent_end backstop), so
usage rows landing after that snapshot were never counted. Verified live on
LLN-1 CORE-E2 (2026-06-12): reviewer session recorded 1,186,081 tokens at
sentinel time but the file grew to 1,750,875 (+565k, ~32% missing).

Fix under test:
- ``{role}_tokens_sessions`` — per-attempt sums keyed by session JSONL path.
  ``_accumulate_role_tokens`` REPLACES the entry for its path (a resumed
  session re-read no longer double-counts) and rebuilds ``{role}_tokens_acc``
  as the sum over all entries. A pre-keyed accumulator (mid-phase deploy) is
  preserved as a frozen legacy entry.
- ``_refresh_role_token_accumulators`` — re-sums every still-existing session
  file and rebuilds the accumulators; called by ``_write_canonical_metrics_row``
  before it reads the token fields, so the durable row reflects the final file
  contents, not the sentinel-time snapshot.
"""
import importlib
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def orch(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))

    import orchestrator as orch_mod
    importlib.reload(orch_mod)

    inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    inst.state = {
        "pipeline_status": "RUNNING",
        "current_agent": "reviewer",
        "current_phase": 2,
        "current_phase_raw_id": "CORE-1",
        "reviewer_retries": 0,
        "last_action": "test",
    }
    inst.lock_fd = None
    inst.openclaw_config = {"hooks_url": "http://x", "hooks_token": "t", "pipeline": {}}
    inst.skill_manager = MagicMock()
    inst._current_attempt_retry_class = "initial_attempt"

    proj = tmp_path / "pipeline-project"
    artifacts = proj / ".autodev" / "pipeline"
    artifacts.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(orch_mod, "STATE_FILE", str(tmp_path / "pipeline_state.json"))
    monkeypatch.setattr(orch_mod, "SYMLINK_TARGET", str(proj))
    monkeypatch.setattr(orch_mod, "PROJECT_ARTIFACTS_DIR", str(artifacts))
    monkeypatch.setattr(orch_mod, "PHASE_STATE_FILE", str(artifacts / "phase_state.json"))
    monkeypatch.setattr(orch_mod, "AUTODEV_PIPELINE_ROOT", str(tmp_path))
    monkeypatch.setattr(orch_mod, "_write_pipeline_event", MagicMock())

    return inst, orch_mod, tmp_path


def _openclaw_row(inp=0, out=0, cache_read=0, cache_write=0, total=0, cost=0.0):
    """Real OpenClaw session row shape: role + usage nested under message{}."""
    return json.dumps({
        "id": "msg",
        "type": "message",
        "timestamp": "2026-06-12T00:00:00Z",
        "message": {
            "role": "assistant",
            "usage": {
                "input": inp, "output": out,
                "cacheRead": cache_read, "cacheWrite": cache_write,
                "totalTokens": total,
                "cost": {"total": cost},
            },
        },
    })


def _write_session(path, *rows):
    path.write_text("\n".join(rows) + "\n")


def _append_session(path, *rows):
    with open(path, "a") as f:
        f.write("\n".join(rows) + "\n")


# ---------------------------------------------------------------------------
# Keyed accumulation — replace per session path, sum across paths.
# ---------------------------------------------------------------------------

class TestKeyedAccumulator:

    def test_same_session_reaccumulated_does_not_double_count(self, orch, tmp_path):
        """A resumed session (restart RETRY reuses the attempt-1 session key →
        same JSONL) re-read at the next sentinel must REPLACE its earlier
        contribution, not add to it."""
        inst, _, _ = orch
        jsonl = tmp_path / "sess-a.jsonl"
        _write_session(jsonl, _openclaw_row(inp=100, out=50, total=150, cost=0.001))

        inst._accumulate_role_tokens("reviewer", str(jsonl))
        inst._accumulate_role_tokens("reviewer", str(jsonl))

        acc = inst.read_phase_state()["reviewer_tokens_acc"]
        assert acc["input"] == 100
        assert acc["total_tokens"] == 150

    def test_reaccumulate_after_growth_takes_new_total(self, orch, tmp_path):
        """Re-reading a grown session file replaces the stale partial sum."""
        inst, _, _ = orch
        jsonl = tmp_path / "sess-a.jsonl"
        _write_session(jsonl, _openclaw_row(inp=100, total=100))
        inst._accumulate_role_tokens("executor", str(jsonl))

        _append_session(jsonl, _openclaw_row(inp=40, total=40))
        inst._accumulate_role_tokens("executor", str(jsonl))

        acc = inst.read_phase_state()["executor_tokens_acc"]
        assert acc["input"] == 140
        assert acc["total_tokens"] == 140

    def test_distinct_attempt_sessions_sum(self, orch, tmp_path):
        """Distinct attempts (distinct session keys → distinct JSONLs) add up."""
        inst, _, _ = orch
        a = tmp_path / "attempt-1.jsonl"
        b = tmp_path / "attempt-2.jsonl"
        _write_session(a, _openclaw_row(inp=100, out=10, total=110, cost=0.001))
        _write_session(b, _openclaw_row(inp=200, out=20, total=220, cost=0.002))

        inst._accumulate_role_tokens("executor", str(a))
        inst._accumulate_role_tokens("executor", str(b))

        acc = inst.read_phase_state()["executor_tokens_acc"]
        assert acc["input"] == 300
        assert acc["output"] == 30
        assert acc["total_tokens"] == 330
        assert abs(acc["cost_total"] - 0.003) < 1e-9

    def test_legacy_pre_keyed_acc_preserved(self, orch, tmp_path):
        """A phase already carrying a pre-keyed {role}_tokens_acc (mid-phase
        deploy) keeps that contribution when the first keyed call lands."""
        inst, _, _ = orch
        ps0 = inst.read_phase_state()
        ps0["reviewer_tokens_acc"] = {"input": 100, "total_tokens": 100}
        inst.write_phase_state_atomic(ps0)

        jsonl = tmp_path / "sess.jsonl"
        _write_session(jsonl, _openclaw_row(inp=7, out=3, total=10))
        inst._accumulate_role_tokens("reviewer", str(jsonl))

        acc = inst.read_phase_state()["reviewer_tokens_acc"]
        assert acc["input"] == 107
        assert acc["total_tokens"] == 110

    def test_non_dict_legacy_acc_ignored(self, orch, tmp_path):
        inst, _, _ = orch
        ps0 = inst.read_phase_state()
        ps0["reviewer_tokens_acc"] = "garbage"
        inst.write_phase_state_atomic(ps0)

        jsonl = tmp_path / "sess.jsonl"
        _write_session(jsonl, _openclaw_row(inp=4, total=4))
        inst._accumulate_role_tokens("reviewer", str(jsonl))

        acc = inst.read_phase_state()["reviewer_tokens_acc"]
        assert acc["input"] == 4

    def test_none_path_does_not_crash_or_pollute(self, orch):
        inst, _, _ = orch
        inst._accumulate_role_tokens("planner", None)
        ps = inst.read_phase_state()
        assert ps.get("planner_tokens_acc", {}).get("input", 0) == 0

    def test_roles_keyed_separately(self, orch, tmp_path):
        inst, _, _ = orch
        jsonl = tmp_path / "sess.jsonl"
        _write_session(jsonl, _openclaw_row(inp=2, total=2))
        inst._accumulate_role_tokens("planner", str(jsonl))
        inst._accumulate_role_tokens("executor", str(jsonl))
        ps = inst.read_phase_state()
        assert ps["planner_tokens_acc"]["input"] == 2
        assert ps["executor_tokens_acc"]["input"] == 2


# ---------------------------------------------------------------------------
# _refresh_role_token_accumulators — re-sum at row-write time.
# ---------------------------------------------------------------------------

class TestRefreshAccumulators:

    def test_refresh_picks_up_post_sentinel_rows(self, orch, tmp_path):
        """Rows appended after the sentinel-time snapshot are counted."""
        inst, _, _ = orch
        jsonl = tmp_path / "sess.jsonl"
        _write_session(jsonl, _openclaw_row(inp=100, total=100, cost=0.001))
        inst._accumulate_role_tokens("reviewer", str(jsonl))

        # Agent keeps streaming after .done (the LLN-1 CORE-E2 pattern).
        _append_session(jsonl, _openclaw_row(inp=565, total=565, cost=0.005))

        inst._refresh_role_token_accumulators()
        acc = inst.read_phase_state()["reviewer_tokens_acc"]
        assert acc["input"] == 665
        assert acc["total_tokens"] == 665
        assert abs(acc["cost_total"] - 0.006) < 1e-9

    def test_refresh_keeps_snapshot_when_file_gone(self, orch, tmp_path):
        """A pruned/missing session file keeps its stored contribution."""
        inst, _, _ = orch
        jsonl = tmp_path / "sess.jsonl"
        _write_session(jsonl, _openclaw_row(inp=100, total=100))
        inst._accumulate_role_tokens("executor", str(jsonl))
        os.remove(jsonl)

        inst._refresh_role_token_accumulators()
        acc = inst.read_phase_state()["executor_tokens_acc"]
        assert acc["input"] == 100
        assert acc["total_tokens"] == 100

    def test_refresh_never_shrinks_a_session_contribution(self, orch, tmp_path):
        """Session JSONLs are append-only; a re-sum smaller than the stored
        snapshot (truncated/rotated file) keeps the snapshot."""
        inst, _, _ = orch
        jsonl = tmp_path / "sess.jsonl"
        _write_session(jsonl, _openclaw_row(inp=100, total=100))
        inst._accumulate_role_tokens("executor", str(jsonl))

        _write_session(jsonl, _openclaw_row(inp=1, total=1))  # truncation
        inst._refresh_role_token_accumulators()
        acc = inst.read_phase_state()["executor_tokens_acc"]
        assert acc["total_tokens"] == 100

    def test_refresh_covers_all_attempts_not_just_last(self, orch, tmp_path):
        """A zombie attempt-1 that streamed past its own sentinel is also
        recounted, alongside the grown last attempt."""
        inst, _, _ = orch
        a = tmp_path / "attempt-1.jsonl"
        b = tmp_path / "attempt-2.jsonl"
        _write_session(a, _openclaw_row(inp=100, total=100))
        inst._accumulate_role_tokens("executor", str(a))
        _append_session(a, _openclaw_row(inp=11, total=11))  # zombie streaming
        _write_session(b, _openclaw_row(inp=200, total=200))
        inst._accumulate_role_tokens("executor", str(b))
        _append_session(b, _openclaw_row(inp=22, total=22))

        inst._refresh_role_token_accumulators()
        acc = inst.read_phase_state()["executor_tokens_acc"]
        assert acc["total_tokens"] == 333

    def test_refresh_preserves_legacy_entry(self, orch, tmp_path):
        inst, _, _ = orch
        ps0 = inst.read_phase_state()
        ps0["reviewer_tokens_acc"] = {"input": 100, "total_tokens": 100}
        inst.write_phase_state_atomic(ps0)
        jsonl = tmp_path / "sess.jsonl"
        _write_session(jsonl, _openclaw_row(inp=10, total=10))
        inst._accumulate_role_tokens("reviewer", str(jsonl))

        inst._refresh_role_token_accumulators()
        acc = inst.read_phase_state()["reviewer_tokens_acc"]
        assert acc["total_tokens"] == 110

    def test_refresh_noop_without_sessions_map(self, orch):
        """Pre-fix phase_state (acc only, no sessions map) is left untouched."""
        inst, _, _ = orch
        ps0 = inst.read_phase_state()
        ps0["reviewer_tokens_acc"] = {"input": 100}
        inst.write_phase_state_atomic(ps0)

        inst._refresh_role_token_accumulators()
        assert inst.read_phase_state()["reviewer_tokens_acc"] == {"input": 100}


# ---------------------------------------------------------------------------
# _write_canonical_metrics_row — the durable row reflects the final files.
# ---------------------------------------------------------------------------

class TestMetricsRowRecount:

    def _read_row(self, artifacts, raw_id):
        metrics = artifacts / "metrics.jsonl"
        rows = [json.loads(l) for l in metrics.read_text().splitlines() if l.strip()]
        return next(r for r in rows if r["phase"] == raw_id)

    def test_row_includes_post_sentinel_streaming(self, orch, tmp_path):
        """The LLN-1 CORE-E2 regression: rows landing between sentinel
        detection and row write must appear in the canonical metrics row."""
        inst, mod, _ = orch
        artifacts = tmp_path / "pipeline-project" / ".autodev" / "pipeline"

        jsonl = tmp_path / "reviewer-sess.jsonl"
        _write_session(jsonl, _openclaw_row(inp=1000, out=100, total=1100, cost=0.01))
        inst._accumulate_role_tokens("reviewer", str(jsonl))

        # Reviewer keeps streaming after .done — sentinel snapshot is stale.
        _append_session(jsonl, _openclaw_row(inp=500, out=65, total=565, cost=0.005))

        inst._write_canonical_metrics_row()

        row = self._read_row(artifacts, "CORE-1")
        assert row["reviewer_tokens"]["total_tokens"] == 1665
        assert abs(row["cost_total"] - 0.015) < 1e-9

    def test_row_does_not_double_count_earlier_attempts(self, orch, tmp_path):
        """Re-summing at row-write must replace each attempt's contribution,
        never add a second copy of an earlier attempt."""
        inst, mod, _ = orch
        artifacts = tmp_path / "pipeline-project" / ".autodev" / "pipeline"

        a = tmp_path / "exec-attempt-1.jsonl"
        b = tmp_path / "exec-attempt-2.jsonl"
        _write_session(a, _openclaw_row(inp=100, total=100, cost=0.001))
        inst._accumulate_role_tokens("executor", str(a))
        _write_session(b, _openclaw_row(inp=200, total=200, cost=0.002))
        inst._accumulate_role_tokens("executor", str(b))
        _append_session(b, _openclaw_row(inp=50, total=50, cost=0.0005))

        inst._write_canonical_metrics_row()

        row = self._read_row(artifacts, "CORE-1")
        assert row["executor_tokens"]["total_tokens"] == 350
        assert abs(row["executor_tokens"]["cost_total"] - 0.0035) < 1e-9
