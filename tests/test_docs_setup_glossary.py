"""Guard SETUP.md and GLOSSARY.md sections required by UX friction tracker DOC rows D-01–D-11."""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SETUP_MD = REPO_ROOT / "SETUP.md"
GLOSSARY_MD = REPO_ROOT / "GLOSSARY.md"


@pytest.fixture
def setup_text() -> str:
    return SETUP_MD.read_text(encoding="utf-8")


@pytest.fixture
def glossary_text() -> str:
    assert GLOSSARY_MD.is_file(), "GLOSSARY.md must exist at repo root"
    return GLOSSARY_MD.read_text(encoding="utf-8")


def test_setup_d01_first_screen_guidance(setup_text: str) -> None:
    assert "First load." in setup_text
    assert "Project Ideas" in setup_text
    assert "Setup & Preflight" in setup_text
    assert "Pipeline Monitor" in setup_text


def test_setup_d02_symlink_resume_orchestrator(setup_text: str) -> None:
    assert "pipeline-project" in setup_text
    assert "/api/resume-orchestrator" in setup_text
    assert "reconciled" in setup_text
    assert "pipeline_state.json" in setup_text
    assert "project_path" in setup_text


def test_setup_d04_phase_raw_id_matches_roadmap(setup_text: str) -> None:
    assert "current_phase_raw_id" in setup_text
    assert "roadmap.md" in setup_text


def test_setup_links_to_glossary(setup_text: str) -> None:
    assert "GLOSSARY.md" in setup_text


def test_glossary_d05_openclaw_vs_autodev(glossary_text: str) -> None:
    assert "## OpenClaw vs AutoDev" in glossary_text
    assert "OPENCLAW_ROOT" in glossary_text
    assert "AUTODEV_PIPELINE_ROOT" in glossary_text


def test_glossary_d06_pipeline_states_table(glossary_text: str) -> None:
    assert "## Pipeline states" in glossary_text
    for row in (
        "RUNNING",
        "WAITING_FOR_SENTINEL",
        "WAITING_FOR_HUMAN",
        "HALTED_SILENT",
        "BLOCKED",
        "PIPELINE_COMPLETE",
        "STOPPED",
        "QUEUE_HALTED",
        "IDLE",
        "UNKNOWN",
    ):
        assert row in glossary_text


def test_glossary_d07_queue_entry_states_table(glossary_text: str) -> None:
    assert "## Queue entry states" in glossary_text
    for row in (
        "READY",
        "ACTIVE",
        "BLOCKED",
        "SKIPPED_PENDING",
        "DEPENDENCY_HOLD",
        "ESCALATION",
        "COMPLETED",
        "FAILED",
    ):
        assert row in glossary_text


def test_glossary_d09_git_branch_layout(glossary_text: str) -> None:
    assert "## Git branch layout" in glossary_text
    assert "phase/" in glossary_text or "phase/N" in glossary_text
    assert "base_branch" in glossary_text


def test_glossary_d10_prd_agent_metrics(glossary_text: str) -> None:
    assert "## PRD-agent metrics" in glossary_text
    assert "readiness" in glossary_text.lower()
    assert "conversion_confidence" in glossary_text


def test_glossary_d11_skill_injection(glossary_text: str) -> None:
    assert "## Skill injection" in glossary_text
    assert "skill_mapping.yaml" in glossary_text
    assert "none_mapped" in glossary_text


def test_glossary_points_to_ui_label_sources(glossary_text: str) -> None:
    assert "PIPELINE_LIVE_PILL" in glossary_text
    assert "queueOnlyRowPill" in glossary_text
