"""The executor is told when the reviewer sent the phase back, and where the
reviewer's blocking issues are.

A reviewer rejection (ROUTE_EXECUTOR) re-runs the executor in a fresh session
(``-b{N}``). Its default prompt ("Begin implementation…") says nothing about
the rejection, and its AGENTS.md said the blocking issues arrive "in your
invocation context", which nothing did. The handler now writes a one-shot
``executor_retry_directive``, delivered as the webhook ``message=`` by
``_invoke_executor`` (the MISSING_ARTIFACTS channel, whose delivery tests live
in test_missing_artifacts_orchestrator.py): a fix round, the work preserved,
the issues in failure_context.json (``source: "reviewer"``).
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DIR = REPO_ROOT / "autodev" / "pipeline"
for _p in (str(PIPELINE_DIR), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import orchestrator as orch_mod  # noqa: E402

_ORCH_SRC = (PIPELINE_DIR / "orchestrator.py").read_text(encoding="utf-8")
_AGENTS_MD = (REPO_ROOT / "autodev" / "agents" / "executor" / "AGENTS.md").read_text(encoding="utf-8")


def _route_executor_body() -> str:
    start = _ORCH_SRC.find('elif gate_result == "ROUTE_EXECUTOR":')
    assert start != -1
    return _ORCH_SRC[start:_ORCH_SRC.find("elif gate_result ==", start + 10)]


def test_directive_frames_the_fix_round():
    d = orch_mod._reviewer_rejection_directive("CORE-E2")
    assert d.startswith("REVIEWER REJECTION:")
    assert "CORE-E2" in d
    assert "PRESERVED" in d and "do NOT rebuild" in d
    assert 'pipeline-project/.autodev/pipeline/failure_context.json (source "reviewer")' in d
    assert "Scenario B" in d


def test_directive_is_self_contained():
    """message= replaces the default prompt, so the directive carries its reads
    and its finish."""
    d = orch_mod._reviewer_rejection_directive("CORE-E2")
    for needle in (
        "pipeline-project/prd.md",
        "pipeline-project/verification.md",
        "pipeline-project/.autodev/pipeline/current_phase.json",
        "pipeline-project/.autodev/pipeline/planner_output.json",
        "pipeline-project/.autodev/pipeline/executor_output.json",
        "pipeline-project/.autodev/pipeline/executor_output.done last",
    ):
        assert needle in d, needle


def test_route_executor_handler_writes_the_directive():
    body = _route_executor_body()
    call = body.find('_ps_re["executor_retry_directive"] = _reviewer_rejection_directive(')
    assert call != -1, "ROUTE_EXECUTOR must queue the rejection directive"
    assert call < body.find("self.write_phase_state_atomic(_ps_re)")


def test_agents_md_says_where_the_blocking_issues_are():
    assert "invocation context" not in _AGENTS_MD, (
        "AGENTS.md must not promise blocking issues in the invocation context; "
        "they are in failure_context.json"
    )
    scenario_b = _AGENTS_MD[_AGENTS_MD.index("**Scenario B"):]
    assert "failure_context.json" in scenario_b.split("\n\n")[0]
