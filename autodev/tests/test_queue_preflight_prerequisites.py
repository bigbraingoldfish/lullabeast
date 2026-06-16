"""Orchestrator `_queue_preflight` — structural checks only.

Host-tool re-probing was **removed**: a reliable present/absent verdict from an
arbitrary declared tool name isn't achievable, so it caused false-positive blocks.
`_queue_preflight` now validates only that the project directory exists, is a git
repo, and has a `roadmap*.md` — and **never** probes declared tools or reads env.
"""

import importlib
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIPELINE_DIR = os.path.join(REPO_ROOT, "autodev", "pipeline")
for _p in [PIPELINE_DIR, REPO_ROOT]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# A verification.md declaring a tool that is definitely NOT on PATH — to prove the
# orchestrator no longer probes/blocks on it.
VERIFICATION_WITH_ABSENT_TOOL = (
    "# Verification\n\n"
    "## Prerequisites\n\n"
    "### Tools\n"
    "- unity6 — Unity 6 LTS — needed by INFRA-1\n"
    "- Python 3.10+ — runtime — needed by all\n\n"
    "### Environment\n"
    "- OPENAI_API_KEY (secret) — provider key — used by all\n"
)


@pytest.fixture
def orch(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_ROOT", str(tmp_path))
    monkeypatch.setenv("AUTODEV_PIPELINE_ROOT", str(tmp_path))
    import orchestrator as orch_mod
    importlib.reload(orch_mod)
    inst = orch_mod.Orchestrator.__new__(orch_mod.Orchestrator)
    inst.lock_fd = None
    return inst, tmp_path


def _make_proj(tmp_path, *, git=True, roadmap=True, verification=None):
    proj = tmp_path / "proj"
    proj.mkdir()
    if git:
        (proj / ".git").mkdir()
    if roadmap:
        (proj / "roadmap.md").write_text("# Roadmap\n")
    if verification is not None:
        (proj / "verification.md").write_text(verification)
    return proj


class TestQueuePreflightStructural:
    def test_valid_project_passes(self, orch):
        inst, tmp_path = orch
        ok, reason = inst._queue_preflight(str(_make_proj(tmp_path)))
        assert ok is True and reason == "ok"

    def test_missing_directory_fails(self, orch):
        inst, tmp_path = orch
        ok, reason = inst._queue_preflight(str(tmp_path / "nope"))
        assert ok is False and "directory" in reason

    def test_not_a_git_repo_fails(self, orch):
        inst, tmp_path = orch
        ok, reason = inst._queue_preflight(str(_make_proj(tmp_path, git=False)))
        assert ok is False and "git" in reason

    def test_no_roadmap_fails(self, orch):
        inst, tmp_path = orch
        ok, reason = inst._queue_preflight(str(_make_proj(tmp_path, roadmap=False)))
        assert ok is False and "roadmap" in reason


class TestNoToolProbing:
    def test_absent_declared_tool_does_not_block(self, orch):
        # The regression guard: a verification.md declaring tools that aren't on PATH
        # (unity6, "Python 3.10+") must NOT block auto-advance — tools aren't probed.
        inst, tmp_path = orch
        proj = _make_proj(tmp_path, verification=VERIFICATION_WITH_ABSENT_TOOL)
        ok, reason = inst._queue_preflight(str(proj))
        assert ok is True and reason == "ok"

    def test_does_not_read_env_values(self, orch):
        # A secret-laden .env present must never be read by the queue preflight.
        inst, tmp_path = orch
        proj = _make_proj(tmp_path, verification=VERIFICATION_WITH_ABSENT_TOOL)
        (proj / ".env").write_text("OPENAI_API_KEY=sk-real-secret\n")
        ok, reason = inst._queue_preflight(str(proj))
        assert ok is True
        assert "sk-real-secret" not in reason
