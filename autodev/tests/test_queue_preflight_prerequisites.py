"""PREREQ-3 — orchestrator queue-preflight declared-tool re-probe (TDD).

These tests are written before the implementation and must fail against the current
``_queue_preflight`` (which checks only dir/.git/roadmap and never probes tools).

The re-probe is **tools only** — deterministic, bounded, no env reads, no LLM. A
missing *required* tool fails fast on auto-advance (the ``baseball`` incident); an
``unknown`` (inconclusive) probe never blocks. ``host_probes.probe`` is patched on the
reloaded orchestrator module so outcomes are controlled offline.
"""

import importlib
import os
import sys
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


VERIFICATION_WITH_TOOLS = (
    "# Verification\n\n"
    "## Project type\ncli\n\n"
    "## Entry point\n- Command: `mycli --help`\n\n"
    "## Public surface\n1. Do the thing\n\n"
    "## Verification stack\n- Acceptance tool: subprocess\n\n"
    "## Prerequisites\n\n"
    "### Tools\n"
    "- node — Node.js 20+ runtime — needed by all\n"
    "- unity6 — Unity 6 LTS — needed by INFRA-1\n\n"
    "### Environment\n"
    "- OPENAI_API_KEY (secret) — provider key — used by all\n"
)

VERIFICATION_NO_BLOCK = (
    "# Verification\n\n"
    "## Project type\ncli\n\n"
    "## Entry point\n- Command: `mycli --help`\n\n"
    "## Public surface\n1. Do the thing\n\n"
    "## Verification stack\n- Acceptance tool: subprocess\n"
)


@pytest.fixture
def orch(tmp_path, monkeypatch):
    """Bare Orchestrator instance with runtime paths under tmp_path (mirrors
    test_orchestrator_queue.py::orch)."""
    monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))

    import orchestrator as orch_mod
    importlib.reload(orch_mod)

    inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    inst.state = {
        "current_phase": 0,
        "current_phase_raw_id": "",
        "current_agent": "planner",
        "pipeline_status": "RUNNING",
        "project_path": str(tmp_path / "current_project"),
    }
    inst.lock_fd = None
    return inst, orch_mod, tmp_path


def _make_proj(tmp_path, verification=VERIFICATION_WITH_TOOLS):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".git").mkdir()
    (proj / "roadmap.md").write_text("# Roadmap\n")
    if verification is not None:
        (proj / "verification.md").write_text(verification)
    return proj


def _fake_probe(*, missing=(), unknown=(), recorder=None):
    def _p(capability):
        cap = str(capability)
        if recorder is not None:
            recorder.append(cap)
        if cap in missing:
            return {"status": "missing", "detail": f"'{cap}' not found on PATH",
                    "guidance": f"Install {cap}"}
        if cap in unknown:
            return {"status": "unknown", "detail": "timed out"}
        return {"status": "found", "version": "1.0.0", "detail": f"{cap}: 1.0.0"}
    return _p


class TestQueuePreflightReprobe:
    def test_declared_tool_present_passes(self, orch):
        inst, mod, tmp_path = orch
        proj = _make_proj(tmp_path)
        with patch.object(mod, "probe", side_effect=_fake_probe()):
            ok, reason = inst._queue_preflight(str(proj))
        assert ok is True
        assert reason == "ok"

    def test_missing_declared_tool_fails_with_name(self, orch):
        inst, mod, tmp_path = orch
        proj = _make_proj(tmp_path)
        with patch.object(mod, "probe", side_effect=_fake_probe(missing=("unity6",))):
            ok, reason = inst._queue_preflight(str(proj))
        assert ok is False
        assert "unity6" in reason

    def test_unknown_probe_does_not_block(self, orch):
        inst, mod, tmp_path = orch
        proj = _make_proj(tmp_path)
        with patch.object(mod, "probe", side_effect=_fake_probe(unknown=("unity6",))):
            ok, reason = inst._queue_preflight(str(proj))
        assert ok is True
        assert reason == "ok"

    def test_no_verification_md_unchanged(self, orch):
        inst, mod, tmp_path = orch
        proj = _make_proj(tmp_path, verification=None)
        with patch.object(mod, "probe", side_effect=_fake_probe(missing=("unity6",))):
            ok, reason = inst._queue_preflight(str(proj))
        # No declaration ⇒ no probe gate ⇒ behaves exactly like today.
        assert ok is True
        assert reason == "ok"

    def test_no_prerequisites_block_unchanged(self, orch):
        inst, mod, tmp_path = orch
        proj = _make_proj(tmp_path, verification=VERIFICATION_NO_BLOCK)
        with patch.object(mod, "probe", side_effect=_fake_probe(missing=("unity6",))):
            ok, reason = inst._queue_preflight(str(proj))
        assert ok is True
        assert reason == "ok"

    def test_unreadable_verification_degrades_gracefully(self, orch):
        inst, mod, tmp_path = orch
        proj = _make_proj(tmp_path)
        # Make verification.md unreadable by replacing the file with a directory,
        # so open() raises — _queue_preflight must not propagate it.
        (proj / "verification.md").unlink()
        (proj / "verification.md").mkdir()
        with patch.object(mod, "probe", side_effect=_fake_probe()):
            ok, reason = inst._queue_preflight(str(proj))
        assert ok is True
        assert reason == "ok"

    def test_reads_no_env_values_only_probes_tools(self, orch):
        inst, mod, tmp_path = orch
        proj = _make_proj(tmp_path)
        # A secret-laden .env is present; the queue preflight must never read it.
        (proj / ".env").write_text("OPENAI_API_KEY=sk-real-secret\n")
        probed = []
        with patch.object(mod, "probe", side_effect=_fake_probe(recorder=probed)):
            ok, reason = inst._queue_preflight(str(proj))
        assert ok is True
        # Only the declared tool names were probed — never the env key.
        assert set(probed) == {"node", "unity6"}
        assert "sk-real-secret" not in reason
