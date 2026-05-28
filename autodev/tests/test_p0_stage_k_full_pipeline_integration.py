"""P0 Stage K — full-pipeline seam integration.

Pins the four handoffs that connect Stages B → C → D → F into one chain:

* **B → C**: ``/api/ideas/{id}/convert`` output (dual sentinels +
  ``roadmap_content`` + ``verification_content``) round-trips through
  :func:`ui.server._preflight_materialize` without raising any ``fail``
  check rows.
* **C → D**: ``roadmap.md`` + ``verification.md`` on disk parse cleanly
  through ``phase_resolver.py``, producing ``current_phase.json`` with
  populated ``behavioral_verification`` + ``verification_path`` fields.
* **D → F**: a ``current_phase.json`` carrying a populated BV block plus a
  ``reviewer_output.json`` with ``verdict: "pass"`` + 3 on-disk evidence
  anchors drives :func:`reviewer_gate.evaluate_reviewer` to return
  ``"PASS"`` — the cutover happy path.
* **D → F transitional**: a phase without a BV block (legacy in-flight
  case from §2.9 of the P0 plan) must skip behavioural enforcement and
  also return ``"PASS"``.

Per the Stage K plan rationale: four seam-pinning tests instead of one
giant bundled test. When a seam regresses, the failing test names exactly
which handoff broke. Each stage has deep per-stage tests already; this
file's value is pinning the *handoffs*.

Existing patterns reused:
* ``aiohttp.ClientSession`` + ``asyncio.sleep`` mocking: mirror
  ``tests/test_api_ideas_convert.py::test_returns_200_with_roadmap_content_on_success``.
* ``_patch_gate_workspace`` ExitStack: mirror
  ``autodev/tests/test_p0_stage_g_data_path_integration.py:36-47``.
* Phase resolver subprocess invocation: mirror
  ``autodev/tests/test_phase_resolver_behavioral.py:29-40``.

Helpers are inlined in this file rather than extracted to conftest because
no third consumer exists yet — promote on the third user (targeted rule).
"""

import json
import os
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DIR = REPO_ROOT / "autodev" / "pipeline"
GATE_DIR = PIPELINE_DIR / "gate_scripts"

# Pipeline modules go on sys.path so the gate modules import cleanly during
# the in-process ``evaluate_reviewer`` call (subprocess-invoked
# ``phase_resolver.py`` uses its own PYTHONPATH env).
for _p in (str(PIPELINE_DIR), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import reviewer_gate as reviewer_gate_module  # noqa: E402
import utils as utils_module  # noqa: E402


# ---------------------------------------------------------------------------
# Canonical fixtures — kept tight (single phase) to keep the seam tests
# focused on the handoff, not on multi-phase iteration (which has its own
# coverage in test_phase_resolver_behavioral.py / test_roadmap_parser.py /
# test_p0_validate_roadmap_strict.py).
# ---------------------------------------------------------------------------


_CANONICAL_ROADMAP = """\
- [ ] `CORE-E1` | HIGH | Render task list

  > Test: List renders without errors.

  **Entry Criteria:**
  Database schema migrated.

  **Exit Criteria:**
  Task list renders; items load via /api/tasks.

  **TDD Requirements:**
  - `test_task_list.py`: Empty state.
  - `test_task_list_api.py`: Returns task array.

  **Done Criteria:**
  - [ ] Tasks display on /tasks
  - [ ] All tests pass
  - [ ] Reviewer approved

  **Behavioral Verification:**
  - **User-observable:** The user sees a list of tasks on /tasks.
  - **How we'll check:** Navigate to /tasks; expect at least one row rendered.
  - **If this fails, the user sees:** The /tasks page does not load.
"""


_CANONICAL_VERIFICATION = """\
# Verification

## Project type
web-app

## Entry point
- Command: `npm run dev`
- Ready signal: HTTP 200 from http://localhost:5173

## Public surface
1. View the task list
2. Open a task and see its detail

## Verification stack
- Acceptance tool: playwright
- Notes: dev server inspection required.
"""


_CANONICAL_PRD = """\
# Product Requirements Document

## Problem Statement
Users need to see and manage their tasks.

## Goals
- A working task list view at /tasks.
"""


# ---------------------------------------------------------------------------
# Inline helpers — see module docstring for rationale.
# ---------------------------------------------------------------------------


def _run_phase_resolver(project_root: Path) -> tuple[subprocess.CompletedProcess, Path]:
    """Subprocess-invoke ``phase_resolver.py`` against ``project_root/roadmap.md``.

    Mirrors the helper in ``test_phase_resolver_behavioral.py:29-40``; not
    promoted to conftest because only two callers exist (Stage D tests +
    this seam test). Returns (CompletedProcess, current_phase_json_path).
    """
    env = {**os.environ, "PYTHONPATH": f"{GATE_DIR}:{PIPELINE_DIR}"}
    result = subprocess.run(
        [
            sys.executable,
            str(GATE_DIR / "phase_resolver.py"),
            str((project_root / "roadmap.md").resolve()),
        ],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(GATE_DIR),
    )
    return result, project_root / ".autodev" / "pipeline" / "current_phase.json"


def _patch_gate_workspace(tmp_dir: str) -> ExitStack:
    """Redirect gate workspace paths to ``tmp_dir`` for the duration of a with-block.

    Verbatim from ``test_p0_stage_g_data_path_integration.py:36-47`` — the
    canonical reviewer-gate test scaffold. Patches both ``utils`` and
    ``reviewer_gate`` because each module imports its own copies of the
    path constants at module-load time.
    """
    stack = ExitStack()
    tmp_dir = tmp_dir.rstrip(os.sep) + os.sep
    stack.enter_context(patch.object(utils_module, "WORKSPACE_DIR", tmp_dir))
    stack.enter_context(patch.object(utils_module, "ARTIFACTS_DIR", tmp_dir))
    ps = os.path.join(tmp_dir.rstrip(os.sep), "phase_state.json")
    stack.enter_context(patch.object(utils_module, "PHASE_STATE_FILE", ps))
    stack.enter_context(patch.object(reviewer_gate_module, "WORKSPACE_DIR", tmp_dir))
    stack.enter_context(patch.object(reviewer_gate_module, "ARTIFACTS_DIR", tmp_dir))
    stack.enter_context(patch.object(reviewer_gate_module, "PHASE_STATE_FILE", ps))
    return stack


def _make_mock_aiohttp():
    """200-returning aiohttp ClientSession mock — convert/format-correction pattern."""
    mock_response = MagicMock()
    mock_response.status = 200
    mock_session = MagicMock()
    mock_session.post = AsyncMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_cls = MagicMock(return_value=mock_session)
    return mock_cls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStageKFullPipelineIntegration:
    """Four seam-pinning tests for the Stage K cutover."""

    # ------------------------------------------------------------------
    # Seam 1: Stage B output → Stage C input
    # ------------------------------------------------------------------

    def test_b_to_c_seam_convert_output_passes_preflight_materialize(self, tmp_path):
        """``/api/ideas/{id}/convert`` output flows cleanly into ``_preflight_materialize``.

        Pins: the shape returned by the convert endpoint is exactly what
        preflight expects — no field-rename drift between the two layers.
        A regression on either side (e.g. convert dropping
        ``verification_content`` from the response, or preflight requiring
        a new field) fires here.
        """
        # ui.server imports late so the autodev pipeline sys.path setup at
        # module top doesn't shadow anything during collection of unrelated
        # autodev/tests files.
        from fastapi.testclient import TestClient
        from ui.server import _preflight_materialize, app

        ideas_dir = tmp_path / "ideas"
        idea_dir = ideas_dir / "idea-1"
        idea_dir.mkdir(parents=True)
        (idea_dir / "session.json").write_text(json.dumps({
            "messages": [],
            "prd_content": _CANONICAL_PRD,
            "created": "2026-01-01T00:00:00Z",
            "updated": "2026-01-01T00:00:00Z",
        }))

        config = {
            "ideas_dir": str(ideas_dir),
            "hooks_url": "http://localhost:18789/hooks/agent",
            "hooks_token": "test-token",
            "conversion_prompt_path": str(tmp_path / "prompt.txt"),
            "autodev_repo_path": str(REPO_ROOT),
        }
        (tmp_path / "prompt.txt").write_text("Convert this PRD.")

        async def write_both_sentinels(*args, **kwargs):
            (idea_dir / "roadmap_draft.md").write_text(_CANONICAL_ROADMAP)
            (idea_dir / "verification_draft.md").write_text(_CANONICAL_VERIFICATION)
            (idea_dir / "verification_draft.done").write_text("")
            (idea_dir / "roadmap_draft.done").write_text("")

        client = TestClient(app)
        with patch("ui.server.load_config", return_value=config), \
             patch("ui.server.aiohttp.ClientSession", _make_mock_aiohttp()), \
             patch("ui.server._inject_converter_skill"), \
             patch("ui.server.CONVERT_POLL_INTERVAL", 0.05), \
             patch("ui.server.asyncio.sleep", new=write_both_sentinels):
            r = client.post("/api/ideas/idea-1/convert")

        assert r.status_code == 200, f"convert failed: {r.status_code} {r.text}"
        body = r.json()
        assert body["roadmap_content"] == _CANONICAL_ROADMAP
        assert body["verification_content"] == _CANONICAL_VERIFICATION

        # Now feed that exact output into preflight materialize and confirm
        # zero failure rows. This is the seam: the shape one layer produces
        # is the shape the next layer consumes.
        proj = tmp_path / "proj"
        proj.mkdir()
        checks = _preflight_materialize(
            str(proj),
            body["roadmap_content"],
            _CANONICAL_PRD,
            body["verification_content"],
        )
        failures = [c for c in checks if c.get("status") == "fail"]
        assert failures == [], (
            f"_preflight_materialize must produce zero fail rows when given the convert "
            f"endpoint's exact output. Got failures: {failures}"
        )
        # All three documents must have been written. ``_preflight_materialize``
        # strips trailing whitespace before writing (pre-existing behavior — see
        # ``rs.strip()`` at ui/server.py inside ``_preflight_materialize``), so
        # compare on stripped content rather than asserting byte-identical output.
        assert (proj / "roadmap.md").read_text().strip() == _CANONICAL_ROADMAP.strip()
        assert (proj / "prd.md").read_text().strip() == _CANONICAL_PRD.strip()
        assert (proj / "verification.md").read_text().strip() == _CANONICAL_VERIFICATION.strip()

    # ------------------------------------------------------------------
    # Seam 2: Stage C output → Stage D input
    # ------------------------------------------------------------------

    def test_c_to_d_seam_materialized_roadmap_parses_with_behavioral_block(self, tmp_path):
        """``roadmap.md`` written by ``_preflight_materialize`` parses through
        ``phase_resolver.py`` with the new Stage D fields populated.

        Pins: ``phase_resolver`` accepts the exact format ``_validate_roadmap_content``
        + the format-correction skill produce. A regression on either side
        (e.g. validator accepting a shape the parser can't read, or parser
        dropping a required field) fires here.
        """
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "roadmap.md").write_text(_CANONICAL_ROADMAP)
        (proj / "verification.md").write_text(_CANONICAL_VERIFICATION)

        result, current_phase_path = _run_phase_resolver(proj)
        assert result.returncode == 0, (
            f"phase_resolver failed: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        data = json.loads(current_phase_path.read_text())

        bv = data.get("behavioral_verification")
        assert isinstance(bv, dict), (
            f"behavioral_verification must be a dict; got {bv!r}. "
            f"Either the parser regressed or the validator accepted a shape the parser can't read."
        )
        assert bv.get("user_observable")
        assert bv.get("how_to_check")
        assert bv.get("failure_language")

        vp = data.get("verification_path")
        assert vp, f"verification_path missing from current_phase.json; data: {data}"
        assert vp.endswith("verification.md")
        # Path must resolve under the project root we just materialized.
        assert str(proj.resolve()) in vp, (
            f"verification_path must resolve under the project root {proj}; got {vp!r}"
        )

    # ------------------------------------------------------------------
    # Seam 3: Stage D output → Stage F input (PASS happy path)
    # ------------------------------------------------------------------

    def test_d_to_f_seam_current_phase_drives_reviewer_gate_pass(self, tmp_path):
        """A ``current_phase.json`` with a populated BV block + a passing
        reviewer output produces ``evaluate_reviewer → "PASS"``.

        Pins: the gate's behavioural-verification check accepts the shape
        the parser writes (3 sub-bullet keys + non-null values), and a
        reviewer_output with verdict:"pass" + ≥3 on-disk evidence anchors
        satisfies all gate requirements simultaneously. A regression at
        either end (parser dropping a sub-bullet, gate tightening the
        evidence shape) fires here.
        """
        cp_block = {
            "user_observable": "The user sees a list of tasks on /tasks.",
            "how_to_check": "Navigate to /tasks; expect at least one row rendered.",
            "failure_language": "The /tasks page does not load.",
        }
        (tmp_path / "current_phase.json").write_text(json.dumps({
            "phase_number": 1,
            "detail": "Phase CORE-E1: Render task list",
            "category": "CORE",
            "raw_id": "CORE-E1",
            "status": "PENDING",
            "exit_criteria": [],
            "behavioral_verification": cp_block,
        }))

        # 3 evidence anchors on disk — gate enforces on-disk existence on verdict: "pass".
        evidence = []
        (tmp_path / "behavioral-smoke").mkdir(parents=True, exist_ok=True)
        for i in range(3):
            rel = f"behavioral-smoke/anchor-{i + 1}.txt"
            (tmp_path / rel).write_text(f"anchor-{i + 1}")
            evidence.append({
                "claim": f"Public-surface claim {i + 1}",
                "file_or_screenshot_or_log": rel,
                "method": "stdout_capture",
            })

        rv_path = tmp_path / "reviewer_output.json"
        rv_path.write_text(json.dumps({
            "blocking_issues": [],
            "integration_tests_passing": True,
            "behavioral_verification": {
                "verdict": "pass",
                "how_to_check_followed": True,
                "evidence": evidence,
            },
        }))

        # Done-criteria artifacts the gate inspects.
        (tmp_path / "phase_state.json").write_text(json.dumps({
            "planner_retries": 0,
            "executor_retries": 0,
            "reviewer_retries": 0,
            "reviewer_rejected": False,
            "escalation_resets": 0,
        }))
        phases_dir = tmp_path / "phases"
        phases_dir.mkdir(parents=True, exist_ok=True)
        (phases_dir / "CORE-E1.md").write_text("# CORE-E1\n")
        (tmp_path / "metrics.jsonl").write_text(
            json.dumps({"ts": "2026-05-27T00:00:00Z", "phase": "CORE-E1"}) + "\n"
        )

        with _patch_gate_workspace(str(tmp_path)):
            verdict = reviewer_gate_module.evaluate_reviewer(str(rv_path))

        assert verdict == "PASS", (
            f"Expected reviewer_gate to return PASS when the BV block is populated AND "
            f"reviewer_output has verdict:pass with 3 on-disk evidence anchors. Got: {verdict!r}. "
            f"Check the reviewer_gate's _check_behavioral_verification + _check_done_criteria paths."
        )

    # ------------------------------------------------------------------
    # Seam 4: Stage D output → Stage F input (transitional null-skip)
    # ------------------------------------------------------------------

    def test_d_to_f_seam_pre_p0_phase_skips_behavioral_gate(self, tmp_path):
        """A phase with ``behavioral_verification: None`` (in-flight legacy
        case from P0 §2.9) must bypass behavioural enforcement entirely.

        Pins: ``_requires_behavioral_verification(None) → False`` so a
        reviewer_output without a ``behavioral_verification`` field is not
        rejected as ``BEHAVIORAL_UNVERIFIED``. The unit test
        ``test_reviewer_gate_behavioral_verification.py`` pins this in
        isolation; this test pins it after the full data path so a
        regression that moves the null-check elsewhere (e.g. into
        ``evaluate_reviewer`` itself) is caught at integration time.
        """
        (tmp_path / "current_phase.json").write_text(json.dumps({
            "phase_number": 1,
            "detail": "Phase CORE-1: Legacy phase",
            "category": "CORE",
            "raw_id": "CORE-1",
            "status": "PENDING",
            "exit_criteria": [],
            "behavioral_verification": None,
        }))

        rv_path = tmp_path / "reviewer_output.json"
        rv_path.write_text(json.dumps({
            "blocking_issues": [],
            "integration_tests_passing": True,
            # Note: no "behavioral_verification" field at all — legacy reviewer output.
        }))

        (tmp_path / "phase_state.json").write_text(json.dumps({
            "planner_retries": 0,
            "executor_retries": 0,
            "reviewer_retries": 0,
            "reviewer_rejected": False,
            "escalation_resets": 0,
        }))
        phases_dir = tmp_path / "phases"
        phases_dir.mkdir(parents=True, exist_ok=True)
        (phases_dir / "CORE-1.md").write_text("# CORE-1\n")
        (tmp_path / "metrics.jsonl").write_text(
            json.dumps({"ts": "2026-05-27T00:00:00Z", "phase": "CORE-1"}) + "\n"
        )

        with _patch_gate_workspace(str(tmp_path)):
            verdict = reviewer_gate_module.evaluate_reviewer(str(rv_path))

        assert verdict == "PASS", (
            f"A phase without a BV block must skip behavioural enforcement and PASS on the "
            f"non-behavioural checks. Got: {verdict!r}. If this returned BEHAVIORAL_UNVERIFIED, "
            f"_requires_behavioral_verification's null-skip regressed; if it returned ROUTE_*, "
            f"some other check on reviewer_output started failing."
        )
