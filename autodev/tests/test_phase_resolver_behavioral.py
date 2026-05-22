"""Tests for Stage D: phase_resolver structured parsing.

phase_resolver.parse_roadmap must extract the per-phase Behavioral
Verification block, plus the structured Entry / Exit / TDD / Done blocks.
It must also write `verification_path` into current_phase.json so the
orchestrator and agents can locate the project-level verification doc.

Existing behavior preserved:
- ``exit_criteria`` (list of strings from ``>`` lines) unchanged — per
  user design decision #4 (additive only; no churn for current consumers).
- ``phase_number``, ``detail``, ``category``, ``status``, ``raw_id``
  unchanged.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[2]
_GATE = _REPO / "autodev" / "pipeline" / "gate_scripts"
_PIPE = _REPO / "autodev" / "pipeline"


def _run_phase_resolver(project: Path):
    """Helper: invoke phase_resolver.py as a subprocess and load the resulting JSON."""
    gate = _GATE / "phase_resolver.py"
    env = {**os.environ, "PYTHONPATH": f"{_GATE}:{_PIPE}"}
    r = subprocess.run(
        [sys.executable, str(gate), str((project / "roadmap.md").resolve())],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(_GATE),
    )
    return r, project / ".autodev" / "pipeline" / "current_phase.json"


# A canonical roadmap with the full per-phase block. Mirrors the format spec
# in autodev/skill-library/roadmap-converter/roadmap-generation/SKILL.md.
CANONICAL_PHASE = """- [ ] `CORE-E1` | HIGH | Implement task list

  > Test: Lists render without errors.

  **Entry Criteria:**
  CORE-E0 is complete, database schema migrated.

  **Exit Criteria:**
  Task list renders, items load via /api/tasks.

  **TDD Requirements:**
  - `test_task_list.py`: Component renders empty state.
  - `test_task_list_api.py`: API returns task array.

  **Done Criteria:**
  - [ ] Tasks display on /tasks
  - [ ] All tests pass
  - [ ] Reviewer approved

  **Behavioral Verification:**
  - **User-observable:** The user sees a list of tasks on /tasks.
  - **How we'll check:** Navigate to /tasks; expect at least one row rendered.
  - **If this fails, the user sees:** The /tasks page does not load.
"""


# ---------------------------------------------------------------------------
# Behavioral Verification extraction
# ---------------------------------------------------------------------------

class TestBehavioralVerificationExtraction:

    def test_parses_behavioral_verification_block(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / "roadmap.md").write_text(CANONICAL_PHASE)

        r, out = _run_phase_resolver(project)
        assert r.returncode == 0, (r.stdout, r.stderr)
        data = json.loads(out.read_text())
        bv = data.get("behavioral_verification")
        assert isinstance(bv, dict), f"expected dict; got {bv!r}"
        assert "list of tasks" in bv.get("user_observable", "")
        assert "Navigate to /tasks" in bv.get("how_to_check", "")
        assert "does not load" in bv.get("failure_language", "")

    def test_returns_null_behavioral_when_block_missing(self, tmp_path):
        """In-flight transitional case (§2.9): pre-P0 roadmaps still parse, but block is None."""
        project = tmp_path / "proj"
        project.mkdir()
        roadmap = (
            "- [ ] `CORE-1` | LOW | Do the thing\n"
            "  > Test: It works.\n"
        )
        (project / "roadmap.md").write_text(roadmap)

        r, out = _run_phase_resolver(project)
        assert r.returncode == 0, (r.stdout, r.stderr)
        data = json.loads(out.read_text())
        # Field must exist as a key even when absent — None is the contract.
        assert "behavioral_verification" in data
        assert data["behavioral_verification"] is None

    def test_sub_bullet_order_independence(self, tmp_path):
        """Sub-bullets in any order are extracted correctly."""
        project = tmp_path / "proj"
        project.mkdir()
        reordered = (
            "- [ ] `CORE-E1` | LOW | Do the thing\n"
            "  > Test: ok\n"
            "  **Behavioral Verification:**\n"
            "  - **If this fails, the user sees:** Page broken.\n"
            "  - **User-observable:** Page works.\n"
            "  - **How we'll check:** Open the page.\n"
        )
        (project / "roadmap.md").write_text(reordered)

        r, out = _run_phase_resolver(project)
        assert r.returncode == 0, (r.stdout, r.stderr)
        bv = json.loads(out.read_text())["behavioral_verification"]
        assert "Page works" in bv["user_observable"]
        assert "Open the page" in bv["how_to_check"]
        assert "Page broken" in bv["failure_language"]


# ---------------------------------------------------------------------------
# Structured Entry/Exit/TDD/Done blocks
# ---------------------------------------------------------------------------

class TestStructuredBlocks:

    def test_parses_entry_exit_tdd_done_blocks(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / "roadmap.md").write_text(CANONICAL_PHASE)

        r, out = _run_phase_resolver(project)
        assert r.returncode == 0
        data = json.loads(out.read_text())
        assert "CORE-E0 is complete" in data.get("entry_criteria", "")
        assert "Task list renders" in data.get("exit_criteria_block", "")
        tdd = data.get("tdd_requirements")
        assert isinstance(tdd, list) and len(tdd) == 2
        assert any("test_task_list.py" in (t.get("file") or "") for t in tdd)
        done = data.get("done_criteria")
        assert isinstance(done, list)
        assert any("Tasks display" in d for d in done)
        assert any("All tests pass" in d for d in done)

    def test_existing_exit_criteria_list_unchanged(self, tmp_path):
        """Decision #4 — additive only; the legacy exit_criteria list (from `>` lines) is preserved."""
        project = tmp_path / "proj"
        project.mkdir()
        (project / "roadmap.md").write_text(CANONICAL_PHASE)

        r, out = _run_phase_resolver(project)
        assert r.returncode == 0
        data = json.loads(out.read_text())
        # The pre-existing `exit_criteria` field (list of `>` prefixed lines)
        # must continue to exist alongside `exit_criteria_block`.
        ec_list = data.get("exit_criteria")
        assert isinstance(ec_list, list), f"exit_criteria must remain a list; got {ec_list!r}"
        # The `> Test:` line is in there.
        assert any("Test" in ec or "Lists render" in ec for ec in ec_list)


# ---------------------------------------------------------------------------
# Regression guards on prior behavior
# ---------------------------------------------------------------------------

class TestRegressions:

    def test_continues_past_non_caret_lines_within_phase_body(self, tmp_path):
        """Pre-Stage D parser broke on first non-`>` line — that was the bug.

        With the new bounded-scan, sections like ``**Entry Criteria:**``
        (which start with `**`, not `>`) must NOT terminate parsing.
        """
        project = tmp_path / "proj"
        project.mkdir()
        (project / "roadmap.md").write_text(CANONICAL_PHASE)

        r, out = _run_phase_resolver(project)
        assert r.returncode == 0
        data = json.loads(out.read_text())
        # If the parser stopped at the first non-`>` line, behavioral_verification
        # would be absent (it appears AFTER the **Entry Criteria** heading).
        assert data.get("behavioral_verification") is not None

    def test_skips_completed_phase_but_advances_global_idx(self, tmp_path):
        """Completed phases are skipped but still advance the global counter (existing behavior)."""
        project = tmp_path / "proj"
        project.mkdir()
        roadmap = (
            "- [x] `CORE-1` | LOW | First phase (done)\n"
            "  > Test: done\n"
            "  **Behavioral Verification:**\n"
            "  - **User-observable:** done.\n"
            "  - **How we'll check:** done.\n"
            "  - **If this fails, the user sees:** done.\n"
            "\n"
            + CANONICAL_PHASE
        )
        (project / "roadmap.md").write_text(roadmap)

        r, out = _run_phase_resolver(project)
        assert r.returncode == 0
        data = json.loads(out.read_text())
        # The second phase is the active one — its phase_number should reflect
        # the global index (1, not 0).
        assert data.get("raw_id") == "CORE-E1"
        assert data.get("phase_number") == 1


# ---------------------------------------------------------------------------
# verification_path field
# ---------------------------------------------------------------------------

class TestVerificationPathField:

    def test_writes_verification_path_field(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / "roadmap.md").write_text(CANONICAL_PHASE)

        r, out = _run_phase_resolver(project)
        assert r.returncode == 0
        data = json.loads(out.read_text())
        vp = data.get("verification_path")
        assert vp, f"verification_path missing; data: {data}"
        # Must point at the project's verification.md (file presence is
        # unrelated — phase_resolver writes the path; preflight enforces
        # existence).
        assert vp.endswith("verification.md")
        assert str(project.resolve()) in vp
