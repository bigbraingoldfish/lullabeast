"""Ideas sidebar readiness_score stays in sync when background assessment finishes."""

import re


def load_index_html():
    with open("ui/index.html", "r") as f:
        return f.read()


def test_readiness_poll_completion_refreshes_ideas_list():
    """When readiness/poll reports done, UI re-fetches GET /api/ideas so sidebar X/10 updates."""
    html = load_index_html()
    assert re.search(r"function IdeasScreen\s*\(\)\s*\{", html), "IdeasScreen not found"

    # Block: poll done → fetch readiness JSON → state updates → refreshIdeas for list badge
    # (raw string uses single quotes so template-literal backticks in the HTML do not break the pattern.)
    assert re.search(
        r"readiness/poll[\s\S]{0,2000}?"
        r"if\s*\(\s*p\.done\s*\)[\s\S]{0,1200}?"
        r"fetch\s*\(\s*`/api/ideas/\$\{ideaId\}/readiness`[\s\S]{0,900}?"
        r"refreshIdeas\s*\(\s*\)",
        html,
    ), (
        "Expected startReadinessPoll completion path to call refreshIdeas() "
        "so ideasList[].readiness_score matches PRD readiness strip"
    )


def test_submit_message_success_still_refreshes_ideas_list():
    """Regression: message POST success must keep refreshing list (readiness may still be pending)."""
    html = load_index_html()
    assert re.search(
        r"fetch\s*\(\s*`/api/ideas/\$\{currentIdeaId\}/message`[\s\S]{0,3500}?refreshIdeas\s*\(\s*\)",
        html,
    ), "submitMessage success path should call refreshIdeas()"


def test_convert_timeout_late_heals_roadmap_from_session():
    """If conversion times out but writes later, the active Roadmap tab should self-heal."""
    html = load_index_html()
    assert re.search(
        r"status\s*===\s*408[\s\S]{0,600}?"
        r"startRoadmapLateHealPoll\s*\(\s*healIdeaId\s*\)",
        html,
    ), "Expected convert 408 path to start roadmap late-heal polling"
    assert re.search(
        r"startRoadmapLateHealPoll[\s\S]{0,2200}?"
        r"fetch\s*\(\s*`/api/ideas/\$\{healIdeaId\}/session`[\s\S]{0,1200}?"
        r"setRoadmapContent\s*\(\s*d\.roadmap_content\s*\|\|\s*\"\"\s*\)",
        html,
    ), (
        "Expected roadmap late-heal poll to call GET /api/ideas/{id}/session "
        "and update roadmapContent when roadmap_draft.done is backfilled"
    )
