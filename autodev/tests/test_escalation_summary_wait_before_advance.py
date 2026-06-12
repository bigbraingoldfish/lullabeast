"""Auto-advance holds for the escalation agent's advisory (bounded wait).

In queue auto-advance mode the main escalation dispatch previously advanced to
the next queue project immediately after the escalation webhook fired.
``_select_next_queue_project`` repoints the ``pipeline-project`` symlink, so the
still-in-flight escalation agent's ``escalation_summary.json`` write followed
the repointed symlink into the WRONG project (OpenClaw sandboxes the write tool
to the workspace — absolute-path writes are silently discarded), and the parked
row kept the deterministic fallback message forever.

The fix: after the webhook returns SUCCESS and BEFORE
``_queue_after_park_maybe_advance()``, the orchestrator waits (bounded by env
``AUTODEV_ESCALATION_SUMMARY_WAIT``, default 300 s, 0 disables) for the summary
to land, promotes it, then advances. On timeout it advances anyway — graceful
degradation preserved. The wait only engages when the queue would actually
auto-advance (queue_mode == "auto" with a non-empty queue — mirroring
``_queue_after_park_maybe_advance``): in manual / single-project mode the
WAITING_FOR_HUMAN poll loop already promotes within one cycle.

Same pass: ``escalation_failed.json`` writes go through a shared atomic helper
(``_write_escalation_failed_atomic``, mkstemp + os.replace per the house rule).

Pattern: unit tests on the extracted helpers + source-inspection guards for the
in-``run()`` wiring (idiom from ``test_escalation_advisory_agent_owned.py``).
"""

import json
import os
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in (PIPELINE_DIR, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

ORCHESTRATOR_PATH = os.path.join(PIPELINE_DIR, "orchestrator.py")


def _source() -> str:
    with open(ORCHESTRATOR_PATH, encoding="utf-8") as f:
        return f.read()


def _slice(src, start_sub, end_sub):
    start = src.find(start_sub)
    assert start != -1, f"anchor not found: {start_sub!r}"
    end = src.find(end_sub, start)
    assert end != -1, f"end anchor not found after {start_sub!r}: {end_sub!r}"
    return src[start:end]


class _FakeClock:
    """Deterministic time.time/time.sleep pair — sleeps advance the clock.

    A busy-spinning implementation (sleep never called) cannot hang the test:
    the call-count guard raises after 10k sleeps, and a missing sleep means
    time never advances, which the guard on promote/exists calls would not
    catch — hence sleep is the only clock-advancer by design.
    """

    def __init__(self, start=1_000_000.0):
        self.now = start
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += max(seconds, 0.001)
        if len(self.sleeps) > 10_000:  # pragma: no cover - failure guard
            raise AssertionError("wait loop did not terminate")


def _bare_orch(monkeypatch, tmp_dir):
    """Bare Orchestrator with artifacts + phase_state + queue wired to tmp_dir."""
    import orchestrator as orc_module

    ps_path = os.path.join(tmp_dir, "phase_state.json")
    queue_path = os.path.join(tmp_dir, "pipeline_queue.json")
    monkeypatch.setattr(orc_module, "PHASE_STATE_FILE", ps_path)
    monkeypatch.setattr(orc_module, "PROJECT_ARTIFACTS_DIR", str(tmp_dir))
    monkeypatch.setattr(orc_module, "QUEUE_FILE", queue_path)

    orch = orc_module.Orchestrator.__new__(orc_module.Orchestrator)
    orch.lock_fd = None
    orch.openclaw_config = {"hooks": {"token": "t"}}
    orch.state = {"current_phase": 1, "current_phase_raw_id": "CORE-1"}
    orch.write_state = MagicMock()
    orch.transition_state = MagicMock()
    return orch, ps_path, queue_path


def _seed_fallback_state(ps_path):
    with open(ps_path, "w") as f:
        json.dump({
            "escalation_message": "Executor failed (ERR_TESTS_FAILING). See the log.",
            "escalation_advisory_status": "fallback",
        }, f)


def _seed_queue(queue_path, mode="auto", entries=None):
    if entries is None:
        entries = [{"id": "e1", "state": "ESCALATION", "project_path": "/tmp/p1",
                    "position": 0}]
    with open(queue_path, "w") as f:
        json.dump({"queue": entries, "queue_mode": mode, "queue_version": 1,
                   "last_updated": ""}, f)


def _write_summary(tmp_dir, summary="Executor hit ERR_X three times.",
                   action="Use Reset Execution."):
    path = os.path.join(tmp_dir, "escalation_summary.json")
    with open(path, "w") as f:
        json.dump({"summary": summary, "recommended_action": action}, f)
    return path


def _patch_clock(monkeypatch):
    import orchestrator as orc_module

    clock = _FakeClock()
    monkeypatch.setattr(orc_module.time, "time", clock.time)
    monkeypatch.setattr(orc_module.time, "sleep", clock.sleep)
    return clock


# ---------------------------------------------------------------------------
# _escalation_summary_wait_seconds — env knob parsing
# ---------------------------------------------------------------------------


class TestWaitSecondsEnvParsing:

    def _parse(self):
        import orchestrator as orc_module
        return orc_module._escalation_summary_wait_seconds()

    def test_default_300(self, monkeypatch):
        monkeypatch.delenv("AUTODEV_ESCALATION_SUMMARY_WAIT", raising=False)
        assert self._parse() == 300

    def test_explicit_value(self, monkeypatch):
        monkeypatch.setenv("AUTODEV_ESCALATION_SUMMARY_WAIT", "120")
        assert self._parse() == 120

    def test_garbage_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("AUTODEV_ESCALATION_SUMMARY_WAIT", "5 min")
        assert self._parse() == 300

    def test_zero_disables(self, monkeypatch):
        """0 is a meaningful value (disable the wait) — unlike the stall/grace
        knobs, the minimum clamp is 0, not 1."""
        monkeypatch.setenv("AUTODEV_ESCALATION_SUMMARY_WAIT", "0")
        assert self._parse() == 0

    def test_negative_clamps_to_zero(self, monkeypatch):
        monkeypatch.setenv("AUTODEV_ESCALATION_SUMMARY_WAIT", "-30")
        assert self._parse() == 0

    def test_empty_string_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("AUTODEV_ESCALATION_SUMMARY_WAIT", "  ")
        assert self._parse() == 300


# ---------------------------------------------------------------------------
# _wait_for_escalation_summary_before_advance — unit behavior
# ---------------------------------------------------------------------------


class TestWaitForEscalationSummary:

    def test_summary_already_on_disk_promotes_without_sleeping(self, monkeypatch, tmp_path):
        orch, ps_path, queue_path = _bare_orch(monkeypatch, tmp_path)
        _seed_fallback_state(ps_path)
        _seed_queue(queue_path, mode="auto")
        _write_summary(tmp_path)
        clock = _patch_clock(monkeypatch)
        assert orch._wait_for_escalation_summary_before_advance() is True
        assert clock.sleeps == [], "summary was present at entry — no wait needed"
        with open(ps_path) as f:
            assert json.load(f)["escalation_advisory_status"] == "ready"

    def test_summary_appears_mid_wait_promotes_and_returns_early(self, monkeypatch, tmp_path):
        orch, ps_path, queue_path = _bare_orch(monkeypatch, tmp_path)
        _seed_fallback_state(ps_path)
        _seed_queue(queue_path, mode="auto")
        monkeypatch.setenv("AUTODEV_ESCALATION_SUMMARY_WAIT", "300")
        clock = _patch_clock(monkeypatch)

        real_sleep = clock.sleep

        def sleep_then_land(seconds):
            real_sleep(seconds)
            if len(clock.sleeps) == 3:
                _write_summary(tmp_path, summary="Planner died on attempt 2.",
                               action="RESET_PHASE")

        import orchestrator as orc_module
        monkeypatch.setattr(orc_module.time, "sleep", sleep_then_land)

        assert orch._wait_for_escalation_summary_before_advance() is True
        assert len(clock.sleeps) == 3, "must return on the tick the summary lands"
        with open(ps_path) as f:
            ps = json.load(f)
        assert ps["escalation_advisory_status"] == "ready"
        assert ps["escalation_message"] == "Planner died on attempt 2."

    def test_timeout_returns_false_and_keeps_fallback(self, monkeypatch, tmp_path):
        orch, ps_path, queue_path = _bare_orch(monkeypatch, tmp_path)
        _seed_fallback_state(ps_path)
        _seed_queue(queue_path, mode="auto")
        monkeypatch.setenv("AUTODEV_ESCALATION_SUMMARY_WAIT", "10")
        clock = _patch_clock(monkeypatch)
        assert orch._wait_for_escalation_summary_before_advance() is False
        assert clock.sleeps, "must have actually waited before timing out"
        # The wait is bounded: total slept wall-clock stays in the budget's
        # neighbourhood (one interval of overshoot at most).
        assert sum(clock.sleeps) <= 10 + max(clock.sleeps)
        with open(ps_path) as f:
            assert json.load(f)["escalation_advisory_status"] == "fallback"

    def test_disabled_via_env_zero_skips_entirely(self, monkeypatch, tmp_path):
        orch, ps_path, queue_path = _bare_orch(monkeypatch, tmp_path)
        _seed_fallback_state(ps_path)
        _seed_queue(queue_path, mode="auto")
        _write_summary(tmp_path)  # even a present summary is ignored when disabled
        monkeypatch.setenv("AUTODEV_ESCALATION_SUMMARY_WAIT", "0")
        clock = _patch_clock(monkeypatch)
        assert orch._wait_for_escalation_summary_before_advance() is False
        assert clock.sleeps == []
        with open(ps_path) as f:
            assert json.load(f)["escalation_advisory_status"] == "fallback"

    def test_stop_sentinel_breaks_early_without_consuming(self, monkeypatch, tmp_path):
        orch, ps_path, queue_path = _bare_orch(monkeypatch, tmp_path)
        _seed_fallback_state(ps_path)
        _seed_queue(queue_path, mode="auto")
        stop_file = os.path.join(tmp_path, "pipeline_stop_requested")
        with open(stop_file, "w") as f:
            f.write("")
        clock = _patch_clock(monkeypatch)
        assert orch._wait_for_escalation_summary_before_advance() is False
        assert clock.sleeps == [], "stop must break out before any sleep"
        assert os.path.exists(stop_file), (
            "the wait must NOT consume the stop sentinel — the loop-top "
            "_check_stop_requested() owns consumption"
        )

    def test_manual_queue_mode_skips_wait(self, monkeypatch, tmp_path):
        """In manual mode the orchestrator stays in WAITING_FOR_HUMAN, whose
        poll loop already promotes — waiting here would add nothing."""
        orch, ps_path, queue_path = _bare_orch(monkeypatch, tmp_path)
        _seed_fallback_state(ps_path)
        _seed_queue(queue_path, mode="manual")
        clock = _patch_clock(monkeypatch)
        assert orch._wait_for_escalation_summary_before_advance() is False
        assert clock.sleeps == []

    def test_empty_queue_skips_wait(self, monkeypatch, tmp_path):
        orch, ps_path, queue_path = _bare_orch(monkeypatch, tmp_path)
        _seed_fallback_state(ps_path)
        _seed_queue(queue_path, mode="auto", entries=[])
        clock = _patch_clock(monkeypatch)
        assert orch._wait_for_escalation_summary_before_advance() is False
        assert clock.sleeps == []

    def test_unreadable_queue_skips_wait_without_raising(self, monkeypatch, tmp_path):
        """A corrupt queue file must not add a new failure mode here —
        _queue_after_park_maybe_advance (called right after) owns surfacing it."""
        orch, ps_path, queue_path = _bare_orch(monkeypatch, tmp_path)
        _seed_fallback_state(ps_path)
        with open(queue_path, "w") as f:
            f.write("{not valid json")
        clock = _patch_clock(monkeypatch)
        assert orch._wait_for_escalation_summary_before_advance() is False
        assert clock.sleeps == []


# ---------------------------------------------------------------------------
# Dispatch-site wiring (source-inspection; in-run() blocks are not extractable)
# ---------------------------------------------------------------------------


class TestDispatchWiring:

    def _main_dispatch_block(self):
        return _slice(
            _source(),
            'session_key = f"pipeline:phase-{phase}:{raw_id}:escalation"',
            "if self._queue_after_park_maybe_advance():",
        )

    def test_wait_sits_between_webhook_and_advance(self):
        block = self._main_dispatch_block()
        wait_pos = block.find("self._wait_for_escalation_summary_before_advance()")
        assert wait_pos != -1, (
            "the main escalation dispatch must wait for escalation_summary.json "
            "before the queue auto-advance repoints the pipeline-project symlink"
        )
        webhook_pos = block.find("webhook_status = invoke_agent_webhook")
        assert webhook_pos != -1
        assert webhook_pos < wait_pos, "the wait must come after the webhook fires"

    def test_wait_gated_on_webhook_success(self):
        block = self._main_dispatch_block()
        wait_pos = block.find("self._wait_for_escalation_summary_before_advance()")
        assert wait_pos != -1
        gate_pos = block.rfind('webhook_status == "SUCCESS"', 0, wait_pos)
        assert gate_pos != -1, (
            "the wait must be gated on webhook SUCCESS — when the webhook failed "
            "there is no in-flight agent and no summary will ever land"
        )

    def test_repo_init_dispatch_does_not_wait(self):
        """Repo-init never auto-advances (park-and-advance deliberately not
        applied) — the wait there would stall a returning orchestrator."""
        block = _slice(_source(), ':repo-init-failure"', "Do not enter phase loop")
        assert "_wait_for_escalation_summary_before_advance" not in block

    def test_crash_handler_does_not_wait(self):
        block = _slice(_source(), "Escalated after unhandled exception", "finally:")
        assert "_wait_for_escalation_summary_before_advance" not in block


# ---------------------------------------------------------------------------
# escalation_failed.json — atomic writes (house mkstemp + os.replace rule)
# ---------------------------------------------------------------------------


class TestWriteEscalationFailedAtomic:

    def _write(self, target_dir, data):
        import orchestrator as orc_module
        return orc_module._write_escalation_failed_atomic(str(target_dir), data)

    def test_writes_readable_json(self, tmp_path):
        data = {"timestamp": "2026-06-12T00:00:00Z", "phase": 3,
                "gate": "escalation", "original_failure_reason": "boom"}
        self._write(tmp_path, data)
        with open(os.path.join(tmp_path, "escalation_failed.json")) as f:
            assert json.load(f) == data

    def test_no_stranded_temp_files_on_success(self, tmp_path):
        self._write(tmp_path, {"phase": 1})
        assert os.listdir(tmp_path) == ["escalation_failed.json"]

    def test_overwrites_existing_file(self, tmp_path):
        self._write(tmp_path, {"phase": 1})
        self._write(tmp_path, {"phase": 2})
        with open(os.path.join(tmp_path, "escalation_failed.json")) as f:
            assert json.load(f)["phase"] == 2

    def test_never_raises_on_unwritable_dir(self, tmp_path):
        missing = tmp_path / "does" / "not" / "exist"
        self._write(missing, {"phase": 1})  # must not raise — failure-path diagnostics

    def test_cleans_temp_on_serialization_failure(self, tmp_path):
        self._write(tmp_path, {"bad": object()})  # not JSON-serializable; must not raise
        assert os.listdir(tmp_path) == [], "failed write must not strand temp files"


class TestEscalationFailedSitesUseAtomicHelper:

    def test_no_bare_open_write_remains(self):
        src = _source()
        assert 'escalation_failed.json"), "w"' not in src, (
            "all escalation_failed.json writes must go through "
            "_write_escalation_failed_atomic (mkstemp + os.replace house rule)"
        )

    def test_all_three_sites_call_helper(self):
        """Repo-init dispatch (fallback_dir), main dispatch (PROJECT_ARTIFACTS_DIR),
        crash handler (OPENCLAW_ROOT)."""
        src = _source()
        calls = src.count("_write_escalation_failed_atomic(")
        assert calls >= 4, (  # 1 def + 3 call sites
            f"expected the def + 3 call sites, found {calls} occurrences"
        )
