"""Static contract tests for L-38: plain-language resume/restart orchestrator errors (ui/index.html)."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "ui" / "index.html"


def _index_text() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def test_helpers_present_and_restart_uses_shared_formatter():
    html = _index_text()
    assert "resumeOrchestratorErrorPresentation" in html
    assert "extractResumeOrchestratorRawDetail" in html
    assert "mapResumeOrchestratorFriendlyMessage" in html
    assert "formatResumeOrchestratorError" in html
    block_start = html.find("const handleRestartOrchestrator = async ()")
    assert block_start != -1
    block_end = html.find("const handleGitRecover", block_start)
    assert block_end != -1
    restart_block = html[block_start:block_end]
    assert "formatResumeOrchestratorError(d" in restart_block or "formatResumeOrchestratorError( d" in restart_block.replace(
        " ", ""
    )


def test_escalation_restart_uses_shared_formatter():
    html = _index_text()
    inner_start = html.find("const handleRestartOrchestrator = async ()")
    inner_end = html.find(
        "const restartOrchestratorButton",
        inner_start,
    )
    assert inner_start != -1 and inner_end != -1
    inner = html[inner_start:inner_end]
    assert "formatResumeOrchestratorError(d" in inner


def test_friendly_copy_for_mapped_server_errors():
    """Substrings aligned with ui/server.py repoint / post_resume_orchestrator."""
    html = _index_text()
    assert (
        "The configured project folder is a real directory, not the pipeline-project symlink"
        in html
    )
    assert (
        "No active project path in pipeline state"
        in html
    )
    assert "Orchestrator is already running" in html  # friendly line for lock (contains phrase)


def test_resume_errors_use_split_like_stop():
    html = _index_text()
    assert "splitApiDetail(resumeError)" in html
    assert "splitApiDetail(orchRestartError)" in html


def test_flow_error_uses_split():
    html = _index_text()
    assert "splitApiDetail(flowError)" in html
