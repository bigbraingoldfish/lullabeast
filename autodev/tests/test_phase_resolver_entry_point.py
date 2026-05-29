"""P1 Stage F — phase_resolver._extract_entry_point.

Parses the ``## Entry point`` section of verification.md into
``{command, ready_signal}``. Each bullet parses INDEPENDENTLY — Stage F
deliberately departs from ``_extract_behavioral_verification``'s
all-or-nothing rule because reachability depends only on ``command``;
coupling it to ``ready_signal`` (an unrelated field) would silently
disable the check.
"""

import os

import pytest

import phase_resolver as resolver_mod  # noqa: E402


def _write_verification(tmp_path, body):
    path = os.path.join(str(tmp_path), "verification.md")
    with open(path, "w") as f:
        f.write(body)
    return path


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_entry_point_extracted_when_section_present(tmp_path):
    """Both bullets present → both fields populated."""
    body = (
        "# Verification\n\n"
        "## Project type\nhttp-api\n\n"
        "## Entry point\n"
        "- Command: `python app.py`\n"
        "- Ready signal: HTTP 200 on /\n\n"
        "## Public surface\n- Item\n"
    )
    path = _write_verification(tmp_path, body)
    result = resolver_mod._extract_entry_point(path)
    assert result == {"command": "python app.py", "ready_signal": "HTTP 200 on /"}


def test_entry_point_command_only_returns_dict_with_command(tmp_path):
    """Departs from behavioral_verification all-or-nothing: reachability needs
    only ``command``; a missing ``ready_signal`` does NOT disable the check."""
    body = (
        "## Entry point\n"
        "- Command: `npm start`\n\n"
        "## Public surface\n- x\n"
    )
    path = _write_verification(tmp_path, body)
    result = resolver_mod._extract_entry_point(path)
    assert result is not None
    assert result["command"] == "npm start"
    assert result["ready_signal"] is None


def test_entry_point_ready_signal_only_returns_dict_with_ready_signal(tmp_path):
    """Symmetric — Ready signal only, no Command. Returns the dict with the
    field that did parse; downstream `command` consumer sees None and skips."""
    body = (
        "## Entry point\n"
        "- Ready signal: HTTP 200\n\n"
        "## Public surface\n- x\n"
    )
    path = _write_verification(tmp_path, body)
    result = resolver_mod._extract_entry_point(path)
    assert result is not None
    assert result["command"] is None
    assert result["ready_signal"] == "HTTP 200"


# ---------------------------------------------------------------------------
# None paths
# ---------------------------------------------------------------------------


def test_entry_point_none_when_section_missing(tmp_path):
    """No ## Entry point heading → None."""
    body = "# Verification\n\n## Project type\nhttp-api\n"
    path = _write_verification(tmp_path, body)
    assert resolver_mod._extract_entry_point(path) is None


def test_entry_point_none_when_verification_missing():
    """Path doesn't exist → None (must NOT raise)."""
    assert resolver_mod._extract_entry_point("/nonexistent/path.md") is None
    assert resolver_mod._extract_entry_point(None) is None
    assert resolver_mod._extract_entry_point("") is None


def test_entry_point_neither_bullet_returns_none(tmp_path):
    """Section heading present but no bullets at all → None (nothing useful)."""
    body = (
        "## Entry point\n\n"
        "## Public surface\n- x\n"
    )
    path = _write_verification(tmp_path, body)
    assert resolver_mod._extract_entry_point(path) is None


def test_entry_point_terminates_at_next_section(tmp_path):
    """A bullet under a LATER section must not be slurped into Entry point."""
    body = (
        "## Entry point\n"
        "- Command: `python app.py`\n\n"
        "## Public surface\n"
        "- Ready signal: SHOULD NOT BE PARSED\n"
    )
    path = _write_verification(tmp_path, body)
    result = resolver_mod._extract_entry_point(path)
    assert result is not None
    assert result["command"] == "python app.py"
    # The "Ready signal" line is under "## Public surface" — must NOT be parsed.
    assert result["ready_signal"] is None
