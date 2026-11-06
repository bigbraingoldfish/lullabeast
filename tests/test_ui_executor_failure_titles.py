"""Executor failure and warning titles must say what the pipeline does.

Two activity-feed claims contradicted the code:

* "Discarded work." A self-failure retry keeps the executor's working tree
  (``reset_execution`` hard-resets only for ``ERR_UNACCOUNTED_DELETION``) and
  gives a fresh session the gate's note. Only that one code discards work.
* "Attempting retry." on the three demoted codes. ``ERR_TDD_COVERAGE_MISMATCH``,
  ``ERR_MANIFEST_FILE_MISSING`` and ``ERR_BEHAVIORAL_ARTIFACTS_MISSING`` are
  non-blocking gate warnings now. The feed renders their titles inside
  "Gate raised N non-blocking warning(s) — …", so a title that reads as an
  error and a retry tells the operator the opposite of what happened.

Same convention as test_ui_observability_p1_render_fixes.py: read index.html
as text, pin the absence of the misleading claim, not the exact prose.
"""

import re
from pathlib import Path

import pytest

INDEX_HTML_PATH = Path(__file__).parent.parent / "ui" / "index.html"

DEMOTED_WARNING_CODES = [
    "ERR_TDD_COVERAGE_MISMATCH",
    "ERR_MANIFEST_FILE_MISSING",
    "ERR_BEHAVIORAL_ARTIFACTS_MISSING",
]


@pytest.fixture(scope="module")
def titles():
    html = INDEX_HTML_PATH.read_text(encoding="utf-8")
    start = html.find("const P3_LAST_ERROR_CODE_TITLES")
    end = html.find("function getLastErrorCodeTitle", start)
    assert start != -1 and end != -1, "P3_LAST_ERROR_CODE_TITLES block not found"
    block = html[start:end]
    keys = re.findall(r"^\s+(ERR_[A-Z_]+):", block, re.MULTILINE)
    entries = dict(re.findall(r'(ERR_[A-Z_]+):\s*"((?:[^"\\]|\\.)*)"', block))
    assert keys and sorted(entries) == sorted(keys), (
        "every title must parse as a plain double-quoted string; update the parser "
        f"if the map's format changed. Unparsed: {sorted(set(keys) - set(entries))}"
    )
    return entries


def test_only_the_hard_reset_code_says_work_is_discarded(titles):
    discarding = sorted(code for code, text in titles.items() if "discard" in text.lower())
    assert set(discarding) <= {"ERR_UNACCOUNTED_DELETION"}, (
        "only ERR_UNACCOUNTED_DELETION hard-resets the working tree; every other "
        f"executor retry keeps the work. Titles claiming a discard: {discarding}"
    )


@pytest.mark.parametrize("code", DEMOTED_WARNING_CODES)
def test_demoted_warning_titles_do_not_read_as_a_failure(titles, code):
    text = titles[code]
    lowered = text.lower()
    assert "retry" not in lowered and "error" not in lowered, (
        f"{code} is a non-blocking warning; its title renders inside "
        f"'Gate raised N non-blocking warning(s) — …' and must not announce a "
        f"failure or retry: {text!r}"
    )
    assert not text.endswith("."), (
        f"{code}'s title is followed by '. Advisory — …' in the warning row; "
        f"a trailing period renders as '..': {text!r}"
    )
