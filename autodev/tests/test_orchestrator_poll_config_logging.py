"""Section 4 — diagnostic [POLL][CONFIG] log line + startup-grace knob.

Without per-invocation logging of the effective thresholds, operators
cannot tell from ``/tmp/orchestrator.log`` what stall/grace values are in
effect for a given run.  This test pins the diagnostic line and the
helper that resolves ``AUTODEV_STARTUP_GRACE_*`` env vars.
"""

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


_ORCH_SRC = open(
    os.path.join(PIPELINE_DIR, "orchestrator.py"), encoding="utf-8"
).read()


# ---------------------------------------------------------------------------
# _startup_grace_seconds helper
# ---------------------------------------------------------------------------


class TestStartupGraceHelper:
    def test_helper_function_defined(self):
        """``_startup_grace_seconds(env_name, default_str)`` must exist with
        the same parsing semantics as ``_stall_timeout_seconds``: invalid
        values fall back to the default, minimum of 1 second enforced."""
        helper = getattr(orch_mod, "_startup_grace_seconds", None)
        assert callable(helper), (
            "orchestrator must define _startup_grace_seconds(env_name, "
            "default_str) mirroring _stall_timeout_seconds"
        )

    def test_returns_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("AUTODEV_STARTUP_GRACE_EXECUTOR", raising=False)
        v = orch_mod._startup_grace_seconds("AUTODEV_STARTUP_GRACE_EXECUTOR", "600")
        assert v == 600

    def test_returns_env_value_when_set(self, monkeypatch):
        monkeypatch.setenv("AUTODEV_STARTUP_GRACE_EXECUTOR", "1200")
        v = orch_mod._startup_grace_seconds("AUTODEV_STARTUP_GRACE_EXECUTOR", "600")
        assert v == 1200

    def test_falls_back_on_garbage_env(self, monkeypatch):
        monkeypatch.setenv("AUTODEV_STARTUP_GRACE_EXECUTOR", "not-a-number")
        v = orch_mod._startup_grace_seconds("AUTODEV_STARTUP_GRACE_EXECUTOR", "600")
        assert v == 600, (
            "Invalid env values must silently fall back to the documented "
            "default rather than crashing the orchestrator at startup"
        )

    def test_enforces_minimum_of_one_second(self, monkeypatch):
        monkeypatch.setenv("AUTODEV_STARTUP_GRACE_EXECUTOR", "0")
        v = orch_mod._startup_grace_seconds("AUTODEV_STARTUP_GRACE_EXECUTOR", "600")
        assert v == 1, "Zero must be clamped to 1 (same as _stall_timeout_seconds)"


# ---------------------------------------------------------------------------
# [POLL][CONFIG] diagnostic log
# ---------------------------------------------------------------------------


class TestPollConfigLogLine:
    """Each agent's poll invocation must emit one ``[POLL][CONFIG]`` line
    before calling ``poll_for_sentinel`` so operators can verify the
    effective thresholds without reading env vars."""

    _LINE_PATTERN = re.compile(
        r"\[POLL\]\[CONFIG\]\s+agent=\{?(\w+)\}?\s+"
        r"startup_grace=.+?\s+"
        r"stall_threshold=.+?\s+"
        r"infra_backstop=",
    )

    @pytest.mark.parametrize("agent", ["planner", "executor", "reviewer"])
    def test_poll_site_emits_poll_config_line(self, agent):
        """Source-level check: each poll block must contain a print or
        log line that matches the canonical ``[POLL][CONFIG] agent=… …``
        shape.  Format is locked so operators / future test code can
        rely on it."""
        # Anchor on the agent's poll call uniquely.  Looking up by the
        # stamp string alone is ambiguous for the executor since the
        # Section-0 retry-start abort also references "executor_activity.stamp".
        marker = f"stall_detection_path=_{agent}_stamp"
        idx = _ORCH_SRC.find(marker)
        assert idx != -1, (
            f"Could not locate {agent} poll site by 'stall_detection_path="
            f"_{agent}_stamp' marker"
        )
        window = _ORCH_SRC[max(0, idx - 1500) : idx + 200]
        assert f"[POLL][CONFIG] agent={agent}" in window, (
            f"{agent} poll site must emit a '[POLL][CONFIG] agent={agent} "
            f"startup_grace=… stall_threshold=… infra_backstop=…' log line "
            f"before calling poll_for_sentinel"
        )

    def test_log_line_format_is_canonical(self):
        """At least one occurrence of the canonical format must exist,
        with all four fields (``agent=``, ``startup_grace=``,
        ``stall_threshold=``, ``infra_backstop=``) in order.

        Tolerant of multi-line f-string source layouts — the format is
        defined by the runtime-emitted string, which the Section 2/4
        helper tests already exercise.
        """
        pat = re.compile(
            r'\[POLL\]\[CONFIG\].{0,500}?'
            r'agent=.{0,200}?'
            r'startup_grace=.{0,200}?'
            r'stall_threshold=.{0,200}?'
            r'infra_backstop=',
            re.DOTALL,
        )
        assert pat.search(_ORCH_SRC), (
            "The [POLL][CONFIG] line must include agent, startup_grace, "
            "stall_threshold, and infra_backstop in that order so the "
            "format is parseable by future tooling"
        )


# ---------------------------------------------------------------------------
# startup_grace_seconds propagation to poll_for_sentinel
# ---------------------------------------------------------------------------


class TestStartupGracePassedToPoll:
    @pytest.mark.parametrize("agent", ["planner", "executor", "reviewer"])
    def test_poll_site_passes_startup_grace_seconds(self, agent):
        """Each poll site must pass ``startup_grace_seconds=`` to
        ``poll_for_sentinel`` using ``_startup_grace_seconds`` to resolve
        the per-agent env var with sensible defaults."""
        # Anchor on the poll call uniquely (see emit-test note above).
        marker = f"stall_detection_path=_{agent}_stamp"
        idx = _ORCH_SRC.find(marker)
        assert idx != -1, (
            f"Could not locate {agent} poll site by 'stall_detection_path="
            f"_{agent}_stamp' marker"
        )
        # Window forward into the poll_for_sentinel call.
        window = _ORCH_SRC[max(0, idx - 1500) : idx + 800]
        assert "startup_grace_seconds=" in window, (
            f"{agent} poll site must pass startup_grace_seconds= to "
            f"poll_for_sentinel for the two-knob design"
        )
        env_name = f"AUTODEV_STARTUP_GRACE_{agent.upper()}"
        assert env_name in window, (
            f"{agent} poll site must read {env_name} via "
            f"_startup_grace_seconds(...)"
        )
