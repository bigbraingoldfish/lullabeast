"""Tests for _parse_agent_response() and the parsed field in POST /api/ideas/{id}/message."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ui.server import _parse_agent_response


FIXTURE_FULL = """\
DRAFTING: Functional Requirements
ASSUMPTION: The user wants a REST API, not GraphQL, since no protocol was specified.
ASSUMPTION: Mobile support is out of scope for v1 based on the conversation so far.

I've drafted the Functional Requirements section based on what we discussed. Let me know if any of these need adjustment.

QUESTIONS:
[SINGLE] What authentication method should the API use?
- API keys
- OAuth 2.0 / JWT
- Session-based (cookie)

[MULTI] Which deployment targets are in scope for v1?
- AWS Lambda
- Docker / self-hosted
- Vercel / serverless edge
- Kubernetes

Let me know your answers and I'll refine the requirements accordingly.
"""

FIXTURE_PROSE_ONLY = "This is a plain prose response with no structured markers at all."

FIXTURE_QUESTIONS_ONLY = """\
QUESTIONS:
[SINGLE] What is your primary use case?
- Internal tooling
- Customer-facing product
- Developer API
"""

FIXTURE_NO_OPTIONS = """\
Here is some prose.

QUESTIONS:
[SINGLE] What deployment target?
- AWS
- GCP
"""


def test_drafting_extracted():
    result = _parse_agent_response(FIXTURE_FULL)
    assert result["drafting"] == "Functional Requirements"


def test_assumptions_extracted():
    result = _parse_agent_response(FIXTURE_FULL)
    assert len(result["assumptions"]) == 2
    assert "REST API" in result["assumptions"][0]
    assert "Mobile support" in result["assumptions"][1]


def test_questions_extracted():
    result = _parse_agent_response(FIXTURE_FULL)
    assert len(result["questions"]) == 2


def test_single_question_type_and_options():
    result = _parse_agent_response(FIXTURE_FULL)
    q1 = result["questions"][0]
    assert q1["type"] == "single"
    assert "authentication" in q1["text"].lower()
    assert q1["options"] == ["API keys", "OAuth 2.0 / JWT", "Session-based (cookie)"]


def test_multi_question_type_and_options():
    result = _parse_agent_response(FIXTURE_FULL)
    q2 = result["questions"][1]
    assert q2["type"] == "multi"
    assert "deployment" in q2["text"].lower()
    assert "AWS Lambda" in q2["options"]
    assert "Docker / self-hosted" in q2["options"]
    assert len(q2["options"]) == 4


def test_prose_excludes_markers():
    result = _parse_agent_response(FIXTURE_FULL)
    prose = result["prose"]
    assert "DRAFTING:" not in prose
    assert "ASSUMPTION:" not in prose
    assert "QUESTIONS:" not in prose
    assert "[SINGLE]" not in prose
    assert "[MULTI]" not in prose
    assert "I've drafted the Functional Requirements" in prose
    assert "Let me know your answers" in prose


def test_prose_only_response():
    result = _parse_agent_response(FIXTURE_PROSE_ONLY)
    assert result["prose"] == FIXTURE_PROSE_ONLY
    assert result["drafting"] is None
    assert result["assumptions"] == []
    assert result["questions"] == []


def test_questions_only_response():
    result = _parse_agent_response(FIXTURE_QUESTIONS_ONLY)
    assert result["prose"] == ""
    assert result["drafting"] is None
    assert len(result["questions"]) == 1
    q = result["questions"][0]
    assert q["type"] == "single"
    assert q["options"] == ["Internal tooling", "Customer-facing product", "Developer API"]


def test_empty_string():
    result = _parse_agent_response("")
    assert result["prose"] == ""
    assert result["drafting"] is None
    assert result["assumptions"] == []
    assert result["questions"] == []


def test_mid_message_drafting_truncates_prose_and_sets_drafting():
    content = "Some prose\nDRAFTING: Not a marker\nMore prose"
    result = _parse_agent_response(content)
    assert result["drafting"] == "Not a marker"
    assert result["prose"] == "Some prose"
    assert "More prose" not in result["prose"]


def test_questions_with_prose_before_and_after():
    result = _parse_agent_response(FIXTURE_FULL)
    # Prose from before the QUESTIONS block should be present
    assert "I've drafted the Functional Requirements" in result["prose"]
    # Prose after QUESTIONS block (trailing line)
    assert "Let me know your answers" in result["prose"]


def test_no_options_question():
    result = _parse_agent_response(FIXTURE_NO_OPTIONS)
    assert "Here is some prose." in result["prose"]
    assert len(result["questions"]) == 1
    assert result["questions"][0]["options"] == ["AWS", "GCP"]


FIXTURE_QUESTIONS_NO_COLON = """\
Some intro.

QUESTIONS
[SINGLE] Pick one
- A
- B
"""

FIXTURE_QUESTIONS_NUMBERED = """\
QUESTIONS:
1. What is your primary use case?
- Internal tooling
- Customer-facing product
2. Which region?
- US
- EU
"""


def test_questions_heading_without_colon():
    result = _parse_agent_response(FIXTURE_QUESTIONS_NO_COLON)
    assert len(result["questions"]) == 1
    assert result["questions"][0]["type"] == "single"
    assert result["questions"][0]["options"] == ["A", "B"]


def test_questions_numbered_question_lines():
    result = _parse_agent_response(FIXTURE_QUESTIONS_NUMBERED)
    assert len(result["questions"]) == 2
    assert "primary use case" in result["questions"][0]["text"].lower()
    assert result["questions"][0]["options"] == ["Internal tooling", "Customer-facing product"]
    assert "region" in result["questions"][1]["text"].lower()
    assert result["questions"][1]["options"] == ["US", "EU"]


FIXTURE_DRAFTING_MID_MESSAGE = """\
Great — clear answers...
Another line of intro.

ASSUMPTION: First assumption for the session.
ASSUMPTION: Second assumption for the session.

DRAFTING: Full PRD Draft

## 1. Problem Statement
The product solves X.

## 2. Goals
Goal one and two.
"""


def test_drafting_mid_message_fixture():
    result = _parse_agent_response(FIXTURE_DRAFTING_MID_MESSAGE)
    assert result["drafting"] == "Full PRD Draft"
    assert "## Problem Statement" not in result["prose"]
    assert "Great — clear answers" in result["prose"]
    assert result["prose"].strip() == result["prose"]
    assert len(result["assumptions"]) == 2
    assert "First assumption" in result["assumptions"][0]


FIXTURE_MARKDOWN_QUESTIONS_HEADING = """\
Intro line.

## QUESTIONS
**1. Game mode?**
- Option A
- Option B

**2. Which platform?**
- Web
- Mobile
"""


def test_markdown_questions_heading_and_bold_numbers():
    result = _parse_agent_response(FIXTURE_MARKDOWN_QUESTIONS_HEADING)
    assert "Intro line." in result["prose"]
    assert len(result["questions"]) == 2
    assert "game mode" in result["questions"][0]["text"].lower()
    assert result["questions"][0]["options"] == ["Option A", "Option B"]
    assert "platform" in result["questions"][1]["text"].lower()
    assert result["questions"][1]["options"] == ["Web", "Mobile"]


def test_questions_heading_triple_hash_with_colon():
    content = (
        "### QUESTIONS:\n"
        "1. Pick one?\n"
        "- X\n"
        "- Y\n"
    )
    result = _parse_agent_response(content)
    assert len(result["questions"]) == 1
    assert result["questions"][0]["options"] == ["X", "Y"]


def test_questions_heading_bold_wrapped_word():
    content = (
        "**QUESTIONS**\n"
        "1. First?\n"
        "- A\n"
    )
    result = _parse_agent_response(content)
    assert len(result["questions"]) == 1
    assert result["questions"][0]["text"].lower().startswith("first")


def test_drafting_line_one_mid_line_does_not_override_drafting():
    content = """\
DRAFTING: First Section
Some prose after line-one drafting.

DRAFTING: Should not replace
Trailing line.
"""
    result = _parse_agent_response(content)
    assert result["drafting"] == "First Section"
    assert "Should not replace" in result["prose"]
    assert "Trailing line." in result["prose"]
